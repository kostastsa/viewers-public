'use strict';
// texview: the page. The source in CodeMirror (left), the compiled PDF with PDF.js (kit.pdfPages, middle), the comments
// and the suggested changes (right), and the chat (kit.chatPanel). A double-click on the PDF opens or closes the source. A
// ⌘/Ctrl+click on the PDF opens the source at that line (SyncTeX), and a ⌘/Ctrl+click on a source line shows its place in the PDF. Save (⌘/Ctrl+S in the editor) writes the
// changed files and the server compiles again. A suggested change shows in the source: the old text crossed out, the new text
// after it, and ✓ (accept) and ✗ (reject).

const $ = id => document.getElementById(id);
const FILE = new URLSearchParams(location.search).get('f') || '';
const CONTEXT = 40;  // characters before and after a quote, in an anchor

const S = {
  doc: null,        // the payload of /api/doc: threads, version, sources, build, ...
  build: null,      // the state of the compile: {status, seq, pdf, errors, warnings, ms, ...}
  pdfLoaded: null,  // the version of the PDF on the screen
  active: null,     // the id of the selected thread
  comp: null,       // the open comment box: {type: 'new' | 'general' | 'reply' | 'edit', anchor, thread, msg, text}
  files: {},        // path -> {doc: CodeMirror.Doc, base: version on disk, gen: clean generation, warned}
  cur: null,        // the path of the file in the editor
  marks: [],        // the marks of the threads in the editor
  lines: [],        // the line handles of the error lines in the editor
  showWarn: false,  // the problems list shows the warnings too
  hideProblems: false,
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
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-TeXView': '1'},
    body: body === undefined ? undefined : JSON.stringify({f: FILE, ...body})});
  let data = {};
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) { const err = new Error(data.error || r.statusText); err.status = r.status; err.data = data; throw err; }
  return data;
}
const enc = encodeURIComponent;
const typing = t => t && t.closest && t.closest('input, textarea, select, [contenteditable], .CodeMirror');
const oneLine = s => s.replace(/\s+/g, ' ').trim();
function store(k, v) { try { localStorage.setItem('texview.' + k, v); } catch (e) { /* not saved */ } }
function stored(k) { try { return localStorage.getItem('texview.' + k); } catch (e) { return null; } }
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

// ------------------------------------------------------------ the PDF
const pv = kit.pdfPages({
  box: $('pages'), zoomLabel: $('zoomLabel'), pageNum: $('pageNum'), pageCount: $('pageCount'), outline: $('outline'),
  toast: msg => toast(msg),
  onZoom: z => store('zoom', String(z)),
});

async function loadPdf() {
  const b = S.build;
  if (!b || !b.pdf || b.pdf === S.pdfLoaded) return;
  const first = !S.pdfLoaded;
  S.pdfLoaded = b.pdf;
  $('pages').querySelectorAll('.loadmsg').forEach(e => e.remove());
  await pv.load(`/pdf?f=${enc(FILE)}&v=${enc(b.pdf)}`, !first);
}

function setDark(on) {
  S.dark = on;
  pv.setDark(on);
  $('darkBtn').classList.toggle('on', on);
  store('dark', on ? '1' : '0');
}
function setZoom(z) { pv.setZoom(z); store('zoom', String(pv.zoom)); }

// A double-click on a page opens or closes the source. It does not move the cursor of the source (texview#27).
$('pages').addEventListener('dblclick', ev => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey || !pv.pointOf(ev)) return;
  getSelection().removeAllRanges();
  $('selbar').style.display = 'none';
  setSrc(document.body.classList.contains('nosrc'));
});
// A ⌘/Ctrl+click on a page: its line in the source. If the source is closed, this opens it (texview#27).
$('pages').addEventListener('click', ev => {
  if (!(ev.metaKey || ev.ctrlKey) || ev.altKey || ev.shiftKey || ev.button !== 0 || ev.detail > 1) return;
  const pt = getSelection().isCollapsed && pv.pointOf(ev);  // a drag that selected text is not a click
  if (pt) syncToSource(pt);
});

async function sourceAt(pt) {
  try {
    return await api('GET', `/api/sync/edit?f=${enc(FILE)}&page=${pt.n}&x=${pt.x.toFixed(1)}&y=${pt.y.toFixed(1)}`);
  } catch (e) { toast(e.message); return null; }
}

async function syncToSource(pt) {
  const r = await sourceAt(pt);
  if (!r) return;
  if (!r.file) { toast('This place comes from a file outside the folder of the document: ' + (r.path || '?')); return; }
  setSrc(true);
  await openFile(r.file, r.line, true);
}

// The place of a source line in the PDF.
async function syncToPdf(path, line) {
  if (!S.pdfLoaded) { toast('There is no PDF yet.'); return; }
  const f = S.files[path];
  try {
    const r = await api('GET', `/api/sync/view?f=${enc(FILE)}&file=${enc(path)}&line=${line}`);
    await pv.showBoxes(r.page, r.boxes);
    if (f && isDirty(f)) toast('This file has unsaved changes: the place in the PDF can be a few lines off.', null, null, 3000);
  } catch (e) { toast(e.message); }
}

// ------------------------------------------------------------ the editor
const cm = CodeMirror($('editor'), {
  mode: 'stex', lineNumbers: true, lineWrapping: true, styleActiveLine: true, matchBrackets: true,
  indentUnit: 2, tabSize: 2, gutters: ['tv-marks', 'CodeMirror-linenumbers'], readOnly: 'nocursor',
  extraKeys: {
    'Cmd-/': 'toggleComment', 'Ctrl-/': 'toggleComment',
    'Cmd-F': () => openFind(false), 'Ctrl-F': () => openFind(false), 'Cmd-G': () => findStep(1), 'Ctrl-G': () => findStep(1),
    'Shift-Cmd-G': () => findStep(-1), 'Shift-Ctrl-G': () => findStep(-1),
    'Cmd-Alt-F': () => openFind(true), 'Shift-Ctrl-F': () => openFind(true),
    Tab: hintTabInUpdate,
  },
});
cm.setValue('');

// ------------------------------------------------------------ find and replace in the editor
// The find bar over the editor: a Find field and a Replace field. A query in lower case finds also the capital letters.
const find = {overlay: null};
const findQ = () => $('findQ').value;
const findFold = q => q === q.toLowerCase();
const findRe = q => new RegExp(q.replace(/[-[\]/{}()*+?.\\^$|]/g, '\\$&'), findFold(q) ? 'gi' : 'g');
const findOpen = () => !$('findbar').hidden;
const findCursor = (q, pos) => cm.getSearchCursor(q, pos, {caseFold: findFold(q)});
function findSelect(cur) {
  cm.setSelection(cur.from(), cur.to());
  cm.scrollIntoView({from: cur.from(), to: cur.to()}, 40);
}
function openFind(replace) {
  const sel = cm.getSelection();
  if (sel && !sel.includes('\n')) $('findQ').value = sel;
  $('findbar').hidden = false;
  findPaint();
  const box = replace && !cm.getOption('readOnly') ? $('replQ') : $('findQ');
  box.focus();
  box.select();
}
function closeFind() {
  $('findbar').hidden = true;
  findPaint();
  cm.focus();
}
// The matches in the editor (an overlay), their count, and the number of the selected match.
function findPaint() {
  if (find.overlay) cm.removeOverlay(find.overlay);
  find.overlay = null;
  const q = findQ();
  $('findQ').classList.remove('none');
  if (!q || !findOpen()) { $('findN').textContent = ''; return; }
  const re = findRe(q);
  find.overlay = {token(stream) {
    re.lastIndex = stream.pos;
    const m = re.exec(stream.string);
    if (m && m.index === stream.pos) { stream.pos += m[0].length || 1; return 'searching'; }
    if (m) stream.pos = m.index; else stream.skipToEnd();
  }};
  cm.addOverlay(find.overlay);
  const all = [...cm.getValue().matchAll(findRe(q))];
  const from = cm.indexFromPos(cm.getCursor('from')), to = cm.indexFromPos(cm.getCursor('to'));
  const at = all.findIndex(m => m.index === from && m.index + m[0].length === to) + 1;
  $('findN').textContent = !all.length ? 'none' : at ? `${at} of ${all.length}` : `${all.length} found`;
  $('findQ').classList.toggle('none', !all.length);
}
// Select the next match (dir 1) or the previous match (dir -1) after the cursor. At the end, continue from the other end.
// start: look from the start of the selection (while the user types the query).
function findStep(dir, start) {
  const q = findQ();
  if (!q) { openFind(false); return; }
  let cur = findCursor(q, cm.getCursor(dir > 0 && !start ? 'to' : 'from'));
  let hit = dir > 0 ? cur.findNext() : cur.findPrevious();
  if (!hit) {
    cur = findCursor(q, dir > 0 ? CodeMirror.Pos(cm.firstLine(), 0) : CodeMirror.Pos(cm.lastLine()));
    hit = dir > 0 ? cur.findNext() : cur.findPrevious();
  }
  if (hit) findSelect(cur);
  findPaint();
}
// Replace the selected match, then go to the next. When the selection is not a match, only go to the next match.
function replaceOne() {
  const q = findQ();
  if (!q || cm.getOption('readOnly')) return;
  const sel = cm.getSelection();
  if (findFold(q) ? sel.toLowerCase() === q : sel === q) cm.replaceSelection($('replQ').value, 'end');
  findStep(1);
}
// Replace all the matches of the file in one step, so that one ⌘/Ctrl+Z undoes them.
function replaceAll() {
  const q = findQ();
  if (!q || cm.getOption('readOnly')) return;
  const by = $('replQ').value;
  let n = 0;
  cm.operation(() => {
    const cur = findCursor(q, CodeMirror.Pos(cm.firstLine(), 0));
    while (cur.findNext()) { cur.replace(by); n++; }
  });
  findPaint();
  toast(n ? `Replaced ${n} match${n === 1 ? '' : 'es'}. ⌘/Ctrl+Z in the editor undoes this.` : 'No match.', null, null, 3000);
}
$('findQ').addEventListener('input', () => { if (findQ()) findStep(1, true); else findPaint(); });
for (const id of ['findQ', 'replQ']) {
  $(id).addEventListener('keydown', ev => {
    const mod = ev.metaKey || ev.ctrlKey;  // ev.code, not ev.key: with Alt on a Mac, the key F gives 'ƒ'
    if (ev.key === 'Escape') closeFind();
    else if (ev.key === 'Enter' && id === 'replQ' && !ev.shiftKey) mod ? replaceAll() : replaceOne();
    else if (ev.key === 'Enter') findStep(ev.shiftKey ? -1 : 1);
    else if (mod && ev.code === 'KeyG') findStep(ev.shiftKey ? -1 : 1);
    else if (mod && ev.code === 'KeyF') { const b = ev.altKey || ev.shiftKey ? $('replQ') : $('findQ'); b.focus(); b.select(); }
    else return;
    ev.preventDefault();
    ev.stopPropagation();
  });
}
$('findPrev').addEventListener('click', () => findStep(-1));
$('findNext').addEventListener('click', () => findStep(1));
$('findClose').addEventListener('click', closeFind);
$('replOne').addEventListener('click', replaceOne);
$('replAll').addEventListener('click', replaceAll);
cm.on('changes', () => { if (findOpen()) findPaint(); });
cm.on('swapDoc', () => { if (findOpen()) findPaint(); });

// ------------------------------------------------------------ suggestions in the editor
// The popup while you type: commands, labels, citation keys and environments. All the data is local. The server gives the
// data of the last compile and of the files that are not open (/api/complete, at the start and after each compile). The page
// scans each open buffer after a pause and filters here, so a key press sends no request (texview#30).
const HINT_COMMANDS = new Set(`
  documentclass usepackage input include includeonly includegraphics graphicspath bibliography bibliographystyle
  printbibliography addbibresource title author date thanks maketitle tableofcontents listoffigures listoftables appendix
  part chapter section subsection subsubsection paragraph subparagraph begin end item label ref eqref pageref autoref cref
  Cref nameref cite citep citet citeauthor citeyear nocite parencite textcite footnote footnotemark footnotetext caption
  centering raggedright raggedleft textbf textit texttt textsf textsc textrm textsl emph underline text textcolor color
  definecolor mathrm mathbf mathit mathsf mathtt mathcal mathbb mathfrak mathscr boldsymbol operatorname tiny scriptsize
  footnotesize small normalsize large Large LARGE huge Huge bfseries itshape ttfamily sffamily rmfamily scshape normalfont
  newcommand renewcommand providecommand newenvironment renewenvironment newtheorem def let DeclareMathOperator newpage
  clearpage cleardoublepage pagebreak linebreak newline noindent indent vspace hspace vfill hfill smallskip medskip bigskip
  quad qquad hline cline toprule midrule bottomrule multicolumn multirow left right big Big bigg Bigg middle frac dfrac tfrac
  sqrt sum prod int oint bigcup bigcap lim limsup liminf sup inf max min log ln exp sin cos tan det dim ker Pr arg binom
  overline underbrace overbrace hat bar tilde vec dot ddot widehat widetilde alpha beta gamma delta epsilon varepsilon zeta
  eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma
  Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega leq geq neq approx sim simeq equiv propto ll gg subset subseteq supset
  supseteq in notin ni cup cap setminus emptyset forall exists neg land lor wedge vee times cdot ast star otimes oplus circ
  pm mp div nabla partial infty ell Re Im to rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow mapsto
  longrightarrow longmapsto implies iff ldots cdots vdots ddots dots langle rangle lfloor rfloor lceil rceil lVert rVert
  lvert rvert mid parallel perp nonumber notag tag intertext substack stackrel mathop limits nolimits displaystyle textstyle
  scriptstyle url href hypersetup setlength setcounter addtocounter stepcounter refstepcounter today LaTeX TeX phantom
  hphantom vphantom verb`.trim().split(/\s+/));
const HINT_ENVS = new Set(`
  document abstract figure figure* table table* tabular tabular* tabularx array center flushleft flushright itemize enumerate
  description quote quotation verse verbatim verbatim* minipage equation equation* align align* alignat alignat* gather gather*
  multline multline* flalign flalign* split aligned gathered cases matrix pmatrix bmatrix vmatrix Vmatrix smallmatrix
  subequations theorem lemma proposition corollary definition remark example proof thebibliography tikzpicture frame block
  algorithm algorithmic titlepage list tabbing lstlisting`.trim().split(/\s+/));
const HINT_LOOK_BACK = 200;  // only this many characters before the cursor are read to find the trigger: some lines are long
const HINT_MAX = 100;        // the longest list in the popup
const HINT_PAUSE = 300;      // the pause in typing (ms) before the scan of a buffer
const HINT_OPTIONS = {hint: hintsAt, completeSingle: false, closeCharacters: /(?!)/};  // a label name can have spaces and colons
const hintServer = {labels: {}, cites: [], sources: {}};
let hintData = null;  // the merged data, made again after a scan or new data of the server

// The definitions and the labels of a buffer, with the same patterns as tex_defs in texview.py.
function scanSource(text) {
  text = text.replace(/(?<!\\)((?:\\\\)*)%.*$/gm, '$1');
  const all = re => [...text.matchAll(re)].map(m => m[1]);
  return {
    labels: all(/\\label(?:\[[^\]]*\])?\s*\{([^{}]+)\}/g),
    commands: all(/\\(?:(?:renew|new|provide)command\*?\s*\{?\s*|def\s*)\\([a-zA-Z@]+)/g).filter(c => !c.includes('@')),
    envs: all(/\\newenvironment\*?\s*\{([^{}\s]+)\}/g),
  };
}
function scanLater(f) {
  clearTimeout(f.scanTimer);
  f.scanTimer = setTimeout(() => { f.scan = scanSource(f.doc.getValue()); hintData = null; }, HINT_PAUSE);
}
async function loadHints() {
  try {
    Object.assign(hintServer, await api('GET', `/api/complete?f=${enc(FILE)}`));
    hintData = null;
  } catch (e) { /* the popup keeps the data that it has */ }
}
function mergedHints() {
  if (hintData) return hintData;
  const labels = new Map(Object.entries(hintServer.labels)), commands = new Set(HINT_COMMANDS), envs = new Set(HINT_ENVS);
  const add = d => { d.commands.forEach(c => commands.add(c)); d.envs.forEach(e => envs.add(e)); };
  for (const [path, d] of Object.entries(hintServer.sources)) if (!(S.files[path] && S.files[path].scan)) add(d);
  for (const f of Object.values(S.files)) {
    if (!f.scan) continue;
    add(f.scan);
    f.scan.labels.forEach(n => { if (!labels.has(n)) labels.set(n, ''); });  // a label that is not compiled yet
  }
  return hintData = {labels, commands, envs};
}

// True if the character at position i of s is escaped by the backslashes before it.
function hintEscaped(s, i) {
  let n = 0;
  while (s[i - 1 - n] === '\\') n++;
  return n % 2 === 1;
}
// True if the cursor (column ch) is in a % comment. This reads the full line: the % can be far before the cursor.
function hintInComment(line, ch) {
  for (let i = line.indexOf('%'); i >= 0 && i < ch; i = line.indexOf('%', i + 1)) if (!hintEscaped(line, i)) return true;
  return false;
}
// The trigger at the end of the text before the cursor: {kind, query, len (the characters that a suggestion replaces)}.
function hintContext(before) {
  const lastKey = s => s.slice(s.lastIndexOf(',') + 1).replace(/^\s+/, '');  // after a comma, only the last key
  const hit = (m, kind, query) => m && !hintEscaped(before, m.index) ? {kind, query, len: query.length} : null;
  let m = before.match(/\\[a-zA-Z]*[cC]ite[a-zA-Z]*\*?(?:\s*\[[^\]]*\])*\s*\{([^{}]*)$/);
  if (m) return hit(m, 'cites', lastKey(m[1]));
  m = before.match(/\\((?:eq|page|auto|name)?ref|[cC]ref)\*?\s*\{([^{}]*)$/);
  if (m) return hit(m, 'labels', /^[cC]/.test(m[1]) ? lastKey(m[2]) : m[2]);
  m = before.match(/\\(?:begin|end)\s*\{([^{}]*)$/);
  if (m) return hit(m, 'envs', m[1]);
  m = before.match(/\\([a-zA-Z]+)$/);
  return m && !hintEscaped(before, m.index) ? {kind: 'commands', query: m[1], len: m[1].length + 1} : null;
}
// The names that fit the query: first the same start with the same case, then the same start, then the query inside the name.
function hintFilter(names, query) {
  const q = query.toLowerCase(), found = [];
  for (const n of names) {
    const low = n.toLowerCase();
    const rank = n.startsWith(query) ? 0 : low.startsWith(q) ? 1 : low.includes(q) ? 2 : -1;
    if (rank >= 0) found.push([rank, n]);
  }
  found.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  return found.slice(0, HINT_MAX).map(x => x[1]);
}
function renderHint(li, data, item) {
  li.append(el('span', null, item.text));
  if (item.num) li.append(el('span', 'tv-hint-num', item.num));
}
function hintsAt(ed) {
  const cur = ed.getCursor(), line = ed.getLine(cur.line);
  const c = hintContext(line.slice(Math.max(0, cur.ch - HINT_LOOK_BACK), cur.ch));
  if (!c || hintInComment(line, cur.ch)) return null;
  const d = mergedHints(), prefix = c.kind === 'commands' ? '\\' : '';
  const names = c.kind === 'cites' ? hintServer.cites : c.kind === 'labels' ? d.labels.keys() : d[c.kind];
  const list = hintFilter(names, c.query).map(n => ({text: prefix + n, render: renderHint,
    num: c.kind === 'labels' ? d.labels.get(n) : ''}));
  if (!list.length || (list.length === 1 && list[0].text === prefix + c.query)) return null;  // nothing to add
  return {list, from: CodeMirror.Pos(cur.line, cur.ch - c.len), to: cur};
}
// While the popup is open, the keys of the addon take Tab. Only in the frame before the popup updates, the addon has no Tab
// key and Tab comes here: update now and accept, so that a fast Tab does not type a tab character. Without a popup, Tab does not change.
function hintTabInUpdate(ed) {
  const c = ed.state.completionActive;
  if (!c || !c.widget) return CodeMirror.Pass;
  c.update();
  if (!c.widget) return CodeMirror.Pass;
  c.widget.pick();
}
// Only typing opens the popup. While it is open, the addon updates it after each change of the cursor.
cm.on('inputRead', (ed, change) => {
  const open = ed.state.completionActive;
  if (change.origin === '+input' && !(open && open.widget) && hintsAt(ed)) ed.showHint(HINT_OPTIONS);
});

const isDirty = f => !!f && !f.doc.isClean(f.gen);
function paintDirty() {
  const any = Object.values(S.files).some(isDirty);
  $('dirty').classList.toggle('on', isDirty(S.files[S.cur]));
  $('saveBtn').disabled = !any;
  [...$('fileSel').options].forEach(o => {
    const name = (o.dataset.name || o.value) + (isDirty(S.files[o.value]) ? ' ●' : '');
    if (o.textContent !== name) o.textContent = name;
  });
}

function renderFiles() {
  const sel = $('fileSel'), list = S.doc ? S.doc.sources : [];
  const known = new Set(list.map(s => s.path));
  const paths = [...list.map(s => s.path), ...Object.keys(S.files).filter(p => !known.has(p))];
  const up = S.doc && S.doc.up || [];  // the documents of the folder above: the way back up from a subdirectory
  const key = paths.join('\n') + '\n' + list.filter(s => s.doc).map(s => s.path).join('\n') + '\n'
    + up.map(s => s.path).join('\n');
  if (sel.dataset.key !== key) {
    sel.replaceChildren();
    // the other files: the top folder first, then one group for each subdirectory (its documents first)
    const dirOf = p => p.includes('/') ? p.slice(0, p.lastIndexOf('/') + 1) : '';
    const ins = list.filter(s => s.input), others = list.filter(s => !s.input && !dirOf(s.path));
    const group = (label, xs, d = '') => {
      if (!xs.length) return;
      const g = el('optgroup');
      g.label = label;
      for (const s of xs) {
        const name = (d && s.doc ? '📄 ' : '') + s.path.slice(d.length);
        const o = el('option', null, name);
        o.value = s.path;
        if (name !== s.path) o.dataset.name = name;
        if (s.doc) { o.dataset.doc = '1'; o.title = 'Open this document with its PDF'; }
        g.append(o);
      }
      sel.append(g);
    };
    if (up.length) {
      const g = el('optgroup');
      g.label = '↑ ../ (the folder above)';
      for (const s of up) {
        const o = el('option', null, s.name);
        o.value = s.name;
        o.dataset.doc = '1';
        o.dataset.href = s.path;
        o.title = 'Open this document with its PDF';
        g.append(o);
      }
      sel.append(g);
    }
    group('In the document', ins);
    group('Other documents (open with the PDF)', others.filter(s => s.doc));
    group('Other files in the folder', others.filter(s => !s.doc));
    const sub = list.filter(s => !s.input && dirOf(s.path));
    for (const d of [...new Set(sub.map(s => dirOf(s.path)))].sort()) {
      const xs = sub.filter(s => dirOf(s.path) === d);
      group('📁 ' + d, [...xs.filter(s => s.doc), ...xs.filter(s => !s.doc)], d);
    }
    sel.dataset.key = key;
  }
  sel.value = S.cur || '';
  paintDirty();
}

async function fetchFile(path) {
  const d = await api('GET', `/api/src?f=${enc(FILE)}&path=${enc(path)}`);
  return d;
}

// Open a source file in the editor (each file keeps its own text, cursor and Undo while the page is open).
async function openFile(path, line, flash) {
  let f = S.files[path];
  if (!f) {
    let d;
    try { d = await fetchFile(path); } catch (e) { toast(e.message); return false; }
    const doc = CodeMirror.Doc(d.text, 'stex');
    f = S.files[path] = {doc, base: d.version, gen: doc.changeGeneration(true), path};
    doc.on('change', () => { paintDirty(); scanLater(f); });
    scanLater(f);
  }
  if (S.cur !== path) {
    cm.swapDoc(f.doc);
    cm.setOption('readOnly', false);
    S.cur = path;
    store('file.' + FILE, path);
    renderFiles();
    paintEditor();
  }
  if (line) {
    const ln = Math.min(cm.lineCount(), Math.max(1, line)) - 1;
    cm.setCursor({line: ln, ch: 0});
    cm.scrollIntoView({line: ln, ch: 0}, cm.getScrollerElement().clientHeight / 3);
    if (flash) {
      const hnd = cm.addLineClass(ln, 'background', 'tv-flash');
      setTimeout(() => cm.removeLineClass(hnd, 'background', 'tv-flash'), 1700);
    }
    cm.focus();
  }
  return true;
}

$('fileSel').addEventListener('change', ev => {
  const sel = ev.target, o = sel.selectedOptions[0];
  if (o && o.dataset.doc) {  // the main file of another document: open that document, with its PDF, in this tab
    const path = sel.value;
    sel.value = S.cur || '';
    location.href = '/view?f=' + enc(o.dataset.href || (S.doc.dir ? S.doc.dir + '/' : '') + path);
    return;
  }
  openFile(sel.value);
});

// Reload a file from the disk, if it has no unsaved changes. The cursor and the scroll stay.
async function reloadFile(path) {
  const f = S.files[path];
  if (!f || isDirty(f)) return;
  let d;
  try { d = await fetchFile(path); } catch (e) { return; }
  if (d.version === f.base || isDirty(f)) return;
  const cur = f.doc.getCursor(), info = path === S.cur ? cm.getScrollInfo() : null;
  f.doc.setValue(d.text);
  f.doc.setCursor(cur);
  f.base = d.version;
  f.gen = f.doc.changeGeneration(true);
  f.warned = false;
  if (info) cm.scrollTo(info.left, info.top);
  if (path === S.cur) paintEditor();
  paintDirty();
}

// Save every changed file, then the server compiles once.
async function saveAll() {
  const dirty = Object.values(S.files).filter(isDirty);
  if (!dirty.length) return true;
  let ok = true, last = null;
  for (const f of dirty) {
    const text = f.doc.getValue(), gen = f.doc.changeGeneration();
    let r;
    try {
      r = await api('POST', '/api/save', {path: f.path, text, base: f.base});
    } catch (e) {
      if (e.status === 409 && e.data && e.data.text != null) {
        if (confirm(`${f.path} changed on disk since you opened it. Keep your version (OK) or take the version on disk (Cancel)?`)) {
          try { r = await api('POST', '/api/save', {path: f.path, text}); } catch (e2) { toast(e2.message, null, null, 9000); ok = false; continue; }
        } else {
          f.doc.setValue(e.data.text);
          f.base = e.data.version;
          f.gen = f.doc.changeGeneration(true);
          continue;
        }
      } else { toast(e.message, null, null, 9000); ok = false; continue; }
    }
    f.base = r.version;
    f.gen = gen;
    f.warned = false;
    last = r;
  }
  if (last) {
    S.doc.threads = last.threads;
    S.doc.version = last.commentsVersion;
    setBuild(last.build);
    renderThreads();
    paintEditor();
  }
  paintDirty();
  return ok;
}

// The marks of the editor: the comments and the suggested changes of this file, and the lines with compile problems.
function locateIn(text, quote, prefix, suffix, line) {
  if (!quote) return null;
  let best = null;
  for (let i = text.indexOf(quote); i >= 0; i = text.indexOf(quote, i + 1)) {
    const before = text.slice(Math.max(0, i - CONTEXT), i), after = text.slice(i + quote.length, i + quote.length + CONTEXT);
    let k = 0, j = 0;
    while (k < before.length && k < (prefix || '').length && before[before.length - 1 - k] === prefix[prefix.length - 1 - k]) k++;
    while (j < after.length && j < (suffix || '').length && after[j] === suffix[j]) j++;
    const ln = text.slice(0, i).split('\n').length;
    const score = (k + j) * 1e6 - (line ? Math.abs(ln - line) : 0);
    if (!best || score > best.score) best = {i, score};
  }
  return best ? {a: best.i, b: best.i + quote.length} : null;
}

// The words that change between the old and the new text of a suggestion: [{k: 'eq' | 'del' | 'ins', text}].
// A longest common subsequence of the tokens (words, LaTeX commands, spaces, other characters). A very long change is
// one deletion and one insertion.
function wordDiff(a, b) {
  const tok = s => s.match(/\s+|\\[A-Za-z@]+|[\p{L}\p{N}_]+|[^]/gu) || [];
  const x = tok(a), y = tok(b), n = x.length, m = y.length;
  if (n * m > 250000) return [{k: 'del', text: a}, {k: 'ins', text: b}].filter(o => o.text);
  const L = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  const push = (k, text) => { const last = out[out.length - 1]; if (last && last.k === k) last.text += text; else out.push({k, text}); };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { push('eq', x[i]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) push('del', x[i++]);
    else push('ins', y[j++]);
  }
  while (i < n) push('del', x[i++]);
  while (j < m) push('ins', y[j++]);
  // a space alone between two changes joins them, so the change reads as one phrase
  for (let k = out.length - 2; k > 0; k--) {
    if (out[k].k === 'eq' && /^\s+$/.test(out[k].text) && out[k - 1].k !== 'eq' && out[k + 1].k !== 'eq')
      out.splice(k, 1, {k: 'del', text: out[k].text}, {k: 'ins', text: out[k].text});
  }
  // each run of changes becomes one deletion (the old words in order) and one insertion (the new words in order)
  const merged = [];
  for (let k = 0; k < out.length;) {
    if (out[k].k === 'eq') { merged.push(out[k++]); continue; }
    let del = '', ins = '';
    while (k < out.length && out[k].k !== 'eq') { if (out[k].k === 'del') del += out[k].text; else ins += out[k].text; k++; }
    if (del) merged.push({k: 'del', text: del});
    if (ins) merged.push({k: 'ins', text: ins});
  }
  return merged;
}

function paintEditor() {
  for (const m of S.marks) m.clear();
  S.marks = [];
  for (const h of S.lines) cm.removeLineClass(h, 'background', 'tv-errline');
  S.lines = [];
  cm.clearGutter('tv-marks');
  if (!S.cur || !S.doc) return;
  const doc = cm.getDoc(), text = doc.getValue();
  for (const t of S.doc.threads) {
    const a = t.anchor;
    if (!a || a.file !== S.cur) continue;
    const quote = t.change ? t.change.old : a.quote;
    const loc = locateIn(text, quote, a.prefix, a.suffix, a.line);
    if (!loc) continue;
    const from = doc.posFromIndex(loc.a), to = doc.posFromIndex(loc.b), act = t.id === S.active ? ' active' : '';
    const attrs = {'data-t': String(t.id), title: (t.change ? 'Suggested change #' : 'Comment #') + t.id};
    if (!t.change) {
      S.marks.push(doc.markText(from, to, {className: 'tv-comment' + act, attributes: attrs}));
      continue;
    }
    // a suggested change, as tracked changes: the words that go are crossed out, the words that come are in green
    S.marks.push(doc.markText(from, to, {className: 'tv-chg' + act, attributes: attrs}));
    let off = loc.a;
    for (const o of wordDiff(t.change.old, t.change.new)) {
      if (o.k === 'eq') { off += o.text.length; continue; }
      if (o.k === 'del') {
        S.marks.push(doc.markText(doc.posFromIndex(off), doc.posFromIndex(off + o.text.length),
                                  {className: 'tv-old' + act, attributes: attrs}));
        off += o.text.length;
      } else {
        const w = el('span', 'tv-new', o.text);
        w.dataset.t = t.id;
        w.title = 'New text of suggested change #' + t.id;
        S.marks.push(doc.setBookmark(doc.posFromIndex(off), {widget: w, insertLeft: true}));
      }
    }
    const acts = el('span', 'tv-acts');
    acts.append(btn('✓', 'Accept this change', () => acceptChange(t.id), 'yes'),
                btn('✗', 'Reject this change', () => rejectChange(t.id), 'no'));
    S.marks.push(doc.setBookmark(to, {widget: acts, insertLeft: false, handleMouseEvents: true}));
  }
  const probs = S.build ? [...(S.build.errors || []).map(e => ({...e, k: 'err'})),
                           ...(S.build.warnings || []).map(e => ({...e, k: 'warn'}))] : [];
  const seen = new Set();
  for (const p of probs) {
    if (p.file !== S.cur || !p.line || p.line > cm.lineCount() || seen.has(p.line)) continue;
    seen.add(p.line);
    const g = el('div', 'tv-gut' + (p.k === 'warn' ? ' warn' : ''));
    g.title = p.msg;
    cm.setGutterMarker(p.line - 1, 'tv-marks', g);
    if (p.k === 'err') S.lines.push(cm.addLineClass(p.line - 1, 'background', 'tv-errline'));
  }
}

cm.getWrapperElement().addEventListener('mousedown', ev => {
  const t = ev.target.closest && ev.target.closest('[data-t]');
  if (t && !ev.metaKey && !ev.ctrlKey && !ev.target.closest('button')) setActive(+t.dataset.t, 'source');
});
// ⌘/Ctrl+click on a line: its place in the PDF (CodeMirror would add a cursor).
cm.on('mousedown', (c, ev) => {
  if (!(ev.metaKey || ev.ctrlKey) || ev.altKey || ev.shiftKey) return;
  ev.preventDefault();
  const pos = cm.coordsChar({left: ev.clientX, top: ev.clientY});
  if (S.cur) syncToPdf(S.cur, pos.line + 1);
});
const cmdClass = ev => document.body.classList.toggle('cmdown', !!(ev.metaKey || ev.ctrlKey));
addEventListener('keydown', cmdClass);
addEventListener('keyup', cmdClass);
addEventListener('blur', () => document.body.classList.remove('cmdown'));
$('syncBtn').onclick = () => { if (S.cur) syncToPdf(S.cur, cm.getCursor().line + 1); };
$('saveBtn').onclick = () => saveAll();
cm.on('cursorActivity', () => {
  const sel = cm.getSelection();
  if (sel.trim()) {
    const a = cm.getCursor('from').line + 1, b = cm.getCursor('to').line + 1;
    setChatSel({quote: sel.slice(0, 3000), where: `${S.cur} ${a === b ? 'line ' + a : 'lines ' + a + '–' + b}`,
                short: `${S.cur.split('/').pop()}:${a}`});
  } else if (chatSel && chatSel.fromEditor) setChatSel(null);
  placeEdSel();
});

// The 💬 Comment bubble at the end of the text selected in the source (as the selection bar on a page).
function placeEdSel() {
  const b = $('edsel');
  if (!S.cur || !cm.hasFocus() || !cm.getSelection().trim()) { b.style.display = 'none'; return; }
  const c = cm.cursorCoords(cm.getCursor('to'), 'window'), box = cm.getWrapperElement().getBoundingClientRect();
  if (c.bottom < box.top || c.top > box.bottom) { b.style.display = 'none'; return; }  // scrolled out of view
  b.style.display = 'flex';
  b.style.left = Math.max(box.left, Math.min(box.right - b.offsetWidth - 4, c.left + 6)) + 'px';
  b.style.top = Math.min(box.bottom - b.offsetHeight - 4, c.bottom + 4) + 'px';
}
cm.on('scroll', placeEdSel);
cm.on('focus', placeEdSel);
cm.on('blur', placeEdSel);
$('edcbtn').addEventListener('mousedown', ev => ev.preventDefault());  // keep the focus and the selection
$('edcbtn').addEventListener('click', () => { commentOnSelection(); $('edsel').style.display = 'none'; });

// The pane of the source: show or hide it, and its width (the bar between the source and the pages).
function setSrc(on) {
  document.body.classList.toggle('nosrc', !on);
  $('srcBtn').classList.toggle('on', on);
  store('src', on ? 'on' : 'off');
  if (on) cm.refresh();
  setTimeout(() => pv.relayout(), 0);
}
(() => {
  const split = $('split');
  const w = parseInt(stored('srcw'), 10);
  if (w > 0) document.documentElement.style.setProperty('--src-w', w + 'px');
  split.addEventListener('mousedown', ev => {
    ev.preventDefault();
    split.classList.add('drag');
    const x0 = ev.clientX, w0 = $('src').offsetWidth;
    const move = e => {
      const nw = Math.max(240, Math.min(window.innerWidth - 360, w0 + e.clientX - x0));
      document.documentElement.style.setProperty('--src-w', nw + 'px');
    };
    const up = () => {
      removeEventListener('mousemove', move);
      removeEventListener('mouseup', up);
      split.classList.remove('drag');
      store('srcw', $('src').offsetWidth);
      cm.refresh();
      pv.relayout();
    };
    addEventListener('mousemove', move);
    addEventListener('mouseup', up);
  });
  split.addEventListener('dblclick', () => {
    document.documentElement.style.removeProperty('--src-w');
    store('srcw', '');
    cm.refresh();
    pv.relayout();
  });
})();

// The zoom of the source, separate from the zoom of the pages: Option or ⌘/Ctrl with +, - and 0 when the focus or the last
// click is in the source pane, or a pinch (⌘/Ctrl+wheel) on the editor. It changes only the size of the text in the editor.
let srcZoom = 1;
function setSrcZoom(v, save = true) {
  srcZoom = Math.round(Math.max(0.6, Math.min(3, v)) * 100) / 100;
  const r = document.documentElement.style;
  if (srcZoom === 1) r.removeProperty('--src-zoom'); else r.setProperty('--src-zoom', String(srcZoom));
  if (save) store('srcZoom', srcZoom === 1 ? '' : String(srcZoom));
  cm.refresh();
}
(() => {
  const v = parseFloat(stored('srcZoom'));
  if (v > 0) setSrcZoom(v, false);
  let inside = false, g = 0;
  document.addEventListener('pointerdown', ev => { inside = $('src').contains(ev.target); }, true);
  // Option with +, - and 0 (the default keys) zooms the pane of the focus or of the last click: the source or the pages.
  // ⌘/Ctrl zooms only the source, and elsewhere it stays the zoom of the browser. ev.code: Option changes ev.key on a Mac.
  addEventListener('keydown', ev => {
    const alt = ev.altKey && !ev.metaKey && !ev.ctrlKey, mod = (ev.metaKey || ev.ctrlKey) && !ev.altKey;
    if (!alt && !mod) return;
    const c = ev.code, key = mod ? ev.key : '';
    const k = c === 'Equal' || c === 'NumpadAdd' || key === '=' || key === '+' ? 1
            : c === 'Minus' || c === 'NumpadSubtract' || key === '-' ? -1
            : c === 'Digit0' || c === 'Numpad0' || key === '0' ? 0 : null;
    if (k === null) return;
    const a = document.activeElement;
    const src = !document.body.classList.contains('nosrc') && ($('src').contains(a) || (inside && !typing(a)));
    if (mod ? !src : typing(a) && !a.closest('.CodeMirror')) return;  // Option in a text box types its character (–, ≠, º)
    ev.preventDefault();
    ev.stopPropagation();
    if (src) setSrcZoom(k === 0 ? 1 : k > 0 ? srcZoom * 1.1 : srcZoom / 1.1);
    else setZoom(k === 0 ? 'fit' : Math.round(pv.scale * (k > 0 ? 1.2 : 1 / 1.2) * 100) / 100);
  }, true);
  $('editor').addEventListener('wheel', ev => {
    if (!ev.ctrlKey || g) return;  // a plain wheel scrolls the source
    ev.preventDefault();
    setSrcZoom(srcZoom * Math.exp(-ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.01)));
  }, {passive: false});
  for (const [type, fn] of [  // Safari: a pinch is a gesture with a scale from its start (g: the size at the start)
    ['gesturestart', () => { g = srcZoom; }],
    ['gesturechange', ev => { if (g) setSrcZoom(g * ev.scale); }],
    ['gestureend', () => { g = 0; }],
  ]) $('editor').addEventListener(type, ev => { ev.preventDefault(); fn(ev); }, {passive: false});
})();

// ------------------------------------------------------------ the compile
function setBuild(b) {
  if (!b) return;
  const old = S.build;
  S.build = b;
  const B = $('buildBtn'), nE = (b.errors || []).length, nW = (b.warnings || []).length;
  B.className = b.status;
  $('buildLabel').textContent = b.status === 'running' ? 'Compiling…'
    : b.status === 'failed' ? 'Failed' + (nE ? ' · ' + plural(nE, 'error') : '')
    : b.status === 'error' ? plural(nE, 'error')
    : b.status === 'ok' ? 'Compiled' + (b.ms != null ? ` · ${(b.ms / 1000).toFixed(1)} s` : '')
    : b.status === 'old' ? 'Compiling…' : '…';
  B.title = (b.status === 'running' ? 'Compiling since ' + (b.started || '?') + '. Click to stop.'
    : 'Compile now (⌘/Ctrl+Shift+B)') + (nW ? ` · ${plural(nW, 'warning')}` : '');
  kit.tab({oracle: !!(oracle && oracle.running) || b.status === 'running'});
  if (!old || old.seq !== b.seq || old.status !== b.status) {
    renderProblems();
    if (S.cur) paintEditor();
    if (!old || b.status !== 'running') loadHints();  // at the start, and after each compile
  }
  if (b.pdf && b.pdf !== S.pdfLoaded && (b.status !== 'running' || !S.pdfLoaded)) loadPdf();  // an old PDF shows at once
  if (b.status === 'failed' && !S.pdfLoaded) {
    const box = $('pages');
    box.querySelectorAll('.loadmsg').forEach(e => e.remove());
    box.prepend(el('div', 'loadmsg', 'The compile made no PDF. The problems are under the source.'));
  }
}

function renderProblems() {
  const box = $('problems'), b = S.build || {};
  const errs = b.errors || [], warns = b.warnings || [];
  if (b.status === 'running' || (!errs.length && !warns.length) || (S.hideProblems && !errs.length)) {
    box.hidden = !(errs.length || warns.length) || S.hideProblems;
    if (b.status === 'running') return;
  }
  box.hidden = S.hideProblems || (!errs.length && !warns.length);
  const head = el('div', 'ph');
  head.append(el('b', null, errs.length ? plural(errs.length, 'error') : 'No errors'),
              el('span', null, warns.length ? '· ' + plural(warns.length, 'warning') : ''), el('span', 'grow'));
  if (warns.length) head.append(btn(S.showWarn ? 'Hide warnings' : 'Show warnings', '', () => { S.showWarn = !S.showWarn; renderProblems(); }));
  head.append(btn('Log', 'The output of latexmk', () => {
    $('reportText').textContent = b.output || '(no output)';
    $('reportDlg').showModal();
  }), btn('×', 'Hide the problems', () => { S.hideProblems = true; renderProblems(); }));
  const rows = [head];
  const item = (p, k) => {
    const r = el('div', 'p ' + k);
    r.append(el('span', 'k', k === 'err' ? 'error' : 'warning'));
    if (p.file || p.line) r.append(el('span', 'w', `${p.file || '?'}${p.line ? ':' + p.line : ''}`));
    r.append(el('span', 'm', p.msg));
    if (p.file && p.line) r.addEventListener('click', () => { setSrc(true); openFile(p.file, p.line, true); });
    return r;
  };
  for (const p of errs) rows.push(item(p, 'err'));
  if (S.showWarn) for (const p of warns) rows.push(item(p, 'warn'));
  box.replaceChildren(...rows);
}

async function compileNow() {
  try {
    if (S.build && S.build.status === 'running') { setBuild((await api('POST', '/api/build', {action: 'stop'})).build); return; }
    if (Object.values(S.files).some(isDirty)) { await saveAll(); return; }  // the save compiles
    S.hideProblems = false;
    setBuild((await api('POST', '/api/build', {action: 'build'})).build);
  } catch (e) { toast(e.message); }
}
$('buildBtn').onclick = compileNow;

// ------------------------------------------------------------ the comments file
async function loadDoc() {
  try {
    S.doc = await api('GET', `/api/doc?f=${enc(FILE)}`);
  } catch (e) {
    $('pages').replaceChildren(el('div', 'err-page', e.message));
    return false;
  }
  renderHeader();
  renderFiles();
  renderThreads();
  setBuild(S.doc.build);
  paintEditor();
  return true;
}

async function op(body, done) {
  try {
    const d = await api('POST', '/api/op', {...body, version: S.doc.version});
    const build = S.build;
    S.doc = d;
    S.build = build;  // the poll brings the new compile
  } catch (e) {
    toast(e.message, null, null, 8000);
    if (e.status === 409) await loadDoc();
    return null;
  }
  renderHeader();
  renderThreads();
  paintEditor();
  if (done) toast(done, 'Undo', undo);
  return S.doc;
}

async function undo() {
  const d = await op({op: 'undo'});
  if (!d) return;
  for (const p of Object.keys(S.files)) await reloadFile(p);
  toast('Undone.');
}

function renderHeader() {
  const d = S.doc;
  kit.crumbs($('name'), d.label, msg => toast(msg));
  kit.docTitle('tex', d.label);
  const dl = $('dirLink');
  dl.hidden = !d.dirOk;
  dl.href = '/?d=' + enc(d.dir);
  $('undoBtn').disabled = !d.canUndo;
}

// ------------------------------------------------------------ suggested changes
async function acceptChange(id) {
  const t = S.doc.threads.find(x => x.id === id);
  if (!t) return;
  if (t.anchor && isDirty(S.files[t.anchor.file]) && !await saveAll()) return;  // the change goes into the saved file
  const d = await op({op: 'accept', thread: id});
  if (!d) return;
  if (d.skipped && d.skipped.length) toast('The old text is no longer in the file, so this change does not fit. Reject it, or ask Claude again.', null, null, 9000);
  else toast('Change accepted.', 'Undo', undo);
  if (t.anchor) await reloadFile(t.anchor.file);
}
async function rejectChange(id) { await op({op: 'reject', thread: id}, 'Change rejected.'); }
$('acceptAllBtn').onclick = async () => {
  const n = S.doc.threads.filter(t => t.change).length;
  if (!n || !confirm(`Accept all ${plural(n, 'suggested change')}? They go into the source files (Undo takes them back).`)) return;
  if (!await saveAll()) return;
  const d = await op({op: 'accept-all'});
  if (!d) return;
  toast(`${plural(d.accepted.length, 'change')} accepted` + (d.skipped.length ? `, ${d.skipped.length} no longer fit.` : '.'), 'Undo', undo, 9000);
  for (const p of Object.keys(S.files)) await reloadFile(p);
};
$('rejectAllBtn').onclick = () => {
  const n = S.doc.threads.filter(t => t.change).length;
  if (n && confirm(`Reject all ${plural(n, 'suggested change')}? (Undo brings them back.)`)) op({op: 'reject-all'}, 'All changes rejected.');
};

// ------------------------------------------------------------ the sidebar
// from: 'source' (a click on a mark in the editor) or 'page'; else the click was on the card.
// keepSide: do not open the sidebar (a new comment from the comment box next to the selection).
async function setActive(id, from, keepSide) {
  S.active = id;
  document.querySelectorAll('.thread').forEach(c => c.classList.toggle('active', +c.dataset.t === id));
  paintEditor();
  const t = S.doc.threads.find(x => x.id === id);
  if (!t) return;
  if (from) {
    if (!keepSide && document.body.classList.contains('noside')) setSide(true);
    const card = document.querySelector(`.thread[data-t="${id}"]`);
    if (card) card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    return;
  }
  if (t.anchor && t.anchor.found) {
    setSrc(true);
    await openFile(t.anchor.file, t.anchor.line, false);
    syncToPdf(t.anchor.file, t.anchor.line);
  }
}

function threadOrder(ts) {
  return [...ts].sort((x, y) => (x.anchor ? 1 : 0) - (y.anchor ? 1 : 0) ||
    (x.anchor && y.anchor ? (x.anchor.file === y.anchor.file ? x.anchor.line - y.anchor.line
      : x.anchor.file < y.anchor.file ? -1 : 1) : 0) || x.id - y.id);
}

function renderThreads() {
  const box = $('threads'), ts = S.doc ? S.doc.threads : [];
  const nChange = ts.filter(t => t.change).length, nOpen = ts.filter(t => t.status === 'open').length;
  const nAns = ts.length - nChange - nOpen;
  $('counts').textContent = ts.length ? [nOpen && `${nOpen} open`, nAns && `${nAns} answered`, nChange && plural(nChange, 'change')]
    .filter(Boolean).join(' · ') : 'Comments';
  paintOracle();
  kit.tab({open: nOpen + nChange});
  const frag = document.createDocumentFragment();
  if (S.comp && S.comp.type === 'general') frag.append(composer('Comment'));
  $('resolveAllBtn').hidden = !ts.length;
  $('resolveAllBtn').textContent = `Resolve all (${ts.length})`;
  $('acceptAllBtn').hidden = $('rejectAllBtn').hidden = nChange < 2;
  if (!ts.length && !(S.comp && S.comp.type === 'general')) {
    frag.append(el('p', 'empty', 'Select text in the source or on a page to comment on it. Ask Claude in the chat ' +
      'for changes: they show here and in the source, to accept or reject.'));
  }
  for (const t of threadOrder(ts)) frag.append(threadCard(t));
  box.replaceChildren(frag);
  kit.typeset(box);
  const ta = box.querySelector('.composer textarea');
  if (ta && S.comp && S.comp.focus) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); S.comp.focus = false; }
  renderPop();
}

// The comment box of a new comment on selected text. It is next to the selection (in the source or on a page),
// so the sidebar can stay closed (as in pdfview). S.comp.at is its place: {pos} in the source, or {page, fx, fy}
// as fractions of the page box. The box follows a scroll or a zoom, and stays in its pane.
function renderPop() {
  const old = $('pop'), c = S.comp;
  if (!c || c.type !== 'new') { if (old) old.remove(); return; }
  if (old && old.comp === c) { placePop(); return; }  // keep the box as it is (the cursor, the focus)
  if (old) old.remove();
  const p = composer('Comment');
  p.id = 'pop';
  p.comp = c;
  document.body.append(p);
  placePop();
  const ta = p.querySelector('textarea');
  if (c.focus) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); c.focus = false; }
}

function placePop() {
  const p = $('pop'), c = S.comp;
  if (!p || !c || !c.at) return;
  let x, y, box;
  if (c.at.pos) {
    box = cm.getWrapperElement().getBoundingClientRect();
    const r = cm.cursorCoords(c.at.pos, 'window');
    x = r.left; y = r.bottom + 4;
  } else {
    box = $('pages').getBoundingClientRect();
    const pg = pv.pages[c.at.page - 1];
    if (!pg) return;
    const r = pg.el.getBoundingClientRect();
    x = r.left + c.at.fx * r.width; y = r.top + c.at.fy * r.height + 6;
  }
  p.style.left = Math.max(box.left + 4, Math.min(x, box.right - p.offsetWidth - 4)) + 'px';
  p.style.top = Math.max(box.top + 4, Math.min(y, box.bottom - p.offsetHeight - 4)) + 'px';
}
document.addEventListener('scroll', placePop, true);  // the source and the pages
window.addEventListener('resize', placePop);

function threadCard(t) {
  const card = el('div', 'thread' + (t.id === S.active ? ' active' : ''));
  card.dataset.t = t.id;
  const top = el('div', 'top'), a = t.anchor;
  if (a) top.append(el('span', 'where', `${a.file}:${a.line}`));
  if (t.change) top.append(el('span', 'sp'));
  else if (a) top.append(el('q', null, oneLine(a.quote)));
  else top.append(el('span', 'gen', '📄 General comment'));
  if (a && !a.found) top.append(el('span', 'pill lost', 'not found'));
  top.append(el('span', 'pill ' + t.status, t.change ? 'Change' : t.status === 'answered' ? 'Answered' : 'Open'));
  card.append(top);
  if (t.change) {  // the change as tracked changes: the words that go crossed out, the words that come in green
    const d = el('div', 'diff'), line = el('div');
    for (const o of wordDiff(t.change.old, t.change.new))
      line.append(o.k === 'eq' ? document.createTextNode(o.text) : el(o.k === 'del' ? 'del' : 'ins', null, o.text));
    if (!t.change.new) line.append(el('span', 'dnote', ' (delete)'));
    d.append(line);
    card.append(d);
  }
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
    if (t.change) acts.append(btn('✓ Accept', 'Put the new text into the source', () => acceptChange(t.id), 'yes'),
                              btn('✗ Reject', 'Remove this suggestion', () => rejectChange(t.id), 'no'));
    acts.append(btn('Reply', '', () => compose({type: 'reply', thread: t.id})));
    if (!t.change) acts.append(btn('Resolve', 'Remove this comment', () => op({op: 'resolve', thread: t.id}, 'Comment resolved.')));
    card.append(acts);
  }
  card.addEventListener('click', ev => {
    if (ev.target.closest('button, textarea, a')) return;
    setActive(t.id);
  });
  return card;
}

function composer(label) {
  const c = el('div', 'composer'), ta = el('textarea');
  if (S.comp.type === 'new') c.append(el('div', 'cwhere', S.comp.where));
  ta.value = S.comp.text || '';
  ta.placeholder = 'Write a comment… Maths in $…$ is typeset.';
  ta.addEventListener('input', () => { S.comp.text = ta.value; });
  ta.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); save(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
  });
  ta.addEventListener('click', ev => ev.stopPropagation());
  const row = el('div', 'row');
  row.append(el('span', 'hint', '⌘/Ctrl+Enter saves'), btn('Cancel', 'Esc', cancel), btn(label, '', () => save(), 'on'));
  if (S.comp.type === 'new')  // the box next to the selection: save, and start the Oracle at once
    row.append(btn('Send', 'Save the comment and start the Oracle to answer the open comments', () => save(true), 'send'));
  c.append(ta, row);
  return c;
}

function compose(c) {
  S.comp = {...c, text: c.text || '', focus: true};
  if (c.type === 'new') { renderThreads(); return; }  // the box is next to the selection (renderPop)
  if (document.body.classList.contains('noside')) setSide(true);
  renderThreads();
  const box = document.querySelector('#threads .composer');
  if (box) box.scrollIntoView({block: 'nearest'});
}

function cancel() { S.comp = null; renderThreads(); }

async function save(send) {
  const c = S.comp;
  if (!c || !c.text.trim()) { toast('Write a comment first.'); return; }
  let body;
  if (c.type === 'new') {
    if (isDirty(S.files[c.anchor.file]) && !await saveAll()) return;  // the server finds the words in the saved file
    body = {op: 'add', anchor: c.anchor, text: c.text};
  } else if (c.type === 'general') body = {op: 'add', text: c.text};
  else if (c.type === 'reply') body = {op: 'reply', thread: c.thread, text: c.text};
  else body = {op: 'edit', thread: c.thread, msg: c.msg, text: c.text};
  S.comp = null;
  const d = await op(body);
  if (!d) { S.comp = c; renderThreads(); return; }
  if (d.added) setActive(d.added, 'page', c.type === 'new');
  if (!send) return;
  if (oracle.running) toast('The comment is saved. Claude is already working: the next Oracle run answers it.');
  else oracleCall('start');
}

// A comment on the selected text: in the source, or on a page (then on its source line), or on the whole document.
async function commentOnSelection() {
  const sel = cm.getSelection();
  if (S.cur && sel.trim() && cm.hasFocus()) {
    const doc = cm.getDoc(), text = doc.getValue();
    const a = doc.indexFromPos(cm.getCursor('from')), b = doc.indexFromPos(cm.getCursor('to'));
    compose({type: 'new', where: `${S.cur}:${cm.getCursor('from').line + 1} “${oneLine(sel).slice(0, 80)}”`,
             at: {pos: cm.getCursor('to')},
             anchor: {file: S.cur, line: cm.getCursor('from').line + 1, quote: text.slice(a, b),
                      prefix: text.slice(Math.max(0, a - CONTEXT), a), suffix: text.slice(b, b + CONTEXT)}});
    return;
  }
  const ps = pdfSelection();
  if (ps) {
    $('selbar').style.display = 'none';
    const rects = getSelection().getRangeAt(0).getClientRects(), last = rects[rects.length - 1];
    const pb = pv.pages[ps.n - 1].el.getBoundingClientRect();
    const at = {page: ps.n, fx: last ? (last.left - pb.left) / pb.width : 0, fy: last ? (last.bottom - pb.top) / pb.height : 0};
    const r = await sourceAt(ps);
    if (!r || !r.file) { toast('No source line for this place.'); return; }
    getSelection().removeAllRanges();
    await openFile(r.file, r.line, true);
    compose({type: 'new', where: `${r.file}:${r.line} (page ${ps.n}: “${oneLine(ps.quote).slice(0, 60)}”)`,
             at, anchor: {file: r.file, line: r.line}});
    return;
  }
  compose({type: 'general'});
}
$('commentBtn').onclick = commentOnSelection;

function setSide(on) {
  document.body.classList.toggle('noside', !on);
  $('sideBtn').classList.toggle('on', on);
  $('sideBtn').title = (on ? 'Hide' : 'Show') + ' the comments (⌘/Ctrl+D or ⌘/Ctrl+\\)';
  store('side', on ? 'on' : 'off');
  setTimeout(() => pv.relayout(), 0);
}

// The contents of the PDF (its outline, from the sections when the document uses hyperref): a panel left of the pages.
function setNav(on) {
  document.body.classList.toggle('nonav', !on);
  $('navBtn').classList.toggle('on', on);
  store('nav', on ? 'on' : 'off');
  setTimeout(() => pv.relayout(), 0);
}

// ------------------------------------------------------------ a selection on a page: Comment, Source, and the chat
let chatSel = null;  // {quote, where, short}: the text that was last selected (source or page), for the chat

function setChatSel(s) {
  const same = (chatSel && s && chatSel.quote === s.quote && chatSel.where === s.where) || (!chatSel && !s);
  chatSel = s;
  if (!same) chat.renderCtx();
}

// The selected text on a page, and the place where it starts (PDF points).
function pdfSelection() {
  const s = getSelection();
  if (!s.rangeCount || s.isCollapsed) return null;
  const r = s.getRangeAt(0);
  const start = r.startContainer.nodeType === 3 ? r.startContainer.parentElement : r.startContainer;
  const pg = pv.pageOf(start);
  if (!pg || !pg.textDiv || !pg.textDiv.contains(r.startContainer)) return null;
  const quote = oneLine(s.toString());
  if (!quote) return null;
  const rect = [...r.getClientRects()].find(x => x.width >= 1 && x.height >= 1);
  if (!rect) return null;
  const pt = pv.pointOf({target: start, clientX: rect.left + 2, clientY: rect.top + rect.height / 2});
  return pt && {...pt, quote};
}

let selTimer = 0;
document.addEventListener('selectionchange', () => {
  const ps = pdfSelection(), b = $('selbar');
  if (!ps) { b.style.display = 'none'; return; }
  const rects = getSelection().getRangeAt(0).getClientRects();
  const last = rects[rects.length - 1];
  if (last) {
    b.style.display = 'flex';
    b.style.left = Math.min(window.innerWidth - b.offsetWidth - 8, last.right + 6) + 'px';
    b.style.top = Math.max(48, last.bottom + 4) + 'px';
  }
  setChatSel({quote: ps.quote.slice(0, 3000), where: `page ${ps.n} of the PDF`, short: `p. ${ps.n}`});
  clearTimeout(selTimer);
  selTimer = setTimeout(async () => {  // the source line of the selection, for Claude
    const r = await api('GET', `/api/sync/edit?f=${enc(FILE)}&page=${ps.n}&x=${ps.x.toFixed(1)}&y=${ps.y.toFixed(1)}`).catch(() => null);
    if (r && r.file && chatSel && chatSel.quote === ps.quote.slice(0, 3000))
      setChatSel({...chatSel, where: `page ${ps.n} of the PDF, from ${r.file} near line ${r.line}`});
  }, 300);
});
for (const id of ['cbtn', 'srcbtn']) $(id).addEventListener('mousedown', ev => ev.preventDefault());  // keep the selection
$('cbtn').addEventListener('click', commentOnSelection);
$('srcbtn').addEventListener('click', () => {
  const ps = pdfSelection();
  $('selbar').style.display = 'none';
  if (ps) syncToSource(ps);
});

// ------------------------------------------------------------ chat
const chat = kit.chatPanel({
  key: 'texview',
  account: {get: () => api('GET', '/api/account?f=' + encodeURIComponent(FILE)),
            post: body => api('POST', '/api/account', body)},
  poll: true,
  skip: '.CodeMirror',
  ready: () => !!S.doc,
  empty: 'Ask about this document. Claude reads the sources and the PDF. When you ask for a change, it suggests it: ' +
    'the change shows in the source and in the sidebar, and you accept or reject it. Claude never edits the files. ' +
    'Turn on "Ask only" if it must not suggest or comment.',
  get: since => api('GET', `/api/chat?f=${enc(FILE)}` + (since == null ? '' : `&since=${since}`)),
  post: body => api('POST', '/api/chat', body),
  extra: () => ({quote: chatSel ? chatSel.quote : '', where: chatSel ? chatSel.where : '',
                 cursor: S.cur ? `${S.cur} line ${cm.getCursor().line + 1}` : '', view_page: pv.curPage}),
  afterSend: () => { chatSel = null; },
  context: () => chatSel && {text: `${chatSel.short} “${oneLine(chatSel.quote).slice(0, 80)}”`, title: chatSel.quote},
  hint: () => 'Select text in the source or on a page to ask about it.',
  clearContext: () => { chatSel = null; },
  render: kit.md,
  typeset: box => kit.typeset(box),
  fillUser: (e, m) => {
    if (m.quote) { const q = el('div', 'cq', '“' + m.quote + '”'); q.title = (m.where ? m.where + ': ' : '') + m.quote; e.append(q); }
    e.append(m.text);
  },
  done: m => {
    if (m && m.changed) {
      loadDoc().then(() => {
        const n = S.doc.threads.filter(t => t.change).length;
        toast(n ? `Claude suggested changes: ${n} wait${n === 1 ? 's' : ''} for you.` : 'Claude changed the comments.', 'Undo', undo, 15000);
      });
    }
  },
  error: text => toast(text, null, null, 9000),
});

// ------------------------------------------------------------ notes to the app (self-improving apps)
kit.improveDash({
  app: 'texview',
  get: () => api('GET', `/api/improve/dash?f=${enc(FILE)}`),
  post: body => api('POST', '/api/improve/dash', body),
  context: () => ({
    page: pv.curPage, pages: pv.pages.length, zoom: pv.zoom === 'fit' ? 'fit' : pv.scale, dark: !!S.dark,
    source: !document.body.classList.contains('nosrc'), sidebar: !document.body.classList.contains('noside'),
    contents: !document.body.classList.contains('nonav'),
    files: Object.keys(S.files).length, unsaved: Object.values(S.files).filter(isDirty).length,
    comments: S.doc ? S.doc.threads.length : 0, build: S.build ? S.build.status : null,
    errors: S.build ? (S.build.errors || []).length : 0, composing: S.comp ? S.comp.type : null,
    chat: !$('chatBox').hidden, oracle: !!(oracle && oracle.running),
  }),
  onDone: (msg, label, fn) => toast(msg, label || null, fn || null, 12000),
});
kit.previewBand({
  get: () => api('GET', `/api/improve/preview?f=${enc(FILE)}`),
  post: body => api('POST', '/api/improve/preview', {...body, f: FILE}),
});

// ------------------------------------------------------------ the Oracle
// One background Claude run per document answers all its open comments (/answer-tex). Its replies and suggested
// changes show in the sidebar as the comments file changes.
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
  if (d.already) toast('Claude is already working on this document.');
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
    : open ? `Start Claude in the background to answer the ${plural(open, 'open comment')}` +
      (oracle.session ? ', and continue its session on this document.' : ', in a new session.')
    : 'No open comments.';
  $('oracleRow').hidden = !oracle.session || oracle.running;
}

$('oracleBtn').onclick = () => {
  if (!oracle.running) oracleCall('start');
  else if (confirm('Stop Claude? The replies that it already wrote stay.')) oracleCall('stop');
};
$('newSession').onclick = () => {
  if (confirm('Start a new Claude session for this document? The next Oracle run does not remember earlier rounds.'))
    oracleCall('new').then(ok => ok && toast('The next Oracle run starts a new session.'));
};

// ------------------------------------------------------------ controls and keys
$('zoomIn').onclick = () => setZoom(Math.round(pv.scale * 1.2 * 100) / 100);
$('zoomOut').onclick = () => setZoom(Math.round(pv.scale / 1.2 * 100) / 100);
$('zoomFit').onclick = () => setZoom('fit');
$('darkBtn').onclick = () => setDark(!S.dark);
$('undoBtn').onclick = undo;
$('sideBtn').onclick = () => setSide(document.body.classList.contains('noside'));
$('srcBtn').onclick = () => setSrc(document.body.classList.contains('nosrc'));
$('navBtn').onclick = () => setNav(document.body.classList.contains('nonav'));
$('resolveAllBtn').onclick = () => {
  const n = S.doc ? S.doc.threads.length : 0;
  if (n && confirm(`Resolve all ${n}? The comments and the suggested changes are removed (Undo brings them back).`))
    op({op: 'resolve-all'}, 'All resolved.');
};

// The keys of texview come before the keys of the kit. ⌘/Ctrl+S saves in the source editor. In all other places, the kit
// uses it for the chat, as in the other viewers (the option skip: '.CodeMirror' of the chat panel). On a Mac, the Control key
// is not ⌘, so Control+S is free: it shows or hides the source. On other systems, Ctrl is the ⌘ key, so Ctrl+S does not change.
addEventListener('keydown', ev => {
  const mod = ev.metaKey || ev.ctrlKey;
  const ctrlS = ev.ctrlKey && !ev.metaKey && !ev.altKey && !ev.shiftKey && ev.code === 'KeyS' && /Mac/.test(navigator.platform);
  if (ctrlS || (ev.altKey && !mod && ev.code === 'Tab')) {  // show or hide the source, also from inside the editor
    ev.preventDefault();
    ev.stopPropagation();
    const on = document.body.classList.contains('nosrc');
    if (!on && $('src').contains(document.activeElement)) document.activeElement.blur();
    setSrc(on);
    return;
  }
  if (!mod || ev.altKey) return;
  const k = ev.key.toLowerCase();
  // ⌘/Ctrl+D and ⌘/Ctrl+E (ev.code: any keyboard layout). On a Mac, Ctrl+D and Ctrl+E stay the text keys in a text box and in the editor.
  const panel = ev.shiftKey || (ev.ctrlKey && !ev.metaKey && /Mac/.test(navigator.platform) && typing(ev.target)) ? '' : ev.code;
  if (k === 's' && !ev.shiftKey && ev.target.closest && ev.target.closest('.CodeMirror')) { ev.preventDefault(); saveAll(); }
  else if (k === 'b' && ev.shiftKey) { ev.preventDefault(); compileNow(); }
  else if (k === 'm' && ev.shiftKey) { ev.preventDefault(); commentOnSelection(); }
  else if (ev.key === '\\' || panel === 'KeyD') { ev.preventDefault(); setSide(document.body.classList.contains('noside')); }
  else if (panel === 'KeyE') { ev.preventDefault(); setNav(document.body.classList.contains('nonav')); }
  else if (!typing(ev.target)) pv.navKey(ev);  // ⌘/Ctrl+[ and ⌘/Ctrl+]: back and forward (in the editor, the indent)
}, true);

document.addEventListener('keydown', ev => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey || typing(ev.target)) return;
  if (ev.key === '+' || ev.key === '=') setZoom(Math.round(pv.scale * 1.2 * 100) / 100);
  else if (ev.key === '-') setZoom(Math.round(pv.scale / 1.2 * 100) / 100);
  else if (ev.key === '0') setZoom('fit');
  else if (ev.key === 'd') setDark(!S.dark);
  else if (ev.key === 'e') setSrc(document.body.classList.contains('nosrc'));
  else if (ev.key === 'o') setNav(document.body.classList.contains('nonav'));
  else if (ev.key === 'Escape' && S.comp) cancel();
  else return;
  ev.preventDefault();
});

addEventListener('beforeunload', ev => {
  if (Object.values(S.files).some(isDirty)) { ev.preventDefault(); ev.returnValue = ''; }
});

// ------------------------------------------------------------ news from the server
// The compile, the comments file, and the file in the editor can change outside this page: look every second.
let polling = false;
async function poll() {
  if (polling || !S.doc || document.hidden) return;
  polling = true;
  try {
    const v = await api('GET', `/api/state?f=${enc(FILE)}` + (S.cur ? `&src=${enc(S.cur)}` : ''));
    $('live').classList.remove('off');
    setBuild(v.build);
    if (v.version !== S.doc.version) await loadDoc();
    const f = S.files[S.cur];
    if (f && v.src && v.src !== f.base) {
      if (!isDirty(f)) await reloadFile(S.cur);
      else if (!f.warned) {
        f.warned = true;
        toast(`${S.cur} changed on disk. Your unsaved changes stay. Save keeps your version.`, 'Take the disk version', async () => {
          f.gen = f.doc.changeGeneration(true);  // forget the changes
          f.base = '';
          f.doc.markClean();
          await reloadFile(S.cur);
        }, 15000);
      }
    }
  } catch (e) { $('live').classList.add('off'); }
  finally { polling = false; }
}

// ------------------------------------------------------------ start
(async () => {
  kit.tab({app: 'tex'});
  setDark((stored('dark') ?? (matchMedia('(prefers-color-scheme: dark)').matches ? '1' : '0')) === '1');
  setSide(stored('side') !== 'off');
  setSrc(stored('src') !== 'off');
  setNav(stored('nav') === 'on');
  const z = stored('zoom');
  pv.zoom = z && z !== 'fit' && !isNaN(+z) ? +z : 'fit';
  if (!FILE) { $('pages').replaceChildren(el('div', 'err-page', 'No file. Open one with: texview main.tex')); return; }
  if (!await loadDoc()) return;
  const want = stored('file.' + FILE);
  const first = S.doc.sources.find(s => s.path === want) ? want : (S.doc.sources[0] || {}).path;
  if (first) await openFile(first);
  else $('editor').append(el('div', 'srcempty', 'No source file.'));
  oracleCall();
  setInterval(poll, 1000);
})();
