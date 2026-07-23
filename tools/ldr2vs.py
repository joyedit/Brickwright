#!/usr/bin/env python3
"""ldr2vs - convert digital Lego builds to Vintage Story schematics.

Input formats:
  .ldr / .mpd / .dat  LDraw models (Eurobricks, BrickSafe, the OMR, etc.)
  .io                 BrickLink Studio files (zip archives; the embedded
                      model.ldr is read directly unless password-protected)
  .vox                MagicaVoxel files (any voxelizer output)

Output: a Vintage Story BlockSchematic .json, ready for the Brickwright
mod's /bw paste command (drop it in VintagestoryData/ModConfig/brickwright/),
or for WorldEdit's /we imp.

Geometry notes:
  - LDraw grid: 1 stud = 20 LDU horizontally, 1 plate = 8 LDU vertically,
    1 brick = 24 LDU (3 plates). -Y is up.
  - Default mapping: 1 stud -> 1 voxel, 1 brick (3 plates) -> 1 voxel.
    That bakes in the 5:6 aspect correction: a 1x1 brick becomes a cube.
    Override with --plates-per-voxel (e.g. 1 for plate-accurate, tall builds).
  - Parts are voxelized as their bounding boxes (slopes become full blocks).
    Footprints for common bricks/plates/tiles/slopes are built in; unknown
    parts fall back to a 1x1 brick at their origin and are reported.

Usage:
  ldr2vs.py castle.ldr                        -> castle.json
  ldr2vs.py castle.ldr -o mycastle.json --colormap mymap.json
  ldr2vs.py build.vox --plates-per-voxel 3
"""

import argparse
import json
import os
import struct
import sys
from collections import Counter

STUD_LDU = 20.0     # horizontal LDU per stud
PLATE_LDU = 8.0     # vertical LDU per plate

# ---------------------------------------------------------------------------
# Built-in part footprints: part number -> (studs_along_x, studs_along_z,
# height_in_plates). LDraw part origins sit at the top-center of the body,
# body extends downward (+Y) by height, and +/-X is the long axis.
# Slopes/arches/wedges are approximated by their full bounding box.
# Extend or override with --partdims extra.json ({"3001": [4, 2, 3], ...}).
# ---------------------------------------------------------------------------
PART_DIMS = {
    # bricks 1xN
    "3005": (1, 1, 3), "3004": (2, 1, 3), "3622": (3, 1, 3), "3010": (4, 1, 3),
    "3009": (6, 1, 3), "3008": (8, 1, 3), "6111": (10, 1, 3), "6112": (12, 1, 3),
    "2465": (16, 1, 3),
    # bricks 2xN
    "3003": (2, 2, 3), "3002": (3, 2, 3), "3001": (4, 2, 3), "2456": (6, 2, 3),
    "3007": (8, 2, 3), "3006": (10, 2, 3),
    # tall bricks / walls / panels
    "2454": (2, 1, 15), "3754": (6, 1, 15), "3755": (4, 1, 15),
    "4864": (2, 1, 6), "4865": (2, 1, 3), "87552": (2, 1, 6), "60581": (4, 1, 9),
    "2453": (1, 1, 15), "2455": (2, 1, 6),
    # plates 1xN
    "3024": (1, 1, 1), "3023": (2, 1, 1), "3623": (3, 1, 1), "3710": (4, 1, 1),
    "3666": (6, 1, 1), "3460": (8, 1, 1), "4477": (10, 1, 1), "60479": (12, 1, 1),
    # plates 2xN
    "3022": (2, 2, 1), "3021": (3, 2, 1), "3020": (4, 2, 1), "3795": (6, 2, 1),
    "3034": (8, 2, 1), "3832": (10, 2, 1), "2445": (12, 2, 1), "91988": (14, 2, 1),
    "4282": (16, 2, 1),
    # plates 4xN / 6xN / 8xN
    "3031": (4, 4, 1), "3032": (6, 4, 1), "3035": (8, 4, 1), "3030": (10, 4, 1),
    "3029": (12, 4, 1), "3958": (6, 6, 1), "3036": (8, 6, 1), "3033": (10, 6, 1),
    "3456": (14, 6, 1), "41539": (8, 8, 1), "92438": (16, 8, 1), "3865": (16, 8, 1),
    "91405": (16, 16, 1), "44336": (16, 16, 1), "4186": (16, 16, 1),
    # tiles
    "3070": (1, 1, 1), "3070b": (1, 1, 1), "3069": (2, 1, 1), "3069b": (2, 1, 1),
    "63864": (3, 1, 1), "2431": (4, 1, 1), "6636": (6, 1, 1), "4162": (8, 1, 1),
    "3068": (2, 2, 1), "3068b": (2, 2, 1), "87079": (4, 2, 1), "6178": (6, 6, 1),
    # slopes (approximated as full boxes)
    "3040": (2, 1, 3), "3040b": (2, 1, 3), "3039": (2, 2, 3), "3038": (3, 2, 3),
    "3037": (4, 2, 3), "4286": (3, 1, 3), "3298": (3, 2, 3), "3299": (4, 2, 3),
    "3660": (2, 2, 3), "3665": (2, 1, 3), "3676": (2, 2, 3), "60481": (2, 1, 6),
    "3678": (4, 2, 3), "3675": (3, 3, 3), "2449": (2, 1, 6), "4460": (2, 1, 9),
    "3684": (4, 3, 9), "3685": (4, 2, 9), "30499": (4, 4, 9),
    # round bricks / plates / cylinders
    "3062": (1, 1, 3), "3062b": (1, 1, 3), "4073": (1, 1, 1), "3941": (2, 2, 3),
    "4589": (1, 1, 3), "2453b": (1, 1, 15), "6558": (1, 1, 9), "43888": (1, 1, 15),
    "6168": (2, 2, 15),
    # arches (full box approximation)
    "3455": (6, 1, 3), "3308": (8, 1, 3), "6183": (4, 2, 6), "3307": (6, 1, 6),
    "3659": (4, 1, 3), "6182": (4, 2, 6), "92950": (5, 1, 3), "6060": (8, 1, 6),
    # doors / windows / frames (full box; put glass color on the part itself)
    "3853": (4, 1, 9), "3854": (4, 1, 9), "60594": (4, 1, 9), "60592": (2, 1, 6),
    "60593": (2, 1, 9), "3861": (4, 1, 15), "30179": (4, 1, 18), "60596": (4, 1, 18),
    "60623": (4, 1, 18), "3644": (4, 1, 15), "2493": (4, 1, 15),
    # fences / bars
    "3185": (4, 1, 3), "30055": (8, 1, 3), "2486": (4, 1, 3), "3633": (4, 1, 3),
    # small modified plates / jumpers / clips
    "6141": (1, 1, 1), "85861": (1, 1, 1), "3794": (2, 1, 1), "3794a": (2, 1, 1),
    "3794b": (2, 1, 1), "15573": (2, 1, 1), "87580": (2, 2, 1), "60897": (1, 1, 1),
    "2540": (2, 1, 1), "60478": (2, 1, 1), "32028": (2, 1, 1), "44728": (2, 2, 1),
    # small slopes (cheese etc.)
    "54200": (1, 1, 2), "15571": (2, 1, 2), "11477": (2, 1, 2), "59900": (1, 1, 2),
    "85863": (1, 1, 1),
    # modified bricks
    "87087": (1, 1, 3), "98283": (2, 1, 3), "2357": (2, 2, 3), "3065": (2, 1, 3),
    "52107": (2, 1, 3), "4733": (1, 1, 3), "6091": (2, 1, 4), "48092": (4, 4, 3),
    "2453a": (1, 1, 15), "59349": (6, 1, 15),
    # tile variants
    "3069a": (2, 1, 1), "3070a": (1, 1, 1), "3068a": (2, 2, 1),
    # classic castle era parts
    "3665a": (2, 1, 3), "4085a": (1, 1, 1), "4444": (2, 2, 3), "4445": (2, 4, 3),
    "3939": (2, 2, 9), "6066": (2, 4, 6), "30246": (1, 1, 3), "2921": (1, 1, 3),
}

# Minifigure and creature parts: never part of a building's structure. Models
# often stage minifigs next to the build; skip them entirely.
MINIFIG_PART_PREFIXES = (
    "3626",                      # heads
    "973", "3814",               # torsos / torso assemblies
    "3815", "3816", "3817",      # hips and legs
    "3818", "3819", "3820",      # arms and hands
    "970", "971", "972",         # legs, old style
    "3624", "3833", "3842", "3878", "2446", "6093", "2528", "2544",  # headgear
    "3846", "3847", "3848", "3849", "4497", "4498", "3959", "6124",  # shields/weapons
    "4493", "4494", "2490", "6021",                                   # horses & tack
)

# LDraw primitives are unit-scale shapes stretched by their transform matrix
# (e.g. 4-4cyli scaled into a flagpole or log). Voxelize them as their
# transformed unit box instead of assuming brick dimensions.
import re as _re
PRIMITIVE_RE = _re.compile(
    r"^(\d-\d(cyli|cylo|cyls|cylc|disc|ndis|edge|chrd|con\d*|ring\d*|rin)\w*"
    r"|box\w*|rect\w*|cube|stud\w*|peghole|axle\w*|plane|tri3\w*)$")

# ---------------------------------------------------------------------------
# Default LDraw colour -> Vintage Story block mapping. All block codes are
# verified to exist in VS 1.22. "rgb" is the LDraw reference colour, used for
# nearest-colour matching when converting .vox files. Override any entry with
# --colormap yourmap.json (same shape; only the keys you supply are replaced).
# ---------------------------------------------------------------------------
DEFAULT_COLORMAP = {
    "0":   {"name": "Black",             "block": "game:rockpolished-basalt",             "rgb": [27, 42, 52]},
    "1":   {"name": "Blue",              "block": "game:glass-blue",                      "rgb": [30, 90, 168]},
    "2":   {"name": "Green",             "block": "game:rockpolished-greenmarble",        "rgb": [0, 133, 43]},
    "3":   {"name": "Dark Turquoise",    "block": "game:glass-blue",                      "rgb": [6, 157, 159]},
    "4":   {"name": "Red",               "block": "game:rockpolished-redmarble",          "rgb": [180, 0, 0]},
    "5":   {"name": "Dark Pink",         "block": "game:glass-pink",                      "rgb": [211, 53, 157]},
    "6":   {"name": "Brown",             "block": "game:planks-walnut-ud",                "rgb": [84, 51, 36]},
    "7":   {"name": "Light Gray",        "block": "game:cobblestone-granite",             "rgb": [138, 146, 141]},
    "8":   {"name": "Dark Gray",         "block": "game:cobblestone-basalt",              "rgb": [84, 89, 85]},
    "9":   {"name": "Light Blue",        "block": "game:glass-blue",                      "rgb": [128, 187, 219]},
    "10":  {"name": "Bright Green",      "block": "game:rockpolished-greenmarble",        "rgb": [75, 159, 74]},
    "11":  {"name": "Turquoise",         "block": "game:glass-blue",                      "rgb": [85, 165, 175]},
    "12":  {"name": "Salmon",            "block": "game:claybricks-good-fire",            "rgb": [242, 112, 94]},
    "13":  {"name": "Pink",              "block": "game:glass-pink",                      "rgb": [252, 151, 172]},
    "14":  {"name": "Yellow",            "block": "game:hay-normal-ud",                   "rgb": [250, 200, 10]},
    "15":  {"name": "White",             "block": "game:rockpolished-whitemarble",        "rgb": [244, 244, 244]},
    "17":  {"name": "Light Green",       "block": "game:rockpolished-greenmarble",        "rgb": [173, 217, 168]},
    "18":  {"name": "Light Yellow",      "block": "game:sand-granite",                    "rgb": [255, 214, 127]},
    "19":  {"name": "Tan",               "block": "game:stonebricks-limestone",           "rgb": [215, 197, 153]},
    "20":  {"name": "Light Violet",      "block": "game:glass-violet",                    "rgb": [201, 202, 226]},
    "22":  {"name": "Purple",            "block": "game:glass-violet",                    "rgb": [129, 0, 123]},
    "25":  {"name": "Orange",            "block": "game:claybricks-good-fire",            "rgb": [254, 138, 24]},
    "26":  {"name": "Magenta",           "block": "game:glass-pink",                      "rgb": [146, 57, 120]},
    "27":  {"name": "Lime",              "block": "game:rockpolished-greenmarble",        "rgb": [165, 202, 24]},
    "28":  {"name": "Dark Tan",          "block": "game:cobblestone-claystone",           "rgb": [149, 138, 115]},
    "29":  {"name": "Bright Pink",       "block": "game:glass-pink",                      "rgb": [228, 173, 200]},
    "33":  {"name": "Trans Dark Blue",   "block": "game:glass-blue",                      "rgb": [0, 32, 160]},
    "34":  {"name": "Trans Green",       "block": "game:glass-green",                     "rgb": [35, 120, 65]},
    "35":  {"name": "Trans Bright Green","block": "game:glass-green",                     "rgb": [86, 230, 70]},
    "36":  {"name": "Trans Red",         "block": "game:glass-red",                       "rgb": [201, 26, 9]},
    "40":  {"name": "Trans Black",       "block": "game:glass-smoky",                     "rgb": [99, 95, 82]},
    "41":  {"name": "Trans Light Blue",  "block": "game:glass-blue",                      "rgb": [174, 239, 236]},
    "42":  {"name": "Trans Neon Green",  "block": "game:glass-green",                     "rgb": [248, 241, 132]},
    "43":  {"name": "Trans Blue",        "block": "game:glass-blue",                      "rgb": [193, 223, 240]},
    "46":  {"name": "Trans Yellow",      "block": "game:glass-yellow",                    "rgb": [245, 205, 47]},
    "47":  {"name": "Trans Clear",       "block": "game:glass-plain",                     "rgb": [252, 252, 252]},
    "70":  {"name": "Reddish Brown",     "block": "game:claybricks-good-fire",            "rgb": [88, 42, 18]},
    "71":  {"name": "Light Bluish Gray", "block": "game:rockpolished-andesite",           "rgb": [160, 165, 169]},
    "72":  {"name": "Dark Bluish Gray",  "block": "game:stonebricks-basalt",              "rgb": [108, 110, 104]},
    "84":  {"name": "Medium Nougat",     "block": "game:claybricks-good-fire",            "rgb": [170, 125, 85]},
    "85":  {"name": "Dark Purple",       "block": "game:glass-violet",                    "rgb": [95, 38, 131]},
    "191": {"name": "Bright Lt Orange",  "block": "game:sand-granite",                    "rgb": [248, 187, 61]},
    "308": {"name": "Dark Brown",        "block": "game:planks-ebony-ud",                 "rgb": [53, 33, 0]},
    "320": {"name": "Dark Red",          "block": "game:claybricks-good-fire",            "rgb": [114, 14, 15]},
    "378": {"name": "Sand Green",        "block": "game:rockpolished-greenmarble",        "rgb": [112, 142, 124]},
    "379": {"name": "Sand Blue",         "block": "game:rockpolished-andesite",           "rgb": [112, 129, 154]},
    "484": {"name": "Dark Orange",       "block": "game:planks-acacia-ud",                "rgb": [179, 84, 8]},
    "503": {"name": "Very Lt Gray",      "block": "game:rockpolished-chalk",              "rgb": [230, 227, 218]},
    "default": {"name": "(unmapped)",    "block": "game:cobblestone-granite",             "rgb": [128, 128, 128]},
}


def warn(msg):
    print("warning: " + msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# LDraw parsing
# ---------------------------------------------------------------------------

def read_ldraw_lines(path):
    """Lines of an LDraw file; for BrickLink Studio .io files (zip archives),
    reads the embedded model.ldr."""
    if path.lower().endswith(".io"):
        import zipfile
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            member = next((n for n in ("model.ldr", "model2.ldr") if n in names), None)
            if member is None:
                raise SystemExit("error: no model.ldr inside " + path
                                 + " (password-protected .io files can't be read; "
                                 "export as LDraw from Studio instead)")
            return zf.read(member).decode("utf-8", errors="replace").splitlines()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def parse_ldraw(path):
    """Parse an .ldr/.mpd/.dat/.io into {subfile_name: [(color, matrix, translation, ref), ...]}.
    Returns (main_model_name, models dict). Matrix is a row-major 3x3 list of lists,
    translation a 3-vector, ref the lowercased referenced file name."""
    models = {}
    main = None
    current = None
    for raw in read_ldraw_lines(path):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "0":
            if len(parts) >= 3 and parts[1].upper() == "FILE":
                name = " ".join(parts[2:]).lower()
                current = name
                models.setdefault(name, [])
                if main is None:
                    main = name
            continue
        if parts[0] != "1" or len(parts) < 15:
            continue
        if current is None:
            current = "__main__"
            models.setdefault(current, [])
            main = current
        try:
            color = parts[1]
            x, y, z = (float(v) for v in parts[2:5])
            a, b, c, d, e, fm, g, h, i = (float(v) for v in parts[5:14])
        except ValueError:
            continue
        ref = " ".join(parts[14:]).lower()
        matrix = [[a, b, c], [d, e, fm], [g, h, i]]
        models[current].append((color, matrix, (x, y, z), ref))
    if main is None:
        raise SystemExit("error: no geometry lines (type 1) found in " + path)
    return main, models


def transform_point(m, t, p):
    return tuple(m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + t[r] for r in range(3))


def combine(m1, t1, m2, t2):
    """Compose transforms: apply (m2,t2) first, then (m1,t1)."""
    m = [[sum(m1[i][k] * m2[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    t = transform_point(m1, t1, t2)
    return m, t


def voxelize_ldraw(path, part_dims, plates_per_voxel):
    """Walk the model and return {(x, y_up, z): ldraw_color} on the voxel grid."""
    main, models = parse_ldraw(path)
    boxes = []          # (world AABB min, max, color)
    unknown = Counter()
    minifig_parts = 0

    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def recurse(name, m, t, color, depth):
        nonlocal minifig_parts
        if depth > 60:
            warn("recursion depth exceeded at " + name + "; circular reference?")
            return
        for (c, cm, ct, ref) in models[name]:
            ec = color if c in ("16", "24") else c
            wm, wt = combine(m, t, cm, ct)
            base = ref.replace("\\", "/").split("/")[-1]
            if ref in models:
                recurse(ref, wm, wt, ec, depth + 1)
                continue
            partno = base[:-4] if base.endswith(".dat") else base
            if any(partno.startswith(p) for p in MINIFIG_PART_PREFIXES):
                minifig_parts += 1
                continue
            dims = part_dims.get(partno)
            if dims is None and PRIMITIVE_RE.match(partno):
                # primitive: unit-scale geometry, sized by its matrix
                lo, hi = (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)
            else:
                if dims is None:
                    unknown[partno] += 1
                    dims = (1, 1, 3)  # assume a 1x1 brick
                w, dpt, hp = dims
                # local box: origin top-center, +Y down
                lo = (-w * STUD_LDU / 2, 0.0, -dpt * STUD_LDU / 2)
                hi = (w * STUD_LDU / 2, hp * PLATE_LDU, dpt * STUD_LDU / 2)
            corners = [(cx, cy, cz)
                       for cx in (lo[0], hi[0])
                       for cy in (lo[1], hi[1])
                       for cz in (lo[2], hi[2])]
            pts = [transform_point(wm, wt, p) for p in corners]
            mn = tuple(min(p[i] for p in pts) for i in range(3))
            mx = tuple(max(p[i] for p in pts) for i in range(3))
            boxes.append((mn, mx, ec))

    recurse(main, ident, (0.0, 0.0, 0.0), "16", 0)

    if minifig_parts:
        warn(f"skipped {minifig_parts} minifigure/creature parts (not building structure)")
    if unknown:
        summary = ", ".join(f"{p} (x{n})" for p, n in unknown.most_common(12))
        more = "" if len(unknown) <= 12 else f" and {len(unknown) - 12} more"
        warn(f"{sum(unknown.values())} parts with unknown footprint treated as 1x1 bricks: {summary}{more}")
        warn("add their sizes to a JSON file and pass --partdims to improve fidelity")

    # Models may sit on the stud grid or be offset by half a stud (1x1-brick
    # centers land on odd multiples of 10 LDU). Pick the grid origin per axis
    # that most box edges align to, so parts snap into single cells.
    v_ldu = plates_per_voxel * PLATE_LDU

    def grid_origin(edges, period):
        hist = Counter(int(round(e)) % int(period) for e in edges)
        return hist.most_common(1)[0][0] if hist else 0

    gx = grid_origin([b[0][0] for b in boxes], STUD_LDU)
    gz = grid_origin([b[0][2] for b in boxes], STUD_LDU)
    gy = grid_origin([b[0][1] for b in boxes], v_ldu)

    def cells(mn, mx, origin, period):
        count = max(1, int(round((mx - mn) / period)))
        first = int(round((mn - origin) / period))
        return range(first, first + count)

    voxels = {}
    for mn, mx, color in boxes:
        for vy in cells(mn[1], mx[1], gy, v_ldu):
            for vz in cells(mn[2], mx[2], gz, STUD_LDU):
                for vx in cells(mn[0], mx[0], gx, STUD_LDU):
                    voxels[(vx, -vy, vz)] = color  # -Y is up in LDraw; flip to +up
    return voxels


# ---------------------------------------------------------------------------
# MagicaVoxel parsing
# ---------------------------------------------------------------------------

VOX_DEFAULT_PALETTE_FIRST = (255, 255, 255, 255)  # index 1 in default palette


def parse_vox(path):
    """Parse a .vox file; returns ({(x, y_up, z): rgb_tuple}) for the first model."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"VOX ":
        raise SystemExit("error: not a MagicaVoxel .vox file")
    pos = 8
    voxels_raw = None
    palette = None

    def read_chunk(pos):
        cid = data[pos:pos + 4]
        n, m = struct.unpack("<ii", data[pos + 4:pos + 12])
        return cid, pos + 12, n, m

    end = len(data)
    while pos + 12 <= end:
        cid, body, n, m = read_chunk(pos)
        if cid == b"XYZI" and voxels_raw is None:
            cnt = struct.unpack("<i", data[body:body + 4])[0]
            voxels_raw = [struct.unpack("<4B", data[body + 4 + i * 4: body + 8 + i * 4])
                          for i in range(cnt)]
        elif cid == b"RGBA":
            palette = [struct.unpack("<4B", data[body + i * 4: body + 4 + i * 4])
                       for i in range(256)]
        pos = body + n

    if voxels_raw is None:
        raise SystemExit("error: no voxel data (XYZI chunk) found")
    if palette is None:
        # MagicaVoxel default palette approximation: grayscale fallback
        palette = [(i, i, i, 255) for i in range(256)]
        warn("no palette in file; using grayscale fallback (re-save in MagicaVoxel to embed one)")

    out = {}
    for (x, y, z, ci) in voxels_raw:
        rgba = palette[(ci - 1) % 256]
        out[(x, z, y)] = (rgba[0], rgba[1], rgba[2])  # vox Z is up -> our Y
    return out


def nearest_block(rgb, colormap):
    best, bestd = None, None
    for key, entry in colormap.items():
        if key == "default" or "rgb" not in entry:
            continue
        r, g, b = entry["rgb"]
        d = (r - rgb[0]) ** 2 + (g - rgb[1]) ** 2 + (b - rgb[2]) ** 2
        if bestd is None or d < bestd:
            best, bestd = entry["block"], d
    return best or colormap["default"]["block"]


# ---------------------------------------------------------------------------
# Schematic output
# ---------------------------------------------------------------------------

def build_schematic(block_voxels, game_version):
    """block_voxels: {(x, y, z): block_code}. Returns BlockSchematic dict."""
    xs = [p[0] for p in block_voxels]
    ys = [p[1] for p in block_voxels]
    zs = [p[2] for p in block_voxels]
    ox, oy, oz = min(xs), min(ys), min(zs)
    sx = max(xs) - ox + 1
    sy = max(ys) - oy + 1
    sz = max(zs) - oz + 1
    if sx > 1024 or sy > 1024 or sz > 1024:
        raise SystemExit(f"error: schematic {sx}x{sy}x{sz} exceeds the 1024-block limit per axis")

    code_ids = {}
    indices = []
    block_ids = []
    for (x, y, z), code in sorted(block_voxels.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        bid = code_ids.setdefault(code, len(code_ids) + 1)
        indices.append(((y - oy) << 20) | ((z - oz) << 10) | (x - ox))
        block_ids.append(bid)

    return {
        "GameVersion": game_version,
        "SizeX": sx, "SizeY": sy, "SizeZ": sz,
        "BlockCodes": {str(bid): code for code, bid in code_ids.items()},
        "ItemCodes": {},
        "Indices": indices,
        "BlockIds": block_ids,
        "DecorIndices": [],
        "DecorIds": [],
        "BlockEntities": {},
        "Entities": [],
        "ReplaceMode": 2,
        "EntranceRotation": -1,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("input", help=".ldr/.mpd/.dat LDraw file, .io Studio file, or .vox MagicaVoxel file")
    ap.add_argument("-o", "--output", help="output .json path (default: input name)")
    ap.add_argument("--colormap", help="JSON overriding the LDraw colour -> VS block map")
    ap.add_argument("--partdims", help="JSON with extra part footprints {partno: [x_studs, z_studs, plates]}")
    ap.add_argument("--plates-per-voxel", type=int, default=3,
                    help="vertical resolution: plates per voxel (default 3 = one brick, corrects the 5:6 aspect)")
    ap.add_argument("--validate", metavar="CODES_JSON",
                    help="JSON array of valid VS block codes; warn about unknown codes")
    ap.add_argument("--game-version", default="1.22.3")
    args = ap.parse_args()

    colormap = dict(DEFAULT_COLORMAP)
    if args.colormap:
        with open(args.colormap) as f:
            for k, v in json.load(f).items():
                colormap[k] = v

    part_dims = dict(PART_DIMS)
    if args.partdims:
        with open(args.partdims) as f:
            for k, v in json.load(f).items():
                part_dims[str(k)] = tuple(v)

    ext = os.path.splitext(args.input)[1].lower()
    if ext == ".vox":
        rgb_voxels = parse_vox(args.input)
        if not rgb_voxels:
            raise SystemExit("error: model contains no voxels")
        block_voxels = {p: nearest_block(rgb, colormap) for p, rgb in rgb_voxels.items()}
    else:
        color_voxels = voxelize_ldraw(args.input, part_dims, args.plates_per_voxel)
        if not color_voxels:
            raise SystemExit("error: model contains no voxels")
        unmapped = Counter()
        block_voxels = {}
        for p, c in color_voxels.items():
            entry = colormap.get(c)
            if entry is None:
                unmapped[c] += 1
                entry = colormap["default"]
            block_voxels[p] = entry["block"]
        for c, n in unmapped.most_common():
            warn(f"LDraw colour {c} not in colormap ({n} voxels) -> {colormap['default']['block']}")

    if args.validate:
        with open(args.validate) as f:
            valid = set(json.load(f))
        for code in sorted(set(block_voxels.values())):
            bare = code.split(":", 1)[-1]
            if bare not in valid:
                warn(f"block code '{code}' not found in {args.validate}; it will load as air in-game")

    schem = build_schematic(block_voxels, args.game_version)
    out = args.output or os.path.splitext(args.input)[0] + ".json"
    with open(out, "w") as f:
        json.dump(schem, f, separators=(",", ":"))

    counts = Counter(block_voxels.values())
    print(f"{out}: {schem['SizeX']}x{schem['SizeY']}x{schem['SizeZ']} "
          f"({len(block_voxels)} blocks, {len(counts)} block types)")
    for code, n in counts.most_common():
        print(f"  {n:>6}  {code}")


if __name__ == "__main__":
    main()
