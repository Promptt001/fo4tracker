# Contributing

Thanks for helping! Bug reports, new decision entries and code are all welcome.

## Reporting a problem

Please open an issue with:

- What you did, what you expected and what happened instead
- Your OS, Python version (`python3 --version`) and `fo4tracker --version`
- Your game version (shown in the window header) and whether you use mods or F4SE
- The text from **Load order ▸ Parser warnings**, if there is any

A save file that reproduces the problem helps a lot. It contains only your game progress, but only
share it if you're comfortable doing so.

## Development setup

No dependencies are needed beyond Python 3.9+ (and Tkinter for the window).

```bash
python3 -m fo4tracker --help
python3 -m unittest discover -s tests              # all tests
xvfb-run -a python3 -m unittest discover -s tests  # include the GUI smoke test on headless Linux
pip install ruff && ruff check .                   # lint (configured in pyproject.toml)
```

### Tests

- Never commit game files (plugins, archives, string tables) or real saves. `tests/fixtures.py`
  builds tiny synthetic equivalents byte by byte. Extend the builders there when you need new cases.
- `fixtures.standard_save()` and `fixtures.build_data_dir()` describe a mid-game scenario that most
  tests share. `fixtures.IsolatedDirs` keeps tests away from your real config and cache.
- To check against a real save locally:
  `FO4_TEST_SAVE=/path/to/save.fos FO4_TEST_DATA="/path/to/Fallout 4/Data" python3 -m unittest discover -s tests`

### Code style

- Standard library only in the `fo4tracker` package. Optional extras must degrade gracefully.
- The tool must stay read-only with respect to saves and game files.
- Prefer degrading (a warning plus partial data) to raising when a save or plugin is unusual.
- If you change what is cached from plugins, bump `PARSER_VERSION` in `gamedata.py`.
- Keep user-facing text plain and specific. Use `analysis.plural()` for counts.

## Adding key decisions

Edit `fo4tracker/decisions.json` (format: [docs/decisions.md](docs/decisions.md)). Please:

- Use the quest's editor ID exactly as in the game data
- Keep `about` short, factual and spoiler-aware (describe the choice, not every outcome)
- Fill `after` where it's clear which quest leads in, so the entry can show as "coming up"
- Run the tests. `test_report.KnowledgeTests` validates the file

## Pull requests

Keep changes focused, add or update tests, and add a line to `CHANGELOG.md` under an
"Unreleased" heading.

## Releasing

1. Update `__version__` in `fo4tracker/__init__.py`.
2. Move the "Unreleased" changelog entries under the new version and date.
3. Tag `vX.Y.Z` and create a GitHub release.
