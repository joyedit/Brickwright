using System.Collections.Generic;
using System.IO;
using System.Linq;
using Vintagestory.API.Client;
using Vintagestory.API.Common;
using Vintagestory.API.Config;
using Vintagestory.API.MathTools;
using Vintagestory.API.Server;

namespace Brickwright
{
    public class BrickwrightModSystem : ModSystem
    {
        ICoreServerAPI sapi;
        string schematicsFolder;
        IServerNetworkChannel serverChannel;

        ICoreClientAPI capi;
        IClientNetworkChannel clientChannel;
        GuiDialogBrickwright dialog;

        class PasteRecord
        {
            // for every position the paste touched: the block that was there
            // before, and the block the schematic placed
            public List<(BlockPos pos, int prevId, int placedId)> Blocks;
            public string Name;
            public BlockPos Anchor;   // player position the paste was centered on
            public bool Pending;      // dialog rotations replace a pending paste; Set clears this
        }

        readonly Dictionary<string, PasteRecord> lastPastes = new();

        // /bw mark selections per player (1 or 2 ground-corner positions)
        readonly Dictionary<string, List<BlockPos>> exportMarks = new();
        const int MarkHighlightSlot = 9451;
        const int MaxExportXZ = 500;
        const int MaxExportHeight = 250;

        public override void Start(ICoreAPI api)
        {
            api.Network.RegisterChannel("brickwright")
                .RegisterMessageType<BwListRequest>()
                .RegisterMessageType<BwListResponse>()
                .RegisterMessageType<BwPlaceRequest>()
                .RegisterMessageType<BwSetRequest>()
                .RegisterMessageType<BwUndoRequest>()
                .RegisterMessageType<BwStatus>()
                .RegisterMessageType<BwCostRequest>()
                .RegisterMessageType<BwCostResponse>();
        }

        // ------------------------------------------------------------------
        // Server
        // ------------------------------------------------------------------

        public override void StartServerSide(ICoreServerAPI api)
        {
            sapi = api;
            schematicsFolder = api.GetOrCreateDataPath(Path.Combine("ModConfig", "brickwright"));

            serverChannel = api.Network.GetChannel("brickwright")
                .SetMessageHandler<BwListRequest>(OnListRequest)
                .SetMessageHandler<BwPlaceRequest>(OnPlaceRequest)
                .SetMessageHandler<BwSetRequest>(OnSetRequest)
                .SetMessageHandler<BwUndoRequest>(OnUndoRequest)
                .SetMessageHandler<BwCostRequest>(OnCostRequest);

            var parsers = api.ChatCommands.Parsers;
            api.ChatCommands.Create("bw")
                .WithDescription(Lang.Get("brickwright:command-desc"))
                .RequiresPrivilege(Privilege.controlserver)
                .BeginSubCommand("list")
                    .WithDescription("List available schematics")
                    .HandleWith(OnListCommand)
                .EndSubCommand()
                .BeginSubCommand("info")
                    .WithDescription("Show size and block counts of a schematic")
                    .WithArgs(parsers.Word("name"))
                    .HandleWith(OnInfoCommand)
                .EndSubCommand()
                .BeginSubCommand("paste")
                    .WithDescription("Place a schematic ahead of you (optionally rotated 90/180/270)")
                    .WithArgs(parsers.Word("name"), parsers.OptionalInt("angle", 0))
                    .HandleWith(OnPasteCommand)
                .EndSubCommand()
                .BeginSubCommand("undo")
                    .WithDescription("Revert your last paste")
                    .HandleWith(OnUndoCommand)
                .EndSubCommand()
                .BeginSubCommand("cost")
                    .WithDescription("Show required materials vs. what you have (inventory + nearby containers)")
                    .WithArgs(parsers.Word("name"))
                    .HandleWith(OnCostCommand)
                .EndSubCommand()
                .BeginSubCommand("mark")
                    .WithDescription("Mark an export corner (aim at a ground block; two diagonal corners define the footprint, height is auto-detected)")
                    .HandleWith(OnMarkCommand)
                .EndSubCommand()
                .BeginSubCommand("unmark")
                    .WithDescription("Clear the export selection")
                    .HandleWith(OnUnmarkCommand)
                .EndSubCommand()
                .BeginSubCommand("export")
                    .WithDescription("Save the marked area as a pasteable schematic. Options: 'keepterrain' includes soil/sand, 'empty' strips container contents (for sharing)")
                    .WithArgs(parsers.Word("name"), parsers.OptionalWord("option1"), parsers.OptionalWord("option2"))
                    .HandleWith(OnExportCommand)
                .EndSubCommand()
                .BeginSubCommand("scan")
                    .WithDescription("Diagnostic: list every material the availability scanner sees")
                    .WithArgs(parsers.OptionalWord("filter"))
                    .HandleWith(OnScanCommand)
                .EndSubCommand();

            api.Logger.Notification("[Brickwright] Loaded. Schematics folder: {0}", schematicsFolder);
        }

        IEnumerable<string> SchematicNames() =>
            Directory.GetFiles(schematicsFolder, "*.json")
                .Select(Path.GetFileNameWithoutExtension)
                .OrderBy(n => n);

        BlockSchematic LoadSchematic(string name, out string failReason)
        {
            failReason = null;
            string path = Path.Combine(schematicsFolder, name + ".json");
            if (!File.Exists(path))
            {
                failReason = Lang.Get("brickwright:not-found", name);
                return null;
            }
            string error = null;
            BlockSchematic schematic = BlockSchematic.LoadFromFile(path, ref error);
            if (schematic == null)
            {
                failReason = "Could not load schematic: " + error;
            }
            return schematic;
        }

        int CountModified(PasteRecord rec)
        {
            var worldAccessor = sapi.World.BlockAccessor;
            int changed = 0;
            foreach (var (pos, _, placedId) in rec.Blocks)
            {
                if (worldAccessor.GetBlock(pos).BlockId != placedId) changed++;
            }
            return changed;
        }

        void Revert(PasteRecord rec)
        {
            var blockAccessor = sapi.World.GetBlockAccessorBulkUpdate(true, true);
            foreach (var (pos, prevId, _) in rec.Blocks)
            {
                blockAccessor.SetBlock(prevId, pos);
            }
            blockAccessor.Commit();
        }

        /// <summary>Paste a schematic. When fromDialog, an unmodified pending paste
        /// is reverted first so direction buttons rotate the building in place.</summary>
        (bool ok, string msg) Place(IServerPlayer player, string name, int angle, bool fromDialog)
        {
            if (angle % 90 != 0)
            {
                return (false, "Angle must be a multiple of 90");
            }

            var schematic = LoadSchematic(name, out string fail);
            if (schematic == null) return (false, fail);

            // anchor ahead of the player along their look direction, far enough
            // that the near face of the structure is at least 6 blocks away
            double clearance = 6 + GameMath.Max(schematic.SizeX, schematic.SizeZ) / 2.0;
            BlockPos anchor = player.Entity.ServerPos.HorizontalAheadCopy(clearance).AsBlockPos;
            anchor.Y = player.Entity.ServerPos.AsBlockPos.Y;

            if (fromDialog
                && lastPastes.TryGetValue(player.PlayerUID, out var prev)
                && prev.Pending
                && CountModified(prev) == 0)
            {
                Revert(prev);
                lastPastes.Remove(player.PlayerUID);
                if (prev.Name == name) anchor = prev.Anchor;  // rotate in place
            }

            if (angle != 0)
            {
                schematic.TransformWhilePacked(sapi.World, EnumOrigin.BottomCenter, GameMath.Mod(angle, 360));
            }

            BlockPos start = schematic.GetStartPos(anchor, EnumOrigin.BottomCenter);

            var blockAccessor = sapi.World.GetBlockAccessorBulkUpdate(true, true);
            schematic.Init(blockAccessor);

            var positions = new List<BlockPos>(schematic.Indices.Count);
            var prevIds = new List<int>(schematic.Indices.Count);
            foreach (uint packed in schematic.Indices)
            {
                int dx = (int)(packed & 0x3ff);
                int dz = (int)((packed >> 10) & 0x3ff);
                int dy = (int)((packed >> 20) & 0x3ff);
                var pos = new BlockPos(start.X + dx, start.Y + dy, start.Z + dz);
                positions.Add(pos);
                prevIds.Add(blockAccessor.GetBlock(pos).BlockId);
            }

            schematic.Place(blockAccessor, sapi.World, start, EnumReplaceMode.ReplaceAllNoAir, false);
            blockAccessor.Commit();

            // read back what actually landed, so undo/rotate can verify the
            // structure is still untouched before reverting
            var blocks = new List<(BlockPos, int, int)>(positions.Count);
            var worldAccessor = sapi.World.BlockAccessor;
            for (int i = 0; i < positions.Count; i++)
            {
                blocks.Add((positions[i], prevIds[i], worldAccessor.GetBlock(positions[i]).BlockId));
            }
            lastPastes[player.PlayerUID] = new PasteRecord
            {
                Blocks = blocks,
                Name = name,
                Anchor = anchor,
                Pending = fromDialog,
            };

            return (true, Lang.Get("brickwright:pasted",
                name, schematic.SizeX, schematic.SizeY, schematic.SizeZ, schematic.BlockIds.Count));
        }

        (bool ok, string msg) Undo(IServerPlayer player)
        {
            if (!lastPastes.TryGetValue(player.PlayerUID, out var rec))
            {
                return (false, Lang.Get("brickwright:nothing-to-undo"));
            }

            // refuse if the structure was modified since pasting: no undoing
            // after blocks have been harvested from it or built onto it
            int changed = CountModified(rec);
            if (changed > 0)
            {
                return (false, Lang.Get("brickwright:undo-modified", changed));
            }

            Revert(rec);
            lastPastes.Remove(player.PlayerUID);
            return (true, Lang.Get("brickwright:undone"));
        }

        // --- network handlers (dialog) ---

        void OnListRequest(IServerPlayer player, BwListRequest packet)
        {
            serverChannel.SendPacket(new BwListResponse { Names = SchematicNames().ToList() }, player);
        }

        void OnPlaceRequest(IServerPlayer player, BwPlaceRequest packet)
        {
            if (!player.HasPrivilege(Privilege.controlserver))
            {
                serverChannel.SendPacket(new BwStatus { Success = false, Message = "Insufficient privileges" }, player);
                return;
            }
            var (ok, msg) = Place(player, packet.Name, packet.Angle, fromDialog: true);
            serverChannel.SendPacket(new BwStatus { Success = ok, Message = msg }, player);
        }

        void OnSetRequest(IServerPlayer player, BwSetRequest packet)
        {
            if (lastPastes.TryGetValue(player.PlayerUID, out var rec) && rec.Pending)
            {
                rec.Pending = false;
                serverChannel.SendPacket(new BwStatus { Success = true, Message = Lang.Get("brickwright:set") }, player);
            }
            else
            {
                serverChannel.SendPacket(new BwStatus { Success = false, Message = Lang.Get("brickwright:nothing-pending") }, player);
            }
        }

        void OnUndoRequest(IServerPlayer player, BwUndoRequest packet)
        {
            if (!player.HasPrivilege(Privilege.controlserver))
            {
                serverChannel.SendPacket(new BwStatus { Success = false, Message = "Insufficient privileges" }, player);
                return;
            }
            var (ok, msg) = Undo(player);
            serverChannel.SendPacket(new BwStatus { Success = ok, Message = msg }, player);
        }

        void OnCostRequest(IServerPlayer player, BwCostRequest packet)
        {
            var schematic = LoadSchematic(packet.Name, out string fail);
            if (schematic == null)
            {
                serverChannel.SendPacket(new BwCostResponse { Summary = fail }, player);
                return;
            }
            var bill = MaterialBill.Compute(sapi, player, schematic);
            serverChannel.SendPacket(new BwCostResponse { Summary = MaterialBill.Summary(bill) }, player);
        }

        TextCommandResult OnCostCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only check cost as an ingame player");
            }
            string name = (string)args[0];
            var schematic = LoadSchematic(name, out string fail);
            if (schematic == null) return TextCommandResult.Error(fail);

            var bill = MaterialBill.Compute(sapi, player, schematic);
            return TextCommandResult.Success(MaterialBill.Report(bill, name));
        }

        TextCommandResult OnScanCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only scan as an ingame player");
            }
            return TextCommandResult.Success(MaterialBill.ScanReport(sapi, player, args[0] as string));
        }

        // --- export selection (/bw mark, unmark, export) ---

        /// <summary>Bounds of the selection: marked footprint, top auto-detected
        /// from the rain height map (highest block that stops rain).</summary>
        (BlockPos min, BlockPos max) SelectionBounds(List<BlockPos> mk)
        {
            var min = new BlockPos(GameMath.Min(mk[0].X, mk[1].X), GameMath.Min(mk[0].Y, mk[1].Y),
                                   GameMath.Min(mk[0].Z, mk[1].Z));
            var max = new BlockPos(GameMath.Max(mk[0].X, mk[1].X), min.Y,
                                   GameMath.Max(mk[0].Z, mk[1].Z));
            var ba = sapi.World.BlockAccessor;
            for (int x = min.X; x <= max.X; x++)
            {
                for (int z = min.Z; z <= max.Z; z++)
                {
                    max.Y = GameMath.Max(max.Y, ba.GetRainMapHeightAt(x, z));
                }
            }
            max.Y = GameMath.Min(max.Y, min.Y + MaxExportHeight - 1);
            return (min, max);
        }

        void SetHighlightCube(IServerPlayer player, int slot, BlockPos a, BlockPos b, int color)
        {
            var cube = a == null ? new List<BlockPos>() : new List<BlockPos> { a, b };
            sapi.World.HighlightBlocks(player, slot, cube, new List<int> { color },
                EnumHighlightBlocksMode.Absolute, EnumHighlightShape.Cube);
        }

        /// <summary>Each corner shows as a glowing 5-high pillar ("flag"); with
        /// both corners set, the full to-be-exported box is shown too.</summary>
        void UpdateMarkHighlight(IServerPlayer player)
        {
            var mk = exportMarks.GetValueOrDefault(player.PlayerUID);
            int pillar1 = ColorUtil.ColorFromRgba(60, 230, 120, 160);
            int pillar2 = ColorUtil.ColorFromRgba(230, 180, 40, 160);
            int boxCol = ColorUtil.ColorFromRgba(60, 220, 140, 70);

            SetHighlightCube(player, MarkHighlightSlot,
                mk?.Count >= 1 ? mk[0] : null, mk?.Count >= 1 ? mk[0].AddCopy(1, 5, 1) : null, pillar1);
            SetHighlightCube(player, MarkHighlightSlot + 1,
                mk?.Count >= 2 ? mk[1] : null, mk?.Count >= 2 ? mk[1].AddCopy(1, 5, 1) : null, pillar2);
            BlockPos bmin = null, bmax = null;
            if (mk?.Count >= 2)
            {
                (bmin, bmax) = SelectionBounds(mk);
                bmax = bmax.AddCopy(1, 1, 1);
            }
            SetHighlightCube(player, MarkHighlightSlot + 2, bmin, bmax, boxCol);
        }

        static readonly string[] TerrainPrefixes = {
            "soil-", "forestfloor", "tallgrass-", "sand-", "gravel-",
            "muddygravel", "peat-", "rawclay-", "snowlayer-"
        };

        /// <summary>Remove natural ground (captured hillside slices) from an
        /// exported schematic, then re-seat the build at y=0.</summary>
        void StripTerrain(BlockSchematic s)
        {
            var terrainIds = new HashSet<int>();
            foreach (var (id, code) in s.BlockCodes)
            {
                string path = code.Path;
                foreach (string pre in TerrainPrefixes)
                {
                    if (path.StartsWith(pre)) { terrainIds.Add(id); break; }
                }
            }
            if (terrainIds.Count == 0) return;

            var indices = new List<uint>(s.Indices.Count);
            var ids = new List<int>(s.BlockIds.Count);
            var removed = new HashSet<uint>();
            int minY = int.MaxValue, maxY = 0;
            for (int i = 0; i < s.Indices.Count; i++)
            {
                if (terrainIds.Contains(s.BlockIds[i])) { removed.Add(s.Indices[i]); continue; }
                indices.Add(s.Indices[i]);
                ids.Add(s.BlockIds[i]);
                int y = (int)(s.Indices[i] >> 20) & 0x3ff;
                minY = GameMath.Min(minY, y);
                maxY = GameMath.Max(maxY, y);
            }
            if (indices.Count == 0 || minY == int.MaxValue) return;

            uint Shift(uint packed) =>
                (uint)((((packed >> 20) & 0x3ff) - minY) << 20) | (packed & 0xfffff);

            for (int i = 0; i < indices.Count; i++) indices[i] = Shift(indices[i]);
            s.Indices = indices;
            s.BlockIds = ids;
            s.SizeY = maxY - minY + 1;

            var decorIdx = new List<uint>(); var decorIds = new List<long>();
            for (int i = 0; i < (s.DecorIndices?.Count ?? 0); i++)
            {
                if (removed.Contains(s.DecorIndices[i])) continue;
                decorIdx.Add(Shift(s.DecorIndices[i]));
                decorIds.Add(s.DecorIds[i]);
            }
            if (s.DecorIndices != null) { s.DecorIndices = decorIdx; s.DecorIds = decorIds; }

            var bes = new Dictionary<uint, string>();
            foreach (var kv in s.BlockEntities)
            {
                if (!removed.Contains(kv.Key)) bes[Shift(kv.Key)] = kv.Value;
            }
            s.BlockEntities.Clear();
            foreach (var kv in bes) s.BlockEntities[kv.Key] = kv.Value;
        }

        /// <summary>Drop only block entities that carry an inventory (chests,
        /// crates...) so containers paste fresh and empty; chisel shapes, sign
        /// text and door state stay intact.</summary>
        void StripInventories(BlockSchematic s)
        {
            var drop = new List<uint>();
            foreach (var kv in s.BlockEntities)
            {
                var tree = s.DecodeBlockEntityData(kv.Value);
                if (tree != null && tree.HasAttribute("inventory")) drop.Add(kv.Key);
            }
            foreach (uint k in drop) s.BlockEntities.Remove(k);
        }

        TextCommandResult OnMarkCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only mark as an ingame player");
            }
            BlockPos pos = player.CurrentBlockSelection?.Position
                ?? player.Entity.Pos.AsBlockPos.DownCopy();

            var mk = exportMarks.GetValueOrDefault(player.PlayerUID);
            if (mk == null || mk.Count >= 2)
            {
                exportMarks[player.PlayerUID] = new List<BlockPos> { pos };
                UpdateMarkHighlight(player);
                return TextCommandResult.Success(
                    $"Corner 1 marked at {pos} (highlighted). Now aim at the diagonally opposite ground corner and /bw mark again.");
            }

            mk.Add(pos);
            var (min, max) = SelectionBounds(mk);
            UpdateMarkHighlight(player);
            return TextCommandResult.Success(
                $"Corner 2 marked. Selection {max.X - min.X + 1}x{max.Z - min.Z + 1}, " +
                $"top auto-detected at y={max.Y} (see highlight box). Save it with: /bw export <name>");
        }

        TextCommandResult OnUnmarkCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only unmark as an ingame player");
            }
            exportMarks.Remove(player.PlayerUID);
            UpdateMarkHighlight(player);
            return TextCommandResult.Success("Export selection cleared.");
        }

        TextCommandResult OnExportCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only export as an ingame player");
            }
            var mk = exportMarks.GetValueOrDefault(player.PlayerUID);
            if (mk == null || mk.Count < 2)
            {
                return TextCommandResult.Error(
                    "Mark two diagonal ground corners first with /bw mark (aim at a block).");
            }
            string name = ((string)args[0]).ToLowerInvariant();
            foreach (char c in name)
            {
                if (!char.IsLetterOrDigit(c) && c != '-' && c != '_')
                {
                    return TextCommandResult.Error("Name may only contain letters, digits, - and _");
                }
            }

            var (min, max) = SelectionBounds(mk);
            if (max.X - min.X + 1 > MaxExportXZ || max.Z - min.Z + 1 > MaxExportXZ)
            {
                return TextCommandResult.Error($"Selection exceeds {MaxExportXZ} blocks per side.");
            }

            var options = new HashSet<string> { args[1] as string, args[2] as string };
            foreach (string opt in options)
            {
                if (opt != null && opt != "keepterrain" && opt != "empty")
                {
                    return TextCommandResult.Error($"Unknown option '{opt}' - valid: keepterrain, empty");
                }
            }

            var schematic = new BlockSchematic();
            schematic.AddArea(sapi.World, min, max.AddCopy(1, 1, 1));
            schematic.Pack(sapi.World, min);
            if (!options.Contains("keepterrain")) StripTerrain(schematic);
            // containers copy with their contents by default; 'empty' strips
            // them for sharing (chests then paste fresh and empty)
            if (options.Contains("empty")) StripInventories(schematic);
            schematic.Entities.Clear();

            string path = Path.Combine(schematicsFolder, name + ".json");
            File.WriteAllText(path, schematic.ToJson());

            exportMarks.Remove(player.PlayerUID);
            UpdateMarkHighlight(player);
            return TextCommandResult.Success(
                $"Exported '{name}': {schematic.SizeX}x{schematic.SizeY}x{schematic.SizeZ}, " +
                $"{schematic.BlockIds.Count} blocks. It is now pasteable (/bw paste {name}) and " +
                $"shareable - the file is at {path}");
        }

        // --- chat command handlers ---

        TextCommandResult OnListCommand(TextCommandCallingArgs args)
        {
            var names = SchematicNames().ToList();
            if (names.Count == 0)
            {
                return TextCommandResult.Success(Lang.Get("brickwright:no-schematics", schematicsFolder));
            }
            return TextCommandResult.Success(string.Join("\n", names.Select(n => "  " + n)));
        }

        TextCommandResult OnInfoCommand(TextCommandCallingArgs args)
        {
            string name = (string)args[0];
            var schematic = LoadSchematic(name, out string fail);
            if (schematic == null) return TextCommandResult.Error(fail);

            var counts = new Dictionary<string, int>();
            foreach (int id in schematic.BlockIds)
            {
                string code = schematic.BlockCodes.TryGetValue(id, out var loc) ? loc.ToString() : "?";
                counts[code] = counts.GetValueOrDefault(code) + 1;
            }
            var lines = counts.OrderByDescending(kv => kv.Value).Select(kv => $"  {kv.Value,6}  {kv.Key}");
            return TextCommandResult.Success(
                $"{name}: {schematic.SizeX}x{schematic.SizeY}x{schematic.SizeZ}, {schematic.BlockIds.Count} blocks\n" +
                string.Join("\n", lines));
        }

        TextCommandResult OnPasteCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only paste as an ingame player");
            }
            var (ok, msg) = Place(player, (string)args[0], (int)args[1], fromDialog: false);
            return ok ? TextCommandResult.Success(msg) : TextCommandResult.Error(msg);
        }

        TextCommandResult OnUndoCommand(TextCommandCallingArgs args)
        {
            if (args.Caller.Player is not IServerPlayer player)
            {
                return TextCommandResult.Error("Can only undo as an ingame player");
            }
            var (ok, msg) = Undo(player);
            return ok ? TextCommandResult.Success(msg) : TextCommandResult.Error(msg);
        }

        // ------------------------------------------------------------------
        // Client
        // ------------------------------------------------------------------

        public override void StartClientSide(ICoreClientAPI api)
        {
            capi = api;
            clientChannel = api.Network.GetChannel("brickwright")
                .SetMessageHandler<BwListResponse>(OnListResponse)
                .SetMessageHandler<BwStatus>(OnStatus)
                .SetMessageHandler<BwCostResponse>(OnCostResponse);

            api.Input.RegisterHotKey("brickwrightdialog", Lang.Get("brickwright:hotkey-name"),
                GlKeys.B, HotkeyType.GUIOrOtherControls, ctrlPressed: true);
            api.Input.SetHotKeyHandler("brickwrightdialog", OnHotkey);

            dialog = new GuiDialogBrickwright(api, this);
        }

        bool OnHotkey(KeyCombination comb)
        {
            if (dialog.IsOpened()) dialog.TryClose();
            else dialog.TryOpen();
            return true;
        }

        public void RequestList() => clientChannel.SendPacket(new BwListRequest());
        public void RequestCost(string name) => clientChannel.SendPacket(new BwCostRequest { Name = name });
        public void SendPlace(string name, int angle) => clientChannel.SendPacket(new BwPlaceRequest { Name = name, Angle = angle });
        public void SendSet() => clientChannel.SendPacket(new BwSetRequest());
        public void SendUndo() => clientChannel.SendPacket(new BwUndoRequest());

        void OnListResponse(BwListResponse msg) => dialog.SetSchematics(msg.Names);
        void OnStatus(BwStatus msg) => dialog.SetStatus(msg.Message);
        void OnCostResponse(BwCostResponse msg) => dialog.SetCost(msg.Summary);
    }
}
