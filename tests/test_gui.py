"""Smoke test for the Tkinter window. Skipped when Tkinter or a display isn't
available (on headless Linux run it under xvfb-run)."""
import os
import time
import unittest

import _path  # noqa: F401
import fixtures as fx

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:          # no tkinter module, or no display
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "Tkinter with a display is required")
class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.env = fx.IsolatedDirs().start()
        from fo4tracker import gui
        self.gui = gui
        self._old_config = gui.CONFIG
        gui.CONFIG = os.path.join(self.env.config, "fo4tracker", "config.json")
        self.data = os.path.join(self.env.root, "Data")
        fx.build_data_dir(self.data)
        self.save = fx.write_standard(self.env.root)
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.gui.CONFIG = self._old_config
        self.env.stop()

    def _pump(self, cond, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if cond():
                return
            time.sleep(0.02)
        self.fail("timed out waiting for the window to update")

    def test_open_browse_and_export(self):
        app = self.gui.App(self.root, self.save, self.data)
        self._pump(lambda: app.rep is not None)
        self.assertIn("Nora", self.root.title())
        self.assertIn("Loaded", app.status.cget("text"))

        # Questlines: groups and rows, filter, search, detail pane
        groups = app.q_tree.get_children()
        self.assertEqual(app.q_tree.item(groups[0], "text"), "Main Quest")
        app.q_tree.selection_set(app.q_tree.get_children(groups[0])[0])
        self.root.update()
        self.assertIn("Reunions", app.q_detail.get("1.0", "end"))
        app.q_tree.selection_set(groups[0])
        self.root.update()
        self.assertIn("Active now", app.q_detail.get("1.0", "end"))
        app.q_search.set("kellogg")
        self.assertEqual(len(app.q_tree.get_children()), 1)
        app.q_search.set("")
        app.q_filter.set("Everything")
        app._fill_quests()
        names = [app.q_tree.set(i, "quest") for g in app.q_tree.get_children() for i in app.q_tree.get_children(g)]
        self.assertIn("The Molecular Level", names)
        app._sort(app.q_tree, "quest")
        self.assertEqual(app.q_tree.item(app.q_tree.get_children()[0], "text"), "Main Quest")

        # Decisions and inventory
        self.assertTrue(app.d_tree.get_children())
        app.d_tree.selection_set(app.d_tree.get_children()[0])
        self.root.update()
        self.assertIn("The Molecular Level", app.d_detail.get("1.0", "end"))
        app._sort(app.i_tree, "count")
        app._sort(app.i_tree, "count")
        first = app.i_tree.get_children()[0]
        self.assertEqual(app.i_tree.set(first, "count"), "1,234")      # descending on 2nd click
        self.assertTrue(app.i_tree.heading("count", "text").endswith("▼"))
        app.i_search.set("stim")
        self.assertEqual(len(app.i_tree.get_children()), 1)

        # Export
        target = os.path.join(self.env.root, "export.html")
        self.assertTrue(app._write_file(target, self.gui.report.to_html(app.rep)))
        self.assertTrue(os.path.getsize(target) > 1000)
        self.assertEqual(app._export_name(".html"), "Nora_save89_progress.html")

    def test_bad_save_shows_error_not_crash(self):
        bad = os.path.join(self.env.root, "bad.fos")
        fx.write(bad, b"nope")
        shown = []
        orig = self.gui.messagebox.showerror
        self.gui.messagebox.showerror = lambda *a, **k: shown.append((a, k))
        try:
            app = self.gui.App(self.root, bad, self.data)
            self._pump(lambda: shown)
        finally:
            self.gui.messagebox.showerror = orig
        self.assertIn("couldn't be read", shown[0][0][1])
        self.assertIn("Not a Fallout 4 save", shown[0][1]["detail"])
        self.assertIsNone(app.rep)

    def test_stale_load_is_ignored(self):
        bad = os.path.join(self.env.root, "bad.fos")
        fx.write(bad, b"nope")
        shown = []
        orig = self.gui.messagebox.showerror
        self.gui.messagebox.showerror = lambda *a, **k: shown.append(a)
        try:
            app = self.gui.App(self.root, bad, self.data)      # slow-ish failure...
            app.open_save(self.save)                          # ...replaced at once by a good save
            self._pump(lambda: app.rep is not None)
            for _ in range(20):
                self.root.update()
                time.sleep(0.01)
        finally:
            self.gui.messagebox.showerror = orig
        self.assertEqual(shown, [])

    def test_safe_filename(self):
        f = self.gui.safe_filename
        self.assertEqual(f('Nora: "The" <Best>?'), "Nora_ _The_ _Best")
        self.assertEqual(f("..."), "report")
        self.assertEqual(f("a/b\\c"), "a_b_c")


if __name__ == "__main__":
    unittest.main()
