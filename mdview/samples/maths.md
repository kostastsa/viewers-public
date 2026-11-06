# Maths sample

A sample for mdview: inline maths, displayed maths, aligned equations, and maths in a comment.

Let $X_1,\dots,X_n$ be independent with $a_i \le X_i \le b_i$, and let $S_n=\sum_{i=1}^n X_i$. Money stays text: it costs $5 and $10.

$$
\Pr\big(|S_n - \mathbb{E}S_n| \ge t\big) \le 2\exp\!\left(-\frac{2t^2}{\sum_i (b_i-a_i)^2}\right)
$$

$$
\begin{aligned}
M(\lambda) &= \mathbb{E}\, e^{\lambda (S_n - \mathbb{E} S_n)} \\
           &\le \exp\!\Big(\tfrac{\lambda^2}{8} \textstyle\sum_i (b_i-a_i)^2\Big)
\end{aligned}
$$

The {==bound is tight==}{>>Which constant? Is $c = 2$ the best value?<<}{>>@claude: For fair coins the exponent $2t^2/n$ is the best possible, up to a factor in front.<<} for all $n$.

A matrix:

$$
A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}, \qquad \det A = -2 .
$$
