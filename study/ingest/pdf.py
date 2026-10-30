"""PDF extraction: text per page; rasterize pages that carry figures or equations."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from study.ingest.base import Chunk, Extracted, Image

MAX_CHARS = 2500  # split very dense pages into several chunks with the same page location
MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "msbm", "msam", "math", "stix", "euler", "rsfs", "mnsymbol")
DPI = 150

_DECORATION = set("•◦▪▸►–—-→⇒⟹⟶✓✗·")  # bullets/arrows often set in a math font

pymupdf.TOOLS.mupdf_display_errors(False)  # broken-but-readable PDFs spam warnings otherwise


def _page_info(page: "pymupdf.Page") -> tuple[str, bool]:
    """(title = largest-font line that isn't a bare page number, uses_math_font) from the page's text spans."""
    best, best_size, math_chars = "", 0.0, 0
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            txt = "".join(sp["text"] for sp in line["spans"]).strip()
            for sp in line["spans"]:
                if any(h in sp["font"].lower() for h in MATH_FONT_HINTS):
                    math_chars += sum(1 for ch in sp["text"] if not ch.isspace() and ch not in _DECORATION)
            size = max((sp["size"] for sp in line["spans"]), default=0)
            if txt and not txt.isdigit() and size > best_size + 0.5:
                best, best_size = txt, size
    return best[:120], math_chars >= 3


class _Baseline:
    """Template elements (logos, frame lines) repeat on every slide; only page-specific visuals count."""

    def __init__(self, doc: "pymupdf.Document"):
        n = doc.page_count
        counts: dict[int, int] = {}
        draws = []
        for page in doc:
            for xref in {im[0] for im in page.get_images(full=False)}:
                counts[xref] = counts.get(xref, 0) + 1
            draws.append(len(page.get_drawings()))
        self.template_xrefs = {x for x, c in counts.items() if n >= 4 and c > n / 2}
        self.draw_median = sorted(draws)[len(draws) // 2] if draws else 0

    def needs_raster(self, page: "pymupdf.Page", text: str, math: bool) -> bool:
        area = abs(page.rect)
        for im in page.get_images(full=False):
            if im[0] in self.template_xrefs:
                continue
            rects = page.get_image_rects(im[0])
            if any(abs(r) > 0.03 * area for r in rects):
                return True
        if len(page.get_drawings()) > self.draw_median + 6:  # plots, diagrams, tikz
            return True
        return math or len(text.strip()) < 200  # equations, or a mostly visual / scanned page


def _split(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) > MAX_CHARS:
            parts.append(cur)
            cur = ""
        cur += (("\n\n" if cur else "") + para)
    parts.append(cur)
    # a single huge paragraph: hard split
    return [p[i:i + MAX_CHARS] for p in parts for i in range(0, len(p), MAX_CHARS)]


def extract_pdf(path: Path, image_dir: Path, rasterize: bool = True) -> Extracted:
    doc = pymupdf.open(path)
    chunks, images = [], []
    image_dir.mkdir(parents=True, exist_ok=True)
    base = _Baseline(doc)
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text", sort=True).strip()
        heading, math = _page_info(page)
        for part in _split(text) if text else []:
            chunks.append(Chunk(i, i, part, heading))
        if rasterize and base.needs_raster(page, text, math):
            out = image_dir / f"p{i:03d}.png"
            if not out.exists():
                page.get_pixmap(dpi=DPI).save(out)
            images.append(Image(i, "page", out))
    meta = {"title": (doc.metadata or {}).get("title") or "", "pages": doc.page_count}
    return Extracted("p", doc.page_count, chunks, images, meta)
