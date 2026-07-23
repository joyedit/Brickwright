using System.Collections.Generic;
using System.Linq;
using Vintagestory.API.Client;

namespace Brickwright
{
    /// <summary>
    /// Placement dialog: filterable schematic list, compass-cross direction
    /// buttons (each places/rotates the pending paste), Set to commit, Undo
    /// to revert. Opened with the brickwrightdialog hotkey (default Ctrl+B).
    /// </summary>
    public class GuiDialogBrickwright : GuiDialog
    {
        const int RowHeight = 25;
        const int ListHeight = 200;
        const int ListWidth = 240;

        readonly BrickwrightModSystem modsys;
        List<string> names = new();
        List<string> filtered = new();
        string selected;
        string filter = "";
        bool ignoreSearchChange;
        List<ElementBounds> rowBounds = new();

        public GuiDialogBrickwright(ICoreClientAPI capi, BrickwrightModSystem modsys) : base(capi)
        {
            this.modsys = modsys;
            Compose();
        }

        public override string ToggleKeyCombinationCode => "brickwrightdialog";

        void Compose(bool focusSearch = false)
        {
            filtered = names
                .Where(n => n.Contains(filter, System.StringComparison.OrdinalIgnoreCase))
                .ToList();

            ElementBounds dialogBounds = ElementStdBounds.AutosizedMainDialog
                .WithAlignment(EnumDialogArea.RightMiddle)
                .WithFixedAlignmentOffset(-GuiStyle.DialogToScreenPadding, 0);

            ElementBounds bgBounds = ElementBounds.Fill.WithFixedPadding(GuiStyle.ElementToDialogPadding);
            bgBounds.BothSizing = ElementSizing.FitToChildren;

            ElementBounds searchBounds = ElementBounds.Fixed(0, 30, ListWidth + 20, 30);
            ElementBounds insetBounds = ElementBounds.Fixed(0, 68, ListWidth, ListHeight);
            ElementBounds clipBounds = insetBounds.ForkContainingChild(3, 3, 3, 3);
            ElementBounds scrollbarBounds = ElementBounds.Fixed(ListWidth + 4, 68, 16, ListHeight);

            ElementBounds faceLabelBounds = ElementBounds.Fixed(0, 276, ListWidth + 20, 20);
            ElementBounds northBounds = ElementBounds.Fixed(105, 298, 50, 30);
            ElementBounds westBounds = ElementBounds.Fixed(45, 338, 50, 30);
            ElementBounds setBounds = ElementBounds.Fixed(105, 338, 50, 30);
            ElementBounds eastBounds = ElementBounds.Fixed(165, 338, 50, 30);
            ElementBounds southBounds = ElementBounds.Fixed(105, 378, 50, 30);
            ElementBounds undoBounds = ElementBounds.Fixed(80, 423, 100, 30);
            ElementBounds costBounds = ElementBounds.Fixed(0, 463, ListWidth + 20, 80);
            ElementBounds statusBounds = ElementBounds.Fixed(0, 548, ListWidth + 20, 45);

            if (selected == null || !filtered.Contains(selected))
            {
                selected = filtered.Count > 0 ? filtered[0] : null;
            }

            var composer = capi.Gui.CreateCompo("brickwrightdialog", dialogBounds)
                .AddShadedDialogBG(bgBounds)
                .AddDialogTitleBar("Brickwright", () => TryClose())
                .BeginChildElements(bgBounds)
                    .AddTextInput(searchBounds, OnSearchChanged, CairoFont.WhiteSmallishText(), "search")
                    .AddInset(insetBounds, 3)
                    .BeginClip(clipBounds);

            rowBounds = new List<ElementBounds>(filtered.Count);
            for (int i = 0; i < filtered.Count; i++)
            {
                var rb = ElementBounds.Fixed(0, i * RowHeight, ListWidth - 10, RowHeight - 2);
                rowBounds.Add(rb);
                int idx = i;
                composer.AddToggleButton(filtered[i], CairoFont.WhiteSmallText(),
                    on => OnRowClick(idx, on), rb, "row-" + i);
            }

            SingleComposer = composer
                    .EndClip()
                    .AddVerticalScrollbar(OnScroll, scrollbarBounds, "scrollbar")
                    .AddStaticText("Place it facing:", CairoFont.WhiteSmallText(), faceLabelBounds)
                    .AddSmallButton("N", () => OnDirection(0), northBounds)
                    .AddSmallButton("W", () => OnDirection(270), westBounds)
                    .AddSmallButton("Set", OnSet, setBounds)
                    .AddSmallButton("E", () => OnDirection(90), eastBounds)
                    .AddSmallButton("S", () => OnDirection(180), southBounds)
                    .AddSmallButton("Undo", OnUndo, undoBounds)
                    .AddDynamicText("", CairoFont.WhiteDetailText(), costBounds, "cost")
                    .AddDynamicText("", CairoFont.WhiteSmallText(), statusBounds, "status")
                .EndChildElements()
                .Compose();

            ignoreSearchChange = true;
            SingleComposer.GetTextInput("search").SetValue(filter);
            ignoreSearchChange = false;

            SingleComposer.GetScrollbar("scrollbar").SetHeights(
                ListHeight - 6,
                System.Math.Max(ListHeight - 6, filtered.Count * RowHeight));

            int selIdx = selected != null ? filtered.IndexOf(selected) : -1;
            if (selIdx >= 0)
            {
                SingleComposer.GetToggleButton("row-" + selIdx).SetValue(true);
            }

            if (focusSearch)
            {
                SingleComposer.FocusElement(SingleComposer.GetTextInput("search").TabIndex);
            }
        }

        public override void OnGuiOpened()
        {
            base.OnGuiOpened();
            modsys.RequestList();
        }

        public void SetSchematics(List<string> newNames)
        {
            names = newNames ?? new List<string>();
            if (selected == null || !names.Contains(selected))
            {
                selected = null;
            }
            Compose();
            if (names.Count == 0)
            {
                SetStatus("No schematics found. Convert one with ldr2vs.py and drop it in ModConfig/brickwright.");
            }
            else if (selected != null)
            {
                modsys.RequestCost(selected);
                SetStatus("Click N/E/S/W to place a preview facing that way, Set to keep it.");
            }
        }

        public void SetStatus(string message)
        {
            SingleComposer?.GetDynamicText("status")?.SetNewText(message);
        }

        public void SetCost(string summary)
        {
            SingleComposer?.GetDynamicText("cost")?.SetNewText(summary);
        }

        void OnSearchChanged(string text)
        {
            if (ignoreSearchChange) return;
            filter = text ?? "";
            Compose(focusSearch: true);
            if (selected != null) modsys.RequestCost(selected);
        }

        void OnScroll(float value)
        {
            for (int i = 0; i < rowBounds.Count; i++)
            {
                rowBounds[i].fixedY = i * RowHeight - value;
                rowBounds[i].CalcWorldBounds();
            }
        }

        void OnRowClick(int idx, bool on)
        {
            if (!on)
            {
                // clicking the selected row again: keep it selected
                SingleComposer.GetToggleButton("row-" + idx).SetValue(true);
                return;
            }
            for (int i = 0; i < filtered.Count; i++)
            {
                if (i != idx) SingleComposer.GetToggleButton("row-" + i).SetValue(false);
            }
            selected = filtered[idx];
            modsys.RequestCost(selected);
            SetStatus("Click N/E/S/W to place a preview facing that way, Set to keep it.");
        }

        bool OnDirection(int angle)
        {
            if (string.IsNullOrEmpty(selected))
            {
                SetStatus("Pick a schematic first.");
                return true;
            }
            modsys.SendPlace(selected, angle);
            return true;
        }

        bool OnSet()
        {
            modsys.SendSet();
            return true;
        }

        bool OnUndo()
        {
            modsys.SendUndo();
            return true;
        }
    }
}
