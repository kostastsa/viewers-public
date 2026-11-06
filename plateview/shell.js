'use strict';
// plateview: the frame of a plate (/p/ID), or the list of plates (/). The plate runs in a sandboxed frame. It asks this
// frame for data and images by messages. The frame asks the server, and sends the answer back, and it tells the plate
// when a file changes. The poll of the frame (every 2 seconds) also tells the server that a tab shows the plate.

const $ = id => document.getElementById(id);
const PID = (location.pathname.match(/^\/p\/([a-z0-9]+)$/) || [])[1] || null;
const enc = encodeURIComponent;

async function api(method, path, body) {
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-PlateView': '1'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) { /* no body */ }
    const err = new Error(msg); err.status = r.status; throw err;
  }
  return r;
}
const getJSON = async path => (await api('GET', path)).json();
function toast(msg) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 2800);
}
function store(k, v) { try { localStorage.setItem('plateview.' + k, v); } catch (e) { /* not kept */ } }
function stored(k) { try { return localStorage.getItem('plateview.' + k); } catch (e) { return null; } }
const STATE = {session: 'kept while its Claude session runs', tab: 'kept while a tab shows it', new: 'new',
               kept: 'kept', null: 'deleted soon'};

// ------------------------------------------------------------ the theme: system, light or dark
function theme(t) {
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store('theme', t);
  $('themeBtn').textContent = {system: '◐ Auto', light: '☀ Light', dark: '☾ Dark'}[t] || '◐ Auto';
  const f = $('frame');
  if (f && f.contentWindow) f.contentWindow.postMessage({pv: 1, op: 'theme', theme: t}, '*');
}

// ------------------------------------------------------------ a plate
let meta = null, plateState = null, files = {};

function paintMeta() {
  $('title').textContent = meta.title;
  document.title = meta.title + ' · plateview';
  const s = $('state');
  s.hidden = false;
  s.textContent = STATE[meta.state] || meta.state;
  s.className = 'chip' + (meta.kept ? ' kept' : '');
  s.title = meta.kept ? 'This plate is kept. Delete it with Delete.' :
    `plateview deletes this plate ${Math.round(meta.grace / 60)} minutes after its Claude session and the last tab end.`;
  $('keepBtn').textContent = meta.kept ? 'Kept ✓' : 'Keep';
  $('keepBtn').classList.toggle('on', !!meta.kept);
}

async function openPlate() {
  try { meta = await getJSON(`/api/plate?p=${PID}`); }
  catch (e) { document.body.append(Object.assign(document.createElement('main'), {className: 'list', textContent: e.message})); return; }
  $('tools').hidden = false;
  paintMeta();
  plateState = meta.version;
  const f = document.createElement('iframe');
  f.id = 'frame';
  f.setAttribute('sandbox', 'allow-scripts');
  f.setAttribute('referrerpolicy', 'no-referrer');
  f.setAttribute('allow', 'clipboard-write');
  f.title = meta.title;
  f.src = `/p/${PID}/plate?theme=${stored('theme') || 'system'}`;
  document.body.append(f);
  setInterval(poll, 2000);
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

// The requests of the plate. Only messages from our own frame count.
addEventListener('message', async ev => {
  const f = $('frame');
  if (!f || ev.source !== f.contentWindow) return;
  const m = ev.data;
  if (!m || m.pv !== 1) return;
  if (m.op === 'error') { showError(m.message); return; }
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
      const r = await api('GET', `/api/file?p=${PID}&path=${enc(m.path)}`);
      const buf = await r.arrayBuffer();
      reply({type: r.headers.get('Content-Type') || 'application/octet-stream', buf}, [buf]);
    }
  } catch (e) { fail(e); }
});

let polling = false;
async function poll() {
  if (polling || document.hidden) return;
  polling = true;
  try {
    const v = await getJSON(`/api/version?p=${PID}`);
    $('live').classList.remove('off');
    if (v.plate !== plateState) {  // the HTML changed (plateview update): load the plate again
      plateState = v.plate;
      files = {};
      $('err').classList.remove('on');
      $('frame').src = `/p/${PID}/plate?theme=${stored('theme') || 'system'}&v=${Date.now()}`;
      meta = await getJSON(`/api/plate?p=${PID}`);
      paintMeta();
    } else {
      const changed = Object.keys(v.files).filter(p => p in files && files[p] !== v.files[p]);
      for (const p of Object.keys(v.files)) if (!(p in files)) files[p] = v.files[p];
      if (changed.length) {
        for (const p of changed) files[p] = v.files[p];
        $('frame').contentWindow.postMessage({pv: 1, op: 'changed', paths: changed}, '*');
      }
    }
  } catch (e) {
    $('live').classList.add('off');
    if (e.status === 404) { toast('This plate was deleted.'); setTimeout(() => { location.href = '/'; }, 1500); }
  } finally { polling = false; }
}

// ------------------------------------------------------------ the list of plates
async function openList() {
  document.title = 'plateview';
  const main = document.createElement('main');
  main.className = 'list';
  main.innerHTML = '<div class="in"><h1>Plates</h1><p id="hint"></p><div id="rows"></div></div>';
  document.body.append(main);
  const draw = async () => {
    let d;
    try { d = await getJSON('/api/plates'); $('live').classList.remove('off'); }
    catch (e) { $('live').classList.add('off'); return; }
    $('hint').textContent = `A plate stays while the Claude session that made it runs, or while a tab shows it. ` +
      `plateview deletes it ${Math.round(d.grace / 60)} minutes after both end. Keep makes a plate permanent.`;
    if (!d.plates.length) { $('rows').replaceChildren(Object.assign(document.createElement('p'), {textContent: 'No plates. Make one with: plateview new FILE.html'})); return; }
    const t = document.createElement('table');
    t.innerHTML = '<thead><tr><th>Plate</th><th>Made</th><th>State</th><th></th></tr></thead>';
    const tb = document.createElement('tbody');
    for (const p of d.plates) {
      const tr = document.createElement('tr'), a = document.createElement('a');
      a.href = '/p/' + p.id; a.textContent = p.title;
      const t1 = document.createElement('td'); t1.className = 't'; t1.append(a, document.createElement('br'), Object.assign(document.createElement('span'), {className: 'id', textContent: p.id}));
      const t2 = document.createElement('td'); t2.textContent = new Date(p.created * 1000).toLocaleString([], {dateStyle: 'short', timeStyle: 'short'});
      const t3 = document.createElement('td'), chip = document.createElement('span');
      chip.className = 'chip' + (p.kept ? ' kept' : ''); chip.textContent = STATE[p.state] || p.state; t3.append(chip);
      const t4 = document.createElement('td'); t4.className = 'acts';
      const del = document.createElement('button');
      del.type = 'button'; del.className = 'danger'; del.textContent = 'Delete';
      del.onclick = async () => { if (!confirm(`Delete the plate “${p.title}”?`)) return; await api('POST', '/api/delete', {p: p.id}); draw(); };
      t4.append(del);
      tr.append(t1, t2, t3, t4);
      tb.append(tr);
    }
    t.append(tb);
    $('rows').replaceChildren(t);
  };
  draw();
  setInterval(draw, 5000);
}

// ------------------------------------------------------------ the buttons
$('themeBtn').onclick = () => theme({system: 'light', light: 'dark', dark: 'system'}[stored('theme') || 'system']);
$('keepBtn').onclick = async () => {
  const r = await (await api('POST', '/api/keep', {p: PID, keep: !meta.kept})).json();
  meta.kept = r.kept;
  meta.state = r.kept ? 'kept' : 'tab';
  paintMeta();
  toast(r.kept ? 'Kept. plateview does not delete this plate.' : 'Not kept. plateview deletes it when its session and the tabs end.');
};
$('exportBtn').onclick = async () => {
  try {
    const r = await api('GET', `/api/export?p=${PID}`);
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (meta.title.replace(/[^\w .-]+/g, '').trim() || 'plate') + '.html';
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('Exported: one HTML file with the data inside.');
  } catch (e) { toast('Export failed: ' + e.message); }
};
$('deleteBtn').onclick = async () => {
  if (!confirm('Delete this plate now? Its data files stay where they are.')) return;
  await api('POST', '/api/delete', {p: PID});
  location.href = '/';
};

kit.tab({app: 'plate'});
theme(stored('theme') || 'system');
if (PID) openPlate(); else openList();
