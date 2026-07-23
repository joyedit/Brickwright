# Brickwright

Import digital Lego builds into **Vintage Story**.

Lego models are voxel structures on a rigid grid, and Vintage Story is a voxel
engine — so digital Lego plans (LDraw `.ldr`/`.mpd`, BrickLink Studio `.io`)
convert cleanly into game schematics. Brickwright gives you both halves of the
pipeline:

1. **`tools/ldr2vs.py`** — converts LDraw or MagicaVoxel files into Vintage
   Story `BlockSchematic` `.json` files.
2. **The Brickwright mod** — a server-side mod that places those schematics
   in-world with a chat command (no WorldEdit required), with one-step undo.

```
[ .io ] --Studio export--> [ .ldr / .mpd ] --ldr2vs.py--> [ VS .json ] --/bw paste--> in-world
                            [ .vox (MagicaVoxel) ] ----^
```

## Finding blueprints

Brickwright reads three families of files, and each has large free libraries:

### Lego builds (`.io`, `.ldr`, `.mpd`)

- **Rebrickable** (rebrickable.com/mocs) — thousands of MOCs with parts lists;
  filter by *free* instructions. Many older/free MOCs attach an `.io` or
  LDraw file directly. Strong castle/medieval categories.
  **Check the file type on the listing**: "PDF Building Instructions" are
  page-by-page pictures for human builders and can NOT be converted — you
  need a listing that includes a `.io` / Studio / LDraw / LDD file. Free
  MOCs usually attach the raw file; paid ones often sell the PDF only.
- **BrickLink Studio Gallery** (bricklink.com/v3/studio/gallery.page) —
  community uploads from the Studio editor, downloadable as `.io`.
- **LDraw Official Model Repository** (library.ldraw.org/omr) — official LEGO
  sets recreated as `.mpd`, searchable by set number. Good hunting for ancient
  architecture: the LEGO Architecture and Icons lines (Colosseum, Great
  Pyramid, and so on) have community recreations here and on Eurobricks.
- **Eurobricks "Official Lego sets made in LDraw" key topic**
  (eurobricks.com, topic 48285) — a 90+ page community archive of official
  sets in `.ldr`/`.mpd`.
- **BrickSafe** (bricksafe.com) — free file host used by MOC builders; search
  "castle", "medieval", "ruins", "temple".

`.io` files can be fed to the converter directly — they are zip archives and
the embedded `model.ldr` is read automatically. If a particular `.io` was
saved password-protected, open it in Studio (free) and use *File → Export As
→ LDraw* instead.

### Voxel models (`.vox`)

Anything made in or for MagicaVoxel converts too — often better suited to
"props" than Lego files, since voxel artists work at block scale already:

- **OpenGameArt** (opengameart.org) — e.g. the free *Medieval Theme Voxels
  Asset Pack* (hundreds of CC-licensed models: houses, towers, siege gear).
- **itch.io** (itch.io/game-assets, search "voxel") — many free/pay-what-you-
  want packs like *Medieval Voxel Buildings*; multi-model files are one `.vox`
  with grouped objects (re-save individual models from MagicaVoxel).
- **MagicaVoxel's own sample repository** (github.com/ephtracy/voxel-model) —
  includes castle and scene samples.

### Minecraft schematics (`.schem`, `.schematic`) — the motherlode

The largest free libraries of block-built castles, ancient buildings and props
are Minecraft schematic sites. Convert them with the second bundled tool:

```bash
python3 tools/mc2vs.py castle.schem
python3 tools/mc2vs.py castle.litematic --skip-leaves
python3 tools/mc2vs.py oldkeep.schematic
```

- **Minecraft-Schematics.com** (minecraft-schematics.com/category/castles/) —
  long-running library with dedicated castle/medieval categories (free account).
- **MineSchematic** (mineschematic.com) — modern site; `.schem` and
  `.litematic` downloads both work.
- **Planet Minecraft** (planetminecraft.com/projects/tag/castle/?share=schematic)
  — filter projects by "downloadable schematic".
- **Abfielder's schematics** (abfielder.com) — thousands of free schematics.

Notes: three formats are read — modern Sponge/WorldEdit `.schem` (v1–3),
Litematica `.litematic` (all regions merged), and legacy MCEdit `.schematic`.
When a site offers several formats, prefer the one the build was uploaded in:
some sites' format converters strip block-state properties (log orientation,
etc.) — e.g. MineSchematic's `.schem` export loses the axis data its
`.litematic` files carry. `.nbt` (structure block) is not supported. Stairs/slabs/walls become full blocks of the same
material; thin decor (torches, fences, doors, rails, flowers, signs) is
skipped so doorways stay open for real VS doors; glass panes become full
glass so windows survive. Water is skipped unless you pass `--water`.
Roofs become VS clay shingles: stairs/slabs that sit high in the build and
cap their column are detected as roofing and their whole block type switches
to a matching `clayshingleblock` color (`--no-roof-tiles` disables); colored
terracotta in roof-plausible colors maps to shingles too. Generic gray stone
(cobble, stone bricks, stone, tuff) all lands in the andesite family so
mixed-texture walls read as finish patterns in a single rock, not random
hues — deepslate stays basalt, granite/diorite stay their own color.
Override any block mapping with `--blockmap yourmap.json`
(`{"minecraft_name": "game:vsblock"}`, value `null` = skip).

### Vintage home plans (plan books, Sears kit homes...)

Old plan-book blueprints don't come as 3D files, so `tools/plan2vs.py`
compiles a hand-authored plan file instead: per-storey ASCII floor grids
(`#` wall, `w` window, `d` door, `.` floor...) plus a list of roof planes
(eave edge, eave height, pitch as blocks-of-run per block-of-rise). Gable
ends and catslide skirts are filled automatically up to the roof underside;
chimney cells punch through and rise above it. Roof planes with `"slanted":
"grayclay"` (or redclay, slate, thatch...) emit VS slanted-roofing pieces —
smooth 45° at run 1, a stepped slant-and-tread profile at shallower pitches —
instead of chunky full blocks. `"edge": "HIP"` turns a whole rect into a hip
roof, generating the corner arrises, ridge cap, ridgeends and pyramid tips
automatically. Digit cells `1`–`9` lay stair runs (real stair blocks with
riser fill) and `void` cells cut the stairwell in the floor above. Scale
convention: 1 block = 2 ft, one storey = 4 layers.

```bash
python3 tools/plan2vs.py samples/sierra-a611.plan.json --validate tools/vs-block-codes-1.22.json
```

`samples/sierra-a611.plan.json` — "The Sierra", design A611 from a 1926 plan
book (English cottage / Tudor Revival) — is the worked example: front-facing
gable, catslide over the garage-turned-cart-shed, back work porch, west
chimney. The full plan format is documented in the `plan2vs.py` docstring.

### Licensing reminders

Official LEGO set designs are copyrighted — convert them for your own world,
don't redistribute the converted schematics. MOC and voxel-pack licenses vary;
most ask for credit, some (CC0) ask nothing. When you share a schematic,
credit the original builder in the file name or a note.

## Converting a build

```bash
python3 tools/ldr2vs.py castle.ldr                 # -> castle.json
python3 tools/ldr2vs.py build.vox                  # MagicaVoxel also works
python3 tools/ldr2vs.py castle.ldr --validate tools/vs-block-codes-1.22.json
```

Drop the resulting `.json` into `~/.config/VintagestoryData/ModConfig/brickwright/`
(the folder is created on first server start with the mod loaded).

Useful options:

| Option | What it does |
|---|---|
| `--colormap map.json` | Override the LDraw-colour → VS-block mapping (only the keys you supply are replaced). |
| `--partdims extra.json` | Add footprints for parts the tool doesn't know: `{"3001": [4, 2, 3]}` = studs-x, studs-z, height-in-plates. |
| `--plates-per-voxel N` | Vertical resolution. Default `3` (one brick = one block, which corrects Lego's 5:6 width:height ratio). Use `1` for plate-accurate tall builds. |
| `--validate codes.json` | Warn about block codes that don't exist in the game (they'd load as air). `tools/vs-creative-codes-1.22.json` lists blocks visible in the creative inventory (recommended); `tools/vs-block-codes-1.22.json` lists every block the game can resolve. Both generated from VS 1.22.3's assets. |

### How the conversion works

- LDraw grid: 1 stud = 20 LDU horizontally, 1 plate = 8 LDU vertically,
  1 brick = 24 LDU. By default one stud becomes one block and one brick-height
  becomes one block, so a 1×1 brick is a perfect cube — the aspect-ratio
  problem solves itself.
- Parts are voxelized as bounding boxes using a built-in footprint table of
  ~120 common bricks/plates/tiles/slopes. Unknown parts (minifigs, greebles)
  become 1×1 bricks and are listed in a warning — add real sizes with
  `--partdims` if they matter.
- Interiors stay hollow, and empty cells don't clear terrain when pasted
  (ReplaceMode: ReplaceAllNoAir). Clear the site first with WorldEdit if the
  build intersects terrain.
- Default palette highlights (every block verified present in the VS 1.22.3
  creative inventory): red → polished red marble, black → polished basalt,
  tan → limestone bricks, orange → fire clay bricks, light/dark bluish gray →
  polished andesite / basalt bricks, white → polished white marble, brown →
  walnut planks, trans-clear → glass. Blues/pinks/purples have no solid
  vanilla equivalent and map to stained glass — override via `--colormap` if
  you'd rather have something opaque. Brick-course blocks are deliberately
  not used by default (they're laid from individual fired bricks, not placed
  from inventory), but `/bw cost` understands them if your own colormap maps
  to them.

## In-game placement dialog

Press **Ctrl+B** (rebindable: "Brickwright placement dialog" in Settings →
Controls) to open a small placement window:

- **Filter box + list** — every schematic in `ModConfig/brickwright/`, in a
  scrollable list. Type in the box to filter by name substring (case-
  insensitive, e.g. `rad` for all the Radford plans); click a row to select.
- **N / E / S / W** (compass cross) — places the building ahead of you along
  your look direction (near face at least 6 blocks away), facing that
  direction (N = as authored). Click another direction and the pending
  building is removed and re-placed rotated **in the same spot**, so you can
  try all four before deciding.
- **Set** (center) — commit the placement. Direction buttons start a fresh
  paste after this; Undo can still remove it.
- **Undo** — remove the building and restore what was there.
- **Materials readout** — picking a schematic shows whether you have the
  blocks for it (inventory + containers within 10 blocks), and what you're
  short. Informational for now; a survival mode where pasting consumes these
  materials is planned.

Rotation and undo both refuse if the structure has been modified since it was
placed (same guard as `/bw undo`).

## In-game commands

Requires the `controlserver` privilege (i.e. run it as admin / in singleplayer
with cheats). The dialog needs the same privilege.

| Command | Effect |
|---|---|
| `/bw list` | List schematics found in `ModConfig/brickwright/`. |
| `/bw info <name>` | Size and block counts without placing. |
| `/bw cost <name>` | Full bill of materials: required blocks vs. what you have in inventory and containers within 10 blocks. Natural stone (`rock-*`) never counts as a supply. |
| `/bw paste <name> [angle]` | Place ahead of you along your look direction. Angle: 90/180/270. |
| `/bw undo` | Revert your last paste (single level, per player). Only works while the paste is untouched — if any block of the structure has been broken, harvested, or replaced since pasting, undo refuses. |
| `/bw mark` | Mark an export corner: the block you're aiming at, or the block **under your feet** if you're looking at the sky (handy when eaves overhang the corner — stand on the spot and look up). Mark two **diagonally opposite ground corners**; height is detected automatically. Each corner shows as a glowing pillar (green = first, amber = second), and the full selection appears as a translucent box once both are set. |
| `/bw unmark` | Clear the export selection. |
| `/bw export <name> [keepterrain] [empty]` | Save the marked area as `ModConfig/brickwright/<name>.json` — instantly pasteable (`/bw paste <name>`, dialog, cost, undo) and shareable: send the file to a friend, they drop it in their own schematics folder. Everything inside copies verbatim by default — furnishings, chiseled-block shapes, sign text, door states, and container contents. Natural terrain caught in the box (soil, sand, gravel, wild grass, snow) is stripped unless you pass `keepterrain`, so sloped building sites don't paste a hill slice. Pass `empty` to strip container contents (chests paste fresh and empty) — recommended before sharing a file, both to keep your valuables private and because pasting stocked chests is free item duplication. |

To share a build: `/bw mark` two ground corners, `/bw export mycastle`, then
hand out the JSON file. On sloped sites, export buildings one at a time —
buildings keep their original *relative* heights, so two houses from different
terrace levels will not both sit flush on flat ground.

Bundled samples (in `samples/`):

- `cottage.ldr` — hand-written LDraw test build; convert it with ldr2vs.py.
- `make_wizardtower.py` — generates `wizardtower.json`, a 13x13x20 voxel
  rendition of the Hirst Arts wizard's tower (hirstarts.com/wiztower, mold
  #50 design by Bruce Hirst; adapted as an original voxel interpretation).

Drop the `.json` files in the schematics folder, then `/bw paste cottage`
or open the dialog with Ctrl+B.

## Building the mod

```bash
./deploy.sh    # builds and installs Brickwright.zip into ~/.config/VintagestoryData/Mods
```

Targets net10.0 (Vintage Story 1.22+). Set `VINTAGE_STORY` if your game lives
somewhere other than `~/Apps/vintagestory`.

## Licensing note

Official Lego set designs are copyrighted. User-created MOCs shared on
BrickSafe or Eurobricks are generally fine to adapt for a free mod — but
credit the original digital builder in your release notes, and ask when a
file has no stated license. This mod ships no Lego-derived assets; the sample
cottage is original.
