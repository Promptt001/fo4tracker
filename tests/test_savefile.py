"""Decoding .fos saves and F4SE co-saves, including damaged files."""
import datetime as dt
import os
import tempfile
import unittest

import _path  # noqa: F401
import fixtures as fx
from fo4tracker import savefile
from fo4tracker.binutil import ParseError


class StandardSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = fx.write_standard(cls.tmp.name)
        cls.save = savefile.load_save(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_header(self):
        s = self.save
        self.assertEqual((s.player_name, s.level, s.location), ("Nora", 25, "Sanctuary Hills"))
        self.assertEqual(s.save_number, 89)
        self.assertEqual(s.sex_name, "Female")
        self.assertEqual(s.saved_at, dt.datetime(2026, 11, 1, 18, 30))
        self.assertEqual(s.game_version, "1.11.169.0")
        self.assertEqual(len(s.screenshot_rgba), s.shot_width * s.shot_height * 4)
        self.assertEqual(s.plugins, ["Fallout4.esm", "DLCRobot.esm", fx.MISSING_MOD])
        self.assertEqual(s.light_plugins, ["ccOTMFO4001-Remnants.esl"])
        self.assertEqual(s.warnings, [])

    def test_stats_and_globals(self):
        self.assertEqual(self.save.stat("Game Difficulty"), 2)
        self.assertIsNone(self.save.stat("No Such Stat"))
        self.assertEqual(self.save.globals, {0x39: 12.0})

    def test_refid_resolution(self):
        s = self.save
        self.assertEqual(s.resolve_refid(fx.esm_ref(0x1234)), 0x1234)
        self.assertEqual(s.resolve_refid(1), 0x01000801)
        self.assertEqual(s.resolve_refid(99), 0)                   # past the form ID array
        self.assertEqual(s.resolve_refid((2 << 22) | 5), 0xFF000005)
        self.assertEqual(s.plugin_key(0x01000801), ("DLCRobot.esm", 0x801))
        self.assertEqual(s.plugin_key(0xFE0000E7), ("ccOTMFO4001-Remnants.esl", 0xE7))
        self.assertIsNone(s.plugin_key(0xFF000001))
        self.assertIsNone(s.plugin_key(0x07000001))

    def test_quests(self):
        q = self.save.quests
        self.assertEqual(q[0x229E7].done_stages, [10])
        self.assertEqual(q[0x229E7].objectives, {200: savefile.OBJ_DISPLAYED})
        self.assertTrue(q[0x229E7].running)
        self.assertTrue(q[0x22A08].completed)
        self.assertTrue(q[0x229EB].completed)                      # compressed change form
        self.assertIn(0x01000801, q)
        self.assertIn(0xFE0000E7, q)
        self.assertNotIn(0x9999, q)                                # REFR change form skipped

    def test_inventory(self):
        inv = {i.formid: i for i in self.save.inventory}
        self.assertEqual(inv[fx.CAPS].count, 1234)
        self.assertEqual(inv[fx.STIMPAK].count, 7)
        self.assertEqual(inv[fx.RADAWAY].count, 3)                 # recovered past an unknown extra
        gun = inv[fx.SHOTGUN]
        self.assertEqual(len(gun.stacks), 2)
        self.assertTrue(gun.stacks[0].equipped)
        self.assertEqual(gun.stacks[0].mods, [fx.LONG_BARREL])
        self.assertEqual(gun.stacks[1].custom_name, "Boomstick <3")
        self.assertIn("unrecognised format", self.save.inventory_note)

    def test_cosave(self):
        c = self.save.cosave
        self.assertIsNotNone(c)
        self.assertEqual(c.f4se_version, "0.6.23")
        self.assertEqual(c.plugin_names, ["Fallout4.esm", "DLCRobot.esm"])


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data, name="Save1.fos"):
        p = os.path.join(self.tmp.name, name)
        fx.write(p, data)
        return p

    def test_not_a_save(self):
        with self.assertRaisesRegex(ParseError, "Not a Fallout 4 save"):
            savefile.load_save(self._write(b"TESV_SAVEGAME" + b"\0" * 100))

    def test_truncated_saves_raise_parse_error(self):
        data = fx.standard_save()
        for cut in (14, 40, 120, 200, len(data) // 2):
            with self.subTest(cut=cut), self.assertRaises(ParseError):
                savefile.load_save(self._write(data[:cut]))

    def test_header_only(self):
        s = savefile.load_save(self._write(fx.standard_save()), parse_body=False)
        self.assertEqual(s.player_name, "Nora")
        self.assertEqual(s.quests, {})

    def test_damaged_change_forms_keep_earlier_data(self):
        good = fx.quest_change(fx.esm_ref(0x229E7), stages={10: True})
        bad = fx.refid(fx.esm_ref(0x22A08)) + b"\x02\x00\x00\x00" + bytes([(2 << 6) | 8, 1]) + \
            b"\xff\xff\x00\x00\x00\x00\x00\x00"                          # claims 65535 bytes
        s = savefile.load_save(self._write(fx.build_save(change_forms=[good, bad])))
        self.assertIn(0x229E7, s.quests)
        self.assertTrue(any("change forms stopped early" in w for w in s.warnings))
        self.assertEqual(s.inventory, [])
        self.assertEqual(s.inventory_note, "player record not found in save")

    def test_old_form_version_has_no_light_plugins(self):
        s = savefile.load_save(self._write(fx.build_save(form_version=67, plugins=("Fallout4.esm",))))
        self.assertEqual(s.light_plugins, [])

    def test_no_screenshot(self):
        s = savefile.load_save(self._write(fx.build_save(shot=(0, 0))))
        self.assertEqual(s.screenshot_rgba, b"")

    def test_bad_cosave_is_ignored(self):
        p = self._write(fx.build_save())
        self._write(b"garbage", "Save1.f4se")
        self.assertIsNone(savefile.load_save(p).cosave)


if __name__ == "__main__":
    unittest.main()
