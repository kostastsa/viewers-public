"""Make the sample PDFs of pdfview (samples/*.pdf) and the comments of the paper (samples/.pdfview/paper.pdf.json).

The samples are the baseline for the preview of a proposal (improve.json "samples") and for tests/samples_test.py:
    paper.pdf      sections and an outline, equations, a theorem, a figure, a table, citations, links, a web link
    twocolumn.pdf  two columns of text, an equation in a column
    slides.pdf     beamer slides: landscape pages, colors, a block
    scan.pdf       a scanned page: one image, no text layer (for box comments)
The PDFs are in git, so you need this script only after a change of a source in samples/src/.
Needs pdflatex (with pgfplots and beamer), pdftoppm, and Pillow for scan.pdf. Run: python3 samples/make_samples.py
"""
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PDFVIEW = HERE.parent / "pdfview.py"


def latex(name):
    with tempfile.TemporaryDirectory() as d:
        for _ in range(2):  # two passes: the references and the outline
            r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-output-directory", d,
                                str(HERE / "src" / f"{name}.tex")], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"pdflatex failed on {name}.tex:\n{r.stdout[-2000:]}")
        shutil.copy(Path(d) / f"{name}.pdf", HERE / f"{name}.pdf")
    print("made", HERE / f"{name}.pdf")


def scan():
    """Page 2 of the paper as a gray image with a small turn and some noise, saved as a PDF with no text."""
    from PIL import Image, ImageFilter
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-f", "2", "-l", "2", "-r", "110", "-gray", "-png", str(HERE / "paper.pdf"),
                        str(Path(d) / "p")], check=True)
        img = Image.open(next(Path(d).glob("p*.png"))).convert("L")
    img = img.rotate(0.6, resample=Image.BICUBIC, fillcolor=255, expand=False).filter(ImageFilter.GaussianBlur(0.5))
    rnd = random.Random(1)
    px = img.load()
    for _ in range(img.width * img.height // 60):  # dust
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = max(0, px[x, y] - rnd.randrange(40, 120))
    img = img.point(lambda v: int(v * 0.93 + 8))  # gray paper
    img.save(HERE / "scan.pdf", "PDF", resolution=110)
    print("made", HERE / "scan.pdf")


def comments():
    """Three threads on the paper: a question on text, a general comment, and an answered thread."""
    side = HERE / ".pdfview" / "paper.pdf.json"
    side.unlink(missing_ok=True)
    pdf = str(HERE / "paper.pdf")

    def run(*a, text):
        r = subprocess.run([sys.executable, str(PDFVIEW), *a], input=text, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"pdfview {' '.join(a)} failed: {r.stderr}")
    run("add", pdf, "-", "--page", "1", "--quote", "tight up to a constant", "--author", "you",
        text="Which constant? Give the value.")
    run("add", pdf, "-", "--author", "you", text="A general comment: check the notation $S_n$ in all sections.")
    run("add", pdf, "-", "--page", "2", "--quote", "Markov's inequality", "--author", "you", text="Why Markov here?")
    run("reply", pdf, "3", "-", text="Markov's inequality bounds the tail by the moment generating function, "
                                     "$\\Pr(Y \\ge t) \\le e^{-\\lambda t}\\,\\mathbb{E} e^{\\lambda Y}$.")
    print("made", side, len(json.loads(side.read_text())["threads"]), "threads")


if __name__ == "__main__":
    for name in ("paper", "twocolumn", "slides"):
        latex(name)
    scan()
    comments()
