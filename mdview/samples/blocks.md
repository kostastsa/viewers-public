# Blocks and tables

A sample for mdview: Markdown tables in a card, a comment in a table cell, and the blocks of pageview.

## Results

| Optimizer       | Learning rate | Accuracy (%) | Test loss | Notes                  |
|:----------------|--------------:|-------------:|----------:|:-----------------------|
| AdamW           |         0.001 |         94.1 |     0.221 | the best               |
| Lion            |        0.0001 |         93.8 |     0.230 | rerun planned          |
| SGD + momentum  |          0.05 |         93.4 |     {==0.248==}{>>@you: Is this loss correct?<<} | the fastest, see below |
| Adam            |         0.001 |         92.1 |     0.287 | seed 3 diverged        |

::: {.table best="Accuracy:max Loss:min"}
| Model    | Accuracy | Loss  |
|----------|----------|-------|
| ResNet-18 | 94.1    | 0.221 |
| ViT-S    | 92.7     | 0.262 |
| ConvNeXt | 95.0     | 0.198 |
:::

::: {.callout .result title="Result"}
AdamW gives the best accuracy, and Lion is close. SGD is the fastest per epoch.
:::

::: stats
- 94.1 % | Best accuracy | AdamW
- 41.2 s | Fastest epoch | SGD
- 5 | Seeds per run
:::

::: columns
::: {.col title="Setup"}
ResNet-18 on CIFAR-10, 50 epochs, five seeds.
:::
::: {.col title="Hardware"}
One A100, mixed precision.
:::
:::

::: tabs
::: {.tab title="Data table"}
::: {.table src="runs.csv" sd="acc:acc_sd" best="acc:max loss:min"}
:::
:::
::: {.tab title="Chart"}
::: {.chart src="runs.csv" type="line" x="epoch" y="acc" color="optimizer" err="acc_sd"}
:::
:::
:::

::: {.toggle title="Settings"}
Batch size 128, cosine schedule, weight decay 0.05.
:::

```python
for opt in ["adamw", "sgd", "lion"]:
    train(opt, epochs=50)
```
