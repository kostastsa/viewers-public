"""The watermark art of the viewers: figures down the left and right margins of a page (kit.watermark in web/kit.js).

The figures are not in Git. They are in the art folder: $VIEWKIT_ART, else viewkit/art, else mdview/art next to
viewkit (the first place of the art). The folder holds figures/manifest.json (a list of {id, kind, w, h, line | mask}),
the images (ID-line.webp or ID-mask.png), and figures/scores.json ({id: score}). A click on a figure adds 1 to its score,
and a double click adds -1. All the viewers share the scores. Without the folder, the viewers draw no watermark.

An app serves three routes with these functions:
    GET  /art/PATH            file(PATH)      an image or the manifest (an empty manifest without the folder)
    GET  /api/art/scores      scores()
    POST /api/art/vote        vote_api(op)    op = {"id": ID, "vote": 1 | -1}
"""
import json
import os
import re
import threading
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
TYPES = {".webp": "image/webp", ".png": "image/png", ".json": "application/json"}
ID_RE = re.compile(r"p\d{3}-\d+|o\d{3}-\d{2}")  # a cut-out figure, or one organism of a Kunstformen plate
_lock = threading.Lock()


def art_dir():
    """The folder of the art (see the top of this file)."""
    if os.environ.get("VIEWKIT_ART"):
        return Path(os.environ["VIEWKIT_ART"]).expanduser()
    for d in (KIT_DIR / "art", KIT_DIR.parent / "mdview" / "art"):
        if d.is_dir():
            return d
    return KIT_DIR / "art"


def _scores_file():
    return art_dir() / "figures" / "scores.json"


def scores():
    """{figure id: score}. An old entry {"up": U, "down": D} counts as U - D."""
    try:
        raw = json.loads(_scores_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v["up"] - v["down"] if isinstance(v, dict) else v for k, v in raw.items()}


def vote(fig, v):
    """Add 1 (v > 0) or -1 to the score of a figure. Returns the new score, or None if there is no such figure."""
    figs = art_dir() / "figures"
    if not ID_RE.fullmatch(fig) or not any((figs / f"{fig}-{k}").is_file() for k in ("mask.png", "line.webp")):
        return None
    with _lock:
        s = scores()
        s[fig] = s.get(fig, 0) + (1 if v > 0 else -1)
        f = _scores_file()
        tmp = f.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(s), encoding="utf-8")
        os.replace(tmp, f)  # atomic: a crash never leaves a half-written file
        return s[fig]


def vote_api(op):
    """The POST /api/art/vote route: (status, json)."""
    score = vote(str((op or {}).get("id", "")), 1 if (op or {}).get("vote") == 1 else -1)
    return (404, {"error": "No such figure"}) if score is None else (200, {"score": score})


def file(rel):
    """(bytes, media type) of the art file rel (for GET /art/REL), or None. Without the folder, the manifest is an
    empty list, so the page draws nothing. Only images and JSON files inside the art folder."""
    root = art_dir().resolve()
    if rel == "figures/manifest.json" and not (root / rel).is_file():
        return b"[]", TYPES[".json"]
    p = (root / rel).resolve()
    if root not in p.parents or not p.is_file() or p.suffix not in TYPES:
        return None
    return p.read_bytes(), TYPES[p.suffix]
