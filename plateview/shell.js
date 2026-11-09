'use strict';
// plateview: the frame of a plate (/p/ID), or the list of plates (/). The plate runs in a sandboxed frame. It asks this
// frame for data and images by messages. The frame asks the server, and sends the answer back, and it tells the plate
// when a file changes. The poll of the frame (every 2 seconds) also tells the server that a tab shows the plate.
// The frame also has the comments of the plate (the sidebar), the chat with Claude (kit.chatPanel), the Oracle, and the
// dashboard of the notes to the app (kit.improveDash). The plate tells the frame what the user selects, and paints the
// marks of the comments (runtime.js, "the bridge"). Only the frame changes the comments, from its own buttons.

const $ = id => document.getElementById(id);
const PID = (location.pathname.match(/^\/p\/([a-z0-9]+)$/) || [])[1] || null;
const enc = encodeURIComponent;

// ------------------------------------------------------------ helpers
async function fetchOk(method, path, body) {
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-PlateView': '1'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) { /* no body */ }
    const err = new Error(msg); err.status = r.status; throw err;
  }
  return r;
}
// A JSON request. A POST names the plate of this tab.
const api = async (method, path, body) => (await fetchOk(method, path, method === 'POST' ? {p: PID, ...body} : undefined)).json();
const getJSON = path => api('GET', path);
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
function toast(msg, label, fn, ms = 4000) {
  const t = el('div', 'toast');
  t.append(el('span', null, msg));
  if (label) t.append(btn(label, '', () => { t.remove(); fn(); }));
  $('toast').append(t);
  setTimeout(() => t.remove(), ms);
}
function store(k, v) { try { localStorage.setItem('plateview.' + k, v); } catch (e) { /* not kept */ } }
function stored(k) { try { return localStorage.getItem('plateview.' + k); } catch (e) { return null; } }
const oneLine = s => String(s).replace(/\s+/g, ' ').trim();
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const typing = t => t && t.closest && t.closest('input, textarea, select, [contenteditable]');
const STATE = {session: 'kept while its Claude session runs', tab: 'kept while a tab shows it', new: 'new',
               kept: 'kept', null: 'deleted soon'};

// ------------------------------------------------------------ the theme: system, light or dark
function theme(t) {
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store('theme', t);
  $('themeBtn').textContent = {system: '◐ Auto', light: '☀ Light', dark: '☾ Dark'}[t] || '◐ Auto';
  toPlate({op: 'theme', theme: t});
}

// ------------------------------------------------------------ a plate
const S = {
  meta: null,       // the payload of /api/plate: the settings, the state and the comments of the plate
  active: null,     // the id of the selected thread
  comp: null,       // the open comment box: {type: 'new' | 'general' | 'reply' | 'edit', anchor, rect, thread, msg, text}
  sel: null,        // the anchor of the selected words or cell in the plate, or null
  lost: new Set(),  // threads whose words or cell are not in the plate now
};
let plateState = null, files = {};
const toPlate = m => { const f = $('frame'); if (f && f.contentWindow) f.contentWindow.postMessage({pv: 1, ...m}, '*'); };

function paintMeta() {
  const meta = S.meta;
  $('title').textContent = meta.title;
  document.title = kit.title(meta.title + ' · plateview');
  const s = $('state');
  s.hidden = false;
  s.textContent = STATE[meta.state] || meta.state;
  s.className = 'chip' + (meta.kept ? ' kept' : '');
  s.title = meta.kept ? 'This plate is kept. Delete it with Delete.' :
    `plateview deletes this plate ${Math.round(meta.grace / 60)} minutes after its Claude session and the last tab end.`;
  $('keepBtn').textContent = meta.kept ? 'Kept ✓' : 'Keep';
  $('keepBtn').classList.toggle('on', !!meta.kept);
  $('undoBtn').disabled = !meta.canUndo;
}

async function loadMeta() {
  S.meta = await getJSON(`/api/plate?p=${PID}`);
  paintMeta();
  renderThreads();
  sendMarks();
}

async function openPlate() {
  try { S.meta = await getJSON(`/api/plate?p=${PID}`); }
  catch (e) { $('stage').append(el('main', 'list', e.message)); document.body.classList.add('list'); return; }
  $('tools').hidden = false;
  paintMeta();
  renderThreads();
  plateState = S.meta.version;
  const f = document.createElement('iframe');
  f.id = 'frame';
  f.setAttribute('sandbox', 'allow-scripts');
  f.setAttribute('referrerpolicy', 'no-referrer');
  f.setAttribute('allow', 'clipboard-write');
  f.title = S.meta.title;
  f.addEventListener('load', () => { S.lost.clear(); sendMarks(); });
  f.src = `/p/${PID}/plate?theme=${stored('theme') || 'system'}`;
  $('stage').append(f);
  setInterval(poll, 2000);
  oracleCall();
}

function showError(msg) {
  const e = $('err');
  e.replaceChildren(document.createTextNode('The plate has an error: ' + msg));
  const x = document.createElement('button');
  x.type = 'button'; x.textContent = 'Hide';
  x.onclick = () => e.classList.remove('on');
  e.append(x);
  e.classList.add('on');
}

// A rectangle of the plate in the coordinates of this page.
function pageRect(r) {
  const f = $('frame');
  if (!r || !f) return null;
  const b = f.getBoundingClientRect();
  return {left: b.left + r.left, top: b.top + r.top, right: b.left + r.right, bottom: b.top + r.bottom};
}

// The messages of the plate. Only messages from our own frame count. The plate is not trusted: a message can only ask
// for a file that the plate may read, or show something in this page. It never changes the comments.
addEventListener('message', async ev => {
  const f = $('frame');
  if (!f || ev.source !== f.contentWindow) return;
  const m = ev.data;
  if (!m || m.pv !== 1) return;
  if (m.op === 'error') { showError(m.message); return; }
  if (m.op in BRIDGE) { BRIDGE[m.op](m); return; }
  if (!m.id || typeof m.path !== 'string') return;
  const reply = (value, transfer) => f.contentWindow.postMessage({pv: 1, id: m.id, value}, '*', transfer || []);
  const fail = e => f.contentWindow.postMessage({pv: 1, id: m.id, error: e.message}, '*');
  try {
    if (m.op === 'data') {
      const d = await getJSON(`/api/data?p=${PID}&path=${enc(m.path)}`);
      files[m.path] = d.state;
      reply({columns: d.columns, rows: d.rows, truncated: d.truncated});
    } else if (m.op === 'json') {
      const d = await getJSON(`/api/json?p=${PID}&path=${enc(m.path)}`);
      files['json:' + m.path] = d.state;
      reply(d.json);
    } else if (m.op === 'file') {
      const r = await fetchOk('GET', `/api/file?p=${PID}&path=${enc(m.path)}`);
      const buf = await r.arrayBuffer();
      reply({type: r.headers.get('Content-Type') || 'application/octet-stream', buf}, [buf]);
    }
  } catch (e) { fail(e); }
});

const str = (v, n) => String(v == null ? '' : v).slice(0, n);
const cleanAnchor = a => !a || typeof a !== 'object' ? null : a.col != null
  ? {table: str(a.table, 300), row: str(a.row, 300), col: str(a.col, 300)}
  : {quote: str(a.quote, 2000), prefix: str(a.prefix, 40), suffix: str(a.suffix, 40)};
const cleanRect = r => r && typeof r === 'object' && ['left', 'top', 'right', 'bottom'].every(k => Number.isFinite(r[k])) ? r : null;
// The messages of the bridge (runtime.js).
const BRIDGE = {
  sel(m) {  // the user selected words in the plate (anchor), or nothing (null)
    const a = cleanAnchor(m.anchor);
    if (a && a.quote) {
      S.sel = {...a, rect: cleanRect(m.rect)};
      showCbtn(pageRect(S.sel.rect), '💬 Comment');
      const q = oneLine(a.quote).slice(0, 1500);
      if (!chatSel || chatSel.quote !== q) { chatSel = {quote: q}; chat.renderCtx(); }
    } else if (S.sel && S.sel.quote != null) { S.sel = null; $('cbtn').style.display = 'none'; }
  },
  cell(m) {  // the user clicked a cell of a table or a grid
    const a = cleanAnchor(m.anchor);
    if (!a || a.col == null) return;
    S.sel = {...a, rect: cleanRect(m.rect)};
    showCbtn(pageRect(S.sel.rect), '💬 Comment on cell');
    chatSel = {quote: '[table ' + a.table + '] ' + str(m.text, 500)};
    chat.renderCtx();
    const t = (S.meta ? S.meta.threads : []).find(x => x.anchor && x.anchor.col != null && sameCell(x.anchor, a));
    if (t) setActive(t.id, true);
  },
  pick(m) { if (Number.isInteger(m.id)) setActive(m.id, true); },  // a click on marked words
  lost(m) {
    const ids = new Set((Array.isArray(m.ids) ? m.ids : []).filter(Number.isInteger));
    if ([...ids].join() === [...S.lost].join()) return;
    S.lost = ids;
    renderThreads();
  },
  draft(m) { if (S.comp && S.comp.type === 'new') { S.comp.rect = cleanRect(m.rect) || S.comp.rect; placePop(); } },
  key(m) {  // a key of this page, pushed while the plate has the focus
    if (!['KeyS', 'KeyM', 'Period', 'Backslash', 'KeyP'].includes(m.code)) return;
    document.dispatchEvent(new KeyboardEvent('keydown', {code: m.code, key: str(m.key, 4), metaKey: !!m.meta,
      ctrlKey: !!m.ctrl, shiftKey: !!m.shift, bubbles: true, cancelable: true}));
  },
};
const sameCell = (x, y) => x.table === y.table && String(x.row) === String(y.row) && String(x.col) === String(y.col);

// Tell the plate which comments to mark: their anchors, the selected thread, and the anchor of a new comment.
function sendMarks() {
  if (!S.meta) return;
  const marks = S.meta.threads.filter(t => t.anchor).map(t => ({id: t.id, anchor: t.anchor}));
  const draft = S.comp && S.comp.type === 'new' ? S.comp.anchor : null;
  toPlate({op: 'marks', marks, active: S.active, draft: draft && (draft.col != null
    ? {table: draft.table, row: draft.row, col: draft.col} : {quote: draft.quote, prefix: draft.prefix, suffix: draft.suffix})});
}

// ------------------------------------------------------------ the sidebar
function setActive(id, fromPlate) {
  S.active = id;
  sendMarks();
  document.querySelectorAll('.thread').forEach(c => c.classList.toggle('active', +c.dataset.t === id));
  if (fromPlate) {
    if (document.body.classList.contains('noside')) setSide(true);
    const card = document.querySelector(`.thread[data-t="${id}"]`);
    if (card) card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  } else toPlate({op: 'show', id});
}
function threadOrder(ts) {
  const k = t => !t.anchor ? 0 : t.anchor.col == null ? 1 : 2;
  return [...ts].sort((x, y) => k(x) - k(y) || x.id - y.id);
}
function whereOf(a) {
  if (!a) return null;
  if (a.col != null) return {where: '▦ ' + a.table, q: `${a.row} · ${a.col}`};
  return {where: '', q: oneLine(a.quote)};
}
function renderThreads() {
  const box = $('threads'), ts = S.meta ? S.meta.threads : [];
  const nOpen = ts.filter(t => t.status === 'open').length;
  $('counts').textContent = ts.length ? `${nOpen} open · ${ts.length - nOpen} answered` : 'Comments';
  $('sideBtn').textContent = ts.length ? `Comments ${ts.length}` : 'Comments';
  paintOracle();
  kit.tab({open: nOpen});
  const frag = document.createDocumentFragment();
  if (S.comp && S.comp.type === 'general') frag.append(composer('Comment on the plate'));
  $('resolveAllBtn').hidden = !ts.length;
  $('resolveAllBtn').textContent = `Resolve all (${ts.length})`;
  if (!ts.length && !(S.comp && S.comp.type === 'general'))
    frag.append(el('p', 'empty', 'Select words in the plate, or click a table cell, to comment on it. Or add a comment about the whole plate.'));
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
  else top.append(el('span', 'gen', '▣ General comment'));
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

// The comment box of a new comment on selected words or on a cell. It floats over the plate, under the words, so the
// sidebar can stay closed.
function renderPop() {
  const old = $('pop'), c = S.comp;
  if (!c || c.type !== 'new') { if (old) old.remove(); return; }
  if (old && old.comp === c) { placePop(); return; }
  if (old) old.remove();
  const p = composer('Comment');
  p.id = 'pop';
  p.comp = c;
  p.prepend(el('div', 'where', c.anchor.col != null ? `Comment on the cell ${c.anchor.row} · ${c.anchor.col}` : 'Comment on the selected words'));
  document.body.append(p);
  placePop();
  const ta = p.querySelector('textarea');
  if (c.focus) { ta.focus({preventScroll: true}); c.focus = false; }
}
function placePop() {
  const p = $('pop');
  if (!p || !S.comp) return;
  const r = pageRect(S.comp.rect) || {left: innerWidth / 2 - 170, top: innerHeight / 3, bottom: innerHeight / 3};
  p.style.left = Math.max(8, Math.min(r.left, innerWidth - p.offsetWidth - 8)) + 'px';
  let top = r.bottom + 6;
  if (top + p.offsetHeight > innerHeight - 8) top = Math.max(8, r.top - p.offsetHeight - 6);
  p.style.top = top + 'px';
}
function compose(c) {
  S.comp = {...c, text: c.text || '', focus: true};
  if (c.type !== 'new' && document.body.classList.contains('noside')) setSide(true);
  renderThreads();
  sendMarks();
  const box = c.type !== 'new' && document.querySelector('#threads .composer');
  if (box) box.scrollIntoView({block: 'nearest'});
}
function cancel() { S.comp = null; renderThreads(); sendMarks(); }
async function save() {
  const c = S.comp;
  if (!c || !c.text.trim()) { toast('Write a comment first.'); return; }
  let body;
  if (c.type === 'new') {
    const a = c.anchor;
    body = {op: 'add', anchor: a.col != null ? {table: a.table, row: a.row, col: a.col} :
      {quote: a.quote, prefix: a.prefix, suffix: a.suffix}, text: c.text};
  } else if (c.type === 'general') body = {op: 'add', text: c.text};
  else if (c.type === 'reply') body = {op: 'reply', thread: c.thread, text: c.text};
  else body = {op: 'edit', thread: c.thread, msg: c.msg, text: c.text};
  S.comp = null;
  const d = await op(body);
  if (!d) { S.comp = c; renderThreads(); sendMarks(); return; }
  if (d.added) { S.active = d.added; renderThreads(); sendMarks(); }
}
async function op(body, done) {
  try {
    S.meta = await api('POST', '/api/op', {...body, version: S.meta.cversion});
  } catch (e) {
    toast(e.message, null, null, 8000);
    if (e.status === 409) await loadMeta();
    return null;
  }
  paintMeta();
  renderThreads();
  sendMarks();
  if (done) toast(done, 'Undo', undo, 6000);
  return S.meta;
}
function undo() { return op({op: 'undo'}).then(d => d && toast('Undone.')); }

function setSide(on) {
  document.body.classList.toggle('noside', !on);
  $('sideBtn').classList.toggle('on', on);
  store('side', on ? 'on' : 'off');
  setTimeout(placePop, 0);
}

// ------------------------------------------------------------ the selection: the comment button and the chat context
let chatSel = null;  // {quote}: the words or the cell that was last selected, for the chat

function showCbtn(rect, label) {
  const b = $('cbtn');
  if (!rect) return;
  b.textContent = label;
  b.style.display = 'block';
  b.style.left = Math.max(8, Math.min(innerWidth - b.offsetWidth - 8, rect.right + 6)) + 'px';
  b.style.top = Math.max(48, Math.min(innerHeight - 40, rect.bottom + 4)) + 'px';
}
function commentOnSelection() {
  const an = S.sel;
  if (!an) { toast('Select words in the plate, or click a table cell, first.'); return; }
  $('cbtn').style.display = 'none';
  S.sel = null;
  toPlate({op: 'unselect'});
  compose({type: 'new', anchor: an, rect: an.rect});
}
$('cbtn').addEventListener('mousedown', ev => ev.preventDefault());
$('cbtn').addEventListener('click', commentOnSelection);
document.addEventListener('mousedown', ev => {
  if (ev.target.closest('#cbtn, #pop')) return;
  // a click outside an empty comment box closes it. A box with text stays, so no text is lost.
  if (S.comp && S.comp.type === 'new' && !S.comp.text.trim()) cancel();
});

// ------------------------------------------------------------ chat
const chat = kit.chatPanel({
  key: 'plateview',
  poll: true,
  ready: () => !!S.meta,
  empty: 'Ask about this plate. Claude can read the plate and its data files. It can change the plate, and answer or ' +
    'add comments, when you ask. It runs on the computer that serves this plate. Turn on "Ask only" if it must not change anything.',
  get: since => getJSON(`/api/chat?p=${PID}` + (since == null ? '' : `&since=${since}`)),
  post: body => api('POST', '/api/chat', body),
  extra: () => ({quote: chatSel ? chatSel.quote : ''}),
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
    if (m && m.changed) loadMeta().then(() => toast('Claude changed the comments.', 'Undo', undo, 15000));
  },
  error: text => toast(text, null, null, 9000),
});

// ------------------------------------------------------------ the Oracle
// One background Claude run per plate answers all its open comments (/answer-plate). Its replies show in the sidebar
// as the comments file changes.
let oracle = {running: false}, oracleTimer = null;
async function oracleCall(action) {
  let d;
  try {
    d = action ? await api('POST', '/api/claude', {action}) : await getJSON(`/api/claude?p=${PID}`);
  } catch (e) {
    if (e.status) toast(e.message, null, null, 9000);
    else oracleTimer = setTimeout(oracleCall, 5000);
    return false;
  }
  const was = oracle.running;
  oracle = d;
  if (d.already) toast('Claude is already working on this plate.');
  if (was && !d.running) oracleDone(d);
  paintOracle();
  clearTimeout(oracleTimer);
  if (d.running) oracleTimer = setTimeout(oracleCall, 3000);
  return true;
}
async function oracleDone(d) {
  await loadMeta();
  const report = () => { $('reportText').textContent = d.report || '(no output)'; $('reportDlg').showModal(); };
  if (d.exit === 0) toast('Claude has answered the comments.', 'Report', report, 12000);
  else if (d.exit == null) toast('Claude was stopped.');
  else toast(`Claude stopped with an error (exit ${d.exit}).`, 'Details', report, 15000);
}
function paintOracle() {
  const b = $('oracleBtn'), open = S.meta ? S.meta.threads.filter(t => t.status === 'open').length : 0;
  b.classList.toggle('busy', !!oracle.running);
  kit.tab({oracle: !!oracle.running});
  b.textContent = oracle.running ? 'Working… ■' : 'Oracle';
  b.disabled = !oracle.running && !open;
  b.title = oracle.running ? `Answering the comments since ${oracle.started}. Click to stop.`
    : open ? `Start Claude in the background to answer the ${plural(open, 'open comment')}` +
      (oracle.session ? ', and continue its session on this plate.' : ', in a new session.')
    : 'No open comments.';
  $('oracleRow').hidden = !oracle.session || oracle.running;
}
$('oracleBtn').onclick = () => {
  if (!oracle.running) oracleCall('start');
  else if (confirm('Stop Claude? The replies that it already wrote stay.')) oracleCall('stop');
};
$('newSession').onclick = () => {
  if (confirm('Start a new Claude session for this plate? The next Oracle run does not remember earlier rounds.'))
    oracleCall('new').then(ok => ok && toast('The next Oracle run starts a new session.'));
};

// ------------------------------------------------------------ news from the server
let polling = false;
async function poll() {
  if (polling || document.hidden) return;
  polling = true;
  try {
    const v = await getJSON(`/api/version?p=${PID}`);
    $('live').classList.remove('off');
    if (v.plate !== plateState) {  // the HTML changed (plateview update, or the chat): load the plate again
      plateState = v.plate;
      files = {};
      $('err').classList.remove('on');
      $('frame').src = `/p/${PID}/plate?theme=${stored('theme') || 'system'}&v=${Date.now()}`;
      await loadMeta();
    } else {
      const changed = Object.keys(v.files).filter(p => p in files && files[p] !== v.files[p]);
      for (const p of Object.keys(v.files)) if (!(p in files)) files[p] = v.files[p];
      if (changed.length) {
        for (const p of changed) files[p] = v.files[p];
        toPlate({op: 'changed', paths: changed});
      }
      if (v.comments !== S.meta.cversion) await loadMeta();  // Claude or the command line changed the comments
    }
  } catch (e) {
    $('live').classList.add('off');
    if (e.status === 404) { toast('This plate was deleted.'); setTimeout(() => { location.href = '/'; }, 1500); }
  } finally { polling = false; }
}

// ------------------------------------------------------------ the list of plates
async function openList() {
  document.body.classList.add('list');
  document.title = 'plateview';
  const main = el('main', 'list');
  main.innerHTML = '<div class="in"><h1>Plates</h1><p id="hint"></p><div id="rows"></div></div>';
  $('stage').append(main);
  const draw = async () => {
    let d;
    try { d = await getJSON('/api/plates'); $('live').classList.remove('off'); }
    catch (e) { $('live').classList.add('off'); return; }
    $('hint').textContent = `A plate stays while the Claude session that made it runs, or while a tab shows it. ` +
      `plateview deletes it ${Math.round(d.grace / 60)} minutes after both end. Keep makes a plate permanent.`;
    if (!d.plates.length) { $('rows').replaceChildren(el('p', null, 'No plates. Make one with: plateview new FILE.html')); return; }
    const t = el('table');
    t.innerHTML = '<thead><tr><th>Plate</th><th>Made</th><th>State</th><th></th></tr></thead>';
    const tb = el('tbody');
    for (const p of d.plates) {
      const tr = el('tr'), a = el('a', null, p.title);
      a.href = '/p/' + p.id;
      const t1 = el('td', 't');
      t1.append(a);
      if (p.open) t1.append(el('span', 'b', `${p.open} open`));
      t1.append(el('br'), el('span', 'id', p.id));
      const t2 = el('td', null, new Date(p.created * 1000).toLocaleString([], {dateStyle: 'short', timeStyle: 'short'}));
      const t3 = el('td'), chip = el('span', 'chip' + (p.kept ? ' kept' : ''), STATE[p.state] || p.state);
      t3.append(chip);
      const t4 = el('td', 'acts');
      t4.append(btn('Delete', '', async () => {
        if (!confirm(`Delete the plate “${p.title}”?`)) return;
        await api('POST', '/api/delete', {p: p.id});
        draw();
      }, 'danger'));
      tr.append(t1, t2, t3, t4);
      tb.append(tr);
    }
    t.append(tb);
    $('rows').replaceChildren(t);
  };
  draw();
  setInterval(draw, 5000);
}

// ------------------------------------------------------------ the buttons and the keys
$('themeBtn').onclick = () => theme({system: 'light', light: 'dark', dark: 'system'}[stored('theme') || 'system']);
$('keepBtn').onclick = async () => {
  const r = await api('POST', '/api/keep', {keep: !S.meta.kept});
  S.meta.kept = r.kept;
  S.meta.state = r.kept ? 'kept' : 'tab';
  paintMeta();
  toast(r.kept ? 'Kept. plateview does not delete this plate.' : 'Not kept. plateview deletes it when its session and the tabs end.');
};
$('exportBtn').onclick = async () => {
  try {
    const r = await fetchOk('GET', `/api/export?p=${PID}`);
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (S.meta.title.replace(/[^\w .-]+/g, '').trim() || 'plate') + '.html';
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('Exported: one HTML file with the data inside.');
  } catch (e) { toast('Export failed: ' + e.message); }
};
// A PDF of the plate: the print view opens in a new tab (the export, with no network) and opens the print dialog.
function printPlate() {
  window.open(`/p/${PID}/print?theme=light`, '_blank', 'noopener');
  toast('The print view opens in a new tab. To save a PDF, select “Save as PDF” in the print dialog.');
}
$('deleteBtn').onclick = async () => {
  if (!confirm('Delete this plate now? Its data files stay where they are. Its comments and its chat go with it.')) return;
  await api('POST', '/api/delete', {});
  location.href = '/';
};
$('undoBtn').onclick = undo;
$('sideBtn').onclick = () => setSide(document.body.classList.contains('noside'));
$('generalBtn').onclick = () => compose({type: 'general'});
$('resolveAllBtn').onclick = () => {
  const n = S.meta ? S.meta.threads.length : 0;
  if (n && confirm(`Resolve all ${plural(n, 'comment')}? They are removed (Undo brings them back).`))
    op({op: 'resolve-all'}, 'All comments resolved.');
};
addEventListener('resize', () => placePop());
document.addEventListener('keydown', ev => {
  if (!PID) return;
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.shiftKey && ev.code === 'KeyM') { ev.preventDefault(); commentOnSelection(); return; }
  if (mod && ev.code === 'Backslash') { ev.preventDefault(); setSide(document.body.classList.contains('noside')); return; }
  if (mod && !ev.shiftKey && !ev.altKey && ev.code === 'KeyP') { ev.preventDefault(); printPlate(); return; }
  if (mod || ev.altKey || typing(ev.target)) return;
  if (ev.key === 'Escape' && S.comp) { ev.preventDefault(); cancel(); }
});

// ------------------------------------------------------------ notes to the app (self-improving apps)
kit.improveDash({
  app: 'plateview',
  get: () => getJSON('/api/improve/dash' + (PID ? `?p=${PID}` : '')),
  post: body => api('POST', '/api/improve/dash', body),
  context: () => ({
    page: PID ? 'plate' : 'list', theme: stored('theme') || 'system', kept: !!(S.meta && S.meta.kept),
    state: S.meta ? S.meta.state : null, files: Object.keys(files).length, error: $('err').classList.contains('on'),
    comments: S.meta ? S.meta.threads.length : 0, sidebar: !document.body.classList.contains('noside'),
    composing: S.comp ? S.comp.type : null, chat: !$('chatBox').hidden, oracle: !!oracle.running,
  }),
  onDone: (msg, label, fn) => toast(msg, label || null, fn || null, 12000),
});
kit.previewBand({
  get: () => getJSON('/api/improve/preview'),
  post: body => api('POST', '/api/improve/preview', body),
});

kit.tab({app: 'plate'});
theme(stored('theme') || 'system');
if (PID) { setSide(stored('side') === 'on'); openPlate(); } else openList();
