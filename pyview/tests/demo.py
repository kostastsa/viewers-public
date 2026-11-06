#> what machine is claude running on?
#> @claude: Claude runs on your Mac (host my-mac.local). It is an Apple Silicon (arm64) Mac with macOS, Darwin 25.6.0.

# %% [markdown]
# # pyview demo
# A markdown cell with maths: $e^{i\pi}+1=0$.

# %% Setup
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

rng = np.random.default_rng(0)
x = rng.standard_t(df=3, size=1000)   # heavy-tailed sample: Student t with 3 degrees of freedom
print("x has", x.size, "values")

# %% [markdown]
# ## Bootstrap and jackknife
# We have one sample $x_1,\dots,x_n$ and we want to know how much a statistic $\hat\theta$
# (for example the mean or the median) would change if we collected a new sample. Both methods
# estimate this standard error from the one sample that we have. They reuse the data.
#
# - The **bootstrap** draws $n$ values from the sample *with replacement*, many times. It
#   computes $\hat\theta$ on each new sample. The standard deviation of these values is the
#   bootstrap standard error.
# - The **jackknife** removes one value at a time. It computes $\hat\theta_{(i)}$ on the $n-1$
#   remaining values, for $i = 1,\dots,n$. The standard error is
#   $\sqrt{\frac{n-1}{n}\sum_i (\hat\theta_{(i)}-\bar\theta_{(\cdot)})^2}$.
#
# The next cell does the bootstrap. The cell after it does the jackknife.

# %% Bootstrap
import numpy as np
boot_rng = np.random.default_rng(2)
n_boot = 2000

boot_mean = np.empty(n_boot)
boot_median = np.empty(n_boot)
for b in range(n_boot):
    resample = boot_rng.choice(x, size=x.size, replace=True)
    boot_mean[b] = resample.mean()
    boot_median[b] = np.median(resample)

print("bootstrap SE of the mean:  ", round(float(boot_mean.std(ddof=1)), 4))
print("bootstrap SE of the median:", round(float(boot_median.std(ddof=1)), 4))
print("95% interval of the median:", np.percentile(boot_median, [2.5, 97.5]).round(3))

plt.figure(figsize=(5, 3))
plt.hist(boot_median, bins=40)
plt.axvline(np.median(x), color="k")
plt.title("bootstrap distribution of the median")
plt.show()

# Naive method: assume the data are normal and use the textbook formula.
# For normal data the median has SE about 1.2533 * s / sqrt(n).
naive_se = 1.2533 * x.std(ddof=1) / np.sqrt(x.size)
naive_grid = np.linspace(boot_median.min(), boot_median.max(), 300)
naive_pdf = np.exp(-0.5 * ((naive_grid - np.median(x)) / naive_se) ** 2) / (naive_se * np.sqrt(2 * np.pi))

print("naive normal-theory SE of the median:", round(float(naive_se), 4))

plt.figure(figsize=(5, 3))
plt.hist(boot_median, bins=40, density=True, alpha=0.5, label="bootstrap")
plt.plot(naive_grid, naive_pdf, color="C3", label="naive normal theory")
plt.axvline(np.median(x), color="k")
plt.title("naive vs bootstrap, median")
plt.legend()

plt.show()

# %% Jackknife
n = x.size
jack_mean = np.array([np.delete(x, i).mean() for i in range(n)])
jack_median = np.array([np.median(np.delete(x, i)) for i in range(n)])

def jackknife_se(values):
    return np.sqrt((n - 1) / n * ((values - values.mean()) ** 2).sum())

print("jackknife SE of the mean:  ", round(float(jackknife_se(jack_mean)), 4))
print("jackknife SE of the median:", round(float(jackknife_se(jack_median)), 4))
print("formula SE of the mean:    ", round(float(x.std(ddof=1) / np.sqrt(n)), 4))

# The jackknife gives n replicates, not a sampling distribution. We use the jackknife SE
# to build a normal curve around the full-sample median and compare it with the bootstrap.
jack_se = jackknife_se(jack_median)
jack_grid = np.linspace(boot_median.min(), boot_median.max(), 300)
jack_pdf = np.exp(-0.5 * ((jack_grid - np.median(x)) / jack_se) ** 2) / (jack_se * np.sqrt(2 * np.pi))

plt.figure(figsize=(5, 3))
plt.hist(boot_median, bins=40, density=True, alpha=0.4, label="bootstrap")
plt.plot(jack_grid, jack_pdf, color="C1", label="jackknife (normal, jackknife SE)")
plt.plot(jack_median, np.zeros(n), "|", color="C1", markersize=12, label="jackknife replicates")
plt.axvline(np.median(x), color="k")
plt.title("jackknife, median")
plt.legend(fontsize=7)
plt.show()

# %% [markdown]
# ### How to read the result
# For the mean, the bootstrap and the jackknife agree with the formula $s/\sqrt{n}$. This is
# the expected result, because the mean is a smooth statistic.
#
# The sample is heavy-tailed (Student $t$ with 3 degrees of freedom). For the median, the
# methods disagree: the bootstrap gives about 0.043, the naive normal-theory formula gives about
# 0.062, and the jackknife gives about 0.003. The naive formula uses $s$, and the large values in
# the tails make $s$ too big, so it overstates the SE. The median changes only when we remove a
# value near the middle, so the jackknife values are not smooth and the jackknife is known to
# work badly for the median. Use the bootstrap for such statistics.
#
# The bootstrap needs many random resamples ($n_{\text{boot}} = 2000$ here). The jackknife
# needs exactly $n$ runs and has no random part.

# %% [markdown]
# ## Bayesian A/B test
# Each variant has an unknown conversion rate $\theta$. We want to know which variant has the
# higher rate. Each visitor either converts or does not convert, so $k$ conversions in $n$
# visitors have the likelihood $k \mid \theta \sim \mathrm{Binomial}(n, \theta)$.
#
# The prior is $\theta \sim \mathrm{Beta}(1,1)$. It is flat: every rate from 0 to 1 is equally
# likely before we see data. The Beta prior is conjugate to the Binomial likelihood, so the
# posterior is also a Beta distribution: $\mathrm{Beta}(1+k,\,1+n-k)$.
#
# The next cell sets the data for variants A and B and plots the two posteriors.

# %% Bayesian A/B test: data and posteriors
from scipy import stats

n_a, k_a = 1000, 100   # variant A: visitors, conversions
n_b, k_b = 1000, 125   # variant B

post_a = stats.beta(1 + k_a, 1 + n_a - k_a)
post_b = stats.beta(1 + k_b, 1 + n_b - k_b)

grid = np.linspace(0.05, 0.20, 500)
plt.figure(figsize=(5, 3))
plt.plot(grid, post_a.pdf(grid), label="A")
plt.plot(grid, post_b.pdf(grid), label="B")
plt.xlabel("conversion rate")
plt.legend()
plt.show()

# %% Bayesian A/B test: decision
ab_rng = np.random.default_rng(1)
s_a = post_a.rvs(100_000, random_state=ab_rng)
s_b = post_b.rvs(100_000, random_state=ab_rng)

print("P(B > A):", round(float((s_b > s_a).mean()), 3))
print("expected uplift:", round(float((s_b / s_a - 1).mean()), 3))
print("95% interval of B - A:", np.percentile(s_b - s_a, [2.5, 97.5]).round(3))
print("expected loss if we choose B:", round(float(np.maximum(s_a - s_b, 0).mean()), 4))

# %% [markdown]
# ### How to read the result
# We draw samples from both posteriors and compare them pair by pair.
#
# - $P(B > A)$ is the share of pairs in which B has the higher rate. It is about 0.96.
# - The expected uplift is the mean relative gain of B over A. It is about 26%.
# - The 95% interval of $\theta_B - \theta_A$ just includes 0, so a small loss is still possible.
# - The expected loss is the mean rate that we lose if we choose B and A is better. It is about
#   0.0002, which is very small.
#
# The evidence favors B, but it is not certain. Choose B if a loss of 0.0002 is acceptable.

# %% A table
df = pd.DataFrame({"a": x[:5], "b": x[5:10]})
df.head()

# %% A plot
plt.figure(figsize=(5, 3))
plt.hist(x, bins=100)
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
