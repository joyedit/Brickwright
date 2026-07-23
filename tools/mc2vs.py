#!/usr/bin/env python3
"""mc2vs - convert Minecraft schematics to Vintage Story schematics.

Input formats:
  .schem       Sponge/WorldEdit schematic, versions 1-3 (modern sites, WorldEdit //schem save)
  .schematic   Legacy MCEdit format (older sites like minecraft-schematics.com)

Output: a Vintage Story BlockSchematic .json for the Brickwright mod
(drop it in VintagestoryData/ModConfig/brickwright/) or WorldEdit /we imp.

Mapping philosophy:
  - Every default target block is verified present in the VS 1.22 creative
    inventory (sole exception: mossycobblestone, which resolves fine and
    spawns naturally in-world). Override any entry with --blockmap yourmap.json
    ({"minecraft_name": "game:vsblock", ...} — use null to skip a block).
  - Stairs, slabs and walls become full blocks of their material (chunky but
    solid). Thin decor (torches, fences, doors, rails, flowers, signs...) is
    skipped: doorways stay open for real VS doors.
  - Roofs become VS clay shingle blocks: stairs/slabs high in the build that
    cap their column are detected as roofing and their whole block type is
    switched to a matching clayshingleblock color (--no-roof-tiles disables).
  - Generic gray stone (cobble, stone bricks, stone, tuff) all maps into the
    andesite family so mixed-texture MC walls read as one material with
    finish patterns rather than several rock hues.
  - Glass panes become full glass blocks so windows survive.
  - Water is skipped (use --water to fill with blue glass). Lava becomes
    solid fired brick so interiors have no walkable holes.

Usage:
  mc2vs.py castle.schem                       -> castle.json
  mc2vs.py keep.schematic --skip-leaves
  mc2vs.py castle.schem --validate tools/vs-creative-codes-1.22.json
"""

import argparse
import gzip
import json
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldr2vs import build_schematic  # noqa: E402


def warn(msg):
    print("warning: " + msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Minimal NBT parser
# ---------------------------------------------------------------------------

class NbtReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n):
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u1(self): return self.take(1)[0]
    def i2(self): return struct.unpack(">h", self.take(2))[0]
    def i4(self): return struct.unpack(">i", self.take(4))[0]
    def i8(self): return struct.unpack(">q", self.take(8))[0]
    def f4(self): return struct.unpack(">f", self.take(4))[0]
    def f8(self): return struct.unpack(">d", self.take(8))[0]

    def string(self):
        return self.take(struct.unpack(">H", self.take(2))[0]).decode("utf-8", "replace")

    def payload(self, tag):
        if tag == 1: return struct.unpack(">b", self.take(1))[0]
        if tag == 2: return self.i2()
        if tag == 3: return self.i4()
        if tag == 4: return self.i8()
        if tag == 5: return self.f4()
        if tag == 6: return self.f8()
        if tag == 7: return self.take(self.i4())            # byte array -> bytes
        if tag == 8: return self.string()
        if tag == 9:
            etag = self.u1()
            return [self.payload(etag) for _ in range(self.i4())]
        if tag == 10:
            out = {}
            while True:
                t = self.u1()
                if t == 0: return out
                name = self.string()
                out[name] = self.payload(t)
        if tag == 11: return [self.i4() for _ in range(self.i4())]
        if tag == 12: return [self.i8() for _ in range(self.i4())]
        raise SystemExit(f"error: unsupported NBT tag {tag}")


def read_nbt(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    r = NbtReader(raw)
    tag = r.u1()
    if tag != 10:
        raise SystemExit("error: not an NBT file (no root compound)")
    r.string()  # root name
    return r.payload(10)


# ---------------------------------------------------------------------------
# Minecraft name -> Vintage Story block mapping (all VS codes creative-verified)
# ---------------------------------------------------------------------------

WOODS = {  # minecraft wood -> VS wood
    "oak": "oak", "spruce": "pine", "birch": "birch", "jungle": "kapok",
    "acacia": "acacia", "dark_oak": "walnut", "mangrove": "redwood",
    "cherry": "maple", "bamboo": "larch", "crimson": "purpleheart", "warped": "larch",
    "pale_oak": "birch",
}

COLORS = {  # minecraft dye color -> VS glass (for stained glass only)
    "white": "game:glass-quartz", "light_gray": "game:glass-smoky",
    "gray": "game:glass-smoky", "black": "game:glass-smoky",
    "red": "game:glass-red", "green": "game:glass-green",
    "lime": "game:glass-green", "blue": "game:glass-blue",
    "cyan": "game:glass-blue", "light_blue": "game:glass-blue",
    "yellow": "game:glass-yellow", "orange": "game:glass-yellow",
    "brown": "game:glass-brown", "purple": "game:glass-violet",
    "magenta": "game:glass-violet", "pink": "game:glass-pink",
}

# colored terracotta is fired clay and MC builders roof with it: send the
# roof-plausible colors to VS clay shingles (other colors fall back to SOLID_COLORS)
TERRACOTTA_SHINGLES = {
    "white": "cream", "light_gray": "gray", "gray": "gray", "black": "black",
    "brown": "brown", "red": "red", "orange": "orange", "yellow": "tan",
}

SOLID_COLORS = {  # minecraft dye color -> VS opaque block (wool/concrete/terracotta)
    "white": "game:rockpolished-whitemarble", "light_gray": "game:rockpolished-andesite",
    "gray": "game:cobblestone-basalt", "black": "game:rockpolished-basalt",
    "red": "game:rockpolished-redmarble", "green": "game:rockpolished-greenmarble",
    "lime": "game:rockpolished-greenmarble", "blue": "game:rockpolished-slate",
    "cyan": "game:cobblestone-slate", "light_blue": "game:rockpolished-slate",
    "yellow": "game:hay-normal-ud", "orange": "game:claybricks-good-fire",
    "brown": "game:planks-walnut-ud", "purple": "game:rockpolished-slate",
    "magenta": "game:rockpolished-redmarble", "pink": "game:claybricks-good-fire",
}

BLOCK_MAP = {
    # generic gray minecraft stone all lands in ONE VS rock (andesite) so mixed
    # cobble/brick/plain walls read as finish patterns, not random rock hues;
    # deepslate=basalt, granite/diorite stay distinct (deliberate MC color picks)
    "stone": "game:rockpolished-andesite", "smooth_stone": "game:rockpolished-andesite",
    "cobblestone": "game:cobblestone-andesite", "mossy_cobblestone": "game:mossycobblestone-andesite",
    "stone_bricks": "game:stonebricks-andesite", "mossy_stone_bricks": "game:agedstonebricks-andesite",
    "cracked_stone_bricks": "game:crackedstonebricks-andesite", "chiseled_stone_bricks": "game:stonebricks-andesite",
    "infested_stone": "game:rockpolished-andesite", "infested_stone_bricks": "game:stonebricks-andesite",
    "granite": "game:rockpolished-granite", "polished_granite": "game:rockpolished-granite",
    "diorite": "game:rockpolished-chalk", "polished_diorite": "game:rockpolished-chalk",
    "andesite": "game:cobblestone-andesite", "polished_andesite": "game:rockpolished-andesite",
    "deepslate": "game:cobblestone-basalt", "cobbled_deepslate": "game:cobblestone-basalt",
    "polished_deepslate": "game:rockpolished-basalt", "deepslate_bricks": "game:stonebricks-basalt",
    "deepslate_tiles": "game:stonebricks-basalt", "chiseled_deepslate": "game:stonebricks-basalt",
    "cracked_deepslate_bricks": "game:crackedstonebricks-basalt",
    "cracked_deepslate_tiles": "game:crackedstonebricks-basalt",
    "basalt": "game:cobblestone-basalt", "smooth_basalt": "game:rockpolished-basalt",
    "polished_basalt": "game:rockpolished-basalt",
    "cracked_polished_blackstone_bricks": "game:crackedstonebricks-basalt",
    "blackstone": "game:cobblestone-basalt", "polished_blackstone": "game:rockpolished-basalt",
    "polished_blackstone_bricks": "game:stonebricks-basalt", "gilded_blackstone": "game:stonebricks-basalt",
    "tuff": "game:cobblestone-andesite", "polished_tuff": "game:rockpolished-andesite",
    "tuff_bricks": "game:stonebricks-andesite", "chiseled_tuff": "game:stonebricks-andesite",
    "chiseled_tuff_bricks": "game:stonebricks-andesite",
    "calcite": "game:rockpolished-chalk",
    "dripstone_block": "game:cobblestone-claystone",
    "bricks": "game:claybricks-good-fire",
    "mud_bricks": "game:cobblestone-claystone", "packed_mud": "game:cobblestone-claystone",
    "sandstone": "game:stonebricks-sandstone", "chiseled_sandstone": "game:stonebricks-sandstone",
    "cut_sandstone": "game:stonebricks-sandstone", "smooth_sandstone": "game:rockpolished-limestone",
    "red_sandstone": "game:claybricks-good-fire", "chiseled_red_sandstone": "game:claybricks-good-fire",
    "cut_red_sandstone": "game:claybricks-good-fire", "smooth_red_sandstone": "game:claybricks-good-fire",
    "obsidian": "game:rockpolished-basalt", "crying_obsidian": "game:rockpolished-basalt",
    "bedrock": "game:rockpolished-basalt",
    "quartz_block": "game:rockpolished-whitemarble", "smooth_quartz": "game:rockpolished-whitemarble",
    "quartz_bricks": "game:rockpolished-whitemarble", "chiseled_quartz_block": "game:rockpolished-whitemarble",
    "quartz_pillar": "game:rockpolished-whitemarble",
    "prismarine": "game:rockpolished-greenmarble", "prismarine_bricks": "game:rockpolished-greenmarble",
    "dark_prismarine": "game:rockpolished-greenmarble",
    "nether_bricks": "game:stonebricks-basalt", "red_nether_bricks": "game:claybricks-good-fire",
    "chiseled_nether_bricks": "game:stonebricks-basalt", "netherrack": "game:claybricks-good-fire",
    "end_stone": "game:stonebricks-limestone", "end_stone_bricks": "game:stonebricks-limestone",
    "purpur_block": "game:glass-violet", "purpur_pillar": "game:glass-violet",
    "terracotta": "game:cobblestone-claystone", "clay": "game:cobblestone-claystone",
    "dirt": "game:soil-medium-none", "coarse_dirt": "game:soil-medium-none",
    "rooted_dirt": "game:soil-medium-none", "dirt_path": "game:soil-medium-none",
    "farmland": "game:soil-medium-none", "mud": "game:soil-medium-none",
    "grass_block": "game:soil-medium-normal", "podzol": "game:soil-medium-none",
    "mycelium": "game:soil-medium-none",
    # mossy stone accent, not soil: peridotite = dark olive-green rock (Feyd 2026-07-11)
    "moss_block": "game:cobblestone-peridotite",
    "pale_moss_block": "game:cobblestone-peridotite",
    # builders use stripped pale oak as white plaster; birch reads tan, chalk reads plaster
    "stripped_pale_oak_log": "game:rockpolished-chalk",
    "stripped_pale_oak_wood": "game:rockpolished-chalk",
    "sand": "game:sand-granite", "red_sand": "game:sand-granite", "gravel": "game:gravel-granite",
    "soul_sand": "game:soil-medium-none", "soul_soil": "game:soil-medium-none",
    "glass": "game:glass-plain", "tinted_glass": "game:glass-smoky",
    "glowstone": "game:glass-yellow", "sea_lantern": "game:glass-yellow",
    "shroomlight": "game:glass-yellow", "ochre_froglight": "game:glass-yellow",
    "ice": "game:lakeice", "packed_ice": "game:glacierice", "blue_ice": "game:glacierice",
    "snow_block": "game:snowblock", "powder_snow": "game:snowblock",
    "bone_block": "game:rockpolished-chalk",
    "bookshelf": "game:planks-oak-ud", "chiseled_bookshelf": "game:planks-oak-ud",
    "crafting_table": "game:planks-oak-ud", "cartography_table": "game:planks-oak-ud",
    "fletching_table": "game:planks-oak-ud", "smithing_table": "game:planks-oak-ud",
    "lectern": "game:planks-oak-ud", "barrel": "game:planks-oak-ud", "composter": "game:planks-oak-ud",
    "furnace": "game:cobblestone-andesite", "blast_furnace": "game:cobblestone-andesite",
    "smoker": "game:cobblestone-andesite", "dispenser": "game:cobblestone-andesite",
    "dropper": "game:cobblestone-andesite", "observer": "game:cobblestone-basalt",
    "lodestone": "game:stonebricks-andesite", "respawn_anchor": "game:rockpolished-basalt",
    "hay_block": "game:hay-normal-ud", "dried_kelp_block": "game:hay-normal-ud",
    "honeycomb_block": "game:hay-normal-ud", "sponge": "game:hay-normal-ud",
    "wet_sponge": "game:hay-normal-ud", "target": "game:hay-normal-ud",
    "melon": "game:rockpolished-greenmarble", "pumpkin": "game:claybricks-good-fire",
    "carved_pumpkin": "game:claybricks-good-fire", "jack_o_lantern": "game:glass-yellow",
    "iron_block": "game:rockpolished-andesite", "gold_block": "game:hay-normal-ud",
    "diamond_block": "game:glass-blue", "emerald_block": "game:rockpolished-greenmarble",
    "lapis_block": "game:glass-blue", "redstone_block": "game:rockpolished-redmarble",
    "coal_block": "game:rockpolished-basalt", "netherite_block": "game:rockpolished-basalt",
    "copper_block": "game:claybricks-good-fire", "raw_copper_block": "game:claybricks-good-fire",
    "amethyst_block": "game:glass-violet",
    # ores render as their host stone so walls/terrain stay continuous
    "coal_ore": "game:rockpolished-andesite", "iron_ore": "game:rockpolished-andesite",
    "copper_ore": "game:rockpolished-andesite", "gold_ore": "game:rockpolished-andesite",
    "redstone_ore": "game:rockpolished-andesite", "emerald_ore": "game:rockpolished-andesite",
    "lapis_ore": "game:rockpolished-andesite", "diamond_ore": "game:rockpolished-andesite",
    "deepslate_coal_ore": "game:cobblestone-basalt", "deepslate_iron_ore": "game:cobblestone-basalt",
    "deepslate_copper_ore": "game:cobblestone-basalt", "deepslate_gold_ore": "game:cobblestone-basalt",
    "deepslate_redstone_ore": "game:cobblestone-basalt", "deepslate_emerald_ore": "game:cobblestone-basalt",
    "deepslate_lapis_ore": "game:cobblestone-basalt", "deepslate_diamond_ore": "game:cobblestone-basalt",
    "nether_gold_ore": "game:claybricks-good-fire", "nether_quartz_ore": "game:claybricks-good-fire",
    "ancient_debris": "game:rockpolished-basalt",
    "red_mushroom_block": "game:claybricks-good-fire",
    "brown_mushroom_block": "game:cobblestone-claystone",
    "mushroom_stem": "game:debarkedlog-birch-ud",
    "magma_block": "game:cobblestone-basalt",
    # lava becomes solid "cooled lava" rather than a walkable hole (water has --water)
    "lava": "game:claybricks-good-fire", "flowing_lava": "game:claybricks-good-fire",
}

# things that should simply not exist in the converted building
SKIP_EXACT = {
    "air", "cave_air", "void_air", "water", "bubble_column",
    "torch", "wall_torch", "soul_torch", "soul_wall_torch", "redstone_torch",
    "redstone_wall_torch", "lantern", "soul_lantern", "end_rod", "chain",
    "ladder", "vine", "glow_lichen", "sculk_vein", "scaffolding", "cobweb",
    "rail", "powered_rail", "detector_rail", "activator_rail",
    "lever", "tripwire", "tripwire_hook", "redstone_wire", "repeater", "comparator",
    "iron_bars", "snow", "fire", "soul_fire", "barrier", "structure_void", "light",
    "short_grass", "grass", "tall_grass", "fern", "large_fern", "dead_bush",
    "seagrass", "tall_seagrass", "kelp", "kelp_plant", "sugar_cane", "bamboo",
    "lily_pad", "sweet_berry_bush", "cactus", "flower_pot", "moss_carpet",
    "rose_bush", "cave_vines", "cave_vines_plant", "azalea", "flowering_azalea",
    "sea_pickle", "candle", "leaf_litter", "copper_lantern", "waxed_copper_lantern",
    "big_dripleaf", "big_dripleaf_stem", "small_dripleaf", "hanging_roots",
    "spore_blossom", "pink_petals", "pale_hanging_moss",
    "chest", "trapped_chest", "ender_chest", "bell", "conduit", "beacon",
    "spawner", "enchanting_table", "brewing_stand", "cauldron", "water_cauldron",
    "anvil", "chipped_anvil", "damaged_anvil", "grindstone", "stonecutter",
    "loom", "jukebox", "note_block", "beehive", "bee_nest", "decorated_pot",
    "campfire", "soul_campfire", "lightning_rod", "waxed_lightning_rod",
    "player_head", "player_wall_head", "armor_stand", "item_frame", "painting",
    "turtle_egg", "frogspawn", "wheat", "carrots", "potatoes", "beetroots",
    "melon_stem", "pumpkin_stem", "cocoa", "nether_wart", "chorus_plant", "chorus_flower",
}

SKIP_SUFFIXES = (
    "_fence", "_fence_gate", "_door", "_trapdoor", "_button", "_pressure_plate",
    "_sign", "_wall_sign", "_hanging_sign", "_wall_hanging_sign", "_banner",
    "_wall_banner", "_carpet", "_sapling", "_tulip", "_orchid", "_bluet",
    "_daisy", "_lily_of_the_valley", "_rose_bush", "_mushroom", "_fungus",
    "_roots", "_sprouts", "_candle", "_candle_cake", "_head", "_wall_head",
    "_skull", "_wall_skull",
    "_coral", "_coral_fan", "_coral_wall_fan", "_egg", "_amethyst_bud",
    "_rail", "_pane_top",   # safety net
    "_chain", "_lightning_rod",   # incl. waxed/oxidized copper variants
)

FLOWER_NAMES = {"dandelion", "poppy", "allium", "azure_bluet", "blue_orchid",
                "oxeye_daisy", "cornflower", "lily_of_the_valley", "wither_rose",
                "sunflower", "lilac", "peony", "pitcher_plant", "torchflower"}


def strip_suffix(name, suffix):
    base = name[: -len(suffix)]
    # wooden stairs/slabs are plank-textured, not bark: oak_stairs -> oak_planks
    if base in WOODS:
        return base + "_planks"
    # stairs/slabs/walls name their material in singular: stone_brick_stairs
    for candidate in (base, base + "s", base + "_block"):
        if candidate in BLOCK_MAP or candidate.endswith("_planks") or candidate in COLORS:
            return candidate
    aliases = {"stone_brick": "stone_bricks", "brick": "bricks", "nether_brick": "nether_bricks",
               "mud_brick": "mud_bricks", "red_nether_brick": "red_nether_bricks",
               "end_stone_brick": "end_stone_bricks", "deepslate_brick": "deepslate_bricks",
               "deepslate_tile": "deepslate_tiles", "polished_blackstone_brick": "polished_blackstone_bricks",
               "quartz": "quartz_block", "purpur": "purpur_block", "cobble": "cobblestone"}
    return aliases.get(base, base)


ROOF_SHINGLE_COLORS = {  # MC roofing material -> clayshingleblock color
    "oak": "tan", "spruce": "brown", "birch": "cream", "jungle": "orange",
    "acacia": "fire", "dark_oak": "black", "mangrove": "red", "cherry": "red",
    "crimson": "red", "bamboo": "tan", "warped": "gray", "pale_oak": "cream",
    "stone": "gray", "smooth_stone": "gray", "cobblestone": "gray",
    "mossy_cobblestone": "gray", "stone_bricks": "gray", "mossy_stone_bricks": "gray",
    "cracked_stone_bricks": "gray", "andesite": "gray", "polished_andesite": "gray",
    "tuff": "gray", "tuff_bricks": "gray", "prismarine": "gray",
    "prismarine_bricks": "gray", "dark_prismarine": "gray", "purpur_block": "gray",
    "granite": "orange", "polished_granite": "orange",
    "diorite": "cream", "polished_diorite": "cream",
    "quartz_block": "cream", "smooth_quartz": "cream",
    "deepslate": "black", "cobbled_deepslate": "black", "polished_deepslate": "black",
    "deepslate_bricks": "black", "deepslate_tiles": "black", "blackstone": "black",
    "polished_blackstone": "black", "polished_blackstone_bricks": "black",
    "nether_bricks": "black",
    "bricks": "red", "mud_bricks": "brown",
    "sandstone": "tan", "smooth_sandstone": "tan", "cut_sandstone": "tan",
    "red_sandstone": "fire", "smooth_red_sandstone": "fire", "cut_red_sandstone": "fire",
}

# full blocks that are plausible roofing when they cap a column (MC medieval
# builds ridge stair-roofs with the matching full block)
ROOF_FULL_BLOCKS = {"deepslate_tiles", "deepslate_bricks", "dark_prismarine", "nether_bricks"}


def roof_shingle(name):
    """VS clay shingle code if this MC block could be roofing, else None."""
    base = None
    for suf in ("_stairs", "_slab"):
        if name.endswith(suf):
            base = strip_suffix(name, suf)
            if base.endswith("_planks"):
                base = base[:-7]
            break
    if base is None and name in ROOF_FULL_BLOCKS:
        base = name
    color = ROOF_SHINGLE_COLORS.get(base)
    return f"game:clayshingleblock-{color}" if color else None


class Resolver:
    def __init__(self, overrides, skip_leaves, water_block):
        self.map = dict(BLOCK_MAP)
        self.overrides = overrides or {}
        self.skip_leaves = skip_leaves
        self.water_block = water_block
        self.skipped = Counter()
        self.unknown = Counter()

    def resolve(self, name, props=None):
        """Minecraft block name (no namespace) + state properties -> VS code or None."""
        if name in self.overrides:
            return self.overrides[name]  # may be None = skip
        if name in ("water", "lava") and self.water_block and name == "water":
            return self.water_block
        if name in SKIP_EXACT or name in FLOWER_NAMES or name.startswith("potted_") or name.endswith("_bed"):
            self.skipped[name] += 1
            return None
        if name.endswith("_leaves"):
            if self.skip_leaves:
                self.skipped[name] += 1
                return None
            return "game:leaves-placed-oak"
        for suf in SKIP_SUFFIXES:
            if name.endswith(suf):
                self.skipped[name] += 1
                return None
        if name in self.map:
            return self.map[name]
        # minecraft axis (y/x/z) -> VS orientation (ud/we/ns)
        orient = {"y": "ud", "x": "we", "z": "ns"}.get((props or {}).get("axis", "y"), "ud")
        if name in WOODS:  # bare wood-name blocks in recent MC versions
            return f"game:log-placed-{WOODS[name]}-{orient}"
        if name.endswith("_planks"):
            wood = WOODS.get(name[:-7], "oak")
            return f"game:planks-{wood}-ud"
        if name.endswith("_shelf"):
            wood = WOODS.get(name[:-6], "oak")
            return f"game:planks-{wood}-ud"
        for suf in ("_log", "_wood", "_stem", "_hyphae"):
            if name.endswith(suf):
                stripped = name.startswith("stripped_")
                base = name[: -len(suf)].replace("stripped_", "")
                wood = WOODS.get(base, "oak")
                family = "debarkedlog" if stripped else "log-placed"
                return f"game:{family}-{wood}-{orient}"
        if name.endswith("_stained_glass_pane"):
            return COLORS.get(name[:-19], "game:glass-plain")
        if name.endswith("_stained_glass"):
            return COLORS.get(name[:-14], "game:glass-plain")
        if name == "glass_pane":
            return "game:glass-plain"
        if name.endswith("_terracotta") and not name.endswith("_glazed_terracotta"):
            color = name[:-11]
            if color in TERRACOTTA_SHINGLES:
                return f"game:clayshingleblock-{TERRACOTTA_SHINGLES[color]}"
        for suf in ("_wool", "_concrete", "_concrete_powder", "_terracotta", "_glazed_terracotta"):
            if name.endswith(suf):
                color = name[: -len(suf)]
                return SOLID_COLORS.get(color, "game:cobblestone-claystone")
        for suf in ("_stairs", "_slab", "_wall"):
            if name.endswith(suf):
                return self.resolve(strip_suffix(name, suf))
        self.unknown[name] += 1
        return None


# ---------------------------------------------------------------------------
# .schem (Sponge versions 1-3)
# ---------------------------------------------------------------------------

def read_varints(data, count):
    out, pos = [], 0
    for _ in range(count):
        v, shift = 0, 0
        while True:
            b = data[pos]; pos += 1
            v |= (b & 0x7F) << shift
            if not b & 0x80: break
            shift += 7
        out.append(v)
    return out


def load_schem(path):
    root = read_nbt(path)
    if "Schematic" in root and isinstance(root["Schematic"], dict):
        root = root["Schematic"]              # v3 nests everything
    w, h, l = int(root["Width"]), int(root["Height"]), int(root["Length"])
    blocks = root.get("Blocks") if isinstance(root.get("Blocks"), dict) else root
    palette = blocks.get("Palette") or root.get("Palette")
    data = blocks.get("Data") or root.get("BlockData")
    if palette is None or data is None:
        raise SystemExit("error: no palette/block data found (is this really a .schem?)")
    names = {}
    for key, idx in palette.items():
        name, _, rest = key.partition("[")
        props = {}
        for kv in rest.rstrip("]").split(","):
            k, _, v = kv.partition("=")
            if k: props[k] = v
        names[int(idx)] = (name.split(":", 1)[-1], props)
    indices = read_varints(bytes(data), w * h * l)
    for i, pi in enumerate(indices):
        entry = names.get(pi)
        if entry is None: continue
        x = i % w
        z = (i // w) % l
        y = i // (w * l)
        yield x, y, z, entry[0], entry[1]


# ---------------------------------------------------------------------------
# .litematic (Litematica) - bit-packed palette indices, one or more regions
# ---------------------------------------------------------------------------

def load_litematic(path):
    root = read_nbt(path)
    regions = root.get("Regions")
    if not regions:
        raise SystemExit("error: no Regions in litematic file")
    for reg in regions.values():
        sx, sy, sz = (int(reg["Size"][a]) for a in ("x", "y", "z"))
        px, py, pz = (int(reg["Position"][a]) for a in ("x", "y", "z"))
        # negative sizes mean the region extends in the negative direction
        ox = px + (sx + 1 if sx < 0 else 0)
        oy = py + (sy + 1 if sy < 0 else 0)
        oz = pz + (sz + 1 if sz < 0 else 0)
        w, h, l = abs(sx), abs(sy), abs(sz)

        palette = reg["BlockStatePalette"]
        names = [(str(p.get("Name", "minecraft:air")).split(":", 1)[-1],
                  {k: str(v) for k, v in (p.get("Properties") or {}).items()})
                 for p in palette]
        bits = max(2, (len(palette) - 1).bit_length())
        mask = (1 << bits) - 1
        longs = [v & 0xFFFFFFFFFFFFFFFF for v in reg["BlockStates"]]

        for i in range(w * h * l):
            start = i * bits
            j, off = divmod(start, 64)
            v = longs[j] >> off
            if off + bits > 64:
                v |= longs[j + 1] << (64 - off)
            name, props = names[v & mask] if (v & mask) < len(names) else ("air", {})
            x = i % w
            z = (i // w) % l
            y = i // (w * l)
            yield ox + x, oy + y, oz + z, name, props


# ---------------------------------------------------------------------------
# .schematic (legacy MCEdit) - numeric IDs
# ---------------------------------------------------------------------------

LEGACY_WOODS = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak"]
LEGACY_COLORS = ["white", "orange", "magenta", "light_blue", "yellow", "lime",
                 "pink", "gray", "light_gray", "cyan", "purple", "blue",
                 "brown", "green", "red", "black"]

def legacy_name(bid, data):
    if bid == 0: return "air"
    if bid == 1: return ["stone", "granite", "polished_granite", "diorite",
                         "polished_diorite", "andesite", "polished_andesite"][data] if data < 7 else "stone"
    if bid == 2: return "grass_block"
    if bid == 3: return "dirt"
    if bid == 4: return "cobblestone"
    if bid == 5: return LEGACY_WOODS[data % 6] + "_planks"
    if bid in (8, 9): return "water"
    if bid in (10, 11): return "lava"
    if bid == 12: return "sand"
    if bid == 13: return "gravel"
    if bid == 17: return LEGACY_WOODS[data % 4] + "_log"
    if bid == 162: return LEGACY_WOODS[4 + data % 2] + "_log"
    if bid in (18, 161): return "oak_leaves"
    if bid == 20: return "glass"
    if bid == 22: return "lapis_block"
    if bid == 24: return "sandstone"
    if bid == 35: return LEGACY_COLORS[data % 16] + "_wool"
    if bid == 41: return "gold_block"
    if bid == 42: return "iron_block"
    if bid in (43, 44):
        return ["smooth_stone", "sandstone", "oak_planks", "cobblestone",
                "bricks", "stone_bricks", "nether_bricks", "quartz_block"][data % 8]
    if bid == 45: return "bricks"
    if bid == 47: return "bookshelf"
    if bid == 48: return "mossy_cobblestone"
    if bid == 49: return "obsidian"
    if bid == 53: return "oak_stairs"
    if bid in (61, 62): return "furnace"
    if bid == 67: return "cobblestone_stairs"
    if bid == 79: return "ice"
    if bid == 80: return "snow_block"
    if bid == 82: return "clay"
    if bid == 87: return "netherrack"
    if bid == 89: return "glowstone"
    if bid == 95: return LEGACY_COLORS[data % 16] + "_stained_glass"
    if bid == 98: return ["stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks",
                          "chiseled_stone_bricks"][data % 4]
    if bid == 102: return "glass_pane"
    if bid == 108: return "brick_stairs"
    if bid == 109: return "stone_brick_stairs"
    if bid == 112: return "nether_bricks"
    if bid == 114: return "nether_brick_stairs"
    if bid == 121: return "end_stone"
    if bid in (125, 126): return LEGACY_WOODS[data % 6] + "_planks"
    if bid == 128: return "sandstone_stairs"
    if bid == 133: return "emerald_block"
    if bid == 134: return "spruce_stairs"
    if bid == 135: return "birch_stairs"
    if bid == 136: return "jungle_stairs"
    if bid == 139: return "cobblestone_wall"
    if bid == 152: return "redstone_block"
    if bid == 155: return "quartz_block"
    if bid == 156: return "quartz_stairs"
    if bid == 159: return LEGACY_COLORS[data % 16] + "_terracotta"
    if bid == 163: return "acacia_stairs"
    if bid == 164: return "dark_oak_stairs"
    if bid == 168: return "prismarine"
    if bid == 169: return "sea_lantern"
    if bid == 170: return "hay_block"
    if bid == 172: return "terracotta"
    if bid == 174: return "packed_ice"
    if bid == 179: return "red_sandstone"
    if bid == 180: return "red_sandstone_stairs"
    return None  # unknown -> skipped with counter


def load_legacy(path):
    root = read_nbt(path)
    w, h, l = int(root["Width"]), int(root["Height"]), int(root["Length"])
    blocks = root["Blocks"]
    data = root.get("Data", b"\x00" * len(blocks))
    unknown_ids = Counter()
    for i in range(w * h * l):
        bid = blocks[i]
        dv = data[i] & 0xF if i < len(data) else 0
        name = legacy_name(bid, dv)
        if name is None:
            unknown_ids[bid] += 1
            continue
        props = None
        if bid in (17, 162):  # legacy logs: axis in data bits 2-3
            props = {"axis": {0: "y", 1: "x", 2: "z"}.get((dv >> 2) & 0x3, "y")}
        x = i % w
        z = (i // w) % l
        y = i // (w * l)
        yield x, y, z, name, props
    if unknown_ids:
        summary = ", ".join(f"id {b} (x{n})" for b, n in unknown_ids.most_common(10))
        warn(f"skipped {sum(unknown_ids.values())} blocks with unmapped legacy ids: {summary}")


# ---------------------------------------------------------------------------

NEIGHBORS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def cull_hidden(voxels):
    """Drop blocks enclosed on all six sides by opaque blocks.

    Litematic builds often hide thousands of filler blocks (wool, dirt) inside
    solid terrain; they bloat the schematic and the survival material bill.
    See-through blocks (glass, ice) never hide a neighbor and are always kept.
    """
    def opaque(code):
        return "glass" not in code and "ice" not in code

    solid = {p for p, c in voxels.items() if opaque(c)}
    hidden = [(x, y, z) for x, y, z in solid
              if all((x + dx, y + dy, z + dz) in solid for dx, dy, dz in NEIGHBORS)]
    for p in hidden:
        del voxels[p]
    return len(hidden)


def retile_roofs(raw, block_voxels):
    """Rewrite roofing blocks to VS clay shingles, in place.

    A stair/slab is roofing if it sits in the upper part of the build and caps
    its column (nothing between it and the sky). The switch is voted per MC
    block type so a roof material converts wholesale: dormers tucked under an
    eave follow the rest of their roof, and a type used mostly indoors (or as
    ground-level steps) is left alone entirely.
    """
    min_y = min(y for _, y, _, _, _ in raw)
    max_y = max(y for _, y, _, _, _ in raw)
    floor_y = min_y + (max_y - min_y) * 0.25  # entry steps / garden walls live below this
    colmax = {}
    for x, y, z, _, _ in raw:
        if colmax.get((x, z), min_y - 1) < y:
            colmax[(x, z)] = y

    exposed, total = Counter(), Counter()
    for x, y, z, name, _ in raw:
        if y >= floor_y and roof_shingle(name):
            total[name] += 1
            if y == colmax[(x, z)]:
                exposed[name] += 1
    roof_names = {n for n in total if exposed[n] / total[n] >= 0.4}

    counts = Counter()
    for x, y, z, name, _ in raw:
        if name in roof_names and y >= floor_y:
            block_voxels[(x, y, z)] = roof_shingle(name)
            counts[name] += 1
    if counts:
        top = ", ".join(f"{k} (x{v})" for k, v in counts.most_common())
        print(f"roofing: {sum(counts.values())} blocks -> clay shingles: {top}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("input", help=".schem (Sponge/WorldEdit) or .schematic (MCEdit) file")
    ap.add_argument("-o", "--output", help="output .json path (default: input name)")
    ap.add_argument("--blockmap", help="JSON overriding the Minecraft -> VS block map")
    ap.add_argument("--skip-leaves", action="store_true", help="drop all leaf blocks")
    ap.add_argument("--water", metavar="VSBLOCK", nargs="?", const="game:glass-blue",
                    help="fill water with this block (default when flag given: blue glass)")
    ap.add_argument("--cull-hidden", action="store_true",
                    help="drop blocks fully enclosed by opaque blocks (interior fill)")
    ap.add_argument("--no-roof-tiles", action="store_true",
                    help="keep roof stairs/slabs as their material instead of VS clay shingles")
    ap.add_argument("--validate", metavar="CODES_JSON",
                    help="JSON array of valid VS block codes; warn about unknown codes")
    ap.add_argument("--game-version", default="1.22.3")
    args = ap.parse_args()

    overrides = None
    if args.blockmap:
        with open(args.blockmap) as f:
            overrides = json.load(f)

    resolver = Resolver(overrides, args.skip_leaves, args.water)

    ext = os.path.splitext(args.input)[1].lower()
    loader = {"": load_schem, ".schem": load_schem,
              ".litematic": load_litematic}.get(ext, load_legacy)

    raw = []
    for x, y, z, name, props in loader(args.input):
        code = resolver.resolve(name, props)
        if code:
            raw.append((x, y, z, name, code))

    block_voxels = {(x, y, z): code for x, y, z, _, code in raw}

    if not block_voxels:
        raise SystemExit("error: nothing converted (all blocks skipped?)")

    if not args.no_roof_tiles:
        retile_roofs(raw, block_voxels)

    if args.cull_hidden:
        n = cull_hidden(block_voxels)
        if n:
            print(f"culled {n} hidden interior blocks")

    if resolver.skipped:
        n = sum(resolver.skipped.values())
        top = ", ".join(f"{k} (x{v})" for k, v in resolver.skipped.most_common(8))
        warn(f"skipped {n} decor/fluid blocks: {top}" + (" ..." if len(resolver.skipped) > 8 else ""))
    if resolver.unknown:
        top = ", ".join(f"{k} (x{v})" for k, v in resolver.unknown.most_common(10))
        warn(f"unmapped block names skipped (add to --blockmap to keep): {top}")

    if args.validate:
        with open(args.validate) as f:
            valid = set(json.load(f))
        for code in sorted(set(block_voxels.values())):
            if code.split(":", 1)[-1] not in valid:
                warn(f"block code '{code}' not found in {args.validate}; it will load as air in-game")

    schem = build_schematic(block_voxels, args.game_version)
    out = args.output or os.path.splitext(args.input)[0] + ".json"
    with open(out, "w") as f:
        json.dump(schem, f, separators=(",", ":"))

    counts = Counter(block_voxels.values())
    print(f"{out}: {schem['SizeX']}x{schem['SizeY']}x{schem['SizeZ']} "
          f"({len(block_voxels)} blocks, {len(counts)} block types)")
    for code, n in counts.most_common(15):
        print(f"  {n:>7}  {code}")
    if len(counts) > 15:
        print(f"  ... and {len(counts) - 15} more block types")


if __name__ == "__main__":
    main()
