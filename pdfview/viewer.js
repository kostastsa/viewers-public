'use strict';
// pdfview: the page. It shows the PDF (kit.pdfPages, PDF.js), the comments in the sidebar, and the chat (kit.chatPanel).
// The PDF is never changed. The comments live in a JSON file next to it, and the server changes that file.

const $ = id => document.getElementById(id);
const FILE = new URLSearchParams(location.search).get('f') || '';
const CONTEXT = 40;       // characters before and after a quote, in an anchor

const S = {
  doc: null,        // the payload of /api/doc: threads, version, ...
  active: null,     // the id of the selected thread
  comp: null,       // the open comment box: {type: 'new' | 'general' | 'reply' | 'edit', anchor, thread, msg, text}
  sel: null,        // the anchor of the selected text, or null
  lost: new Set(),  // threads whose words are not found on their page
  dark: false,
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
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-PDFView': '1'},
    body: body === undefined ? undefined : JSON.stringify({f: FILE, ...body})});
  let data = {};
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) { const err = new Error(data.error || r.statusText); err.status = r.status; throw err; }
  return data;
}
const enc = encodeURIComponent;
const typing = t => t && (t.closest('input, textarea, [contenteditable]'));
const oneLine = s => s.replace(/\s+/g, ' ').trim();
function store(k, v) { try { localStorage.setItem('pdfview.' + k, v); } catch (e) { /* not saved */ } }
function stored(k) { try { return localStorage.getItem('pdfview.' + k); } catch (e) { return null; } }

// ------------------------------------------------------------ the comments file
async function loadDoc() {
  try {
    S.doc = await api('GET', `/api/doc?f=${enc(FILE)}`);
  } catch (e) {
    $('pages').replaceChildren(el('div', 'err-page', e.message));
    return false;
  }
  renderHeader();
  renderThreads();
  repaintMarks();
  return true;
}

async function op(body, done) {
  try {
    S.doc = await api('POST', '/api/op', {...body, version: S.doc.version});
  } catch (e) {
    toast(e.message, null, null, 8000);
    if (e.status === 409) await loadDoc();
    return null;
  }
  renderHeader();
  renderThreads();
  repaintMarks();
  if (done) toast(done, 'Undo', undo);
  return S.doc;
}

function undo() { return op({op: 'undo'}).then(d => d && toast('Undone.')); }

function renderHeader() {
  const d = S.doc;
  kit.crumbs($('name'), d.label, msg => toast(msg));
  kit.docTitle('pdf', d.label);
  const dl = $('dirLink');
  dl.hidden = !d.dirOk;
  dl.href = '/?d=' + enc(d.dir);
  $('undoBtn').disabled = !d.canUndo;
}

// ------------------------------------------------------------ the pages (kit.pdfPages, viewkit/web/pdfpages.js)
// The kit draws the pages, the zoom and the pinch, the dark pages, the links and the contents. pdfview paints its marks
// on each page when the kit has drawn it (paintMarks).
const pv = kit.pdfPages({
  box: $('pages'), zoomLabel: $('zoomLabel'), pageNum: $('pageNum'), pageCount: $('pageCount'), outline: $('outline'),
  onPage: pg => paintMarks(pg),
  onSize: () => placePop(),
  skipClick: ev => !!(BOX.drag || ev.target.closest('#pop')),  // the end of a box drag, or a click in the comment box
  onZoom: z => store('zoom', String(z)),
  toast: msg => toast(msg),
});

function loadPdf(keepView) { return pv.load(`/pdf?f=${enc(FILE)}&v=${enc(S.doc.pdf)}`, keepView); }

function setDark(on) {
  S.dark = on;
  pv.setDark(on);
  $('darkBtn').classList.toggle('on', on);
  store('dark', on ? '1' : '0');
}

// ------------------------------------------------------------ the text of a page, and anchors
// The anchor of the selected text: on the page where the selection starts, cut at the end of that page.
function selectionAnchor() {
  const s = getSelection();
  if (!s.rangeCount || s.isCollapsed) return null;
  const r = s.getRangeAt(0);
  const start = r.startContainer.nodeType === 3 ? r.startContainer.parentElement : r.startContainer;
  const pageEl = start && start.closest && start.closest('.page');
  if (!pageEl) return null;
  const pg = pv.pages[+pageEl.dataset.n - 1];
  if (!pg || !pg.text || !pg.textDiv.contains(r.startContainer)) return null;
  let a = pv.offsetOf(pg, r.startContainer, r.startOffset);
  let b = pg.textDiv.contains(r.endContainer) ? pv.offsetOf(pg, r.endContainer, r.endOffset) : pg.text.s.length;
  const t = pg.text.s;
  while (a < b && /\s/.test(t[a])) a++;
  while (b > a && /\s/.test(t[b - 1])) b--;
  if (b <= a) return null;
  return {page: pg.n, quote: t.slice(a, b), prefix: t.slice(Math.max(0, a - CONTEXT), a),
          suffix: t.slice(b, b + CONTEXT), a, b};
}

function commonEnd(x, y) { let k = 0; while (k < x.length && k < y.length && x[x.length - 1 - k] === y[y.length - 1 - k]) k++; return k; }
function commonStart(x, y) { let k = 0; while (k < x.length && k < y.length && x[k] === y[k]) k++; return k; }

// Find the words of an anchor in the page text. Two passes, each with the best fit of the words before and after:
//   1. the same words, with all white space taken as one space (a line end in the page is a space in a saved quote);
//   2. the same letters without white space, without soft hyphens, and without a hyphen before white space (a word
//      that breaks at a line end: "argu-" + line end + "ment" in the page, "argu- ment" in a quote saved by the
//      viewer, "argument" in a quote from pdftotext).
function normalize(s, squeeze) {
  const chars = [], idx = [];
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (squeeze) {
      if (/\s|\u00ad/.test(c) || (c === '-' && /\s/.test(s[i + 1] || ''))) continue;
      chars.push(c.toLowerCase());
    } else if (/\s/.test(c)) {
      if (!chars.length || chars[chars.length - 1] === ' ') continue;
      chars.push(' ');
    } else chars.push(c);
    idx.push(i);
  }
  return {s: chars.join(''), idx};
}

function locate(text, an) {
  for (const squeeze of [false, true]) {
    const T = normalize(text, squeeze), q = normalize(an.quote || '', squeeze).s.trim();
    if (!q) continue;
    const pre = normalize(an.prefix || '', squeeze).s, suf = normalize(an.suffix || '', squeeze).s;
    let best = null;
    for (let i = T.s.indexOf(q); i >= 0; i = T.s.indexOf(q, i + 1)) {
      const score = commonEnd(T.s.slice(Math.max(0, i - CONTEXT), i), pre) +
                    commonStart(T.s.slice(i + q.length, i + q.length + CONTEXT), suf);
      if (!best || score > best.score) best = {i, score};
    }
    if (best) return {a: T.idx[best.i], b: T.idx[best.i + q.length - 1] + 1};
  }
  return null;
}

// ------------------------------------------------------------ highlights on the pages
function paintMarks(pg) {
  if (pg.state !== 'done' || !S.doc) return;
  pg.marks.replaceChildren();
  const box = pg.el.getBoundingClientRect();
  const draw = (a, b, cls, tid) => {
    for (const r of pv.rangeOf(pg, a, b).getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const d = el('div', cls);
      d.style.left = (r.left - box.left) + 'px';
      d.style.top = (r.top - box.top) + 'px';
      d.style.width = r.width + 'px';
      d.style.height = r.height + 'px';
      if (tid != null) {
        d.dataset.t = tid;
        d.title = 'Comment #' + tid;
        d.addEventListener('click', ev => { ev.stopPropagation(); setActive(tid, true); });
      }
      pg.marks.append(d);
    }
  };
  // a box anchor: a frame at its area (PDF points from the top-left corner), in the pixels of the page now
  const drawBox = (b, cls, tid) => {
    const kx = box.width / pg.w, ky = box.height / pg.h, d = el('div', 'box ' + (cls || ''));
    d.style.left = b[0] * kx + 'px'; d.style.top = b[1] * ky + 'px';
    d.style.width = (b[2] - b[0]) * kx + 'px'; d.style.height = (b[3] - b[1]) * ky + 'px';
    if (tid != null) {  // the inside lets the pointer through (the text in the box stays selectable): a tab selects it
      d.dataset.t = tid;
      const tab = el('span', 'btag', '▭ ' + tid), x = el('span', 'bx', '×');
      tab.title = 'Comment #' + tid;
      x.title = 'Delete this box comment (Undo brings it back)';
      tab.addEventListener('click', ev => { ev.stopPropagation(); setActive(tid, true); });
      x.addEventListener('click', ev => { ev.stopPropagation(); op({op: 'resolve', thread: tid}, 'Box comment deleted.'); });
      tab.append(x);
      d.append(tab);
    }
    pg.marks.append(d);
  };
  let lostChanged = false;
  for (const t of S.doc.threads) {
    if (!t.anchor || t.anchor.page !== pg.n) continue;
    if (t.anchor.box) { drawBox(t.anchor.box, t.id === S.active ? 'active' : '', t.id); continue; }
    const loc = locate(pg.text.s, t.anchor);
    if (!loc) { if (!S.lost.has(t.id)) { S.lost.add(t.id); lostChanged = true; } continue; }
    if (S.lost.delete(t.id)) lostChanged = true;
    draw(loc.a, loc.b, t.id === S.active ? 'active' : '', t.id);
  }
  for (const h of S.doc.highlights || []) {  // a highlight: green; a click opens its menu
    if (!h.anchor || h.anchor.page !== pg.n || (S.comp && S.comp.fromHighlight === h.id)) continue;
    const loc = h.anchor.box ? null : locate(pg.text.s, h.anchor);
    if (!h.anchor.box && !loc) continue;
    const before = pg.marks.children.length;
    if (h.anchor.box) drawBox(h.anchor.box, 'hl');
    else draw(loc.a, loc.b, 'hl');
    for (const d of [...pg.marks.children].slice(before)) {
      if (h.anchor.box) {  // a box highlight: a tab with × to remove it
        const tab = el('span', 'btag', '▭'), x = el('span', 'bx', '×');
        x.title = 'Remove this highlight (Undo brings it back)';
        x.addEventListener('click', ev => { ev.stopPropagation(); op({op: 'unhighlight', highlight: h.id}, 'Highlight removed.'); });
        tab.append(x);
        d.append(tab);
      }
      d.dataset.h = h.id;
      d.title = 'Highlight (click: comment or remove)';
      d.style.pointerEvents = h.anchor.box ? 'none' : '';
      d.addEventListener('click', ev => { ev.stopPropagation(); openHmenu(h, ev); });
    }
  }
  if (S.comp && S.comp.type === 'new' && S.comp.anchor.page === pg.n && S.comp.anchor.box) drawBox(S.comp.anchor.box, 'draft');
  else if (S.comp && S.comp.type === 'new' && S.comp.anchor.page === pg.n) {
    const loc = locate(pg.text.s, S.comp.anchor);
    if (loc) draw(loc.a, loc.b, 'draft');
  }
  if (lostChanged) renderThreads();
}

function repaintMarks() { for (const pg of pv.pages) paintMarks(pg); }

async function showAnchor(t) {
  if (!t.anchor) return;
  const pg = pv.pages[t.anchor.page - 1];
  if (!pg) return;
  const box = $('pages');
  if (pg.el.offsetTop > box.scrollTop + box.clientHeight || pg.el.offsetTop + pg.el.offsetHeight < box.scrollTop)
    box.scrollTop = pg.el.offsetTop - 16;
  await pv.renderNow(pg);
  const marks = [...pg.el.querySelectorAll(`.marks div[data-t="${t.id}"]`)];
  if (!marks.length) return;
  const r = marks[0].getBoundingClientRect(), b = box.getBoundingClientRect();
  if (r.top < b.top + 40 || r.bottom > b.bottom - 40) box.scrollTop += r.top - b.top - b.height / 3;
  marks.forEach(m => { m.classList.remove('flash'); void m.offsetWidth; m.classList.add('flash'); });
}

// ------------------------------------------------------------ the sidebar
// fromPage: the click was on the page, so show the card in the sidebar. keepSide: do not open a hidden sidebar.
function setActive(id, fromPage, keepSide) {
  S.active = id;
  repaintMarks();
  document.querySelectorAll('.thread').forEach(c => c.classList.toggle('active', +c.dataset.t === id));
  const card = document.querySelector(`.thread[data-t="${id}"]`);
  if (fromPage) {
    if (!keepSide && document.body.classList.contains('noside')) setSide(true);
    if (card) card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  } else {
    const t = S.doc.threads.find(x => x.id === id);
    if (t) showAnchor(t);
  }
}

function threadOrder(ts) {
  return [...ts].sort((x, y) => (x.anchor ? 1 : 0) - (y.anchor ? 1 : 0) ||
    (x.anchor && y.anchor ? x.anchor.page - y.anchor.page : 0) || x.id - y.id);
}

function renderThreads() {
  const box = $('threads'), ts = S.doc ? S.doc.threads : [];
  const nOpen = ts.filter(t => t.status === 'open').length;
  $('counts').textContent = ts.length ? `${nOpen} open · ${ts.length - nOpen} answered` : 'Comments';
  if (typeof paintOracle === 'function') paintOracle();
  kit.tab({open: nOpen});
  const frag = document.createDocumentFragment();
  if (S.comp && S.comp.type === 'general') frag.append(composer('Comment on the file'));
  $('resolveAllBtn').hidden = !ts.length;
  $('resolveAllBtn').textContent = `Resolve all (${ts.length})`;
  if (!ts.length && !(S.comp && S.comp.type === 'general')) {
    frag.append(el('p', 'empty', 'Select text on a page to comment on it, or add a comment about the whole file.'));
  }
  for (const t of threadOrder(ts)) frag.append(threadCard(t));
  box.replaceChildren(frag);
  kit.typeset(box);
  const ta = box.querySelector('.composer textarea');
  if (ta && S.comp && S.comp.focus) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); S.comp.focus = false; }
  renderPop();
}

// The comment box of a new comment on selected text. It is on the page, under the selection, so the sidebar can stay
// closed. S.comp.at is its place as fractions of the page box, so the box follows a zoom or a pinch.
function renderPop() {
  const old = $('pop'), c = S.comp;
  if (!c || c.type !== 'new') { if (old) old.remove(); return; }
  if (old && old.comp === c) { placePop(); return; }  // keep the box as it is (the cursor, the focus)
  if (old) old.remove();
  const p = composer('Comment');
  p.id = 'pop';
  p.comp = c;
  p.prepend(el('div', 'where', (c.anchor.box ? 'Comment on an area of page ' : 'Comment on page ') + c.anchor.page));
  $('pages').append(p);
  placePop();
  const ta = p.querySelector('textarea');
  if (c.focus) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); c.focus = false; }
}

function placePop() {
  const p = $('pop'), c = S.comp;
  if (!p || !c || !c.at) return;
  const pg = pv.pages[c.anchor.page - 1];
  if (!pg) return;
  const x = pg.el.offsetLeft + c.at.fx * pg.el.offsetWidth;
  const right = Math.max(pg.el.offsetLeft + pg.el.offsetWidth, $('pages').clientWidth);
  p.style.left = Math.max(8, Math.min(x, right - p.offsetWidth - 8)) + 'px';
  p.style.top = (pg.el.offsetTop + c.at.fy * pg.el.offsetHeight + 6) + 'px';
}

function threadCard(t) {
  const card = el('div', 'thread' + (t.id === S.active ? ' active' : ''));
  card.dataset.t = t.id;
  const top = el('div', 'top');
  if (t.anchor && t.anchor.box) top.append(el('span', 'where', 'p. ' + t.anchor.page + ' ▭'),
                                         el('q', null, t.anchor.quote ? oneLine(t.anchor.quote) : 'an area of the page'));
  else if (t.anchor) top.append(el('span', 'where', 'p. ' + t.anchor.page), el('q', null, oneLine(t.anchor.quote)));
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
  card.addEventListener('click', ev => {
    if (ev.target.closest('button, textarea, a')) return;
    setActive(t.id, false);
  });
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

function compose(c) {
  S.comp = {...c, text: c.text || '', focus: true};
  if (c.type === 'new') { renderThreads(); repaintMarks(); return; }  // the box is on the page (renderPop)
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
    const {page, quote, prefix, suffix, box} = c.anchor;
    body = {op: 'add', anchor: box ? {page, box, quote} : {page, quote, prefix, suffix}, text: c.text};
    if (c.fromHighlight != null) body.from_highlight = c.fromHighlight;
  } else if (c.type === 'general') body = {op: 'add', text: c.text};
  else if (c.type === 'reply') body = {op: 'reply', thread: c.thread, text: c.text};
  else body = {op: 'edit', thread: c.thread, msg: c.msg, text: c.text};
  S.comp = null;
  const d = await op(body);
  if (!d) { S.comp = c; renderThreads(); return; }
  if (d.added) setActive(d.added, true, c.type === 'new');
}

function setSide(on) {
  document.body.classList.toggle('noside', !on);
  $('sideBtn').classList.toggle('on', on);
  $('sideBtn').title = (on ? 'Hide' : 'Show') + ' the comments (⌘/Ctrl+\\)';
  store('side', on ? 'on' : 'off');
  setTimeout(() => { if (pv.zoom === 'fit') pv.layout(); else repaintMarks(); }, 0);
}

function setNav(on) {
  document.body.classList.toggle('nonav', !on);
  $('navBtn').classList.toggle('on', on);
  store('nav', on ? 'on' : 'off');
  setTimeout(() => { if (pv.zoom === 'fit') pv.layout(); else repaintMarks(); }, 0);
}

// ------------------------------------------------------------ selection: comment button and chat context
let chatSel = null;  // {quote, page}: the text that was last selected, for the chat

document.addEventListener('selectionchange', () => {
  const an = selectionAnchor();
  S.sel = an;
  const b = $('selbar');
  if (!an) { b.style.display = 'none'; return; }
  const rects = getSelection().getRangeAt(0).getClientRects();
  const last = rects[rects.length - 1];
  if (last) {
    b.style.display = 'flex';
    b.style.left = Math.min(window.innerWidth - b.offsetWidth - 8, last.right + 6) + 'px';
    b.style.top = Math.max(48, last.bottom + 4) + 'px';
  }
  const q = oneLine(an.quote).slice(0, 1500);
  if (!chatSel || chatSel.quote !== q || chatSel.page !== an.page) { chatSel = {quote: q, page: an.page}; chat.renderCtx(); }
});

function commentOnSelection() {
  const an = S.sel || selectionAnchor();
  if (!an) { toast('Select text on a page first.'); return; }
  // The box goes under the last line of the words, on the page where they start.
  const pg = pv.pages[an.page - 1], box = pg.el.getBoundingClientRect();
  const rects = [...pv.rangeOf(pg, an.a, an.b).getClientRects()].filter(r => r.width >= 1 && r.height >= 1);
  const last = rects[rects.length - 1];
  const at = last ? {fx: (last.left - box.left) / box.width, fy: (last.bottom - box.top) / box.height} : {fx: 0, fy: 0};
  $('selbar').style.display = 'none';
  getSelection().removeAllRanges();
  compose({type: 'new', anchor: an, at});
}

// ------------------------------------------------------------ the box: a comment on an area of a page
// Hold ⌥ Option (the cursor becomes a cross) and drag: a box. The box is kept in PDF points from the top-left corner
// of the page, so it stays in place at every zoom. The text in it goes with it, for Claude. Esc during a drag stops it.
const BOX = {drag: null};
function textInBox(pg, r) {  // r: the box in client pixels
  const out = [];
  for (const g of pg.text.segs) {
    if (g.br) continue;
    const b = g.node.parentElement.getBoundingClientRect(), cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2;
    if (cx >= r.left && cx <= r.right && cy >= r.top && cy <= r.bottom) out.push(g.node.data);
  }
  return oneLine(out.join(' '));
}
$('pages').addEventListener('mousedown', ev => {
  if (ev.button !== 0 || !ev.altKey) return;
  const pageEl = ev.target.closest && ev.target.closest('.page');
  if (!pageEl || ev.target.closest('#pop')) return;
  const pg = pv.pages[+pageEl.dataset.n - 1];
  if (!pg || pg.state !== 'done') return;
  ev.preventDefault();  // no text selection
  getSelection().removeAllRanges();
  const d = el('div', 'boxdraw');
  pageEl.append(d);
  BOX.drag = {pg, d, x: ev.clientX, y: ev.clientY};
}, true);
function boxRect(ev) {
  const r = BOX.drag.pg.el.getBoundingClientRect(), cl = (v, a, b) => Math.max(a, Math.min(b, v));
  const x0 = cl(Math.min(BOX.drag.x, ev.clientX), r.left, r.right), x1 = cl(Math.max(BOX.drag.x, ev.clientX), r.left, r.right);
  const y0 = cl(Math.min(BOX.drag.y, ev.clientY), r.top, r.bottom), y1 = cl(Math.max(BOX.drag.y, ev.clientY), r.top, r.bottom);
  return {page: r, left: x0, right: x1, top: y0, bottom: y1};
}
document.addEventListener('mousemove', ev => {
  if (!BOX.drag) return;
  const b = boxRect(ev), d = BOX.drag.d;
  Object.assign(d.style, {left: b.left - b.page.left + 'px', top: b.top - b.page.top + 'px',
                          width: b.right - b.left + 'px', height: b.bottom - b.top + 'px'});
});
document.addEventListener('mouseup', ev => {
  if (!BOX.drag) return;
  const {pg, d} = BOX.drag, b = boxRect(ev);
  BOX.drag = null;
  d.remove();
  if (b.right - b.left < 6 || b.bottom - b.top < 6) return;  // a click, not a box
  const kx = pg.w / b.page.width, ky = pg.h / b.page.height;
  const box = [(b.left - b.page.left) * kx, (b.top - b.page.top) * ky, (b.right - b.page.left) * kx, (b.bottom - b.page.top) * ky]
    .map(v => Math.round(v * 10) / 10);
  const quote = textInBox(pg, b);
  chatSel = {quote: `[the area ${box.join(',')} of page ${pg.n}] ${quote}`.trim().slice(0, 1500), page: pg.n};
  chat.renderCtx();
  compose({type: 'new', anchor: {page: pg.n, box, quote}, at: {fx: box[0] / pg.w, fy: box[3] / pg.h}});
});
// the cross cursor while ⌥ Option is down (the pointer also tells it, for a key that went down in another window)
const altClass = ev => document.body.classList.toggle('altdown', !!ev.altKey);
addEventListener('keydown', altClass);
addEventListener('keyup', altClass);
document.addEventListener('mousemove', altClass, {passive: true});
addEventListener('blur', () => document.body.classList.remove('altdown'));
addEventListener('keydown', ev => {  // Esc stops a box drag, also with ⌥ Option still down
  if (ev.key === 'Escape' && BOX.drag) { ev.preventDefault(); BOX.drag.d.remove(); BOX.drag = null; }
}, true);

$('cbtn').addEventListener('mousedown', ev => ev.preventDefault());  // keep the selection
$('cbtn').addEventListener('click', commentOnSelection);

// ------------------------------------------------------------ highlights: passages with no comment
async function highlightSelection() {
  const an = S.sel || selectionAnchor();
  if (!an) { toast('Select text on a page first.'); return; }
  $('selbar').style.display = 'none';
  getSelection().removeAllRanges();
  const {page, quote, prefix, suffix} = an;
  if (await op({op: 'highlight', anchor: {page, quote, prefix, suffix}})) toast('Highlighted.', 'Undo', undo);
}
$('hbtn').addEventListener('mousedown', ev => ev.preventDefault());
$('hbtn').addEventListener('click', highlightSelection);
let hmenuFor = null;  // the highlight of the open menu
function openHmenu(h, ev) {
  hmenuFor = h;
  const m = $('hmenu');
  m.style.display = 'flex';
  m.style.left = Math.min(window.innerWidth - m.offsetWidth - 8, ev.clientX + 6) + 'px';
  m.style.top = Math.max(48, ev.clientY + 10) + 'px';
}
function closeHmenu() { hmenuFor = null; $('hmenu').style.display = 'none'; }
document.addEventListener('mousedown', ev => { if (hmenuFor && !ev.target.closest('#hmenu')) closeHmenu(); });
$('hremove').addEventListener('click', async () => {
  const h = hmenuFor;
  closeHmenu();
  if (h && await op({op: 'unhighlight', highlight: h.id})) toast('Highlight removed.', 'Undo', undo);
});
$('hcomment').addEventListener('click', () => {  // the comment box opens at the highlight; the save makes it a comment
  const h = hmenuFor;
  closeHmenu();
  if (!h) return;
  const a = h.anchor, pg = pv.pages[a.page - 1];
  let at = {fx: 0, fy: 0};
  if (a.box) at = {fx: a.box[0] / pg.w, fy: a.box[3] / pg.h};
  else {
    const loc = pg.text && locate(pg.text.s, a);
    if (loc) {
      const r = [...pv.rangeOf(pg, loc.a, loc.b).getClientRects()].filter(x => x.width >= 1).pop(), b = pg.el.getBoundingClientRect();
      if (r) at = {fx: (r.left - b.left) / b.width, fy: (r.bottom - b.top) / b.height};
    }
  }
  compose({type: 'new', anchor: {...a}, at, fromHighlight: h.id});
});
// A click on the pages outside an empty comment box closes it. A box with text stays, so no text is lost.
$('pages').addEventListener('mousedown', ev => {
  if (S.comp && S.comp.type === 'new' && !S.comp.text.trim() && !ev.target.closest('#pop')) cancel();
});

// ------------------------------------------------------------ chat
const chat = kit.chatPanel({
  key: 'pdfview',
  account: {get: () => api('GET', '/api/account?f=' + encodeURIComponent(FILE)),
            post: body => api('POST', '/api/account', body)},
  poll: true,
  ready: () => !!S.doc,
  empty: 'Ask about this PDF. Claude can read it, and it answers or adds comments when you ask. ' +
    'It runs on the computer that serves this page. Turn on "Ask only" if it must not change anything.',
  get: since => api('GET', `/api/chat?f=${enc(FILE)}` + (since == null ? '' : `&since=${since}`)),
  post: body => api('POST', '/api/chat', body),
  extra: () => ({quote: chatSel ? chatSel.quote : '', page: chatSel ? chatSel.page : null, view_page: pv.curPage}),
  afterSend: () => { chatSel = null; },
  context: () => chatSel && {text: `p. ${chatSel.page} “${chatSel.quote}”`, title: chatSel.quote},
  hint: () => 'Select text to ask about it.',
  clearContext: () => { chatSel = null; },
  render: kit.md,
  typeset: box => kit.typeset(box),
  fillUser: (e, m) => {
    if (m.quote) { const q = el('div', 'cq', '“' + m.quote + '”'); q.title = m.quote; e.append(q); }
    e.append(m.text);
  },
  done: m => { if (m && m.changed) { loadDoc(); toast('Claude changed the comments.', 'Undo', undo, 15000); } },
  error: text => toast(text, null, null, 9000),
});

// ------------------------------------------------------------ notes to the app (self-improving apps)
// ⌘/Ctrl+Shift+. opens the dashboard of the kit: the notes wait there until Send all, which starts the automatic loop
// (viewkit/improvekit.py). The context is the state of the viewer only: never the text or the path of the PDF.
kit.improveDash({
  app: 'pdfview',
  get: () => api('GET', `/api/improve/dash?f=${enc(FILE)}`),
  post: body => api('POST', '/api/improve/dash', body),
  context: () => ({
    page: pv.curPage, pages: pv.pages.length, zoom: pv.zoom === 'fit' ? 'fit' : pv.scale, dark: !!S.dark,
    sidebar: !document.body.classList.contains('noside'), contents: !document.body.classList.contains('nonav'),
    comments: S.doc ? S.doc.threads.length : 0, composing: S.comp ? S.comp.type : null,
    chat: !$('chatBox').hidden, oracle: !!(oracle && oracle.running), selection: !!S.sel,
  }),
  onDone: (msg, label, fn) => toast(msg, label || null, fn || null, 12000),
});
// In the sandbox of a proposal (pdfview try, or the automatic loop): the band with Accept, Change and Reject.
kit.previewBand({
  get: () => api('GET', `/api/improve/preview?f=${enc(FILE)}`),
  post: body => api('POST', '/api/improve/preview', {...body, f: FILE}),
});

// ------------------------------------------------------------ the Oracle
// One background Claude run per PDF answers all its open comments (/answer-pdf). The server refuses a second run while
// one is going, from this tab or any other. Its replies show in the sidebar as the comments file changes.
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
  if (d.already) toast('Claude is already working on this file.');
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
      (oracle.session ? ', and continue its session on this file.' : ', in a new session.')
    : 'No open comments.';
  $('oracleRow').hidden = !oracle.session || oracle.running;
}

$('oracleBtn').onclick = () => {
  if (!oracle.running) oracleCall('start');
  else if (confirm('Stop Claude? The replies that it already wrote stay.')) oracleCall('stop');
};
$('newSession').onclick = () => {
  if (confirm('Start a new Claude session for this file? The next Oracle run does not remember earlier rounds.'))
    oracleCall('new').then(ok => ok && toast('The next Oracle run starts a new session.'));
};

// ------------------------------------------------------------ controls and keys
function setZoom(z) {
  pv.setZoom(z);
  store('zoom', String(pv.zoom));
}

$('zoomIn').onclick = () => setZoom(Math.round(pv.scale * 1.2 * 100) / 100);
$('zoomOut').onclick = () => setZoom(Math.round(pv.scale / 1.2 * 100) / 100);
$('zoomFit').onclick = () => setZoom('fit');
$('darkBtn').onclick = () => setDark(!S.dark);
$('undoBtn').onclick = undo;
$('sideBtn').onclick = () => setSide(document.body.classList.contains('noside'));
$('navBtn').onclick = () => setNav(document.body.classList.contains('nonav'));
$('generalBtn').onclick = () => compose({type: 'general'});
$('resolveAllBtn').onclick = () => {
  const n = S.doc ? S.doc.threads.length : 0;
  if (n && confirm(`Resolve all ${n} comment${n > 1 ? 's' : ''}? They are removed (Undo brings them back).`))
    op({op: 'resolve-all'}, 'All comments resolved.');
};

document.addEventListener('keydown', ev => {
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.shiftKey && (ev.key === 'M' || ev.key === 'm')) { ev.preventDefault(); commentOnSelection(); return; }
  if (mod && ev.shiftKey && (ev.key === 'H' || ev.key === 'h')) { ev.preventDefault(); highlightSelection(); return; }
  if (pv.navKey(ev)) return;  // ⌘/Ctrl+[ and ⌘/Ctrl+]: back and forward over the jumps in the PDF
  if (mod && ev.key === '\\') { ev.preventDefault(); setSide(document.body.classList.contains('noside')); return; }
  if (mod || ev.altKey || typing(ev.target)) return;
  if (ev.key === '+' || ev.key === '=') setZoom(Math.round(pv.scale * 1.2 * 100) / 100);
  else if (ev.key === '-') setZoom(Math.round(pv.scale / 1.2 * 100) / 100);
  else if (ev.key === '0') setZoom('fit');
  else if (ev.key === 'd') setDark(!S.dark);
  else if (ev.key === 'o') setNav(document.body.classList.contains('nonav'));
  else if (ev.key === 'Escape' && S.comp) cancel();
  else return;
  ev.preventDefault();
});

// ------------------------------------------------------------ news from the server
// The comments file can change outside this page (Claude, the command line, another tab): look every 2 seconds.
let polling = false;
async function poll() {
  if (polling || !S.doc || document.hidden) return;
  polling = true;
  try {
    const v = await api('GET', `/api/version?f=${enc(FILE)}`);
    $('live').classList.remove('off');
    if (v.pdf !== S.doc.pdf) {
      await loadDoc();
      await loadPdf(true);
      toast('The PDF changed on disk.');
    } else if (v.version !== S.doc.version) {
      await loadDoc();
    }
  } catch (e) { $('live').classList.add('off'); }
  finally { polling = false; }
}

// ------------------------------------------------------------ start
(async () => {
  kit.tab({app: 'pdf'});
  setDark((stored('dark') ?? (matchMedia('(prefers-color-scheme: dark)').matches ? '1' : '0')) === '1');
  setSide(stored('side') !== 'off');
  setNav(stored('nav') === 'on');
  const z = stored('zoom');
  pv.zoom = z && z !== 'fit' && !isNaN(+z) ? +z : 'fit';
  if (!FILE) { $('pages').replaceChildren(el('div', 'err-page', 'No file. Open one with: pdfview FILE.pdf')); return; }
  if (!await loadDoc()) return;
  oracleCall();
  await loadPdf(false);
  $('pages').focus();
  setInterval(poll, 2000);
})();
