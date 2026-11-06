---
title: pageview syntax
view: page
---

# pageview syntax

A pageview page is a Markdown file (pandoc, CommonMark with extensions). All of normal Markdown works: headings, lists, links, images, tables, code, maths and footnotes. The extra blocks below are pandoc **fenced divs**: a line `::: {.name key="value"}`, the content, and a line `:::`. For a block inside a block, give the outer block more colons (`::::`).

Data files and images are paths relative to the page. They must be in the folder of the page, in a folder below it, or in the same Git project.

## Front matter

The YAML block at the top of the file. All keys are optional.

```yaml
---
title: Optimizers on CIFAR-10
subtitle: Six optimizers, five seeds
date: 2026-11-05
author: K. T.
tags: [🧪 6 × 5 seeds, ⏱ 50 epochs]
view: page         # this file is a pageview page (Finder and mdview use it)
toc: false         # no contents list
width: wide        # a wider text column
---
```

## Callout

```markdown
::: {.callout .result title="Result"}
AdamW gives the best accuracy.
:::
```

Kinds: `.note` (the default), `.tip`, `.result`, `.warn`, `.danger`. `title` is optional.

## Stat cards

```markdown
::: stats
- 94.1 % | Best accuracy | AdamW
- 41.2 s | Fastest epoch | SGD
- 5 | Seeds per run
:::
```

Each item is `VALUE | LABEL | NOTE`. The note is optional.

## Tables

**Every Markdown table** gets a fixed header, sort by column, Wide, full window and Copy (LaTeX, Markdown, CSV). A cell such as `94.1 ± 0.2` sorts by `94.1`.

A `.table` block adds options. Without `src`, it holds a Markdown table:

```markdown
::: {.table best="Accuracy:max Loss:min" sort="Accuracy:desc"}
| Optimizer | Accuracy | Loss |
|---|--:|--:|
| AdamW | 94.1 ± 0.2 | 0.221 |
| SGD | 91.2 ± 0.3 | 0.312 |

**Table 1.** Test results.
:::
```

With `src`, the rows come from a CSV, TSV, JSON or JSON Lines file. The text in the block is the caption.

```markdown
::: {.table src="runs.csv" cols="optimizer,lr,acc,loss,time" sd="acc:acc_sd loss:loss_sd"
     labels="acc: Accuracy (%) | time: Epoch time (s)" best="acc:max loss:min time:min"
     sort="acc:desc" dec="acc:1 loss:3"}
**Table 1.** Test results after 50 epochs.
:::
```

| Option | Meaning |
|---|---|
| `src` | The data file. JSON: a list of records, or an object of column lists. |
| `cols` | The columns to show, in this order. Default: all. |
| `labels` | The column names to show: `column: Label \| column: Label`. |
| `sd` | Pairs `mean:sd`. The cell shows `mean ± sd`, and the sd column is not shown alone. |
| `best` | Pairs `column:max` or `column:min`. The best value gets ★. |
| `sort` | `column:asc` or `column:desc`, the first sort. |
| `dec` | Pairs `column:N`, the number of decimals. |
| `key` | The column that names a row, for cell comments. Default: the first column. |
| `height` | The highest table in pixels before it scrolls (default 520). |
| `.wide` | A class: the table uses the full width of the page. |

### Grid tables (pivot)

With `rows` and `columns`, a file in **long form** (one row per run) becomes a grid. The viewer can then swap rows and columns.

```markdown
::: {.table src="vision_grid.csv" rows="model" columns="dataset" value="acc_mean" sd="acc_sd"
     row-groups="family" col-groups="group" row-info="params_m: Params (M)"
     best="col:max" color="heat" summary="mean rank" footer="mean best" .wide}
**Table 3.** Top-1 accuracy (%), mean ± sd over the seeds.
:::
```

| Option | Meaning |
|---|---|
| `rows`, `columns`, `value` | The columns of the file for the rows, the columns and the cells of the grid. |
| `sd` | The column of the standard deviation of `value`. |
| `row-groups`, `col-groups` | Columns that group the rows (a label under each name) and the columns (a header row above them). |
| `row-info` | More columns about each row, `column: Label \| …`, for example the number of parameters. |
| `best` | `col:max`, `col:min`, `row:max`, `row:min` or `off`. |
| `color` | `heat`, `rank` or `off`. |
| `summary` | `mean`, `rank` (the mean rank of the row): more columns at the right. |
| `footer` | `mean`, `best`: more rows at the bottom. |

## Data files opened alone

`pageview runs.csv` shows the file as one table that fills the window. An optional file `runs.view.json` next to it gives the same options as a `.table` block, for example `{"rows": "model", "columns": "dataset", "value": "acc_mean", "sd": "acc_sd", "title": "Accuracy (%)"}`.

## Charts

```markdown
::: {.chart src="curves.csv" type="line" x="epoch" y="acc" color="optimizer"
     x-label="Epoch" y-label="Test accuracy (%)" y-min=60}
**Figure 1.** Test accuracy per epoch.
:::
```

| Option | Meaning |
|---|---|
| `type` | `line`, `scatter`, `bar` or `area`. |
| `x`, `y` | Columns. `y` can name more than one column, separated by commas: one line for each. |
| `color` | A column that splits the rows into series (one line, or one bar colour, for each value). |
| `err` | A column with the error of `y`: a band for lines, a whisker for bars. |
| `x-label`, `y-label` | The axis titles. |
| `x-min`, `x-max`, `y-min`, `y-max` | The axis limits. |
| `log-x`, `log-y` | `true` for a log axis. |
| `height` | The height in pixels (default 320). |

Move the pointer on a chart to read the values. Click a name in the legend to hide its series.

## Figures

```markdown
::: {.figures cols=3}
![**(a)** Confusion matrix](fig/confusion.svg)
![**(b)** Gradient norms](fig/gradnorm.pdf)
![**(c)** Learning rate](fig/lr.png)
:::
```

PNG, JPEG, GIF, WebP, SVG and PDF (the first page). Put each image on its own line, with an empty line between them for separate captions. A click on any image opens it large.

## Tabs

```markdown
:::: tabs
::: {.tab title="AdamW"}
- lr 1e-3
:::
::: {.tab title="SGD"}
- lr 0.1
:::
::::
```

## Toggle

```markdown
::: {.toggle title="Hardware and run details"}
Hidden until you click the title. Add `open=true` to show it at the start.
:::
```

## Columns

```markdown
:::: columns
::: {.col title="👍 What worked"}
- Warm-up
:::
::: {.col title="👎 What did not"}
- Batch 512
:::
::::
```

## Code

A fenced code block gets a **Copy** button. There is no colour highlighting.

## Maths

`$…$` inline and `$$…$$` displayed, typeset by MathJax.

## Widgets

For content that the blocks above cannot show, a `widget` code block holds HTML, CSS and a script:

````markdown
```{.widget title="Learning rate schedule" height=180}
<input type="range" min="0" max="15" value="5" id="w">
<svg id="s" width="600" height="120"></svg>
<script> /* draw the schedule */ </script>
```
````

The widget runs in a sandboxed frame. It has no network, no access to your files, and no access to the page. The colours of the page are CSS variables in it: `--bg`, `--fg`, `--muted`, `--line`, `--accent`. The frame takes the height of its content (`height` is the first height).

## Wide blocks

Add the class `.wide` to a block (`::: {.table .wide …}` or `::: {.chart .wide …}`), and it uses the full width of the page.

## Links

A link to another page or data file (`[runs](runs.csv)`) opens it in pageview. A link to an image opens the image.

## Not yet

Mermaid diagrams show as code. EPS figures do not show.
