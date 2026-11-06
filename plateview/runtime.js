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
  const CTX = {toast, theme, data: p => data(p)};
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
          PV.make(kind, host, dd, opts, CTX);
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

  window.pv = {data, watch, json, watchJSON, records, fromRecords, url, need, figure, seg, select, toast, theme,
               table: component('table'), grid: component('grid'), chart: component('chart'),
               fmt: (x, dec) => PV.fmtNum(x, dec), exported: !!BUNDLE};
})();
