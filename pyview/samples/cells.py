# %% [markdown]
# # Cells sample
# A sample for pyview: markdown with maths $e^{i\pi}+1=0$, text output, a table, an error, and comments.
# Standard library only, so it runs in every Python.

# %% Setup
import math
import random
import statistics

random.seed(0)
xs = [random.gauss(0, 1) for _ in range(500)]
print("n =", len(xs))

# %% A table as text
#> Is this table wide enough for the browser?
rows = [("mean", statistics.mean(xs)), ("stdev", statistics.stdev(xs)), ("max", max(xs))]
for name, v in rows:
    print(f"{name:>6} | {v:8.4f}")

# %% Long output
for i in range(40):
    print(i, round(math.sin(i / 5), 4))

# %% State is shared between cells
statistics.median(xs)

# %% [markdown]
# ## A section
# A list:
# - one item
# - a second item with `code`

# %% An error
#> Why does this cell fail?
#> @claude: It divides by zero on purpose, to show an error.
print("before the error")
1 / 0
