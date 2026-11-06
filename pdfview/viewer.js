'use strict';
// pdfview: the page. It shows the PDF with PDF.js, the comments in the sidebar, and the chat (kit.chatPanel).
// The PDF is never changed. The comments live in a JSON file next to it, and the server changes that file.

const $ = id => document.getElementById(id);
const FILE = new URLSearchParams(location.search).get('f') || '';
const PDFJS = '/vendor/pdfjs/';
const MAX_RENDERED = 12;  // pages with canvases at one time; the others are blank until they come into view
const CONTEXT = 40;       // characters before and after a quote, in an anchor

const S = {
  doc: null,        // the payload of /api/doc: threads, version, ...
  lib: null,        // the PDF.js module
  pdf: null,        // the PDF.js document
  pages: [],        // one per page: {n, w, h, vp1, el, state, gen, ...}
  scale: 1, zoom: 'fit', gen: 0,
  curPage: 1, want: new Set(), pumping: false,
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

// ------------------------------------------------------------ PDF.js
async function loadPdf(keepView) {
  const view = keepView ? viewPoint() : {n: 1, frac: 0};
  if (!S.lib) {
    S.lib = await import(PDFJS + 'pdf.min.mjs');
    S.lib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.mjs';
  }
  if (S.pdf) { S.pdf.destroy(); S.pdf = null; }
  const task = S.lib.getDocument({
    url: `/pdf?f=${enc(FILE)}&v=${enc(S.doc.pdf)}`, withCredentials: true,
    cMapUrl: PDFJS + 'cmaps/', cMapPacked: true, standardFontDataUrl: PDFJS + 'standard_fonts/',
    isEvalSupported: false,  // no eval: the Content-Security-Policy forbids it
    enableXfa: false, disableRange: true, disableStream: true, disableAutoFetch: true,
  });
  try {
    S.pdf = await task.promise;
  } catch (e) {
    $('pages').replaceChildren(el('div', 'err-page', 'Cannot open the PDF: ' + (e.message || e)));
    return;
  }
  const box = $('pages');
  box.replaceChildren();
  S.pages = [];
  for (let n = 1; n <= S.pdf.numPages; n++) {
    const page = await S.pdf.getPage(n);
    const vp1 = page.getViewport({scale: 1});
    const pg = {n, w: vp1.width, h: vp1.height, vp1, state: 'empty', gen: -1};
    pg.el = el('div', 'page');
    pg.el.dataset.n = n;
    pg.el.append(el('div', 'num', String(n)));
    box.append(pg.el);
    S.pages.push(pg);
  }
  $('pageCount').textContent = '/ ' + S.pdf.numPages;
  layout(view);
  loadOutline();
}

// The scale: the zoom, or the width of the pages column.
function fitScale() {
  const maxW = Math.max(...S.pages.map(p => p.w), 1);
  return Math.max(0.2, ($('pages').clientWidth - 72) / maxW);
}

// Set the scale and draw the pages again. The old drawings stay, stretched, until the new ones are ready, so the pages
// do not flash. opts.noScroll: keep the scroll position as it is (after a pinch, which already placed the view).
function layout(view, opts = {}) {
  if (!S.pages.length) return;
  view = opts.noScroll ? null : (view || viewPoint());
  S.gen++;
  S.scale = S.zoom === 'fit' ? fitScale() : S.zoom;
  for (const pg of S.pages) clearPage(pg, true);
  sizePages();
  if (view) {
    const pg = S.pages[view.n - 1];
    if (pg) $('pages').scrollTop = pg.el.offsetTop + view.frac * pg.el.offsetHeight - 16;
  }
  onScroll();
}

// The page boxes at the scale S.scale. The text layer follows --scale-factor. A drawing made at another scale stretches
// with its box (the canvases fill it), and its highlights get the same stretch.
function sizePages() {
  for (const pg of S.pages) {
    pg.el.style.width = Math.floor(pg.w * S.scale) + 'px';
    pg.el.style.height = Math.floor(pg.h * S.scale) + 'px';
    pg.el.style.setProperty('--scale-factor', S.scale);
    const k = pg.rscale ? S.scale / pg.rscale : 1;
    for (const m of pg.el.querySelectorAll('.marks')) m.style.transform = Math.abs(k - 1) < 1e-6 ? '' : `scale(${k})`;
  }
  $('zoomLabel').textContent = Math.round(S.scale * 100) + '%';
  placePop();
}

// Where the reader is: the page at the top of the view and how far down it, to keep it when the layout changes.
function viewPoint() {
  const box = $('pages'), top = box.scrollTop + 16;
  if (!S.pages.length || !S.pages[0].el.offsetHeight) return null;  // no layout yet
  for (const pg of S.pages) {
    if (pg.el.offsetTop + pg.el.offsetHeight > top)
      return {n: pg.n, frac: Math.max(0, (top - pg.el.offsetTop) / pg.el.offsetHeight)};
  }
  return null;
}

// Make a page ready to draw again. keep: leave its old drawing in place until the new one is ready.
function clearPage(pg, keep) {
  if (pg.task) { pg.task.cancel(); pg.task = null; }
  if (pg.tl) { pg.tl.cancel(); pg.tl = null; }
  pg.el.querySelectorAll('.loading').forEach(e => e.remove());
  const old = [...pg.el.querySelectorAll('canvas, .textLayer, .marks')];
  if (keep && (pg.state === 'done' || pg.stale)) pg.stale = pg.stale || old;
  else { old.forEach(e => e.remove()); pg.stale = null; pg.rscale = null; }
  pg.state = 'empty';
  pg.text = null;
  pg.ready = new Promise(r => { pg.resolve = r; });
}

function onScroll() {
  const box = $('pages'), top = box.scrollTop, bottom = top + box.clientHeight, mid = (top + bottom) / 2;
  const margin = box.clientHeight;
  S.want = new Set();
  for (const pg of S.pages) {
    const a = pg.el.offsetTop, b = a + pg.el.offsetHeight;
    if (b > top - margin && a < bottom + margin) S.want.add(pg);
    if (a <= mid && b >= mid) S.curPage = pg.n;
  }
  if (document.activeElement !== $('pageNum')) $('pageNum').value = S.curPage;
  pump();
}

// Render the wanted pages, the nearest first, one at a time.
async function pump() {
  if (S.pumping || P.active) return;  // no drawing while a pinch goes on
  S.pumping = true;
  try {
    for (;;) {
      const next = [...S.want].filter(p => p.state === 'empty')
        .sort((a, b) => Math.abs(a.n - S.curPage) - Math.abs(b.n - S.curPage))[0];
      if (!next) break;
      await renderPage(next);
      dropFar();
    }
  } finally { S.pumping = false; }
}

function dropFar() {
  const done = S.pages.filter(p => p.state === 'done' && !S.want.has(p));
  const extra = S.pages.filter(p => p.state === 'done').length - MAX_RENDERED;
  done.sort((a, b) => Math.abs(b.n - S.curPage) - Math.abs(a.n - S.curPage));
  for (const pg of done.slice(0, Math.max(0, extra))) clearPage(pg);
}

async function renderNow(pg) {
  if (pg.state !== 'done') { S.want.add(pg); pump(); }
  await pg.ready;
}

async function renderPage(pg) {
  const gen = S.gen, scale = S.scale;
  pg.state = 'rendering';
  pg.gen = gen;
  const loading = el('div', 'loading', '…');
  if (!pg.stale) pg.el.append(loading);  // a page with its old drawing needs no "…"
  try {
    const page = await S.pdf.getPage(pg.n);
    const vp = page.getViewport({scale});
    const dpr = window.devicePixelRatio || 1;
    const canvas = el('canvas', 'base');
    canvas.width = Math.floor(vp.width * dpr);
    canvas.height = Math.floor(vp.height * dpr);
    const ctx = canvas.getContext('2d', {alpha: false});
    pg.task = page.render({canvasContext: ctx, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null});
    await pg.task.promise;
    if (gen !== S.gen || P.active) return;  // a new layout or a pinch: the drawing after it replaces this one
    const ops = (pg.task._internalRenderTask && pg.task._internalRenderTask.operatorList) || await page.getOperatorList();
    pg.task = null;
    if (!pg.links) pg.links = await pageLinks(page, pg);
    if (gen !== S.gen || P.active) return;
    paintLinks(ctx, pg.links, canvas.width, canvas.height);
    const imgs = imageCanvas(canvas, imageRects(ops, vp.transform, dpr, canvas.width, canvas.height));

    const textDiv = el('div', 'textLayer');
    pg.tl = new S.lib.TextLayer({textContentSource: page.streamTextContent(), container: textDiv, viewport: vp});
    await pg.tl.render();
    pg.tl = null;
    if (gen !== S.gen || P.active) return;  // a new layout or a pinch: the drawing after it replaces this one
    textDiv.append(el('div', 'endOfContent'));
    const marks = el('div', 'marks');
    loading.remove();
    if (pg.stale) { pg.stale.forEach(e => e.remove()); pg.stale = null; }
    pg.rscale = scale;
    pg.el.append(canvas, imgs, textDiv, marks);
    pg.textDiv = textDiv;
    pg.marks = marks;
    pg.text = pageText(textDiv);
    pg.state = 'done';
    paintMarks(pg);
    pg.resolve();
  } catch (e) {
    if (gen !== S.gen || (e && e.name === 'RenderingCancelledException')) return;
    if (pg.stale) { pg.stale.forEach(e => e.remove()); pg.stale = null; }
    if (!loading.isConnected) pg.el.append(loading);
    loading.textContent = 'Cannot draw this page: ' + (e.message || e);
    pg.state = 'error';
    pg.resolve();
  }
}

// ------------------------------------------------------------ dark pages, images in their colors
// In dark mode the page canvas is inverted with a CSS filter (and a hue turn, so colors stay close). A second canvas
// on top holds the image areas of the page, copied from the page before the filter, so photos keep their colors.
// The image areas come from the operator list of the page: the transform at each image drawing, applied to the unit
// square that PDF images fill.
function imageRects(ops, vt, dpr, W, H) {
  const O = S.lib.OPS, out = [], stack = [];
  let m = [1, 0, 0, 1, 0, 0], annot = 0;
  const mul = (a, b) => [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
    a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]];
  const base = mul([dpr, 0, 0, dpr, 0, 0], vt);
  const add = t => {
    const f = mul(base, t), xs = [], ys = [];
    for (const [x, y] of [[0, 0], [1, 0], [0, 1], [1, 1]]) { xs.push(f[0] * x + f[2] * y + f[4]); ys.push(f[1] * x + f[3] * y + f[5]); }
    const x0 = Math.max(0, Math.floor(Math.min(...xs))), y0 = Math.max(0, Math.floor(Math.min(...ys)));
    const x1 = Math.min(W, Math.ceil(Math.max(...xs))), y1 = Math.min(H, Math.ceil(Math.max(...ys)));
    if (x1 - x0 >= 4 && y1 - y0 >= 4) out.push([x0, y0, x1 - x0, y1 - y0]);
  };
  for (let i = 0; i < ops.fnArray.length; i++) {
    const fn = ops.fnArray[i], a = ops.argsArray[i];
    if (fn === O.save || fn === O.beginGroup) stack.push(m);  // a group draws with the transform it starts with
    else if (fn === O.restore || fn === O.endGroup || fn === O.paintFormXObjectEnd) m = stack.pop() || m;
    else if (fn === O.transform) m = mul(m, a);
    else if (fn === O.paintFormXObjectBegin) { stack.push(m); if (a[0]) m = mul(m, a[0]); }
    else if (fn === O.beginAnnotation) annot++;  // images of annotations (stamps) are left as they are
    else if (fn === O.endAnnotation) annot--;
    else if (annot) continue;
    else if (fn === O.paintImageXObject || fn === O.paintInlineImageXObject) add(m);
    else if (fn === O.paintImageXObjectRepeat) {
      const [, sx, sy, pos] = a;
      for (let k = 0; k < pos.length; k += 2) add(mul(m, [sx, 0, 0, sy, pos[k], pos[k + 1]]));
    }
  }
  // A scanned page is one large image: it is inverted with the page, so it is not copied.
  return out.filter(([, , w, h]) => w * h < 0.8 * W * H);
}

function imageCanvas(src, rects) {
  const c = el('canvas', 'imgs');
  c.width = src.width;
  c.height = src.height;
  const ctx = c.getContext('2d');
  for (const [x, y, w, h] of rects) ctx.drawImage(src, x, y, w, h, x, y, w, h);
  return c;
}

function setDark(on) {
  S.dark = on;
  document.body.classList.toggle('darkpages', on);
  $('darkBtn').classList.toggle('on', on);
  store('dark', on ? '1' : '0');
}

// ------------------------------------------------------------ the text of a page, and anchors
// The text of a page is its text layer: the text nodes in order, and a line break for each <br>.
function pageText(div) {
  const segs = [];
  let s = '';
  const w = document.createTreeWalker(div, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (n.nodeType === 3) { segs.push({node: n, start: s.length, len: n.data.length}); s += n.data; }
    else if (n.tagName === 'BR') { segs.push({node: n, start: s.length, len: 1, br: true}); s += '\n'; }
  }
  return {s, segs};
}

// The offset in the page text of a point of a selection (a node and an offset in it).
function offsetOf(pg, node, off) {
  if (node.nodeType === 3) {
    const seg = pg.text.segs.find(g => g.node === node);
    if (seg) return seg.start + off;
  }
  const r = document.createRange();
  r.setStart(node, off);
  for (const g of pg.text.segs) if (r.comparePoint(g.node, 0) >= 0) return g.start;
  return pg.text.s.length;
}

function pointAt(pg, off, end) {
  for (const g of pg.text.segs) {
    if (off < g.start + g.len || (end && off === g.start + g.len)) {
      if (g.br) { const p = g.node.parentNode; return [p, [...p.childNodes].indexOf(g.node) + (off > g.start ? 1 : 0)]; }
      return [g.node, Math.min(g.len, Math.max(0, off - g.start))];
    }
  }
  const last = pg.text.segs[pg.text.segs.length - 1];
  return last ? [last.node, last.br ? 0 : last.len] : [pg.textDiv, 0];
}

function rangeOf(pg, a, b) {
  const r = document.createRange();
  r.setStart(...pointAt(pg, a, false));
  r.setEnd(...pointAt(pg, b, true));
  return r;
}

// The anchor of the selected text: on the page where the selection starts, cut at the end of that page.
function selectionAnchor() {
  const s = getSelection();
  if (!s.rangeCount || s.isCollapsed) return null;
  const r = s.getRangeAt(0);
  const start = r.startContainer.nodeType === 3 ? r.startContainer.parentElement : r.startContainer;
  const pageEl = start && start.closest && start.closest('.page');
  if (!pageEl) return null;
  const pg = S.pages[+pageEl.dataset.n - 1];
  if (!pg || !pg.text || !pg.textDiv.contains(r.startContainer)) return null;
  let a = offsetOf(pg, r.startContainer, r.startOffset);
  let b = pg.textDiv.contains(r.endContainer) ? offsetOf(pg, r.endContainer, r.endOffset) : pg.text.s.length;
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
    for (const r of rangeOf(pg, a, b).getClientRects()) {
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

function repaintMarks() { for (const pg of S.pages) paintMarks(pg); }

async function showAnchor(t) {
  if (!t.anchor) return;
  const pg = S.pages[t.anchor.page - 1];
  if (!pg) return;
  const box = $('pages');
  if (pg.el.offsetTop > box.scrollTop + box.clientHeight || pg.el.offsetTop + pg.el.offsetHeight < box.scrollTop)
    box.scrollTop = pg.el.offsetTop - 16;
  await renderNow(pg);
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
  const pg = S.pages[c.anchor.page - 1];
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
  setTimeout(() => { if (S.zoom === 'fit') layout(); else repaintMarks(); }, 0);
}

function setNav(on) {
  document.body.classList.toggle('nonav', !on);
  $('navBtn').classList.toggle('on', on);
  store('nav', on ? 'on' : 'off');
  setTimeout(() => { if (S.zoom === 'fit') layout(); else repaintMarks(); }, 0);
}

// ------------------------------------------------------------ the contents (the outline of the PDF)
async function loadOutline() {
  const box = $('outline');
  let items = null;
  try { items = await S.pdf.getOutline(); } catch (e) { /* none */ }
  if (!items || !items.length) { box.replaceChildren(el('div', 'empty', 'This PDF has no contents.')); return; }
  const list = xs => {
    const ul = el('ul');
    for (const it of xs) {
      const li = el('li'), a = el('a', null, it.title || '(untitled)');
      a.href = '#';
      a.title = it.title || '';
      a.addEventListener('click', ev => { ev.preventDefault(); if (it.dest) goDest(it.dest); });
      li.append(a);
      if (it.items && it.items.length) li.append(list(it.items));
      ul.append(li);
    }
    return ul;
  };
  box.replaceChildren(list(items));
}

async function goDest(dest) {  // returns the page number, or null
  try {
    if (typeof dest === 'string') dest = await S.pdf.getDestination(dest);
    if (!Array.isArray(dest)) return;
    const idx = typeof dest[0] === 'object' ? await S.pdf.getPageIndex(dest[0]) : dest[0];
    const pg = S.pages[idx];
    if (!pg) return;
    const kind = dest[1] && dest[1].name;
    const y = kind === 'XYZ' ? dest[3] : (kind === 'FitH' || kind === 'FitBH') ? dest[2] : null;
    const off = y != null ? pg.vp1.convertToViewportPoint(0, y)[1] * S.scale : 0;
    $('pages').scrollTop = pg.el.offsetTop + Math.max(0, off) - 16;
    return idx + 1;
  } catch (e) { toast('Cannot go to this place: ' + (e.message || e)); }
  return null;
}

// ------------------------------------------------------------ links in the PDF
// A link to a place inside the PDF (a reference, a citation, an equation, a section) goes there on a click, and
// ⌘/Ctrl+[ comes back. A web link does not open (see Security in the README). The page has no link
// elements: a click that is not a drag looks for a link under the pointer, so text selection works on links too.
async function pageLinks(page, pg) {
  let annots = [];
  try { annots = await page.getAnnotations({intent: 'display'}); } catch (e) { /* none */ }
  const out = [];
  for (const a of annots) {
    if (a.subtype !== 'Link' || !a.dest || a.url || !Array.isArray(a.rect)) continue;
    const [x0, y0, x1, y1] = pg.vp1.convertToViewportRectangle(a.rect);
    out.push({dest: a.dest, l: Math.min(x0, x1) / pg.w, r: Math.max(x0, x1) / pg.w,
              t: Math.min(y0, y1) / pg.h, b: Math.max(y0, y1) / pg.h});
  }
  return out;
}
// The color of the links, drawn into the page canvas: 'lighten' makes the dark text in a link box the link color and
// leaves the paper as it is. The dark pages invert the canvas with a hue turn, so the links stay blue there (a light
// blue). No layer with a CSS blend mode over the page: while you scroll, a browser can show such a layer as a solid box
// over the words (pdfview#3).
const LINK_COLOR = '#1f5fd6';
function paintLinks(ctx, links, W, H) {
  if (!links || !links.length) return;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = 'lighten';
  ctx.fillStyle = LINK_COLOR;
  for (const k of links) ctx.fillRect(k.l * W, k.t * H, (k.r - k.l) * W, (k.b - k.t) * H);
  ctx.restore();
}
function linkAt(ev) {
  const pageEl = ev.target.closest && ev.target.closest('.page');
  const pg = pageEl && S.pages[+pageEl.dataset.n - 1];
  if (!pg || !pg.links || !pg.links.length) return null;
  const r = pageEl.getBoundingClientRect(), fx = (ev.clientX - r.left) / r.width, fy = (ev.clientY - r.top) / r.height;
  return pg.links.find(k => fx >= k.l && fx <= k.r && fy >= k.t && fy <= k.b) || null;
}
const BACK = [];  // the places before the links that you followed: {i: page index, f: the part of the page above the top}
function here() {
  const top = $('pages').scrollTop + 16;
  let i = 0;
  while (i + 1 < S.pages.length && S.pages[i + 1].el.offsetTop <= top) i++;
  const p = S.pages[i].el;
  return {i, f: (top - p.offsetTop) / p.offsetHeight};
}
function goBack() {
  const b = BACK.pop(), pg = b && S.pages[b.i];  // the PDF can change on disk and have fewer pages
  if (!pg) return false;
  const p = pg.el;
  $('pages').scrollTop = p.offsetTop + b.f * p.offsetHeight - 16;
  return true;
}
async function followLink(k) {
  const from = here(), n = await goDest(k.dest);
  if (n != null) BACK.push(from);  // no notice at the bottom: it covered the page (pdfview#4)
}
$('pages').addEventListener('click', ev => {
  if (ev.button !== 0 || ev.altKey || ev.metaKey || ev.ctrlKey || ev.shiftKey || BOX.drag) return;
  if (ev.target.closest('#pop') || !getSelection().isCollapsed) return;  // a drag that selected text is not a click
  const k = linkAt(ev);
  if (k) { ev.preventDefault(); followLink(k); }
});
$('pages').addEventListener('mousemove', ev => {
  const pageEl = ev.target.closest && ev.target.closest('.page');
  const on = !!(pageEl && !ev.buttons && linkAt(ev));
  for (const e of document.querySelectorAll('.page.onlink')) if (e !== pageEl || !on) e.classList.remove('onlink');
  if (on) pageEl.classList.add('onlink');
}, {passive: true});


function goPage(n) {
  const pg = S.pages[Math.min(S.pages.length, Math.max(1, n)) - 1];
  if (pg) $('pages').scrollTop = pg.el.offsetTop - 16;
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
  const pg = S.pages[an.page - 1], box = pg.el.getBoundingClientRect();
  const rects = [...rangeOf(pg, an.a, an.b).getClientRects()].filter(r => r.width >= 1 && r.height >= 1);
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
  const pg = S.pages[+pageEl.dataset.n - 1];
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
  const a = h.anchor, pg = S.pages[a.page - 1];
  let at = {fx: 0, fy: 0};
  if (a.box) at = {fx: a.box[0] / pg.w, fy: a.box[3] / pg.h};
  else {
    const loc = pg.text && locate(pg.text.s, a);
    if (loc) {
      const r = [...rangeOf(pg, loc.a, loc.b).getClientRects()].filter(x => x.width >= 1).pop(), b = pg.el.getBoundingClientRect();
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
  poll: true,
  ready: () => !!S.doc,
  empty: 'Ask about this PDF. Claude can read it, and it answers or adds comments when you ask. ' +
    'It runs on the computer that serves this page. Turn on "Ask only" if it must not change anything.',
  get: since => api('GET', `/api/chat?f=${enc(FILE)}` + (since == null ? '' : `&since=${since}`)),
  post: body => api('POST', '/api/chat', body),
  extra: () => ({quote: chatSel ? chatSel.quote : '', page: chatSel ? chatSel.page : null, view_page: S.curPage}),
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
    page: S.curPage, pages: S.pages.length, zoom: S.zoom === 'fit' ? 'fit' : S.scale, dark: !!S.dark,
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
  S.zoom = z === 'fit' ? 'fit' : Math.min(5, Math.max(0.25, z));
  store('zoom', String(S.zoom));
  layout();
}

$('zoomIn').onclick = () => setZoom(Math.round(S.scale * 1.2 * 100) / 100);
$('zoomOut').onclick = () => setZoom(Math.round(S.scale / 1.2 * 100) / 100);
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
$('pageNum').addEventListener('keydown', ev => {
  if (ev.key === 'Enter') { goPage(parseInt(ev.target.value, 10) || 1); $('pages').focus(); }
  else if (ev.key === 'Escape') { ev.target.value = S.curPage; $('pages').focus(); }
});
$('pageNum').addEventListener('focus', ev => ev.target.select());

// ------------------------------------------------------------ pinch to zoom (trackpad)
// Chrome and Firefox send a pinch as wheel events with ctrlKey. Safari sends gesturestart, gesturechange and gestureend
// with a scale. While the fingers move, the pages only stretch (fast), and the point under the fingers stays in place.
// When the pinch ends, the pages are drawn again, sharp, at the new scale.
const P = {active: false, scale: 1, g0: 1, at: null, timer: 0, safari: false};

function pinchAnchor(cx, cy) {  // the page under the point, and where in it, as fractions
  const box = $('pages'), r = box.getBoundingClientRect();
  const x = box.scrollLeft + cx - r.left, y = box.scrollTop + cy - r.top;
  const pg = S.pages.find(p => p.el.offsetTop + p.el.offsetHeight >= y) || S.pages[S.pages.length - 1];
  if (!pg) return null;
  return {pg, fx: (x - pg.el.offsetLeft) / pg.el.offsetWidth, fy: (y - pg.el.offsetTop) / pg.el.offsetHeight, cx, cy};
}

function pinchBegin(cx, cy) {
  if (P.active || !S.pages.length) return;
  P.active = true;
  P.scale = S.scale;
  P.at = pinchAnchor(cx, cy);
}

function pinchTo(scale) {
  if (!P.active) return;
  P.scale = Math.min(5, Math.max(0.25, scale));
  S.scale = P.scale;
  sizePages();
  const a = P.at;
  if (a) {
    const box = $('pages'), r = box.getBoundingClientRect();
    box.scrollLeft = a.pg.el.offsetLeft + a.fx * a.pg.el.offsetWidth - (a.cx - r.left);
    box.scrollTop = a.pg.el.offsetTop + a.fy * a.pg.el.offsetHeight - (a.cy - r.top);
  }
}

function pinchEnd() {
  if (!P.active) return;
  P.active = false;
  S.zoom = Math.round(P.scale * 100) / 100;
  store('zoom', String(S.zoom));
  layout(null, {noScroll: true});
}

$('pages').addEventListener('wheel', ev => {
  if (!ev.ctrlKey || P.safari) return;  // a plain wheel scrolls
  ev.preventDefault();  // not the zoom of the whole page
  pinchBegin(ev.clientX, ev.clientY);
  pinchTo(P.scale * Math.exp(-ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.01)));
  clearTimeout(P.timer);
  P.timer = setTimeout(pinchEnd, 250);
}, {passive: false});
for (const [type, fn] of [
  ['gesturestart', ev => { P.safari = true; pinchBegin(ev.clientX, ev.clientY); P.g0 = P.scale; }],
  ['gesturechange', ev => pinchTo(P.g0 * ev.scale)],
  ['gestureend', () => { P.safari = false; pinchEnd(); }],
]) $('pages').addEventListener(type, ev => { ev.preventDefault(); fn(ev); }, {passive: false});

let scrollRaf = 0;
$('pages').addEventListener('scroll', () => { if (!scrollRaf) scrollRaf = requestAnimationFrame(() => { scrollRaf = 0; onScroll(); }); });
let resizeTimer = 0;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (S.zoom === 'fit') layout(); else repaintMarks(); }, 150);
});

document.addEventListener('keydown', ev => {
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.shiftKey && (ev.key === 'M' || ev.key === 'm')) { ev.preventDefault(); commentOnSelection(); return; }
  if (mod && ev.shiftKey && (ev.key === 'H' || ev.key === 'h')) { ev.preventDefault(); highlightSelection(); return; }
  if (mod && !ev.shiftKey && ev.code === 'BracketLeft' && BACK.length) { ev.preventDefault(); goBack(); return; }
  if (mod && ev.key === '\\') { ev.preventDefault(); setSide(document.body.classList.contains('noside')); return; }
  if (mod || ev.altKey || typing(ev.target)) return;
  if (ev.key === '+' || ev.key === '=') setZoom(Math.round(S.scale * 1.2 * 100) / 100);
  else if (ev.key === '-') setZoom(Math.round(S.scale / 1.2 * 100) / 100);
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
  S.zoom = z && z !== 'fit' && !isNaN(+z) ? +z : 'fit';
  if (!FILE) { $('pages').replaceChildren(el('div', 'err-page', 'No file. Open one with: pdfview FILE.pdf')); return; }
  if (!await loadDoc()) return;
  oracleCall();
  await loadPdf(false);
  $('pages').focus();
  setInterval(poll, 2000);
})();
