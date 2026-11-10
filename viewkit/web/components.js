'use strict';
// viewkit: the components of the viewers that show data (pageview, plateview). One global, PV.
// pageview: PV.enhance(root, ctx) turns the blocks that pandoc writes (fenced divs with classes and data-* attributes)
//   into tables, grids, charts, figures, tabs, toggles, columns, callouts, stat cards, code with Copy, and widgets.
//   pageview/SYNTAX.md describes the blocks.
// plateview: PV.make(kind, host, data, opts, ctx) makes one table, grid or chart from an options object, with the same
//   option names as the blocks. The styles are in components.css.
// Generated parts carry the class pv-gen (their text is not used for text comments), and buttons carry pv-ui.

const PV = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function sv(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.append(e);
    return e;
  }
  function button(label, title, fn, cls) {
    const b = el('button', 'pv-ui' + (cls ? ' ' + cls : ''), label);
    b.type = 'button';
    if (title) b.title = title;
    b.addEventListener('click', ev => { ev.stopPropagation(); fn(ev, b); });
    return b;
  }
  // An option of a block: pandoc writes most attributes as data-NAME, and a few (src, title, height, width) as they are.
  const opt = (e, k) => e.getAttribute('data-' + k) ?? e.getAttribute(k);
  const optOn = (e, k) => { const v = opt(e, k); return v != null && v !== 'false' && v !== '0'; };
  // "a:max b:min" -> {a: 'max', b: 'min'}. Names may hold spaces when they are quoted: "Test loss":min.
  function pairs(s) {
    const out = {};
    if (!s) return out;
    const re = /(?:"([^"]+)"|([^\s:]+)):(\S+)/g;
    let m;
    while ((m = re.exec(s))) out[m[1] ?? m[2]] = m[3];
    return out;
  }
  // "a: Label one | b: Label two" -> {a: 'Label one', b: 'Label two'}
  function labels(s) {
    const out = {};
    for (const part of (s || '').split('|')) {
      const i = part.indexOf(':');
      if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
    }
    return out;
  }
  const list = s => (s || '').split(',').map(x => x.trim()).filter(Boolean);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let CTX = {};

  function toast(m) { if (CTX.toast) CTX.toast(m); }
  function copy(text, what) {
    const ok = () => toast('Copied ' + what + '.');
    try {
      navigator.clipboard.writeText(text).then(ok, () => fallbackCopy(text, what));
    } catch (e) { fallbackCopy(text, what); }
  }
  function fallbackCopy(text, what) {
    const ta = el('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;left:0;top:0';
    document.body.append(ta);
    ta.select();
    let done = false;
    try { done = document.execCommand('copy'); } catch (e) { /* none */ }
    ta.remove();
    toast(done ? 'Copied ' + what + '.' : 'The browser does not allow Copy here.');
  }

  // ------------------------------------------------------------ numbers
  const NUM = /^\s*([+-]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(%?)\s*(?:(?:±|\+-|\+\/-)\s*((?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*%?)?\s*$/;
  // A cell of a Markdown table: {v: the sort value, num, sd, html, text}
  function parseCell(text, htmlText) {
    const t = text.replace(/[\u00a0\u2009\u202f]/g, ' ').trim(), m = NUM.exec(t);
    if (m) {
      const num = parseFloat(m[1].replace(/,/g, '')), sd = m[3] != null ? parseFloat(m[3].replace(/,/g, '')) : null;
      return {v: num, num, sd, html: htmlText, text: t};
    }
    return {v: t.toLowerCase(), num: null, sd: null, html: htmlText, text: t};
  }
  function fmtNum(x, dec) {
    if (x == null || typeof x !== 'number' || !isFinite(x)) return x == null ? '' : String(x);
    if (dec != null) return x.toFixed(dec);
    if (Number.isInteger(x)) return String(x);
    return String(parseFloat(x.toPrecision(6)));
  }
  // The decimals of a column: the most that its values show, at most 4.
  // Values over more than two orders of magnitude (a learning rate) keep their own decimals: null.
  function autoDec(vals) {
    const abs = vals.filter(v => typeof v === 'number' && isFinite(v) && v !== 0).map(Math.abs);
    if (abs.length && Math.max(...abs) / Math.min(...abs) >= 100) return null;
    let d = 0;
    for (const v of vals) {
      if (typeof v !== 'number' || !isFinite(v) || Number.isInteger(v)) continue;
      const s = String(parseFloat(v.toPrecision(6)));
      if (s.includes('e')) return null;
      d = Math.max(d, (s.split('.')[1] || '').length);
    }
    return Math.min(d, 4);
  }

  // ------------------------------------------------------------ the table component
  // model: {key, columns: [{k, label, num, dec}], rows: [{key, cells: {k: cell}}]}, cell: {v, num, sd, html, text}
  // o: {best: {k: 'max'|'min'}, sort: [k, dir], height, caption (node), wide (bool), full (bool), title}
  function tableWidget(host, model, o) {
    const T = {sort: o.sort ? o.sort[0] : null, dir: o.sort ? o.sort[1] : 1, hidden: new Set(o.hidden || []),
               filter: '', sel: null};
    const box = el('div', 'pv-table pv-gen'), bar = el('div', 'tbar pv-ui'), wrap = el('div', 'twrap');
    box.dataset.table = model.key;
    const table = el('table'), thead = el('thead'), tbody = el('tbody');
    table.append(thead, tbody);
    wrap.append(table);
    if (o.height) wrap.style.maxHeight = o.height + 'px';
    const ttl = el('span', 'ttl', o.title || '');
    const filter = el('input');
    filter.type = 'search'; filter.placeholder = 'Filter rows…'; filter.setAttribute('aria-label', 'Filter rows');
    filter.addEventListener('input', () => { T.filter = filter.value; render(); });
    const grow = el('span', 'grow');
    const colsBtn = button('Columns ▾', 'Show or hide columns', (ev, b) => columnsMenu(b));
    const wideBtn = button('⤢ Wide', 'Use the full width of the page', () => {
      const on = host.classList.toggle('wide'); wideBtn.classList.toggle('on', on);
    });
    const fullBtn = button('⛶', 'Full window (Esc to close)', () => fullWindow(box, host));
    const collBtn = button('▾', 'Collapse the table', () => {
      const on = box.classList.toggle('collapsed'); collBtn.textContent = on ? '▸' : '▾';
    });
    bar.append(ttl, filter, grow, colsBtn);
    if (!o.full) bar.append(wideBtn, fullBtn);
    bar.append(button('LaTeX', 'Copy the table as LaTeX', () => copy(exportAs('latex'), 'the table as LaTeX')),
               button('MD', 'Copy the table as Markdown', () => copy(exportAs('md'), 'the table as Markdown')),
               button('CSV', 'Copy the table as CSV', () => copy(exportAs('csv'), 'the table as CSV')));
    if (!o.full) bar.append(collBtn);
    box.append(bar, wrap);
    if (o.caption) { const c = el('div', 'tcap'); c.append(o.caption); box.append(c); }
    if (o.wide) { host.classList.add('wide'); wideBtn.classList.add('on'); }

    const cols = () => model.columns.filter(c => !T.hidden.has(c.k));
    function rowsNow() {
      const f = T.filter.toLowerCase();
      let rs = model.rows.filter(r => !f || Object.values(r.cells).some(c => c && String(c.text).toLowerCase().includes(f)));
      if (T.sort != null) {
        rs = [...rs].sort((a, b) => {
          const x = a.cells[T.sort], y = b.cells[T.sort];
          const xv = x ? x.v : null, yv = y ? y.v : null;
          if (xv === yv) return 0;
          if (xv == null || xv === '') return 1;
          if (yv == null || yv === '') return -1;
          if (typeof xv !== typeof yv) return (typeof xv === 'number' ? -1 : 1);
          return (xv < yv ? -1 : 1) * T.dir;
        });
      }
      return rs;
    }
    function bests(rs) {
      const out = {};
      for (const [k, how] of Object.entries(o.best || {})) {
        const vs = rs.map(r => r.cells[k]).filter(c => c && c.num != null).map(c => c.num);
        if (vs.length) out[k] = how === 'min' ? Math.min(...vs) : Math.max(...vs);
      }
      return out;
    }
    function cellHTML(c, col) {
      if (!c) return '';
      if (c.fromData) {
        if (c.num == null) return esc(c.text);
        return esc(fmtNum(c.num, col.dec)) + (c.sd != null ? `<span class="sd"> ± ${esc(fmtNum(c.sd, col.sdDec ?? col.dec))}</span>` : '');
      }
      return c.html;
    }
    function render() {
      const cs = cols(), rs = rowsNow(), b = bests(rs);
      thead.replaceChildren(); tbody.replaceChildren();
      const tr = el('tr');
      for (const c of cs) {
        const th = el('th', c.num ? 'num' : '');
        th.tabIndex = 0;
        th.innerHTML = c.labelHTML || esc(c.label);
        if (o.best && o.best[c.k]) {
          const h = el('span', 'hint', o.best[c.k] === 'min' ? '↓' : '↑');
          h.title = o.best[c.k] === 'min' ? 'Lower is better' : 'Higher is better';
          th.append(h);
        }
        if (T.sort === c.k) th.append(el('span', 'dir', T.dir > 0 ? '▲' : '▼'));
        const go = () => {
          if (T.sort === c.k) T.dir = -T.dir;
          else { T.sort = c.k; T.dir = (o.best && o.best[c.k] === 'max') || (c.num && !(o.best && o.best[c.k])) ? -1 : 1; }
          render();
        };
        th.addEventListener('click', go);
        th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        tr.append(th);
      }
      thead.append(tr);
      for (const r of rs) {
        const row = el('tr');
        for (const c of cs) {
          const cell = r.cells[c.k], td = el('td', c.num ? 'num' : '');
          td.innerHTML = cellHTML(cell, c);
          if (cell && c.k in b && cell.num === b[c.k]) td.classList.add('best');
          td.dataset.row = r.key; td.dataset.col = c.k;
          const anchor = {table: model.key, row: r.key, col: c.k};
          if (CTX.cellThreads && CTX.cellThreads(anchor).length) td.classList.add('cmt');
          if (T.sel && T.sel.row === r.key && T.sel.col === c.k) td.classList.add('sel');
          td.addEventListener('click', ev => {
            if (ev.target.closest('a')) return;
            T.sel = {row: r.key, col: c.k};
            box.querySelectorAll('td.sel').forEach(x => x.classList.remove('sel'));
            td.classList.add('sel');
            if (CTX.cellClick) CTX.cellClick(anchor, td, `${r.key} · ${c.label}: ${cell ? cell.text : ''}`);
          });
          row.append(td);
        }
        tbody.append(row);
      }
      if (!rs.length) {
        const td = el('td', 'empty', model.rows.length ? 'No rows match the filter.' : 'The table is empty.');
        td.colSpan = cs.length || 1;
        const row = el('tr'); row.append(td); tbody.append(row);
      }
      ttl.textContent = o.title || '';
      ttl.title = `${rs.length} of ${model.rows.length} rows`;
    }
    let menu = null;
    function columnsMenu(b) {
      if (menu) { menu.remove(); menu = null; return; }
      menu = el('div', 'colmenu pv-ui');
      for (const c of model.columns) {
        const l = el('label'), cb = el('input');
        cb.type = 'checkbox'; cb.checked = !T.hidden.has(c.k);
        cb.addEventListener('change', () => { if (cb.checked) T.hidden.delete(c.k); else T.hidden.add(c.k); render(); });
        l.append(cb, ' ' + c.label);
        menu.append(l);
      }
      menu.addEventListener('click', ev => ev.stopPropagation());
      const r = b.getBoundingClientRect(), pr = box.getBoundingClientRect();
      menu.style.left = Math.max(0, Math.min(r.left - pr.left, pr.width - 200)) + 'px';
      menu.style.top = (r.bottom - pr.top + 4) + 'px';
      box.append(menu);
      setTimeout(() => document.addEventListener('click', function off() { if (menu) menu.remove(); menu = null; document.removeEventListener('click', off); }), 0);
    }
    function exportAs(kind) {
      const cs = cols(), rs = rowsNow(), b = bests(rs);
      const txt = (c, col, tex) => {
        if (!c) return '';
        let s;
        if (c.num != null) {
          const m = c.fromData ? fmtNum(c.num, col.dec) : c.text.split(/±|\+-|\+\/-/)[0].trim().replace(/%$/, '').trim();
          const sd = c.sd != null ? (c.fromData ? fmtNum(c.sd, col.sdDec ?? col.dec) : String(c.sd)) : null;
          s = sd != null ? (tex ? `$${m} \\pm ${sd}$` : `${m} ± ${sd}`) : m;
        } else s = tex ? c.text.replace(/([%&#_$])/g, '\\$1') : c.text;
        if (col.k in b && c.num === b[col.k]) s = tex ? `\\textbf{${s}}` : (kind === 'md' ? `**${s}**` : s);
        return s;
      };
      if (kind === 'csv') {
        const q = s => /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        return [cs.map(c => q(c.label)).join(','), ...rs.map(r => cs.map(c => q(txt(r.cells[c.k], c, false))).join(','))].join('\n');
      }
      if (kind === 'md') {
        return ['| ' + cs.map(c => c.label).join(' | ') + ' |', '|' + cs.map(c => c.num ? '---:' : '---').join('|') + '|',
          ...rs.map(r => '| ' + cs.map(c => txt(r.cells[c.k], c, false).replace(/\|/g, '\\|')).join(' | ') + ' |')].join('\n');
      }
      return ['\\begin{tabular}{' + cs.map(c => c.num ? 'r' : 'l').join('') + '}', '\\toprule',
        cs.map(c => c.label.replace(/([%&#_$])/g, '\\$1')).join(' & ') + ' \\\\', '\\midrule',
        ...rs.map(r => cs.map(c => txt(r.cells[c.k], c, true)).join(' & ') + ' \\\\'), '\\bottomrule', '\\end{tabular}'].join('\n');
    }
    box.pvRender = render;
    box.pvFind = a => [...tbody.querySelectorAll('td')].find(td => td.dataset.row === a.row && td.dataset.col === a.col) || null;
    render();
    return box;
  }

  // A full-window view of a component, over the page. Esc or ✕ puts it back.
  function fullWindow(node, host) {
    const ov = el('div', 'pv-full pv-ui'), head = el('div', 'pv-fullbar');
    const place = document.createComment('pv-full');
    node.replaceWith(place);
    head.append(el('span', 'grow'), button('✕ Close', 'Close the full window (Esc)', close));
    ov.append(head, node);
    node.classList.add('isfull');
    document.body.append(ov);
    document.body.classList.add('pv-hasfull');
    function close() {
      node.classList.remove('isfull');
      place.replaceWith(node);
      ov.remove();
      document.body.classList.remove('pv-hasfull');
      removeEventListener('keydown', key, true);
    }
    function key(e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }
    addEventListener('keydown', key, true);
  }

  // A Markdown table (HTML from pandoc) as a model.
  function modelOfTable(t, key) {
    const heads = [...t.querySelectorAll('thead th')];
    let bodyRows = [...t.querySelectorAll('tbody tr')];
    let names = heads.map(h => h.textContent.trim());
    if (!heads.length && bodyRows.length) { names = [...bodyRows[0].cells].map((_, i) => 'Column ' + (i + 1)); }
    const seen = {};
    const columns = names.map((n, i) => {
      let k = n || 'Column ' + (i + 1);
      if (seen[k]) k += ' (' + (++seen[k]) + ')'; else seen[k] = 1;
      return {k, label: n || k, labelHTML: heads[i] ? heads[i].innerHTML : null};
    });
    const rows = bodyRows.map((tr, ri) => {
      const cells = {};
      [...tr.cells].forEach((td, i) => { if (columns[i]) cells[columns[i].k] = parseCell(td.textContent, td.innerHTML); });
      const first = cells[columns[0] && columns[0].k];
      return {key: first && first.text ? first.text : String(ri + 1), cells};
    });
    for (const c of columns) {
      const vs = rows.map(r => r.cells[c.k]).filter(x => x && x.text !== '');
      c.num = vs.length > 0 && vs.every(x => x.num != null);
    }
    return {key, columns, rows};
  }

  // A data file as a model, with the options of a .table block.
  function modelOfData(d, e, key) {
    const want = list(opt(e, 'cols')), lab = labels(opt(e, 'labels')), sd = pairs(opt(e, 'sd')), dec = pairs(opt(e, 'dec'));
    const sdCols = new Set(Object.values(sd));
    const idx = {};
    d.columns.forEach((c, i) => { idx[c] = i; });
    let names = want.length ? want.filter(c => c in idx) : d.columns.filter(c => !sdCols.has(c));
    const keyCol = opt(e, 'key') && opt(e, 'key') in idx ? opt(e, 'key') : (names[0] ?? d.columns[0]);
    const columns = names.map(c => {
      const vals = d.rows.map(r => r[idx[c]]);
      const num = vals.filter(v => v != null && v !== '').every(v => typeof v === 'number');
      const sdVals = sd[c] && sd[c] in idx ? d.rows.map(r => r[idx[sd[c]]]) : [];
      return {k: c, label: lab[c] || c, num, dec: dec[c] != null ? +dec[c] : (num ? autoDec(vals) : null),
              sdDec: dec[c] != null ? +dec[c] : autoDec(sdVals), sdOf: sd[c]};
    });
    const rows = d.rows.map((r, ri) => {
      const cells = {};
      for (const c of columns) {
        const v = r[idx[c.k]];
        const s = c.sdOf && c.sdOf in idx ? r[idx[c.sdOf]] : null;
        if (typeof v === 'number') cells[c.k] = {v, num: v, sd: typeof s === 'number' ? s : null, text: fmtNum(v, c.dec) + (typeof s === 'number' ? ' ± ' + fmtNum(s, c.sdDec) : ''), fromData: true};
        else cells[c.k] = {v: v == null ? null : String(v).toLowerCase(), num: null, sd: null, text: v == null ? '' : String(v), fromData: true};
      }
      const kv = r[idx[keyCol]];
      return {key: kv != null && kv !== '' ? String(kv) : String(ri + 1), cells};
    });
    return {key, columns, rows};
  }

  function tableOptions(e, model) {
    const best = {};
    const findCol = k => (model.columns.find(c => c.k === k) || model.columns.find(c => c.label === k) || {}).k;
    for (const [k, v] of Object.entries(pairs(opt(e, 'best')))) { const c = findCol(k); if (c) best[c] = v === 'min' ? 'min' : 'max'; }
    let sort = null;
    const s = opt(e, 'sort');
    if (s) { const [k, d] = s.split(':'); const c = findCol(k); if (c) sort = [c, d === 'desc' ? -1 : 1]; }
    return {best, sort, height: +(opt(e, 'height') || 520)};
  }

  function captionOf(e) {
    const frag = document.createDocumentFragment();
    for (const c of [...e.childNodes]) if (!(c.nodeType === 1 && c.tagName === 'TABLE')) frag.append(c);
    const strong = frag.querySelector && frag.querySelector('strong');
    const hasText = [...frag.childNodes].some(n => n.textContent.trim());
    return {node: hasText ? frag : null, title: strong ? strong.textContent.replace(/\.$/, '') : ''};
  }

  // ------------------------------------------------------------ the grid (pivot) component
  function pivotWidget(host, d, e, key, full) {
    const idx = {};
    d.columns.forEach((c, i) => { idx[c] = i; });
    const need = ['rows', 'columns', 'value'].map(k => opt(e, k));
    const miss = need.filter(c => !(c in idx));
    if (miss.length) return errorBox(`The grid needs the columns ${miss.join(', ')} in ${opt(e, 'src')}.`);
    const [rk, ck, vk] = need, sk = opt(e, 'sd'), rg = opt(e, 'row-groups'), cg = opt(e, 'col-groups');
    const info = labels(opt(e, 'row-info'));
    const ent = (k, gk) => {
      const out = [], seen = new Map();
      for (const r of d.rows) {
        const name = r[idx[k]];
        if (name == null || seen.has(String(name))) continue;
        const o = {key: String(name), name: String(name), group: gk && gk in idx ? String(r[idx[gk]] ?? '') : '', info: {}};
        for (const ik of Object.keys(info)) if (ik in idx) o.info[ik] = r[idx[ik]];
        seen.set(o.key, o); out.push(o);
      }
      return out;
    };
    const ROWS = ent(rk, rg), COLS = ent(ck, cg), CELL = {};
    for (const r of d.rows) {
      const v = r[idx[vk]];
      if (typeof v !== 'number') continue;
      CELL[String(r[idx[rk]]) + '\u0001' + String(r[idx[ck]])] = {mean: v, sd: sk && sk in idx && typeof r[idx[sk]] === 'number' ? r[idx[sk]] : null};
    }
    const dec = opt(e, 'dec') != null && !isNaN(+opt(e, 'dec')) ? +opt(e, 'dec') : autoDec(Object.values(CELL).map(c => c.mean));
    const sdDec = autoDec(Object.values(CELL).map(c => c.sd));
    const bestOpt = (opt(e, 'best') || 'col:max').split(':');
    const S = {tr: false, best: bestOpt[0] === 'off' ? 'off' : (bestOpt[0] === 'row' ? 'row' : 'col'), dir: bestOpt[1] === 'min' ? 'min' : 'max',
               color: opt(e, 'color') || 'heat', sort: 'mean', sdir: -1, filter: '', sd: true, compact: false};
    const summary = list((opt(e, 'summary') || 'mean').replace(/\s+/g, ',')), footer = list((opt(e, 'footer') || '').replace(/\s+/g, ','));
    const box = el('div', 'pv-grid pv-gen'), bar = el('div', 'tbar pv-ui'), wrap = el('div', 'twrap');
    box.dataset.table = key;
    box.pvFind = a => {  // the cell of a cell anchor, in the grid as it is now (it may be transposed)
      const [r, c] = S.tr ? [a.col, a.row] : [a.row, a.col];
      return [...table.querySelectorAll('td[data-r]')].find(td => td.dataset.r === r && td.dataset.c === c) || null;
    };
    const table = el('table', 'grid');
    wrap.append(table);
    if (!full) wrap.style.maxHeight = (+(opt(e, 'height') || 560)) + 'px';
    const filter = el('input');
    filter.type = 'search'; filter.placeholder = 'Filter rows…'; filter.setAttribute('aria-label', 'Filter rows');
    filter.addEventListener('input', () => { S.filter = filter.value; render(); });
    const seg = (label, key, vals) => {
      const s = el('span', 'seg'); s.append(el('span', 'lab', label));
      for (const [v, t] of vals) {
        const b = button(t, '', () => { S[key] = v; s.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); render(); });
        if (S[key] === v) b.classList.add('on');
        s.append(b);
      }
      return s;
    };
    const tog = (label, title, key, cls) => {
      const b = button(label, title, () => { S[key] = !S[key]; b.classList.toggle('on', S[key]); if (cls) box.classList.toggle(cls, key === 'sd' ? !S[key] : S[key]); else render(); });
      if (S[key]) b.classList.add('on');
      return b;
    };
    const ttl = el('span', 'ttl', '');
    bar.append(ttl, filter, seg('Best per', 'best', [['col', 'Column'], ['row', 'Row'], ['off', 'Off']]),
      seg('Color', 'color', [['heat', 'Heat'], ['rank', 'Rank'], ['off', 'Off']]),
      tog('± sd', 'Show the standard deviation', 'sd', 'nosd'), tog('Compact', 'Smaller rows', 'compact', 'compact'),
      tog('⇄ Transpose', 'Swap the rows and the columns', 'tr'), el('span', 'grow'));
    if (!full) bar.append(button('⤢ Wide', 'Use the full width of the page', (ev, b) => { b.classList.toggle('on', host.classList.toggle('wide')); }),
                          button('⛶', 'Full window (Esc to close)', () => fullWindow(box, host)));
    bar.append(button('LaTeX', 'Copy the grid as LaTeX', () => copy(exportAs('latex'), 'the grid as LaTeX')),
               button('MD', 'Copy the grid as Markdown', () => copy(exportAs('md'), 'the grid as Markdown')),
               button('CSV', 'Copy the grid as CSV', () => copy(exportAs('csv'), 'the grid as CSV')));
    box.append(bar, wrap);
    const cap = captionOf(e);
    if (cap.node) { const c = el('div', 'tcap'); c.append(cap.node); box.append(c); }
    ttl.textContent = opt(e, 'title') || cap.title || '';
    if (e.classList.contains('wide')) host.classList.add('wide');

    const get = (r, c) => S.tr ? CELL[c.key + '\u0001' + r.key] : CELL[r.key + '\u0001' + c.key];
    const anchorOf = (r, c) => S.tr ? {table: key, row: c.key, col: r.key} : {table: key, row: r.key, col: c.key};
    const better = (a, b) => S.dir === 'min' ? a < b : a > b;
    function groupsOf(cs) {
      const out = [];
      for (const c of cs) { if (out.length && out[out.length - 1].g === c.group) out[out.length - 1].n++; else out.push({g: c.group, n: 1}); }
      return out;
    }
    function model() {
      const cols = S.tr ? ROWS : COLS, f = S.filter.toLowerCase();
      let rows = (S.tr ? COLS : ROWS).filter(r => !f || (r.name + ' ' + r.group).toLowerCase().includes(f));
      const rank = {};
      for (const c of cols) {
        const vs = rows.map(r => [r.key, get(r, c)]).filter(x => x[1]).sort((a, b) => S.dir === 'min' ? a[1].mean - b[1].mean : b[1].mean - a[1].mean);
        vs.forEach(([k], i) => { rank[k + '\u0001' + c.key] = i + 1; });
      }
      const sum = {};
      for (const r of rows) {
        const vals = cols.map(c => get(r, c)).filter(Boolean), rk = cols.map(c => rank[r.key + '\u0001' + c.key]).filter(Boolean);
        sum[r.key] = {mean: vals.length ? vals.reduce((a, v) => a + v.mean, 0) / vals.length : null,
                      rank: rk.length ? rk.reduce((a, b) => a + b, 0) / rk.length : null};
      }
      const sv = r => S.sort === 'name' ? r.name.toLowerCase() : S.sort === 'mean' ? sum[r.key].mean : S.sort === 'rank' ? sum[r.key].rank
        : S.sort.startsWith('info:') ? r.info[S.sort.slice(5)] : (get(r, {key: S.sort.slice(4)}) || {mean: null}).mean;
      rows = [...rows].sort((a, b) => {
        const x = sv(a), y = sv(b);
        if (x === y) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (x < y ? -1 : 1) * S.sdir;
      });
      const colStat = {}, rowStat = {};
      for (const c of cols) { const vs = rows.map(r => get(r, c)).filter(Boolean).map(v => v.mean); colStat[c.key] = vs.length ? [Math.min(...vs), Math.max(...vs)] : [0, 0]; }
      for (const r of rows) { const vs = cols.map(c => get(r, c)).filter(Boolean).map(v => v.mean); rowStat[r.key] = vs.length ? [Math.min(...vs), Math.max(...vs)] : [0, 0]; }
      return {rows, cols, rank, sum, colStat, rowStat};
    }
    const sums = () => summary.filter(s => s === 'mean' || (s === 'rank' && !S.tr));
    const infos = () => S.tr ? [] : Object.keys(info);
    function render() {
      const M = model(), {rows, cols} = M;
      table.replaceChildren();
      const thead = el('thead'), tbody = el('tbody'), tfoot = el('tfoot');
      table.append(thead, tbody, tfoot);
      const dir = k => S.sort === k ? `<span class="dir">${S.sdir > 0 ? '▲' : '▼'}</span>` : '';
      const ss = sums(), is = infos(), hasGroups = cols.some(c => c.group);
      if (hasGroups) {
        const g = el('tr', 'g');
        g.innerHTML = '<th class="stick"></th>' + is.map(() => '<th></th>').join('') +
          groupsOf(cols).map(x => `<th colspan="${x.n}">${esc(x.g)}</th>`).join('') + (ss.length ? `<th colspan="${ss.length}">Summary</th>` : '');
        thead.append(g);
      }
      const h = el('tr', 'c');
      let hh = `<th class="stick" data-k="name" tabindex="0">${esc(S.tr ? ck : rk)}${dir('name')}</th>`;
      for (const k of is) hh += `<th data-k="info:${esc(k)}" tabindex="0">${esc(info[k])}${dir('info:' + k)}</th>`;
      cols.forEach((c, i) => { hh += `<th data-k="col:${esc(c.key)}" data-ci="${i}" tabindex="0">${esc(c.name)}${dir('col:' + c.key)}${c.group && !hasGroups ? '' : ''}</th>`; });
      for (const s of ss) hh += `<th data-k="${s}" tabindex="0">${s === 'mean' ? 'Mean' : 'Avg rank'}${dir(s)}<span class="sm">${s === 'rank' ? '1 = best' : ''}</span></th>`;
      h.innerHTML = hh;
      thead.append(h);
      for (const r of rows) {
        const tr = el('tr');
        let b = `<td class="stick">${esc(r.name)}${r.group ? `<span class="fam">${esc(r.group)}</span>` : ''}</td>`;
        for (const k of is) b += `<td class="num">${esc(fmtNum(r.info[k]))}</td>`;
        const [rlo, rhi] = M.rowStat[r.key];
        cols.forEach((c, ci) => {
          const v = get(r, c);
          const a = anchorOf(r, c);
          const cm = CTX.cellThreads && CTX.cellThreads(a).length ? ' cmt' : '';
          if (!v) { b += `<td class="na${cm}" data-ci="${ci}" data-r="${esc(r.key)}" data-c="${esc(c.key)}" title="No value">—</td>`; return; }
          const [lo, hi] = S.best === 'row' ? [rlo, rhi] : M.colStat[c.key];
          let t = hi > lo ? (v.mean - lo) / (hi - lo) : 1;
          if (S.dir === 'min') t = 1 - t;
          const rkn = M.rank[r.key + '\u0001' + c.key], n = rows.length;
          let cls = 'num' + cm, st = '';
          const isBest = (S.best === 'col' && v.mean === (S.dir === 'min' ? M.colStat[c.key][0] : M.colStat[c.key][1])) ||
                         (S.best === 'row' && v.mean === (S.dir === 'min' ? rlo : rhi));
          if (isBest) cls += ' best';
          if (S.color === 'heat') st = `background:color-mix(in srgb,var(--heat) ${Math.round(4 + 46 * t * t)}%,var(--surface))`;
          if (S.color === 'rank') {
            if (rkn <= 3) st = `background:color-mix(in srgb,var(--good) ${[40, 24, 12][rkn - 1]}%,var(--surface))`;
            else if (rkn > n - 2) st = `background:color-mix(in srgb,var(--cold) ${rkn === n ? 26 : 13}%,var(--surface))`;
          }
          b += `<td class="${cls}" style="${st}" data-ci="${ci}" data-r="${esc(r.key)}" data-c="${esc(c.key)}">${esc(fmtNum(v.mean, dec))}${v.sd != null ? `<span class="sd"> ± ${esc(fmtNum(v.sd, sdDec))}</span>` : ''}</td>`;
        });
        for (const s of ss) b += `<td class="num sum">${s === 'mean' ? esc(fmtNum(M.sum[r.key].mean, dec)) : esc(fmtNum(M.sum[r.key].rank, 1))}</td>`;
        tr.innerHTML = b;
        tbody.append(tr);
      }
      if (footer.length) {
        for (const f of footer) {
          const tr = el('tr', 'f');
          let b = `<td class="stick">${f === 'mean' ? 'Mean' : 'Best'}</td>` + is.map(() => '<td></td>').join('');
          for (const c of cols) {
            const vs = rows.map(r => [r, get(r, c)]).filter(x => x[1]);
            if (!vs.length) { b += '<td>—</td>'; continue; }
            if (f === 'mean') b += `<td class="num">${esc(fmtNum(vs.reduce((a, x) => a + x[1].mean, 0) / vs.length, dec))}</td>`;
            else { const top = vs.sort((a, x) => S.dir === 'min' ? a[1].mean - x[1].mean : x[1].mean - a[1].mean)[0]; b += `<td class="num">${esc(fmtNum(top[1].mean, dec))} <span class="nm">${esc(top[0].name)}</span></td>`; }
          }
          b += ss.map(() => '<td></td>').join('');
          tr.innerHTML = b;
          tfoot.append(tr);
        }
      }
      if (hasGroups) table.style.setProperty('--h1', thead.rows[0].getBoundingClientRect().height + 'px');
      const fr = [...tfoot.rows];
      fr.forEach((row, i) => { const below = fr.slice(i + 1).reduce((a, x) => a + x.getBoundingClientRect().height, 0); row.querySelectorAll('td').forEach(td => { td.style.bottom = below + 'px'; }); });
    }
    table.addEventListener('click', ev => {
      const th = ev.target.closest('thead th[data-k]');
      if (th) {
        const k = th.dataset.k;
        if (S.sort === k) S.sdir = -S.sdir; else { S.sort = k; S.sdir = k === 'name' || k === 'rank' ? 1 : (S.dir === 'min' && (k.startsWith('col:') || k === 'mean') ? 1 : -1); }
        render(); return;
      }
      const td = ev.target.closest('td[data-r]');
      if (td) {
        table.querySelectorAll('td.sel').forEach(x => x.classList.remove('sel'));
        td.classList.add('sel');
        const r = {key: td.dataset.r}, c = {key: td.dataset.c};
        const a = anchorOf(r, c), v = get(r, c);
        if (CTX.cellClick) CTX.cellClick(a, td, `${a.row} · ${a.col}: ${v ? fmtNum(v.mean, dec) + (v.sd != null ? ' ± ' + fmtNum(v.sd, sdDec) : '') : '—'}`);
      }
    });
    table.addEventListener('keydown', ev => { const th = ev.target.closest('th[data-k]'); if (th && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); th.click(); } });
    function exportAs(kind) {
      const M = model(), {rows, cols} = M, ss = sums();
      const isBest = (r, c, v) => v && ((S.best === 'col' && v.mean === (S.dir === 'min' ? M.colStat[c.key][0] : M.colStat[c.key][1])) ||
                                       (S.best === 'row' && v.mean === (S.dir === 'min' ? M.rowStat[r.key][0] : M.rowStat[r.key][1])));
      const head = [S.tr ? ck : rk, ...cols.map(c => c.name), ...ss.map(s => s === 'mean' ? 'Mean' : 'Avg rank')];
      const sumv = r => ss.map(s => s === 'mean' ? fmtNum(M.sum[r.key].mean, dec) : fmtNum(M.sum[r.key].rank, 1));
      if (kind === 'csv') return [head.join(','), ...rows.map(r => [r.name, ...cols.map(c => { const v = get(r, c); return v ? fmtNum(v.mean, dec) : ''; }), ...sumv(r)].join(','))].join('\n');
      if (kind === 'md') return ['| ' + head.join(' | ') + ' |', '|---|' + cols.map(() => '---:').join('|') + ss.map(() => '|---:').join('') + '|',
        ...rows.map(r => '| ' + [r.name, ...cols.map(c => { const v = get(r, c); if (!v) return '—'; const s = fmtNum(v.mean, dec) + (v.sd != null && S.sd ? ' ± ' + fmtNum(v.sd, sdDec) : ''); return isBest(r, c, v) ? `**${s}**` : s; }), ...sumv(r)].join(' | ') + ' |')].join('\n');
      const groups = groupsOf(cols), hasG = cols.some(c => c.group), texEsc = s => String(s).replace(/([%&#_$])/g, '\\$1');
      const lines = ['\\begin{tabular}{l' + 'r'.repeat(cols.length + ss.length) + '}', '\\toprule'];
      if (hasG) {
        lines.push(' & ' + groups.map(g => `\\multicolumn{${g.n}}{c}{${texEsc(g.g)}}`).join(' & ') + ss.map(() => ' &').join('') + ' \\\\');
        let at = 1; const cm = [];
        for (const g of groups) { cm.push(`\\cmidrule(lr){${at + 1}-${at + g.n}}`); at += g.n; }
        lines.push(cm.join(' '));
      }
      lines.push(head.map(texEsc).join(' & ') + ' \\\\', '\\midrule');
      for (const r of rows) lines.push([texEsc(r.name), ...cols.map(c => { const v = get(r, c); if (!v) return '--'; const s = v.sd != null && S.sd ? `$${fmtNum(v.mean, dec)} \\pm ${fmtNum(v.sd, sdDec)}$` : fmtNum(v.mean, dec); return isBest(r, c, v) ? `\\textbf{${s}}` : s; }), ...sumv(r)].join(' & ') + ' \\\\');
      return lines.concat(['\\bottomrule', '\\end{tabular}']).join('\n');
    }
    box.pvRender = render;
    render();
    return box;
  }

  // ------------------------------------------------------------ the chart component
  const PALETTE = 8;
  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) { hi = lo + 1; }
    const span = hi - lo, step0 = Math.pow(10, Math.floor(Math.log10(span / n))), err = span / n / step0;
    const step = step0 * (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1);
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
    return out;
  }
  function chartWidget(host, d, e) {
    const idx = {};
    d.columns.forEach((c, i) => { idx[c] = i; });
    const type = opt(e, 'type') || 'line', xk = opt(e, 'x') || d.columns[0], ys = list(opt(e, 'y'));
    const ck = opt(e, 'color'), ek = opt(e, 'err');
    const yks = ys.length ? ys : d.columns.filter(c => c !== xk && c !== ck && typeof d.rows[0]?.[idx[c]] === 'number').slice(0, 1);
    const miss = [xk, ...yks, ck, ek].filter(c => c && !(c in idx));
    if (miss.length) return errorBox(`The chart needs the columns ${miss.join(', ')} in ${opt(e, 'src')}.`);
    const logX = optOn(e, 'log-x'), logY = optOn(e, 'log-y');
    const catX = type === 'bar' || d.rows.some(r => typeof r[idx[xk]] !== 'number');
    // the series: one per y column, or one per value of the color column
    const series = [];
    const add = (name, pts) => series.push({name, pts, on: true, c: (series.length % PALETTE) + 1});
    if (ck) {
      const groups = new Map();
      for (const r of d.rows) { const g = String(r[idx[ck]]); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(r); }
      for (const [g, rs] of groups) add(g, rs.map(r => ({x: r[idx[xk]], y: r[idx[yks[0]]], e: ek ? r[idx[ek]] : null})));
    } else for (const y of yks) add(y, d.rows.map(r => ({x: r[idx[xk]], y: r[idx[y]], e: ek && yks.length === 1 ? r[idx[ek]] : null})));
    for (const s of series) s.pts = s.pts.filter(p => typeof p.y === 'number' && (catX || typeof p.x === 'number'));
    const cats = catX ? [...new Set(series.flatMap(s => s.pts.map(p => String(p.x))))] : null;
    const H = +(opt(e, 'height') || 320), L = 56, R = 16, Tp = 12, B = 44;
    let W = 760;  // the width of the drawing: the width of the chart on the screen, so the labels keep their size
    const box = el('div', 'pv-chart pv-gen'), legend = el('div', 'legend pv-ui'), svg = sv('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img'});
    const tip = el('div', 'tip');
    tip.hidden = true;
    box.append(legend, svg, tip);
    const cap = captionOf(e);
    if (cap.node) { const c = el('div', 'tcap'); c.append(cap.node); box.append(c); }
    svg.setAttribute('aria-label', cap.title || 'Chart');
    if (e.classList.contains('wide')) host.classList.add('wide');
    const tx = v => logX ? Math.log10(v) : v, ty = v => logY ? Math.log10(v) : v;
    function draw() {
      const bw = box.getBoundingClientRect().width;
      if (bw > 0) W = Math.max(320, Math.round(bw - 28));
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      svg.replaceChildren();
      const on = series.filter(s => s.on);
      const all = on.flatMap(s => s.pts);
      let ylo = Math.min(...all.map(p => p.y - (p.e || 0))), yhi = Math.max(...all.map(p => p.y + (p.e || 0)));
      if (!all.length) { ylo = 0; yhi = 1; }
      if (type === 'bar' || type === 'area') { ylo = Math.min(0, ylo); yhi = Math.max(0, yhi); }
      if (opt(e, 'y-min') != null) ylo = +opt(e, 'y-min');
      if (opt(e, 'y-max') != null) yhi = +opt(e, 'y-max');
      if (logY) { ylo = Math.log10(Math.max(1e-300, ylo > 0 ? ylo : Math.min(...all.map(p => p.y).filter(v => v > 0)))); yhi = Math.log10(yhi); }
      else { const pad = (yhi - ylo) * 0.04 || 1; if (opt(e, 'y-min') == null && !(type === 'bar' && ylo === 0)) ylo -= pad; if (opt(e, 'y-max') == null) yhi += pad; }
      let xlo = 0, xhi = 1;
      if (!catX) {
        xlo = Math.min(...all.map(p => tx(p.x))); xhi = Math.max(...all.map(p => tx(p.x)));
        if (opt(e, 'x-min') != null) xlo = tx(+opt(e, 'x-min'));
        if (opt(e, 'x-max') != null) xhi = tx(+opt(e, 'x-max'));
        if (!(xhi > xlo)) { xhi = xlo + 1; }
      }
      const px = v => L + (tx(v) - xlo) / (xhi - xlo) * (W - L - R);
      const py = v => Tp + (1 - (ty(v) - ylo) / (yhi - ylo)) * (H - Tp - B);
      const band = cats ? (W - L - R) / cats.length : 0, cx = c => L + (cats.indexOf(String(c)) + 0.5) * band;
      // grid and axes
      const yt = logY ? niceTicks(Math.ceil(ylo), Math.floor(yhi), Math.max(1, Math.floor(yhi) - Math.ceil(ylo))).map(v => Math.pow(10, v)) : niceTicks(ylo, yhi, 5);
      for (const v of yt) {
        const y = py(v);
        if (y < Tp - 1 || y > H - B + 1) continue;
        sv('line', {x1: L, x2: W - R, y1: y, y2: y, class: 'grid'}, svg);
        sv('text', {x: L - 8, y: y + 4, 'text-anchor': 'end', class: 'tk'}, svg).textContent = fmtNum(v);
      }
      sv('line', {x1: L, x2: W - R, y1: H - B, y2: H - B, class: 'ax'}, svg);
      if (cats) cats.forEach(c => { sv('text', {x: cx(c), y: H - B + 16, 'text-anchor': 'middle', class: 'tk'}, svg).textContent = c.length > 14 ? c.slice(0, 13) + '…' : c; });
      else {
        const xt = logX ? niceTicks(Math.ceil(xlo), Math.floor(xhi), Math.max(1, Math.floor(xhi) - Math.ceil(xlo))).map(v => Math.pow(10, v)) : niceTicks(xlo, xhi, 6);
        for (const v of xt) { const x = px(v); if (x < L - 1 || x > W - R + 1) continue; sv('text', {x, y: H - B + 16, 'text-anchor': 'middle', class: 'tk'}, svg).textContent = fmtNum(v); }
      }
      if (opt(e, 'x-label') || xk) sv('text', {x: (L + W - R) / 2, y: H - 6, 'text-anchor': 'middle', class: 'tk lab'}, svg).textContent = opt(e, 'x-label') || xk;
      const yl = opt(e, 'y-label') || (yks.length === 1 ? yks[0] : '');
      if (yl) sv('text', {x: 14, y: (Tp + H - B) / 2, 'text-anchor': 'middle', class: 'tk lab', transform: `rotate(-90 14 ${(Tp + H - B) / 2})`}, svg).textContent = yl;
      const clip = `pvclip${Math.random().toString(36).slice(2, 8)}`;
      const cp = sv('clipPath', {id: clip}, sv('defs', {}, svg));
      sv('rect', {x: L, y: Tp - 4, width: W - L - R, height: H - Tp - B + 8}, cp);
      const g = sv('g', {'clip-path': `url(#${clip})`}, svg);
      if (type === 'bar') {
        const nb = on.length || 1, bw = band * 0.8 / nb;
        on.forEach((s, si) => {
          for (const p of s.pts) {
            const x = cx(p.x) - band * 0.4 + si * bw, y0 = py(Math.max(ylo, 0)), y1 = py(p.y);
            sv('rect', {x: x + 1, y: Math.min(y0, y1), width: Math.max(1, bw - 2), height: Math.abs(y0 - y1), rx: 2, class: 'f' + s.c}, g);
            if (p.e != null) sv('line', {x1: x + bw / 2, x2: x + bw / 2, y1: py(p.y - p.e), y2: py(p.y + p.e), class: 'err'}, g);
          }
        });
      } else {
        for (const s of on) {
          const pts = catX ? s.pts : [...s.pts].sort((a, b) => a.x - b.x);
          const X = p => catX ? cx(p.x) : px(p.x);
          if (pts.some(p => p.e != null) && type !== 'scatter') {
            const up = pts.map(p => `${X(p).toFixed(1)} ${py(p.y + (p.e || 0)).toFixed(1)}`), dn = pts.map(p => `${X(p).toFixed(1)} ${py(p.y - (p.e || 0)).toFixed(1)}`).reverse();
            sv('path', {d: 'M' + up.join('L') + 'L' + dn.join('L') + 'Z', class: 'band f' + s.c}, g);
          }
          if (type === 'scatter') { for (const p of pts) sv('circle', {cx: X(p), cy: py(p.y), r: 3.2, class: 'dot f' + s.c}, g); continue; }
          const d0 = pts.map((p, i) => (i ? 'L' : 'M') + X(p).toFixed(1) + ' ' + py(p.y).toFixed(1)).join('');
          if (type === 'area' && pts.length) sv('path', {d: d0 + `L${X(pts[pts.length - 1]).toFixed(1)} ${py(Math.max(ylo, 0))}L${X(pts[0]).toFixed(1)} ${py(Math.max(ylo, 0))}Z`, class: 'band f' + s.c}, g);
          sv('path', {d: d0, class: 'ln s' + s.c}, g);
          if (pts.length && pts.length < 40) for (const p of pts) sv('circle', {cx: X(p), cy: py(p.y), r: 2.2, class: 'f' + s.c}, g);
        }
      }
      // hover: the nearest x (lines, bars) or the nearest point (scatter)
      const cur = sv('line', {y1: Tp, y2: H - B, class: 'cursor', visibility: 'hidden'}, svg);
      const hit = sv('rect', {x: L, y: Tp, width: W - L - R, height: H - Tp - B, fill: 'transparent'}, svg);
      hit.addEventListener('pointermove', ev => {
        const r = svg.getBoundingClientRect(), mx = (ev.clientX - r.left) / r.width * W, my = (ev.clientY - r.top) / r.height * H;
        tip.replaceChildren();
        let head = '', rows = [];
        if (type === 'scatter') {
          let best = null;
          for (const s of on) for (const p of s.pts) { const dd = Math.hypot((catX ? cx(p.x) : px(p.x)) - mx, py(p.y) - my); if (!best || dd < best.d) best = {d: dd, p, s}; }
          if (!best || best.d > 30) { tip.hidden = true; cur.setAttribute('visibility', 'hidden'); return; }
          head = `${xk} ${fmtNum(best.p.x)}`; rows = [[best.s, best.p]];
          cur.setAttribute('visibility', 'hidden');
        } else {
          let xv;
          if (catX) xv = cats[Math.max(0, Math.min(cats.length - 1, Math.floor((mx - L) / band)))];
          else {
            let bd = Infinity;
            for (const s of on) for (const p of s.pts) { const dd = Math.abs(px(p.x) - mx); if (dd < bd) { bd = dd; xv = p.x; } }
          }
          if (xv == null) return;
          const X = catX ? cx(xv) : px(xv);
          cur.setAttribute('x1', X); cur.setAttribute('x2', X); cur.setAttribute('visibility', 'visible');
          head = `${opt(e, 'x-label') || xk} ${catX ? xv : fmtNum(xv)}`;
          for (const s of on) { const p = s.pts.find(q => (catX ? String(q.x) === String(xv) : q.x === xv)); if (p) rows.push([s, p]); }
        }
        tip.append(el('div', 'h', head));
        for (const [s, p] of rows) {
          const dv = el('div'), i = el('i');
          i.style.background = `var(--c${s.c})`;
          dv.append(i, `${s.name}  ${fmtNum(p.y)}${p.e != null ? ' ± ' + fmtNum(p.e) : ''}`);
          tip.append(dv);
        }
        tip.hidden = false;
        const br = box.getBoundingClientRect();
        tip.style.left = Math.min(ev.clientX - br.left + 14, br.width - tip.offsetWidth - 6) + 'px';
        tip.style.top = (ev.clientY - br.top - 18) + 'px';
      });
      hit.addEventListener('pointerleave', () => { tip.hidden = true; cur.setAttribute('visibility', 'hidden'); });
    }
    function drawLegend() {
      legend.replaceChildren();
      if (series.length < 2) return;
      for (const s of series) {
        const b = button('', 'Show or hide ' + s.name, () => { s.on = !s.on; drawLegend(); draw(); }, s.on ? '' : 'off');
        const i = el('i'); i.style.background = `var(--c${s.c})`;
        b.append(i, s.name);
        legend.append(b);
      }
    }
    drawLegend(); draw();
    box.pvRender = draw;
    if (typeof ResizeObserver !== 'undefined') {  // draw again when the width changes (a resize, a tab that opens)
      let last = 0;
      new ResizeObserver(() => { const w = Math.round(box.getBoundingClientRect().width); if (w > 0 && Math.abs(w - last) > 8) { last = w; draw(); } }).observe(box);
    }
    return box;
  }

  function errorBox(msg) { return el('div', 'pv-error pv-gen', msg); }

  // ------------------------------------------------------------ the simple blocks
  const CALLOUT_ICONS = {note: 'ℹ️', tip: '💡', result: '✅', warn: '⚠️', danger: '⛔'};
  function callout(e) {
    const kind = ['tip', 'result', 'warn', 'danger'].find(k => e.classList.contains(k)) || 'note';
    e.classList.add('pv-callout', 'k-' + kind);
    const body = el('div', 'body');
    body.append(...e.childNodes);
    const t = opt(e, 'title');
    if (t) { const h = el('div', 'ctitle pv-gen', t); body.prepend(h); e.removeAttribute('title'); }
    e.append(el('span', 'ic pv-gen', opt(e, 'icon') || CALLOUT_ICONS[kind]), body);
  }
  // A stat card from each item "VALUE | LABEL | NOTE". The nodes of the item move into the card (they are not copied),
  // so a viewer that maps the text to the source (mdview) keeps its comments and changes on them.
  function statParts(li) {
    // a "|" inside a longer text node becomes a text node of its own (the walker visits the new nodes too)
    const w = document.createTreeWalker(li, NodeFilter.SHOW_TEXT);
    for (let t; (t = w.nextNode());) {
      const i = t.data.indexOf('|');
      if (i < 0 || t.data === '|') continue;
      const rest = t.splitText(i);
      if (rest.data.length > 1) rest.splitText(1);
    }
    const parts = [[]];
    for (const n of [...li.childNodes]) {
      if (n.textContent.trim() === '|' && parts.length < 3) { parts.push([]); n.remove(); } else parts[parts.length - 1].push(n);
    }
    for (const p of parts) {  // drop the spaces at the edges of each part
      while (p.length && !p[0].textContent.trim()) p.shift().remove();
      while (p.length && !p[p.length - 1].textContent.trim()) p.pop().remove();
    }
    return parts;
  }
  function stats(e) {
    const items = [...e.querySelectorAll(':scope > ul > li, :scope > ol > li')];
    if (!items.length) return;
    const grid = el('div', 'pv-stats');
    for (const li of items) {
      const [v = [], l = [], n = []] = statParts(li);
      const card = el('div', 'stat'), val = el('div', 'v'), lab = el('div', 'l');
      const t = v.length === 1 && v[0].nodeType === 3 ? v[0].data : null;
      const m = t != null && /^([+-]?[\d.,]+(?:[eE][+-]?\d+)?)\s*(.*)$/.exec(t);
      if (m && m[2]) {  // one text node: the unit in small type
        v[0].data = m[1];
        val.append(v[0], el('small', null, m[2]));
      } else val.append(...v);
      lab.append(...l);
      card.append(lab, val);
      if (n.length) { const s = el('div', 's'); s.append(...n); card.append(s); }
      grid.append(card);
    }
    e.replaceChildren(grid);
  }
  function tabs(e) {
    const panes = [...e.querySelectorAll(':scope > .tab')];
    if (!panes.length) return;
    const bar = el('div', 'tablist pv-ui'), key = 'tab:' + (e.dataset.pos || '');
    bar.setAttribute('role', 'tablist');
    let cur = 0;
    try { cur = Math.min(panes.length - 1, +(sessionStorage.getItem('pageview.' + key) || 0)); } catch (x) { /* none */ }
    const show = i => {
      cur = i;
      panes.forEach((p, j) => { p.hidden = j !== i; });
      bar.querySelectorAll('button').forEach((b, j) => b.setAttribute('aria-selected', String(j === i)));
      try { sessionStorage.setItem('pageview.' + key, String(i)); } catch (x) { /* none */ }
      for (const c of panes[i].querySelectorAll('.pv-chart, .pv-grid')) if (c.pvRender) c.pvRender();
    };
    panes.forEach((p, i) => {
      const b = button(opt(p, 'title') || 'Tab ' + (i + 1), '', () => show(i));
      b.setAttribute('role', 'tab');
      p.removeAttribute('title');
      p.setAttribute('role', 'tabpanel');
      bar.append(b);
    });
    e.classList.add('pv-tabs');
    e.prepend(bar);
    show(cur);
  }
  function toggle(e) {
    const d = el('details', 'pv-toggle'), s = el('summary', 'pv-ui', opt(e, 'title') || 'Details'), body = el('div', 'body');
    body.append(...e.childNodes);
    d.append(s, body);
    if (optOn(e, 'open')) d.open = true;
    d.addEventListener('toggle', () => { for (const c of body.querySelectorAll('.pv-chart')) if (c.pvRender) c.pvRender(); });
    e.removeAttribute('title');
    e.replaceChildren(d);
  }
  function columns(e) {
    e.classList.add('pv-cols');
    for (const c of e.querySelectorAll(':scope > .col')) {
      const t = opt(c, 'title');
      if (t) { c.prepend(el('h3', 'ctitle', t)); c.removeAttribute('title'); }
    }
  }
  function figures(e) {
    e.classList.add('pv-figs');
    const n = +opt(e, 'cols');
    if (n > 0) e.style.setProperty('--cols', n);
    // pandoc puts two images on adjacent lines in one paragraph: make one figure for each image
    for (const p of [...e.querySelectorAll(':scope > p')]) {
      const imgs = [...p.querySelectorAll('img')];
      if (!imgs.length) continue;
      const frag = document.createDocumentFragment();
      for (const img of imgs) {
        const f = el('figure'), cap = el('figcaption');
        cap.textContent = img.alt || '';
        f.append(img, cap);
        frag.append(f);
      }
      p.replaceWith(frag);
    }
  }
  function codeCopy(pre) {
    if (pre.querySelector(':scope > .cp')) return;
    pre.classList.add('pv-code');
    pre.append(button('Copy', 'Copy the code', () => copy((pre.querySelector('code') || pre).textContent, 'the code'), 'cp'));
  }
  function widget(pre) {
    const i = opt(pre, 'i');
    const box = el('div', 'pv-widget pv-gen'), f = el('iframe');
    f.setAttribute('sandbox', 'allow-scripts');
    f.setAttribute('referrerpolicy', 'no-referrer');
    f.title = opt(pre, 'title') || 'Widget';
    f.style.height = (+(opt(pre, 'height') || 200)) + 'px';
    f.src = `/widget?f=${encodeURIComponent(CTX.file)}&i=${encodeURIComponent(i)}&theme=${CTX.theme ? CTX.theme() : 'light'}`;
    if (opt(pre, 'title')) box.append(el('div', 'wtitle', opt(pre, 'title')));
    box.append(f);
    if (pre.classList.contains('wide')) box.classList.add('wide');
    pre.replaceWith(box);
  }
  addEventListener('message', ev => {
    const h = ev.data && ev.data.pvWidgetHeight;
    if (typeof h !== 'number') return;
    for (const f of document.querySelectorAll('.pv-widget iframe')) {
      if (f.contentWindow === ev.source) f.style.height = Math.min(2000, Math.max(40, Math.ceil(h) + 4)) + 'px';
    }
  });

  // ------------------------------------------------------------ images: open large on click
  function lightbox(img) {
    const ov = el('div', 'pv-lightbox pv-ui'), inner = el('div', 'inner'), big = el('img');
    big.src = img.currentSrc || img.src;
    big.alt = img.alt || '';
    inner.append(big);
    const cap = img.closest('figure') && img.closest('figure').querySelector('figcaption');
    if (cap) inner.append(cap.cloneNode(true));
    ov.append(inner);
    document.body.append(ov);
    const close = () => { ov.remove(); removeEventListener('keydown', key, true); };
    const key = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    ov.addEventListener('click', close);
    addEventListener('keydown', key, true);
  }

  // ------------------------------------------------------------ enhance a rendered page
  // ctx: {file, data(src) -> Promise<{columns, rows}>, cellThreads(anchor) -> [ids], cellClick(anchor, td, text),
  //       toast(msg), theme() -> 'light' | 'dark', keepTables, widgets}
  // keepTables: true leaves the Markdown tables as pandoc wrote them (mdview styles them in place, so its comments and
  // changes stay on the cells). widgets: false leaves a widget block as code (a viewer without the /widget route).
  async function enhance(root, ctx) {
    CTX = ctx;
    root.querySelectorAll('div.callout').forEach(callout);
    root.querySelectorAll('div.stats').forEach(stats);
    root.querySelectorAll('div.columns').forEach(columns);
    root.querySelectorAll('div.figures').forEach(figures);
    // an image alone in a paragraph: a figure, with its text as the caption
    for (const p of [...root.querySelectorAll('p')]) {
      const img = p.querySelector(':scope > img');
      if (!img || p.closest('.pv-figs') || p.childNodes.length !== 1) continue;
      const f = el('figure', 'pv-fig'), cap = el('figcaption', null, img.alt || '');
      for (const a of p.attributes) f.setAttribute(a.name, a.value);
      f.append(img);
      if (img.alt) f.append(cap);
      p.replaceWith(f);
    }
    if (ctx.widgets !== false) root.querySelectorAll('pre.widget').forEach(widget);
    root.querySelectorAll('pre:not(.widget)').forEach(codeCopy);
    root.querySelectorAll('img').forEach(img => {
      img.loading = 'lazy';
      img.addEventListener('click', () => lightbox(img));
      img.addEventListener('error', () => { img.classList.add('broken'); img.title = 'The image cannot be shown: ' + (img.alt || img.src); }, {once: true});
    });
    // tables: the .table blocks first, then every other Markdown table
    let n = 0;
    const jobs = [];
    for (const e of root.querySelectorAll('div.table, div.chart')) {
      const src = opt(e, 'src');
      if (e.classList.contains('chart')) {
        if (!src) { e.replaceChildren(errorBox('A chart needs a data file: src="FILE.csv".')); continue; }
        jobs.push(ctx.data(src).then(d => { e.replaceChildren(chartWidget(e, d, e)); }, err => { e.replaceChildren(errorBox(err.message)); }));
        continue;
      }
      if (src) {
        jobs.push(ctx.data(src).then(d => {
          if (opt(e, 'rows') && opt(e, 'columns')) { const g = pivotWidget(e, d, e, src, false); e.replaceChildren(g); return; }
          const cap = captionOf(e);
          const model = modelOfData(d, e, src);
          const o = tableOptions(e, model);
          e.replaceChildren(tableWidget(e, model, {...o, caption: cap.node, title: opt(e, 'title') || cap.title, wide: e.classList.contains('wide')}));
        }, err => { e.replaceChildren(errorBox(err.message)); }));
      } else if (!ctx.keepTables) {
        const t = e.querySelector('table');
        if (!t) continue;
        const model = modelOfTable(t, 'table-' + (++n));
        const o = tableOptions(e, model), cap = captionOf(e);
        e.replaceChildren(tableWidget(e, model, {...o, caption: cap.node, title: opt(e, 'title') || cap.title, wide: e.classList.contains('wide')}));
      }
    }
    for (const t of ctx.keepTables ? [] : [...root.querySelectorAll('table')]) {
      if (t.closest('.pv-gen, .pv-table')) continue;
      const host = el('div', 'table pv-auto');
      if (t.dataset.pos) host.dataset.pos = t.dataset.pos;
      t.replaceWith(host);
      const model = modelOfTable(t, 'table-' + (++n));
      host.append(tableWidget(host, model, {height: 520}));
    }
    await Promise.all(jobs);
    // tabs and toggles last: their panes hold the components above
    root.querySelectorAll('div.tabs').forEach(tabs);
    root.querySelectorAll('div.toggle').forEach(toggle);
  }

  // A data file opened alone: one component that fills the window.
  async function dataPage(root, ctx, name, settings) {
    CTX = ctx;
    const d = await ctx.data('');
    const e = el('div', 'table');
    for (const [k, v] of Object.entries(settings || {})) if (v != null && typeof v !== 'object') e.setAttribute('data-' + k, String(v));
    root.replaceChildren(e);
    if (settings.rows && settings.columns) { e.append(pivotWidget(e, d, e, name, true)); return; }
    const model = modelOfData(d, e, name);
    const o = tableOptions(e, model);
    e.append(tableWidget(e, model, {...o, full: true, title: settings.title || name, height: 0}));
    if (d.truncated) toast('The file has more rows than pageview shows (50 000).');
  }

  // One component from an options object, for pages that are code (plateview). kind: 'table', 'grid' or 'chart'.
  // d: the data, {columns, rows}. opts: the options of SYNTAX.md with the same names (for example best: 'acc:max',
  // sd: 'acc:acc_sd'; a grid needs rows, columns and value), plus caption (text), title, wide, height and key.
  // Returns the element of the component. host (an element) gets it as its only child.
  function make(kind, host, d, opts, ctx) {
    if (ctx) CTX = ctx;
    opts = opts || {};
    const e = el('div', 'table');
    for (const [k, v] of Object.entries(opts)) {
      if (v == null || v === false || typeof v === 'object' || typeof v === 'function' || k === 'caption') continue;
      e.setAttribute('data-' + k, v === true ? 'true' : String(v));
    }
    if (opts.wide) { e.classList.add('wide'); host.classList.add('wide'); }
    if (opts.caption) e.append(el('p', null, String(opts.caption)));
    const key = opts.key || opts.src || 'table';
    let box;
    if (kind === 'chart') box = chartWidget(host, d, e);
    else if (kind === 'grid' || (opts.rows && opts.columns)) box = pivotWidget(host, d, e, key, !!opts.full);
    else {
      const model = modelOfData(d, e, key), o = tableOptions(e, model), cap = captionOf(e);
      if (opts.height === 0 || opts.height === '0') o.height = 0;
      box = tableWidget(host, model, {...o, caption: cap.node, title: opts.title || cap.title, wide: !!opts.wide, full: !!opts.full});
    }
    host.replaceChildren(box);
    if (box.pvRender) requestAnimationFrame(() => box.pvRender());  // the grid measures its header rows on screen
    return box;
  }

  // Draw again the tables and charts (for example when the comments change, to mark the cells).
  function refresh(root) {
    root.querySelectorAll('.pv-table, .pv-grid').forEach(b => b.pvRender && b.pvRender());
  }
  function redrawCharts(root) { root.querySelectorAll('.pv-chart').forEach(b => b.pvRender && b.pvRender()); }

  return {enhance, dataPage, make, lightbox, refresh, redrawCharts, parseCell, fmtNum};
})();
