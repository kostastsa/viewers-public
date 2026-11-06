"""Data files and figures for the viewers (pageview, plateview): CSV, TSV, JSON and JSON Lines as columns and rows, and
the first page of a PDF figure as a PNG. Standard library only. pdftoppm (poppler) makes the PNG, else sips of macOS."""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess

DATA_EXT = {".csv", ".tsv", ".json", ".jsonl"}
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
               ".pdf": "application/pdf"}
DATA_MAX = 20_000_000   # the largest data file
ROWS_MAX = 50_000


class DataError(Exception):
    """A data file that cannot be read as a table. The message is for the user."""


def _text(p):
    raw = p.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _num(s):
    """A number from a cell, or the text as it is. Values that JSON cannot hold (NaN, infinity, lists) are changed."""
    if isinstance(s, float) and s != s or s in (float("inf"), float("-inf")):
        return None
    if isinstance(s, (list, dict)):
        return json.dumps(s, ensure_ascii=False)
    if not isinstance(s, str):
        return s
    t = s.strip()
    if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", t):
        v = float(t)
        return int(v) if re.fullmatch(r"[+-]?\d+", t) and abs(v) < 2 ** 53 else v
    if t.lower() in ("nan", "inf", "-inf", "+inf"):
        return None
    return s


def read_table(p):
    """{"columns": [...], "rows": [[...], ...]} of a data file. Numbers become numbers. Raises OpError."""
    if p.stat().st_size > DATA_MAX:
        raise DataError(f"{p.name} is larger than {DATA_MAX // 1_000_000} MB.")
    text = _text(p)
    ext = p.suffix.lower()
    if ext in (".csv", ".tsv"):
        dialect = "excel-tab" if ext == ".tsv" else "excel"
        if ext == ".csv":
            try:
                if csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter == ";":
                    dialect = csv.excel()
                    dialect.delimiter = ";"
            except csv.Error:
                pass
        rows = list(csv.reader(io.StringIO(text), dialect))
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            return {"columns": [], "rows": []}
        cols = [c.strip() or f"column {i + 1}" for i, c in enumerate(rows[0])]
        body = [[_num(c) for c in r[:len(cols)]] + [None] * (len(cols) - len(r)) for r in rows[1:ROWS_MAX + 1]]
        return {"columns": cols, "rows": body, "truncated": len(rows) - 1 > ROWS_MAX}
    try:
        if ext == ".jsonl":
            recs = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            recs = json.loads(text)
    except ValueError as e:
        raise DataError(f"{p.name} is not valid JSON: {e}")
    if isinstance(recs, dict) and all(isinstance(v, list) for v in recs.values()) and recs:  # columns of lists
        cols = list(recs)
        n = max(len(v) for v in recs.values())
        return {"columns": cols, "rows": [[_num(recs[c][i]) if i < len(recs[c]) else None for c in cols]
                                          for i in range(min(n, ROWS_MAX))]}
    if isinstance(recs, dict) and isinstance(recs.get("rows"), list) and isinstance(recs.get("columns"), list):
        return {"columns": [str(c) for c in recs["columns"]],
                "rows": [[_num(v) for v in r] for r in recs["rows"][:ROWS_MAX] if isinstance(r, list)]}
    if isinstance(recs, list) and recs and all(isinstance(r, dict) for r in recs):
        cols = []
        for r in recs:
            for k in r:
                if k not in cols:
                    cols.append(k)
        return {"columns": cols, "rows": [[_num(r.get(c)) for c in cols] for r in recs[:ROWS_MAX]],
                "truncated": len(recs) > ROWS_MAX}
    raise DataError(f"{p.name} is not a table: it must be a list of records, or columns of lists.")


def pdf_figure(p, cache):
    """A PNG of the first page of the PDF p, made once with pdftoppm (or sips) and kept in the folder `cache`.
    None if it fails."""
    st = p.stat()
    key = hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:20]
    out = cache / (key + ".png")
    if out.exists():
        return out
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = cache / (key + ".tmp")
    exe = shutil.which("pdftoppm")
    try:
        if exe:
            subprocess.run([exe, "-png", "-r", "160", "-f", "1", "-l", "1", "-singlefile", str(p), str(tmp)],
                           capture_output=True, timeout=30)
            made = tmp.with_suffix(".tmp.png")
        else:
            made = tmp.with_suffix(".png.tmp")
            subprocess.run(["sips", "-s", "format", "png", str(p), "--out", str(made)], capture_output=True, timeout=30)
        if made.exists():
            os.replace(made, out)
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None
