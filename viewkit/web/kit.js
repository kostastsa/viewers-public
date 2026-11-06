// viewkit: page code that mdview and pyview share. Load it before viewer.js. It defines one global, `kit`.
const kit = (() => {
  const DOT = '● ';

  // The text for document.title. While an answer waits for you, the title keeps its dot.
  function title(base) { return (document.title.startsWith(DOT) ? DOT : '') + base; }

  // What tells you that a chat answer has ended, and the key that opens the chat.
  //   button, box, text: the Chat button, the panel, and its text box
  //   isOpen(), setOpen(on): read and change whether the panel is open
  //   skip: a selector. In a matching element (a code editor) the hotkey does nothing, because the editor uses the key.
  // Returns {notify, opened}. Call notify() when an answer ends, and opened() when the panel opens.
  //   Panel closed: the Chat button pulses until the panel opens.
  //   Panel open, window without focus: the panel pulses and the tab title gets a dot, until you come back.
  //   ⌘/Ctrl+S opens the chat and focuses the text box. In the text box it hides the chat again. It also stops the
  //   Save dialog of the browser.
  function chatAttention({button, box, text, isOpen, setOpen, skip}) {
    function seen() {
      if (document.hidden || !document.hasFocus()) return;
      box.classList.remove('ready');
      if (document.title.startsWith(DOT)) document.title = document.title.slice(DOT.length);
      tab({ready: false});
    }
    addEventListener('focus', seen);
    document.addEventListener('visibilitychange', seen);
    document.addEventListener('keydown', ev => {
      if (!(ev.metaKey || ev.ctrlKey) || ev.shiftKey || ev.altKey || ev.code !== 'KeyS' || ev.defaultPrevented) return;
      if (skip && ev.target.closest && ev.target.closest(skip)) return;
      ev.preventDefault();
      if (isOpen() && document.activeElement === text) setOpen(false);
      else if (isOpen()) text.focus();
      else setOpen(true);
    });
    return {
      notify() {
        if (!isOpen()) button.classList.add('ready');
        if (document.hasFocus() && !document.hidden) return;
        box.classList.add('ready');
        if (!document.title.startsWith(DOT)) document.title = DOT + document.title;
        tab({ready: true});
      },
      opened() { button.classList.remove('ready'); seen(); },
    };
  }

  // A small element maker for the kit. It sets plain text only (never HTML), so the apps can keep their own helpers.
  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // The chat panel at the bottom right. The page must have the elements with these ids:
  //   chatOpen (button), chatBox (panel), chatNew, chatMin, chatMsgs, chatCtx, chatAsk (checkbox), chatText, chatSend.
  // The server keeps the conversation. The panel shows it, sends the questions, and tells you when an answer ends.
  // opts (the functions marked * are necessary):
  //   key: the localStorage prefix, e.g. 'mdview'. The panel saves KEY.chat (open or closed), KEY.chatAsk, KEY.chatSize and KEY.chatZoom.
  //   empty: the text of an empty conversation.
  //   get(since)*: a Promise of the conversation, {messages, busy, rev} (polling) or {messages, busy, cseq} (events).
  //     When polling, `since` is the last rev, and the answer can be {same: true}.
  //   post(body)*: a Promise that sends {text, ask_only, ...extra()} or {action: 'stop' | 'new'}. It fails with an Error.
  //   extra(): more fields for a question, e.g. the selected text or cell.
  //   afterSend(): called when a question has gone, e.g. to forget the selected text.
  //   context(): the chip above the text box, {text, title}, or null. hint(): the text when there is no chip.
  //   clearContext(): the × of the chip.
  //   render(text)*: a node for the Markdown text of an answer. typeset(box): typesets the maths in the messages.
  //   fillUser(e, m): fills the bubble of a question (default: its text).
  //   done(info): an answer has ended. info.changed is true if Claude changed the file.
  //   error(message): shows an error (default: alert).
  //   poll: true if the page polls get() (the server has no event stream). Otherwise call event(ev) for each chat event.
  //   ready(): when polling, false while the page cannot poll yet.
  //   skip: a selector, as for chatAttention.
  // Returns {load, event, poll, renderCtx, open, isOpen, busy, attn}. attn is the object of chatAttention.
  function chatPanel(opts) {
    const $ = id => document.getElementById(id);
    const box = $('chatMsgs'), err = opts.error || (m => alert(m));
    const C = {open: false, msgs: [], busy: false, rev: null, cseq: 0, loaded: false, loading: false, buf: [],
               askOnly: false, polling: false, tick: 0, raf: 0};
    try {
      C.askOnly = localStorage.getItem(opts.key + '.chatAsk') === '1';
      C.open = localStorage.getItem(opts.key + '.chat') === 'open';
    } catch (e) { /* the defaults */ }
    const attn = chatAttention({button: $('chatOpen'), box: $('chatBox'), text: $('chatText'),
                                isOpen: () => C.open, setOpen: on => open(on), skip: opts.skip});

    function renderCtx() {
      const el = $('chatCtx'), c = opts.context ? opts.context() : null;
      el.replaceChildren();
      if (!c) { el.textContent = opts.hint ? opts.hint() : ''; return; }
      const chip = h('span', 'chip'), x = h('button', null, '×');
      chip.title = c.title || c.text;
      x.type = 'button';
      x.title = 'Do not tell Claude about this';
      x.onclick = ev => { ev.stopPropagation(); if (opts.clearContext) opts.clearContext(); renderCtx(); };
      chip.append(h('span', null, c.text), x);
      el.append(chip);
    }

    function fillMsg(e, m) {
      e.replaceChildren();
      if (m.role === 'user') {
        if (opts.fillUser) opts.fillUser(e, m); else e.textContent = m.text;
        return;
      }
      for (const p of m.parts || []) {
        if (!p) continue;
        if (p.type === 'text') { if (p.text) e.append(opts.render(p.text)); }
        else if (p.type === 'tool') e.append(h('div', 'ctool', '▸ ' + p.name + (p.summary ? ' ' + p.summary : '')));
      }
      copyButtons(e);
      if (m.streaming && !(m.parts || []).some(Boolean)) e.append(h('div', 'ctool', 'thinking…'));
      if (m.error) e.append(h('div', 'cerr', m.error));
      if (m.stopped) e.append(h('div', 'cfoot', 'stopped'));
      else if (!m.streaming && m.cost != null) e.append(h('div', 'cfoot', '$' + Number(m.cost).toFixed(3)));
    }

    // A Copy button on each code block of an answer, so you do not have to select the code.
    function copyButtons(e) {
      for (const pre of e.querySelectorAll('pre')) {
        if (pre.parentNode.classList.contains('kitCode')) continue;
        const wrap = h('div', 'kitCode'), b = h('button', 'kitCopy', 'Copy');
        b.type = 'button';
        b.title = 'Copy this code';
        b.onclick = ev => {
          ev.stopPropagation();
          copyText(pre.textContent).then(ok => {
            b.textContent = ok ? 'Copied' : 'Cannot copy';
            clearTimeout(b.timer);
            b.timer = setTimeout(() => { b.textContent = 'Copy'; }, 1200);
          });
        };
        pre.replaceWith(wrap);
        wrap.append(pre, b);
      }
    }

    // Resize: drag the left edge, the top edge or the top-left corner of the panel. A double-click on one of them
    // gives the default size back. The size goes into --kit-chat-w and --kit-chat-h on <html>, so the app can make
    // room for the panel, and into localStorage as KEY.chatSize.
    function setSize(s) {
      const r = document.documentElement.style;
      if (!s) { r.removeProperty('--kit-chat-w'); r.removeProperty('--kit-chat-h'); return; }
      r.setProperty('--kit-chat-w', s.w + 'px');
      r.setProperty('--kit-chat-h', s.h + 'px');
    }
    function saveSize(s) {
      try { if (s) localStorage.setItem(opts.key + '.chatSize', JSON.stringify(s)); else localStorage.removeItem(opts.key + '.chatSize'); }
      catch (e) { /* not saved */ }
    }
    function grips() {
      const cb = $('chatBox'), lim = (v, lo, hi) => Math.round(Math.max(lo, Math.min(hi, v)));
      try { const s = JSON.parse(localStorage.getItem(opts.key + '.chatSize')); if (s && s.w > 0 && s.h > 0) setSize(s); }
      catch (e) { /* the default size */ }
      for (const d of ['n', 'w', 'nw']) {
        const g = h('div', 'kitGrip ' + d);
        g.title = 'Drag to resize the chat. Double-click: the default size.';
        g.addEventListener('pointerdown', ev => {
          if (ev.button !== 0) return;
          ev.preventDefault();
          g.setPointerCapture(ev.pointerId);
          const r0 = cb.getBoundingClientRect(), x0 = ev.clientX, y0 = ev.clientY;
          let size = null;
          const move = e => {
            size = {w: lim(d === 'n' ? r0.width : r0.width + x0 - e.clientX, 240, innerWidth - 32),
                    h: lim(d === 'w' ? r0.height : r0.height + y0 - e.clientY, 180, innerHeight - 32)};
            setSize(size);
          };
          const up = () => {
            g.removeEventListener('pointermove', move);
            g.removeEventListener('pointerup', up);
            g.removeEventListener('pointercancel', up);
            document.body.classList.remove('kitResizing');
            if (size) saveSize(size);
          };
          document.body.classList.add('kitResizing');
          g.addEventListener('pointermove', move);
          g.addEventListener('pointerup', up);
          g.addEventListener('pointercancel', up);
        });
        g.addEventListener('dblclick', () => { setSize(null); saveSize(null); });
        cb.append(g);
      }
    }

    // The text size of the chat. With the focus in the panel (or after a click in it): Cmd/Ctrl+= larger,
    // Cmd/Ctrl+- smaller, Cmd/Ctrl+0 the default. A pinch or Ctrl+wheel on the panel does the same, and does not zoom
    // the document or the page. The size goes into --kit-chat-zoom on <html> (the CSS zoom of the messages, the chip
    // and the text box, so the px sizes of the app scale too), and into localStorage as KEY.chatZoom.
    function textSize() {
      const cb = $('chatBox'), r = document.documentElement.style;
      let z = 1, g = 0, inside = false, saveTimer = 0;
      const set = (v, save) => {
        z = Math.round(Math.max(0.7, Math.min(2.5, v)) * 100) / 100;
        if (z === 1) r.removeProperty('--kit-chat-zoom'); else r.setProperty('--kit-chat-zoom', String(z));
        if (!save) return;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          try { if (z === 1) localStorage.removeItem(opts.key + '.chatZoom'); else localStorage.setItem(opts.key + '.chatZoom', String(z)); }
          catch (e) { /* not saved */ }
        }, 200);
      };
      try { const v = parseFloat(localStorage.getItem(opts.key + '.chatZoom')); if (v > 0) set(v, false); }
      catch (e) { /* the default size */ }
      document.addEventListener('pointerdown', ev => { inside = cb.contains(ev.target); }, true);
      window.addEventListener('keydown', ev => {
        if (!(ev.metaKey || ev.ctrlKey) || ev.altKey || !C.open) return;
        const a = document.activeElement;
        if (!(cb.contains(a) || (inside && !(a && a.closest('input,textarea,select,[contenteditable]'))))) return;
        const k = ev.code === 'Equal' || ev.key === '=' || ev.key === '+' ? 1 : ev.code === 'Minus' || ev.key === '-' ? -1
                : ev.code === 'Digit0' || ev.key === '0' ? 0 : null;
        if (k === null) return;
        ev.preventDefault();
        ev.stopPropagation();
        set(k === 0 ? 1 : k > 0 ? z * 1.1 : z / 1.1, true);
      }, true);
      cb.addEventListener('wheel', ev => {
        if (!ev.ctrlKey || g) return;  // a plain wheel scrolls the messages
        ev.preventDefault();
        ev.stopPropagation();
        set(z * Math.exp(-ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.01)), true);
      }, {passive: false});
      for (const [type, fn] of [  // Safari: a pinch is a gesture with a scale from its start (g: the size at the start)
        ['gesturestart', () => { g = z; }],
        ['gesturechange', ev => { if (g) set(g * ev.scale, true); }],
        ['gestureend', () => { g = 0; }],
      ]) cb.addEventListener(type, ev => { ev.preventDefault(); ev.stopPropagation(); fn(ev); }, {passive: false});
    }

    function msgEl(m) { const e = h('div', 'cm ' + m.role); fillMsg(e, m); return e; }
    function bottom() { box.scrollTop = box.scrollHeight; }
    function nearBottom() { return box.scrollHeight - box.scrollTop - box.clientHeight < 60; }

    function render() {
      const atBottom = nearBottom(), kids = box.children;
      if (C.busy && C.msgs.length && kids.length === C.msgs.length && kids[0].classList.contains('cm'))
        fillMsg(kids[kids.length - 1], C.msgs[C.msgs.length - 1]);  // while an answer is written: only its own bubble
      else if (C.msgs.length) box.replaceChildren(...C.msgs.map(msgEl));
      else box.replaceChildren(h('div', 'cempty', opts.empty || ''));
      if (atBottom) bottom();
      if (opts.typeset) opts.typeset(box);
      const b = $('chatSend');
      b.textContent = C.busy ? 'Stop' : 'Send';
      b.classList.toggle('stop', C.busy);
      $('chatAsk').checked = C.askOnly;
      $('chatNew').disabled = C.busy;
      tab({chat: C.busy});
    }

    function renderLast() {  // while an answer streams in: only its own bubble, at most once per frame
      C.raf = 0;
      const last = box.lastElementChild, m = C.msgs[C.msgs.length - 1];
      if (!last || !m || m.role !== 'assistant') return;
      const atBottom = nearBottom();
      fillMsg(last, m);
      if (atBottom) bottom();
    }

    function finished(info) { attn.notify(); if (opts.done) opts.done(info || {}); }

    // Polling: ask for the conversation, and get only a "same" when nothing changed since the last rev.
    async function poll() {
      if (C.polling) return;
      C.polling = true;
      try {
        const d = await opts.get(C.rev);
        if (!d || d.same) return;
        const was = C.busy;
        Object.assign(C, {rev: d.rev, busy: d.busy, msgs: d.messages});
        render();
        if (was && !d.busy) finished(d.messages[d.messages.length - 1]);
      } catch (e) { /* the next poll tries again */ }
      finally { C.polling = false; }
    }

    // Events: load the whole conversation once (again after each new connection), then apply the events in order.
    async function load() {
      C.loading = true; C.buf = [];
      try {
        const d = await opts.get(null);
        Object.assign(C, {msgs: d.messages, busy: d.busy, cseq: d.cseq, loaded: true});
        render();
      } catch (e) { /* the panel stays as it is; the next connection tries again */ }
      C.loading = false;
      const held = C.buf; C.buf = [];
      held.forEach(event);
    }

    function event(ev) {
      if (C.loading) { C.buf.push(ev); return; }  // the conversation is being fetched: apply this after it
      if (ev.cseq <= C.cseq || !C.loaded) return;
      C.cseq = ev.cseq;
      const m = C.msgs[C.msgs.length - 1];
      if (ev.k === 'start') { C.msgs.push(ev.user, {role: 'assistant', parts: [], streaming: true}); C.busy = true; render(); bottom(); }
      else if (ev.k === 'reset') { C.msgs = []; C.busy = false; render(); }
      else if (!m || m.role !== 'assistant') return;
      else if (ev.k === 'delta' || ev.k === 'tool') {
        if (ev.k === 'delta') { m.parts[ev.i] = m.parts[ev.i] || {type: 'text', text: ''}; m.parts[ev.i].text += ev.text; }
        else m.parts[ev.i] = ev.part;
        if (!C.raf) C.raf = requestAnimationFrame(renderLast);
      } else if (ev.k === 'done') {
        Object.assign(m, {streaming: false, error: ev.error || undefined, stopped: ev.stopped, cost: ev.cost});
        C.busy = false;
        render();
        finished({changed: ev.changed});
      }
    }

    function open(on) {
      C.open = on;
      $('chatBox').hidden = !on;
      $('chatOpen').hidden = on;
      document.body.classList.toggle('chatopen', on);
      if (on) attn.opened();
      try { localStorage.setItem(opts.key + '.chat', on ? 'open' : 'closed'); } catch (e) { /* not saved */ }
      if (on) { renderCtx(); bottom(); setTimeout(() => $('chatText').focus(), 0); if (opts.poll) poll(); }
    }

    async function send() {
      const t = $('chatText'), text = t.value.trim();
      if (!text) return;
      if (C.busy) { err('Claude is still answering. Press Stop first.'); return; }
      try {
        await opts.post(Object.assign({text, ask_only: C.askOnly}, opts.extra ? opts.extra() : {}));
        t.value = '';
        if (opts.afterSend) opts.afterSend();
        renderCtx();
        if (opts.poll) { await poll(); bottom(); }
      } catch (e) { err(e.message); }
    }

    function action(name) {
      return opts.post({action: name}).then(() => { if (opts.poll) return poll(); }).catch(e => err(e.message));
    }

    $('chatOpen').onclick = () => open(true);
    $('chatMin').onclick = () => open(false);
    $('chatSend').onclick = () => C.busy ? action('stop') : send();
    $('chatNew').onclick = () => {
      if (C.msgs.length && !confirm('Start a new conversation? The old one is removed.')) return;
      action('new');
    };
    $('chatAsk').onchange = ev => {
      C.askOnly = ev.target.checked;
      try { localStorage.setItem(opts.key + '.chatAsk', C.askOnly ? '1' : '0'); } catch (e) { /* not saved */ }
    };
    $('chatText').addEventListener('keydown', ev => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send(); }
      else if (ev.key === 'Escape') ev.target.blur();
    });
    grips();
    textSize();
    render();
    open(C.open);
    if (opts.poll) {
      setInterval(() => {  // fast while an answer is written; now and then otherwise, for a second tab
        if ((opts.ready && !opts.ready()) || (document.hidden && !C.busy)) return;  // a hidden tab still follows an answer
        C.tick++;
        if (C.busy || (C.open && C.tick % 8 === 0)) poll();
      }, 500);
      poll();
    }
    return {load, event, poll, renderCtx, open, isOpen: () => C.open, busy: () => C.busy, attn};
  }

  // ------------------------------------------------------------ Markdown and maths
  // Inline Markdown: `code`, maths, **bold**, *italic* or _italic_, and [links](https://...). Plain text only, so it is safe.
  // Maths, as in pandoc: $$…$$ and \[…\] (display), \(…\) and $…$ (inline). $…$ needs no space just inside the dollars and
  // no digit right after the closing one, so "costs $5 and $10" stays text. \$ is a literal dollar.
  const INLINE = [
    ['code', /`([^`\n]+)`/g],
    ['math', /(?<!\\)\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$(?![\s$])((?:\\.|[^$\\])+?)(?<![\s\\])\$(?!\d)/g],
    ['link', /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g],
    ['bold', /\*\*([^*\n]+)\*\*/g],
    ['em', /(?<![\w*])\*([^*\n]+)\*(?![\w*])|(?<!\w)_([^_\n]+)_(?!\w)/g],
  ];

  function inline(text) {
    const frag = document.createDocumentFragment();
    let pos = 0;
    for (;;) {
      let best = null;
      for (const [kind, re] of INLINE) {
        re.lastIndex = pos;
        const m = re.exec(text);
        if (m && (!best || m.index < best.m.index)) best = {kind, m};
      }
      if (!best) break;
      const {kind, m} = best;
      frag.append(text.slice(pos, m.index).replace(/\\\$/g, '$'));
      if (kind === 'code') frag.append(h('code', null, m[1]));
      else if (kind === 'math') {
        const display = m[1] != null || m[2] != null, tex = m[1] ?? m[2] ?? m[3] ?? m[4];
        frag.append(h('span', 'math ' + (display ? 'display' : 'inline'), display ? `\\[${tex}\\]` : `\\(${tex}\\)`));
      } else if (kind === 'link') {
        const a = h('a', null, m[1]);
        a.href = m[2]; a.target = '_blank'; a.rel = 'noopener noreferrer';
        frag.append(a);
      } else { const e = h(kind === 'bold' ? 'strong' : 'em'); e.append(inline(m[1] ?? m[2])); frag.append(e); }
      pos = m.index + m[0].length;
    }
    frag.append(text.slice(pos).replace(/\\\$/g, '$'));
    return frag;
  }

  // A little Markdown, for chat answers and comments: paragraphs, headings, lists, code blocks, display maths, and the
  // inline part above. Returns a div.md.
  function md(text) {
    const root = h('div', 'md'), lines = String(text).replace(/\r/g, '').split('\n');
    let para = [], list = null;
    const flush = () => {
      if (!para.length) return;
      if (para.every(l => l.trim().startsWith('|'))) root.append(h('pre', null, para.join('\n')));  // a table: as it is
      else { const p = h('p'); p.append(inline(para.join('\n'))); root.append(p); }
      para = [];
    };
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      if (/^\s*```/.test(l)) {
        flush(); list = null;
        const code = [];
        for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
        const pre = h('pre'); pre.append(h('code', null, code.join('\n'))); root.append(pre);
        continue;
      }
      const li = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(l), hd = /^#{1,6}\s+(.*)$/.exec(l);
      if (li) {
        flush();
        const tag = li[1] ? 'ul' : 'ol';
        if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); root.append(list); }
        const item = h('li'); item.append(inline(li[3])); list.append(item);
      } else if (hd) { flush(); list = null; const e = h('h4'); e.append(inline(hd[1])); root.append(e); }
      else if (!l.trim()) { flush(); list = null; }
      else if (list && /^\s/.test(l)) list.lastChild.append(' ', inline(l.trim()));
      else { list = null; para.push(l); }
    }
    flush();
    return root;
  }

  // Typeset the maths spans (from md) in these elements. The first call loads the local MathJax copy of the kit
  // (/kit/vendor/mathjax), so nothing loads from the internet. One typeset runs at a time, as MathJax asks.
  let mathQueue = Promise.resolve(), mathWait = [];
  function typeset(roots) {
    roots = [].concat(roots || []).filter(r => r && r.querySelector && r.querySelector('.math'));
    if (!roots.length) return;
    if (window.MathJax && MathJax.typesetPromise) {
      mathQueue = mathQueue.then(() => MathJax.typesetPromise(roots)).catch(() => {});
      return;
    }
    mathWait.push(...roots);
    if (window.MathJax) return;  // still loading: the startup typesets the waiting elements
    window.MathJax = {
      tex: {inlineMath: [['\\(', '\\)']], displayMath: [['\\[', '\\]']]},
      options: {enableMenu: false},  // the menu needs parts that the local copy does not have
      startup: {typeset: false, ready() {
        MathJax.startup.defaultReady();
        MathJax.startup.promise.then(() => { const w = mathWait; mathWait = []; typeset(w.filter(r => r.isConnected)); });
      }},
    };
    const s = document.createElement('script');
    s.src = '/kit/vendor/mathjax/tex-chtml.js';
    s.async = true;
    document.head.appendChild(s);
  }

  // ------------------------------------------------------------ the tab icon
  // Each viewer has its own icon, so you know its browser tabs at once: mdview a blue M↓, pyview a yellow-green Py,
  // pdfview a red page, pageview a teal square with bars, plateview a slate square with panels. The icon also shows the state of the document:
  //   open: the number of open comments, in a badge at the bottom right
  //   chat, oracle: Claude works (the chat or the Oracle): a cyan dot at the top right
  //   ready: an answer waits for you: a yellow dot at the top right (the chat panel and chatAttention set it)
  // Call kit.tab({app: 'md' | 'py' | 'pdf' | 'page' | 'plate'}) once, then kit.tab({...}) with the parts that change.
  const TAB = {app: null, open: 0, chat: false, oracle: false, ready: false};
  let tabLink = null, tabKey = '';

  function drawIcon(ctx, app) {
    const S = 64, round = (x, y, w, hh, r) => {
      ctx.beginPath();
      ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + hh, r); ctx.arcTo(x + w, y + hh, x, y + hh, r);
      ctx.arcTo(x, y + hh, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
    };
    ctx.clearRect(0, 0, S, S);
    if (app === 'pdf') {  // a red page with a folded corner, and three lines of text
      ctx.fillStyle = '#d93a32';
      ctx.beginPath();
      ctx.moveTo(10, 4); ctx.lineTo(40, 4); ctx.lineTo(54, 18); ctx.lineTo(54, 60); ctx.lineTo(10, 60); ctx.closePath();
      ctx.fill();
      ctx.fillStyle = '#f7a29c';
      ctx.beginPath(); ctx.moveTo(40, 4); ctx.lineTo(40, 18); ctx.lineTo(54, 18); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 15px system-ui, -apple-system, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('PDF', 32, 44);
      ctx.fillRect(17, 26, 20, 4);
      return;
    }
    if (app === 'plate') {  // a slate square with three panels, one gold: a plate of figures and tables
      round(4, 4, 56, 56, 13);
      ctx.fillStyle = '#2f3d5c';
      ctx.fill();
      ctx.fillStyle = '#f2b134';
      round(12, 12, 18, 17, 3); ctx.fill();
      ctx.fillStyle = '#ffffff';
      round(34, 12, 18, 17, 3); ctx.fill();
      round(12, 34, 40, 18, 3); ctx.fill();
      return;
    }
    if (app === 'page') {  // a teal square with three rising bars: a page of tables and charts
      round(4, 4, 56, 56, 13);
      ctx.fillStyle = '#16857a';
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.fillRect(14, 47, 36, 4);
      ctx.fillRect(16, 33, 8, 12); ctx.fillRect(28, 24, 8, 21); ctx.fillRect(40, 14, 8, 31);
      return;
    }
    round(4, 4, 56, 56, 13);
    ctx.fillStyle = app === 'py' ? '#9cc230' : '#3a6fd8';
    ctx.fill();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    if (app === 'py') {
      ctx.fillStyle = '#1d2a06';
      ctx.font = 'bold 30px system-ui, -apple-system, sans-serif';
      ctx.fillText('Py', 32, 34);
    } else {  // the Markdown mark: M and a down arrow
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 30px system-ui, -apple-system, sans-serif';
      ctx.fillText('M', 24, 34);
      ctx.fillRect(43, 18, 6, 16);
      ctx.beginPath(); ctx.moveTo(37, 32); ctx.lineTo(55, 32); ctx.lineTo(46, 44); ctx.closePath(); ctx.fill();
    }
  }

  // The base of the tab icon is the icon of the app as macOS shows it (web/icons/APP-app.png, tests/app_icons.py), so
  // the tab and the app have the same icon (pdfview keeps the flat red page: FLAT in tests/app_icons.py). While it loads, the tab keeps the icon of the page (APP.png, the same
  // picture). If it is missing, the tab uses the flat drawing of drawIcon.
  const BASE = {};  // app -> 'loading' | 'none' | Image
  function baseIcon(app) {
    if (BASE[app] === undefined) {
      BASE[app] = 'loading';
      const img = new Image();
      img.onload = () => { BASE[app] = img; tabKey = ''; drawTab(); };
      img.onerror = () => { BASE[app] = 'none'; tabKey = ''; drawTab(); };
      img.src = `/kit/icons/${app}-app.png`;
    }
    return BASE[app];
  }

  function drawTab() {
    if (!TAB.app) return;
    const base = baseIcon(TAB.app);
    if (base === 'loading') return;
    const busy = TAB.chat || TAB.oracle, key = [TAB.app, TAB.open, busy, TAB.ready].join();
    if (key === tabKey) return;
    tabKey = key;
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const ctx = c.getContext('2d');
    if (base === 'none') drawIcon(ctx, TAB.app);
    else { ctx.imageSmoothingQuality = 'high'; ctx.drawImage(base, 0, 0, 64, 64); }
    if (TAB.ready || busy) {  // the dot: yellow for an answer that waits, cyan while Claude works
      ctx.beginPath(); ctx.arc(54, 10, 9, 0, 2 * Math.PI);
      ctx.fillStyle = TAB.ready ? '#ffd60a' : '#2fd4ec'; ctx.fill();
      ctx.lineWidth = 3; ctx.strokeStyle = '#111'; ctx.stroke();
    }
    if (TAB.open > 0) {  // the badge: the number of open comments
      const t = TAB.open > 9 ? '9+' : String(TAB.open);
      ctx.beginPath(); ctx.arc(51, 51, 13, 0, 2 * Math.PI);
      ctx.fillStyle = '#ff9418'; ctx.fill();
      ctx.lineWidth = 3; ctx.strokeStyle = '#111'; ctx.stroke();
      ctx.fillStyle = '#111';
      ctx.font = `bold ${t.length > 1 ? 14 : 20}px system-ui, -apple-system, sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(t, 51, 52);
    }
    if (!tabLink) {
      document.querySelectorAll('link[rel~="icon"]').forEach(l => l.remove());
      tabLink = document.createElement('link');
      tabLink.rel = 'icon';
      tabLink.type = 'image/png';
      document.head.appendChild(tabLink);
    }
    tabLink.href = c.toDataURL('image/png');
  }

  function tab(o) { Object.assign(TAB, o || {}); drawTab(); }

  // A PNG of the flat drawing of the icon of an app, as a data URL, `size` pixels wide (default 64).
  // tests/make_icons.py uses it to make web/icons/APP-1024.png, the source of the Finder icon of the macOS app.
  function iconURL(app, size = 64) {
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const ctx = c.getContext('2d');
    ctx.scale(size / 64, size / 64);
    drawIcon(ctx, app);
    return c.toDataURL('image/png');
  }

  // ------------------------------------------------------------ the title and the path of a document
  // The label comes from the server (serverkit.doc_label): {name, context, crumbs, path, abs}.
  // The title is the name, then the context: "paper.pdf · thesis/reading". The tab icon tells the app.
  function docTitle(app, label) {
    document.title = title(label.name + (label.context ? ' · ' + label.context : ''));
  }

  // Copy text to the clipboard. A Promise of true when it worked.
  function copyText(text) {
    const old = () => {  // the old way, for a page that has no navigator.clipboard
      const t = h('textarea');
      t.value = text;
      t.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.append(t);
      t.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { /* not copied */ }
      t.remove();
      return ok;
    };
    if (navigator.clipboard && navigator.clipboard.writeText)
      return navigator.clipboard.writeText(text).then(() => true, () => old());
    return Promise.resolve(old());
  }

  // The path in the page header: "thesis › ch2 › report.md". The full path shows when the pointer is on it.
  // A click on the name copies the full path; onCopy(message) can tell the user.
  function crumbs(el, label, onCopy) {
    el.replaceChildren();
    el.classList.add('crumbs');
    for (const c of label.crumbs || []) el.append(h('span', 'crumb', c), h('span', 'sep', ' › '));
    const n = h('b', 'crumbName', label.name);
    n.title = label.path + ' (click to copy the full path)';
    n.addEventListener('click', ev => {
      ev.stopPropagation();
      const done = () => onCopy && onCopy('Copied: ' + label.abs);
      if (navigator.clipboard) navigator.clipboard.writeText(label.abs).then(done, () => onCopy && onCopy(label.abs));
      else if (onCopy) onCopy(label.abs);
    });
    el.append(n);
    el.title = label.path;
  }

  // ------------------------------------------------------------ the dashboard (self-improving apps)
  // ⌘/Ctrl+Shift+. opens and closes the dashboard of the app. A note is about the app, not about the document.
  //   Right: "New note", a text box. Add (or Enter) puts the note in the list. It does not send it.
  //   Left: "Notes to send", the notes that wait (a click on a note edits it, × deletes it), and Send all, which sends
  //     each note to the automatic loop (improvekit.dash_api: an issue, then a run, then a preview tab).
  //   Below: "Status", the runs of the notes, with Open for a preview and GitHub for a question or a failure. A note
  //     with a question of Claude (or a failure) shows the text and a box for the answer: Answer (or Try again)
  //     starts the run again on the same note.
  //   A new note has a scope: "All viewers" (the change goes into viewkit if it can; the default) or "Only APP".
  //   app: the name of the app
  //   get(): a Promise of {drafts, runs, auto}; post(body): sends {action, ...} and returns the same
  //   context(): the state of the page to keep with a new note (page, zoom, panels). Never the text or the path of the
  //     document. The dashboard adds the last buttons that the user clicked.
  //   onDone(message, label, fn): optional, to tell the user (a toast). The page follows the runs, also with the
  //     dashboard closed, and tells the user when a preview is open, or when Claude asks a question or fails.
  // Returns {open, close}.
  function improveDash({app, get, post, context, onDone}) {
    const clicks = [];
    document.addEventListener('click', ev => {
      const b = ev.target.closest && ev.target.closest('button, [role="button"], a, input, select');
      if (!b || b.closest('.kitDash')) return;
      const label = b.id || b.getAttribute('aria-label') || (b.textContent || '').trim().slice(0, 24) || b.tagName.toLowerCase();
      clicks.push(label);
      if (clicks.length > 8) clicks.shift();
    }, true);
    const LABEL = {queued: 'waits', working: 'Claude works', testing: 'tests run', preview: 'preview ready',
                   changing: 'Claude changes it', accepting: 'merging', shipped: 'shipped', rejecting: 'closing',
                   rejected: 'rejected', question: 'question', failed: 'failed'};
    const BUSY = ['queued', 'working', 'testing', 'changing', 'accepting', 'rejecting'];
    let box = null, data = {drafts: [], runs: []}, known = null, timer = null, scope = 'all';
    const answers = {};  // the answers that the user writes, kept while the list draws again
    const btn = (label, cls, title) => { const b = h('button', cls, label); b.type = 'button'; if (title) b.title = title; return b; };
    const tell = (m, label, fn) => onDone && onDone(m, label, fn);

    // Tell the user when a run comes to a state that needs them. `known` holds the last state of each run.
    function news(runs) {
      const now = {};
      for (const r of runs) now[r.n] = r.state;
      if (known) for (const r of runs) {
        if (known[r.n] === r.state) continue;
        if (r.state === 'preview' && known[r.n] !== 'preview') tell(`The preview of note #${r.n} is open in a new tab.`, 'Open', () => window.open(r.url, '_blank'));
        else if (r.state === 'question') tell(`Claude asks a question on note #${r.n}.`, 'GitHub', () => window.open(r.issue, '_blank'));
        else if (r.state === 'failed') tell(`Note #${r.n}: ${r.message || 'failed'}`, r.issue ? 'GitHub' : null, () => window.open(r.issue, '_blank'));
      }
      known = now;
    }
    async function refresh() {
      try { data = await get(); } catch (e) { return; }
      news(data.runs || []);
      if (box) draw();
      const busy = (data.runs || []).some(r => BUSY.includes(r.state));
      clearTimeout(timer);
      if (busy || box) timer = setTimeout(refresh, box ? 3000 : 5000);
    }
    async function act(body) {
      try { data = await post(body); news(data.runs || []); draw(); return true; }
      catch (e) { if (box) box.querySelector('.kitDashErr').textContent = e.message || String(e); return false; }
    }

    function draw() {
      const list = box.querySelector('.kitDashList'), runsEl = box.querySelector('.kitDashRuns');
      const ds = data.drafts || [];
      box.querySelector('.kitDashCount').textContent = ds.length ? `(${ds.length})` : '';
      const send = box.querySelector('.kitDashSend');
      send.disabled = !ds.length;
      send.textContent = ds.length ? `Send all (${ds.length})` : 'Send all';
      if (!list.contains(document.activeElement)) {
        list.replaceChildren(...(ds.length ? ds.map(d => {
          const li = h('div', 'kitDashNote');
          const t = h('div', 'kitDashText', d.text);
          t.title = 'Click to edit';
          t.tabIndex = 0;
          t.addEventListener('click', () => {
            const ta = h('textarea');
            ta.value = d.text;
            ta.rows = Math.min(6, Math.max(2, d.text.split('\n').length));
            const save = async () => { const v = ta.value.trim(); if (v && v !== d.text) await act({action: 'edit', id: d.id, text: v}); else draw(); };
            ta.addEventListener('keydown', ev => {
              if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); ta.blur(); }
              else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); ta.value = d.text; ta.blur(); }
            });
            ta.addEventListener('blur', save);
            t.replaceWith(ta);
            ta.focus();
          });
          const x = btn('×', 'kitDashDel', 'Delete this note');
          x.addEventListener('click', () => act({action: 'delete', id: d.id}));
          li.append(t, h('span', 'kitDashScope', d.scope === 'app' ? 'only ' + app : 'all viewers'), x);
          return li;
        }) : [h('div', 'kitDashEmpty', 'No notes wait. Write one on the right.')]));
      }
      const rs = data.runs || [];
      if (runsEl.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA') return;
      runsEl.replaceChildren(...(rs.length ? rs.map(r => {
        const row = h('div', 'kitDashRun ' + r.state);
        row.append(h('span', 'kitDashN', '#' + r.n), h('span', 'kitDashTitle', r.title || ''));
        if (r.kit) row.append(h('span', 'kitDashScope', 'all viewers'));
        row.append(h('span', 'kitDashState', LABEL[r.state] || r.state));
        if (r.state === 'preview' && r.url) { const o = btn('Open', 'kitDashOpen', 'Open the preview tab'); o.addEventListener('click', () => window.open(r.url, '_blank')); row.append(o); }
        else if (r.issue) { const g = btn('GitHub', 'kitDashOpen', 'Open the note on GitHub'); g.addEventListener('click', () => window.open(r.issue, '_blank')); row.append(g); }
        if (r.error || r.message) row.title = (r.message || '') + (r.error ? '\n' + r.error : '');
        if (r.state !== 'question' && r.state !== 'failed') return row;
        // Claude asks a question, or the run failed: the text, and a box for the answer
        const wrap = h('div', 'kitDashAsk');
        const q = h('div', 'kitDashQ');
        q.append(md((r.error || r.message || '').replace(/^QUESTION:\s*/i, '')));
        const ta = h('textarea');
        ta.rows = 2;
        ta.value = answers[r.n] || '';
        ta.placeholder = r.state === 'question' ? 'Your answer, for example "1 and 2" (Enter sends)' : 'Optional: what to do in a different way (Enter tries again)';
        ta.addEventListener('input', () => { answers[r.n] = ta.value; });
        const go = btn(r.state === 'question' ? 'Answer' : 'Try again', 'kitDashSend', 'Claude starts again on this note, with your answer');
        const send = async () => {
          const text = ta.value.trim();
          if (r.state === 'question' && !text) { ta.focus(); return; }
          go.disabled = true;
          if (await act({action: 'answer', n: r.n, text})) { delete answers[r.n]; tell(`Claude works on note #${r.n} again.`); refresh(); }
          else go.disabled = false;
        };
        go.addEventListener('click', send);
        ta.addEventListener('keydown', ev => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send(); } });
        const line = h('div', 'kitDashAnswer');
        line.append(ta, go);
        wrap.append(row, q, line);
        return wrap;
      }) : [h('div', 'kitDashEmpty', 'No runs yet.')]));
    }

    function open() {
      if (box) { box.querySelector('.kitDashNew textarea').focus(); return; }
      box = h('div', 'kitDash');
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-label', 'Improve ' + app);
      const head = h('div', 'kitDashHead');
      const x = btn('×', 'kitDashClose', 'Close (Esc or ⌘/Ctrl+Shift+.)');
      x.addEventListener('click', close);
      head.append(h('b', null, 'Improve ' + app), h('span', 'kitDashHint', 'Notes about the app. They wait here until you send them.'), x);
      const left = h('div', 'kitDashCol');
      const lh = h('div', 'kitDashSub');
      lh.append('Notes to send ', h('span', 'kitDashCount'));
      const send = btn('Send all', 'kitDashSend', 'Send each note to Claude: a proposal and a preview tab for each note');
      send.addEventListener('click', async () => {
        const k = (data.drafts || []).length;
        send.disabled = true;
        send.textContent = 'Sending…';
        if (await act({action: 'send'})) {
          const s = data.sent || [];
          const runs = s.filter(r => r.run).length, kept = s.filter(r => r.queued).length, filed = s.filter(r => r.url).length;
          const nn = c => `${c} note${c === 1 ? '' : 's'}`;
          tell((runs ? `${nn(runs)} sent to Claude. A preview tab opens for each one when it is ready.`
                     : filed ? `${nn(filed)} sent to GitHub. The automatic loop is off.` : '') +
               (kept ? ` ${nn(kept)} kept on this computer (no network). They go with the next notes.` : '') +
               (s.length < k ? ` ${nn(k - s.length)} not sent: see the list.` : ''));
          refresh();
        }
      });
      left.append(lh, h('div', 'kitDashList'), send);
      const right = h('div', 'kitDashCol kitDashNew');
      const ta = h('textarea');
      ta.placeholder = `What is wrong with ${app}, or what do you want it to do?`;
      ta.rows = 5;
      const add = btn('Add', 'kitDashAdd', 'Put the note in the list (Enter)');
      const doAdd = async () => {
        const text = ta.value.trim();
        if (!text) return;
        let ctx = {};
        try { ctx = (context && context()) || {}; } catch (e) { /* no context */ }
        ta.value = '';  // at once: the user can write the next note while this one goes to the server
        ta.focus();
        if (!await act({action: 'add', text, scope, context: {...ctx, clicks: clicks.slice()}}) && !ta.value) ta.value = text;
      };
      add.addEventListener('click', doAdd);
      ta.addEventListener('keydown', ev => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); doAdd(); } });
      // the scope of the new note: all the viewers (the default), or only this app
      const sc = h('div', 'kitDashScopes');
      sc.setAttribute('role', 'radiogroup');
      sc.setAttribute('aria-label', 'The change is for');
      const opts = [['all', 'All viewers', 'The change goes into viewkit if it can, so all the viewers get it'],
                    ['app', 'Only ' + app, 'Only ' + app + ' changes']];
      const scBtns = opts.map(([v, label, title]) => {
        const b = btn(label, 'kitDashOpt', title);
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-checked', String(v === scope));
        b.addEventListener('click', () => { scope = v; scBtns.forEach((x, i) => x.setAttribute('aria-checked', String(opts[i][0] === v))); });
        return b;
      });
      sc.append(h('span', 'kitDashKeys', 'For:'), ...scBtns);
      const addRow = h('div', 'kitDashAddRow');
      addRow.append(add, sc);
      right.append(h('div', 'kitDashSub', 'New note'), ta, h('div', 'kitDashKeys', 'Enter adds · Shift+Enter: new line · Esc closes'), addRow);
      const cols = h('div', 'kitDashCols');
      cols.append(left, right);
      const st = h('div', 'kitDashSub', 'Status');
      box.append(head, cols, st, h('div', 'kitDashRuns'), h('div', 'kitDashErr'));
      box.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !ev.defaultPrevented) { ev.preventDefault(); close(); } });
      document.body.append(box);
      draw();
      ta.focus();
      refresh();
    }
    function close() { if (box) { box.remove(); box = null; } }
    document.addEventListener('keydown', ev => {
      if (!(ev.metaKey || ev.ctrlKey) || !ev.shiftKey || ev.altKey || ev.code !== 'Period') return;
      ev.preventDefault();
      if (box) close(); else open();
    });
    refresh();  // a run that goes on after a reload: the page still tells the user at its end
    return {open, close};
  }

  // ------------------------------------------------------------ the preview band (self-improving apps)
  // In the sandbox server of a proposal, a card at the top shows the note, the summary of Claude, and three buttons:
  // Accept (merge the change into the app), Change… (feedback: Claude changes the proposal, and the page reloads), and
  // Reject (close the note, with an optional reason). The server does the work in the background (improverun.py).
  //   get(): a Promise of the status ({preview: false} outside a preview)
  //   post(body): sends {action, text}, a Promise of the new status
  async function previewBand({get, post}) {
    let st;
    try { st = await get(); } catch (e) { return; }
    if (!st || !st.preview) return;
    const rev = st.rev;
    const card = h('div', 'kitPreview');
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Preview of a proposal');
    const head = h('div', 'kitPreviewHead');
    const name = h('b', null, `Preview of note #${st.n}`);
    const ttl = h('span', 'kitPreviewTitle', st.title || '');
    const min = h('button', 'kitPreviewMin', '–');
    min.title = 'Make the card small';
    min.type = 'button';
    head.append(name, ttl, min);
    const sum = h('div', 'kitPreviewSum');
    // the files of the preview: the document of the note, then the samples of the app (copies)
    const files = h('div', 'kitPreviewFiles');
    const here = new URLSearchParams(location.search).get('f');
    for (const f of st.files || []) {
      const u = new URL(f.url, location.href), a = h('a', null, f.name.replace(/^sample: /, ''));
      a.href = u.pathname + u.search.replace(/[?&]t=[^&]*/, '').replace(/^&/, '?');
      if (f.name.startsWith('sample: ')) a.classList.add('sample');
      if (u.searchParams.get('f') === here) a.classList.add('here');
      a.title = f.name.startsWith('sample: ') ? 'A sample file of the app (a copy)' : 'The document of the note (a copy)';
      files.append(a);
    }
    if (files.children.length < 2) files.hidden = true;
    else files.prepend(h('span', 'kitPreviewKeys', 'Try it on:'));
    const msg = h('div', 'kitPreviewMsg');
    const err = h('div', 'kitPreviewErr');
    const row = h('div', 'kitPreviewRow');
    const btn = (label, cls, title) => { const b = h('button', cls, label); b.type = 'button'; b.title = title; return b; };
    const acc = btn('Accept', 'kitAccept', 'Merge this change into the app and commit it');
    const chg = btn('Change…', '', 'Tell Claude what to change in this proposal');
    const rej = btn('Reject', 'kitReject', 'Close the note without the change');
    row.append(acc, chg, rej);
    const form = h('div', 'kitPreviewForm');
    form.hidden = true;
    const ta = h('textarea');
    ta.rows = 2;
    const send = btn('Send', 'kitAccept', '');
    form.append(ta, send);
    card.append(head, sum, files, row, form, msg, err);
    document.body.append(card);
    document.title = `Preview #${st.n} · ` + document.title;
    let mode = null;
    function show(s) {
      sum.replaceChildren(md(s.summary || ''));
      msg.textContent = s.state === 'preview' ? '' : (s.message || s.state);
      err.textContent = s.error || '';
      const busy = s.state !== 'preview';
      for (const b of [acc, chg, rej, send]) b.disabled = busy;
      card.classList.toggle('busy', busy);
    }
    function ask(m) {
      mode = mode === m ? null : m;
      form.hidden = !mode;
      ta.value = '';
      ta.placeholder = mode === 'change' ? 'What should Claude change? (Enter sends)' : 'Why? (optional, Enter rejects)';
      send.textContent = mode === 'change' ? 'Send' : 'Reject';
      if (mode) ta.focus();
    }
    async function act(action, text) {
      form.hidden = true;
      mode = null;
      try { show(await post({action, text})); } catch (e) { err.textContent = e.message || String(e); }
    }
    min.addEventListener('click', () => { card.classList.toggle('small'); min.textContent = card.classList.contains('small') ? '+' : '–'; });
    acc.addEventListener('click', () => act('accept'));
    chg.addEventListener('click', () => ask('change'));
    rej.addEventListener('click', () => ask('reject'));
    send.addEventListener('click', () => act(mode, ta.value.trim()));
    ta.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') { ev.preventDefault(); ask(null); }
      else if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); if (mode === 'change' && !ta.value.trim()) return; act(mode, ta.value.trim()); }
    });
    show(st);
    const t = setInterval(async () => {
      let s;
      try { s = await get(); } catch (e) { return; }  // the server stops after Accept or Reject
      if (!s || !s.preview) return;
      if (s.state === 'preview' && s.rev !== rev) { clearInterval(t); location.reload(); return; }
      show(s);
      if (s.state === 'shipped' || s.state === 'rejected') {
        clearInterval(t);
        msg.textContent = (s.message || '') + (s.back ? ' The live tab opens.' : ' You can close this tab.');
        if (s.back) setTimeout(() => { location.href = s.back; }, 1500);
      }
    }, 2000);
  }

  return {title, chatAttention, chatPanel, md, typeset, tab, iconURL, docTitle, crumbs, copyText, improveDash, previewBand};
})();
