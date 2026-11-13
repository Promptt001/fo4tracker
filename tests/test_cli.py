"""The command line: python -m fo4tracker ..."""
import contextlib
import io
import os
import unittest

import _path  # noqa: F401
import fixtures as fx
from fo4tracker import __version__
from fo4tracker.__main__ import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:          # argparse --version / usage errors
            code = e.code
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.env = fx.IsolatedDirs().start()
        self.data = os.path.join(self.env.root, "Data")
        fx.build_data_dir(self.data)
        self.save = fx.write_standard(self.env.root)

    def tearDown(self):
        self.env.stop()

    def test_text_report(self):
        code, out, err = run(self.save, "--text", "--data", self.data)
        self.assertEqual(code, 0, err)
        self.assertIn("Find Kellogg in Fort Hagen", out)
        self.assertIn("Reading Fallout4.esm", err)                    # progress goes to stderr

    def test_html_report(self):
        target = os.path.join(self.env.root, "out", "report.html")
        os.makedirs(os.path.dirname(target))
        code, out, err = run(self.save, "--html", target, "--data", self.data, "--no-cache")
        self.assertEqual(code, 0, err)
        self.assertIn("Wrote", out)
        with open(target, encoding="utf-8") as fh:
            self.assertIn("<h2 id='quests'>Questlines</h2>", fh.read())
        self.assertFalse(os.path.isdir(os.path.join(self.env.cache, "fo4tracker")) and
                         os.listdir(os.path.join(self.env.cache, "fo4tracker")))

    def test_version(self):
        code, out, _err = run("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_missing_save(self):
        code, _out, err = run(os.path.join(self.env.root, "nope.fos"), "--text")
        self.assertEqual(code, 1)
        self.assertIn("save file not found", err)

    def test_not_a_save(self):
        bad = os.path.join(self.env.root, "bad.fos")
        fx.write(bad, b"hello")
        code, _out, err = run(bad, "--text", "--data", self.data)
        self.assertEqual(code, 1)
        self.assertIn("Not a Fallout 4 save", err)
        self.assertNotIn("Traceback", err)

    def test_missing_data_dir(self):
        code, _out, err = run(self.save, "--text", "--data", os.path.join(self.env.root, "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("Data folder not found", err)

    def test_unwritable_html_target(self):
        code, _out, err = run(self.save, "--html", os.path.join(self.env.root, "no", "such", "dir", "x.html"),
                              "--data", self.data)
        self.assertEqual(code, 1)
        self.assertIn("couldn't write", err)

    def test_report_needs_a_save(self):
        code, _out, err = run("--text")
        self.assertEqual(code, 2)
        self.assertIn("a save file", err)


if __name__ == "__main__":
    unittest.main()
