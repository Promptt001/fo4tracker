# Troubleshooting

### "the window needs Tkinter, which isn't installed"

Install Tkinter for your Python (`sudo apt install python3-tk` on Debian/Ubuntu,
`sudo dnf install python3-tkinter` on Fedora, `sudo pacman -S tk` on Arch). The `--text` and `--html`
options work without it.

### "Latest save" can't find my saves

The tool looks in each Steam library's Proton prefix and in `Documents/My Games/Fallout4/Saves`.
If your saves live elsewhere (another Wine prefix, a custom `sLocalSavePath` in `Fallout4Custom.ini`,
or a mod manager profile), use **Open save…** once. The tool remembers that folder for the next time.

### Quest names show as "Unidentified quest (plugin:ID)" or objectives as "Objective 200"

The tool doesn't have the game data for that quest:

- Check that **Game data** in the toolbar points at the folder that contains `Fallout4.esm`.
- If the quest belongs to a mod, the mod's plugin has to be in that `Data` folder. Mod-manager
  virtual filesystems (MO2) aren't visible to the tool. Point it at the mod's real files, or
  live with the IDs. The **Load order** tab lists every plugin it couldn't find.
- After a game or mod update, use **File ▸ Clear game data cache**.

### Names are missing even though the Data folder is set

The string tables (`Strings/Fallout4_en.STRINGS` and the others) normally live inside
`Fallout4 - Interface.ba2`. If you play in another language, the tool currently reads only `_en`
tables. Loose files in `Data/Strings/` take priority over the archives.

### "some item details used an unrecognised format and were skipped"

The inventory contains an extra-data type the parser doesn't know yet. The item and its count are
still listed. Only details such as a custom name or attached mods may be missing. Please open an issue
and say which item it is. A save that reproduces it helps a lot, if you're willing to share one.

### "The save looks damaged or truncated"

The file ended early or contains inconsistent offsets. This usually means the copy is incomplete
(cloud sync in progress, or the game was still writing it). Try again in a moment, or try an older save.

### The first load is slow

The first time the tool sees a plugin, it reads the whole file, and `Fallout4.esm` is large. After
that it uses the cache in `~/.cache/fo4tracker` (or `%LOCALAPPDATA%\fo4tracker`) and loads in about a second.

### Where are my settings?

`~/.config/fo4tracker/config.json` (`%APPDATA%\fo4tracker\config.json` on Windows) stores the
Data folder, the last save and export folders, and the window size. Deleting it resets everything.
