"""Optional checks against a real save. Skipped unless FO4_TEST_SAVE is set:

    FO4_TEST_SAVE=/path/to/Save89.fos python -m unittest discover -s tests

Add FO4_TEST_DATA=/path/to/Fallout 4/Data to also check names from the real
game data (otherwise the synthetic Data folder from the fixtures is used).
"""
import os
import tempfile
import unittest

import _path  # noqa: F401
import fixtures as fx
from fo4tracker import gamedata, report, savefile
from fo4tracker.analysis import Report

REAL_SAVE = os.environ.get("FO4_TEST_SAVE")
REAL_DATA = os.environ.get("FO4_TEST_DATA")


@unittest.skipUnless(REAL_SAVE and os.path.exists(REAL_SAVE), "set FO4_TEST_SAVE to run")
class RealSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.save = savefile.load_save(REAL_SAVE)
        cls.tmp = tempfile.TemporaryDirectory()
        data = REAL_DATA
        if not data:
            data = cls.tmp.name
            fx.build_data_dir(data)
        cls.gd = gamedata.GameData.load(data, cls.save.plugins, cls.save.light_plugins,
                                        use_cache=bool(REAL_DATA))
        cls.report = Report(cls.save, cls.gd)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_header(self):
        self.assertTrue(self.save.player_name)
        self.assertGreater(len(self.save.quests), 100)
        self.assertEqual(len(self.save.screenshot_rgba), self.save.shot_width * self.save.shot_height * 4)

    def test_inventory_matches_stats(self):
        caps = sum(i.count for i in self.save.inventory if i.formid == 0xF)
        self.assertGreater(caps, 0)
        self.assertLessEqual(caps, self.save.stat("Most Caps Carried"))

    def test_report_uses_game_data(self):
        r = self.report
        mq106 = r.quest("MQ106")
        self.assertEqual(mq106.name, "Reunions")
        names = [x.name for x in r.inventory]
        self.assertIn("Stimpak", names)
        self.assertTrue(r.summary())
        self.assertTrue(report.to_html(r))
        self.assertTrue(report.to_text(r))

    @unittest.skipIf(REAL_DATA, "only meaningful with the synthetic Data folder")
    def test_against_synthetic_data(self):
        r = self.report
        self.assertIn("Find Kellogg in Fort Hagen", [o.text for o in r.quest("MQ106").objectives])
        self.assertEqual(r.quest("MQ205").status, "not started")
        self.assertTrue(any("Combat Shotgun" in x.name for x in r.inventory))
        states = {d.quest.edid: d.state for d in r.decisions}
        self.assertEqual(states.get("MS17"), "decided")
        self.assertIn("MQ206", states)


if __name__ == "__main__":
    unittest.main()
