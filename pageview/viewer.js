'use strict';
// pageview: the page. It shows a rich page (or a data file as one table), the comments in the sidebar, and the chat
// (kit.chatPanel). components.js (PV) makes the blocks. The page and its data are never changed by the viewer. The
// comments live in a JSON file next to the page, and the server changes that file.

const $ = id => document.getElementById(id);
const FILE = new URLSearchParams(location.search).get('f') || '';
const CONTEXT = 40;  // characters before and after a quote, in an anchor

const S = {
  doc: null,        // the payload of /api/doc
  html: null,       // the HTML that is shown now, so a change of the comments alone does not draw the page again
  deps: null,
  active: null,     // the id of the selected thread
  comp: null,       // the open comment box: {type: 'new' | 'general' | 'reply' | 'edit', anchor, thread, msg, text}
  sel: null,        // the anchor of the selected text or cell, or null
  lost: new Set(),  // threads whose words are not found
  ranges: new Map(),  // thread id -> the Range of its words on the page
  text: null,       // {s, segs}: the text of the page, for text anchors
};

// ------------------------------------------------------------ helpers
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function btn(label, title, fn, cls) {
  const b = el('button', cls, label);
  b.type = 'button';
  if (title) b.title = title;
  b.addEventListener('click', ev => { ev.stopPropagation(); fn(ev); });
  return b;
}
function toast(msg, label, fn, ms = 6000) {
  const t = el('div', 'toast');
  t.append(el('span', null, msg));
  if (label) t.append(btn(label, '', () => { t.remove(); fn(); }));
  $('toast').append(t);
  setTimeout(() => t.remove(), ms);
}
async function api(method, path, body) {
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-PageView': '1'},
    body: body === undefined ? undefined : JSON.stringify({f: FILE, ...body})});
  let data = {};
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) { const err = new Error(data.error || r.statusText); err.status = r.status; throw err; }
  return data;
}
const enc = encodeURIComponent;
const typing = t => t && t.closest && t.closest('input, textarea, select, [contenteditable]');
const oneLine = s => String(s).replace(/\s+/g, ' ').trim();
function store(k, v) { try { localStorage.setItem('pageview.' + k, v); } catch (e) { /* not saved */ } }
function stored(k) { try { return localStorage.getItem('pageview.' + k); } catch (e) { return null; } }

// ------------------------------------------------------------ theme: system, light or dark
function theme() {
  const t = document.documentElement.dataset.theme;
  return t || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}
function setTheme(t) {
  if (t === 'system') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  store('theme', t);
  $('themeBtn').textContent = {system: '◐ Auto', light: '☀ Light', dark: '☾ Dark'}[t];
  for (const f of document.querySelectorAll('.pv-widget iframe')) f.src = f.src.replace(/theme=\w+/, 'theme=' + theme());
}

// ------------------------------------------------------------ the document
async function loadDoc(opts = {}) {
  let d;
  try {
    d = await api('GET', `/api/doc?f=${enc(FILE)}`);
  } catch (e) {
    $('doc').replaceChildren(el('div', 'err-page', e.message));
    return false;
  }
  S.doc = d;
  renderHeader();
  const changed = d.kind === 'page' ? (d.html !== S.html || d.deps !== S.deps || opts.force) : (d.deps !== S.deps || d.doc !== (S.docState) || opts.force);
  if (changed) await renderBody();
  else PV.refresh($('doc'));
  renderThreads();
  repaintMarks();
  return true;
}

function renderHeader() {
  const d = S.doc;
  kit.crumbs($('name'), d.label, msg => toast(msg));
  kit.docTitle('page', d.label);
  const dl = $('dirLink');
  dl.hidden = !d.dirOk;
  dl.href = '/?d=' + enc(d.dir);
  $('undoBtn').disabled = !d.canUndo;
}

const dataOf = src => api('GET', `/api/data?f=${enc(FILE)}&src=${enc(src)}`);
const CTX = {
  get file() { return FILE; },
  data: dataOf,
  toast: m => toast(m),
  theme,
  cellThreads: a => (S.doc ? S.doc.threads : []).filter(t => t.anchor && t.anchor.col != null && sameCell(t.anchor, a)).map(t => t.id),
  cellClick: (a, td, text) => cellSelected(a, td, text),
};
const sameCell = (x, y) => x.table === y.table && String(x.row) === String(y.row) && String(x.col) === String(y.col);

async function renderBody() {
  const d = S.doc, box = $('doc'), scroller = $('main'), y = scroller.scrollTop;
  S.html = d.html; S.deps = d.deps; S.docState = d.doc;
  document.body.classList.toggle('datadoc', d.kind === 'data');
  if (d.kind === 'data') {
    box.replaceChildren();
    document.body.classList.add('nonav');
    try { await PV.dataPage(box, CTX, d.name, d.settings || {}); }
    catch (e) { box.replaceChildren(el('div', 'err-page', e.message)); }
    S.text = null;
    return;
  }
  const meta = d.meta || {};
  const frag = document.createElement('div');
  frag.innerHTML = d.html;
  const firstH1 = frag.querySelector(':scope > h1, :scope > div > h1');
  if (meta.title && !(firstH1 && oneLine(firstH1.textContent) === oneLine(meta.title))) {
    const head = el('header', 'pv-head');
    head.append(el('h1', null, meta.title));
    if (meta.subtitle) head.append(el('p', 'sub', meta.subtitle));
    const row = el('div', 'meta');
    if (meta.date) row.append(el('span', null, '📅 ' + meta.date));
    if (meta.author) row.append(el('span', null, '✍︎ ' + [].concat(meta.author).join(', ')));
    for (const t of [].concat(meta.tags || [])) row.append(el('span', 'chip', t));
    if (row.childNodes.length) head.append(row);
    frag.prepend(head);
  }
  document.body.classList.toggle('widetext', meta.width === 'wide');
  // build the new page off screen, with its data, and swap it in when it is ready: no flash on a reload
  addSourceButtons(frag);
  await PV.enhance(frag, CTX);
  box.replaceChildren(...frag.childNodes);
  PV.refresh(box);  // the grids measure their header rows, which needs the page on screen
  kit.typeset([box]);
  buildToc(meta);
  S.text = null;
  scroller.scrollTop = y;
}

// ------------------------------------------------------------ the source of a block
function blockLines(e) {
  const pos = e.dataset.pos;
  if (!pos) return null;
  let a = Infinity, b = 0;
  for (const part of pos.split(';')) {
    const m = /^(\d+):(\d+)-(\d+):(\d+)$/.exec(part);
    if (!m) continue;
    a = Math.min(a, +m[1]);
    b = Math.max(b, +m[4] === 1 && +m[3] > +m[1] ? +m[3] - 1 : +m[3]);
  }
  return a <= b ? [a, b] : null;
}
function addSourceButtons(box) {
  for (const e of [...box.children]) {
    const ln = blockLines(e);
    if (!ln || e.tagName === 'HR') continue;
    e.classList.add('blk');
    const b = btn('Source', `Lines ${ln[0]}–${ln[1]} of the file`, () => {
      let pre = e.querySelector(':scope > .pv-src');
      if (pre) { pre.remove(); b.classList.remove('on'); b.textContent = 'Source'; return; }
      pre = el('pre', 'pv-src pv-ui');
      pre.dataset.file = `${S.doc.name} · lines ${ln[0]}–${ln[1]}`;
      pre.textContent = S.doc.src.split('\n').slice(ln[0] - 1, ln[1]).join('\n');
      e.prepend(pre);
      b.classList.add('on'); b.textContent = 'Hide source';
    }, 'srcbtn pv-ui');
    e.prepend(b);
  }
}

// ------------------------------------------------------------ the contents
function buildToc(meta) {
  const nav = $('toc'), hs = [...$('doc').querySelectorAll('h2[id], h3[id]')].filter(h => !h.closest('.pv-gen, .pv-tabs, details'));
  nav.replaceChildren();
  for (const h of hs) {
    const c = h.cloneNode(true);
    c.querySelectorAll('.pv-ui').forEach(x => x.remove());
    const a = el('a', h.tagName === 'H3' ? 'h3' : '', c.textContent.trim());
    a.href = '#' + h.id;
    a.addEventListener('click', ev => { ev.preventDefault(); h.scrollIntoView({block: 'start'}); });
    nav.append(a);
  }
  const off = meta.toc === 'false' || hs.length < 2;
  document.body.classList.toggle('notoc', off);
  curSection();
}
function curSection() {
  const links = [...$('toc').querySelectorAll('a')];
  if (!links.length) return null;
  let cur = null;
  const top = $('main').getBoundingClientRect().top + 90;
  for (const a of links) { const h = document.getElementById(a.getAttribute('href').slice(1)); if (h && h.getBoundingClientRect().top < top) cur = a; }
  links.forEach(a => a.classList.toggle('cur', a === cur));
  return cur ? cur.textContent : null;
}

// ------------------------------------------------------------ the text of the page, and text anchors
const SKIP = '.pv-gen, .pv-ui, .pv-src, mjx-container, .math, script, style, #pop, figcaption';
function pageText() {
  if (S.text) return S.text;
  const root = $('doc'), segs = [];
  let s = '';
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => (n.parentElement && n.parentElement.closest(SKIP)) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  for (let n = w.nextNode(); n; n = w.nextNode()) { segs.push({node: n, start: s.length, len: n.data.length}); s += n.data; }
  S.text = {s, segs};
  return S.text;
}
function offsetOf(T, node, off) {
  if (node.nodeType === 3) {
    const seg = T.segs.find(g => g.node === node);
    if (seg) return seg.start + off;
  }
  const r = document.createRange();
  r.setStart(node, off);
  for (const g of T.segs) if (r.comparePoint(g.node, 0) >= 0) return g.start;
  return T.s.length;
}
function pointAt(T, off, end) {
  for (const g of T.segs) if (off < g.start + g.len || (end && off === g.start + g.len)) return [g.node, Math.min(g.len, Math.max(0, off - g.start))];
  const last = T.segs[T.segs.length - 1];
  return last ? [last.node, last.len] : [$('doc'), 0];
}
function rangeOf(T, a, b) {
  const r = document.createRange();
  r.setStart(...pointAt(T, a, false));
  r.setEnd(...pointAt(T, b, true));
  return r;
}
function lineOf(node) {
  let e = node.nodeType === 3 ? node.parentElement : node;
  for (; e && e !== $('doc'); e = e.parentElement) { const ln = blockLines(e); if (ln) return ln[0]; }
  return null;
}
function selectionAnchor() {
  const s = getSelection();
  if (!s.rangeCount || s.isCollapsed || !S.doc || S.doc.kind !== 'page') return null;
  const r = s.getRangeAt(0), root = $('doc');
  if (!root.contains(r.startContainer)) return null;
  const startEl = r.startContainer.nodeType === 3 ? r.startContainer.parentElement : r.startContainer;
  if (startEl.closest(SKIP)) return null;
  const T = pageText();
  let a = offsetOf(T, r.startContainer, r.startOffset), b = root.contains(r.endContainer) ? offsetOf(T, r.endContainer, r.endOffset) : T.s.length;
  while (a < b && /\s/.test(T.s[a])) a++;
  while (b > a && /\s/.test(T.s[b - 1])) b--;
  if (b <= a) return null;
  return {quote: T.s.slice(a, b), prefix: T.s.slice(Math.max(0, a - CONTEXT), a), suffix: T.s.slice(b, b + CONTEXT),
          line: lineOf(r.startContainer), a, b};
}
function commonEnd(x, y) { let k = 0; while (k < x.length && k < y.length && x[x.length - 1 - k] === y[y.length - 1 - k]) k++; return k; }
function commonStart(x, y) { let k = 0; while (k < x.length && k < y.length && x[k] === y[k]) k++; return k; }
// Find the words of an anchor: the exact words (the best fit of the words before and after them, and of the line),
// else the same letters without spaces.
function locate(T, an) {
  const text = T.s, q = an.quote;
  let best = null;
  for (let i = text.indexOf(q); i >= 0; i = text.indexOf(q, i + 1)) {
    let score = commonEnd(text.slice(Math.max(0, i - CONTEXT), i), an.prefix || '') +
                commonStart(text.slice(i + q.length, i + q.length + CONTEXT), an.suffix || '');
    if (an.line) { const seg = T.segs.find(g => i < g.start + g.len); if (seg && lineOf(seg.node) === an.line) score += 20; }
    if (!best || score > best.score) best = {a: i, b: i + q.length, score};
  }
  if (best) return best;
  const squeeze = s => {
    const chars = [], idx = [];
    for (let i = 0; i < s.length; i++) { if (/\s|­/.test(s[i])) continue; chars.push(s[i].toLowerCase()); idx.push(i); }
    return {s: chars.join(''), idx};
  };
  const Tq = squeeze(text), Q = squeeze(q).s;
  if (!Q) return null;
  const i = Tq.s.indexOf(Q);
  return i < 0 ? null : {a: Tq.idx[i], b: Tq.idx[i + Q.length - 1] + 1};
}

// ------------------------------------------------------------ highlights (the CSS Custom Highlight API)
const HL = typeof CSS !== 'undefined' && CSS.highlights && typeof Highlight !== 'undefined';
function repaintMarks() {
  S.ranges.clear();
  if (!S.doc) return;
  let lostChanged = false;
  const T = S.doc.kind === 'page' ? pageText() : null, marks = [], active = [], draft = [];
  for (const t of S.doc.threads) {
    if (!t.anchor || t.anchor.col != null || !T) continue;
    const loc = locate(T, t.anchor);
    if (!loc) { if (!S.lost.has(t.id)) { S.lost.add(t.id); lostChanged = true; } continue; }
    if (S.lost.delete(t.id)) lostChanged = true;
    const r = rangeOf(T, loc.a, loc.b);
    S.ranges.set(t.id, r);
    (t.id === S.active ? active : marks).push(r);
  }
  for (const t of S.doc.threads) {  // a cell comment whose cell is not in the page
    if (!t.anchor || t.anchor.col == null) continue;
    const found = !!findCell(t.anchor);
    if (found === S.lost.has(t.id)) { if (found) S.lost.delete(t.id); else S.lost.add(t.id); lostChanged = true; }
  }
  if (S.comp && S.comp.type === 'new' && S.comp.anchor.quote && T) {
    const loc = locate(T, S.comp.anchor);
    if (loc) draft.push(rangeOf(T, loc.a, loc.b));
  }
  if (HL) {
    CSS.highlights.set('pv-mark', new Highlight(...marks));
    CSS.highlights.set('pv-active', new Highlight(...active));
    CSS.highlights.set('pv-draft', new Highlight(...draft));
  }
  document.querySelectorAll('td.cact').forEach(td => td.classList.remove('cact'));
  const at = S.doc.threads.find(t => t.id === S.active && t.anchor && t.anchor.col != null);
  if (at) { const td = findCell(at.anchor); if (td) td.classList.add('cact'); }
  if (lostChanged) renderThreads();
}
function findCell(a) {
  for (const box of document.querySelectorAll('#doc .pv-table, #doc .pv-grid')) {
    if (box.dataset.table !== a.table || !box.pvFind) continue;
    const td = box.pvFind(a);
    if (td) return td;
  }
  return null;
}
// A click on highlighted words selects their comment.
$('doc').addEventListener('click', ev => {
  if (!S.ranges.size || !getSelection().isCollapsed || ev.target.closest('.pv-ui, #pop, a, button')) return;
  let node, off;
  if (document.caretPositionFromPoint) { const p = document.caretPositionFromPoint(ev.clientX, ev.clientY); if (p) { node = p.offsetNode; off = p.offset; } }
  else if (document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(ev.clientX, ev.clientY); if (r) { node = r.startContainer; off = r.startOffset; } }
  if (!node) return;
  for (const [id, r] of S.ranges) {
    try { if (r.isPointInRange(node, off)) { setActive(id, true); return; } } catch (e) { /* another document */ }
  }
});

async function showAnchor(t) {
  if (!t.anchor) return;
  let target = null;
  if (t.anchor.col != null) target = findCell(t.anchor);
  else { const r = S.ranges.get(t.id); if (r) target = r; }
  if (!target) return;
  const box = $('main'), b = box.getBoundingClientRect(), r = target.getBoundingClientRect();
  if (r.top < b.top + 60 || r.bottom > b.bottom - 40) box.scrollTop += r.top - b.top - b.height / 3;
  if (t.anchor.col != null) { target.classList.remove('flash'); void target.offsetWidth; target.classList.add('flash'); }
}

// ------------------------------------------------------------ the sidebar
function setActive(id, fromPage) {
  S.active = id;
  repaintMarks();
  document.querySelectorAll('.thread').forEach(c => c.classList.toggle('active', +c.dataset.t === id));
  const card = document.querySelector(`.thread[data-t="${id}"]`);
  if (fromPage) {
    if (document.body.classList.contains('noside')) setSide(true);
    if (card) card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  } else {
    const t = S.doc.threads.find(x => x.id === id);
    if (t) showAnchor(t);
  }
}
function threadOrder(ts) {
  const k = t => !t.anchor ? [0, 0] : t.anchor.col == null ? [1, t.anchor.line || 0] : [2, 0];
  return [...ts].sort((x, y) => { const a = k(x), b = k(y); return a[0] - b[0] || a[1] - b[1] || x.id - y.id; });
}
function whereOf(a) {
  if (!a) return null;
  if (a.col != null) return {where: '▦ ' + a.table, q: `${a.row} · ${a.col}`};
  return {where: a.line ? 'l. ' + a.line : '', q: oneLine(a.quote)};
}
function renderThreads() {
  const box = $('threads'), ts = S.doc ? S.doc.threads : [];
  const nOpen = ts.filter(t => t.status === 'open').length;
  $('counts').textContent = ts.length ? `${nOpen} open · ${ts.length - nOpen} answered` : 'Comments';
  $('sideBtn').textContent = ts.length ? `Comments ${ts.length}` : 'Comments';
  paintOracle();
  kit.tab({open: nOpen});
  const frag = document.createDocumentFragment();
  if (S.comp && S.comp.type === 'general') frag.append(composer('Comment on the page'));
  $('resolveAllBtn').hidden = !ts.length;
  $('resolveAllBtn').textContent = `Resolve all (${ts.length})`;
  if (!ts.length && !(S.comp && S.comp.type === 'general'))
    frag.append(el('p', 'empty', 'Select words on the page, or click a table cell, to comment on it. Or add a comment about the whole page.'));
  for (const t of threadOrder(ts)) frag.append(threadCard(t));
  box.replaceChildren(frag);
  kit.typeset(box);
  const ta = box.querySelector('.composer textarea');
  if (ta && S.comp && S.comp.focus) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); S.comp.focus = false; }
  renderPop();
}
function threadCard(t) {
  const card = el('div', 'thread' + (t.id === S.active ? ' active' : ''));
  card.dataset.t = t.id;
  const top = el('div', 'top'), w = whereOf(t.anchor);
  if (w) { if (w.where) top.append(el('span', 'where', w.where)); top.append(el('q', null, w.q)); }
  else top.append(el('span', 'gen', '📄 General comment'));
  if (S.lost.has(t.id)) top.append(el('span', 'pill lost', 'not found'));
  top.append(el('span', 'pill ' + t.status, t.status === 'answered' ? 'Answered' : 'Open'));
  card.append(top);
  t.msgs.forEach((m, i) => {
    const row = el('div', 'msg ' + (m.author === 'claude' ? 'claude' : 'you'));
    const who = el('div', 'who');
    who.append(el('b', null, m.author === 'claude' ? 'Claude' : 'You'));
    if (m.ts) who.append(el('span', null, new Date(m.ts * 1000).toLocaleString([], {dateStyle: 'short', timeStyle: 'short'})));
    if (m.edited) who.append(el('span', null, '(edited)'));
    const acts = el('span', 'acts');
    acts.append(btn('Edit', 'Edit this message', () => compose({type: 'edit', thread: t.id, msg: i, text: m.text})),
                btn('Delete', 'Delete this message', () => op({op: 'delete', thread: t.id, msg: i}, 'Message deleted.')));
    who.append(acts);
    row.append(who);
    if (S.comp && S.comp.type === 'edit' && S.comp.thread === t.id && S.comp.msg === i) row.append(composer('Save'));
    else row.append(kit.md(m.text));
    card.append(row);
  });
  if (S.comp && S.comp.type === 'reply' && S.comp.thread === t.id) card.append(composer('Reply'));
  else {
    const acts = el('div', 'tacts');
    acts.append(btn('Reply', '', () => compose({type: 'reply', thread: t.id})),
                btn('Resolve', 'Remove this comment', () => op({op: 'resolve', thread: t.id}, 'Comment resolved.')));
    card.append(acts);
  }
  card.addEventListener('click', ev => { if (!ev.target.closest('button, textarea, a')) setActive(t.id, false); });
  return card;
}
function composer(label) {
  const c = el('div', 'composer'), ta = el('textarea');
  ta.value = S.comp.text || '';
  ta.placeholder = 'Write a comment… Maths in $…$ is typeset.';
  ta.addEventListener('input', () => { S.comp.text = ta.value; });
  ta.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); save(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
  });
  ta.addEventListener('click', ev => ev.stopPropagation());
  const row = el('div', 'row');
  row.append(el('span', 'hint', '⌘/Ctrl+Enter saves'), btn('Cancel', 'Esc', cancel), btn(label, '', save, 'on'));
  c.append(ta, row);
  return c;
}

// The comment box of a new comment on selected words or on a cell. It is on the page, under the words, so the sidebar
// can stay closed.
function renderPop() {
  const old = $('pop'), c = S.comp;
  if (!c || c.type !== 'new') { if (old) old.remove(); return; }
  if (old && old.comp === c) { placePop(); return; }
  if (old) old.remove();
  const p = composer('Comment');
  p.id = 'pop';
  p.comp = c;
  p.prepend(el('div', 'where', c.anchor.col != null ? `Comment on the cell ${c.anchor.row} · ${c.anchor.col}` : 'Comment on the selected words'));
  p.addEventListener('mousedown', ev => ev.stopPropagation());
  document.body.append(p);
  placePop();
  const ta = p.querySelector('textarea');
  if (c.focus) { ta.focus({preventScroll: true}); c.focus = false; }
}
function popTarget() {
  const c = S.comp;
  if (!c) return null;
  if (c.anchor.col != null) return findCell(c.anchor);
  const T = pageText(), loc = locate(T, c.anchor);
  if (!loc) return null;
  const rs = [...rangeOf(T, loc.a, loc.b).getClientRects()].filter(r => r.width >= 1);
  return rs.length ? {getBoundingClientRect: () => rs[rs.length - 1]} : null;
}
function placePop() {
  const p = $('pop');
  if (!p) return;
  const t = popTarget();
  const r = t ? t.getBoundingClientRect() : {left: innerWidth / 2 - 170, bottom: innerHeight / 3};
  p.style.left = Math.max(8, Math.min(r.left, innerWidth - p.offsetWidth - 8)) + 'px';
  let top = r.bottom + 6;
  if (top + p.offsetHeight > innerHeight - 8) top = Math.max(8, r.top - p.offsetHeight - 6);
  p.style.top = top + 'px';
}
function compose(c) {
  S.comp = {...c, text: c.text || '', focus: true};
  if (c.type === 'new') { renderThreads(); repaintMarks(); return; }
  if (document.body.classList.contains('noside')) setSide(true);
  renderThreads();
  repaintMarks();
  const box = document.querySelector('#threads .composer');
  if (box) box.scrollIntoView({block: 'nearest'});
}
function cancel() { S.comp = null; renderThreads(); repaintMarks(); }
async function save() {
  const c = S.comp;
  if (!c || !c.text.trim()) { toast('Write a comment first.'); return; }
  let body;
  if (c.type === 'new') {
    const a = c.anchor;
    body = {op: 'add', anchor: a.col != null ? {table: a.table, row: a.row, col: a.col} :
      {quote: a.quote, prefix: a.prefix, suffix: a.suffix, line: a.line}, text: c.text};
  } else if (c.type === 'general') body = {op: 'add', text: c.text};
  else if (c.type === 'reply') body = {op: 'reply', thread: c.thread, text: c.text};
  else body = {op: 'edit', thread: c.thread, msg: c.msg, text: c.text};
  S.comp = null;
  const d = await op(body);
  if (!d) { S.comp = c; renderThreads(); return; }
  if (d.added) { S.active = d.added; renderThreads(); repaintMarks(); }
}
async function op(body, done) {
  try {
    S.doc = {...S.doc, ...await api('POST', '/api/op', {...body, version: S.doc.version})};
  } catch (e) {
    toast(e.message, null, null, 8000);
    if (e.status === 409) await loadDoc();
    return null;
  }
  renderHeader();
  PV.refresh($('doc'));
  renderThreads();
  repaintMarks();
  if (done) toast(done, 'Undo', undo);
  return S.doc;
}
function undo() { return op({op: 'undo'}).then(d => d && toast('Undone.')); }

function setSide(on) {
  document.body.classList.toggle('noside', !on);
  $('sideBtn').classList.toggle('on', on);
  store('side', on ? 'on' : 'off');
  setTimeout(() => { placePop(); PV.redrawCharts($('doc')); }, 0);
}
function setNav(on) {
  document.body.classList.toggle('nonav', !on);
  $('navBtn').classList.toggle('on', on);
  store('nav', on ? 'on' : 'off');
}

// ------------------------------------------------------------ selection: comment button and chat context
let chatSel = null;  // {quote, where}: the text or the cell that was last selected, for the chat

function showCbtn(rect, label) {
  const b = $('cbtn');
  b.textContent = label;
  b.style.display = 'block';
  b.style.left = Math.max(8, Math.min(innerWidth - b.offsetWidth - 8, rect.right + 6)) + 'px';
  b.style.top = Math.max(48, Math.min(innerHeight - 40, rect.bottom + 4)) + 'px';
}
document.addEventListener('selectionchange', () => {
  const an = selectionAnchor();
  if (an) {
    S.sel = an;
    const rects = getSelection().getRangeAt(0).getClientRects(), last = rects[rects.length - 1];
    if (last) showCbtn(last, '💬 Comment');
    const q = oneLine(an.quote).slice(0, 1500);
    if (!chatSel || chatSel.quote !== q) { chatSel = {quote: q}; chat.renderCtx(); }
  } else if (S.sel && S.sel.quote != null) { S.sel = null; $('cbtn').style.display = 'none'; }
});
function cellSelected(a, td, text) {
  S.sel = {...a, text};
  showCbtn(td.getBoundingClientRect(), '💬 Comment on cell');
  chatSel = {quote: '[table ' + a.table + '] ' + text};
  chat.renderCtx();
  const ids = CTX.cellThreads(a);
  if (ids.length) setActive(ids[0], true);
}
function commentOnSelection() {
  const an = S.sel || selectionAnchor();
  if (!an) { toast('Select words on the page, or click a table cell, first.'); return; }
  $('cbtn').style.display = 'none';
  getSelection().removeAllRanges();
  S.sel = null;
  compose({type: 'new', anchor: an});
}
$('cbtn').addEventListener('mousedown', ev => ev.preventDefault());  // keep the selection
$('cbtn').addEventListener('click', commentOnSelection);
document.addEventListener('mousedown', ev => {
  if (ev.target.closest('#cbtn, #pop, td')) return;
  if (S.sel && S.sel.col != null) { S.sel = null; $('cbtn').style.display = 'none'; }
  // a click outside an empty comment box closes it. A box with text stays, so no text is lost.
  if (S.comp && S.comp.type === 'new' && !S.comp.text.trim()) cancel();
});

// ------------------------------------------------------------ chat
const chat = kit.chatPanel({
  key: 'pageview',
  account: {get: () => api('GET', '/api/account?f=' + encodeURIComponent(FILE)),
            post: body => api('POST', '/api/account', body)},
  poll: true,
  ready: () => !!S.doc,
  empty: 'Ask about this page. Claude can read it and its data files. It can change the page and answer or add comments when you ask. ' +
    'It runs on the computer that serves this page. Turn on "Ask only" if it must not change anything.',
  get: since => api('GET', `/api/chat?f=${enc(FILE)}` + (since == null ? '' : `&since=${since}`)),
  post: body => api('POST', '/api/chat', body),
  extra: () => ({quote: chatSel ? chatSel.quote : '', where: curSection() || ''}),
  afterSend: () => { chatSel = null; },
  context: () => chatSel && {text: '“' + chatSel.quote.slice(0, 60) + '”', title: chatSel.quote},
  hint: () => 'Select words or a cell to ask about it.',
  clearContext: () => { chatSel = null; },
  render: kit.md,
  typeset: box => kit.typeset(box),
  fillUser: (e, m) => {
    if (m.quote) { const q = el('div', 'cq', '“' + m.quote + '”'); q.title = m.quote; e.append(q); }
    e.append(m.text);
  },
  done: m => {
    if (m && m.changed) toast('Claude changed the comments.', 'Undo', undo, 15000);
    if (m && (m.changed || m.pageChanged)) loadDoc();
  },
  error: text => toast(text, null, null, 9000),
});

// ------------------------------------------------------------ the Oracle
let oracle = {running: false}, oracleTimer = null;
async function oracleCall(action) {
  let d;
  try {
    d = action ? await api('POST', '/api/claude', {action}) : await api('GET', `/api/claude?f=${enc(FILE)}`);
  } catch (e) {
    if (e.status) toast(e.message, null, null, 9000);
    else oracleTimer = setTimeout(oracleCall, 5000);
    return false;
  }
  const was = oracle.running;
  oracle = d;
  if (d.already) toast('Claude is already working on this page.');
  if (was && !d.running) oracleDone(d);
  paintOracle();
  clearTimeout(oracleTimer);
  if (d.running) oracleTimer = setTimeout(oracleCall, 3000);
  return true;
}
async function oracleDone(d) {
  await loadDoc();
  const report = () => { $('reportText').textContent = d.report || '(no output)'; $('reportDlg').showModal(); };
  if (d.exit === 0) toast('Claude has answered the comments.', 'Report', report, 12000);
  else if (d.exit == null) toast('Claude was stopped.');
  else toast(`Claude stopped with an error (exit ${d.exit}).`, 'Details', report, 15000);
}
function paintOracle() {
  const b = $('oracleBtn'), open = S.doc ? S.doc.threads.filter(t => t.status === 'open').length : 0;
  b.classList.toggle('busy', !!oracle.running);
  kit.tab({oracle: !!oracle.running});
  b.textContent = oracle.running ? 'Working… ■' : 'Oracle';
  b.disabled = !oracle.running && !open;
  b.title = oracle.running ? `Answering the comments since ${oracle.started}. Click to stop.`
    : open ? `Start Claude in the background to answer the ${open} open comment${open > 1 ? 's' : ''}` +
      (oracle.session ? ', and continue its session on this page.' : ', in a new session.')
    : 'No open comments.';
  $('oracleRow').hidden = !oracle.session || oracle.running;
}
$('oracleBtn').onclick = () => {
  if (!oracle.running) oracleCall('start');
  else if (confirm('Stop Claude? The replies that it already wrote stay.')) oracleCall('stop');
};
$('newSession').onclick = () => {
  if (confirm('Start a new Claude session for this page? The next Oracle run does not remember earlier rounds.'))
    oracleCall('new').then(ok => ok && toast('The next Oracle run starts a new session.'));
};

// ------------------------------------------------------------ controls and keys
$('undoBtn').onclick = undo;
$('sideBtn').onclick = () => setSide(document.body.classList.contains('noside'));
$('navBtn').onclick = () => setNav(document.body.classList.contains('nonav'));
$('themeBtn').onclick = () => setTheme({system: 'light', light: 'dark', dark: 'system'}[stored('theme') || 'system']);
$('srcAll').onclick = () => {
  const on = !document.body.classList.contains('allsrc');
  document.body.classList.toggle('allsrc', on);
  $('srcAll').classList.toggle('on', on);
};
$('generalBtn').onclick = () => compose({type: 'general'});
$('resolveAllBtn').onclick = () => {
  const n = S.doc ? S.doc.threads.length : 0;
  if (n && confirm(`Resolve all ${n} comment${n > 1 ? 's' : ''}? They are removed (Undo brings them back).`))
    op({op: 'resolve-all'}, 'All comments resolved.');
};
$('main').addEventListener('scroll', () => { curSection(); placePop(); }, {passive: true});
let resizeTimer = 0;
addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { S.text = null; repaintMarks(); placePop(); }, 150);
});
document.addEventListener('keydown', ev => {
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.shiftKey && (ev.key === 'M' || ev.key === 'm')) { ev.preventDefault(); commentOnSelection(); return; }
  if (mod && ev.key === '\\') { ev.preventDefault(); setSide(document.body.classList.contains('noside')); return; }
  if (mod || ev.altKey || typing(ev.target)) return;
  if (ev.key === 'Escape' && S.comp) cancel();
  else if (ev.key === 'o') setNav(document.body.classList.contains('nonav'));
  else return;
  ev.preventDefault();
});

// ------------------------------------------------------------ news from the server
// The page, its data files and its comments can change outside this tab (Claude, an experiment, the command line).
let polling = false;
async function poll() {
  if (polling || !S.doc || document.hidden) return;
  polling = true;
  try {
    const v = await api('GET', `/api/version?f=${enc(FILE)}`);
    $('live').classList.remove('off');
    if (v.doc !== S.doc.doc || v.deps !== S.doc.deps) { await loadDoc({force: true}); }
    else if (v.version !== S.doc.version) await loadDoc();
  } catch (e) { $('live').classList.add('off'); }
  finally { polling = false; }
}

// ------------------------------------------------------------ start
(async () => {
  kit.tab({app: 'page'});
  setTheme(stored('theme') || 'system');
  setSide(stored('side') === 'on');
  setNav(stored('nav') !== 'off');
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => setTheme(stored('theme') || 'system'));
  if (!FILE) { $('doc').replaceChildren(el('div', 'err-page', 'No file. Open one with: pageview FILE.md')); return; }
  if (!await loadDoc()) return;
  oracleCall();
  setInterval(poll, 2000);
})();
