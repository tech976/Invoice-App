#!/usr/bin/env python
"""Precompute the glyph-outline table used to decode fontless PDFs.

Some bills embed subset fonts with every trace of unicode stripped: no
`ToUnicode`, no `/Encoding`, a `cmap` pointing into the private use area and
`post` format 3.0. The characters are unrecoverable from the PDF's structure —
but the *outlines* survive intact, and a subset of Arial still draws its 'A'
exactly the way Arial does.

So each glyph is reduced to a hash of its outline, and that hash is looked up
in this table to recover the character. The table is built once, here, from
fonts installed on a workstation, and shipped as data — the server needs no
fonts of its own, and no font file is redistributed, only one-way hashes.

    python scripts/build_glyph_signatures.py
    python scripts/build_glyph_signatures.py --fonts ~/fonts --out table.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.filterwarnings("ignore")
logging.getLogger("fontTools").setLevel(logging.ERROR)

from app.extraction.local.glyphs import TABLE_PATH, outline_signature  # noqa: E402

# Where desktop fonts live, by platform. Only the families that actually turn
# up in accounting-software PDFs are worth the table space.
FONT_DIRS = [
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path("/usr/share/fonts"),
    Path("C:/Windows/Fonts"),
]
FAMILIES = (
    "arial", "times new roman", "courier new", "verdana", "tahoma", "georgia",
    "trebuchet", "calibri", "helvetica", "cambria", "segoe",
)


# What a trade bill can actually print. Arial Unicode alone carries tens of
# thousands of CJK outlines that would bloat the table forty-fold for
# characters no invoice will ever use.
WANTED_RANGES = (
    (0x0020, 0x024F),   # ASCII, Latin-1, Latin Extended-A/B
    (0x2010, 0x203A),   # dashes, quotes, dagger, per-mille
    (0x20A0, 0x20BF),   # currency signs, including the rupee
    (0x2122, 0x2122),   # trademark
    (0x2190, 0x21FF),   # arrows
    (0x2200, 0x22FF),   # maths operators
    (0x25A0, 0x25FF),   # geometric shapes used as bullets
)


def _wanted(code: int) -> bool:
    if 0xE000 <= code <= 0xF8FF:      # private use area
        return False
    return any(lo <= code <= hi for lo, hi in WANTED_RANGES)


def font_files(dirs: list[Path]) -> list[Path]:
    out: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".ttc", ".otf"):
                continue
            if any(f in path.stem.lower() for f in FAMILIES):
                out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonts", type=Path, action="append",
                    help="extra directory to scan (repeatable)")
    ap.add_argument("--out", type=Path, default=TABLE_PATH)
    args = ap.parse_args()

    from fontTools.ttLib import TTCollection, TTFont

    dirs = FONT_DIRS + (args.fonts or [])
    table: dict[str, str] = {}
    fonts = 0

    for path in font_files(dirs):
        try:
            if path.suffix.lower() == ".ttc":
                members = list(TTCollection(str(path)).fonts)
            else:
                members = [TTFont(str(path), fontNumber=0, lazy=True)]
        except Exception:  # noqa: BLE001 - a font we cannot parse is skipped
            continue

        for font in members:
            try:
                cmap = font.getBestCmap()
                glyphs = font.getGlyphSet()
            except Exception:  # noqa: BLE001
                continue
            if not cmap:
                continue
            fonts += 1
            for code, name in cmap.items():
                if not _wanted(code):
                    continue
                signature = outline_signature(glyphs, name)
                if signature and signature not in table:
                    table[signature] = chr(code)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(table, ensure_ascii=False, sort_keys=True))
    size_kb = args.out.stat().st_size / 1024
    print(f"{len(table):,} outlines from {fonts} font(s) -> {args.out} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
