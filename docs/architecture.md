# Architecture

Fallout 4 Save Tracker is a small, dependency-free Python package. Data flows in one direction:

```
 .fos save ──► savefile.py ──► SaveGame ─┐
 (.f4se)                                 │
                                         ├──► analysis.Report ──► gui.py      (Tkinter window)
 Data folder ─► gamedata.py ─► GameData ─┘                     └─► report.py  (HTML / text)
 (.esm/.esp/.esl, .STRINGS, .ba2)            ▲
                                             │
             decisions.json, core_quests.json (bundled knowledge)
```

## Modules

| Module | Responsibility |
|---|---|
| `binutil.py` | `Reader`, a bounds-checked cursor over little-endian binary data (`u8`…`u64`, `f32`, length-prefixed strings, Bethesda `vsval` and 3-byte RefIDs). Every out-of-range read raises `ParseError` |
| `savefile.py` | Decodes `.fos` saves: header, screenshot, plugin lists, misc stats, globals, quest change forms and the player's inventory. Also reads the F4SE co-save (`.f4se`) if one sits next to the save |
| `gamedata.py` | Locates Steam libraries, save folders and the game's `Data` folder. Reads plugins (`QUST`, `GLOB` and item records), localized string tables and BA2 archives. Caches one JSON index per plugin |
| `analysis.py` | Combines a `SaveGame` with optional `GameData` into a `Report`: quest views with status, questline grouping, decisions, inventory rows and the executive summary |
| `report.py` | Renders a `Report` as a standalone HTML page (with the save's screenshot embedded as a PNG) or as plain text |
| `gui.py` | The Tkinter window. Loads saves on a worker thread and passes results back through a queue |
| `__main__.py` | Command-line entry point (`python -m fo4tracker`, or the `fo4tracker` script) |
| `decisions.json` | Built-in list of key story decisions, matched by quest editor ID |
| `core_quests.json` | Names of about 120 `Fallout4.esm` quests, used when the Data folder isn't available |

## Design principles

- **Read-only.** Nothing ever writes to the save or the game folder. The tool writes only to its own
  cache (`~/.cache/fo4tracker`) and config (`~/.config/fo4tracker`) folders.
- **Degrade, don't crash.** A damaged section of a save adds a warning and the rest is still
  shown. An unknown inventory extra-data type triggers a resynchronising search, and the item
  is reported without that detail. A plugin that can't be read is skipped and listed.
- **Standard library only.** It runs on a stock Python install. Drag and drop (`tkinterdnd2`) is optional.
- **No game files in the repo or the tests.** The test suite synthesises plugins, string tables,
  BA2 archives and saves byte by byte (`tests/fixtures.py`).

## Identifiers

Three kinds of identifier appear throughout the code:

- **Save RefID**: a 3-byte value in the save. The top 2 bits select how to resolve it: an index into
  the save's form ID array, a `Fallout4.esm` form, or a form created at runtime.
  `SaveGame.resolve_refid` turns it into a load-order form ID.
- **Load-order form ID**: a 32-bit value whose top byte is the plugin's index in the save's
  load order (`0xFE` = light plugin, followed by a 12-bit light index). `SaveGame.plugin_key` turns
  it into `(plugin filename, local id)`.
- **Key**: `(plugin filename lowercased, local id)`. This is what `GameData` is indexed by, so a save
  and the game data can be matched even when the load order differs between them.

## Threading (GUI)

`App.open_save` starts a daemon thread that parses the save, loads (or reuses) `GameData` and
builds the `Report`. Messages go back to the Tk thread through a `queue.Queue`, which is polled every
100 ms. Each load gets an increasing ID, and results from a superseded load are dropped. This
way, opening two saves in quick succession always shows the second one. `GameData` instances are
cached per (Data folder, load order) and guarded by a lock.
