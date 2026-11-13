"""Reading plugins, string tables and BA2 archives, plus the on-disk cache."""
import json
import os
import tempfile
import unittest

import _path  # noqa: F401
import fixtures as fx
from fo4tracker import gamedata

PLUGINS = ["Fallout4.esm", "DLCRobot.esm", "DLCCoast.esm"]
LIGHT = ["ccOTMFO4001-Remnants.esl"]


class GameDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        fx.build_data_dir(cls.tmp.name)
        cls.gd = gamedata.GameData.load(cls.tmp.name, PLUGINS, LIGHT, use_cache=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_quests_and_strings(self):
        q = self.gd.quests[("fallout4.esm", 0x229E7)]
        self.assertEqual(q.name, "Reunions")
        self.assertEqual(q.qtype, 1)
        self.assertEqual(q.objectives[200], "Find Kellogg in <Alias=FortHagen>")
        self.assertEqual(q.stages[1000][0][1], "I tracked Kellogg to Fort Hagen.")
        self.assertEqual(self.gd.quests[("fallout4.esm", 0x22A08)].stages[900][0][0], 1)

    def test_masters_case_and_light(self):
        self.assertEqual(self.gd.quests[("dlcrobot.esm", 0x801)].name, "Mechanical Menace")
        self.assertEqual(self.gd.quests[("ccotmfo4001-remnants.esl", 0xE7)].name, "Remnants Quest")
        self.assertIn("DLCCoast.esm", self.gd.missing_plugins)
        self.assertEqual(self.gd.loaded_plugins, ["Fallout4.esm", "DLCRobot.esm", "ccOTMFO4001-Remnants.esl"])

    def test_items_globals(self):
        self.assertEqual(self.gd.items[("fallout4.esm", 0x14831C)], ("WEAP", "CombatShotgun", "Combat Shotgun"))
        self.assertEqual(self.gd.items[("fallout4.esm", 0x1234AB)][2], "Long Barrel")
        self.assertEqual(self.gd.globals[("fallout4.esm", 0x39)], "GameDaysPassed")
        self.assertNotIn(("fallout4.esm", 0x1234), self.gd.items)

    def test_damaged_plugin_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            fx.write(os.path.join(d, "Fallout4.esm"), b"not a plugin at all")
            msgs = []
            gd = gamedata.GameData.load(d, ["Fallout4.esm"], use_cache=False, progress=msgs.append)
            self.assertEqual(gd.loaded_plugins, [])
            self.assertTrue(any("Skipped Fallout4.esm" in m for m in msgs))

    def test_glob_characters_in_data_path(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "[GOG] Fallout 4", "Data")
            fx.build_data_dir(data)
            gd = gamedata.GameData.load(data, ["Fallout4.esm"], use_cache=False)
            self.assertEqual(gd.quests[("fallout4.esm", 0x229E7)].name, "Reunions")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.env = fx.IsolatedDirs().start()
        self.data = os.path.join(self.env.root, "Data")
        fx.build_data_dir(self.data)

    def tearDown(self):
        self.env.stop()

    def _cache_files(self):
        return sorted(f for f in os.listdir(gamedata.cache_dir()) if f.endswith(".json"))

    def test_cache_roundtrip(self):
        first = gamedata.GameData.load(self.data, PLUGINS, LIGHT)
        self.assertEqual(len(self._cache_files()), 3)
        msgs = []
        second = gamedata.GameData.load(self.data, PLUGINS, LIGHT, progress=msgs.append)
        self.assertFalse(any("Reading" in m for m in msgs), msgs)      # served from cache
        self.assertEqual(first.quests, second.quests)
        self.assertEqual(first.items, second.items)
        self.assertEqual(first.globals, second.globals)

    def test_damaged_cache_is_rebuilt(self):
        gamedata.GameData.load(self.data, ["Fallout4.esm"])
        (name,) = self._cache_files()
        with open(os.path.join(gamedata.cache_dir(), name), "w") as fh:
            fh.write("{ truncated")
        gd = gamedata.GameData.load(self.data, ["Fallout4.esm"])
        self.assertEqual(gd.quests[("fallout4.esm", 0x229E7)].name, "Reunions")
        with open(os.path.join(gamedata.cache_dir(), name)) as fh:
            self.assertEqual(json.load(fh)["version"], gamedata.PARSER_VERSION)

    def test_stale_entries_are_pruned_and_clear_cache(self):
        cdir = gamedata.cache_dir()
        stale = os.path.join(cdir, "Fallout4.esm_1_2_en_v3.pkl")
        other = os.path.join(cdir, "unrelated.txt")
        for p in (stale, other):
            fx.write(p, b"x")
        gamedata.GameData.load(self.data, ["Fallout4.esm"])
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(other))
        self.assertEqual(gamedata.clear_cache(), 1)
        self.assertEqual(self._cache_files(), [])
        self.assertTrue(os.path.exists(other))


class LocateTests(unittest.TestCase):
    def test_latest_save(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "Save1.fos"), os.path.join(d, "Save2.FOS")
            fx.write(a, b"x")
            fx.write(b, b"x")
            fx.write(os.path.join(d, "Save3.f4se"), b"x")
            os.utime(a, (1000, 1000))
            os.utime(b, (2000, 2000))
            self.assertEqual(gamedata.latest_save([d, os.path.join(d, "missing")]), b)
            self.assertIsNone(gamedata.latest_save([]))


if __name__ == "__main__":
    unittest.main()
