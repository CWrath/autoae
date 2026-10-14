# Auto AE UI theme

Every window is styled by three files in this folder:

| File | What it does |
|---|---|
| `autoae_theme.json` | CustomTkinter theme. Colours for every CTk widget in light and dark. |
| `theme.py` | Colour tokens, type scale, icons, and shared widgets (below). |
| `assets/` | Lucide icons (ISC licence, see `assets/icons/LICENSE-lucide.txt`) and the AE app icon. |

`main.py` calls `theme.apply()` before the window exists. **Tools never call `ctk.set_appearance_mode()` or
`ctk.set_default_color_theme()`**. Either one reloads a stock theme for every window opened afterwards.

The originals from before the redesign are in `_backup_before_redesign/`.

## Adding a tool

Copy `tool_template.py` into `tools/`. It already has the header, action bar, cards and a log. Set:

```python
self.category = "Media"   # Project Setup | Media | Editorial & Turnover | Storage & Backup
self.icon = "film"        # any PNG name in assets/icons
```

## Building blocks

```python
import theme
from theme import C

page = theme.page(window)                                   # padded, transparent content area
theme.tool_header(page, self).pack(fill="x", pady=(0, 18))  # icon tile + name + description
actions = theme.action_bar(page)                            # pack this before any expanding card
card = theme.Card(page, title="Sources", subtitle="optional helper line")   # content goes in card.body
field = theme.PathField(card.body, "Source media", "placeholder", command=browse)  # field.entry
theme.form_row(card.body, 0, "Resolution", ctk.CTkOptionMenu(card.body, values=[...]))
theme.Button(actions, "Run", kind="primary")                # primary | secondary | ghost | danger
theme.LogBox(parent)            # log; accepts insert(tk.END, text) / delete(0, tk.END) and colours
                                # "Copied:", "Missing:", "Error"..., "[3/10]" counters and timestamps
theme.ThemedListbox(parent)     # tk.Listbox that follows light/dark (text_role="warning" to tint rows)
theme.StatusLabel(parent, rules=[("Watching", "success")])  # colour follows the text you set
theme.ProgressBar(parent, percent_label=label)              # drives a "42%" label, no dot at 0%
theme.style_ttk(window)         # ttk.Treeview / Scrollbar / Notebook to match the theme
theme.add_scrollbar(widget, master, row, column)            # CTk scrollbar for a gridded Listbox/Treeview
theme.muted(parent, "hint"), theme.overline(parent, "LABEL"), theme.divider(parent)
theme.font("display" | "heading" | "body_strong" | "body" | "caption" | "overline" | "mono")
theme.sized_font(28, "bold", mono=True)                     # one-off sizes such as the timecode readout
```

Rules of thumb:
- One `primary` button per window, far right. Use `secondary` for Browse/Add/Save, `ghost` for Clear/Cancel,
  and `danger` for Stop/Delete/Remove.
- No hex colours or `("Helvetica", n)` in tools. Use `C["..."]` (each is a light/dark pair) and `theme.font(role)`.
- Frames that only lay things out get `fg_color="transparent"`; anything that should look like a box is a `theme.Card`.

## Customising

- **Accent colour:** `accent*` tokens in `theme.py` plus the matching entries in `autoae_theme.json`.
- **Font:** default is Roboto (ships with CustomTkinter). For a native Windows look, set
  `"CTkFont" → "Windows" → "family"` to `"Segoe UI"` in `autoae_theme.json`.
- **New icons:** save any Lucide icon (lucide.dev) as a 64×64 PNG with a transparent background in
  `assets/icons/`. Only the transparency is used; colour is applied in code.
- **Packaging with PyInstaller:** add the data files, e.g.
  `--add-data "autoae_theme.json;." --add-data "assets;assets"`. Without them the app falls back to the stock theme
  and blank icons instead of crashing.

## Notes

- Theme choice (System/Light/Dark) is saved to `autoae_settings.json` next to `main.py`.

## Mac version

`python build_macos_autoae.py` builds `dist/AutoAE-macos.zip` from this folder (details in `macos/README.md`).
To keep a tool working on both:

- Open files and folders with `os.startfile` on Windows and `subprocess.Popen(["open", path])` on a Mac.
- Bind shortcuts to `<Command-…>` on a Mac and `<Control-…>` elsewhere, as `main.py` does for search.
- Put data a tool saves next to its code or in the app folder. On a Mac the app runs from
  `~/Library/Application Support/AUTO_AE/app`, which is writable; the `.app` itself may not be.
- New packages go in `macos/requirements-macos.txt` as well as your Windows environment.
