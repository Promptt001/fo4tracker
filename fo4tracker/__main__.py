"""Entry point.

    python -m fo4tracker                     # open the window
    python -m fo4tracker SAVE.fos            # open the window with a save
    python -m fo4tracker SAVE.fos --text     # print the report, no window
    python -m fo4tracker SAVE.fos --html out.html
    python -m fo4tracker --latest --text     # newest save, printed
"""
import argparse
import os
import sys

from . import __version__


def _fail(msg, code=1):
    print(f"fo4tracker: error: {msg}", file=sys.stderr)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fo4tracker",
        description="Fallout 4 save tracker: quest progress, key decisions, inventory and stats "
                    "from a .fos save. Opens a window unless --text or --html is given.")
    ap.add_argument("save", nargs="?", help=".fos save file (default: open the window)")
    ap.add_argument("--data", metavar="DIR",
                    help="Fallout 4 Data folder, the one containing Fallout4.esm (auto-detected if omitted)")
    ap.add_argument("--html", metavar="FILE", help="write a standalone HTML report and exit")
    ap.add_argument("--text", action="store_true", help="print a plain-text report and exit")
    ap.add_argument("--latest", action="store_true", help="use your most recent save")
    ap.add_argument("--no-cache", action="store_true", help="don't read or write the game data cache")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    from . import gamedata

    save = args.save
    if args.latest:
        save = gamedata.latest_save()
        if not save:
            return _fail("couldn't find your Fallout 4 save folder; pass the .fos path instead.")
    if save and not os.path.isfile(save):
        return _fail(f"save file not found: {save}")
    if args.data and not os.path.isdir(args.data):
        return _fail(f"Data folder not found: {args.data}")
    if args.data and not gamedata._ci_path(args.data, "Fallout4.esm"):
        print(f"Warning: Fallout4.esm isn't in {args.data}; is this the game's Data folder?",
              file=sys.stderr)

    if not (args.html or args.text):
        try:
            from .gui import main as gui_main
        except ImportError as e:
            if "tkinter" not in str(e).lower() and "_tkinter" not in str(e).lower():
                raise
            return _fail("the window needs Tkinter, which isn't installed for this Python.\n"
                         "  Debian/Ubuntu: sudo apt install python3-tk\n"
                         "  Fedora:        sudo dnf install python3-tkinter\n"
                         "  Arch:          sudo pacman -S tk\n"
                         "Or use --text / --html, which work without it.")
        gui_main(([save] if save else []) + (["--data", args.data] if args.data else []))
        return 0

    if not save:
        ap.error("a save file (or --latest) is required with --html/--text")

    from . import report, savefile
    from .analysis import Report
    from .binutil import ParseError

    try:
        s = savefile.load_save(save)
    except ParseError as e:
        return _fail(f"{os.path.basename(save)}: {e}")
    except OSError as e:
        return _fail(f"couldn't read {save}: {e.strerror or e}")

    data_dir = args.data or next(iter(gamedata.find_data_dirs()), None)
    gd = None
    if data_dir:
        gd = gamedata.GameData.load(data_dir, s.plugins, s.light_plugins, use_cache=not args.no_cache,
                                    progress=lambda m: print(m, file=sys.stderr))
    else:
        print("Fallout 4 Data folder not found; quest and item names will be limited (use --data).",
              file=sys.stderr)
    rep = Report(s, gd)
    if args.html:
        try:
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(report.to_html(rep))
        except OSError as e:
            return _fail(f"couldn't write {args.html}: {e.strerror or e}")
        print(f"Wrote {os.path.abspath(args.html)}", file=sys.stderr if args.text else sys.stdout)
    if args.text:
        # Redirected output on Windows uses a legacy code page that lacks "→" or "⚠";
        # substitute those characters rather than crash.
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        print(report.to_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
