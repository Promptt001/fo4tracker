"""Low-level format helpers: binary reader, string tables, text cleanup."""
import unittest

import _path  # noqa: F401
import fixtures as fx
from fo4tracker import gamedata
from fo4tracker.analysis import clean_text, plural
from fo4tracker.binutil import ParseError, Reader
from fo4tracker.savefile import SaveGame


class ReaderTests(unittest.TestCase):
    def test_vsval(self):
        self.assertEqual(Reader(bytes([0x08])).vsval(), 2)
        self.assertEqual(Reader(bytes([0x3D, 0x01])).vsval(), 79)
        self.assertEqual(Reader(bytes([0x02, 0x00, 0x01])).vsval(), 0x10000 >> 2)

    def test_vsval_roundtrip(self):
        for n in (0, 1, 63, 64, 1000, 16383, 16384, 100000):
            self.assertEqual(Reader(fx.vsval(n)).vsval(), n, n)

    def test_vsval_past_end_is_parse_error(self):
        with self.assertRaises(ParseError):
            Reader(b"").vsval()
        with self.assertRaises(ParseError):
            Reader(b"\x01").vsval()          # says 2 bytes, has 1

    def test_end_is_clamped_to_buffer(self):
        r = Reader(b"\x01\x02", 0, 1000)
        self.assertEqual(r.end, 2)
        with self.assertRaises(ParseError):
            r.u32()

    def test_refid_and_wstring(self):
        r = Reader(fx.refid(0x400014) + fx.wstr("Nora"))
        self.assertEqual(r.refid(), 0x400014)
        self.assertEqual(r.wstring(), "Nora")


class StringTableTests(unittest.TestCase):
    def test_string_tables(self):
        s = fx.Strings()
        s.add("Hello")
        s.add("Long text", "dlstrings")
        self.assertEqual(gamedata.parse_string_table(s.build("strings"), "strings"), {1: "Hello"})
        self.assertEqual(gamedata.parse_string_table(s.build("dlstrings"), "dlstrings"), {2: "Long text"})

    def test_cp1252_fallback(self):
        data = b"\x01\x00\x00\x00\x06\x00\x00\x00" + b"\x01\x00\x00\x00\x00\x00\x00\x00" + b"Caf\xe9!\x00"
        self.assertEqual(gamedata.parse_string_table(data, "strings"), {1: "Café!"})


class TextTests(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(clean_text("Go to <Alias=FortHagen>"), "Go to Fort Hagen")
        self.assertEqual(clean_text("<Global=Count> left", {"count": 3.0}), "3 left")
        self.assertEqual(clean_text("<Global=Ratio>", {"ratio": 0.5}), "0.5")
        self.assertEqual(clean_text(None), "")

    def test_plural(self):
        self.assertEqual(plural(1, "quest"), "1 quest")
        self.assertEqual(plural(0, "quest"), "0 quests")
        self.assertEqual(plural(2500, "cap"), "2,500 caps")
        self.assertEqual(plural(2, "entry", "entries"), "2 entries")

    def test_playtime_pretty(self):
        cases = {
            "1d.16h.43m.1 days.16 hours.43 minutes": "1 day, 16 hours, 43 minutes",
            "0d.3h.1m.0 days.3 hours.1 minutes": "3 hours, 1 minute",
            "0d.0h.0m.0 days.0 hours.0 minutes": "0 minutes",
            "something else": "something else",
        }
        for raw, pretty in cases.items():
            self.assertEqual(SaveGame("x", playtime=raw).playtime_pretty, pretty)


if __name__ == "__main__":
    unittest.main()
