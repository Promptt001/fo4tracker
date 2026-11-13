"""Turning a save + game data into the report, and exporting it."""
import json
import os
import re
import unittest
from html.parser import HTMLParser

import _path  # noqa: F401
import fixtures as fx
from fo4tracker import gamedata, report, savefile
from fo4tracker.analysis import Report, load_knowledge, quest_line


class _Collector(HTMLParser):
    """Checks the HTML is well formed enough to parse and collects ids/text."""

    def __init__(self):
        super().__init__()
        self.ids, self.text = set(), []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "id":
                self.ids.add(v)

    def handle_data(self, data):
        self.text.append(data)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = fx.IsolatedDirs().start()
        data = os.path.join(cls.env.root, "Data")
        fx.build_data_dir(data)
        path = fx.write_standard(cls.env.root)
        cls.save = savefile.load_save(path)
        cls.gd = gamedata.GameData.load(data, cls.save.plugins, cls.save.light_plugins, use_cache=False)
        cls.rep = Report(cls.save, cls.gd)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def test_quest_statuses_and_lines(self):
        r = self.rep
        self.assertEqual(r.quest("MQ106").status, "active")
        self.assertEqual(r.quest("mq106").line, "Main Quest")
        self.assertEqual([o.text for o in r.quest("MQ106").active_objectives], ["Find Kellogg in Fort Hagen"])
        self.assertEqual(r.quest("MQ106").journal, [(10, "I need to find Kellogg.", 1)])
        self.assertEqual(r.quest("MS17").status, "completed")          # via "complete quest" log flag
        self.assertEqual(r.quest("MQ205").status, "completed")
        self.assertEqual(r.quest("MQ206").status, "not started")
        self.assertEqual(r.quest("DLC01MQ01").line, "Automatron")
        self.assertEqual(r.quest("ccOTM_Quest").line, "Creation Club")
        self.assertIsNone(r.quest("DialogueGenericRaider"))           # no journal: hidden
        unknown = [q for q in r.quests if not q.known]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].line, "Mods")
        self.assertIn("myquestmod.esp", unknown[0].name)

    def test_lines_are_in_story_order(self):
        self.assertEqual(list(self.rep.lines), ["Main Quest", "Automatron", "Side Quests", "Creation Club", "Mods"])
        mq = self.rep.lines["Main Quest"]
        self.assertEqual((mq["completed"], mq["active"], mq["not started"]), (1, 1, 1))

    def test_decisions(self):
        states = {d.quest.edid: d for d in self.rep.decisions}
        self.assertEqual(states["MQ206"].state, "coming up")             # MQ205 done
        self.assertEqual(states["MS17"].state, "decided")
        self.assertIn("Amelia", states["MS17"].evidence[0])
        self.assertEqual(self.rep.decisions[0].state, "coming up")       # most urgent first

    def test_inventory_rows(self):
        rows = {r.name: r for r in self.rep.inventory}
        self.assertEqual(rows["Bottle Cap"].count, 1234)
        self.assertEqual(self.rep.caps(), 1234)
        self.assertTrue(rows["Combat Shotgun"].equipped)
        self.assertEqual(rows["Combat Shotgun"].mods, ["Long Barrel"])
        self.assertTrue(rows["Boomstick <3  (Combat Shotgun)"].custom)
        self.assertEqual(self.rep.inventory[0].category, "Weapons")

    def test_summary(self):
        summary = dict(self.rep.summary())
        overview = summary["Overview"][0]
        self.assertIn("level 25 female", overview)
        self.assertIn("Difficulty: Normal", overview)
        self.assertIn("1 main quest completed", summary["Main story"][0])
        self.assertIn("• Current: Reunions — Find Kellogg in Fort Hagen", summary["Main story"])
        self.assertEqual(summary["Factions"][0], "Most invested faction so far: Brotherhood of Steel.")
        self.assertIn("• Brotherhood of Steel: 3 quests completed.", summary["Factions"])
        self.assertIn("Supplies: 7 Stimpaks, 3 RadAway.", summary["Inventory"])
        self.assertTrue(any("1 plugin used by this save is missing" in n for n in summary["Notes"]))

    def test_text_report(self):
        text = report.to_text(self.rep)
        self.assertTrue(text.startswith("Nora — level 25 — save #89"))
        self.assertIn("[Active] Reunions", text)
        self.assertIn("      1,234  Bottle Cap", text)

    def test_html_report(self):
        page = report.to_html(self.rep)
        p = _Collector()
        p.feed(page)
        self.assertTrue({"summary", "decisions", "quests", "inventory", "stats", "load-order"} <= p.ids)
        self.assertIn("Boomstick &lt;3", page)                           # escaped
        self.assertNotIn("Boomstick <3", page)
        self.assertIn("data:image/png;base64,", page)
        self.assertIn(fx.MISSING_MOD, page)
        self.assertIn("F4SE 0.6.23", page)
        self.assertEqual(page.count("<h3>Key decisions</h3>"), 0)        # not duplicated

    def test_html_escapes_player_name(self):
        s = savefile.SaveGame("x.fos", player_name="<script>alert(1)</script>", saved_at=None)
        page = report.to_html(Report(s, None))
        self.assertNotIn("<script>alert", page)
        self.assertIn("unknown date", page)

    def test_screenshot_png(self):
        png = report.screenshot_png(self.save)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIsNone(report.screenshot_png(savefile.SaveGame("x")))


class NoGameDataTests(unittest.TestCase):
    def test_report_without_data_folder(self):
        with fx.IsolatedDirs() as env:
            s = savefile.load_save(fx.write_standard(env.root))
            r = Report(s, None)
            self.assertEqual(r.quest("MQ106").name, "Reunions")         # from core_quests.json
            self.assertEqual([o.text for o in r.quest("MQ106").objectives], ["Objective 200"])
            self.assertIn("Stimpak", [x.name for x in r.inventory])     # KNOWN_ITEMS fallback
            self.assertTrue(r.notes[0].startswith("Game data not loaded"))
            report.to_html(r)
            report.to_text(r)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.env = fx.IsolatedDirs().start()
        self.user = os.path.join(self.env.root, "decisions.json")

    def tearDown(self):
        self.env.stop()

    def _write(self, obj):
        with open(self.user, "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_builtin_list_is_valid(self):
        decisions, core, problems = load_knowledge(self.user)
        self.assertEqual(problems, [])
        self.assertGreater(len(decisions), 10)
        kinds = {"decision", "point_of_no_return", "ending"}
        for d in decisions:
            self.assertTrue(d["edid"] and d["about"], d)
            self.assertIn(d.get("kind", "decision"), kinds)
            self.assertIsInstance(d.get("after", []), list)
        self.assertEqual(core[0x229E7], ("MQ106", "Reunions"))
        edids = [d["edid"].lower() for d in decisions]
        self.assertEqual(len(edids), len(set(edids)), "duplicate edids in decisions.json")

    def test_user_entries_merge_and_win(self):
        self._write({"decisions": [
            {"edid": "mq206", "about": "Mine.", "kind": "decision"},
            {"edid": "MyMod01", "about": "New.", "kind": "weird-kind"},
            {"about": "no edid"},
        ]})
        decisions, _core, problems = load_knowledge(self.user)
        by = {d["edid"].lower(): d for d in decisions}
        self.assertEqual(by["mq206"]["about"], "Mine.")
        self.assertEqual(by["mymod01"]["kind"], "decision")               # unknown kind normalised
        self.assertEqual(len(problems), 1)
        self.assertIn("1 entry", problems[0])

    def test_broken_user_file_is_reported(self):
        self._write("{ not json")
        decisions, _core, problems = load_knowledge(self.user)
        self.assertGreater(len(decisions), 10)
        self.assertIn("couldn't be read", problems[0])

    def test_problems_show_up_in_report_notes(self):
        os.makedirs(os.path.join(self.env.config, "fo4tracker"))
        with open(os.path.join(self.env.config, "fo4tracker", "decisions.json"), "w") as fh:
            fh.write("[]")
        r = Report(savefile.SaveGame("x.fos"), None)
        self.assertTrue(any("decisions file" in n for n in r.notes), r.notes)


class QuestLineTests(unittest.TestCase):
    def test_quest_line(self):
        cases = [
            (("MQ302Min", 0, "Fallout4.esm"), "Minutemen"),
            (("BoS301", 0, "Fallout4.esm"), "Brotherhood of Steel"),
            (("MS04", 0, "Fallout4.esm"), "Side Quests"),
            (("COMCurie", 0, "Fallout4.esm"), "Companions"),
            (("Something", 6, "Fallout4.esm"), "Miscellaneous"),
            (("DLC03MQ01", 0, "DLCCoast.esm"), "Far Harbor"),
            (("DLC04MQ01", 0, "DLCNukaWorld.esm"), "Nuka-World"),
            (("Any", 0, "DLCworkshop01.esm"), "Workshop DLC"),
            (("Any", 0, "SomeMod.esp"), "Mods"),
            (("Odd", 0, "Fallout4.esm"), "Other"),
        ]
        for args, line in cases:
            self.assertEqual(quest_line(*args), line, args)


class DataFileTests(unittest.TestCase):
    def test_core_quests_json(self):
        here = os.path.join(_path.ROOT, "fo4tracker", "core_quests.json")
        with open(here, encoding="utf-8") as fh:
            rows = json.load(fh)["quests"]
        for fid, edid, name in rows:
            self.assertRegex(fid, r"^[0-9A-F]{6}$")
            self.assertTrue(edid and name)
        self.assertEqual(len({r[0] for r in rows}), len(rows), "duplicate form IDs")
        self.assertTrue(all(re.match(r"^[\w-]+$", r[1]) for r in rows))


if __name__ == "__main__":
    unittest.main()
