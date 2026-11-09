const $ = id => document.getElementById(id);
const FILE = new URLSearchParams(location.search).get('f') || '';

// Vivid dark palettes: one hue each, with saturated accents and backgrounds tinted by the same hue.
// One is picked per file (hash of its path), so each document keeps its own color cue.
const HUES = [0, 12, 24, 36, 48, 62, 80, 100, 125, 150, 165, 180, 192, 205, 218, 230, 245, 258, 270, 285, 300, 315, 330, 345];
const PALETTES = HUES.map(h => {
  const c = (s, l) => `hsl(${h} ${s}% ${l}%)`;
  return {bg:c(10, 10), panel:c(0, 15), border:c(50, 30), code:c(46, 9.5), claude:c(40, 18),
          fg:c(25, 91), muted:c(18, 66), accent:c(90, 68), hl:c(48, 40), hl2:c(55, 42)};
});
(() => {
  let h = 0;
  for (const c of FILE) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const pal = PALETTES[h % PALETTES.length];
  for (const k in pal) document.documentElement.style.setProperty('--' + k, pal[k]);
})();
// Watermark: figures from art/figures/ (manifest.json) down the left and right columns, drawn anew on every load as
// binary drawings (f.line, or the silhouette f.mask) in the file's accent color. Each side gets a stack of single
// organisms cut from the Kunstformen plates (the old cut-out figures when the manifest has none). Each
// figure has one signed score, kept on the server: +1 per click, -1 per double click. Its weight is exp(0.5 * score),
// the score limited to +-6, so liked figures come up more often and disliked ones rarely but not never.
const ART = '/art/figures/', WM_STACK = 10, WM_MAXH = 300;  // WM_MAXH: the tallest a figure is drawn, in px
(async () => {
  try {
    const get = async url => (await fetch(url)).json();
    const [man, scores] = await Promise.all([get(ART + 'manifest.json'), get('/api/art/scores').catch(() => ({}))]);
    const organisms = man.filter(f => f.kind === 'organism');
    const figs = organisms.length ? organisms : man.filter(f => f.kind === 'figure');
    const used = new Set();
    const draw = (list, n) => {  // weighted draw without replacement
      list = list.filter(f => !used.has(f.id));
      const out = [];
      while (out.length < n && list.length) {
        const w = list.map(f => Math.exp(0.5 * Math.max(-6, Math.min(6, scores[f.id] || 0))));
        let x = Math.random() * w.reduce((a, b) => a + b, 0), i = 0;
        while (i < list.length - 1 && (x -= w[i]) >= 0) i++;
        used.add(list[i].id);
        out.push({f: list[i], s: scores[list[i].id] || 0});
        list.splice(i, 1);
      }
      return out;
    };
    wmInit([draw(figs, WM_STACK), draw(figs, WM_STACK)]);
  } catch (e) { /* no art folder: no watermark */ }
})();

function wmInit(sides) {
  const pool = sides.flat();
  const root = document.documentElement, tip = document.createElement('div');
  tip.id = 'wmvote';
  document.body.appendChild(tip);
  for (const it of pool) {
    it.el = document.createElement('div');
    it.el.className = 'wmfig';
    it.dx = 0.1 + 0.8 * Math.random();  // where it sits across the column
    const url = `url(${ART}${it.f.line || it.f.mask})`;
    it.el.style.setProperty('-webkit-mask-image', url);
    it.el.style.setProperty('mask-image', url);
    document.body.appendChild(it.el);
  }
  // Stack each side's figures from the top of its column, as many as fit.
  const layout = () => {
    const W = root.clientWidth, H = root.clientHeight;
    const cols = [{x: 16, w: W > 1180 ? 220 : 180}, {x: W - 16 - 300, w: 300}];  // the grid columns of main
    pool.forEach(it => it.el.hidden = true);
    if (W <= 860) return;  // one column: no room beside the text
    sides.forEach((items, c) => {
      let y = 66;
      for (const it of items) {
        const k = Math.min((cols[c].w - 8) / it.f.w, WM_MAXH / it.f.h), w = it.f.w * k, h = it.f.h * k;
        if (y + h > H - 8) break;
        Object.assign(it.el.style, {left: cols[c].x + (cols[c].w - w) * it.dx + 'px',
          top: y + 'px', width: w + 'px', height: h + 'px'});
        it.el.hidden = false;
        y += h + 28;
      }
    });
  };
  layout();
  addEventListener('resize', layout);

  // The figures sit behind the page, so they are hit-tested here: a click on the empty background over one is a
  // +1, a double click -1. Hovering shows the figure's score in a small box by the cursor.
  const hit = ev => {
    const t = ev.target;
    if (!(t === root || t === document.body || ['MAIN', 'ASIDE', 'NAV'].includes(t.tagName))) return null;
    return pool.find(it => {
      if (it.el.hidden) return false;
      const r = it.el.getBoundingClientRect();
      return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    });
  };
  const signed = s => (s > 0 ? '+' : '') + s;
  const vote = async (it, v) => {
    try {
      const r = await fetch('/api/art/vote', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MDView': '1'},
        body: JSON.stringify({id: it.f.id, vote: v})});
      if (!r.ok) throw new Error((await r.json()).error || r.statusText);
      it.s = (await r.json()).score;
      tip.textContent = signed(it.s);
      tip.classList.add('flash');
      setTimeout(() => tip.classList.remove('flash'), 1200);
    } catch (e) { toast(e.message); }
  };
  let timer = 0;
  document.addEventListener('mousemove', ev => {
    const it = hit(ev);
    document.body.classList.toggle('wm-hover', !!it);
    if (it) tip.textContent = signed(it.s);
    tip.style.left = Math.min(ev.clientX + 14, root.clientWidth - 60) + 'px';
    tip.style.top = Math.min(ev.clientY + 14, root.clientHeight - 30) + 'px';
  });
  document.addEventListener('click', ev => {
    const it = ev.button === 0 && hit(ev);
    if (!it) return;
    if (ev.detail === 1) timer = setTimeout(() => vote(it, 1), 260);  // wait: it may become a double click
    else if (ev.detail === 2) { clearTimeout(timer); vote(it, -1); }
  });
}
const docEl = $('doc'), threadsEl = $('threads'), fab = $('fab'), dlg = $('dlg'), ta = $('ta');
const INLINE = new Set(['SPAN','A','EM','STRONG','CODE','SUP','SUB','DEL','S','U','MARK','Q','SMALL','ABBR','B','I','KBD','INS']);
const BLOCK_SEL = 'p,li,h1,h2,h3,h4,h5,h6,td,th,dt,dd,figcaption,caption';

let state = null;        // last payload from /api/doc
let index = [];          // [{node, offs}] text nodes in #doc with their offsets in state.clean
let lineStarts = [];
let editing = false;     // a textarea is open: postpone live reloads
let pendingReload = false;
let pendingAdd = null;   // {s, e, quote} while the dialog is open
let draft = '';          // comment text kept after a failed save

// ------------------------------------------------------------ server
async function getDoc() {
  const r = await fetch('/api/doc?f=' + encodeURIComponent(FILE));
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}

async function load(quiet) {
  try { apply(await getDoc()); setLive(true); }
  catch (e) {
    if (!state) docEl.innerHTML = '<p class="error"></p>', docEl.firstChild.textContent = e.message;
    else if (!quiet) toast(e.message);
  }
}

async function op(body) {
  let r;
  try {
    r = await fetch('/api/op', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MDView': '1'},
      body: JSON.stringify({...body, f: FILE, version: state.version})});
  } catch { setLive(false); toast('Could not reach mdview. Is it still running?'); return false; }
  const d = await r.json();
  if (r.status === 409) { await load(); toast(d.error); return false; }
  if (!r.ok) { toast(d.error || r.statusText); return false; }
  apply(d);
  return true;
}

function setLive(on) {
  $('live').classList.toggle('off', !on);
  $('liveText').textContent = on ? 'Live' : 'Disconnected';
}

setInterval(async () => {
  if (document.hidden || !state) return;
  try {
    const r = await fetch('/api/version?f=' + encodeURIComponent(FILE));
    const d = await r.json();
    setLive(r.ok);
    if (r.ok && d.version && d.version !== state.version) {
      if (editing || dlg.open) pendingReload = true;
      else { await load(true); toast('Updated from file'); }
    }
  } catch { setLive(false); }
}, 1500);

function flushReload() {
  if (pendingReload && !editing && !dlg.open) { pendingReload = false; load(true); }
}

// ------------------------------------------------------------ render
function apply(d) {
  const y = scrollY;
  state = d;
  kit.docTitle('md', d.label);
  kit.crumbs($('name'), d.label, msg => toast(msg));
  $('home').href = '/?d=' + encodeURIComponent(d.dir);
  $('home').hidden = !d.dirOk; // the folder wasn't passed to mdview
  renderDoc();
  renderThreads();
  typesetMath();
  scrollTo(0, y);
  tocState();
}

function renderDoc() {
  docEl.innerHTML = state.html;
  if (state.title) {
    const h = document.createElement('h1');
    h.className = 'doc-title'; h.textContent = state.title;
    docEl.prepend(h);
  }
  fixLinks();
  lineStarts = [0];
  for (let i = 0; i < state.clean.length; i++) if (state.clean[i] === '\n') lineStarts.push(i + 1);
  buildIndex();
  paintSuggestions();
  buildIndex();
  paintMarks();
  paintMathMarks();
  buildIndex();
  paintPins();
  paintFolds();
  paintDeletes();
  buildIndex();
  buildToc();
}

// Where a relative link (in the document or a comment) goes in mdview: Markdown to the viewer,
// text files (LaTeX, code) to the read-only text view, where #L12 or #L12-L20 highlights lines,
// and anything else to /files/ (images, media, PDFs, keeping #page=N). Null for web, absolute
// and in-page links, which stay as they are.
const isRel = u => u && !/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(u);
const fileUrl = f => '/files/' + f.split('/').map(encodeURIComponent).join('/');
function linkTarget(u) {
  if (!isRel(u)) return null;
  const dir = FILE.includes('/') ? FILE.slice(0, FILE.lastIndexOf('/') + 1) : '';
  const i = u.indexOf('#'), path = i < 0 ? u : u.slice(0, i), hash = i < 0 ? '' : u.slice(i + 1);
  let file;
  try { file = decodeURIComponent(new URL(path, 'http://x/' + dir).pathname.slice(1)); } catch { return null; }
  const ext = (file.match(/(\.[^./]+)$/) || ['', ''])[1].toLowerCase();
  if (ext === '.md' || ext === '.markdown')
    return {href: '/view?f=' + encodeURIComponent(file) + (hash ? '#' + hash : '')};
  if ((state?.textTypes || []).includes(ext)) {
    const m = hash.match(/^L(\d+)(?:-L?(\d+))?$/);
    return {href: '/text?f=' + encodeURIComponent(file) + (m ? `&lines=${m[1]}-${m[2] || m[1]}#L${m[1]}` : ''), tab: true};
  }
  return {href: fileUrl(file) + (hash ? '#' + hash : ''), tab: true};
}
function setLink(a, u) {
  const t = linkTarget(u);
  if (t) a.href = t.href;
  else if (/^(https?|mailto):/i.test(u) || u.startsWith('#')) a.href = u;
  else return false;  // other schemes (javascript:, file:, ...) stay text
  if (t ? t.tab : /^https?:/i.test(u)) { a.target = '_blank'; a.rel = 'noopener'; }
  return true;
}

function fixLinks() {
  docEl.querySelectorAll('img[src]').forEach(img => {
    const t = linkTarget(img.getAttribute('src'));
    if (t) img.src = t.href.split('#')[0];
  });
  docEl.querySelectorAll('a[href]').forEach(a => {
    const t = linkTarget(a.getAttribute('href'));
    if (!t) return;
    a.href = t.href;
    if (t.tab) { a.target = '_blank'; a.rel = 'noopener'; }
  });
}

let mathQueue = Promise.resolve();
function typesetMath() {
  const roots = [docEl, $('tocList'), threadsEl, $('chatMsgs')].filter(r => r.querySelector('.math'));
  if (!roots.length) return;
  if (window.MathJax && MathJax.typesetPromise) {  // one typeset at a time, as MathJax asks
    mathQueue = mathQueue.then(() => { kit.mathPrune(); return MathJax.typesetPromise(roots); }).catch(() => {});  // mathPrune: no old maths kept in memory
    return;
  }
  if (window.MathJax) return; // still loading; startup will typeset
  // A local copy (vendor/mathjax), so nothing is loaded from the internet. The menu is off
  // because its accessibility parts aren't included in that copy.
  window.MathJax = {tex: {inlineMath: [['\\(', '\\)']], displayMath: [['\\[', '\\]']]},
    options: {enableMenu: false}};
  const s = document.createElement('script');
  s.src = '/vendor/mathjax/tex-chtml.js';
  s.async = true;
  document.head.appendChild(s);
}

// ------------------------------------------------------------ source mapping
// pandoc's sourcepos gives every word a data-pos="line:col-line:col" in the clean
// text. Columns count code points, with tabs expanded to multiples of 4.
function posToOffset(line, col) {
  const clean = state.clean;
  let i = lineStarts[line - 1];
  if (i === undefined) return clean.length;
  let vcol = 1;
  while (vcol < col && i < clean.length && clean[i] !== '\n') {
    vcol = clean[i] === '\t' ? vcol + 4 - ((vcol - 1) % 4) : vcol + 1;
    i += clean.codePointAt(i) > 0xffff ? 2 : 1;
  }
  return i;
}

function parsePos(str) {
  if (!str) return null;
  const segs = str.slice(str.lastIndexOf('@') + 1).split(';');
  const a = segs[0].split('-')[0].split(':').map(Number);
  const b = segs[segs.length - 1].split('-')[1].split(':').map(Number);
  if (a.some(isNaN) || b.some(isNaN)) return null;
  return [posToOffset(a[0], a[1]), posToOffset(b[0], b[1])];
}

// Nearest ancestor that carries a position. 'gap' = text between words (soft breaks).
function posEl(node) {
  for (let el = node.parentElement; el && el !== docEl; el = el.parentElement) {
    if (el.tagName === 'PRE' || el.classList.contains('math') || el.tagName === 'MJX-CONTAINER') return null;
    if (el.dataset.pos) return INLINE.has(el.tagName) ? el : 'gap';
  }
  return null;
}

function align(T, S, base) {
  const o = new Array(T.length);
  if (T.length === S.length) { for (let i = 0; i < T.length; i++) o[i] = base + i; return o; }
  const k = S.indexOf(T);
  if (k >= 0 && S.indexOf(T, k + 1) < 0) { for (let i = 0; i < T.length; i++) o[i] = base + k + i; return o; }
  let j = 0, ok = true;
  for (let i = 0; i < T.length && ok; i++) {
    const jj = S.indexOf(T[i], j);
    if (jj < 0) ok = false; else { o[i] = base + jj; j = jj + 1; }
  }
  if (ok) return o;
  for (let i = 0; i < T.length; i++) o[i] = base + Math.floor(i * S.length / T.length);
  return o;
}

function textNodes(root) {
  const out = [], w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) out.push(n);
  return out;
}

function mapElement(el) {
  const r = parsePos(el.dataset.pos);
  if (!r) return null;
  const nodes = textNodes(el), T = nodes.map(n => n.data).join('');
  const offs = align(T, state.clean.slice(r[0], r[1]), r[0]), m = new Map();
  let k = 0;
  for (const n of nodes) { m.set(n, offs.slice(k, k + n.length)); k += n.length; }
  return m;
}

function buildIndex() {
  index = [];
  const cache = new Map();
  let prevEnd = null;
  for (const n of textNodes(docEl)) {
    if (!n.length) continue;
    const el = posEl(n);
    let offs = null;
    if (el === 'gap') {
      if (prevEnd !== null && !n.data.trim()) offs = Array.from(n.data, (_, i) => prevEnd + i);
    } else if (el) {
      if (!cache.has(el)) cache.set(el, mapElement(el));
      offs = cache.get(el)?.get(n) || null;
    }
    if (!offs) continue;
    index.push({node: n, offs});
    prevEnd = offs[offs.length - 1] + 1;
  }
}

function lowerBound(offs, x) {
  let lo = 0, hi = offs.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (offs[mid] < x) lo = mid + 1; else hi = mid; }
  return lo;
}

// ------------------------------------------------------------ highlights
function paintMarks() {
  const ranged = state.threads.filter(t => t.e > t.s);
  for (const {node, offs} of index) {
    const segs = [];
    for (const t of ranged) {
      const a = lowerBound(offs, t.s), b = lowerBound(offs, t.e);
      if (a < b) segs.push([a, b, t]);
    }
    segs.sort((x, y) => y[0] - x[0]);
    for (const [a, b, t] of segs) {
      if (b < node.length) node.splitText(b);
      const mid = a > 0 ? node.splitText(a) : node;
      const m = document.createElement('mark');
      m.className = 'c ' + t.status;
      m.dataset.t = t.id;
      mid.parentNode.insertBefore(m, mid);
      m.appendChild(mid);
    }
  }
}

// Maths isn't in the text index: highlight a whole formula when it lies inside a comment's range.
function paintMathMarks() {
  const ranged = state.threads.filter(t => t.e > t.s);
  docEl.querySelectorAll('.math').forEach(m => {
    const r = parsePos(m.parentElement?.dataset.pos);
    const t = r && ranged.find(t => r[0] >= t.s && r[1] <= t.e);
    if (t) { m.classList.add('cm', t.status); m.dataset.t = t.id; }
  });
}

function paintPins() {
  for (const t of state.threads.filter(t => t.e === t.s && !t.general)) {
    const pin = document.createElement('button');
    pin.className = 'pin'; pin.dataset.t = t.id; pin.setAttribute('aria-label', 'Comment');
    const hit = index.find(x => x.offs[x.offs.length - 1] >= t.s);
    if (!hit) { (docEl.lastElementChild || docEl).appendChild(pin); continue; }
    const i = lowerBound(hit.offs, t.s);
    const at = i > 0 ? hit.node.splitText(i) : hit.node;
    at.parentNode.insertBefore(pin, at);
  }
}

// ------------------------------------------------------------ suggested changes
// {~~old~>new~~}, {++new++}, {--old--}: the clean text holds both sides, shown as <del>/<ins>.
function paintSuggestions() {
  const parts = [];
  for (const g of state.suggestions) {
    if (g.de > g.ds) parts.push([g.ds, g.de, 'del', g]);
    if (g.ie > g.is) parts.push([g.is, g.ie, 'ins', g]);
  }
  for (const {node, offs} of index) {
    const segs = [];
    for (const [s, e, tag, g] of parts) {
      const a = lowerBound(offs, s), b = lowerBound(offs, e);
      if (a < b) segs.push([a, b, tag, g]);
    }
    segs.sort((x, y) => y[0] - x[0]);
    for (const [a, b, tag, g] of segs) {
      if (b < node.length) node.splitText(b);
      const mid = a > 0 ? node.splitText(a) : node;
      const w = document.createElement(tag);
      w.className = 'sg'; w.dataset.g = g.id;
      mid.parentNode.insertBefore(w, mid);
      w.appendChild(mid);
    }
  }
  // Maths isn't in the text index: mark whole formulas by their position.
  docEl.querySelectorAll('.math').forEach(m => {
    const r = parsePos(m.parentElement?.dataset.pos);
    const hit = r && parts.find(([s, e]) => r[0] >= s && r[1] <= e);
    if (hit) { m.classList.add('sg-' + hit[2]); m.dataset.g = hit[3].id; }
  });
}
function suggOf(id) { return docEl.querySelectorAll(`.sg[data-g="${id}"], .math[data-g="${id}"]`); }
function suggCard(id) { return threadsEl.querySelector(`.thread.sugg[data-g="${id}"]`); }

const MARK_SEL = 'mark.c, button.pin, .math.cm';
function marksOf(id) { return docEl.querySelectorAll(`mark.c[data-t="${id}"], button.pin[data-t="${id}"], .math.cm[data-t="${id}"]`); }
function cardOf(id) { return threadsEl.querySelector(`.thread[data-t="${id}"]`); }

function flash(els) {
  els.forEach(e => e.classList.add('flash'));
  setTimeout(() => els.forEach(e => e.classList.remove('flash')), 900);
}

docEl.addEventListener('click', e => {
  const g = e.target.closest('.sg, .math[data-g]');
  if (g && getSelection().isCollapsed && !e.target.closest(MARK_SEL)) {
    setSidebar(true);
    const card = suggCard(g.dataset.g);
    if (card) { card.scrollIntoView({behavior: 'smooth', block: 'nearest'}); flash([card]); }
    return;
  }
  const m = e.target.closest(MARK_SEL);
  if (!m && !e.target.closest('a, button')) {  // a click on a formula selects it, so it can be commented on
    const f = e.target.closest('.math');
    if (f && !f.dataset.g && getSelection().isCollapsed) { getSelection().selectAllChildren(f); return; }
  }
  if (!m || !getSelection().isCollapsed) return;
  setSidebar(true);
  const card = cardOf(m.dataset.t);
  if (card) { card.scrollIntoView({behavior: 'smooth', block: 'nearest'}); flash([card]); }
});
docEl.addEventListener('mouseover', e => hover(e.target.closest(MARK_SEL), true));
docEl.addEventListener('mouseout', e => hover(e.target.closest(MARK_SEL), false));
function hover(m, on) {
  if (!m) return;
  marksOf(m.dataset.t).forEach(x => x.classList.toggle('hover', on));
  cardOf(m.dataset.t)?.classList.toggle('hover', on);
}

// ------------------------------------------------------------ folding sections
// Each heading gets a ▸/▾ toggle in the margin. Folding hides everything up to the next heading
// of the same or a higher level; subsections keep their own state. Only the view changes, never
// the file. Folded headings are remembered per file, by heading id.
const FOLD_KEY = 'mdview.fold:' + FILE;
let folded = new Set((() => { try { return JSON.parse(localStorage.getItem(FOLD_KEY)) || []; } catch { return []; } })());
const levelOf = e => /^H[1-6]$/.test(e.tagName) && e.id && !e.classList.contains('doc-title') ? +e.tagName[1] : 0;
const headings = () => [...docEl.children].filter(levelOf);

// An x beside each top-level block deletes it from the file (undo is in the toast).
// A table lists several position segments; the block spans from the first start to the last end.
function blockRange(el) {
  const segs = el.dataset.pos.slice(el.dataset.pos.lastIndexOf('@') + 1).split(';');
  let s = Infinity, e = -Infinity;
  for (const seg of segs) {
    const [a, b] = seg.split('-').map(x => x.split(':').map(Number));
    if (!b || a.some(isNaN) || b.some(isNaN)) return null;
    s = Math.min(s, posToOffset(a[0], a[1]));
    e = Math.max(e, posToOffset(b[0], b[1]));
  }
  return s < e ? [s, e] : null;
}

async function blockSource(s, e) {
  let r;
  try {
    r = await fetch('/api/op', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MDView': '1'},
      body: JSON.stringify({op: 'block-source', s, e, f: FILE, version: state.version})});
  } catch { setLive(false); toast('Could not reach mdview. Is it still running?'); return null; }
  const d = await r.json();
  if (r.status === 409) { await load(); toast(d.error); return null; }
  if (!r.ok) { toast(d.error || r.statusText); return null; }
  return d.text;
}

function editBlock(el) {
  if (editing) return;
  const [s, e] = blockRange(el);
  blockSource(s, e).then(text => {
    if (text === null || editing) return;
    const {wrap, area} = inlineEditor(el, text, 'Save', v => op({op: 'replace-block', s, e, text: v})
      .then(ok => { if (ok) undoToast('Block edited'); return ok; }), true);
    wrap.className = 'block-edit';
    area.rows = Math.min(30, Math.max(3, text.split('\n').length + 1));
  });
}

// Write a new block at `where` ('before' or 'after' the block `el`, or the 'end' of the file).
function addBlock(el, where) {
  if (editing) return;
  const r = el && blockRange(el);
  const spot = document.createElement('div');
  if (el) el.after(spot); else docEl.append(spot);
  const {wrap, area} = inlineEditor(spot, '', 'Add', v => op({op: 'insert-block', where, s: r?.[0], e: r?.[1], text: v})
    .then(ok => { if (ok) undoToast('Text added'); return ok; }), true, t => t.remove());
  if (where === 'before') el.before(wrap);
  wrap.className = 'block-edit';
  area.rows = 4;
  area.placeholder = 'Write Markdown…';
}

// Double-click a block to edit it (not on a link, a comment highlight, a button or a suggestion).
docEl.addEventListener('dblclick', e => {
  if (e.target.closest('a, button, mark.c, .math, .sg, mjx-container')) return;
  let el = e.target;
  while (el && el.parentElement !== docEl) el = el.parentElement;
  if (!el || !el.dataset.pos || !blockRange(el)) return;
  getSelection().removeAllRanges();
  editBlock(el);
});

function paintDeletes() {
  if (!Array.from(docEl.children).some(e => e.dataset.pos && blockRange(e))) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'empty-add'; b.textContent = '+ Add text';
    b.onclick = () => addBlock(null, 'end');
    docEl.append(b);
    return;
  }
  let first = true;
  for (let el of Array.from(docEl.children)) {
    if (!el.dataset.pos || !blockRange(el)) continue;
    if (el.tagName === 'PRE') {  // a pre clips its children: put the buttons on a wrapper
      const w = document.createElement('div');
      w.dataset.pos = el.dataset.pos;
      el.replaceWith(w); w.append(el);
      el = w;
    }
    const pen = document.createElement('button');
    pen.className = 'eblk'; pen.type = 'button'; pen.title = 'Edit this block'; pen.setAttribute('aria-label', 'Edit this block');
    pen.onclick = () => editBlock(el);
    el.appendChild(pen);
    const add = (where, cls) => {
      const a = document.createElement('button');
      a.className = 'ablk' + cls; a.type = 'button'; a.title = 'Add text ' + where + ' this block'; a.setAttribute('aria-label', a.title);
      a.onclick = () => addBlock(el, where);
      el.appendChild(a);
    };
    if (first) add('before', ' above');
    add('after', '');
    first = false;
    const b = document.createElement('button');
    b.className = 'xblk'; b.type = 'button'; b.title = 'Delete this block'; b.setAttribute('aria-label', 'Delete this block');
    b.onclick = () => {
      const [s, e] = blockRange(el);
      op({op: 'delete-block', s, e}).then(ok => ok && undoToast('Block deleted'));
    };
    el.appendChild(b);
  }
}

function paintFolds() {
  for (const h of headings()) {
    const b = document.createElement('button');
    b.className = 'fold'; b.type = 'button';
    b.title = 'Fold or unfold this section (Alt-click: its subsections too)';
    b.onclick = e => { e.stopPropagation(); toggleFold(h, e.altKey); };
    h.prepend(b);
  }
  $('foldBtn').hidden = !headings().length;
  applyFolds();
}

function applyFolds() {
  let fold = 0;  // level of the folded heading hiding what follows, 0 = none
  let owner = null, counts = new Map();
  for (const e of docEl.children) {
    const l = levelOf(e);
    if ((l && l <= fold) || e.matches('section.footnotes')) fold = 0;
    e.classList.toggle('folded-away', fold > 0);
    if (fold) {  // count the comments hidden under the folded heading
      e.querySelectorAll('mark.c, button.pin').forEach(m => counts.get(owner).add(m.dataset.t));
      continue;
    }
    if (l) {
      const f = folded.has(e.id);
      e.classList.toggle('folded', f);
      e.removeAttribute('data-n');
      e.classList.remove('has-open');
      if (f) { fold = l; owner = e; counts.set(e, new Set()); }
    }
  }
  for (const [h, ids] of counts) {
    if (!ids.size) continue;
    const open = state.threads.filter(t => ids.has(String(t.id)) && t.status === 'open').length;
    h.dataset.n = open ? `${open} open` : `${ids.size} answered`;
    h.classList.toggle('has-open', open > 0);
  }
  $('foldBtn').textContent = folded.size ? 'Unfold all' : 'Fold all';
  tocState();
}

function saveFolds() {
  const ids = new Set(headings().map(h => h.id));
  folded = new Set([...folded].filter(id => ids.has(id)));  // forget headings that are gone
  try { localStorage.setItem(FOLD_KEY, JSON.stringify([...folded])); } catch {}
  applyFolds();
}

function toggleFold(h, deep) {
  const fold = !folded.has(h.id);
  const hs = [h];
  if (deep)  // Alt-click: the same for every subsection
    for (let e = h.nextElementSibling; e && !(levelOf(e) && levelOf(e) <= levelOf(h)); e = e.nextElementSibling)
      if (levelOf(e)) hs.push(e);
  hs.forEach(x => fold ? folded.add(x.id) : folded.delete(x.id));
  saveFolds();
}

// Unfold whatever hides `node`, e.g. before scrolling to a comment.
function reveal(node) {
  let e = node;
  while (e && e.parentElement !== docEl) e = e.parentElement;
  if (!e || !e.classList.contains('folded-away')) return;
  let min = levelOf(e) || 7;
  for (let p = e.previousElementSibling; p && min > 1; p = p.previousElementSibling) {
    const l = levelOf(p);
    if (l && l < min) { folded.delete(p.id); min = l; }
  }
  saveFolds();
}

$('foldBtn').onclick = () => {
  // Fold all leaves a lone top heading (the usual single # title) open, so the outline shows.
  const hs = headings(), top = Math.min(...hs.map(levelOf));
  const lone = hs.filter(h => levelOf(h) === top).length === 1;
  folded = folded.size ? new Set() : new Set(hs.filter(h => !(lone && levelOf(h) === top)).map(h => h.id));
  saveFolds();
};
docEl.addEventListener('click', e => {  // links within the page, e.g. a table of contents
  const a = e.target.closest('a[href^="#"]');
  const t = a && document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1)));
  if (t && docEl.contains(t)) reveal(t);
});

// ------------------------------------------------------------ contents
// A list of the headings on the left. Clicking one unfolds it if needed and scrolls to it; the
// section being read is highlighted. Each entry shows the open comments in its own text.
const tocLinks = new Map();  // heading id -> link

function buildToc() {
  const hs = headings(), list = $('tocList');
  tocLinks.clear();
  list.replaceChildren();
  $('toc').hidden = $('tocBtn').hidden = hs.length < 2;
  document.body.classList.toggle('hastoc', hs.length >= 2);
  if (hs.length < 2) return;
  const top = Math.min(...hs.map(levelOf));
  const openIds = new Set(state.threads.filter(t => t.status === 'open').map(t => String(t.id)));
  for (const h of hs) {
    const a = el('a', levelOf(h) === top ? 'top' : '');
    a.href = '#' + h.id;
    a.style.paddingLeft = 6 + (levelOf(h) - top) * 12 + 'px';
    const t = el('span', 't'), c = h.cloneNode(true);  // keep inline formatting and maths
    c.querySelectorAll('button').forEach(b => b.remove());
    c.querySelectorAll('mark, a').forEach(m => m.replaceWith(...m.childNodes));
    t.append(...c.childNodes);
    a.append(t);
    const ids = new Set();  // open comments between this heading and the next one
    for (let e = h; e && !(e !== h && levelOf(e)); e = e.nextElementSibling)
      e.querySelectorAll('mark.c, button.pin').forEach(m => openIds.has(m.dataset.t) && ids.add(m.dataset.t));
    if (ids.size) { const n = el('span', 'n', String(ids.size)); n.title = `${ids.size} open comment${ids.size > 1 ? 's' : ''}`; a.append(n); }
    a.onclick = e => {
      e.preventDefault();
      reveal(h);
      h.scrollIntoView({block: 'start'});
      history.replaceState(null, '', '#' + h.id);
    };
    tocLinks.set(h.id, a);
    list.append(a);
  }
  tocState();
}

// Dim entries hidden in a folded section, and highlight the section being read.
function tocState() {
  if (!tocLinks.size) return;
  let cur = null;
  for (const h of headings()) {
    const away = h.classList.contains('folded-away');
    tocLinks.get(h.id)?.classList.toggle('away', away);
    if (!away && h.getBoundingClientRect().top <= 90) cur = h;
  }
  for (const [id, a] of tocLinks) a.classList.toggle('active', !!cur && id === cur.id);
  const a = cur && tocLinks.get(cur.id), nav = $('toc');
  if (a && (a.offsetTop < nav.scrollTop || a.offsetTop + a.offsetHeight > nav.scrollTop + nav.clientHeight))
    nav.scrollTop = a.offsetTop - nav.clientHeight / 3;
}
let spyFrame;
addEventListener('scroll', () => { cancelAnimationFrame(spyFrame); spyFrame = requestAnimationFrame(tocState); }, {passive: true});

let toc = true;
function setToc(on) {
  toc = on;
  document.body.classList.toggle('notoc', !on);
  $('tocBtn').setAttribute('aria-pressed', String(on));
  $('tocBtn').title = (on ? 'Hide' : 'Show') + ' the contents (⌘/Ctrl + Shift + \\)';
  $('tocHide').title = 'Hide the contents (⌘/Ctrl + Shift + \\)';
  $('tocShow').title = 'Show the contents (⌘/Ctrl + Shift + \\)';
  try { localStorage.setItem('mdview.toc', on ? 'on' : 'off'); } catch {}
}
$('tocBtn').onclick = () => setToc(!toc);
$('tocHide').onclick = () => setToc(false);
$('tocShow').onclick = () => setToc(true);
setToc((() => { try { return localStorage.getItem('mdview.toc'); } catch { return null; } })() !== 'off');

// ------------------------------------------------------------ sidebar
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function btn(label, fn, cls) {
  const b = el('button', cls, label);
  b.type = 'button';
  b.onclick = e => { e.stopPropagation(); fn(); };
  return b;
}
// Comment text with its maths marked for MathJax: $$…$$ and \[…\] (display), \(…\) and $…$ (inline).
// As in pandoc, $…$ needs no space just inside the dollars and no digit right after the closing
// one, so "costs $5 and $10" stays text; \$ is a literal dollar.
const MATH_RE = /(?<!\\)\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$(?![\s$])((?:\\.|[^$\\])+?)(?<![\s\\])\$(?!\d)/g;
function mathSpan(m) {
  const display = m[1] != null || m[2] != null, tex = m[1] ?? m[2] ?? m[3] ?? m[4];
  return el('span', 'math ' + (display ? 'display' : 'inline'), display ? `\\[${tex}\\]` : `\\(${tex}\\)`);
}

// Comment text with a little Markdown: `code`, maths, [links](url) or [links](<url with spaces>),
// **bold** and *italic*. Whichever starts first wins, so maths and code are never reformatted.
const INLINE_MD = [
  ['code', /`([^`\n]+)`/g],
  ['math', MATH_RE],
  ['link', /\[((?:[^[\]\n]|\[[^\]\n]*\])+)\]\((<[^<>\n]*>|[^()\s]+)\)/g],
  ['strong', /\*\*(?!\s)(.+?)(?<!\s)\*\*/g],
  ['em', /(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])/g],
];
function withMath(text, inLink) {
  const frag = document.createDocumentFragment();
  let pos = 0;
  for (;;) {
    let best = null;
    for (const [kind, re] of INLINE_MD) {
      if (inLink && kind === 'link') continue;
      re.lastIndex = pos;
      const m = re.exec(text);
      if (m && (!best || m.index < best.m.index)) best = {kind, m};
    }
    if (!best) break;
    const {kind, m} = best;
    frag.append(plain(text.slice(pos, m.index)));
    if (kind === 'code') frag.append(el('code', null, m[1]));
    else if (kind === 'math') frag.append(mathSpan(m));
    else if (kind === 'link') {
      const a = el('a');
      a.append(withMath(m[1], true));
      if (setLink(a, m[2].replace(/^<|>$/g, ''))) { a.onclick = e => e.stopPropagation(); frag.append(a); }
      else frag.append(m[0]);
    } else { const e = el(kind); e.append(withMath(m[1], inLink)); frag.append(e); }
    pos = m.index + m[0].length;
  }
  frag.append(plain(text.slice(pos)));
  return frag;
}
const plain = t => t.replace(/\\\$/g, '$');

function who(m) {
  if (!m.author) return 'You';
  return m.author.toLowerCase() === 'claude' ? 'Claude' : m.author;
}

// A comment that starts with this tag asks Claude to answer in a reply and change nothing (the answer-comments skill).
const ASK_TAG = '[ask only]';
const askSplit = t => t.startsWith(ASK_TAG) ? [true, t.slice(ASK_TAG.length).trim()] : [false, t];
const askJoin = (ask, t) => ask ? `${ASK_TAG} ${t}` : t;

// A ⋯ button that opens a small menu of [label, action, class] items (null = divider).
// The menu is fixed to the window, so the sidebar's scroll area doesn't clip it.
let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.menu.remove(); openMenu.dots.classList.remove('open');
  document.removeEventListener('click', closeMenu);
  document.removeEventListener('keydown', menuKey);
  removeEventListener('scroll', closeMenu, true);
  removeEventListener('resize', closeMenu);
  openMenu = null;
}
function menuKey(e) { if (e.key === 'Escape') closeMenu(); }

function actionsMenu(items) {
  const dots = el('button', 'dots', '⋯');
  dots.type = 'button'; dots.title = 'Actions'; dots.setAttribute('aria-label', 'Actions');
  dots.onclick = e => {
    e.stopPropagation();
    const was = openMenu?.dots === dots;
    closeMenu();
    if (was) return;
    const menu = el('div', 'menu');
    for (const it of items) {
      if (!it) { menu.append(el('hr')); continue; }
      menu.append(btn(it[0], () => { closeMenu(); it[1](); }, it[2]));
    }
    document.body.append(menu);
    const r = dots.getBoundingClientRect(), h = menu.offsetHeight;
    menu.style.right = (innerWidth - r.right) + 'px';
    menu.style.top = (r.bottom + h + 8 > innerHeight ? r.top - h - 2 : r.bottom + 2) + 'px';
    dots.classList.add('open');
    openMenu = {menu, dots};
    document.addEventListener('click', closeMenu);
    document.addEventListener('keydown', menuKey);
    addEventListener('scroll', closeMenu, true);
    addEventListener('resize', closeMenu);
  };
  return dots;
}

function renderThreads() {
  editing = false;
  const ts = state.threads;
  const nOpen = ts.filter(t => t.status === 'open').length, gs = state.suggestions;
  kit.tab({app: 'md', open: nOpen});
  $('counts').textContent = [ts.length && `${nOpen} open · ${ts.length - nOpen} answered`,
    gs.length && `${gs.length} suggested change${gs.length > 1 ? 's' : ''}`].filter(Boolean).join(' · ');
  paintClaude();
  threadsEl.replaceChildren();
  if (gs.length) renderSuggestions(gs);
  if (!ts.length && !gs.length) {
    threadsEl.append(el('p', 'empty', 'Select text in the document to comment on it, or add a general comment.'));
    return;
  }
  if (ts.length) {
    const row = el('div', 'resall');
    row.append(btn(`Resolve all (${ts.length})`, () => {
      if (confirm(`Resolve all ${ts.length} comment${ts.length > 1 ? 's' : ''}? They are removed from the file.`))
        op({op: 'resolve-all'}).then(ok => ok && undoToast('All comments resolved and removed from the file'));
    }, 'link'));
    threadsEl.append(row);
  }
  const general = ts.filter(t => t.general), inText = ts.filter(t => !t.general);
  const sections = [gs.length, general.length, inText.length].filter(Boolean).length;
  for (const t of [...general, ...inText]) {
    if (sections > 1 && (t === general[0] || t === inText[0]))
      threadsEl.append(el('h3', null, t.general ? 'General' : 'In the text'));
    const card = el('div', 'thread ' + t.status + (t.general ? ' general' : ''));
    card.dataset.t = t.id;
    const top = el('div', 'top');
    const q = t.quote.replace(/\s+/g, ' ').trim();
    const label = t.general ? '📄 General comment' : '💬 Note in the text';
    const slot = el('div', 'slot');
    const qEl = el('q');
    qEl.append(withMath(q));
    top.append(q ? qEl : el('span', 'noquote', label),
      el('span', 'pill', t.status === 'answered' ? 'Answered' : 'Open'));
    top.onclick = () => {
      const ms = marksOf(t.id);
      if (ms.length) { reveal(ms[0]); ms[0].scrollIntoView({behavior: 'smooth', block: 'center'}); flash([...ms]); }
    };
    card.append(top);
    t.msgs.forEach((m, i) => {
      const box = el('div', 'msg' + ((m.author || '').toLowerCase() === 'claude' ? ' claude' : ''));
      const text = el('span', 'text');
      const [asked, shown] = m.author ? [false, m.text] : askSplit(m.text);
      if (asked) text.append(el('span', 'tag', 'Ask only'));
      text.append(withMath(shown));
      const dots = actionsMenu([
        ['Reply', () => inlineEditor(slot, '', 'Reply', v => op({op: 'reply', id: t.id, text: v}), false, null, true)],
        ['Resolve', () => op({op: 'resolve', id: t.id}).then(ok => ok && undoToast('Comment resolved and removed from the file'))]]);
      const macts = el('span', 'macts');
      macts.append(
        btn('Edit', () => inlineEditor(text, m.text, 'Save', v => op({op: 'edit', id: t.id, msg: i, text: v}), false, null, !m.author)),
        btn('Delete', () => { if (confirm('Delete this message?')) op({op: 'delete', id: t.id, msg: i}).then(ok => ok && undoToast('Message deleted')); }));
      box.append(macts, dots, el('span', 'who', who(m)), text);
      card.append(box);
    });
    card.append(slot);
    card.addEventListener('mouseenter', () => marksOf(t.id).forEach(x => x.classList.add('hover')));
    card.addEventListener('mouseleave', () => marksOf(t.id).forEach(x => x.classList.remove('hover')));
    threadsEl.append(card);
  }
}

function renderSuggestions(gs) {
  const head = el('h3', 'shead', 'Suggested changes');
  const all = el('span', 'tacts2');
  all.append(btn('Accept all', () => op({op: 'accept-all'}).then(ok => ok && undoToast('All changes accepted')), 'link'),
    btn('Reject all', () => op({op: 'reject-all'}).then(ok => ok && undoToast('All changes rejected')), 'link'));
  head.append(all);
  threadsEl.append(head);
  for (const g of gs) {
    const card = el('div', 'thread sugg');
    card.dataset.g = g.id;
    const top = el('div', 'top');
    const label = {replace: 'Replace', insert: 'Insert', delete: 'Delete'}[g.kind];
    const where = g.thread != null && state.threads[g.thread] ? ' · in a comment' : '';
    const tacts = el('span', 'tacts');
    tacts.append(btn('Accept', () => op({op: 'accept', id: g.id}).then(ok => ok && undoToast('Change accepted')), 'link'),
      btn('Reject', () => op({op: 'reject', id: g.id}).then(ok => ok && undoToast('Change rejected')), 'link'));
    top.append(el('span', 'noquote', `✎ ${label}, line ${g.line}${where}`), tacts);
    top.onclick = () => {
      const ms = suggOf(g.id);
      if (ms.length) { reveal(ms[0]); ms[0].scrollIntoView({behavior: 'smooth', block: 'center'}); flash([...ms]); }
    };
    const diff = el('div', 'diff');
    if (g.old) { const d = el('del'); d.append(withMath(g.old)); diff.append(d); }
    if (g.old && g.new) diff.append(' ');
    if (g.new) { const i = el('ins'); i.append(withMath(g.new)); diff.append(i); }
    card.append(top, diff);
    card.addEventListener('mouseenter', () => suggOf(g.id).forEach(x => x.classList.add('hover')));
    card.addEventListener('mouseleave', () => suggOf(g.id).forEach(x => x.classList.remove('hover')));
    threadsEl.append(card);
  }
}

// Replace `target` with a textarea + Cancel/Save until done.
// withAsk: also show the "Ask only" box, which puts ASK_TAG in front of the text.
function inlineEditor(target, value, label, save, raw, onCancel, withAsk) {
  if (editing) return;
  editing = true;
  const wrap = el('div');
  const area = el('textarea');
  const [asked, plain] = withAsk ? askSplit(value) : [false, value];
  area.value = plain;
  const row = el('div', 'acts');
  const done = () => { wrap.replaceWith(target); editing = false; if (onCancel) onCancel(target); flushReload(); };
  row.append(el('span', 'hint', '⌘/Ctrl + Enter'));
  let ask;
  if (withAsk) {
    const lab = el('label', 'askOnly');
    lab.title = 'Claude answers in a reply to this comment. It suggests no change and edits nothing.';
    ask = el('input'); ask.type = 'checkbox'; ask.checked = asked;
    lab.append(ask, 'Ask only');
    row.append(lab);
  }
  row.append(btn('Cancel', done), btn(label, () => submit(), 'primary'));
  async function submit() {
    const v = raw ? area.value.replace(/\s+$/, '') : area.value.trim();
    if (!v.trim()) return done();
    editing = false;
    const ok = await save(askJoin(ask?.checked, v));
    if (!ok && wrap.isConnected) { editing = true; area.focus(); }
  }
  area.onkeydown = e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
    if (e.key === 'Escape') { e.preventDefault(); done(); }
  };
  wrap.append(area, row);
  target.replaceWith(wrap);
  area.focus();
  return {wrap, area};
}

// ------------------------------------------------------------ adding comments
function selectionToSource() {
  const sel = getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const r = sel.getRangeAt(0);
  if (!docEl.contains(r.commonAncestorContainer)) return null;
  const parts = [];
  for (const ent of index) {
    if (!r.intersectsNode(ent.node)) continue;
    const n = ent.node;
    const a = n === r.startContainer ? r.startOffset : 0;
    const b = n === r.endContainer ? r.endOffset : n.length;
    if (a < b) parts.push({ent, a, b});
  }
  const txt = p => p.ent.node.data.slice(p.a, p.b);
  while (parts.length && !txt(parts[0]).trim()) parts.shift();
  while (parts.length && !txt(parts[parts.length - 1]).trim()) parts.pop();
  // Formulas in the selection: whole, by their position (they are not in the text index).
  const maths = [...docEl.querySelectorAll('.math')].filter(m => r.intersectsNode(m))
    .map(m => ({m, r: parsePos(m.parentElement?.dataset.pos)})).filter(x => x.r);
  if (!parts.length && !maths.length) return {error: "That part of the page can't take comments (code blocks aren't supported)."};
  const block = n => n.closest(BLOCK_SEL);
  const blocks = new Set(maths.map(x => block(x.m)));
  let s = Infinity, e = -Infinity;
  if (parts.length) {
    const f = parts[0], l = parts[parts.length - 1];
    const ft = txt(f); f.a += ft.length - ft.trimStart().length;
    const lt = txt(l); l.b -= lt.length - lt.trimEnd().length;
    blocks.add(block(f.ent.node.parentElement)); blocks.add(block(l.ent.node.parentElement));
    s = f.ent.offs[f.a]; e = l.ent.offs[l.b - 1] + 1;
    // Selection starts/ends exactly at a styled word: include its markup (**, [](…), `).
    if (f.a === 0) { const x = edgeOf(f.ent.node, true); if (x != null && x < s) s = x; }
    if (l.b === l.ent.node.length) { const x = edgeOf(l.ent.node, false); if (x != null && x > e) e = x; }
  }
  if (blocks.size > 1)
    return {error: 'A comment has to stay within one paragraph, heading, list item or table cell.'};
  for (const x of maths) { s = Math.min(s, x.r[0]); e = Math.max(e, x.r[1]); }
  if (!(e > s)) return {error: "Couldn't work out where that text is in the file."};
  const clash = state.threads.find(t => !t.general && (t.e > t.s ? (t.s < e && s < t.e) : (s < t.s && t.s < e)));
  if (clash) return {error: 'That text overlaps an existing comment. Reply to it instead.', thread: clash.id};
  return {s, e, quote: maths.length ? state.clean.slice(s, e) : parts.map(txt).join('')};
}

function edgeOf(node, start) {
  let best = null;
  for (let p = node.parentElement; p && p !== docEl && INLINE.has(p.tagName); p = p.parentElement) {
    if (!p.dataset.pos) continue;
    const ns = textNodes(p).filter(n => n.data.trim());
    if (ns[start ? 0 : ns.length - 1] === node) best = p;
  }
  const r = best && parsePos(best.dataset.pos);
  return r ? r[start ? 0 : 1] : null;
}

function startComment() {
  const res = selectionToSource();
  fab.hidden = true;
  if (!res) return;
  if (res.error) {
    toast(res.error);
    if (res.thread != null) { const c = cardOf(res.thread); c?.scrollIntoView({block: 'nearest'}); c && flash([c]); }
    return;
  }
  openDialog(res, res.quote.length > 160 ? res.quote.slice(0, 160) + '…' : res.quote);
}

function openDialog(add, quote) {
  pendingAdd = add;
  $('dlgQuote').textContent = quote;
  ta.value = draft;
  $('cmtAsk').checked = false;
  dlg.returnValue = '';
  dlg.showModal();
  ta.focus();
}
$('addGeneral').onclick = () => { fab.hidden = true; openDialog({general: true}, 'General comment on the whole document'); };

dlg.addEventListener('close', async () => {
  const text = ta.value.trim(), add = pendingAdd;
  pendingAdd = null;
  if (dlg.returnValue === 'ok' && text && add) {
    draft = text;
    const sent = askJoin($('cmtAsk').checked, text);
    const body = add.general ? {op: 'general', text: sent} : {op: 'add', s: add.s, e: add.e, text: sent};
    if (await op(body)) {
      draft = '';
      getSelection().removeAllRanges();
      if (add.general) [...threadsEl.querySelectorAll('.thread.general')].pop()?.scrollIntoView({block: 'nearest'});
    }
  } else draft = '';
  flushReload();
});
ta.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); dlg.close('ok'); }
});

function placeFab() {
  const sel = getSelection();
  if (!state || dlg.open || !sel.rangeCount || sel.isCollapsed || !docEl.contains(sel.getRangeAt(0).commonAncestorContainer)) {
    fab.hidden = true;
    return;
  }
  const rects = sel.getRangeAt(0).getClientRects();
  const rect = rects[rects.length - 1] || sel.getRangeAt(0).getBoundingClientRect();
  fab.hidden = false;
  fab.style.left = Math.max(8, Math.min(rect.right - fab.offsetWidth / 2, innerWidth - fab.offsetWidth - 8)) + 'px';
  fab.style.top = Math.min(rect.bottom + 8, innerHeight - fab.offsetHeight - 8) + 'px';
}
let fabTimer;
document.addEventListener('selectionchange', () => { clearTimeout(fabTimer); fabTimer = setTimeout(placeFab, 120); });
addEventListener('scroll', () => { if (!fab.hidden) placeFab(); }, {passive: true});
fab.addEventListener('mousedown', e => e.preventDefault()); // keep the selection
fab.addEventListener('click', startComment);
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'm') { e.preventDefault(); startComment(); }
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.code === 'Backslash') { e.preventDefault(); setToc(!toc); }
  else if ((e.metaKey || e.ctrlKey) && e.key === '\\') { e.preventDefault(); setSidebar(!sidebar); }
});

// ------------------------------------------------------------ Ask Claude
// One background Claude run per document answers all its open comments (/answer-comments).
// The server refuses a second run while one is going, from this tab or any other.
let claude = {running: false}, claudeTimer = null;
async function claudeCall(action, extra) {
  let r, d;
  try {
    r = action
      ? await fetch('/api/claude', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MDView': '1'},
          body: JSON.stringify({f: FILE, action, ...extra})})
      : await fetch('/api/claude?f=' + encodeURIComponent(FILE));
    d = await r.json();
  } catch { setLive(false); claudeTimer = setTimeout(claudeCall, 5000); return false; }
  if (!r.ok) { toast(d.error || r.statusText); return false; }
  const was = claude.running;
  claude = d;
  if (d.already) toast('Claude is already working on this file.');
  if (was && !d.running) claudeDone(d);
  paintClaude();
  clearTimeout(claudeTimer);
  if (d.running) claudeTimer = setTimeout(claudeCall, 3000);
  return true;
}
function claudeDone(d) {
  const report = () => { $('reportText').textContent = d.report || '(no output)'; $('reportDlg').showModal(); };
  if (d.exit === 0) toast('Claude has answered the comments.', 'Report', report);
  else if (d.exit == null) toast('Claude was stopped.');
  else toast(`Claude stopped with an error (exit ${d.exit}).`, 'Details', report);
}
function paintClaude() {
  const b = $('askClaude'), open = state ? state.threads.filter(t => t.status === 'open').length : 0;
  b.classList.toggle('busy', !!claude.running);
  kit.tab({app: 'md', oracle: !!claude.running});
  b.textContent = claude.running ? 'Working… ■' : 'Oracle';
  b.disabled = !claude.running && !open;
  b.title = claude.running ? `Answering the comments since ${claude.started}. Click to stop.`
    : open ? `Start Claude in the background to answer the ${open} open comment${open > 1 ? 's' : ''}` +
      (claude.session ? ', continuing its session on this file.' : ', in a new session.')
    : 'No open comments.';
  $('claudeRow').hidden = !claude.session || claude.running;
}
$('newSession').onclick = () => {
  if (confirm('Start a new Claude session for this file? The next Oracle run won\'t remember earlier rounds.'))
    claudeCall('new').then(ok => ok && toast('The next Oracle run starts a new session.'));
};
$('askClaude').onclick = () => {
  if (!claude.running) claudeCall('start');
  else if (confirm('Stop Claude? Edits it has already made stay in the file.')) claudeCall('stop');
};

// ------------------------------------------------------------ toast
let toastTimer;
function toast(text, action, fn) {
  $('toastText').textContent = text;
  const b = $('toastBtn');
  b.hidden = !action;
  if (action) { b.textContent = action; b.onclick = () => { $('toast').style.display = 'none'; fn(); }; }
  $('toast').style.display = 'flex';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').style.display = 'none'; }, action ? 7000 : 3500);
}
function undoToast(text) { toast(text, 'Undo', () => op({op: 'undo'})); }

$('undoBtn').onclick = () => op({op: 'undo'}).then(ok => ok && toast('Undone'));

// ------------------------------------------------------------ sidebar on/off
let sidebar = true;
function setSidebar(on) {
  sidebar = on;
  document.body.classList.toggle('noside', !on);
  $('sideBtn').setAttribute('aria-pressed', String(on));
  $('sideBtn').title = (on ? 'Hide' : 'Show') + ' the comments (⌘/Ctrl + \\)';
  try { localStorage.setItem('mdview.sidebar', on ? 'on' : 'off'); } catch {}
}
$('sideBtn').onclick = () => setSidebar(!sidebar);
setSidebar((() => { try { return localStorage.getItem('mdview.sidebar'); } catch { return null; } })() !== 'off');

// ------------------------------------------------------------ chat
// A conversation with Claude about this document, in a panel at the bottom right (kit.chatPanel, from viewkit).
// The server runs `claude` on the computer that serves the page and keeps the conversation; this page polls for it.
let chatQuote = '';  // the passage that was last selected in the document: the context that Claude gets

// A little Markdown for Claude's answers: paragraphs, headings, lists, code blocks; the inline part as in comments.
function chatMd(text) {
  const root = el('div', 'md'), lines = text.replace(/\r/g, '').split('\n');
  let para = [], list = null;
  const flush = () => {
    if (!para.length) return;
    if (para.every(l => l.trim().startsWith('|'))) root.append(el('pre', null, para.join('\n')));  // a table: as it is
    else { const p = el('p'); p.append(withMath(para.join(' '))); root.append(p); }
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    const fence = /^\s*```/.test(l);
    if (fence) {
      flush(); list = null;
      const code = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      const pre = el('pre'); pre.append(el('code', null, code.join('\n'))); root.append(pre);
      continue;
    }
    const li = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(l), h = /^#{1,6}\s+(.*)$/.exec(l);
    if (li) {
      flush();
      const tag = li[1] ? 'ul' : 'ol';
      if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag); root.append(list); }
      const item = el('li'); item.append(withMath(li[3])); list.append(item);
    } else if (h) { flush(); list = null; const e = el('h4'); e.append(withMath(h[1])); root.append(e); }
    else if (!l.trim()) { flush(); list = null; }
    else { if (list && /^\s/.test(l)) { list.lastChild.append(' ', withMath(l.trim())); continue; } list = null; para.push(l); }
  }
  flush();
  return root;
}

// ------------------------------------------------------------ notes to the app (self-improving apps)
// ⌘/Ctrl+Shift+. opens the dashboard of the kit: the notes wait there until Send all, which starts the automatic loop
// (viewkit/improvekit.py). The context is the state of the viewer only: never the text or the path of the document.
async function improveApi(method, path, body) {
  const r = await fetch(path, method === 'GET' ? undefined : {method, headers: {'Content-Type': 'application/json', 'X-MDView': '1'},
    body: JSON.stringify({f: FILE, ...body})});
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
kit.improveDash({
  app: 'mdview',
  get: () => improveApi('GET', '/api/improve/dash?f=' + encodeURIComponent(FILE)),
  post: body => improveApi('POST', '/api/improve/dash', body),
  context: () => ({
    sidebar: sidebar, contents: !!(typeof toc !== 'undefined' && toc), chat: !document.getElementById('chatBox').hidden,
    comments: state && state.threads ? state.threads.length : 0,
    suggestions: state && state.suggestions ? state.suggestions.length : 0,
    claude: !!(typeof claude !== 'undefined' && claude && claude.running),
  }),
  onDone: (msg, label, fn) => toast(msg, label, fn),
});
// In the sandbox of a proposal (mdview try, or the automatic loop): the card with Accept, Change and Reject.
kit.previewBand({
  get: () => improveApi('GET', '/api/improve/preview?f=' + encodeURIComponent(FILE)),
  post: body => improveApi('POST', '/api/improve/preview', body),
});

const chat = kit.chatPanel({
  key: 'mdview',
  account: {get: () => improveApi('GET', '/api/account?f=' + encodeURIComponent(FILE)),
            post: body => improveApi('POST', '/api/account', body)},
  poll: true,
  ready: () => !!state,
  empty: 'Ask about this document. Claude can read it and change it. ' +
    'It runs on the computer that serves this page. Turn on "Ask only" if it must not change anything.',
  get: async since => {
    const r = await fetch(`/api/chat?f=${encodeURIComponent(FILE)}` + (since == null ? '' : `&since=${since}`));
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    return d;
  },
  post: async body => {
    const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MDView': '1'},
      body: JSON.stringify({f: FILE, ...body})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    return d;
  },
  extra: () => ({quote: chatQuote}),
  afterSend: () => { chatQuote = ''; },
  context: () => chatQuote ? {text: '“' + chatQuote + '”', title: chatQuote} : null,
  hint: () => 'Select text to ask about it.',
  clearContext: () => { chatQuote = ''; },
  render: chatMd,
  typeset: () => typesetMath(),
  fillUser: (e, m) => {
    if (m.quote) { const q = el('div', 'cq', '“' + m.quote + '”'); q.title = m.quote; e.append(q); }
    e.append(m.text);
  },
  done: chatDone,
  error: text => toast(text),
});

document.addEventListener('selectionchange', () => {
  const s = getSelection();
  if (!s || s.isCollapsed || !s.anchorNode || !docEl.contains(s.anchorNode)) return;
  const q = s.toString().trim().replace(/\s+/g, ' ').slice(0, 1500);
  if (q && q !== chatQuote) { chatQuote = q; chat.renderCtx(); }
});

async function chatDone(m) {
  if (!m || !m.changed) return;
  if (editing || dlg.open) pendingReload = true; else await load(true);
  toast('Claude changed the document.', 'Undo', () => op({op: 'undo'}).then(ok => ok && toast('Undone')));
}

load();
claudeCall();
