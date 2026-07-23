#!/usr/bin/env python3
"""plan2vs - compile a declarative house plan (.plan.json) into a VS schematic.

For turning vintage plan-book houses (Sears kit homes, 1920s pattern books...)
into Vintage Story BlockSchematics without modelling block-by-block. You author
a plan file; this compiles it. Scale convention: 1 block = 2 ft.

Plan file shape:
{
  "name": "sierra-a611",
  "legend": {
    "#": {"type": "wall",     "block": "game:claybricks-good-fire"},
    "+": {"type": "wall",     "block": "game:rockpolished-chalk"},
    "w": {"type": "window",   "block": "game:claybricks-good-fire",
                              "glass": "game:glass-plain"},
    "d": {"type": "door",     "block": "game:claybricks-good-fire"},
    "e": {"type": "door",     "block": "game:rockpolished-chalk"},
    "g": {"type": "door",     "block": "game:claybricks-good-fire"},
    "C": {"type": "chimney",  "block": "game:claybricks-good-fire"},
    "P": {"type": "post",     "block": "game:log-placed-oak-ud",
                              "floor": "game:stonebricks-andesite"},
    ".": {"type": "floor",    "block": "game:planks-oak-ud"},
    ",": {"type": "floor",    "block": "game:cobblestone-andesite"},
    "p": {"type": "floor",    "block": "game:stonebricks-andesite"}
  },
  "storeys": [
    {"floor_y": 0, "wall_h": 3, "grid": ["row0 (z=0, north/back)", ...]},
    {"floor_y": 4, "wall_h": 3, "grid": [...]}
  ],
  "roofs": [
    {"rect": [x0, z0, x1, z1], "edge": "W|E|N|S|FLAT", "eave_y": 7,
     "run": 1, "block": "game:clayshingleblock-gray", "max_y": 14}
  ]
}

Semantics (all y values are block layers, storey floor slab sits AT floor_y):
- every non-space grid cell lays a slab at floor_y ("floor" override, else the
  cell's own block - so walls get a matching base course).
- wall/window/chimney cells rise floor_y+1 .. floor_y+wall_h.
  windows are glass on rows floor_y+window_lo .. floor_y+window_hi
  (defaults 2..3: one-block sill, two-block pane).
- door cells leave rows floor_y+1 .. floor_y+door_h open (default 2), wall
  above; put several side by side for wide cart/garage openings.
- post cells rise like walls (porch columns).
- digit cells 1-9 are stair runs: step n gets a stair block at floor_y+n with
  riser fill below, ascending toward the cell holding n+1. Configure wood with
  top-level {"stairs": {"wood": "oak"}}. Floor cells in the storey above are
  auto-cut wherever a slab would leave less than 2 blocks of air above a
  step's walking level, so climbing never bumps the ceiling; use void cells
  only where a wider stairwell opening is wanted.
- void cells ("type": "void") place nothing at all - no floor slab - and
  cancel roof fills from below: stairwell openings, double-height spaces.
- roofs: height at a cell = eave_y + distance_from_low_edge // run, clamped to
  max_y; overlapping planes keep the highest. "edge": "HIP" makes the whole
  rect a hip roof (height = distance to the NEAREST rect edge, run 1 only) -
  with "slanted" it emits oriented slopes, outer corners, ridge caps,
  ridgeends and pyramid tips automatically. Planes with "slanted":
  "<material>" (grayclay, redclay, slate, thatch...) emit VS slanted roofing
  oriented down the slope: smooth 45-degree pieces at run 1; at run>=2 a
  stepped profile with one slant at each step-down and flat shingle blocks
  between (VS has no true shallow-pitch piece - the top/bottom half-wedges
  are 45-degree transition fillers, not a 22.5-degree system). The flat
  treads default to clayshingleblock in the matching color; override with
  "block". FLAT planes always use "block".
- after roofing, wall-ish cells of the TOPMOST storey covering each column are
  extended up to the roof underside (gable ends, catslide fills). Cells that
  are plain floor in a higher storey cancel fills from below.
- chimney cells punch through the roof and rise chimney_above (default 3)
  blocks over it.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldr2vs import build_schematic


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


WALLISH = {"wall", "window", "door", "chimney", "post"}

# VS slanted roofing names its variant by the HIGH side (ascent direction,
# same convention as stairs): a -west- slant descends toward east. Verified
# against 41 shipped worldgen schematics (~1500 votes), after two attempts at
# deriving it from shape rotateY tables each came out 90 degrees off.
# Keyed by the plane's LOW edge, the direction the roof descends toward.
SLANT_LABEL = {"E": "west", "S": "north", "W": "east", "N": "south"}

# Hip-roof pieces, all conventions vote-verified from shipped schematics:
# ridge variant names its axis; a corner takes one of its two faces' labels;
# a ridgeend is named for the direction the ridge CONTINUES, phase-shifted
# 90 degrees exactly like the main pieces (continues-north -> "east").
RIDGE_AXIS = {frozenset("NS"): "we", frozenset("EW"): "ns"}
CORNER_LABEL = {frozenset("ES"): "west", frozenset("SW"): "north",
                frozenset("NE"): "south", frozenset("NW"): "east"}
RIDGEEND_LABEL = {"N": "east", "W": "north", "S": "west", "E": "south"}


def hip_piece(faces, mat):
    """Roofing piece for a HIP cell given the set of low edges it serves."""
    if len(faces) == 1:
        return f"slantedroofing-{mat}-{SLANT_LABEL[next(iter(faces))]}-free"
    if faces in RIDGE_AXIS:
        return f"slantedroofingridge-{mat}-{RIDGE_AXIS[faces]}-free"
    if len(faces) == 2:
        return f"slantedroofingcornerouter-{mat}-{CORNER_LABEL[faces]}-free"
    if len(faces) == 3:
        cont = next(iter({"N", "E", "S", "W"} - faces))
        return f"slantedroofingridgeend-{mat}-{RIDGEEND_LABEL[cont]}-free"
    return f"slantedroofingtip-{mat}-free"


def compile_plan(plan):
    legend = plan["legend"]
    voxels = {}
    # (x,z) -> (wall_top_y, fill_block) or None; last (highest) storey wins
    fills = {}
    chimney_cells = {}
    stair_wood = plan.get("stairs", {}).get("wood", "oak")
    # (x,z) -> block layer of the stair step there; floors in later storeys
    # are auto-cut while they'd leave <2 blocks of air over the step's top
    stair_clear = {}

    for storey in plan["storeys"]:
        fy = storey["floor_y"]
        wh = storey["wall_h"]
        door_h = storey.get("door_h", 2)
        win_lo = storey.get("window_lo", 2)
        win_hi = storey.get("window_hi", min(3, wh))
        stair_cells = {}
        for z, row in enumerate(storey["grid"]):
            for x, ch in enumerate(row):
                if ch == " ":
                    continue
                if ch.isdigit():
                    stair_cells[(x, z)] = int(ch)
                    fills[(x, z)] = None
                    continue
                if ch not in legend:
                    raise SystemExit(f"error: grid char '{ch}' at x={x} z={z} "
                                     f"(floor_y={fy}) missing from legend")
                spec = legend[ch]
                typ = spec["type"]
                if typ == "void":
                    fills[(x, z)] = None
                    continue
                if typ == "floor" and fy <= stair_clear.get((x, z), -99) + 2:
                    # stairwell auto-cut: a slab here would block headroom on
                    # the stair step below
                    fills[(x, z)] = None
                    continue
                blk = spec["block"]
                voxels[(x, fy, z)] = spec.get("floor", blk)
                top = fy + wh
                if typ in ("wall", "chimney", "post"):
                    for y in range(fy + 1, top + 1):
                        voxels[(x, y, z)] = blk
                elif typ == "window":
                    for y in range(fy + 1, top + 1):
                        pane = fy + win_lo <= y <= fy + win_hi
                        voxels[(x, y, z)] = spec["glass"] if pane else blk
                elif typ == "door":
                    for y in range(fy + 1 + door_h, top + 1):
                        voxels[(x, y, z)] = blk
                elif typ != "floor":
                    raise SystemExit(f"error: unknown legend type '{typ}'")
                if typ in WALLISH:
                    fills[(x, z)] = (top, blk)
                else:
                    fills[(x, z)] = None
                if typ == "chimney":
                    chimney_cells[(x, z)] = (top, blk)

        # stair runs: each step ascends toward the neighbor holding step+1
        for (x, z), n in stair_cells.items():
            nxt = [(dx, dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if stair_cells.get((x + dx, z + dz)) == n + 1]
            prv = [(dx, dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if stair_cells.get((x + dx, z + dz)) == n - 1]
            if nxt:
                dx, dz = nxt[0]
            elif prv:
                dx, dz = -prv[0][0], -prv[0][1]
            else:
                raise SystemExit(f"error: stair step {n} at x={x} z={z} has no "
                                 f"neighboring step {n + 1} or {n - 1}")
            label = {(1, 0): "east", (-1, 0): "west",
                     (0, 1): "south", (0, -1): "north"}[(dx, dz)]
            voxels[(x, fy, z)] = f"game:planks-{stair_wood}-ud"
            for y in range(fy + 1, fy + n):
                voxels[(x, y, z)] = f"game:planks-{stair_wood}-ud"
            voxels[(x, fy + n, z)] = f"game:plankstairs-{stair_wood}-up-{label}-free"
            stair_clear[(x, z)] = fy + n

    # roof heightmap
    heights = {}
    for plane in plan.get("roofs", []):
        x0, z0, x1, z1 = plane["rect"]
        edge = plane["edge"]
        run = plane.get("run", 1)
        slant = plane.get("slanted") if edge != "FLAT" else None
        if edge == "HIP" and run != 1:
            raise SystemExit("error: HIP roofs support run 1 only")
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                dd = {"W": x - x0, "E": x1 - x, "N": z - z0, "S": z1 - z}
                if edge == "HIP":
                    d = min(dd.values())
                    faces = frozenset(k for k, v in dd.items() if v == d)
                else:
                    d = dd[edge] if edge != "FLAT" else 0
                h = plane["eave_y"] + (d // run if edge != "FLAT" else 0)
                if "max_y" in plane:
                    h = min(h, plane["max_y"])
                if slant and edge == "HIP":
                    blk = "game:" + hip_piece(faces, slant)
                elif slant and (run == 1 or d % run == 0):
                    blk = f"game:slantedroofing-{slant}-{SLANT_LABEL[edge]}-free"
                elif slant:
                    # flat tread between step-downs: matching full shingle
                    # block (VS has no true shallow-pitch roofing piece)
                    blk = plane.get("block",
                                    f"game:clayshingleblock-{slant.removesuffix('clay')}")
                else:
                    blk = plane["block"]
                if (x, z) not in heights or h > heights[(x, z)][0]:
                    heights[(x, z)] = (h, blk)
    for (x, z), (h, blk) in heights.items():
        voxels[(x, h, z)] = blk

    # extend topmost walls to the roof underside (gables, catslide skirts)
    for (x, z), fill in fills.items():
        if fill is None or (x, z) not in heights:
            continue
        top, blk = fill
        for y in range(top + 1, heights[(x, z)][0]):
            voxels[(x, y, z)] = blk

    # chimneys punch through floors above and the roof, rising past it
    above = plan.get("chimney_above", 3)
    for (x, z), (top, blk) in chimney_cells.items():
        roof_y = heights.get((x, z), (top,))[0]
        for y in range(top + 1, roof_y + 1 + above):
            voxels[(x, y, z)] = blk

    return voxels


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("input", help=".plan.json house plan file")
    ap.add_argument("-o", "--output", help="output .json path (default: input name)")
    ap.add_argument("--validate", metavar="CODES_JSON",
                    help="JSON array of valid VS block codes; warn about unknown codes")
    ap.add_argument("--game-version", default="1.22.3")
    args = ap.parse_args()

    with open(args.input) as f:
        plan = json.load(f)
    voxels = compile_plan(plan)

    if args.validate:
        with open(args.validate) as f:
            valid = set(json.load(f))
        for code in sorted(set(voxels.values())):
            if code.split(":", 1)[-1] not in valid:
                warn(f"block code '{code}' not found in {args.validate}; "
                     f"it will load as air in-game")

    schem = build_schematic(voxels, args.game_version)
    out = args.output or os.path.splitext(args.input)[0].replace(".plan", "") + ".json"
    with open(out, "w") as f:
        json.dump(schem, f, separators=(",", ":"))

    from collections import Counter
    counts = Counter(voxels.values())
    print(f"{out}: {schem['SizeX']}x{schem['SizeY']}x{schem['SizeZ']} "
          f"({len(voxels)} blocks, {len(counts)} block types)")
    for code, n in counts.most_common():
        print(f"{n:>9}  {code}")


if __name__ == "__main__":
    main()
