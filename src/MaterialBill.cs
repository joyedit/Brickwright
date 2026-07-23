using System.Collections.Generic;
using System.Linq;
using System.Text;
using Vintagestory.API.Common;
using Vintagestory.API.MathTools;
using Vintagestory.API.Server;

namespace Brickwright
{
    /// <summary>
    /// Computes what a schematic costs to build and what the player has on
    /// hand (inventory + nearby containers). Phase 1: read-only reporting;
    /// the same data will later drive consumption, refunds and color-based
    /// substitution.
    /// </summary>
    public class MaterialBill
    {
        /// <summary>How far around the player to scan for chests/containers.</summary>
        public const int ContainerRadius = 10;

        // orientation/state suffixes that don't change which item you need:
        // planks-oak-ns and planks-oak-we both come from the same plank block
        static readonly HashSet<string> OrientSuffixes = new()
        {
            "ns", "we", "ud", "north", "east", "south", "west", "up", "down", "free", "snow"
        };

        public class Entry
        {
            public string Code;          // normalized block code, or "item:..." for item-based materials
            public string Title;         // in-game display name (what creative search matches)
            public int Required;
            public int Available;
            public bool NaturalStone;    // raw terrain rock: never counted as a supply

            public string ShortCode => (Code.StartsWith("item:") ? Code.Substring(5) : Code).Replace("game:", "");
            public string DisplayName => Title != null ? $"{Title} [{ShortCode}]" : ShortCode;
        }

        /// <summary>Some blocks aren't inventory blocks but are built from items:
        /// brick course blocks are laid from individual burned bricks (4 or 8 per
        /// course). Returns the material key and how many are needed per block.</summary>
        static (string key, int perBlock) MaterialFor(string normCode)
        {
            int sep = normCode.IndexOf(':');
            string domain = normCode.Substring(0, sep);
            string path = normCode.Substring(sep + 1);

            if (path.StartsWith("brickcourse-"))
            {
                string[] parts = path.Split('-');   // brickcourse-{four|eight}-{pattern}-{color}
                int per = parts.Length > 1 && parts[1] == "eight" ? 8 : 4;
                string color = parts[parts.Length - 1];
                return ($"item:{domain}:burnedbrick-{color}", per);
            }
            return (normCode, 1);
        }

        public static string Normalize(AssetLocation code)
        {
            string[] parts = code.Path.Split('-');
            int end = parts.Length;
            while (end > 1 && OrientSuffixes.Contains(parts[end - 1])) end--;
            return code.Domain + ":" + string.Join("-", parts.Take(end));
        }

        static bool IsNaturalStone(string normalizedCode)
        {
            string path = normalizedCode.Split(':').Last();
            return path.StartsWith("rock-") || path == "rock";
        }

        /// <summary>Required materials (blocks or items) per material key, with a
        /// representative original block code for display-name resolution.</summary>
        public static Dictionary<string, (int count, AssetLocation sample)> Requirements(BlockSchematic schematic)
        {
            var required = new Dictionary<string, (int, AssetLocation)>();
            foreach (int id in schematic.BlockIds)
            {
                if (!schematic.BlockCodes.TryGetValue(id, out var code)) continue;
                var (key, perBlock) = MaterialFor(Normalize(code));
                required[key] = required.TryGetValue(key, out var cur)
                    ? (cur.Item1 + perBlock, cur.Item2)
                    : (perBlock, code);
            }
            return required;
        }

        /// <summary>In-game display name for a material key — this is what the
        /// creative inventory search matches (e.g. stonebricks-basalt shows as
        /// "Basalt ashlar blocks").</summary>
        static string ResolveTitle(ICoreServerAPI sapi, string key, AssetLocation sample)
        {
            try
            {
                if (key.StartsWith("item:"))
                {
                    var item = sapi.World.GetItem(new AssetLocation(key.Substring(5)));
                    return item == null ? null : item.GetHeldItemName(new ItemStack(item));
                }
                var block = sapi.World.GetBlock(sample);
                return block == null ? null : block.GetHeldItemName(new ItemStack(block));
            }
            catch
            {
                return null;
            }
        }

        /// <summary>Counts matching blocks in the player's inventory and in
        /// containers within ContainerRadius. Natural stone never counts.</summary>
        public static Dictionary<string, int> Available(ICoreServerAPI sapi, IServerPlayer player, ICollection<string> codes)
        {
            var wanted = new HashSet<string>(codes.Where(c => !IsNaturalStone(c)));
            var found = new Dictionary<string, int>();

            void CountSlot(ItemSlot slot)
            {
                var stack = slot?.Itemstack;
                if (stack?.Collectible?.Code == null) return;
                string key = stack.Block != null
                    ? Normalize(stack.Block.Code)
                    : "item:" + stack.Collectible.Code;
                if (wanted.Contains(key))
                {
                    found[key] = found.GetValueOrDefault(key) + stack.StackSize;
                }
            }

            foreach (var inv in player.InventoryManager.Inventories.Values)
            {
                if (inv == null) continue;
                string cls = inv.ClassName;
                if (cls == "creative" || cls == "mouse" || cls == "craftinggrid" || cls == "ground") continue;
                foreach (var slot in inv) CountSlot(slot);
            }

            BlockPos center = player.Entity.Pos.AsBlockPos;
            var ba = sapi.World.BlockAccessor;
            var pos = new BlockPos();
            for (int x = -ContainerRadius; x <= ContainerRadius; x++)
            {
                for (int y = -ContainerRadius; y <= ContainerRadius; y++)
                {
                    for (int z = -ContainerRadius; z <= ContainerRadius; z++)
                    {
                        pos.Set(center.X + x, center.Y + y, center.Z + z);
                        if (ba.GetBlockEntity(pos) is IBlockEntityContainer container && container.Inventory != null)
                        {
                            foreach (var slot in container.Inventory) CountSlot(slot);
                        }
                    }
                }
            }

            return found;
        }

        /// <summary>Diagnostic: everything the availability scanner can see for
        /// this player, as material keys with per-source counts.</summary>
        public static string ScanReport(ICoreServerAPI sapi, IServerPlayer player, string filter)
        {
            var sb = new StringBuilder();

            var held = player.InventoryManager.ActiveHotbarSlot?.Itemstack;
            if (held?.Collectible?.Code != null)
            {
                string key = held.Block != null ? Normalize(held.Block.Code) : "item:" + held.Collectible.Code;
                sb.AppendLine($"Holding: {held.StackSize}x {held.Collectible.Code} (class {held.Class}) -> counts as '{key}'"
                    + (IsNaturalStone(key) ? " [natural stone: never counted]" : ""));
            }
            else
            {
                sb.AppendLine("Holding: nothing");
            }

            var counts = new Dictionary<string, Dictionary<string, int>>();
            void Count(ItemSlot slot, string source)
            {
                var stack = slot?.Itemstack;
                if (stack?.Collectible?.Code == null) return;
                string key = stack.Block != null ? Normalize(stack.Block.Code) : "item:" + stack.Collectible.Code;
                if (!counts.TryGetValue(key, out var bySource))
                {
                    counts[key] = bySource = new Dictionary<string, int>();
                }
                bySource[source] = bySource.GetValueOrDefault(source) + stack.StackSize;
            }

            foreach (var inv in player.InventoryManager.Inventories.Values)
            {
                if (inv == null) continue;
                string cls = inv.ClassName ?? "?";
                // don't even enumerate the non-counted classes: the creative
                // inventory is not a normal slot list server-side
                if (cls == "creative" || cls == "mouse" || cls == "craftinggrid" || cls == "ground") continue;
                foreach (var slot in inv) Count(slot, cls);
            }

            BlockPos center = player.Entity.Pos.AsBlockPos;
            var ba = sapi.World.BlockAccessor;
            var pos = new BlockPos();
            for (int x = -ContainerRadius; x <= ContainerRadius; x++)
            {
                for (int y = -ContainerRadius; y <= ContainerRadius; y++)
                {
                    for (int z = -ContainerRadius; z <= ContainerRadius; z++)
                    {
                        pos.Set(center.X + x, center.Y + y, center.Z + z);
                        if (ba.GetBlockEntity(pos) is IBlockEntityContainer container && container.Inventory != null)
                        {
                            foreach (var slot in container.Inventory) Count(slot, "containers");
                        }
                    }
                }
            }

            var keys = counts.Keys
                .Where(k => filter == null || k.Contains(filter, System.StringComparison.OrdinalIgnoreCase))
                .OrderBy(k => k);
            int shown = 0;
            foreach (var key in keys)
            {
                var srcs = string.Join(", ", counts[key].Select(kv => $"{kv.Key} {kv.Value}"));
                sb.AppendLine($"  {counts[key].Values.Sum(),6}x {key}  ({srcs})");
                if (++shown >= 40) { sb.AppendLine("  ... (more; narrow with /bw scan <filter>)"); break; }
            }
            if (shown == 0) sb.AppendLine(filter == null ? "  nothing counted" : $"  nothing matching '{filter}'");
            return sb.ToString().TrimEnd();
        }

        public static List<Entry> Compute(ICoreServerAPI sapi, IServerPlayer player, BlockSchematic schematic)
        {
            var required = Requirements(schematic);
            var available = Available(sapi, player, required.Keys.ToList());
            return required
                .OrderByDescending(kv => kv.Value.count)
                .Select(kv => new Entry
                {
                    Code = kv.Key,
                    Title = ResolveTitle(sapi, kv.Key, kv.Value.sample),
                    Required = kv.Value.count,
                    Available = available.GetValueOrDefault(kv.Key),
                    NaturalStone = IsNaturalStone(kv.Key),
                })
                .ToList();
        }

        /// <summary>Full multi-line bill for chat output.</summary>
        public static string Report(List<Entry> bill, string name)
        {
            var sb = new StringBuilder();
            int total = bill.Sum(e => e.Required);
            sb.AppendLine($"{name}: {total} blocks, {bill.Count} materials (containers scanned within {ContainerRadius} blocks):");
            foreach (var e in bill)
            {
                string status = e.NaturalStone
                    ? "natural stone - needs substitution"
                    : (e.Available >= e.Required ? "OK" : $"missing {e.Required - e.Available}");
                sb.AppendLine($"  {e.Required,6}x {e.DisplayName}  (have {e.Available}, {status})");
            }
            return sb.ToString().TrimEnd();
        }

        /// <summary>Compact summary for the dialog status area.</summary>
        public static string Summary(List<Entry> bill)
        {
            int totalReq = bill.Sum(e => e.Required);
            var missing = bill.Where(e => e.NaturalStone || e.Available < e.Required).ToList();
            if (missing.Count == 0)
            {
                return $"Materials: all {totalReq} blocks on hand.";
            }
            var lines = missing.Take(3).Select(e => e.NaturalStone
                ? $"  {e.Title ?? e.ShortCode}: natural stone"
                : $"  {e.Title ?? e.ShortCode}: {e.Required - e.Available} short");
            string more = missing.Count > 3 ? $"\n  +{missing.Count - 3} more (see /bw cost)" : "";
            return $"Materials: {missing.Count} of {bill.Count} short:\n" + string.Join("\n", lines) + more;
        }
    }
}
