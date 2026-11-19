'use strict';
// plateview: the deck runtime. A deck is a plate with <main class="pv-deck">: each <section> in it is one slide of
// data-width × data-height px (default 1920 × 1080), with its speaker notes in an <aside>. This file shows one slide at
// a time, scaled to the window, or all slides in a grid. Keys: → ← Space PageUp PageDown Home End (move), N (notes),
// G or Esc (grid), F (full screen). It also defines <x-connector> and <x-shape>. README.md, section "Decks".
(() => {
  // <x-connector x1 y1 x2 y2 head="end|start|both|none" dash="8 6">: a line in the box of the nearest positioned
  // element (for example a diagram with position: relative). The color is the CSS color, the width the border width.
  const SVG = 'http://www.w3.org/2000/svg';
  class XConnector extends HTMLElement {
    static get observedAttributes() { return ['x1', 'y1', 'x2', 'y2', 'head', 'dash', 'width']; }
    connectedCallback() { this.draw(); }
    attributeChangedCallback() { if (this.isConnected) this.draw(); }
    draw() {
      const n = k => parseFloat(this.getAttribute(k)) || 0;
      const cs = getComputedStyle(this);
      const bw = parseFloat(cs.borderTopWidth) || 0;
      const w = parseFloat(this.getAttribute('width')) || bw || 2;
      let [x1, y1, x2, y2] = [n('x1'), n('y1'), n('x2'), n('y2')];
      const head = (this.getAttribute('head') || 'none').toLowerCase();
      const len = Math.hypot(x2 - x1, y2 - y1) || 1, ux = (x2 - x1) / len, uy = (y2 - y1) / len;
      const hl = Math.max(10, w * 4.2), hw = hl * 0.62;
      const svg = document.createElementNS(SVG, 'svg');
      svg.setAttribute('aria-hidden', 'true');
      svg.style.left = svg.style.top = -bw + 'px';
      const tip = (x, y, dx, dy) => {  // an arrow head with its point at (x, y), along (dx, dy)
        const p = document.createElementNS(SVG, 'polygon');
        const bx = x - dx * hl, by = y - dy * hl;
        p.setAttribute('points', `${x},${y} ${bx - dy * hw / 2},${by + dx * hw / 2} ${bx + dy * hw / 2},${by - dx * hw / 2}`);
        p.setAttribute('fill', 'currentColor');
        return p;
      };
      const heads = [];
      if (head === 'end' || head === 'both') { heads.push(tip(x2, y2, ux, uy)); x2 -= ux * hl * 0.8; y2 -= uy * hl * 0.8; }
      if (head === 'start' || head === 'both') { heads.push(tip(x1, y1, -ux, -uy)); x1 += ux * hl * 0.8; y1 += uy * hl * 0.8; }
      const line = document.createElementNS(SVG, 'line');
      Object.entries({x1, y1, x2, y2, stroke: 'currentColor', 'stroke-width': w, 'stroke-linecap': 'round'})
        .forEach(([k, v]) => line.setAttribute(k, v));
      if (this.getAttribute('dash')) line.setAttribute('stroke-dasharray', this.getAttribute('dash'));
      svg.append(line, ...heads);
      this.replaceChildren(svg);
    }
  }
  if (!customElements.get('x-connector')) customElements.define('x-connector', XConnector);
  if (!customElements.get('x-shape')) customElements.define('x-shape', class extends HTMLElement {});

  function start() {
    const main = document.querySelector('main.pv-deck');
    if (!main) return;
    const W = parseFloat(main.dataset.width) || 1920, H = parseFloat(main.dataset.height) || 1080;
    main.style.setProperty('--w', W + 'px');
    main.style.setProperty('--h', H + 'px');
    const page = document.createElement('style');  // the page of the PDF: the size of one slide
    page.textContent = `@page { size: ${W}px ${H}px; margin: 0 }`;
    document.head.append(page);

    // the slides: each one in a slot; its notes go to the notes panel
    const notes = document.createElement('div');
    notes.className = 'pv-deck-notes';
    const slides = [...main.children].filter(e => e.tagName === 'SECTION');
    const slots = slides.map((s, i) => {
      const slot = document.createElement('div');
      slot.className = 'pv-slot';
      slot.dataset.n = `${i + 1}`;
      if (s.dataset.chapter) slot.dataset.chapter = s.dataset.chapter;
      if (!s.id) s.id = `slide-${i + 1}`;
      s.before(slot);
      slot.append(s);
      const a = [...s.children].filter(e => e.tagName === 'ASIDE');
      const box = document.createElement('aside');
      box.dataset.slide = s.id;
      if (a.length) a.forEach(x => box.append(...x.childNodes, ...(x !== a[a.length - 1] ? ['\n\n'] : [])));
      else { const e = document.createElement('span'); e.className = 'empty'; e.textContent = 'This slide has no notes.'; box.append(e); }
      a.forEach(x => x.remove());
      notes.append(box);
      return slot;
    });
    if (!slots.length) return;
    main.after(notes);
    // an image of the deck folder: through pv, as a plate has no files of its own
    document.querySelectorAll('img[data-pv-src]').forEach(img => {
      if (window.pv) pv.url(img.dataset.pvSrc).then(u => { img.src = u; }, e => pv.toast(e.message));
    });

    // the bar
    const bar = document.createElement('div');
    bar.className = 'pv-ui pv-deck-bar';
    bar.innerHTML = '<button type="button" data-a="prev" title="Back (←)">‹</button><span class="n"></span>' +
      '<button type="button" data-a="next" title="Next (→ or Space)">›</button><span class="sep"></span>' +
      '<button type="button" data-a="notes" title="Speaker notes (N)">Notes</button>' +
      '<button type="button" data-a="grid" title="All slides (G or Esc)">Grid</button>' +
      '<button type="button" data-a="full" title="Full screen (F)">Full screen</button>';
    document.body.append(bar);
    const btn = a => bar.querySelector(`[data-a="${a}"]`);

    const framed = window.parent !== window && !(window.pv && pv.exported);
    const tell = m => { if (framed) parent.postMessage({pv: 1, op: 'deck', ...m}, '*'); };
    const q = new URLSearchParams(location.search);
    let cur = 0, notesOn = q.get('notes') === '1', notesH = 0;
    const first = q.get('slide') || decodeURIComponent(location.hash.slice(1));
    if (first) {
      const k = slides.findIndex(s => s.id === first);
      cur = k >= 0 ? k : Math.min(slots.length, Math.max(1, parseInt(first, 10) || 1)) - 1;
    }

    function fit() {
      const grid = main.dataset.mode === 'grid';
      notesH = notesOn && !grid ? Math.round(Math.min(260, innerHeight * 0.3)) : 0;
      document.body.style.setProperty('--pv-notes', notesH + 'px');
      main.style.setProperty('--pv-below', (44 + notesH) + 'px');
      let s;
      if (grid) {
        const cols = Math.max(1, Math.min(4, Math.floor((innerWidth - 44 + 22) / (300 + 22))));
        s = Math.min(420, (innerWidth - 44 - 22 * (cols - 1)) / cols) / W;
      } else {
        const pad = innerWidth < 700 ? 8 : 24;
        s = Math.min((innerWidth - 2 * pad) / W, (innerHeight - 44 - notesH - pad) / H);
      }
      main.style.setProperty('--s', Math.max(0.05, s).toFixed(5));
    }
    function paint() {
      bar.querySelector('.n').textContent = `${cur + 1} / ${slots.length}`;
      btn('prev').disabled = cur === 0;
      btn('next').disabled = cur === slots.length - 1;
      btn('notes').setAttribute('aria-pressed', String(notesOn));
      btn('grid').setAttribute('aria-pressed', String(main.dataset.mode === 'grid'));
      document.body.classList.toggle('pv-notes', notesOn && main.dataset.mode !== 'grid');
      notes.querySelectorAll('aside').forEach((a, i) => a.classList.toggle('on', i === cur));
    }
    function go(i, how) {
      i = Math.max(0, Math.min(slots.length - 1, i));
      const back = i < cur, changed = i !== cur;
      slots[cur].classList.remove('on', 'pv-in-fade', 'pv-in-slide', 'back');
      cur = i;
      const slot = slots[cur], tr = (slides[cur].dataset.transition || '').toLowerCase();
      slot.classList.add('on');
      if (changed && how !== 'quiet' && main.dataset.mode === 'slides' && (tr === 'fade' || tr === 'slide')) {
        slot.classList.add('pv-in-' + tr);
        slot.classList.toggle('back', back);
      }
      if (main.dataset.mode === 'grid') slot.scrollIntoView({block: 'nearest'});
      paint();
      tell({slide: slides[cur].id, n: cur + 1, of: slots.length});
      if (!framed && slides[cur].id) { try { history.replaceState(null, '', '#' + slides[cur].id); } catch (e) { /* sandbox */ } }
    }
    function mode(m) {
      main.dataset.mode = m;
      fit();
      paint();
      if (m === 'grid') slots[cur].scrollIntoView({block: 'center'});
    }
    const full = () => {
      if (framed) { tell({full: true}); return; }
      if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {});
    };
    const toggleNotes = () => { notesOn = !notesOn; tell({notes: notesOn}); fit(); paint(); };

    bar.addEventListener('click', ev => {
      const a = ev.target.closest('button') && ev.target.closest('button').dataset.a;
      if (a === 'prev') go(cur - 1); else if (a === 'next') go(cur + 1);
      else if (a === 'notes') toggleNotes();
      else if (a === 'grid') mode(main.dataset.mode === 'grid' ? 'slides' : 'grid');
      else if (a === 'full') full();
    });
    main.addEventListener('click', ev => {
      if (main.dataset.mode !== 'grid') return;
      const slot = ev.target.closest('.pv-slot');
      if (!slot) return;
      ev.preventDefault();
      go(slots.indexOf(slot), 'quiet');
      mode('slides');
    }, true);
    const KEYS = {ArrowRight: 1, ArrowDown: 1, PageDown: 1, ' ': 1, ArrowLeft: -1, ArrowUp: -1, PageUp: -1};
    function key(k, shift) {
      if (k in KEYS) { go(cur + (k === ' ' && shift ? -1 : KEYS[k])); return true; }
      if (k === 'Home') { go(0); return true; }
      if (k === 'End') { go(slots.length - 1); return true; }
      if (k === 'n' || k === 'N') { toggleNotes(); return true; }
      if (k === 'g' || k === 'G' || k === 'o' || k === 'O') { mode(main.dataset.mode === 'grid' ? 'slides' : 'grid'); return true; }
      if (k === 'Escape') { mode(main.dataset.mode === 'grid' ? 'slides' : 'grid'); return true; }
      if (k === 'Enter' && main.dataset.mode === 'grid') { mode('slides'); return true; }
      if (k === 'f' || k === 'F') { full(); return true; }
      return false;
    }
    addEventListener('keydown', ev => {
      if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.defaultPrevented) return;
      if (ev.target.closest && ev.target.closest('input, textarea, select, [contenteditable]')) return;
      if (key(ev.key, ev.shiftKey)) ev.preventDefault();
    });
    // the frame sends the keys that the user pushes while the focus is in the frame (not in the plate)
    addEventListener('message', ev => {
      if (ev.source !== parent || !ev.data || ev.data.pv !== 1) return;
      if (ev.data.op === 'deckkey' && typeof ev.data.key === 'string') key(ev.data.key, !!ev.data.shift);
      else if (ev.data.op === 'deckfull') document.body.classList.toggle('pv-deck-full', !!ev.data.on);
    });
    // a comment that the user selects in the sidebar: show its slide (runtime.js sends pv:reveal)
    document.addEventListener('pv:reveal', ev => {
      const n = ev.detail && (ev.detail.nodeType === 1 ? ev.detail : ev.detail.parentElement);
      if (!n) return;
      const slot = n.closest('.pv-slot'), note = n.closest('.pv-deck-notes > aside');
      const i = slot ? slots.indexOf(slot) : note ? [...notes.children].indexOf(note) : -1;
      if (i < 0) return;
      if (note && !notesOn) toggleNotes();
      if (main.dataset.mode === 'grid') mode('slides');
      if (i !== cur) go(i, 'quiet');
    });
    addEventListener('resize', fit);
    main.dataset.mode = 'slides';
    fit();
    slots[cur].classList.add('on');
    paint();
    tell({slide: slides[cur].id, n: cur + 1, of: slots.length, ready: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
