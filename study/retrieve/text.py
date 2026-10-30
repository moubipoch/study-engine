"""Text helpers shared by search and bundle."""

import re

_PRIVATE_USE = re.compile("[-]")  # symbol-font bullets/arrows extracted from PDFs


def compact(text: str) -> str:
    """Drop trailing spaces and runs of blank lines (PDF extraction is very airy); keep indentation."""
    text = _PRIVATE_USE.sub("•", text)
    text = "\n".join(ln.rstrip() for ln in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()
