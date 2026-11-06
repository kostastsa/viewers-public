# %% [markdown]
# # pyview demo
# A markdown cell with maths: $e^{i\pi}+1=0$.

# %% Setup
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

rng = np.random.default_rng(0)
x = rng.normal(size=200)
print("x has", x.size, "values")

# %% A table
df = pd.DataFrame({"a": x[:5], "b": x[5:10]})
df.head()

# %% A plot
plt.figure(figsize=(5, 3))
plt.hist(x, bins=20)
plt.title("histogram")
plt.show()

# %% State is shared between cells
print("mean:", round(float(x.mean()), 3))
x.std()

# %% An error
print("before the error")
1 / 0

# %% After the error
print("not reached by Run all")
