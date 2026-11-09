'use strict';
// plateview: the pv library inside a plate. The plate runs in a sandbox with no network. pv asks the frame of plateview
// for data files and images by messages, and the frame asks the server, which gives only the files that the plate may
// read. In an exported plate (window.__PV_BUNDLE), the data and the images are in the file itself.
// The components are viewkit/web/components.js (PV). README.md lists the pv functions.
(() => {
  const BUNDLE = window.__PV_BUNDLE || null;
  const pending = new Map();
  let seq = 0;
  const watchers = new Map();  // path -> Set of functions that get the data again when the file changes
  const cache = new Map();     // path -> Promise of the data
  const urls = new Map();      // path -> Promise of a blob URL

  function ask(op, path) {
    if (BUNDLE) {
      if (op === 'data') {
        const d = BUNDLE.data[path];
        if (!d) return Promise.reject(new Error(`This export has no data for ${path}.`));
        return d.error ? Promise.reject(new Error(d.error)) : Promise.resolve(d);
      }
      if (op === 'json') {
        const j = (BUNDLE.json || {})[path];
        if (!j) return Promise.reject(new Error(`This export has no JSON file ${path}.`));
        return j.error ? Promise.reject(new Error(j.error)) : Promise.resolve(j.json);
      }
      const f = BUNDLE.files[path];
      if (!f) return Promise.reject(new Error(`This export has no file ${path}.`));
      const bin = atob(f.b64), buf = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
      return Promise.resolve({type: f.type, buf: buf.buffer});
    }
    if (parent === window) return Promise.reject(new Error('This plate has no data here. Open it with plateview, or use its Export.'));
    return new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, {resolve, reject});
      parent.postMessage({pv: 1, id, op, path}, '*');
    });
  }

  addEventListener('message', ev => {
    if (ev.source !== parent) return;
    const m = ev.data;
    if (!m || m.pv !== 1) return;
    if (m.id) {
      const p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.error) p.reject(new Error(m.error)); else p.resolve(m.value);
      return;
    }
    if (m.op === 'changed') {
      for (const path of m.paths || []) {
        cache.delete(path);
        if (urls.has(path)) { urls.get(path).then(u => URL.revokeObjectURL(u), () => {}); urls.delete(path); }
        for (const fn of watchers.get(path) || []) fn();
      }
    } else if (m.op === 'theme') setTheme(m.theme);
    else if (C.onMessage) C.onMessage(m);  // the comments (bridge, below)
  });

  // ------------------------------------------------------------ theme
  function setTheme(t) {
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
    document.dispatchEvent(new CustomEvent('pv:theme'));
  }
  setTheme(new URLSearchParams(location.search).get('theme'));
  const theme = () => document.documentElement.dataset.theme ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');

  // ------------------------------------------------------------ errors go to the frame, so you see them
  const report = msg => { if (!BUNDLE) parent.postMessage({pv: 1, op: 'error', message: String(msg).slice(0, 600)}, '*'); };
  addEventListener('error', e => report(e.message + (e.lineno ? ` (line ${e.lineno})` : '')));
  addEventListener('unhandledrejection', e => report(e.reason && e.reason.message || e.reason));

  // ------------------------------------------------------------ data
  const data = path => {
    if (!cache.has(path)) {
      const p = ask('data', path);
      cache.set(path, p);
      p.catch(() => cache.delete(path));
    }
    return cache.get(path);
  };
  // fn(data) now, and again each time the file changes. Returns a function that stops it.
  function watch(path, fn, onError) {
    const run = () => data(path).then(fn, e => (onError ? onError(e) : report(e.message)));
    if (!watchers.has(path)) watchers.set(path, new Set());
    watchers.get(path).add(run);
    run();
    return () => watchers.get(path).delete(run);
  }
  // Any JSON file (a JSON Lines file as a list), as it is. Its key in the caches is "json:" and the path.
  const json = path => {
    const key = 'json:' + path;
    if (!cache.has(key)) {
      const p = ask('json', path);
      cache.set(key, p);
      p.catch(() => cache.delete(key));
    }
    return cache.get(key);
  };
  function watchJSON(path, fn, onError) {
    const key = 'json:' + path, run = () => json(path).then(fn, e => (onError ? onError(e) : report(e.message)));
    if (!watchers.has(key)) watchers.set(key, new Set());
    watchers.get(key).add(run);
    run();
    return () => watchers.get(key).delete(run);
  }
  const records = d => d.rows.map(r => Object.fromEntries(d.columns.map((c, i) => [c, r[i]])));
  function fromRecords(list, columns) {
    const cols = columns || [...new Set(list.flatMap(o => Object.keys(o)))];
    return {columns: cols, rows: list.map(o => cols.map(c => o[c] ?? null))};
  }
  // A blob URL of an image (a PDF figure becomes a PNG on the server).
  const url = path => {
    if (!urls.has(path)) {
      const p = ask('file', path).then(f => URL.createObjectURL(new Blob([f.buf], {type: f.type})));
      urls.set(path, p);
      p.catch(() => urls.delete(path));
    }
    return urls.get(path);
  };
  // Load files now, so that Export has them (Export holds the files that the plate has loaded).
  const need = paths => Promise.allSettled([].concat(paths).map(p => /\.(csv|tsv|json|jsonl)$/i.test(p) ? data(p) : url(p)));

  // ------------------------------------------------------------ components
  const target = t => (typeof t === 'string' ? document.querySelector(t) : t);
  const toast = msg => {
    let box = document.getElementById('pv-toast');
    if (!box) { box = document.createElement('div'); box.id = 'pv-toast'; document.body.append(box); }
    const t = document.createElement('div');
    t.className = 'pv-toastmsg';
    t.textContent = msg;
    box.append(t);
    setTimeout(() => t.remove(), 3000);
  };
  const CTX = {toast, theme, data: p => data(p),
               cellThreads: a => C.marks.filter(m => m.anchor.col != null && sameCell(m.anchor, a)).map(m => m.id),
               cellClick: (a, td, text) => { if (BRIDGE) post({op: 'cell', anchor: a, text, rect: rectOf(td)}); }};
  function errorIn(host, e) {
    const b = document.createElement('div');
    b.className = 'pv-error';
    b.textContent = e && e.message ? e.message : String(e);
    host.replaceChildren(b);
  }
  // A table, grid or chart of a data file (a path) or of data ({columns, rows}). It draws again when the file changes.
  // opts: the options of the component (see README.md), and prepare(d) -> d, a function that changes the data first.
  function component(kind) {
    return (where, source, opts = {}) => {
      const host = target(where);
      if (!host) { report(`pv.${kind}: no element ${where}`); return null; }
      const draw = d => {
        try {
          const dd = opts.prepare ? opts.prepare(d) : d;
          // the key of a table names it in a cell comment: the data file, or opts.key
          PV.make(kind, host, dd, {...opts, key: opts.key || (typeof source === 'string' ? source : undefined)}, CTX);
        } catch (e) { errorIn(host, e); report(`pv.${kind}: ${e.message}`); }
      };
      if (typeof source === 'string') return watch(source, draw, e => errorIn(host, e));
      draw(source);
      return () => {};
    };
  }
  // A figure: an image file (PNG, JPEG, GIF, WebP, SVG, or the first page of a PDF). A click opens it large.
  function figure(where, path, opts = {}) {
    const host = target(where);
    if (!host) { report(`pv.figure: no element ${where}`); return null; }
    const f = document.createElement('figure'), img = document.createElement('img');
    f.className = 'pv-figure' + (opts.plain ? ' plain' : '');
    img.alt = opts.alt || opts.caption || path;
    f.append(img);
    if (opts.caption) { const c = document.createElement('figcaption'); c.textContent = opts.caption; f.append(c); }
    img.addEventListener('click', () => PV.lightbox(img));
    host.replaceChildren(f);
    const load = () => url(path).then(u => { img.src = u; }, e => errorIn(host, e));
    if (!watchers.has(path)) watchers.set(path, new Set());
    watchers.get(path).add(load);
    load();
    return f;
  }

  // ------------------------------------------------------------ controls
  // Segmented buttons: options [[value, label], ...]. onChange(value). Returns {value, set(v)}.
  function seg(where, {options, value, onChange, label}) {
    const host = target(where), box = document.createElement('div');
    box.className = 'pv-seg';
    box.setAttribute('role', 'group');
    if (label) box.setAttribute('aria-label', label);
    const ctl = {value};
    const paint = () => box.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === String(ctl.value))));
    for (const o of options) {
      const [v, text] = Array.isArray(o) ? o : [o, o];
      const b = document.createElement('button');
      b.type = 'button'; b.dataset.v = String(v); b.innerHTML = String(text);
      b.addEventListener('click', () => { ctl.value = v; paint(); if (onChange) onChange(v); });
      box.append(b);
    }
    ctl.set = v => { ctl.value = v; paint(); if (onChange) onChange(v); };
    paint();
    host.replaceChildren(box);
    return ctl;
  }
  // A select: options [[value, label], ...].
  function select(where, {options, value, onChange, label}) {
    const host = target(where), s = document.createElement('select');
    s.className = 'pv-select';
    if (label) s.setAttribute('aria-label', label);
    for (const o of options) {
      const [v, text] = Array.isArray(o) ? o : [o, o];
      const op = document.createElement('option');
      op.value = String(v); op.textContent = String(text);
      s.append(op);
    }
    if (value != null) s.value = String(value);
    s.addEventListener('change', () => onChange && onChange(s.value));
    host.replaceChildren(s);
    return {get value() { return s.value; }, set(v) { s.value = String(v); if (onChange) onChange(s.value); }};
  }

  // ------------------------------------------------------------ comments: the bridge to the frame
  // The frame cannot see into the sandbox. The plate tells the frame what the user selects (words, or a cell of a
  // table), and paints the marks of the comments that the frame sends. The plate never changes the comments: only the
  // frame does, from its own buttons. An exported plate has no frame and no bridge.
  function bridge() {
    const SKIP = 'script, style, textarea, select, option, .pv-ui, #pv-toast, .pv-error';
    const CONTEXT = 40;
    const HL = typeof CSS !== 'undefined' && CSS.highlights && typeof Highlight !== 'undefined';
    let T = null, lost = '', sel = false, timer = 0;
    const ranges = new Map();  // thread id -> the Range of its words
    function plateText() {
      if (T) return T;
      const segs = [];
      let s = '';
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: n => (n.parentElement && n.parentElement.closest(SKIP)) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
      for (let n = w.nextNode(); n; n = w.nextNode()) { segs.push({node: n, start: s.length, len: n.data.length}); s += n.data; }
      T = {s, segs};
      return T;
    }
    function offsetOf(node, off) {
      const t = plateText();
      if (node.nodeType === 3) { const g = t.segs.find(x => x.node === node); if (g) return g.start + off; }
      const r = document.createRange();
      r.setStart(node, off);
      for (const g of t.segs) if (r.comparePoint(g.node, 0) >= 0) return g.start;
      return t.s.length;
    }
    function pointAt(off, end) {
      const t = plateText();
      for (const g of t.segs) if (off < g.start + g.len || (end && off === g.start + g.len)) return [g.node, Math.min(g.len, Math.max(0, off - g.start))];
      const last = t.segs[t.segs.length - 1];
      return last ? [last.node, last.len] : [document.body, 0];
    }
    function rangeOf(a, b) { const r = document.createRange(); r.setStart(...pointAt(a, false)); r.setEnd(...pointAt(b, true)); return r; }
    const commonEnd = (x, y) => { let k = 0; while (k < x.length && k < y.length && x[x.length - 1 - k] === y[y.length - 1 - k]) k++; return k; };
    const commonStart = (x, y) => { let k = 0; while (k < x.length && k < y.length && x[k] === y[k]) k++; return k; };
    // The words of an anchor: the exact words with the best fit of the words before and after them, else the same
    // letters without spaces.
    function locate(an) {
      const text = plateText().s, q = an.quote;
      let best = null;
      for (let i = text.indexOf(q); i >= 0; i = text.indexOf(q, i + 1)) {
        const score = commonEnd(text.slice(Math.max(0, i - CONTEXT), i), an.prefix || '') +
                      commonStart(text.slice(i + q.length, i + q.length + CONTEXT), an.suffix || '');
        if (!best || score > best.score) best = {a: i, b: i + q.length, score};
      }
      if (best) return best;
      const squeeze = s => { const c = [], idx = []; for (let i = 0; i < s.length; i++) { if (/\s/.test(s[i])) continue; c.push(s[i].toLowerCase()); idx.push(i); } return {s: c.join(''), idx}; };
      const Tq = squeeze(text), Q = squeeze(q).s;
      if (!Q) return null;
      const i = Tq.s.indexOf(Q);
      return i < 0 ? null : {a: Tq.idx[i], b: Tq.idx[i + Q.length - 1] + 1};
    }
    function findCell(a) {
      for (const box of document.querySelectorAll('.pv-table, .pv-grid')) {
        if (box.dataset.table !== a.table || !box.pvFind) continue;
        const td = box.pvFind(a);
        if (td) return td;
      }
      return null;
    }
    // Paint the marks, and tell the frame which comments have no place in the plate now.
    function paint() {
      ranges.clear();
      const marks = [], act = [], dr = [], gone = [];
      for (const m of C.marks) {
        if (m.anchor.col != null) { if (!findCell(m.anchor)) gone.push(m.id); continue; }
        const loc = locate(m.anchor);
        if (!loc) { gone.push(m.id); continue; }
        const r = rangeOf(loc.a, loc.b);
        ranges.set(m.id, r);
        (m.id === C.active ? act : marks).push(r);
      }
      if (C.draft && C.draft.quote) { const loc = locate(C.draft); if (loc) dr.push(rangeOf(loc.a, loc.b)); }
      if (HL) {
        CSS.highlights.set('pv-mark', new Highlight(...marks));
        CSS.highlights.set('pv-active', new Highlight(...act));
        CSS.highlights.set('pv-draft', new Highlight(...dr));
      }
      document.querySelectorAll('td.cact').forEach(td => td.classList.remove('cact'));
      const am = C.marks.find(m => m.id === C.active && m.anchor.col != null);
      if (am) { const td = findCell(am.anchor); if (td) td.classList.add('cact'); }
      const key = gone.sort((x, y) => x - y).join(',');
      if (key !== lost) { lost = key; post({op: 'lost', ids: gone}); }
    }
    const later = () => { clearTimeout(timer); timer = setTimeout(() => { T = null; paint(); }, 150); };
    // The plate draws its tables and charts late, and again when a file changes: find the words again.
    new MutationObserver(later).observe(document.body, {subtree: true, childList: true, characterData: true});
    addEventListener('resize', later);

    function anchorOfSelection() {
      const s = getSelection();
      if (!s.rangeCount || s.isCollapsed) return null;
      const r = s.getRangeAt(0), start = r.startContainer.nodeType === 3 ? r.startContainer.parentElement : r.startContainer;
      if (!start || start.closest(SKIP)) return null;
      const t = plateText();
      let a = offsetOf(r.startContainer, r.startOffset), b = offsetOf(r.endContainer, r.endOffset);
      while (a < b && /\s/.test(t.s[a])) a++;
      while (b > a && /\s/.test(t.s[b - 1])) b--;
      if (b <= a) return null;
      const rects = r.getClientRects();
      return {anchor: {quote: t.s.slice(a, b), prefix: t.s.slice(Math.max(0, a - CONTEXT), a), suffix: t.s.slice(b, b + CONTEXT)},
              rect: rectOf(rects[rects.length - 1] || r)};
    }
    document.addEventListener('selectionchange', () => {
      const x = anchorOfSelection();
      if (x) { sel = true; post({op: 'sel', ...x}); }
      else if (sel) { sel = false; post({op: 'sel', anchor: null}); }
    });
    // A click on marked words selects their comment.
    document.addEventListener('click', ev => {
      if (!ranges.size || !getSelection().isCollapsed || ev.target.closest('a, button, input, select, textarea')) return;
      let node, off;
      if (document.caretPositionFromPoint) { const p = document.caretPositionFromPoint(ev.clientX, ev.clientY); if (p) { node = p.offsetNode; off = p.offset; } }
      else if (document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(ev.clientX, ev.clientY); if (r) { node = r.startContainer; off = r.startOffset; } }
      if (!node) return;
      for (const [id, r] of ranges) {
        try { if (r.isPointInRange(node, off)) { post({op: 'pick', id}); return; } } catch (e) { /* another node */ }
      }
    });
    // The place of the new comment, so the comment box of the frame stays under the words when the plate scrolls.
    function draftRect() {
      if (!C.draft) return null;
      if (C.draft.col != null) { const td = findCell(C.draft); return td ? rectOf(td) : null; }
      const loc = locate(C.draft);
      if (!loc) return null;
      const rs = [...rangeOf(loc.a, loc.b).getClientRects()].filter(r => r.width >= 1);
      return rs.length ? rectOf(rs[rs.length - 1]) : null;
    }
    let raf = 0;
    addEventListener('scroll', () => {
      if (!C.draft || raf) return;
      raf = requestAnimationFrame(() => { raf = 0; post({op: 'draft', rect: draftRect()}); });
    }, {capture: true, passive: true});
    // The keys of the frame work also when the plate has the focus: the chat (⌘/Ctrl+S), the comment (⌘/Ctrl+Shift+M),
    // the sidebar (⌘/Ctrl+\), the dashboard (⌘/Ctrl+Shift+.) and the PDF (⌘/Ctrl+P).
    addEventListener('keydown', ev => {
      if (!(ev.metaKey || ev.ctrlKey) || ev.altKey) return;
      const c = ev.code;
      if (!((!ev.shiftKey && (c === 'KeyS' || c === 'KeyP')) || (ev.shiftKey && (c === 'KeyM' || c === 'Period')) || c === 'Backslash')) return;
      ev.preventDefault();
      post({op: 'key', code: c, key: ev.key, shift: ev.shiftKey, meta: ev.metaKey, ctrl: ev.ctrlKey});
    }, true);

    C.onMessage = m => {
      if (m.op === 'marks') {
        const cells = JSON.stringify(C.marks.filter(x => x.anchor.col != null).map(x => x.id));
        C.marks = Array.isArray(m.marks) ? m.marks.filter(x => x && x.anchor) : [];
        C.active = m.active || null;
        C.draft = m.draft || null;
        if (JSON.stringify(C.marks.filter(x => x.anchor.col != null).map(x => x.id)) !== cells) PV.refresh(document.body);
        T = null;
        paint();
        if (C.draft) post({op: 'draft', rect: draftRect()});
      } else if (m.op === 'show') {
        const mk = C.marks.find(x => x.id === m.id);
        if (!mk) return;
        const target = mk.anchor.col != null ? findCell(mk.anchor) : ranges.get(mk.id);
        if (!target) return;
        const r = target.getBoundingClientRect();
        if (r.top < 40 || r.bottom > innerHeight - 40) scrollBy({top: r.top - innerHeight / 3});
        if (mk.anchor.col != null) { target.classList.remove('flash'); void target.offsetWidth; target.classList.add('flash'); }
      } else if (m.op === 'unselect') getSelection().removeAllRanges();
    };
  }
  const C = {marks: [], active: null, draft: null, onMessage: null};  // the comments that the frame sends
  const BRIDGE = !BUNDLE && parent !== window;
  const post = m => parent.postMessage({pv: 1, ...m}, '*');
  const sameCell = (x, y) => x.table === y.table && String(x.row) === String(y.row) && String(x.col) === String(y.col);
  const rectOf = e => { const r = e.getBoundingClientRect ? e.getBoundingClientRect() : e; return {left: r.left, top: r.top, right: r.right, bottom: r.bottom}; };
  if (BRIDGE) {
    if (document.body) bridge(); else addEventListener('DOMContentLoaded', bridge);
  }

  window.pv = {data, watch, json, watchJSON, records, fromRecords, url, need, figure, seg, select, toast, theme,
               table: component('table'), grid: component('grid'), chart: component('chart'),
               fmt: (x, dec) => PV.fmtNum(x, dec), exported: !!BUNDLE};
})();
