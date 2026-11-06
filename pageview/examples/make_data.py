"""Make the invented example data of pageview: optimizers/runs.csv, optimizers/curves.csv and vision_grid.csv.
The numbers are examples, not results. Run: python3 make_data.py"""
import csv
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = [("SGD", "0.1", 91.2, 0.3, 0.312, 0.010, 41.2, "✅"), ("SGD + momentum", "0.05", 93.4, 0.2, 0.248, 0.008, 41.9, "✅"),
        ("Nesterov", "0.05", 93.6, 0.2, 0.241, 0.007, 42.3, "✅"), ("Adam", "1e-3", 92.1, 0.4, 0.287, 0.012, 47.8, "⚠️ seed 3 diverged"),
        ("AdamW", "1e-3", 94.1, 0.2, 0.221, 0.006, 48.1, "✅"), ("Lion", "1e-4", 93.8, 0.3, 0.230, 0.009, 44.6, "🔁 rerun planned")]
with open(HERE / "optimizers" / "runs.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["optimizer", "lr", "acc", "acc_sd", "loss", "loss_sd", "time", "notes"])
    w.writerows(RUNS)
with open(HERE / "optimizers" / "curves.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["epoch", "optimizer", "acc", "acc_sd"])
    for i, (name, top, tau) in enumerate([("SGD", 91.2, 9.5), ("Lion", 93.8, 6.5), ("AdamW", 94.1, 5.2)]):
        for e in range(0, 51):
            base = top - (top - 10) * math.exp(-e / tau)
            v = 10.0 if e == 0 else min(top + 0.2, base + 0.6 * math.sin(e * 1.7 + i * 2) * math.exp(-e / 25))
            w.writerow([e, name, round(v, 2), round(0.3 + 2.5 * math.exp(-e / 8), 2)])
MODELS = [("ResNet-18", "CNN", 11.7, 0.10), ("ResNet-50", "CNN", 25.6, 0.42), ("WRN-28-10", "CNN", 36.5, 0.50),
          ("DenseNet-121", "CNN", 8.0, 0.36), ("EfficientNet-B0", "CNN", 5.3, 0.47), ("MobileNet-V3", "CNN", 5.4, 0.18),
          ("ConvNeXt-T", "CNN", 28.6, 0.78), ("ViT-S/16", "Transformer", 22.1, 0.55), ("DeiT-S", "Transformer", 22.1, 0.62),
          ("Swin-T", "Transformer", 28.3, 0.80)]
DATA = [("MNIST", "32 px", 99.45, 0.25), ("Fashion-MNIST", "32 px", 93.6, 2.0), ("CIFAR-10", "32 px", 94.6, 3.0),
        ("CIFAR-100", "32 px", 76.5, 6.5), ("SVHN", "32 px", 96.0, 1.4), ("STL-10", "96 px", 85.0, 9.0),
        ("Tiny-ImageNet", "64 px", 60.0, 14.0), ("Flowers-102", "224 px", 89.0, 9.5), ("Food-101", "224 px", 82.0, 8.0),
        ("ImageNet-1k", "224 px", 70.0, 12.0)]
seed = 42
def rnd():
    global seed
    seed = seed * 16807 % 2147483647
    return seed / 2147483647
with open(HERE / "vision_grid.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["model", "family", "params_m", "dataset", "size", "group", "acc_mean", "acc_sd", "seeds"])
    for m, fam, params, s in MODELS:
        for d, size, base, spread in DATA:
            conv = -1.4 * spread / 3 if fam == "Transformer" and size == "32 px" else 0
            v = min(99.8, base + spread * (s - 0.45) + conv + (rnd() - 0.5) * spread * 0.25)
            sd = 0.03 + rnd() * 0.06 * spread / 3 + (0 if d == "MNIST" else 0.08)
            if (m, d) == ("ViT-S/16", "MNIST"):
                continue  # not run
            group = "Small images (32 px)" if size == "32 px" else "Large images (64–224 px)"
            w.writerow([m, fam, params, d, size, group, round(v, 2), round(sd, 2), 3 if d == "ImageNet-1k" else 5])
print("made", HERE / "optimizers" / "runs.csv", HERE / "optimizers" / "curves.csv", HERE / "vision_grid.csv")
