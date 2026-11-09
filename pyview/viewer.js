'use strict';
// pyview viewer: cells, outputs, editing and comments. Vanilla JS; talks to pyview.py.

const $ = id => document.getElementById(id);
const FILE = new URLSearchParams(location.search).get('f') || '';
const enc = encodeURIComponent;
const S = {math: [], seq: 0, gen: 0, doc: null, sel: null, editing: null, editBase: null, editor: null, comp: null, rename: null, kernel: null, live: false,
           claude: false, pend: {}};

// ------------------------------------------------------------ helpers
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function btn(label, cls, title, fn) {
  const b = el('button', cls, label);
  if (title) b.title = title;
  b.addEventListener('click', ev => { ev.stopPropagation(); fn(ev); });
  return b;
}

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: {'Content-Type': 'application/json', 'X-PyView': '1'},
    body: body === undefined ? undefined : JSON.stringify({f: FILE, ...body})});
  let data = {};
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) { const err = new Error(data.error || r.statusText); err.status = r.status; throw err; }
  return data;
}

function toast(msg, label, fn, ms = 6000) {
  const t = el('div', 'toast');
  t.appendChild(el('span', '', esc(msg)));
  if (label) t.appendChild(btn(label, '', '', () => { t.remove(); fn(); }));
  $('toast').appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function openMenu(anchor, items) {
  document.querySelectorAll('.menu').forEach(m => m.remove());
  const m = el('div', 'menu');
  for (const it of items) m.appendChild(btn(it.label, '', '', () => { m.remove(); it.fn(); }));
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  m.style.top = Math.min(r.bottom + 2, innerHeight - m.offsetHeight - 8) + 'px';
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - m.offsetWidth - 8)) + 'px';
  const close = ev => { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener('mousedown', close, true); } };
  setTimeout(() => document.addEventListener('mousedown', close, true), 0);
}

// ------------------------------------------------------------ Python highlighting (display only)
const KW = new Set('False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'.split(' '));
const BI = new Set('print len range int float str list dict set tuple bool open enumerate zip map filter sum min max abs sorted reversed isinstance type super self cls object repr round any all iter next input format getattr setattr hasattr vars dir id hash bytes callable'.split(' '));
const tok = (c, s) => `<span class="tk-${c}">${esc(s)}</span>`;

function hlLines(lines) {
  let tq = null;
  return lines.map(line => {
    let i = 0, h = '', prev = '';
    const n = line.length;
    while (i < n) {
      if (tq) {
        const j = line.indexOf(tq, i);
        if (j < 0) { h += tok('str', line.slice(i)); i = n; } else { h += tok('str', line.slice(i, j + 3)); i = j + 3; tq = null; }
        continue;
      }
      const ch = line[i];
      if (ch === '#') { h += tok('com', line.slice(i)); i = n; }
      else if (ch === '"' || ch === "'") {
        const t3 = ch.repeat(3);
        if (line.startsWith(t3, i)) {
          const j = line.indexOf(t3, i + 3);
          if (j < 0) { h += tok('str', line.slice(i)); tq = t3; i = n; } else { h += tok('str', line.slice(i, j + 3)); i = j + 3; }
        } else {
          let j = i + 1;
          while (j < n && line[j] !== ch) j += line[j] === '\\' ? 2 : 1;
          h += tok('str', line.slice(i, j + 1)); i = j + 1;
        }
      } else if (/[A-Za-z_]/.test(ch)) {
        let j = i + 1;
        while (j < n && /\w/.test(line[j])) j++;
        const w = line.slice(i, j);
        if (prev === 'def' || prev === 'class') h += tok('def', w);
        else if (KW.has(w)) h += tok('kw', w);
        else if (BI.has(w) && line[j] === '(' || w === 'self') h += tok('bi', w);
        else h += esc(w);
        prev = w; i = j; continue;
      } else if (/\d/.test(ch) && !/\w/.test(line[i - 1] || ' ')) {
        let j = i + 1;
        while (j < n && /[\w.]/.test(line[j])) j++;
        h += tok('num', line.slice(i, j)); i = j;
      } else { h += esc(ch); i++; }
      if (!/\s/.test(ch)) prev = '';
    }
    return h;
  });
}

// ------------------------------------------------------------ markdown (small, with maths left for MathJax)
const safeUrl = u => !/^[a-z][a-z0-9+.-]*:/i.test(u) || /^(https?|mailto):/i.test(u);

function mdInline(s) {
  const stash = [];
  const keep = h => { stash.push(h); return '\u0001' + (stash.length - 1) + '\u0001'; };
  s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, a, code) => keep('<code>' + esc(code.trim()) + '</code>'));
  s = s.replace(/\$\$[\s\S]+?\$\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]|\$(?![\s\d])[^$\n]*?[^\s\\$]\$(?!\d)|\$[^\s$\\]\$(?!\d)/g, m => keep(esc(m)));
  s = esc(s);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => safeUrl(u) ? `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>` : t);
  s = s.replace(/\*\*(.+?)\*\*|__(.+?)__/g, (m, a, b) => `<strong>${a || b}</strong>`);
  s = s.replace(/(^|[^\w*])\*([^*\s][^*]*?)\*(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/~~(.+?)~~/g, '<del>$1</del>');
  return s.replace(/\u0001(\d+)\u0001/g, (_, i) => stash[+i]);
}

function mdList(lines, i) {
  const items = [];
  while (i < lines.length) {
    const m = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]);
    if (m) { items.push({ind: m[1].length, ord: /\d/.test(m[2]), text: m[3]}); i++; }
    else if (items.length && /^\s+\S/.test(lines[i])) { items[items.length - 1].text += ' ' + lines[i].trim(); i++; }
    else break;
  }
  let k = 0;
  const level = ind => {
    const tag = items[k].ord ? 'ol' : 'ul';
    let h = `<${tag}>`;
    while (k < items.length && items[k].ind >= ind) {
      h += '<li>' + mdInline(items[k].text);
      k++;
      if (k < items.length && items[k].ind > ind) h += level(items[k].ind);
      h += '</li>';
    }
    return h + `</${tag}>`;
  };
  return [level(items[0].ind), i];
}

const cellsOf = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());

function mdRender(src) {
  const lines = src.split('\n'), out = [];
  let i = 0;
  const starter = l => /^(#{1,6}\s|>|```|~~~|\$\$|\s*([-*+]|\d+[.)])\s)/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    let m;
    if (!l.trim()) { i++; continue; }
    if ((m = /^(`{3,}|~{3,})/.exec(l))) {
      const code = []; i++;
      while (i < lines.length && !lines[i].startsWith(m[1])) code.push(lines[i++]);
      i++;
      out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>');
    } else if (/^\s*\$\$/.test(l)) {
      let text = l.trim();
      i++;
      if (!/^\$\$[\s\S]+\$\$$/.test(text)) {
        while (i < lines.length) { text += '\n' + lines[i]; if (/\$\$\s*$/.test(lines[i++])) break; }
      }
      out.push('<div>' + esc(text) + '</div>');
    } else if ((m = /^(#{1,6})\s+(.*?)\s*#*$/.exec(l))) {
      out.push(`<h${m[1].length}>${mdInline(m[2])}</h${m[1].length}>`); i++;
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push('<hr>'); i++; }
    else if (/^>/.test(l)) {
      const q = [];
      while (i < lines.length && /^>/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ''));
      out.push('<blockquote>' + mdRender(q.join('\n')) + '</blockquote>');
    } else if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const [h, next] = mdList(lines, i);
      out.push(h); i = next;
    } else if (l.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      const head = cellsOf(l);
      i += 2;
      let h = '<table><thead><tr>' + head.map(c => `<th>${mdInline(c)}</th>`).join('') + '</tr></thead><tbody>';
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        h += '<tr>' + cellsOf(lines[i++]).map(c => `<td>${mdInline(c)}</td>`).join('') + '</tr>';
      }
      out.push(h + '</tbody></table>');
    } else {
      const para = [l]; i++;
      while (i < lines.length && lines[i].trim() && !starter(lines[i])) para.push(lines[i++]);
      out.push('<p>' + mdInline(para.join('\n')) + '</p>');
    }
  }
  return out.join('\n');
}

let mathQueue = Promise.resolve();
function typesetMath(roots) {
  roots = roots.filter(r => /\$|\\\(|\\\[/.test(r.textContent));
  if (!roots.length) return;
  if (window.MathJax && MathJax.typesetPromise) { mathQueue = mathQueue.then(() => { kit.mathPrune(); return MathJax.typesetPromise(roots); }).catch(() => {}); return; }  // mathPrune: no old maths kept in memory
  if (window.MathJax) return;  // still loading; its startup typesets the page
  window.MathJax = {tex: {inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\[', '\\]']], processEscapes: true},
    options: {enableMenu: false, skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']}};
  const s = document.createElement('script');
  s.src = '/vendor/mathjax/tex-chtml.js';
  s.async = true;
  document.head.appendChild(s);
}

// ------------------------------------------------------------ outputs
const FRAME_HEAD = '<!doctype html><meta charset=utf-8><meta name="color-scheme" content="dark light"><style>body{margin:0;font:13px system-ui,sans-serif;color:#222;background:transparent}' +
  '@media (prefers-color-scheme:dark){body{color:#e8e8e8}}table{border-collapse:collapse;border:0}th,td{padding:3px 10px;text-align:right;border:0;' +
  'border-bottom:1px solid #8884}thead th{border-bottom:1px solid #8888}tr:hover{background:#8881}</style>';

const plain = s => String(s).replace(/\x1b\[[0-9;]*[A-Za-z]/g, '').split('\n').map(l => l.slice(l.lastIndexOf('\r') + 1)).join('\n');

const outKey = o => o.type + ':' + (o.file || ((o.html || '').length + ':' + (o.text || '').length));

function itemEl(o) {
  let e;
  if (o.type === 'stream') { e = el('pre', o.name); e.textContent = plain(o.text); }
  else if (o.type === 'result' && o.html) {
    e = el('iframe');
    e.setAttribute('sandbox', 'allow-same-origin');  // no scripts; same origin only so the frame can be sized
    e.srcdoc = FRAME_HEAD + o.html;
    e.addEventListener('load', () => { try { e.style.height = (e.contentDocument.documentElement.scrollHeight + 2) + 'px'; } catch (err) { /* keep default */ } });
  } else if (o.type === 'result') { e = el('pre', 'res'); e.textContent = plain(o.text); }
  else if (o.type === 'image') {
    e = el('img');
    e.src = `/api/img?f=${enc(FILE)}&n=${enc(o.file)}`;
    e.width = o.w; e.height = o.h; e.alt = o.text || 'figure';  // the size is known, so nothing moves while it loads
  } else if (o.type === 'error') { e = el('pre', 'err'); e.textContent = plain(o.text); }
  else e = el('div', 'note', esc(o.text || ''));
  e.dataset.k = outKey(o);
  return e;
}

// Bring a cell's output box up to date by changing only what differs. An image or a table that is
// already there stays as it is, so nothing reloads or flashes.
function syncOuts(box, outputs) {
  const kids = box.children;
  for (let i = 0; i < outputs.length; i++) {
    const o = outputs[i];
    if (i < kids.length) {
      const k = kids[i];
      if (o.type === 'stream' && k.tagName === 'PRE' && k.className === o.name) {
        const t = plain(o.text);
        if (k.textContent !== t) k.textContent = t;
        k.dataset.k = outKey(o);
        continue;
      }
      if (o.type !== 'stream' && k.dataset.k === outKey(o)) continue;
      while (kids.length > i) kids[i].remove();  // it differs from here on
    }
    box.appendChild(itemEl(o));
  }
  while (kids.length > outputs.length) kids[outputs.length].remove();
}

function outsEl(c) {
  const box = el('div', 'outs');
  syncOuts(box, c.outputs || []);
  return box;
}

function refreshOuts(idx) {  // streaming: update the output of one cell, at most once per frame
  if (S.pend[idx]) return;
  S.pend[idx] = requestAnimationFrame(() => {
    delete S.pend[idx];
    const c = S.doc && S.doc.cells.find(x => x.idx === idx), host = document.querySelector(`.cell[data-idx="${idx}"]`);
    if (!c || !host) return;
    if (!host._p || !host._p.outs) { fillCell(host, c); return; }
    host._p.outs.classList.toggle('stale', !!c.staleOuts);
    syncOuts(host._p.outs, c.outputs || []);
    host._sig.outs = sig(c.outputs || []);
  });
}

// ------------------------------------------------------------ the editor
// CodeMirror (vendor/codemirror.js) when it loaded, else a plain text box. Both give the same few methods.
function indentOf(line) { return /^\s*/.exec(line)[0]; }

function textBox(wrap, c, text, cancel, run, split) {
  const ta = el('textarea');
  ta.value = text; ta.spellcheck = false; ta.rows = 1;
  ta.setAttribute('autocapitalize', 'off'); ta.setAttribute('autocomplete', 'off');
  wrap.appendChild(ta);
  const fit = () => { ta.style.height = 'auto'; ta.style.height = (ta.scrollHeight + 2) + 'px'; };
  ta.addEventListener('input', fit);
  ta.addEventListener('keydown', ev => {
    const mod = ev.metaKey || ev.ctrlKey;
    if (ev.key === 'Enter' && (ev.shiftKey || mod)) { ev.preventDefault(); run(!ev.shiftKey); return; }
    if (ev.key === 'Escape') { ev.preventDefault(); cancel(); return; }
    if (ev.ctrlKey && (ev.key === '_' || ev.key === '-')) { ev.preventDefault(); split(); return; }
    if (ev.key === 'Tab') {
      ev.preventDefault();
      const v = ta.value, a = ta.selectionStart, b = ta.selectionEnd;
      if (a === b && !ev.shiftKey) ta.setRangeText('    ', a, b, 'end');
      else {
        const s0 = v.lastIndexOf('\n', a - 1) + 1;
        let e0 = v.indexOf('\n', b); if (e0 < 0) e0 = v.length;
        ta.setRangeText(v.slice(s0, e0).split('\n').map(l => ev.shiftKey ? l.replace(/^( {1,4}|\t)/, '') : '    ' + l).join('\n'), s0, e0, 'select');
      }
      fit(); return;
    }
    if (ev.key === 'Enter' && c.kind === 'code') {
      const v = ta.value, a = ta.selectionStart, line = v.slice(v.lastIndexOf('\n', a - 1) + 1, a);
      ev.preventDefault();
      ta.setRangeText('\n' + indentOf(line) + (/:\s*$/.test(line) ? '    ' : ''), a, ta.selectionEnd, 'end'); fit();
    }
  });
  wrap.getValue = () => ta.value;
  wrap.cursorLine = () => ta.value.slice(0, ta.selectionStart).split('\n').length - 1;
  wrap.focusEnd = () => { fit(); ta.focus(); ta.selectionStart = ta.selectionEnd = ta.value.length; };
  wrap.ensureFocus = () => { fit(); if (document.activeElement !== ta) ta.focus(); };
}

function makeEditor(c, text) {
  const wrap = el('div', 'editor');
  const run = stay => finishEdit(c.idx, stay ? 'stay' : 'advance');
  const save = () => saveEdit(c.idx);  // Esc: keep the text and leave, without running
  const split = () => splitAtCursor(c.idx);
  if (window.PyviewCM) {
    const ed = PyviewCM.create(wrap, {doc: text, language: c.kind === 'markdown' ? 'markdown' : 'python', run, cancel: save, split});
    wrap.getValue = ed.getValue; wrap.cursorLine = ed.cursorLine; wrap.focusEnd = ed.focusEnd;
    wrap.ensureFocus = () => { if (!ed.hasFocus()) ed.focus(); };
  } else textBox(wrap, c, text, save, run, split);
  const bar = el('div', 'bar');
  bar.appendChild(btn('Save', '', 'Keep the changes and leave the editor, without running (Esc)', save));
  if (c.kind === 'code') bar.appendChild(btn('Save and run', '', 'Shift+Enter', () => run(false)));
  bar.appendChild(btn('Discard', '', 'Throw the changes away', cancelEdit));
  bar.appendChild(el('span', 'hint', 'Esc save · Shift+Enter run and go on · ⌘/Ctrl+Enter run and stay · Ctrl+Shift+- split at cursor'));
  wrap.appendChild(bar);
  return wrap;
}

// ------------------------------------------------------------ editing actions
// A document holds the events up to d.seq. One older than the events the page applied would undo them: fetch again.
function adopt(d) {
  if (d.seq < S.seq) { setTimeout(load, 60); return; }
  S.doc = d;
  S.seq = d.seq;
  afterLoad();
}

async function op(body, undoLabel) {
  try {
    adopt(await api('POST', '/api/op', {version: S.doc.version, ...body}));
    if (undoLabel) toast(undoLabel, 'Undo', undo);
    return true;
  } catch (e) {
    toast(e.status === 409 ? 'The file changed on disk. Reloaded; try again.' : e.message);
    if (e.status === 409) await load();
    return false;
  }
}

async function undo() {
  try { adopt(await api('POST', '/api/op', {type: 'undo'})); }
  catch (e) { toast(e.message); }
}

async function startEdit(idx) {
  if (S.editing != null && S.editing !== idx) await saveEdit(S.editing);  // moving to another cell keeps the first edit
  const c = S.doc.cells.find(x => x.idx === idx);
  if (!c) return;
  S.sel = idx; S.editing = idx; S.editBase = c.raw;
  S.editor = makeEditor(c, c.raw);
  render();
  S.editor.focusEnd();
}

// Keep the text and leave the editor. Nothing runs, so the cell shows "edited since run" if it had run before.
async function saveEdit(idx) {
  const text = S.editor.getValue(), changed = text !== S.editBase;
  S.editing = null; S.editor = null; S.editBase = null;
  if (changed) await op({type: 'edit-cell', cell: idx, text}); else render();
  select(idx);
}

function cancelEdit() {
  S.editing = null; S.editor = null; S.editBase = null;
  render();
}

async function finishEdit(idx, how) {
  const c = S.doc.cells.find(x => x.idx === idx), text = S.editor.getValue(), changed = text !== S.editBase;
  S.editing = null; S.editor = null; S.editBase = null;
  if (changed && !(await op({type: 'edit-cell', cell: idx, text}))) { render(); return; }
  if (!changed) render();
  if (c.kind === 'code' && S.doc.cells.find(x => x.idx === idx).runnable) runCells('cell', idx);
  if (how === 'advance') {
    const cells = S.doc.cells, k = cells.findIndex(x => x.idx === idx);
    if (k + 1 < cells.length) select(cells[k + 1].idx); else addCell(idx, 'code');
  }
}

async function splitAtCursor(idx) {
  const k = S.editor.cursorLine(), text = S.editor.getValue(), changed = text !== S.editBase;
  S.editing = null; S.editor = null; S.editBase = null;
  if (changed && !(await op({type: 'edit-cell', cell: idx, text}))) return;
  if (!(await op({type: 'split-cell', cell: idx, line: k}))) return;
  select(idx + 1);
}

async function addCell(after, kind) {
  if (await op({type: 'add-cell', after, kind})) startEdit(after + 1);
}

const moveCell = (idx, dir) => op({type: 'move-cell', cell: idx, dir}).then(ok => ok && select(idx + dir));
const deleteCell = idx => op({type: 'delete-cell', cell: idx}, 'Cell deleted.');
const setType = (idx, kind) => op({type: 'set-type', cell: idx, kind});
const setTitle = (idx, title) => op({type: 'set-title', cell: idx, title});

function renameField(c) {
  const inp = el('input', 'rename');
  inp.value = S.rename.text; inp.placeholder = 'Cell title'; inp.spellcheck = false;
  inp.addEventListener('input', () => { S.rename.text = inp.value; });
  inp.addEventListener('keydown', ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') { ev.preventDefault(); const t = inp.value; S.rename = null; setTitle(c.idx, t).then(ok => ok || render()); }
    if (ev.key === 'Escape') { ev.preventDefault(); S.rename = null; render(); }
  });
  inp.addEventListener('blur', () => { if (S.rename && S.rename.cell === c.idx) { S.rename = null; render(); } });
  setTimeout(() => { inp.focus(); inp.select(); }, 0);
  return inp;
}

function select(idx) {
  S.sel = idx;
  renderChatCtx();
  document.querySelectorAll('.cell.sel').forEach(e => e.classList.remove('sel'));
  const e = document.querySelector(`.cell[data-idx="${idx}"]`);
  if (e) { e.classList.add('sel'); e.scrollIntoView({block: 'nearest'}); }
}

// ------------------------------------------------------------ running
async function runCells(mode, cell) {
  try { await api('POST', '/api/run', {mode, cell}); }
  catch (e) { toast(e.message, null, null, 9000); }
}

// ------------------------------------------------------------ rendering
function threadsOf(c) {
  const t = S.doc.threads;
  return {cell: t.filter(x => x.scope === 'cell' && x.cell === c.idx), line: t.filter(x => x.scope === 'line' && x.cell === c.idx)};
}

function cellHead(c, th) {
  const h = el('div', 'chead');
  const running = c.state === 'running';
  h.appendChild(el('span', 'count', c.kind === 'markdown' ? 'md' : running ? '[*]' : c.count ? `[${c.count}]` : '[ ]'));
  if (S.rename && S.rename.cell === c.idx) h.appendChild(renameField(c));
  else if (c.title) h.appendChild(el('span', 'title', esc(c.title)));
  if (running) h.appendChild(el('span', 'badge running', 'running'));
  else if (c.state === 'queued') h.appendChild(el('span', 'badge running', 'queued'));
  else if (c.edited) h.appendChild(el('span', 'badge edited', 'edited since run'));
  else if (c.previous && c.outputs && c.outputs.length) h.appendChild(el('span', 'badge prev', 'previous session'));
  if (c.ok === false && !running && c.outputs && c.outputs.length) h.appendChild(el('span', 'badge err', 'error'));
  if (c.ms != null && c.count && !running) h.appendChild(el('span', '', c.ms < 1000 ? c.ms + ' ms' : (c.ms / 1000).toFixed(1) + ' s'));
  h.appendChild(el('span', 'sp'));
  const can = S.doc.trusted && S.doc.python;
  if (c.runnable) h.appendChild(btn('▶', '', can ? 'Run (Shift+Enter)' : 'Trust the file first', () => runCells('cell', c.idx)));
  const n = th.cell.length;
  h.appendChild(btn(n ? `💬 ${n}` : '💬', '', 'Comment on this cell', () => { S.comp = {type: 'cell', cell: c.idx, text: ''}; render(); }));
  h.appendChild(btn('🗑', '', 'Delete this cell (Undo brings it back)', () => deleteCell(c.idx)));
  h.appendChild(btn('⋯', '', 'More', ev => openMenu(ev.currentTarget, [
    {label: c.kind === 'code' ? 'Edit' : 'Edit markdown', fn: () => startEdit(c.idx)},
    {label: 'Add code cell below', fn: () => addCell(c.idx, 'code')},
    {label: 'Add markdown cell below', fn: () => addCell(c.idx, 'markdown')},
    ...(c.idx && c.kind === 'code' ? [{label: 'Rename', fn: () => { S.rename = {cell: c.idx, text: c.title || ''}; render(); }}] : []),
    ...(c.idx ? [{label: 'Move up', fn: () => moveCell(c.idx, -1)}, {label: 'Move down', fn: () => moveCell(c.idx, 1)},
      {label: c.kind === 'code' ? 'Change to markdown' : 'Change to code', fn: () => setType(c.idx, c.kind === 'code' ? 'markdown' : 'code')}] : []),
    ...(c.runnable ? [{label: 'Run this cell and all below', fn: () => runCells('below', c.idx)},
      {label: 'Run all cells above', fn: () => runCells('above', c.idx)}] : []),
    {label: 'Ask Claude about this cell', fn: () => askAbout(c.idx)},
    {label: 'Delete cell', fn: () => deleteCell(c.idx)},
  ])));
  return h;
}

function composerEl(comp, onSend, label) {
  const box = el('div', 'composer'), ta = el('textarea');
  ta.value = comp.text || ''; ta.placeholder = 'Comment…'; ta.spellcheck = false;
  ta.addEventListener('input', () => { comp.text = ta.value; });
  const send = () => { if (ta.value.trim()) onSend(ta.value.trim()); };
  const cancel = () => { S.comp = null; render(); };
  ta.addEventListener('keydown', ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); send(); }
    if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
  });
  const row = el('div', 'row');
  row.appendChild(btn('Cancel', '', '', cancel));
  row.appendChild(btn(label || 'Comment', 'go', '⌘/Ctrl+Enter', send));
  box.append(ta, row);
  if (!comp.shown) { comp.shown = true; setTimeout(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }, 0); }
  return box;
}

async function sendComment(body) {
  const ok = await op(body);
  if (ok) { S.comp = null; render(); }
}

const sig = x => JSON.stringify(x);

function cellSigs(c, th) {
  const lc = S.comp && S.comp.type === 'line' && S.comp.cell === c.idx ? S.comp.line : null;
  return {
    head: sig([c.kind, c.title, c.state, c.count, c.ms, c.ok, c.edited, c.previous, c.runnable,
               !!(c.outputs && c.outputs.length), th.cell.length, !!(S.rename && S.rename.cell === c.idx), !!(S.doc.trusted && S.doc.python)]),
    comp: S.comp && S.comp.type === 'cell' && S.comp.cell === c.idx ? 'c' : '',
    body: S.editing === c.idx ? 'editing'
      : sig([c.kind, c.kind === 'markdown' ? c.md : [c.code, c.nums], th.line.map(t => [t.id, t.line, t.status]), lc]),
    outs: c.kind === 'code' ? sig(c.outputs || []) : '',
  };
}

function cellComposer(c) {
  const box = composerEl(S.comp, text => sendComment({type: 'comment-add', scope: 'cell', cell: c.idx, line: 0, text}));
  box.classList.add('lcomp');
  return box;
}

function bodyEl(c, th) {
  if (c.kind === 'markdown') {
    const m = el('div', 'md' + (c.md.trim() ? '' : ' empty'), c.md.trim() ? mdRender(c.md) : 'Empty markdown cell. Double-click to write.');
    m.addEventListener('dblclick', () => startEdit(c.idx));
    return m;
  }
  const src = el('div', 'src'), html = hlLines(c.code);
  src.addEventListener('dblclick', () => startEdit(c.idx));
  if (!c.code.length || (c.code.length === 1 && !c.code[0].trim())) {
    src.appendChild(el('div', 'ln', '<span class="g"></span><code style="color:var(--muted)">(empty: double-click to write)</code>'));
  }
  c.code.forEach((line, k) => {
    const abs = c.nums[k], here = th.line.filter(t => t.line === abs);
    const row = el('div', 'ln' + (here.length ? ' thr' + (here.every(t => t.status === 'answered') ? ' answered' : '') : ''));
    row.dataset.abs = abs;
    const g = el('span', 'g');
    if (here.length) g.appendChild(btn('💬', 'bub', 'Show the comments', () => focusThread(here[0].id)));
    else g.appendChild(btn('+', 'add', 'Comment on this line', () => { S.comp = {type: 'line', cell: c.idx, line: abs, text: ''}; render(); }));
    row.appendChild(g);
    row.appendChild(el('code', '', html[k] || ' '));
    src.appendChild(row);
    if (S.comp && S.comp.type === 'line' && S.comp.line === abs && S.comp.cell === c.idx) {
      const box = composerEl(S.comp, text => sendComment({type: 'comment-add', scope: 'line', cell: c.idx, line: abs, text}));
      box.classList.add('lcomp'); src.appendChild(box);
    }
  });
  return src;
}

// A cell is four parts in order: head, comment box, code (or markdown), output. Each one is replaced only
// when its signature changed, so a cell whose output just finished does not redraw its code, and the
// other cells are not touched at all.
function fillCell(e, c) {
  const th = threadsOf(c), sg = cellSigs(c, th), old = e._sig || {}, p = e._p;
  const cls = ['cell', c.kind === 'markdown' ? 'mdc' : 'codec'];
  if (S.sel === c.idx) cls.push('sel');
  if (c.state === 'running') cls.push('running');
  if (c.edited) cls.push('edited');
  if (c.previous) cls.push('previous');
  if (th.cell.length) cls.push('thr');
  e.className = cls.join(' ');
  let prev = null;
  for (const k of ['head', 'comp', 'body', 'outs']) {
    if (old[k] !== sg[k]) {
      let n = null;
      if (k === 'head') n = cellHead(c, th);
      else if (k === 'comp') n = sg.comp ? cellComposer(c) : null;
      else if (k === 'body') { n = S.editing === c.idx ? S.editor : bodyEl(c, th); if (n.classList.contains('md')) S.math.push(n); }
      else if (c.kind === 'code') { if (p.outs) { syncOuts(p.outs, c.outputs || []); n = p.outs; } else n = outsEl(c); }
      if (p[k] !== n) {  // n is null for a part this cell does not have: add nothing (after(null) writes "null")
        if (p[k] && n) p[k].replaceWith(n);
        else if (p[k]) p[k].remove();
        else if (n) { if (prev) prev.after(n); else e.prepend(n); }
      }
      p[k] = n;
    }
    if (p[k]) prev = p[k];
  }
  if (p.outs) p.outs.classList.toggle('stale', !!c.staleOuts);
  e._sig = sg;
}

function buildCell(c) {
  const e = el('section');
  e.dataset.idx = c.idx;
  e._p = {};
  e.addEventListener('click', ev => { if (S.sel !== c.idx && !ev.target.closest('textarea')) select(c.idx); });
  fillCell(e, c);
  return e;
}

function gapEl(after, end) {
  const g = el('div', 'gap' + (end ? ' end' : ''));
  g.appendChild(btn('+ Code', '', 'Add a code cell here', () => addCell(after, 'code')));
  g.appendChild(btn('+ Markdown', '', 'Add a markdown cell here', () => addCell(after, 'markdown')));
  return g;
}

// Keep the elements that are already there (same cell number), update the ones that changed, and move
// only what is out of place. Moving an element that holds an iframe would reload it.
function renderCells() {
  const host = $('cells'), cells = S.doc.cells;
  S.math = [];
  const have = new Map();
  for (const n of [...host.children]) { if (n.dataset.key) have.set(n.dataset.key, n); else n.remove(); }
  const want = [];
  if (!cells.length) want.push(el('div', 'empty-doc', 'No cells yet.'));
  const gap = (after, end) => {
    const key = 'g' + after + (end ? 'e' : '');
    const g = have.get(key) || gapEl(after, end);
    g.dataset.key = key;
    return g;
  };
  let prev = 0;
  for (const c of cells) {
    if (c.idx !== 0) want.push(gap(prev, false));
    let e = have.get('c' + c.idx);
    if (e) fillCell(e, c); else { e = buildCell(c); e.dataset.key = 'c' + c.idx; }
    want.push(e);
    prev = c.idx;
  }
  want.push(gap(prev, true));
  const keep = new Set(want);
  for (const n of [...host.children]) if (!keep.has(n)) n.remove();
  let cur = host.firstChild;
  for (const n of want) { if (n === cur) cur = cur.nextSibling; else host.insertBefore(n, cur); }
  if (S.editor) S.editor.ensureFocus();
  typesetMath(S.math);
}

const shortPath = p => p ? p.split('/').slice(-4).join('/') : '';

function renderHeader() {
  const d = S.doc, k = S.kernel || d.kernel;
  kit.crumbs($('fname'), d.label, msg => toast(msg));
  $('hostb').textContent = 'on ' + d.host;
  $('chatHost').textContent = 'on ' + d.host;
  kit.docTitle('py', d.label);
  const ks = $('kstate');
  ks.className = k.state;
  $('ktext').textContent = k.state + (k.mem ? ` · ${k.mem} MB` : '') + (k.queue ? ` · ${k.queue} queued` : '');
  ks.title = k.python || d.python || '';
  const ok = d.trusted && d.python;
  $('runall').disabled = !ok;
  $('interrupt').disabled = !ok || k.state !== 'running';
  $('restart').disabled = !ok || k.state === 'stopped';
  $('undo').disabled = !d.canUndo;
  $('sideBtn').textContent = d.threads.length ? `Comments (${d.threads.length})` : 'Comments';
  const claude = $('claude');
  claude.textContent = S.claude ? 'working…' : 'Oracle';
  kit.tab({app: 'py', oracle: !!S.claude});
  claude.title = S.claude ? 'Oracle is working on the comments. Click to stop it.' : 'Oracle: Claude answers the comments and changes the code (/answer-cells)';
  const b = $('banner');
  if (!d.python) { b.classList.add('on'); $('bantext').textContent = d.pythonNote; $('trust').hidden = true; }
  else if (!d.trusted) {
    b.classList.add('on');
    $('bantext').textContent = 'Running a cell runs code on this computer. This file is read-only until you trust it.';
    $('trust').hidden = false;
  } else b.classList.remove('on');
  $('home').href = '/?d=' + enc(d.file.split('/').slice(0, -1).join('/'));
}

function threadCard(t) {
  const card = el('div', 'thread ' + t.status);
  card.dataset.id = t.id;
  const loc = t.scope === 'general' ? 'General' : t.scope === 'cell' ? `Cell ${t.cell}` : `Cell ${t.cell} · line ${t.line + 1}`;
  const l = el('div', 'tloc', esc(loc) + (t.scope === 'line' && t.target ? ` · <span class="tt">${esc(t.target)}</span>` : ''));
  l.addEventListener('click', () => scrollToThread(t));
  card.appendChild(l);
  for (const m of t.msgs) {
    const row = el('div', 'msg ' + m.author);
    row.innerHTML = `<b>${esc(m.author)}</b>`;
    row.appendChild(document.createTextNode(m.text));
    const acts = el('span', 'macts');
    acts.appendChild(btn('Edit', '', '', () => { S.comp = {type: 'edit', line: m.line, text: m.text}; render(); }));
    acts.appendChild(btn('Delete', '', '', () => op({type: 'comment-delete', line: m.line}, 'Comment deleted.')));
    row.appendChild(acts);
    if (S.comp && S.comp.type === 'edit' && S.comp.line === m.line) {
      row.replaceChildren(composerEl(S.comp, text => sendComment({type: 'comment-edit', line: m.line, text}), 'Save'));
    }
    card.appendChild(row);
  }
  if (S.comp && S.comp.type === 'reply' && S.comp.thread === t.id) {
    card.appendChild(composerEl(S.comp, text => sendComment({type: 'comment-reply', thread: t.id, text}), 'Reply'));
  } else {
    const acts = el('div', 'tacts');
    acts.appendChild(btn('Reply', '', '', () => { S.comp = {type: 'reply', thread: t.id, text: ''}; render(); }));
    acts.appendChild(btn('Resolve', '', 'Remove the comment and keep the code', () => op({type: 'comment-resolve', thread: t.id}, 'Comment resolved.')));
    card.appendChild(acts);
  }
  return card;
}

function renderThreads() {
  const side = $('threads'), ts = S.doc.threads;
  kit.tab({app: 'py', open: ts.filter(t => t.status === 'open').length});
  if (S.comp && ['general', 'reply', 'edit'].includes(S.comp.type) && !sideOn) setSide(true);  // the box lives in the panel
  const sg = sig([ts, S.comp && [S.comp.type, S.comp.line, S.comp.thread]]);
  if (sg === S.thrSig) return;  // the same threads and the same open comment box: leave it, so typing is not lost
  S.thrSig = sg;
  const frag = document.createDocumentFragment();
  const order = [...ts].sort((a, b) => (a.scope === 'general' ? -1 : 0) - (b.scope === 'general' ? -1 : 0) || a.id - b.id);
  if (S.comp && S.comp.type === 'general') {
    frag.appendChild(el('h4', '', 'New comment on the file'));
    frag.appendChild(composerEl(S.comp, text => sendComment({type: 'comment-add', scope: 'general', line: 0, text})));
  }
  if (ts.length) {
    const hd = el('h4', '', `<span>Comments (${ts.length})</span>`);
    hd.appendChild(btn(`Resolve all (${ts.length})`, '', 'Remove every comment and keep the code', () => {
      if (confirm(`Resolve all ${ts.length} comment${ts.length > 1 ? 's' : ''}? They are removed from the file (Undo brings them back).`))
        op({type: 'comment-resolve-all'}, 'All comments resolved.');
    }));
    frag.appendChild(hd);
  }
  else if (!S.comp) frag.appendChild(el('h4', '', 'No comments'));
  order.forEach(t => frag.appendChild(threadCard(t)));
  side.replaceChildren(frag);
}

// ------------------------------------------------------------ contents
// The markdown headings and the titled code cells (# %% Title), as on the left of mdview. A click scrolls to
// the place, the part being read is highlighted, and each entry shows the open comments of its part.
const tocLinks = [];

function tocEntries() {
  const out = [], open = {};
  S.doc.threads.forEach(t => { if (t.status === 'open' && t.scope !== 'general') open[t.cell] = (open[t.cell] || 0) + 1; });
  let lvl = 0, k = -1;
  const own = {};  // the entry that each cell's comments are counted under
  for (const c of S.doc.cells) {
    if (c.kind === 'markdown') {
      let fence = false, ord = 0;
      for (const line of c.md.split('\n')) {
        if (/^(`{3,}|~{3,})/.test(line)) { fence = !fence; continue; }
        const m = !fence && /^(#{1,6})\s+(.*?)\s*#*$/.exec(line);
        if (m) { out.push({level: m[1].length, text: m[2].replace(/`|\*\*?/g, ''), cell: c.idx, ord: ord++, code: false, open: 0}); lvl = m[1].length; k = out.length - 1; }
      }
    } else if (c.title) { out.push({level: lvl + 1, text: c.title, cell: c.idx, ord: -1, code: true, open: 0}); k = out.length - 1; }
    own[c.idx] = Math.max(k, 0);
  }
  if (out.length) for (const [idx, n] of Object.entries(open)) out[own[idx] != null ? own[idx] : 0].open += n;
  return out;
}

function tocTarget(e) {
  const cell = document.querySelector(`.cell[data-idx="${e.cell}"]`);
  if (cell && e.ord >= 0) {
    const hs = cell.querySelectorAll('.md > h1, .md > h2, .md > h3, .md > h4, .md > h5, .md > h6');
    if (hs[e.ord]) return hs[e.ord];
  }
  return cell;
}

function buildToc() {
  const es = tocEntries(), show = es.length >= 2, list = $('tocList');
  $('toc').hidden = $('tocBtn').hidden = !show;
  document.body.classList.toggle('hastoc', show);
  const sg = sig(es);
  if (sg === S.tocSig) { tocState(); return; }  // the same entries: leave the panel alone
  S.tocSig = sg;
  tocLinks.length = 0;
  list.replaceChildren();
  if (!show) return;
  const top = Math.min(...es.map(e => e.level));
  for (const e of es) {
    const a = el('a', (e.level === top ? 'top ' : '') + (e.code ? 'code' : ''));
    a.href = '#';
    a.style.paddingLeft = 6 + (e.level - top) * 12 + 'px';
    a.append(el('span', 't', esc(e.text)));
    if (e.open) { const n = el('span', 'n', String(e.open)); n.title = `${e.open} open comment${e.open > 1 ? 's' : ''}`; a.append(n); }
    a.onclick = ev => { ev.preventDefault(); const t = tocTarget(e); if (t) t.scrollIntoView({block: 'start'}); };
    tocLinks.push({a, e});
    list.append(a);
  }
  tocState();
}

function tocState() {
  if (!tocLinks.length) return;
  let cur = null;
  for (const l of tocLinks) { const t = tocTarget(l.e); if (t && t.getBoundingClientRect().top <= 90) cur = l; }
  tocLinks.forEach(l => l.a.classList.toggle('active', l === cur));
  const nav = $('toc');
  if (cur && (cur.a.offsetTop < nav.scrollTop || cur.a.offsetTop + cur.a.offsetHeight > nav.scrollTop + nav.clientHeight))
    nav.scrollTop = cur.a.offsetTop - nav.clientHeight / 3;
}
let spyFrame;
addEventListener('scroll', () => { cancelAnimationFrame(spyFrame); spyFrame = requestAnimationFrame(tocState); }, {passive: true});

let tocOn = true;
function setToc(on) {
  tocOn = on;
  document.body.classList.toggle('notoc', !on);
  $('tocBtn').setAttribute('aria-pressed', String(on));
  const t = (on ? 'Hide' : 'Show') + ' the contents (⌘/Ctrl + Shift + \\)';
  $('tocBtn').title = t; $('tocHide').title = t; $('tocShow').title = t;
  try { localStorage.setItem('pyview.toc', on ? 'on' : 'off'); } catch (e) { /* not saved */ }
}
$('tocBtn').onclick = () => setToc(!tocOn);
$('tocHide').onclick = () => setToc(false);
$('tocShow').onclick = () => setToc(true);
addEventListener('keydown', ev => { if ((ev.metaKey || ev.ctrlKey) && ev.shiftKey && ev.code === 'Backslash') { ev.preventDefault(); setToc(!tocOn); } });
setToc((() => { try { return localStorage.getItem('pyview.toc'); } catch (e) { return null; } })() !== 'off');

let sideOn = true;
function setSide(on) {
  sideOn = on;
  document.body.classList.toggle('noside', !on);
  $('sideBtn').setAttribute('aria-pressed', String(on));
  $('sideBtn').title = (on ? 'Hide' : 'Show') + ' the comments (⌘/Ctrl + \\)';
  try { localStorage.setItem('pyview.side', on ? 'on' : 'off'); } catch (e) { /* not saved */ }
}
$('sideBtn').onclick = () => setSide(!sideOn);
addEventListener('keydown', ev => { if ((ev.metaKey || ev.ctrlKey) && !ev.shiftKey && ev.code === 'Backslash') { ev.preventDefault(); setSide(!sideOn); } });
setSide((() => { try { return localStorage.getItem('pyview.side'); } catch (e) { return null; } })() !== 'off');

function render() {
  if (!S.doc) return;
  renderHeader();
  renderCells();
  renderThreads();
  buildToc();
  renderChatCtx();
}

function afterLoad() {
  S.gen++;
  // The cell being edited may have moved if cells were added or removed on disk.
  if (S.editing != null && S.doc) {
    const same = S.doc.cells.filter(c => c.raw === S.editBase);
    if (same.length === 1) S.editing = same[0].idx;
    else if (!S.doc.cells.some(c => c.idx === S.editing)) { S.editing = null; S.editor = null; }
  }
  S.claude = !!(S.doc.claude && S.doc.claude.running);
  if (S.kernel && S.doc.kernel) S.kernel = S.doc.kernel;
  render();
}

function focusThread(id) {
  if (!sideOn) setSide(true);
  const card = document.querySelector(`.thread[data-id="${id}"]`);
  if (!card) return;
  card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  card.classList.add('flash');
  setTimeout(() => card.classList.remove('flash'), 1200);
}

function scrollToThread(t) {
  const target = t.scope === 'line' ? document.querySelector(`.ln[data-abs="${t.line}"]`)
    : t.scope === 'cell' ? document.querySelector(`.cell[data-idx="${t.cell}"]`) : null;
  if (!target) return;
  target.scrollIntoView({block: 'center', behavior: 'smooth'});
  target.classList.add('flash');
  setTimeout(() => target.classList.remove('flash'), 1200);
}

// ------------------------------------------------------------ live updates
async function load() {
  try { adopt(await api('GET', `/api/doc?f=${enc(FILE)}`)); }
  catch (e) { $('cells').innerHTML = `<div class="err-page">${esc(e.message)}</div>`; }
}

function cellFor(ev) { return S.doc && S.doc.cells.find(c => c.idx === ev.cell && c.hash === ev.hash) || (S.doc && S.doc.cells.find(c => c.idx === ev.cell)); }

function onEvent(ev) {
  if (ev.t === 'chat') { chat.event(ev); return; }  // chat events have their own numbers
  if (!S.doc || ev.seq <= S.seq) return;  // not loaded yet, or the document already holds this event
  S.seq = ev.seq;
  if (ev.t === 'state') {
    S.kernel = ev;
    renderHeader();
    document.querySelectorAll('.cell').forEach(e => {
      const c = S.doc.cells.find(x => x.idx === +e.dataset.idx);
      if (!c || !c.runnable) return;
      const st = ev.running === c.idx ? 'running' : ev.queued.includes(c.idx) ? 'queued' : '';
      if (st !== (c.state || '')) { c.state = st; fillCell(e, c); }
    });
  } else if (ev.t === 'start') {
    const c = cellFor(ev);
    if (!c) return;
    // The old output stays on screen, dimmed, until the first new output arrives: nothing jumps.
    Object.assign(c, {state: 'running', staleOuts: true, count: ev.n, edited: false, previous: false, ok: true});
    const e = document.querySelector(`.cell[data-idx="${c.idx}"]`);
    if (e) fillCell(e, c);
  } else if (ev.t === 'stream' || ev.t === 'output') {
    const c = cellFor(ev);
    if (!c) return;
    if (c.staleOuts) { c.outputs = []; c.staleOuts = false; }
    c.outputs = c.outputs || [];
    if (ev.t === 'stream') {
      const last = c.outputs[c.outputs.length - 1];
      if (last && last.type === 'stream' && last.name === ev.name) last.text += ev.text;
      else c.outputs.push({type: 'stream', name: ev.name, text: ev.text});
    } else c.outputs.push(ev.item);
    refreshOuts(c.idx);
  } else if (ev.t === 'done') {
    load();
  }
}

function startEvents() {
  const es = new EventSource(`/api/events?f=${enc(FILE)}`);
  es.onopen = () => { S.live = true; S.seq = 0; $('live').classList.remove('off'); load(); chat.load(); };  // a new server counts from 0
  es.onerror = () => { S.live = false; $('live').classList.add('off'); };
  es.onmessage = e => { try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } };
}

async function poll() {
  try {
    const gen = S.gen;
    const v = await api('GET', `/api/version?f=${enc(FILE)}`);
    // An answer that began before the page last updated is out of date: it would look like an outside change.
    if (S.doc && gen === S.gen && v.version !== S.doc.version) {
      await load();
      if (S.editing == null) toast('The file changed on disk.');
    }
    const cl = !!v.claude;
    if (cl !== S.claude) { S.claude = cl; if (S.doc) renderHeader(); }
  } catch (e) { /* the server may be restarting */ }
  setTimeout(poll, 1500);
}

// ------------------------------------------------------------ header buttons and keys
$('runall').addEventListener('click', () => runCells('all'));
$('interrupt').addEventListener('click', () => api('POST', '/api/interrupt', {}).catch(e => toast(e.message)));
$('restart').addEventListener('click', () => api('POST', '/api/restart', {}).then(() => toast('Runner stopped. Variables are cleared.')).catch(e => toast(e.message)));
$('undo').addEventListener('click', undo);
$('gcomment').addEventListener('click', () => { S.comp = {type: 'general', text: ''}; renderThreads(); });
$('trust').addEventListener('click', async () => { try { await api('POST', '/api/trust', {}); await load(); } catch (e) { toast(e.message); } });
$('claude').addEventListener('click', async () => {
  try {
    const r = await api('POST', '/api/claude', S.claude ? {stop: true} : {});
    if (r.error) toast(r.error); else { S.claude = !S.claude; renderHeader(); }
  } catch (e) { toast(e.message); }
});

document.addEventListener('keydown', ev => {
  if (ev.target.closest('textarea, input, .cm-editor, [contenteditable]') || !S.doc) return;
  const cells = S.doc.cells, k = cells.findIndex(c => c.idx === S.sel), mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.shiftKey && ev.key.toLowerCase() === 'm') {  // comment on the selected text's line, else on the cell
    ev.preventDefault();
    const sel = getSelection(), n = sel && sel.anchorNode && (sel.anchorNode.parentElement || sel.anchorNode).closest('.ln');
    if (n) S.comp = {type: 'line', cell: +n.closest('.cell').dataset.idx, line: +n.dataset.abs, text: ''};
    else if (S.sel != null) S.comp = {type: 'cell', cell: S.sel, text: ''};
    render();
    return;
  }
  if (mod && ev.key !== 'Enter') return;
  if (ev.key === 'ArrowDown' || ev.key === 'j') { ev.preventDefault(); if (k + 1 < cells.length) select(cells[k + 1].idx); else if (k < 0 && cells.length) select(cells[0].idx); }
  else if (ev.key === 'ArrowUp' || ev.key === 'k') { ev.preventDefault(); if (k > 0) select(cells[k - 1].idx); }
  else if (S.sel == null) return;
  else if (ev.key === 'Enter' && (ev.shiftKey || mod)) {
    ev.preventDefault();
    const c = cells[k];
    if (c && c.runnable) runCells('cell', c.idx);
    if (ev.shiftKey && k + 1 < cells.length) select(cells[k + 1].idx);
  }
  else if (ev.key === 'Enter') { ev.preventDefault(); startEdit(S.sel); }
  else if (ev.key === 'a') { ev.preventDefault(); addCell(Math.max(0, S.sel - 1), 'code'); }
  else if (ev.key === 'b') { ev.preventDefault(); addCell(S.sel, 'code'); }
  else if (ev.key === 'm' && S.sel) setType(S.sel, 'markdown');
  else if (ev.key === 'y' && S.sel) setType(S.sel, 'code');
  else if (ev.key === 'Escape') { S.comp = null; render(); }
});

window.addEventListener('beforeunload', ev => { if (S.editing != null && S.editor && S.editor.getValue() !== S.editBase) ev.preventDefault(); });

// ------------------------------------------------------------ chat
// A conversation with Claude about this notebook, in a panel at the bottom right (kit.chatPanel, from viewkit).
// The server runs `claude` on the computer that serves the page, streams the answer on the event stream,
// and keeps the conversation next to the notebook.
let chatCtxOff = false;  // true after the × of the chip: Claude is not told about the selected cell

function chatCtxCell() {
  if (chatCtxOff || S.sel == null || !S.doc) return null;
  return S.doc.cells.find(c => c.idx === S.sel) || null;
}

// ------------------------------------------------------------ notes to the app (self-improving apps)
// ⌘/Ctrl+Shift+. opens the dashboard of the kit: the notes wait there until Send all, which starts the automatic loop
// (viewkit/improvekit.py). The context is the state of the viewer only: never the code or the path of the file.
kit.improveDash({
  app: 'pyview',
  get: () => api('GET', `/api/improve/dash?f=${encodeURIComponent(FILE)}`),
  post: body => api('POST', '/api/improve/dash', body),
  context: () => ({
    cells: S.doc && S.doc.cells ? S.doc.cells.length : 0, selected: S.sel, editing: S.editing != null,
    kernel: S.kernel ? (S.kernel.state || 'on') : 'off', live: !!S.live, claude: !!S.claude,
    chat: !document.getElementById('chatBox').hidden,
  }),
  onDone: (msg, label, fn) => toast(msg, label || null, fn || null, 12000),
});
// In the sandbox of a proposal (pyview try, or the automatic loop): the card with Accept, Change and Reject.
kit.previewBand({
  get: () => api('GET', `/api/improve/preview?f=${encodeURIComponent(FILE)}`),
  post: body => api('POST', '/api/improve/preview', body),
});

const chat = kit.chatPanel({
  key: 'pyview',
  account: {get: () => api('GET', '/api/account?f=' + encodeURIComponent(FILE)),
            post: body => api('POST', '/api/account', body)},
  empty: 'Ask about this notebook. Claude can read it, change cells, and run them. ' +
    'It runs on the computer that serves this page. Turn on "Ask only" if it must not change anything.',
  get: () => api('GET', `/api/chat?f=${enc(FILE)}`),
  post: body => api('POST', '/api/chat', body),
  extra: () => { const c = chatCtxCell(); return {cell: c ? c.idx : null}; },
  context: () => { const c = chatCtxCell(); return c && {text: `Cell ${c.idx}` + (c.title ? ` · ${c.title}` : '')}; },
  hint: () => chatCtxOff ? '' : 'Select a cell to talk about it.',
  clearContext: () => { chatCtxOff = true; },
  render: text => el('div', 'md', mdRender(text)),
  typeset: box => typesetMath([...box.querySelectorAll('.cm.assistant .md')]),
  fillUser: (e, m) => { e.textContent = m.text; if (m.cell != null) e.title = 'about cell ' + m.cell; },
  done: info => { if (info.changed) { toast('Claude changed the notebook.', 'Undo', undo, 15000); load(); } },
  error: text => toast(text, null, null, 9000),
});

function renderChatCtx() { chat.renderCtx(); }
function askAbout(idx) { select(idx); chatCtxOff = false; chat.open(true); $('chatText').focus(); }

// ------------------------------------------------------------ start
if (!FILE) { $('cells').innerHTML = '<div class="err-page">No file. Open one with: pyview FILE.py</div>'; }
else { load().then(() => { startEvents(); poll(); }); }
