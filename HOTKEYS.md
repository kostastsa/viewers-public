# Hotkeys

This file lists the keys of the viewers. The first part gives the keys that many viewers share. The next parts give the keys of each viewer.

- **⌘/Ctrl** means ⌘ on a Mac and Ctrl on other systems.
- **Option** is the Alt key on other systems.
- **Single keys** (for example `d` or `o`) work only when the cursor is not in a text box or in an editor.

## Shared keys

| Key | Action | Viewers |
|---|---|---|
| ⌘/Ctrl+S | Open the chat and put the cursor in its text box. In the text box, hide the chat. | All. In the source editor of `texview`, this key saves. |
| ⌘/Ctrl+Shift+M | Write a comment on the selected text. | All |
| ⌘/Ctrl+\\ | Show or hide the comments sidebar. | All |
| ⌘/Ctrl+Shift+. | Open or close the dashboard of the notes to the app. | All, but not `pageview` |

### In a comment box

| Key | Action |
|---|---|
| ⌘/Ctrl+Enter | Save the comment. |
| Esc | Close the box. |

### In the chat

| Key | Action |
|---|---|
| Enter | Send the message. |
| Shift+Enter | Start a new line. |
| Esc | Remove the cursor from the text box. |
| ⌘/Ctrl+= | Make the text of the chat larger. The focus must be in the chat. |
| ⌘/Ctrl+- | Make the text of the chat smaller. The focus must be in the chat. |
| ⌘/Ctrl+0 | Set the default size of the text. The focus must be in the chat. |

### In the dashboard

| Key | Action |
|---|---|
| Enter | Add the note, send the answer to a question, or save the edited note. |
| Shift+Enter | Start a new line. |
| Esc | Close the dashboard. In an edited note, keep the old text. |

### In the preview of a proposal

| Key | Action |
|---|---|
| Enter | Send the text in the box. |
| Shift+Enter | Start a new line. |
| Esc | Close the box. |

## PDF pages (`pdfview` and `texview`)

| Key | Action |
|---|---|
| `+` or `=` | Zoom in. |
| `-` | Zoom out. |
| `0` | Fit the width of the pages. |
| `d` | Make the pages dark or light. |
| `o` | Show or hide the contents of the PDF. |
| ⌘/Ctrl+[ | Return to the place before a jump. In the source editor of `texview`, this key changes the indent. |
| ⌘/Ctrl+] | Go forward again. In the source editor of `texview`, this key changes the indent. |
| Enter (in the page number) | Go to the page. |
| Esc (in the page number) | Show the current page number again. |

A jump is a click on a link, a section of the contents, or a page number. A pinch or Ctrl+wheel also zooms.

## `mdview`

| Key or click | Action |
|---|---|
| ⌘/Ctrl+Shift+\\ | Show or hide the contents. |
| Double-click on a block | Edit the Markdown of the block. |
| Alt+click on a fold button | Fold or unfold the section and its subsections. |
| Esc | Close the ⋯ menu. |

## `pyview`

### When no editor is open

| Key | Action |
|---|---|
| ↑ or `k` | Select the previous cell. |
| ↓ or `j` | Select the next cell. |
| Enter | Edit the selected cell. |
| Shift+Enter | Run the cell and select the next cell. |
| ⌘/Ctrl+Enter | Run the cell and keep the selection. |
| `a` | Add a code cell above. |
| `b` | Add a code cell below. |
| `m` | Change the cell to Markdown. |
| `y` | Change the cell to code. |
| Esc | Close the comment box. |
| ⌘/Ctrl+Shift+\\ | Show or hide the contents. |
| Double-click on a cell | Edit the cell. |

### In the cell editor

| Key | Action |
|---|---|
| Shift+Enter | Save, run the cell and go to the next cell. |
| ⌘/Ctrl+Enter | Save, run the cell and stay. |
| Esc | Save and close the editor. The cell does not run. |
| Ctrl+Shift+- | Split the cell at the cursor. |
| Tab | Indent. |
| Shift+Tab | Remove the indent. |

In `pyview`, ⌘/Ctrl+Shift+M writes a comment on the selected line. If no line is selected, the comment goes on the selected cell.

In the title field of a cell, Enter saves the title and Esc cancels the change.

## `pdfview`

| Key or mouse | Action |
|---|---|
| ⌘/Ctrl+Shift+H | Highlight the selected text. |
| Option+drag on a page | Write a comment on an area. |
| Esc | Stop the drag. |

## `pageview`

| Key | Action |
|---|---|
| `o` | Show or hide the contents. |

Tables (also in `plateview`):

| Key | Action |
|---|---|
| Enter or Space on a column name | Sort the table. The focus must be on the column name. |
| Esc | Close the table or the figure in the full window. |

## `plateview`

| Key | Action |
|---|---|
| ⌘/Ctrl+P | Save a PDF of the plate. |

These keys also work in the plate: ⌘/Ctrl+S, ⌘/Ctrl+P, ⌘/Ctrl+Shift+M, ⌘/Ctrl+\\ and ⌘/Ctrl+Shift+.

## `texview`

### The document

| Key or mouse | Action |
|---|---|
| ⌘/Ctrl+S (in the source editor) | Save all the changed files. The server then compiles. |
| ⌘/Ctrl+Shift+B | Compile now. |
| Control+S (Mac only) | Show or hide the source. |
| Option+Tab | Show or hide the source. |
| `e` | Show or hide the source. |
| ⌘/Ctrl+D | Show or hide the comments sidebar. On a Mac, use ⌘ in a text box. |
| ⌘/Ctrl+E | Show or hide the contents of the PDF. On a Mac, use ⌘ in a text box. |
| Double-click on a page | Show or hide the source. |
| ⌘/Ctrl+click on a page | Show the line in the source. |
| ⌘/Ctrl+click on a source line | Show the place in the PDF. |
| Double-click on the bar between the panes | Set the default size of the panes. |

### Zoom of each pane

| Key | Action |
|---|---|
| Option with `+`, `-` or `0` | Zoom the pane that has the focus: the source or the pages. |
| ⌘/Ctrl with `+`, `-` or `0` | Change the size of the source text. The focus must be in the source. |

### In the source editor

| Key | Action |
|---|---|
| ⌘/Ctrl+/ | Change the lines to LaTeX comments, or change them back. |
| ⌘/Ctrl+F | Open the find bar. |
| ⌘+Option+F (Mac), Ctrl+Shift+F (other systems) | Open the find bar, with the cursor in the Replace field. |
| ⌘/Ctrl+G | Go to the next match. |
| ⌘/Ctrl+Shift+G | Go to the previous match. |
| ⌘/Ctrl+Z | Undo. |

### In the find bar

| Key | Action |
|---|---|
| Enter (Find field) | Go to the next match. |
| Shift+Enter | Go to the previous match. |
| Enter (Replace field) | Replace this match and go to the next match. |
| ⌘/Ctrl+Enter (Replace field) | Replace all the matches. |
| Esc | Close the find bar. |

### In the popup of suggestions

| Key | Action |
|---|---|
| Enter or Tab | Accept the selected suggestion. |
| Esc | Close the popup. |
