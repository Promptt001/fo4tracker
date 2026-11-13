"""Turn raw save data (+ optional game data) into a progress report."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

from . import savefile as sf
from .gamedata import LOG_COMPLETE, LOG_FAIL, QUEST_TYPES, QuestDef

HERE = os.path.dirname(os.path.abspath(__file__))

LINE_ORDER = ["Main Quest", "Minutemen", "Brotherhood of Steel", "Railroad", "Institute",
              "Automatron", "Far Harbor", "Nuka-World", "Side Quests", "Miscellaneous",
              "Companions", "Workshop DLC", "Creation Club", "Mods", "Other"]
FACTIONS = ["Minutemen", "Brotherhood of Steel", "Railroad", "Institute"]
FACTION_STAT = {
    "Brotherhood of Steel": "Brotherhood of Steel Quests Completed",
    "Institute": "Institute Quests Completed",
    "Minutemen": "Minutemen Quests Completed",
    "Railroad": "Railroad Quests Completed",
}
BASE_PLUGINS = {"fallout4.esm", "dlcrobot.esm", "dlcworkshop01.esm", "dlccoast.esm",
                "dlcworkshop02.esm", "dlcworkshop03.esm", "dlcnukaworld.esm",
                "dlcultrahighresolution.esm"}

# A few well-known Fallout4.esm items, used when game data isn't loaded.
KNOWN_ITEMS = {
    0x0000000F: ("MISC", "Bottle caps"), 0x0000000A: ("MISC", "Bobby pin"),
    0x00023736: ("ALCH", "Stimpak"), 0x00023742: ("ALCH", "RadAway"),
    0x00024057: ("ALCH", "Rad-X"), 0x00075FE4: ("AMMO", "Fusion Core"),
}
TYPE_CATEGORY = {"WEAP": "Weapons", "ARMO": "Apparel", "AMMO": "Ammo", "ALCH": "Aid",
                 "MISC": "Misc & Junk", "BOOK": "Books & Magazines", "KEYM": "Keys",
                 "NOTE": "Holotapes & Notes", "OMOD": "Mods"}

DECISION_WORDS = re.compile(r"\b(or|decide|choose|choice|side with|spare|betray|deal with|confront)\b", re.I)
TOKEN = re.compile(r"<(Alias|Global|Token|Ref)(?:[.\w]*)=([^>]+)>", re.I)

STATUS_ORDER = {"active": 0, "started": 1, "completed": 2, "failed": 3, "not started": 4}
DECISION_KINDS = {"decision", "point_of_no_return", "ending"}


def plural(n, word, many=None):
    """plural(1, "quest") -> "1 quest", plural(3, "quest") -> "3 quests"."""
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


@dataclass
class Objective:
    index: int
    text: str
    state: int

    @property
    def label(self):
        return {1: "Active", 2: "Done", 3: "Done", 4: "Failed", 5: "Failed"}.get(self.state, "Hidden")


@dataclass
class QuestView:
    key: tuple
    formid: int | None
    edid: str
    name: str
    line: str
    status: str
    tracked: bool = False
    objectives: list = field(default_factory=list)
    journal: list = field(default_factory=list)       # (stage, text, variants)
    done_stages: list = field(default_factory=list)
    known: bool = True                                 # name resolved

    @property
    def active_objectives(self):
        return [o for o in self.objectives if o.state == sf.OBJ_DISPLAYED]

    @property
    def failed_objectives(self):
        return [o for o in self.objectives if o.state in (sf.OBJ_FAILED, sf.OBJ_FAILED_DISPLAYED)]

    @property
    def last_entry(self):
        return self.journal[-1][1] if self.journal else ""


@dataclass
class Decision:
    quest: QuestView
    kind: str
    about: str
    state: str          # decided / in progress / coming up / not reached
    evidence: list = field(default_factory=list)


@dataclass
class InvRow:
    category: str
    name: str
    count: int
    equipped: bool
    custom: bool
    formid: int
    plugin: str
    mods: list


def _config_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "fo4tracker")


def user_decisions_path():
    return os.path.join(_config_dir(), "decisions.json")


def _valid_decision(d):
    return (isinstance(d, dict) and isinstance(d.get("edid"), str) and d["edid"].strip()
            and isinstance(d.get("after", []), list))


def load_knowledge(user_path=None):
    """Return (decisions, core_quests, problems).

    Built-in decisions are merged with the user's own decisions.json (theirs win
    on the same edid). `problems` lists anything wrong with the user's file so it
    can be shown instead of being silently ignored.
    """
    problems = []
    with open(os.path.join(HERE, "decisions.json"), encoding="utf-8") as fh:
        decisions = json.load(fh)["decisions"]
    user = user_path or user_decisions_path()
    if os.path.exists(user):
        try:
            with open(user, encoding="utf-8") as fh:
                extra = json.load(fh).get("decisions", [])
            if not isinstance(extra, list):
                raise ValueError('"decisions" must be a list')
            by = {d["edid"].lower(): d for d in decisions}
            skipped = 0
            for d in extra:
                if not _valid_decision(d):
                    skipped += 1
                    continue
                if d.get("kind", "decision") not in DECISION_KINDS:
                    d = dict(d, kind="decision")
                by[d["edid"].lower()] = d
            decisions = list(by.values())
            if skipped:
                problems.append(f"{plural(skipped, 'entry', 'entries')} in {user} "
                                f"{'was' if skipped == 1 else 'were'} skipped (each needs an \"edid\").")
        except (OSError, ValueError, AttributeError) as e:
            problems.append(f"Your decisions file {user} couldn't be read ({e}); using the built-in list.")
    with open(os.path.join(HERE, "core_quests.json"), encoding="utf-8") as fh:
        core = {int(fid, 16): (edid, name) for fid, edid, name in json.load(fh)["quests"]}
    return decisions, core, problems


def quest_line(edid: str, qtype: int, plugin: str) -> str:
    e = edid or ""
    low = plugin.lower()
    if e.startswith("DLC01") or low == "dlcrobot.esm":
        return "Automatron"
    if e.startswith("DLC03") or low == "dlccoast.esm":
        return "Far Harbor"
    if e.startswith("DLC04") or low == "dlcnukaworld.esm":
        return "Nuka-World"
    if low.startswith("dlcworkshop"):
        return "Workshop DLC"
    if low.startswith("cc"):
        return "Creation Club"
    if low not in BASE_PLUGINS:
        return "Mods"
    if e.startswith("MQ302Min"):
        return "Minutemen"
    if e.startswith("MQ302BoS"):
        return "Brotherhood of Steel"
    if e.startswith("MQ302RR"):
        return "Railroad"
    if qtype in (1, 2, 3, 4, 5):
        return QUEST_TYPES[qtype]
    for pre, line in (("MQ", "Main Quest"), ("BoS", "Brotherhood of Steel"), ("Inst", "Institute"),
                      ("RR", "Railroad"), ("Min", "Minutemen"), ("COM", "Companions"),
                      ("Com", "Companions")):
        if e.startswith(pre):
            return line
    if qtype == 7 or re.match(r"^(MS\d|DN\d|FF|V81)", e):
        return "Side Quests"
    if qtype == 6:
        return "Miscellaneous"
    return "Other"


def clean_text(text: str, globals_by_edid=None) -> str:
    def rep(m):
        kind, name = m.group(1).lower(), m.group(2)
        if kind == "global" and globals_by_edid and name.lower() in globals_by_edid:
            v = globals_by_edid[name.lower()]
            return str(int(v)) if float(v).is_integer() else f"{v:g}"
        return re.sub(r"(?<!^)(?=[A-Z])", " ", name).strip()
    return TOKEN.sub(rep, text or "").strip()


class Report:
    def __init__(self, save: sf.SaveGame, gamedata=None):
        self.save = save
        self.gd = gamedata
        self.kb, self.core, kb_problems = load_knowledge()
        self.notes = list(kb_problems)
        self._globals_by_edid = self._map_globals()
        self.quests = self._build_quests()
        self.by_edid = {q.edid.lower(): q for q in self.quests if q.edid}
        self.lines = self._build_lines()
        self.decisions = self._build_decisions()
        self.inventory = self._build_inventory()
        if not gamedata:
            self.notes.append("Game data not loaded: only main/faction quest names are known and most "
                              "objective text, journal entries and item names are missing. Point the "
                              "tool at your Fallout 4 Data folder for the full picture.")
        elif gamedata.missing_plugins:
            n = len(gamedata.missing_plugins)
            self.notes.append(f"{plural(n, 'plugin')} used by this save "
                              f"{'are' if n != 1 else 'is'} missing from your Data folder, so some "
                              "names are unknown (see Load order).")
        if save.inventory_note:
            self.notes.append("Inventory: " + save.inventory_note.rstrip(".") + ".")

    # ---- lookups --------------------------------------------------------
    def _key(self, formid):
        k = self.save.plugin_key(formid)
        return (k[0].lower(), k[1]) if k else None

    def _map_globals(self):
        out = {}
        if not self.gd:
            return out
        for fid, val in self.save.globals.items():
            k = self._key(fid)
            if k and k in self.gd.globals:
                out[self.gd.globals[k].lower()] = val
        return out

    def _qdef(self, key):
        if self.gd and key in self.gd.quests:
            return self.gd.quests[key]
        if key and key[0] == "fallout4.esm" and key[1] in self.core:
            edid, name = self.core[key[1]]
            return QuestDef(edid=edid, name=name, plugin="Fallout4.esm")
        return None

    # ---- quests ---------------------------------------------------------
    def _build_quests(self):
        views = {}
        # Everything the save knows about
        for fid, qs in self.save.quests.items():
            key = self._key(fid)
            if key is None:
                continue
            views[key] = self._view(key, fid, qs)
        # Journal quests the save has never touched = not started
        if self.gd:
            for key, qd in self.gd.quests.items():
                if key not in views and qd.name and qd.has_journal and not qd.name.startswith("["):
                    views[key] = self._view(key, None, None)
        out = [v for v in views.values() if v is not None]
        out.sort(key=lambda v: (LINE_ORDER.index(v.line) if v.line in LINE_ORDER else 99,
                                STATUS_ORDER.get(v.status, 9), v.name.lower()))
        return out

    def _view(self, key, fid, qs):
        qd = self._qdef(key)
        plugin = qd.plugin if qd and qd.plugin else key[0]
        if qd is None:
            if not qs or not qs.objectives:
                return None
            name = f"Unidentified quest ({key[0]}:{key[1]:06X})"
            edid, qtype, known = "", 0, False
        else:
            name, edid, qtype, known = qd.name, qd.edid, qd.qtype, True
            if not name or name.startswith("["):
                return None
            if self.gd and not qd.has_journal and not (qs and qs.objectives):
                return None   # internal/dialogue quest, not shown in the Pip-Boy
            if not self.gd and not (qs and (qs.objectives or qs.completed)):
                return None
        name = clean_text(name, self._globals_by_edid)
        line = quest_line(edid, qtype, plugin)
        v = QuestView(key, fid, edid, name, line, "not started", known=known)
        if qs is None:
            return v
        v.tracked = bool(qs.flags & sf.Q_ACTIVE_TRACKED)
        v.done_stages = qs.done_stages
        completed = qs.completed
        failed = qs.failed
        if qd:
            for st in v.done_stages:
                logs = qd.stages.get(st, [])
                texts = [(f, t) for f, t in logs if t]
                for f, _t in logs:
                    if f & LOG_COMPLETE:
                        completed = True
                    if f & LOG_FAIL:
                        failed = True
                if texts:
                    v.journal.append((st, clean_text(texts[0][1], self._globals_by_edid), len(texts)))
        for idx in sorted(qs.objectives):
            state = qs.objectives[idx]
            if state == sf.OBJ_DORMANT:
                continue
            text = qd.objectives.get(idx, "") if qd else ""
            text = clean_text(text, self._globals_by_edid) or f"Objective {idx}"
            v.objectives.append(Objective(idx, text, state))
        if completed:
            v.status = "completed"
        elif failed:
            v.status = "failed"
        elif v.active_objectives:
            v.status = "active"
        elif v.done_stages or v.objectives or v.journal:
            v.status = "started"
        return v

    def _build_lines(self):
        lines = {}
        for q in self.quests:
            d = lines.setdefault(q.line, {"quests": [], "completed": 0, "active": 0, "failed": 0,
                                          "not started": 0, "started": 0})
            d["quests"].append(q)
            d[q.status] = d.get(q.status, 0) + 1
        return dict(sorted(lines.items(), key=lambda kv: LINE_ORDER.index(kv[0])
                           if kv[0] in LINE_ORDER else 99))

    # ---- decisions ------------------------------------------------------
    def _build_decisions(self):
        out = []
        seen = set()
        for d in self.kb:
            q = self.by_edid.get(d["edid"].lower())
            if q is None:
                continue
            seen.add(q.key)
            if q.status in ("completed", "failed"):
                state = "decided"
            elif q.status in ("active", "started"):
                state = "in progress"
            else:
                after = [self.by_edid.get(a.lower()) for a in d.get("after", [])]
                if after and all(a is not None and a.status == "completed" for a in after):
                    state = "coming up"
                else:
                    state = "not reached"
            ev = []
            if q.journal:
                ev.append("Journal: " + q.journal[-1][1])
            for o in q.failed_objectives:
                ev.append("Route not taken: " + o.text)
            out.append(Decision(q, d.get("kind", "decision"), d.get("about", ""), state, ev))
        # Generic: active objectives that read like a choice
        for q in self.quests:
            if q.key in seen or q.status != "active":
                continue
            choice = [o.text for o in q.active_objectives if DECISION_WORDS.search(o.text)]
            if choice:
                out.append(Decision(q, "possible", "Your current objectives offer different ways forward.",
                                    "in progress", ["Objective: " + c for c in choice]))
        rank = {"in progress": 0, "coming up": 1, "decided": 2, "not reached": 3}
        out.sort(key=lambda d: (rank[d.state], LINE_ORDER.index(d.quest.line)
                                if d.quest.line in LINE_ORDER else 99))
        return out

    # ---- inventory ------------------------------------------------------
    def _build_inventory(self):
        rows = []
        for it in self.save.inventory:
            key = self._key(it.formid)
            rec = self.gd.items.get(key) if (self.gd and key) else None
            if rec:
                rtype, edid, name = rec
                name = name or edid
            elif it.formid in KNOWN_ITEMS:
                rtype, name = KNOWN_ITEMS[it.formid]
            else:
                rtype = ""
                name = f"{key[0] if key else '?'}:{(key[1] if key else it.formid):06X}"
            cat = TYPE_CATEGORY.get(rtype, "Unidentified")
            plugin = key[0] if key else ""
            # Group unmodified stacks; list renamed/modded ones separately
            plain = [s for s in it.stacks if not s.custom_name]
            for s in it.stacks:
                if s.custom_name:
                    rows.append(InvRow(cat, f"{s.custom_name}  ({name})", s.count, s.equipped, True,
                                       it.formid, plugin, self._mod_names(s.mods)))
            if plain:
                mods = []
                for s in plain:
                    mods.extend(self._mod_names(s.mods))
                rows.append(InvRow(cat, name, sum(s.count for s in plain),
                                   any(s.equipped for s in plain), False, it.formid, plugin,
                                   sorted(set(mods))))
        order = list(TYPE_CATEGORY.values()) + ["Unidentified"]
        rows.sort(key=lambda r: (order.index(r.category) if r.category in order else 99, r.name.lower()))
        return rows

    def _mod_names(self, mods):
        out = []
        if not self.gd:
            return out
        for fid in mods:
            k = self._key(fid)
            rec = self.gd.items.get(k) if k else None
            if rec and rec[2]:
                out.append(rec[2])
        return out

    # ---- summary --------------------------------------------------------
    def caps(self):
        return sum(r.count for r in self.inventory if r.formid == 0xF)

    def quest(self, edid):
        return self.by_edid.get(edid.lower())

    def summary(self):
        """Executive summary as a list of (heading, [paragraphs/bullets])."""
        s = self.save
        out = []
        diff = s.stat("Game Difficulty")
        diff_name = sf.DIFFICULTY.get(diff, str(diff)) if diff is not None else "unknown"
        intro = (f"{s.player_name} is a level {s.level} {s.sex_name.lower()} character "
                 f"({int(s.xp):,} / {int(s.xp_next):,} XP to the next level), last saved in "
                 f"{s.location} after {s.playtime_pretty} of play ({s.stat('Days Passed', '?')} in-game days). "
                 f"Difficulty: {diff_name}.")
        if s.stat("Survival Denied"):
            intro += " The save has left Survival mode at some point."
        out.append(("Overview", [intro]))

        # Main quest
        mq = self.lines.get("Main Quest", {"quests": []})
        mq_done = [q for q in mq["quests"] if q.status == "completed"]
        mq_active = [q for q in mq["quests"] if q.status == "active"]
        lines = []
        ending = [q for q in self.quests if q.edid.startswith(("MQ302", "Inst308")) and q.status == "completed"]
        if ending:
            lines.append("The story is finished: " + ", ".join(f"{q.name} ({q.line})" for q in ending) + ".")
        lines.append(f"{plural(len(mq_done), 'main quest')} completed (game stat: "
                     f"{s.stat('Main Quests Completed', '?')}).")
        for q in mq_active:
            objs = "; ".join(o.text for o in q.active_objectives) or q.last_entry
            lines.append(f"• Current: {q.name}" + (f" — {objs}" if objs else ""))
        out.append(("Main story", lines))

        # Factions
        flines = []
        for fac in FACTIONS:
            d = self.lines.get(fac)
            stat = s.stat(FACTION_STAT.get(fac, ""), None)
            done = d["completed"] if d else 0
            act = [q for q in (d["quests"] if d else []) if q.status == "active"]
            started = done or act or (stat or 0) > 0
            txt = f"• {fac}: "
            if not started:
                txt += "not joined / no progress yet."
            else:
                n = stat if stat is not None else done
                txt += f"{plural(n, 'quest')} completed"
                if act:
                    txt += "; active: " + ", ".join(q.name for q in act[:4])
                    if len(act) > 4:
                        txt += f" (+{len(act) - 4} more)"
                txt += "."
            flines.append(txt)
        lead = max(FACTIONS, key=lambda f: s.stat(FACTION_STAT.get(f, ""), 0) or 0)
        if (s.stat(FACTION_STAT.get(lead, ""), 0) or 0) > 0:
            flines.insert(0, f"Most invested faction so far: {lead}.")
        out.append(("Factions", flines))

        # DLC
        dl = []
        for dlc in ("Automatron", "Far Harbor", "Nuka-World"):
            d = self.lines.get(dlc)
            if not d or not (d["completed"] or d["active"] or d["started"]):
                dl.append(f"• {dlc}: not started.")
            else:
                act = [q.name for q in d["quests"] if q.status == "active"]
                dl.append(f"• {dlc}: {plural(d['completed'], 'quest')} completed" +
                          (f"; active: {', '.join(act[:4])}" if act else "") + ".")
        out.append(("DLC", dl))

        # Side content
        side = self.lines.get("Side Quests", {"completed": 0, "active": 0, "quests": []})
        out.append(("Side quests", [
            f"{plural(side['completed'], 'side quest')} completed, {side['active']} active "
            f"(game stats: {s.stat('Side Quests Completed', '?')} side, "
            f"{s.stat('Misc Objectives Completed', '?')} misc objectives, "
            f"{s.stat('Quests Completed', '?')} quests overall)."]))

        # Decisions
        dec = []
        for d in self.decisions:
            if d.state == "in progress":
                tag = "⚠ Point of no return" if d.kind == "point_of_no_return" else "Decision ahead"
                dec.append(f"• {tag} — {d.quest.name} ({d.quest.line}): {d.about}")
        for d in self.decisions:
            if d.state == "coming up":
                dec.append(f"• Coming up — {d.quest.name}: {d.about}")
        made = [d for d in self.decisions if d.state == "decided"]
        for d in made:
            dec.append(f"• Decided — {d.quest.name}: " + (d.evidence[0] if d.evidence else d.about))
        if not dec:
            dec.append("No tracked story decisions reached yet.")
        out.append(("Key decisions", dec))

        # Inventory
        inv = self.inventory
        if inv:
            weapons = [r for r in inv if r.category == "Weapons"]
            named = [r.name for r in inv if r.custom]
            eq = [r.name for r in inv if r.equipped]
            il = [f"{plural(self.caps(), 'cap')}, {plural(len(inv), 'inventory line')}, "
                  f"{plural(len(weapons), 'weapon')}."]
            if eq:
                il.append("Equipped: " + ", ".join(eq) + ".")
            if named:
                il.append("Named/renamed gear: " + ", ".join(named) + ".")
            supplies = []
            for fid, word, many in ((0x23736, "Stimpak", None), (0x23742, "RadAway", "RadAway"),
                                    (0x75FE4, "Fusion Core", None)):
                c = sum(r.count for r in inv if r.formid == fid)
                if c:
                    supplies.append(plural(c, word, many))
            if supplies:
                il.append("Supplies: " + ", ".join(supplies) + ".")
            out.append(("Inventory", il))

        # Play style
        def g(name):
            return s.stat(name, 0) or 0
        kills = sum(g(k) for k in ("People Killed", "Creatures Killed", "Animals Killed",
                                   "Robots Killed", "Synths Killed"))
        out.append(("Play style", [
            f"{kills:,} kills "
            f"({g('Legendary Enemies Killed')} legendary), {g('Sneak Attacks'):,} sneak attacks, "
            f"{g('Critical Strikes'):,} critical hits. {g('Locks Picked')} locks picked, "
            f"{g('Computers Hacked')} terminals hacked, {g('Speech Successes')} speech successes.",
            f"Explored {g('Locations Discovered')} locations, cleared {g('Dungeons Cleared')}; "
            f"{g('Workshops Unlocked')} workshops unlocked, {g('Objects Built'):,} objects built; "
            f"{g('Bobbleheads Collected')} bobbleheads, {g('Skill Books Read')} magazines."]))
        if self.notes:
            out.append(("Notes", self.notes))
        return out
