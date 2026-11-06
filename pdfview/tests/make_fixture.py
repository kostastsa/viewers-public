"""Make tests/fixture.pdf: two pages of text, a phrase that occurs two times on page 1, a word that breaks with a
hyphen at a line end on page 2, a color image, an outline, and two links on page 1: one to Section 2 (inside the
PDF), and one to a web page.
Standard library only. Run: python3 tests/make_fixture.py"""
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixture.pdf"

PAGE1 = [
    (72, 720, 20, "A short paper about bounds"),
    (72, 680, 12, "We show that the bound is tight for all n. The proof is short."),
    (72, 662, 12, "A second claim: the bound is tight for small n only in the old work."),
    (72, 644, 12, "The figure below is a color image. In dark mode it keeps its colors."),
    (72, 400, 12, "The constant c depends on the dimension d of the space."),
    (72, 370, 12, "See Section 2 for the results."),
    (72, 340, 12, "Visit the web page of the project."),
]
PAGE2 = [
    (72, 720, 16, "Section 2. Results"),
    (72, 690, 12, "The main theorem holds for every finite group of odd order."),
    (72, 672, 12, "We end with an open question about the case of even order."),
    (72, 640, 12, "The proof of the lemma needs a long and careful argu-"),
    (72, 622, 12, "ment that we do not repeat in this short note."),
]


def text_ops(lines):
    out = ["BT"]
    for x, y, size, s in lines:
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        out.append(f"/F1 {size} Tf 1 0 0 1 {x} {y} Tm ({s}) Tj")
    out.append("ET")
    return "\n".join(out)


def image_data(w=64, h=48):
    rows = []
    for j in range(h):
        row = bytearray()
        for i in range(w):
            row += bytes([255 * i // (w - 1), 60, 255 * j // (h - 1)])  # red to the right, blue down
        rows.append(bytes(row))
    return zlib.compress(b"".join(rows)), w, h


def build():
    img, iw, ih = image_data()
    c1 = text_ops(PAGE1) + "\nq 200 0 0 150 72 460 cm /Im1 Do Q\n"
    c2 = text_ops(PAGE2) + "\n"
    objs = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 9 0 R /PageMode /UseOutlines >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R "
           "/Resources << /Font << /F1 7 0 R >> /XObject << /Im1 8 0 R >> >> /Annots [12 0 R 13 0 R] >>",
        4: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
        5: ("stream", "", c1.encode()),
        6: ("stream", "", c2.encode()),
        7: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        8: ("stream", f"/Type /XObject /Subtype /Image /Width {iw} /Height {ih} /ColorSpace /DeviceRGB "
                      f"/BitsPerComponent 8 /Filter /FlateDecode", img),
        9: "<< /Type /Outlines /First 10 0 R /Last 11 0 R /Count 2 >>",
        10: "<< /Title (Introduction) /Parent 9 0 R /Next 11 0 R /Dest [3 0 R /XYZ 0 792 null] >>",
        11: "<< /Title (Section 2. Results) /Parent 9 0 R /Prev 10 0 R /Dest [4 0 R /XYZ 0 740 null] >>",
        # the words "See Section 2" link to page 2; "web page" links to a web address
        12: "<< /Type /Annot /Subtype /Link /Rect [72 366 147 382] /Border [0 0 0] /Dest [4 0 R /XYZ 0 740 null] >>",
        13: "<< /Type /Annot /Subtype /Link /Rect [130 336 200 352] /Border [0 0 0] "
            "/A << /S /URI /URI (https://example.com/) >> >>",
    }
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for n in sorted(objs):
        offsets[n] = len(out)
        o = objs[n]
        if isinstance(o, tuple):
            _, extra, data = o
            out += f"{n} 0 obj\n<< {extra} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream\nendobj\n"
        else:
            out += f"{n} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for n in sorted(objs):
        out += f"{offsets[n]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    OUT.write_bytes(bytes(out))
    print(OUT)


if __name__ == "__main__":
    build()
