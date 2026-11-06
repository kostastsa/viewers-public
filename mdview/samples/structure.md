# Structure sample

A sample for mdview: headings, lists, a table, code, a quote, links and footnotes.

## Lists

1. The first step.
2. The second step, with a sub-list:
   - an item
   - an item with `code`
3. The last step.

- [x] A task that is done
- [ ] A task that waits

## A table

| Method | Time (s) | Error |
|:-------|---------:|------:|
| A      |     1.20 | 0.031 |
| B      |     0.85 | 0.047 |
| C      |    12.00 | 0.009 |

## Code

```python
def mean(xs):
    """The mean of a list of numbers."""
    return sum(xs) / len(xs)
```

## A quote and links

> A short quote, on two
> lines.

See [the Lists section](#lists), the web page [example.com](https://example.com), and a footnote.[^1]

### A third-level heading

#### A fourth-level heading

Text under a deep heading.

[^1]: The text of the footnote.
