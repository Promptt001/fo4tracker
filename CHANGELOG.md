# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-14

First public release.

### Features
- Desktop window (Tkinter) with Summary, Questlines, Decisions, Inventory, Stats and Load order tabs.
- Command line: `--text`, `--html`, `--latest`, `--data`, `--no-cache`, `--version`.
- Standalone HTML report with the save's screenshot, section navigation and print support.
- Reads quest names, objectives and journal entries from `Fallout4.esm`, the DLC and Creation Club
  plugins, including localized string tables inside `.ba2` archives.
- Inventory with custom item names, attached mods and equipped state.
- A starter list of key story decisions that you can extend with your own `decisions.json`.
- Automatic discovery of Steam libraries (native, Debian, Flatpak, Snap, Proton), save folders and the
  game's Data folder.

### Hardening (audit before release)
- Truncated or damaged saves now raise a clear "damaged or truncated" error instead of a traceback.
  Damage late in the change forms keeps the data read before it and adds a warning.
- The binary reader never reads past its buffer, even when a length field lies.
- The game data cache is JSON (it was pickle). Writes are atomic, damaged entries are rebuilt, and
  stale entries are pruned.
- Data folders whose path contains glob characters (for example `[GOG] Fallout 4`) now work.
- A broken entry in your `decisions.json` is skipped and reported in the Notes, instead of silently
  discarding the whole file.

### GUI polish
- Opening a second save while the first is still loading always shows the second one.
- Friendly error dialogs (with the technical detail behind a "details" section).
- Export failures are reported. Export file names are sanitised and include the save number.
- New: **View report in browser** (Ctrl+B), **Help ▸ About**, **Help ▸ Custom decisions file…**.
- Clicking a questline shows its summary. Sortable columns show ▲/▼ and also sort within questlines.
- The window size is remembered, the title shows the open save, and shortcuts work with Caps Lock on.
- Fonts derive from the system default font, so they scale with desktop settings.

### Report polish
- Correct singular and plural forms ("1 main quest", "1 plugin is missing").
- Leading zero units are dropped from playtime ("3 hours, 5 minutes").
- Thousands separators in the text report. The HTML header shows caps and a fallback when the save date is unknown.

### Command line
- Clear errors and exit code 1 for a missing or unreadable save or Data folder, or an unwritable output.
- Install hints when Tkinter is missing.
- Text output no longer crashes when redirected to a legacy Windows code page.

### Tests and tooling
- The tests now build complete synthetic `.fos` saves and F4SE co-saves, so the whole pipeline
  (parser → report → HTML/text → CLI → GUI) is tested without game files.
- The suite grew from 10 to 64 tests. A GUI smoke test runs under Xvfb.
- `pyproject.toml` with `fo4tracker` and `fo4tracker-gui` entry points, ruff configuration and GitHub Actions CI.
