// The editor pyview uses for cells. Built once into vendor/codemirror.js (see vendor/codemirror-src/).
import {EditorView, keymap, drawSelection, highlightActiveLine, dropCursor} from '@codemirror/view';
import {EditorState, Prec} from '@codemirror/state';
import {defaultKeymap, history, historyKeymap, indentWithTab} from '@codemirror/commands';
import {python} from '@codemirror/lang-python';
import {markdown} from '@codemirror/lang-markdown';
import {syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching, indentUnit} from '@codemirror/language';
import {closeBrackets, closeBracketsKeymap} from '@codemirror/autocomplete';
import {tags as t} from '@lezer/highlight';

const theme = EditorView.theme({
  '&': {backgroundColor: 'var(--code)', color: 'var(--fg)', border: '1px solid var(--accent)', borderRadius: '3px', fontSize: '13px'},
  '&.cm-focused': {outline: 'none'},
  '.cm-scroller': {fontFamily: 'var(--ui)', lineHeight: '1.5'},
  '.cm-content': {caretColor: 'var(--fg)', padding: '8px 0'},
  '.cm-line': {padding: '0 10px'},
  '.cm-cursor, .cm-dropCursor': {borderLeftColor: 'var(--fg)'},
  '.cm-activeLine': {backgroundColor: 'transparent'},
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': {backgroundColor: 'var(--hl2) !important'},
  '.cm-matchingBracket': {backgroundColor: 'var(--hl)', outline: '1px solid var(--hl2)'},
});

const highlight = HighlightStyle.define([
  {tag: [t.keyword, t.controlKeyword, t.operatorKeyword, t.definitionKeyword, t.moduleKeyword], color: 'var(--kw)'},
  {tag: [t.string, t.special(t.string)], color: 'var(--str)'},
  {tag: [t.number, t.bool, t.null], color: 'var(--num)'},
  {tag: t.comment, color: 'var(--com)', fontStyle: 'italic'},
  {tag: [t.definition(t.variableName), t.function(t.variableName), t.className, t.function(t.propertyName)], color: 'var(--def)'},
  {tag: [t.standard(t.variableName), t.self], color: 'var(--bi)'},
  {tag: [t.heading, t.strong], fontWeight: 'bold', color: 'var(--fg)'},
  {tag: t.emphasis, fontStyle: 'italic'},
  {tag: [t.link, t.url], color: 'var(--accent)'},
  {tag: t.monospace, color: 'var(--str)'},
]);

// opts: doc, language ('python' | 'markdown'), run(stay), cancel, split, onInput
window.PyviewCM = {
  create(parent, opts) {
    const cmd = fn => () => { fn(); return true; };
    const view = new EditorView({
      parent,
      state: EditorState.create({
        doc: opts.doc,
        extensions: [
          history(), drawSelection(), dropCursor(), indentOnInput(), bracketMatching(), closeBrackets(),
          indentUnit.of('    '), EditorState.tabSize.of(4),
          opts.language === 'markdown' ? markdown() : python(),
          syntaxHighlighting(highlight), theme,
          Prec.highest(keymap.of([
            {key: 'Shift-Enter', run: cmd(() => opts.run(false))},
            {key: 'Mod-Enter', run: cmd(() => opts.run(true))},
            {key: 'Escape', run: cmd(opts.cancel)},
            {key: 'Ctrl-Shift--', run: cmd(opts.split)},
            {key: 'Ctrl-_', run: cmd(opts.split)},
          ])),
          keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, indentWithTab]),
          EditorView.updateListener.of(u => { if (u.docChanged && opts.onInput) opts.onInput(); }),
        ],
      }),
    });
    return {
      view,
      getValue: () => view.state.doc.toString(),
      cursorLine: () => view.state.doc.lineAt(view.state.selection.main.head).number - 1,
      focusEnd: () => { view.focus(); view.dispatch({selection: {anchor: view.state.doc.length}, scrollIntoView: true}); },
      hasFocus: () => view.hasFocus,
      focus: () => view.focus(),
    };
  },
};
