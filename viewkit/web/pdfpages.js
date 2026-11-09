'use strict';
// kit.pdfPages: the pages of a PDF, drawn with PDF.js (web/vendor/pdfjs). pdfview and texview use it.
// It draws the pages near the view, with a text layer for selection, and it does the zoom (buttons, keys, pinch), the
// dark pages (images keep their colors), the links inside the PDF, and the contents (the outline of the PDF).
// The app keeps its own marks on the pages: each drawn page has a layer `pg.marks`, and opts.onPage(pg) runs after a
// page is drawn. Load web/kit.js and web/pdfpages.css first.
//
//   const pv = kit.pdfPages({
//     box,           the scroll box of the pages (an element)
//     zoomLabel, pageNum, pageCount, outline,   optional elements: the zoom in %, the page input, "/ N", the contents
//     onPage(pg),    a page is drawn: paint the marks of the app on pg.marks
//     onSize(),      the page boxes changed size (a zoom or a pinch): move what the app placed on the pages
//     onScroll(n),   the page in the middle of the view is now n
//     skipClick(ev), true: this click is not for a link (the app uses it, for example at the end of a drag)
//     toast(msg),    show a short message
//   });
//   await pv.load(url, keepView)   open (or open again) the PDF at url; keepView keeps the place in the document
//   pv.pages, pv.scale, pv.zoom, pv.curPage, pv.pdf   the state (pages: {n, w, h, el, state, marks, text, textDiv})
//   pv.setZoom(z | 'fit'), pv.zoomBy(k), pv.relayout(), pv.setDark(on), pv.goPage(n), pv.goBack(), pv.renderNow(pg)
//   pv.goForward(), pv.navKey(ev)   ⌘/Ctrl+[ and ⌘/Ctrl+]: back and forward over the jumps (links, contents, page number)
//   pv.pointOf(ev) -> {n, x, y}     the place under the pointer, in PDF points from the top-left corner of the page
//   pv.showBoxes(n, boxes, cls)     scroll to the boxes ([x0, y0, x1, y1] in PDF points) on page n and mark them
//   pv.rangeOf(pg, a, b), pv.offsetOf(pg, node, off)   the text of a drawn page (pg.text = {s, segs})
kit.pdfPages = function (o) {
  const PDFJS = '/kit/vendor/pdfjs/';
  const MAX_RENDERED = 12;  // pages with canvases at one time; the others are blank until they come into view
  const box = o.box;
  const V = {lib: null, pdf: null, pages: [], scale: 1, zoom: 'fit', gen: 0, curPage: 1, dark: false, url: null};
  const want = {set: new Set()};
  let pumping = false;
  const P = {active: false, scale: 1, g0: 1, at: null, timer: 0, safari: false};  // a pinch
  // Remove drawn parts of a page. A canvas gets the size 0 first: Safari frees the memory of a canvas late when the
  // page only removes it, and each page canvas is many MB (width x height x 4 bytes).
  const drop = els => {
    for (const e of els) {
      if (!e) continue;
      for (const c of e.tagName === 'CANVAS' ? [e] : e.querySelectorAll('canvas')) { c.width = 0; c.height = 0; }
      e.remove();
    }
  };
  const h = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  // ------------------------------------------------------------ open the PDF
  async function load(url, keepView) {
    const view = keepView ? viewPoint() : {n: 1, frac: 0};
    if (!V.lib) {
      V.lib = await import(PDFJS + 'pdf.min.mjs');
      V.lib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.mjs';
    }
    const task = V.lib.getDocument({
      url, withCredentials: true,
      cMapUrl: PDFJS + 'cmaps/', cMapPacked: true, standardFontDataUrl: PDFJS + 'standard_fonts/',
      isEvalSupported: false,  // no eval: the Content-Security-Policy forbids it
      enableXfa: false, disableRange: true, disableStream: true, disableAutoFetch: true,
    });
    let pdf;
    try {
      pdf = await task.promise;
    } catch (e) {
      if (!V.pdf) box.replaceChildren(h('div', 'err-page', 'Cannot open the PDF: ' + (e.message || e)));
      return false;
    }
    V.gen++;  // a drawing of the old document that is still going stops quietly
    if (V.pdf) V.pdf.destroy();  // the old pages stay on the screen until the new ones are ready
    V.pdf = pdf;
    V.url = url;
    const pages = [];
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const vp1 = page.getViewport({scale: 1});
      const pg = {n, w: vp1.width, h: vp1.height, vp1, state: 'empty', gen: -1};
      pg.el = h('div', 'page');
      pg.el.dataset.n = n;
      pg.el.append(h('div', 'num', String(n)));
      pages.push(pg);
    }
    // A new version of the same PDF (texview compiles again): each page keeps the old drawing until the new one is
    // ready, so the pages do not flash.
    for (const pg of pages) {
      const old = V.pages[pg.n - 1];
      if (!old || Math.abs(old.w - pg.w) > 0.5 || Math.abs(old.h - pg.h) > 0.5) continue;
      if (old.task) { old.task.cancel(); old.task = null; }
      if (old.tl) { old.tl.cancel(); old.tl = null; }
      const kids = old.stale || (old.state === 'done' ? [...old.el.querySelectorAll('canvas, .textLayer, .marks')] : []);
      if (!kids.length) continue;
      pg.el.append(...kids);
      pg.stale = kids;
      pg.rscale = old.rscale;
    }
    const top = box.scrollTop;
    drop(box.querySelectorAll(':scope > .page, :scope > .err-page'));  // the drawings that did not go to the new pages
    for (const pg of pages) box.append(pg.el);
    V.pages = pages;
    if (o.pageCount) o.pageCount.textContent = '/ ' + pdf.numPages;
    box.scrollTop = top;
    layout(view);
    loadOutline();
    return true;
  }

  // The scale: the zoom, or the width of the pages column.
  function fitScale() {
    const maxW = Math.max(...V.pages.map(p => p.w), 1);
    return Math.max(0.2, (box.clientWidth - 72) / maxW);
  }

  // Set the scale and draw the pages again. The old drawings stay, stretched, until the new ones are ready, so the
  // pages do not flash. opts.noScroll: keep the scroll position as it is (after a pinch, which already placed the view).
  function layout(view, opts = {}) {
    if (!V.pages.length) return;
    view = opts.noScroll ? null : (view || viewPoint());
    V.gen++;
    V.scale = V.zoom === 'fit' ? fitScale() : V.zoom;
    for (const pg of V.pages) clearPage(pg, true);
    sizePages();
    if (view) {
      const pg = V.pages[view.n - 1];
      if (pg) box.scrollTop = pg.el.offsetTop + view.frac * pg.el.offsetHeight - 16;
    }
    onScroll();
  }

  // The page boxes at the scale V.scale. The text layer follows --scale-factor. A drawing made at another scale
  // stretches with its box (the canvases fill it), and its marks get the same stretch.
  function sizePages() {
    for (const pg of V.pages) {
      pg.el.style.width = Math.floor(pg.w * V.scale) + 'px';
      pg.el.style.height = Math.floor(pg.h * V.scale) + 'px';
      pg.el.style.setProperty('--scale-factor', V.scale);
      const k = pg.rscale ? V.scale / pg.rscale : 1;
      for (const m of pg.el.querySelectorAll('.marks')) m.style.transform = Math.abs(k - 1) < 1e-6 ? '' : `scale(${k})`;
    }
    if (o.zoomLabel) o.zoomLabel.textContent = Math.round(V.scale * 100) + '%';
    if (o.onSize) o.onSize();
  }

  // Where the reader is: the page at the top of the view and how far down it, to keep it when the layout changes.
  function viewPoint() {
    const top = box.scrollTop + 16;
    if (!V.pages.length || !V.pages[0].el.offsetHeight) return null;  // no layout yet
    for (const pg of V.pages) {
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
    else { drop(old); if (pg.stale) drop(pg.stale); pg.stale = null; pg.rscale = null; }
    pg.state = 'empty';
    pg.text = null;
    pg.ready = new Promise(r => { pg.resolve = r; });
  }

  function onScroll() {
    const top = box.scrollTop, bottom = top + box.clientHeight, mid = (top + bottom) / 2;
    const margin = box.clientHeight;
    want.set = new Set();
    for (const pg of V.pages) {
      const a = pg.el.offsetTop, b = a + pg.el.offsetHeight;
      if (b > top - margin && a < bottom + margin) want.set.add(pg);
      if (a <= mid && b >= mid) V.curPage = pg.n;
    }
    if (o.pageNum && document.activeElement !== o.pageNum) o.pageNum.value = V.curPage;
    if (o.onScroll) o.onScroll(V.curPage);
    pump();
  }

  // Draw the wanted pages, the nearest first, one at a time.
  async function pump() {
    if (pumping || P.active) return;  // no drawing while a pinch goes on
    pumping = true;
    try {
      for (;;) {
        const next = [...want.set].filter(p => p.state === 'empty' && V.pages.includes(p))
          .sort((a, b) => Math.abs(a.n - V.curPage) - Math.abs(b.n - V.curPage))[0];
        if (!next) break;
        await renderPage(next);
        dropFar();
      }
      dropFar();
    } finally { pumping = false; }
  }

  // Keep few drawings: an old drawing (after a compile or a zoom) of a page that is not near the view goes, and the
  // drawn pages that are farthest from the view go when there are more than MAX_RENDERED.
  function dropFar() {
    for (const pg of V.pages) if (pg.stale && pg.state === 'empty' && !want.set.has(pg)) clearPage(pg);
    const done = V.pages.filter(p => p.state === 'done' && !want.set.has(p));
    const extra = V.pages.filter(p => p.state === 'done').length - MAX_RENDERED;
    done.sort((a, b) => Math.abs(b.n - V.curPage) - Math.abs(a.n - V.curPage));
    for (const pg of done.slice(0, Math.max(0, extra))) clearPage(pg);
  }

  async function renderNow(pg) {
    if (pg.state !== 'done') { want.set.add(pg); pump(); }
    await pg.ready;
  }

  async function renderPage(pg) {
    const gen = V.gen, scale = V.scale, pdf = V.pdf;
    let canvas = null, imgs = null;
    const stop = () => drop([canvas, imgs]);  // a drawing that is not used: free its canvases now
    pg.state = 'rendering';
    pg.gen = gen;
    const loading = h('div', 'loading', '…');
    if (!pg.stale) pg.el.append(loading);  // a page with its old drawing needs no "…"
    try {
      const page = await pdf.getPage(pg.n);
      const vp = page.getViewport({scale});
      const dpr = window.devicePixelRatio || 1;
      canvas = h('canvas', 'base');
      canvas.width = Math.floor(vp.width * dpr);
      canvas.height = Math.floor(vp.height * dpr);
      const ctx = canvas.getContext('2d', {alpha: false});
      pg.task = page.render({canvasContext: ctx, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null});
      await pg.task.promise;
      if (gen !== V.gen || P.active) return stop();  // a new layout or a pinch: the drawing after it replaces this one
      const ops = (pg.task._internalRenderTask && pg.task._internalRenderTask.operatorList) || await page.getOperatorList();
      pg.task = null;
      if (!pg.links) pg.links = await pageLinks(page, pg);
      if (gen !== V.gen || P.active) return stop();
      paintLinks(ctx, pg.links, canvas.width, canvas.height);
      imgs = imageCanvas(canvas, imageRects(ops, vp.transform, dpr, canvas.width, canvas.height));

      const textDiv = h('div', 'textLayer');
      pg.tl = new V.lib.TextLayer({textContentSource: page.streamTextContent(), container: textDiv, viewport: vp});
      await pg.tl.render();
      pg.tl = null;
      if (gen !== V.gen || P.active) return stop();  // a new layout or a pinch: the drawing after it replaces this one
      textDiv.append(h('div', 'endOfContent'));
      const marks = h('div', 'marks');
      loading.remove();
      if (pg.stale) { drop(pg.stale); pg.stale = null; }
      pg.rscale = scale;
      pg.el.append(...[canvas, imgs].filter(Boolean), textDiv, marks);
      canvas = imgs = null;  // on the page now: stop() must not free them
      pg.textDiv = textDiv;
      pg.marks = marks;
      pg.text = pageText(textDiv);
      pg.state = 'done';
      if (o.onPage) o.onPage(pg);
      pg.resolve();
    } catch (e) {
      stop();
      if (gen !== V.gen || (e && e.name === 'RenderingCancelledException')) return;
      if (pg.stale) { drop(pg.stale); pg.stale = null; }
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
    const O = V.lib.OPS, out = [], stack = [];
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
    return out.filter(([, , w, hh]) => w * hh < 0.8 * W * H);
  }

  function imageCanvas(src, rects) {
    if (!rects.length) return null;  // no images: no second canvas (it would be as large as the page)
    const c = h('canvas', 'imgs');
    c.width = src.width;
    c.height = src.height;
    const ctx = c.getContext('2d');
    for (const [x, y, w, hh] of rects) ctx.drawImage(src, x, y, w, hh, x, y, w, hh);
    return c;
  }

  function setDark(on) {
    V.dark = on;
    document.body.classList.toggle('darkpages', on);
  }

  // ------------------------------------------------------------ the text of a page
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

  function pointIn(pg, off, end) {
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
    r.setStart(...pointIn(pg, a, false));
    r.setEnd(...pointIn(pg, b, true));
    return r;
  }

  // The page of an element, and the place of a pointer event on its page in PDF points (top-left corner).
  function pageOf(target) {
    const pageEl = target && target.closest && target.closest('.page');
    return pageEl ? V.pages[+pageEl.dataset.n - 1] || null : null;
  }
  function pointOf(ev) {
    const pg = pageOf(ev.target);
    if (!pg) return null;
    const r = pg.el.getBoundingClientRect();
    return {n: pg.n, x: (ev.clientX - r.left) * pg.w / r.width, y: (ev.clientY - r.top) * pg.h / r.height};
  }

  // Scroll to boxes on page n (PDF points from the top-left corner) and mark them for a moment.
  async function showBoxes(n, boxes, cls = 'sync') {
    const pg = V.pages[n - 1];
    if (!pg || !boxes.length) return;
    const top = Math.min(...boxes.map(b => b[1])), k = pg.el.offsetHeight / pg.h;
    const y = pg.el.offsetTop + top * k;
    if (y < box.scrollTop + 40 || y > box.scrollTop + box.clientHeight - 80) box.scrollTop = y - box.clientHeight / 3;
    await renderNow(pg);
    if (!pg.marks) return;
    pg.marks.querySelectorAll('.' + cls).forEach(e => e.remove());
    const kx = pg.el.offsetWidth / pg.w / (pg.rscale ? V.scale / pg.rscale : 1), ky = pg.el.offsetHeight / pg.h / (pg.rscale ? V.scale / pg.rscale : 1);
    for (const b of boxes) {
      const d = h('div', cls);
      d.style.left = b[0] * kx + 'px'; d.style.top = b[1] * ky + 'px';
      d.style.width = Math.max(4, (b[2] - b[0]) * kx) + 'px'; d.style.height = Math.max(4, (b[3] - b[1]) * ky) + 'px';
      pg.marks.append(d);
      setTimeout(() => d.remove(), 2600);
    }
  }

  // ------------------------------------------------------------ the contents (the outline of the PDF)
  async function loadOutline() {
    const out = o.outline;
    if (!out) return;
    let items = null;
    try { items = await V.pdf.getOutline(); } catch (e) { /* none */ }
    if (!items || !items.length) { out.replaceChildren(h('div', 'empty', 'This PDF has no contents.')); return; }
    const list = xs => {
      const ul = h('ul');
      for (const it of xs) {
        const li = h('li'), a = h('a', null, it.title || '(untitled)');
        a.href = '#';
        a.title = it.title || '';
        a.addEventListener('click', ev => { ev.preventDefault(); if (it.dest) jump(() => goDest(it.dest)); });
        li.append(a);
        if (it.items && it.items.length) li.append(list(it.items));
        ul.append(li);
      }
      return ul;
    };
    out.replaceChildren(list(items));
  }

  async function goDest(dest) {  // returns the page number, or null
    try {
      if (typeof dest === 'string') dest = await V.pdf.getDestination(dest);
      if (!Array.isArray(dest)) return null;
      const idx = typeof dest[0] === 'object' ? await V.pdf.getPageIndex(dest[0]) : dest[0];
      const pg = V.pages[idx];
      if (!pg) return null;
      const kind = dest[1] && dest[1].name;
      const y = kind === 'XYZ' ? dest[3] : (kind === 'FitH' || kind === 'FitBH') ? dest[2] : null;
      const off = y != null ? pg.vp1.convertToViewportPoint(0, y)[1] * V.scale : 0;
      box.scrollTop = pg.el.offsetTop + Math.max(0, off) - 16;
      return idx + 1;
    } catch (e) { if (o.toast) o.toast('Cannot go to this place: ' + (e.message || e)); }
    return null;
  }

  // ------------------------------------------------------------ links in the PDF
  // A link to a place inside the PDF (a reference, a citation, an equation, a section) goes there on a click, and
  // ⌘/Ctrl+[ comes back. A web link does not open. The page has no link elements: a click that is not a drag looks
  // for a link under the pointer, so text selection works on links too.
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
  // blue). No layer with a CSS blend mode over the page: while you scroll, a browser can show such a layer as a solid
  // box over the words (pdfview#3).
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
    const pg = pageOf(ev.target);
    if (!pg || !pg.links || !pg.links.length) return null;
    const r = pg.el.getBoundingClientRect(), fx = (ev.clientX - r.left) / r.width, fy = (ev.clientY - r.top) / r.height;
    return pg.links.find(k => fx >= k.l && fx <= k.r && fy >= k.t && fy <= k.b) || null;
  }
  // The history of the jumps: a link, a contents entry or a page number. ⌘/Ctrl+[ goes back to the place before a
  // jump, and ⌘/Ctrl+] goes forward again, as in a browser (texview#22). A place: {i: page index, f: the part of the
  // page above the top}. A new jump clears the places ahead.
  const BACK = [], FWD = [];
  function here() {
    const top = box.scrollTop + 16;
    let i = 0;
    while (i + 1 < V.pages.length && V.pages[i + 1].el.offsetTop <= top) i++;
    const p = V.pages[i].el;
    return {i, f: (top - p.offsetTop) / p.offsetHeight};
  }
  function step(from, to) {  // go to the last place of the stack from, and put the current place on the stack to
    const b = from.pop(), pg = b && V.pages[b.i];  // the PDF can change and have fewer pages
    if (!pg) return false;
    to.push(here());
    box.scrollTop = pg.el.offsetTop + b.f * pg.el.offsetHeight - 16;
    return true;
  }
  const goBack = () => step(BACK, FWD), goForward = () => step(FWD, BACK);
  async function jump(go) {  // go() moves the view and returns the page number, or null if it did not move
    const from = V.pages.length ? here() : null, n = await go();
    if (n != null && from) { BACK.push(from); FWD.length = 0; }
    return n;
  }
  function navKey(ev) {  // the app calls it from its keydown handler: true if the key was ⌘/Ctrl+[ or ⌘/Ctrl+]
    if (!(ev.metaKey || ev.ctrlKey) || ev.shiftKey || ev.altKey) return false;
    const go = ev.code === 'BracketLeft' ? goBack : ev.code === 'BracketRight' ? goForward : null;
    if (!go) return false;
    ev.preventDefault();  // not the history of the browser tab, which leaves the document
    go();
    return true;
  }
  const followLink = k => jump(() => goDest(k.dest));  // no notice at the bottom: it covered the page (pdfview#4)
  box.addEventListener('click', ev => {
    if (ev.button !== 0 || ev.altKey || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.detail > 1) return;
    if ((o.skipClick && o.skipClick(ev)) || !getSelection().isCollapsed) return;  // a drag that selected text is not a click
    const k = linkAt(ev);
    if (k) { ev.preventDefault(); followLink(k); }
  });
  box.addEventListener('mousemove', ev => {
    const pg = pageOf(ev.target);
    const on = !!(pg && !ev.buttons && linkAt(ev));
    for (const e of box.querySelectorAll('.page.onlink')) if (!pg || e !== pg.el || !on) e.classList.remove('onlink');
    if (on) pg.el.classList.add('onlink');
  }, {passive: true});

  function goPage(n) {
    const pg = V.pages[Math.min(V.pages.length, Math.max(1, n)) - 1];
    if (pg) box.scrollTop = pg.el.offsetTop - 16;
  }
  if (o.pageNum) {
    o.pageNum.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { const n = parseInt(ev.target.value, 10) || 1; jump(async () => (goPage(n), n)); box.focus(); }
      else if (ev.key === 'Escape') { ev.target.value = V.curPage; box.focus(); }
    });
    o.pageNum.addEventListener('focus', ev => ev.target.select());
  }

  // ------------------------------------------------------------ zoom, and pinch to zoom (trackpad)
  function setZoom(z) {
    V.zoom = z === 'fit' ? 'fit' : Math.min(5, Math.max(0.25, z));
    layout();
  }
  const zoomBy = k => setZoom(Math.round(V.scale * k * 100) / 100);

  // Chrome and Firefox send a pinch as wheel events with ctrlKey. Safari sends gesturestart, gesturechange and
  // gestureend with a scale. While the fingers move, the pages only stretch (fast), and the point under the fingers
  // stays in place. When the pinch ends, the pages are drawn again, sharp, at the new scale.
  function pinchAnchor(cx, cy) {  // the page under the point, and where in it, as fractions
    const r = box.getBoundingClientRect();
    const x = box.scrollLeft + cx - r.left, y = box.scrollTop + cy - r.top;
    const pg = V.pages.find(p => p.el.offsetTop + p.el.offsetHeight >= y) || V.pages[V.pages.length - 1];
    if (!pg) return null;
    return {pg, fx: (x - pg.el.offsetLeft) / pg.el.offsetWidth, fy: (y - pg.el.offsetTop) / pg.el.offsetHeight, cx, cy};
  }
  function pinchBegin(cx, cy) {
    if (P.active || !V.pages.length) return;
    P.active = true;
    P.scale = V.scale;
    P.at = pinchAnchor(cx, cy);
  }
  function pinchTo(scale) {
    if (!P.active) return;
    P.scale = Math.min(5, Math.max(0.25, scale));
    V.scale = P.scale;
    sizePages();
    const a = P.at;
    if (a) {
      const r = box.getBoundingClientRect();
      box.scrollLeft = a.pg.el.offsetLeft + a.fx * a.pg.el.offsetWidth - (a.cx - r.left);
      box.scrollTop = a.pg.el.offsetTop + a.fy * a.pg.el.offsetHeight - (a.cy - r.top);
    }
  }
  function pinchEnd() {
    if (!P.active) return;
    P.active = false;
    V.zoom = Math.round(P.scale * 100) / 100;
    if (o.onZoom) o.onZoom(V.zoom);
    layout(null, {noScroll: true});
  }
  box.addEventListener('wheel', ev => {
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
  ]) box.addEventListener(type, ev => { ev.preventDefault(); fn(ev); }, {passive: false});

  let scrollRaf = 0;
  box.addEventListener('scroll', () => { if (!scrollRaf) scrollRaf = requestAnimationFrame(() => { scrollRaf = 0; onScroll(); }); });
  let resizeTimer = 0;
  const relayout = () => { if (V.zoom === 'fit') layout(); else if (o.onSize) o.onSize(); };
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(relayout, 150); });

  return Object.assign(V, {
    load, layout, relayout, setZoom, zoomBy, setDark, goPage, goBack, goForward, navKey, goDest, renderNow, viewPoint, pointOf, pageOf,
    showBoxes, rangeOf, offsetOf, pinching: () => P.active, canGoBack: () => BACK.length > 0, canGoForward: () => FWD.length > 0,
  });
};
