#!/usr/bin/env python3
"""Generate wizardtower.json - a voxel rendition of the Hirst Arts wizard's
tower (hirstarts.com/wiztower/wiztower.html, mold #50): an octagonal stone
shaft with arrow slits, flaring out over corbel rows to a wider top chamber
with arched windows, crowned with capped battlements.

Run from the repo root:  python3 samples/make_wizardtower.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from ldr2vs import build_schematic  # noqa: E402

import json  # noqa: E402

WALL = "game:cobblestone-granite"        # gray stone shaft
TRIM = "game:stonebricks-granite"        # corbels, roof slab, window sills
FLOOR = "game:planks-walnut-ud"          # chamber floor
CAP = "game:stonebricks-basalt"          # dark battlement caps
GLASS = "game:glass-violet"              # wizardly windows


def octagon(r, d):
    """Cells of an octagon: square radius r with corners cut at |x|+|z| > d."""
    return {(x, z) for x in range(-r, r + 1) for z in range(-r, r + 1)
            if abs(x) + abs(z) <= d}


def ring(cells):
    """Boundary cells of a filled shape (any 8-neighbour outside)."""
    out = set()
    for (x, z) in cells:
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (x + dx, z + dz) not in cells:
                    out.add((x, z))
                    break
            else:
                continue
            break
    return out


shaft = octagon(4, 6)        # 9x9 octagon
chamber = octagon(6, 9)      # 13x13 octagon
corbel = octagon(5, 7)       # intermediate step

vox = {}

def put(x, y, z, code):
    vox[(x, y, z)] = code

# --- shaft: y0-11, hollow octagonal ring ---
for y in range(0, 12):
    for (x, z) in ring(shaft):
        put(x, y, z, WALL)

# --- door: south face (z=+4), 3 wide, arched ---
for y in (0, 1, 2):
    for x in (-1, 0, 1):
        vox.pop((x, y, 4), None)
vox.pop((0, 3, 4), None)

# --- arrow slits: 1x2 verticals on N/E/W at two heights ---
for y in (4, 5):
    vox.pop((0, y, -4), None)   # north
    vox.pop((4, y, 0), None)    # east
    vox.pop((-4, y, 0), None)   # west
for y in (8, 9):
    vox.pop((0, y, -4), None)
    vox.pop((4, y, 0), None)
    vox.pop((-4, y, 0), None)

# --- corbel flare: two stepped slabs carrying the wider top chamber ---
for (x, z) in corbel:
    put(x, 12, z, TRIM)
for (x, z) in chamber:
    put(x, 13, z, TRIM if (x, z) in ring(chamber) else FLOOR)

# --- top chamber: y14-17, walls with arched windows on all four faces ---
for y in range(14, 18):
    for (x, z) in ring(chamber):
        put(x, y, z, WALL)
for y in (14, 15, 16):
    spread = (-1, 0, 1) if y < 16 else (0,)   # arch narrows at the top
    for i in spread:
        put(i, y, -6, GLASS)   # north
        put(i, y, 6, GLASS)    # south
        put(6, y, i, GLASS)    # east
        put(-6, y, i, GLASS)   # west

# --- roof slab over the chamber ---
for (x, z) in chamber:
    put(x, 18, z, TRIM)

# --- battlements: alternating merlons with dark caps ---
for (x, z) in ring(chamber):
    if (x + z) % 2 == 0:
        put(x, 19, z, CAP)

blocks = dict(vox)
schem = build_schematic(blocks, "1.22.3")
out = os.path.join(os.path.dirname(__file__), "wizardtower.json")
with open(out, "w") as f:
    json.dump(schem, f, separators=(",", ":"))

from collections import Counter  # noqa: E402
counts = Counter(blocks.values())
print(f"{out}: {schem['SizeX']}x{schem['SizeY']}x{schem['SizeZ']} ({len(blocks)} blocks)")
for code, n in counts.most_common():
    print(f"  {n:>5}  {code}")
