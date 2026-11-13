"""Reader for Fallout 4 save games (.fos) and F4SE co-saves (.f4se).

Everything here is read-only. The layout was worked out against real
Anniversary Edition saves (form version 69, game 1.11.x); older saves from
1.10 and earlier use the same structure apart from the light-plugin list.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field

from .binutil import ParseError, Reader

# Change form type codes (low 6 bits of the type byte)
CF_REFR, CF_ACHR, CF_QUST = 0, 1, 8

# Quest change flags
QF_FLAGS = 1 << 1
QF_SCRIPT_DELAY = 1 << 2
QF_ALREADY_RUN = 1 << 26
QF_INSTANCES = 1 << 27
QF_RUNDATA = 1 << 28
QF_OBJECTIVES = 1 << 29
QF_SCRIPT = 1 << 30
QF_STAGES = 1 << 31

# Runtime quest flags (uint16 stored in the change form)
Q_RUNNING = 0x0001
Q_COMPLETED = 0x0002
Q_FAILED = 0x0040
Q_ACTIVE_TRACKED = 0x0800

# Objective state values stored in the save
OBJ_DORMANT, OBJ_DISPLAYED, OBJ_COMPLETED, OBJ_COMPLETED_DISPLAYED = 0, 1, 2, 3
OBJ_FAILED, OBJ_FAILED_DISPLAYED = 4, 5

PLAYER_REF = 0x14

DIFFICULTY = {0: "Very Easy", 1: "Easy", 2: "Normal", 3: "Hard", 4: "Very Hard",
              5: "Survival", 6: "Survival"}


@dataclass
class QuestState:
    formid: int                    # load-order form ID
    flags: int = 0                 # runtime quest flags
    stages: dict = field(default_factory=dict)      # stage index -> done (bool)
    objectives: dict = field(default_factory=dict)  # objective index -> state

    @property
    def completed(self):
        return bool(self.flags & Q_COMPLETED)

    @property
    def failed(self):
        return bool(self.flags & Q_FAILED)

    @property
    def running(self):
        return bool(self.flags & Q_RUNNING)

    @property
    def done_stages(self):
        return sorted(s for s, d in self.stages.items() if d)


@dataclass
class ItemStack:
    count: int
    flags: int
    custom_name: str | None = None
    mods: list = field(default_factory=list)   # load-order form IDs of OMODs
    extra_types: list = field(default_factory=list)

    @property
    def equipped(self):
        return bool(self.flags & 1)


@dataclass
class InventoryItem:
    formid: int
    stacks: list

    @property
    def count(self):
        return sum(s.count for s in self.stacks)


@dataclass
class ChangeForm:
    refid: int
    formid: int
    change_flags: int
    cf_type: int
    version: int
    data: bytes


@dataclass
class CoSave:
    path: str
    format_version: int
    f4se_version: str
    runtime: str
    plugin_signatures: list
    plugin_names: list


@dataclass
class SaveGame:
    path: str
    version: int = 0
    save_number: int = 0
    player_name: str = ""
    level: int = 0
    location: str = ""
    playtime: str = ""
    race: str = ""
    sex: int = 0
    xp: float = 0.0
    xp_next: float = 0.0
    saved_at: _dt.datetime | None = None
    shot_width: int = 0
    shot_height: int = 0
    screenshot_rgba: bytes = b""
    form_version: int = 0
    game_version: str = ""
    plugins: list = field(default_factory=list)
    light_plugins: list = field(default_factory=list)
    misc_stats: list = field(default_factory=list)      # (category, name, value)
    globals: dict = field(default_factory=dict)         # load-order formid -> float
    formid_array: list = field(default_factory=list)
    change_forms: list = field(default_factory=list)
    quests: dict = field(default_factory=dict)          # formid -> QuestState
    inventory: list = field(default_factory=list)       # InventoryItem
    inventory_note: str = ""
    warnings: list = field(default_factory=list)
    cosave: CoSave | None = None

    # ---- helpers -------------------------------------------------------
    @property
    def sex_name(self):
        return "Female" if self.sex == 1 else "Male"

    @property
    def playtime_pretty(self):
        # stored like "1d.16h.43m.1 days.16 hours.43 minutes"
        m = re.match(r"(\d+)d\.(\d+)h\.(\d+)m", self.playtime)
        if not m:
            return self.playtime
        d, h, mi = (int(x) for x in m.groups())
        parts = [(d, "day"), (h, "hour"), (mi, "minute")]
        # Drop leading zero units ("0 days, 3 hours" -> "3 hours") but keep at least minutes.
        while len(parts) > 1 and parts[0][0] == 0:
            parts.pop(0)
        return ", ".join(f"{n} {w}{'' if n == 1 else 's'}" for n, w in parts)

    def stat(self, name, default=None):
        for _cat, n, v in self.misc_stats:
            if n == name:
                return v
        return default

    def resolve_refid(self, raw: int) -> int:
        """3-byte save RefID -> load-order form ID (0 if unresolvable)."""
        kind = raw >> 22
        val = raw & 0x3FFFFF
        if kind == 0:
            if 0 < val <= len(self.formid_array):
                return self.formid_array[val - 1]
            return 0
        if kind == 1:
            return val                     # Fallout4.esm
        if kind == 2:
            return 0xFF000000 | val        # created in-game
        return 0

    def plugin_key(self, formid: int):
        """Load-order form ID -> (plugin filename, local id) or None."""
        idx = formid >> 24
        if idx == 0xFE:
            li = (formid >> 12) & 0xFFF
            if li < len(self.light_plugins):
                return self.light_plugins[li], formid & 0xFFF
            return None
        if idx == 0xFF:
            return None
        if idx < len(self.plugins):
            return self.plugins[idx], formid & 0xFFFFFF
        return None


# ---------------------------------------------------------------------------
# Main save parser
# ---------------------------------------------------------------------------

def load_save(path: str, parse_body: bool = True) -> SaveGame:
    """Read a .fos file. Raises ParseError if it isn't a readable Fallout 4 save.

    With parse_body=False only the header, screenshot and plugin list are read,
    which is fast enough for listing many saves.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:12] != b"FO4_SAVEGAME":
        raise ParseError("Not a Fallout 4 save (missing FO4_SAVEGAME header)")
    try:
        return _parse(path, data, parse_body)
    except ParseError as e:
        raise ParseError(f"The save looks damaged or truncated: {e}") from e
    except (struct.error, IndexError, ValueError, MemoryError) as e:
        raise ParseError(f"The save looks damaged or truncated ({e})") from e


def _parse(path: str, data: bytes, parse_body: bool) -> SaveGame:
    sg = SaveGame(path=path)
    r = Reader(data, 12)
    header_size = r.u32()
    header_end = r.pos + header_size
    sg.version = r.u32()
    sg.save_number = r.u32()
    sg.player_name = r.wstring()
    sg.level = r.u32()
    sg.location = r.wstring()
    sg.playtime = r.wstring()
    sg.race = r.wstring()
    sg.sex = r.u16()
    sg.xp = r.f32()
    sg.xp_next = r.f32()
    ft = r.u64()
    try:
        sg.saved_at = _dt.datetime(1601, 1, 1) + _dt.timedelta(microseconds=ft // 10)
    except OverflowError:
        sg.saved_at = None
    sg.shot_width = r.u32()
    sg.shot_height = r.u32()
    r.pos = header_end
    n = sg.shot_width * sg.shot_height * 4
    sg.screenshot_rgba = r.bytes(n)
    sg.form_version = r.u8()
    sg.game_version = r.wstring()
    plugin_info_size = r.u32()
    plugin_end = r.pos + plugin_info_size
    count = r.u8()
    sg.plugins = [r.wstring() for _ in range(count)]
    if sg.form_version >= 68 and r.pos < plugin_end:
        count = r.u16()
        sg.light_plugins = [r.wstring() for _ in range(count)]
    r.pos = plugin_end
    sg.cosave = _try_cosave(path)
    if not parse_body:
        return sg

    # File location table
    if r.pos + 40 > len(data):
        raise ParseError("file location table is missing")
    (formid_off, _unk3, gd1_off, gd2_off, cf_off, gd3_off,
     gd1_n, gd2_n, gd3_n, cf_n) = struct.unpack_from("<10I", data, r.pos)

    for off in (formid_off, gd1_off, gd2_off, cf_off, gd3_off):
        if off > len(data):
            raise ParseError(f"section offset {off} is beyond the end of the file")

    # Form ID array
    fr = Reader(data, formid_off)
    n = fr.u32()
    if fr.pos + 4 * n > len(data):
        raise ParseError("form ID array runs past the end of the file")
    sg.formid_array = list(struct.unpack_from(f"<{n}I", data, fr.pos))

    # Global data tables
    for off, cnt in ((gd1_off, gd1_n), (gd2_off, gd2_n), (gd3_off, gd3_n)):
        gr = Reader(data, off)
        for _ in range(cnt):
            try:
                gtype = gr.u32()
                glen = gr.u32()
            except ParseError:
                break
            start = gr.pos
            try:
                if gtype == 0:
                    sg.misc_stats = _parse_misc_stats(Reader(data, start, start + glen))
                elif gtype == 3:
                    sg.globals = _parse_globals(Reader(data, start, start + glen), sg)
            except ParseError as e:
                sg.warnings.append(f"global data {gtype}: {e}")
            gr.pos = start + glen

    # Change forms
    try:
        _parse_change_forms(sg, Reader(data, cf_off), cf_n)
    except ParseError as e:
        # Keep whatever was read before the damage instead of failing outright.
        sg.warnings.append(f"change forms stopped early: {e}")

    player = next((c for c in sg.change_forms if c.formid == PLAYER_REF), None)
    if player is not None:
        try:
            sg.inventory, sg.inventory_note = parse_inventory(player.data, sg)
        except Exception as e:  # never let inventory kill the whole report
            sg.inventory, sg.inventory_note = [], f"inventory could not be decoded ({e})"
    else:
        sg.inventory_note = "player record not found in save"
    return sg


def _parse_change_forms(sg: SaveGame, cr: Reader, count: int):
    for _ in range(count):
        raw_ref = cr.refid()
        change_flags = cr.u32()
        tbyte = cr.u8()
        version = cr.u8()
        lsize = tbyte >> 6
        if lsize == 0:
            l1, l2 = cr.u8(), cr.u8()
        elif lsize == 1:
            l1, l2 = cr.u16(), cr.u16()
        else:
            l1, l2 = cr.u32(), cr.u32()
        cf_type = tbyte & 0x3F
        if cf_type not in (CF_QUST, CF_ACHR):
            cr.skip(l1)
            continue
        blob = cr.bytes(l1)
        if l2:
            try:
                blob = zlib.decompress(blob)
            except zlib.error:
                sg.warnings.append(f"could not decompress change form {raw_ref:06X}")
                continue
        fid = sg.resolve_refid(raw_ref)
        cf = ChangeForm(raw_ref, fid, change_flags, cf_type, version, blob)
        if cf_type == CF_QUST:
            try:
                sg.quests[fid] = _parse_quest(cf)
            except ParseError as e:
                sg.warnings.append(f"quest {fid:08X}: {e}")
        elif fid == PLAYER_REF:
            sg.change_forms.append(cf)


def _parse_misc_stats(r: Reader):
    out = []
    n = r.u32()
    for _ in range(n):
        name = r.wstring()
        cat = r.u8()
        val = r.i32()
        out.append((cat, name, val))
    return out


def _parse_globals(r: Reader, sg: SaveGame):
    out = {}
    n = r.vsval()
    for _ in range(n):
        raw = r.refid()
        val = r.f32()
        out[sg.resolve_refid(raw)] = val
    return out


def _parse_quest(cf: ChangeForm) -> QuestState:
    q = QuestState(cf.formid)
    r = Reader(cf.data)
    fl = cf.change_flags
    if fl & 1:
        r.skip(4)                       # generic form flags
    if fl & QF_FLAGS:
        q.flags = r.u16()
    if fl & QF_SCRIPT_DELAY:
        r.skip(4)
    if fl & QF_STAGES:
        for _ in range(r.vsval()):
            idx = r.u16()
            done = r.u8()
            q.stages[idx] = bool(done & 1)
    if fl & QF_SCRIPT:
        return q                        # layout unknown; stop here safely
    if fl & QF_OBJECTIVES:
        for _ in range(r.vsval()):
            idx = r.u32()
            state = r.u32()
            q.objectives[idx] = state
    return q


# ---------------------------------------------------------------------------
# Player inventory
# ---------------------------------------------------------------------------
# Inventory layout (player ACHR change form):
#   u32 item count
#   per item: refid(3) u32 stackCount
#     per stack: vsval extraCount, extras..., i32 count, u16 flags
# Extra data entries are type-tagged; sizes below were derived from real saves.
_FIXED_EXTRA = {0x1C: 6, 0x9F: 5, 0xA0: 4, 0x49: 1, 0x2E: 4, 0x25: 4, 0x21: 7,
                0x48: 3, 0x8E: 3, 0x2F: 4}
_MAX_STACKS = 256
_MAX_EXTRAS = 64


class _Unknown(Exception):
    pass


def _read_extra(r: Reader, stack: ItemStack, sg: SaveGame):
    t = r.u8()
    stack.extra_types.append(t)
    if t in _FIXED_EXTRA:
        r.skip(_FIXED_EXTRA[t])
        return
    if t == 0x88:                       # list of (refid, u32)
        n = r.vsval()
        r.skip(7 * n)
        return
    if t == 0x35:                       # object instance: attached mods
        r.u16()
        n = r.u32()
        if n > 200:
            raise ParseError("implausible mod count")
        for _ in range(n):
            raw = r.refid()
            r.skip(3)
            stack.mods.append(sg.resolve_refid(raw))
        return
    if t == 0x99:                       # text display data (custom names)
        r.skip(6)
        kind = r.i32()
        r.skip(2)
        if kind == -2:
            stack.custom_name = r.wstring()
        r.skip(1)
        return
    raise _Unknown(t)


def _plausible_ref(raw: int, sg: SaveGame) -> bool:
    kind = raw >> 22
    v = raw & 0x3FFFFF
    if kind == 1:
        return 0 < v < 0x400000
    if kind == 0:
        if not 0 < v <= len(sg.formid_array):
            return False
        return sg.plugin_key(sg.formid_array[v - 1]) is not None
    return False


def parse_inventory(blob: bytes, sg: SaveGame, item_filter=None):
    """Locate and decode the player's inventory inside the ACHR change form.

    Returns (items, note). `item_filter(formid) -> bool` can be supplied to
    tighten validation when game data is available.
    """
    marker = blob.find(b"BShkbAnimationGraph")
    end = marker - 10 if marker >= 10 else len(blob)

    def item_ok(p):
        if p + 7 > end:
            return False
        raw = (blob[p] << 16) | (blob[p + 1] << 8) | blob[p + 2]
        if not _plausible_ref(raw, sg):
            return False
        if item_filter and not item_filter(sg.resolve_refid(raw)):
            return False
        sc = struct.unpack_from("<I", blob, p + 3)[0]
        return 1 <= sc <= _MAX_STACKS

    budget = [200000]
    recovered = [False]

    def parse_items(p, left):
        items = []
        r = Reader(blob, p, end)
        while left:
            if not item_ok(r.pos):
                return None
            raw = r.refid()
            sc = r.u32()
            stacks = []
            for si in range(sc):
                res = parse_stack(r, sg)
                if res is None:
                    # unknown extra type: search for a consistent continuation
                    return _recover(r.pos, sc - si, left, raw, stacks, items)
                stacks.append(res)
            items.append(InventoryItem(sg.resolve_refid(raw), stacks))
            left -= 1
        return items if r.pos == end or marker < 0 else None

    def parse_stack(r, sg):
        start = r.pos
        try:
            n = r.vsval()
            if n > _MAX_EXTRAS:
                raise ParseError("too many extras")
            st = ItemStack(0, 0)
            for _ in range(n):
                _read_extra(r, st, sg)
            st.count = r.i32()
            st.flags = r.u16()
            if st.count < 0 or st.count > 100_000_000 or st.flags > 0x1FF:
                raise ParseError("bad stack trailer")
            return st
        except _Unknown:
            r.pos = start
            return None

    def _recover(stack_pos, stacks_left, left, raw, stacks_done, items_done):
        # Skip forward byte by byte looking for a trailer that lets the rest
        # of the inventory parse cleanly.
        r = Reader(blob, stack_pos, end)
        try:
            r.vsval()
        except ParseError:
            return None
        for q in range(r.pos, min(end - 5, r.pos + 4096)):
            budget[0] -= 1
            if budget[0] <= 0:
                return None
            cnt, fl = struct.unpack_from("<iH", blob, q)
            if not (0 <= cnt <= 100_000_000 and fl <= 0x1FF):
                continue
            st = ItemStack(cnt, fl)
            nxt = q + 6
            if stacks_left > 1:
                rr = Reader(blob, nxt, end)
                more = []
                ok = True
                for _ in range(stacks_left - 1):
                    s2 = parse_stack(rr, sg)
                    if s2 is None:
                        ok = False
                        break
                    more.append(s2)
                if not ok:
                    continue
                nxt = rr.pos
                tail_stacks = [st] + more
            else:
                tail_stacks = [st]
            if left - 1 == 0:
                if nxt != end and marker >= 0:
                    continue
                rest = []
            else:
                if not item_ok(nxt):
                    continue
                rest = parse_items(nxt, left - 1)
                if rest is None:
                    continue
            item = InventoryItem(sg.resolve_refid(raw), stacks_done + tail_stacks)
            recovered[0] = True
            return items_done + [item] + rest
        return None

    # Find the item-count header: scan for a u32 count followed by a valid item
    for p in range(0, max(0, end - 11)):
        n = struct.unpack_from("<I", blob, p)[0]
        if not 1 <= n <= 20000 or not item_ok(p + 4):
            continue
        budget[0] = 200000
        try:
            items = parse_items(p + 4, n)
        except ParseError:
            items = None
        if items:
            note = ("some item details used an unrecognised format and were skipped"
                    if recovered[0] else "")
            return items, note
    return [], "inventory layout not recognised in this save"


# ---------------------------------------------------------------------------
# F4SE co-save
# ---------------------------------------------------------------------------

def _try_cosave(save_path: str):
    base, _ = os.path.splitext(save_path)
    path = base + ".f4se"
    if not os.path.exists(path):
        return None
    try:
        return load_cosave(path)
    except Exception:
        return None


def _ver(v: int) -> str:
    return f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 4) & 0xFFF}"


def load_cosave(path: str) -> CoSave:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"F4SE":
        raise ParseError("not an F4SE co-save")
    fmt, f4se_ver, runtime, nplugins = struct.unpack_from("<IIII", data, 4)
    r = Reader(data, 20)
    sigs, names = [], []
    for _ in range(nplugins):
        sig, nchunks, length = r.u32(), r.u32(), r.u32()
        start = r.pos
        sigs.append(sig)
        cr = Reader(data, start, start + length)
        for _ in range(nchunks):
            ctype, _cver, clen = cr.u32(), cr.u32(), cr.u32()
            cstart = cr.pos
            if sig == 0 and ctype == 0x504C474E:  # 'PLGN' (written as NGLP)
                pr = Reader(data, cstart, cstart + clen)
                try:
                    cnt = pr.u16()
                    for _ in range(cnt):
                        if pr.u8() == 0xFE:
                            pr.u16()
                        names.append(pr.wstring())
                except ParseError:
                    pass
            cr.pos = cstart + clen
        r.pos = start + length
    return CoSave(path, fmt, _ver(f4se_ver), _ver(runtime), sigs, names)


if __name__ == "__main__":  # pragma: no cover - quick manual check
    s = load_save(sys.argv[1])
    print(s.player_name, s.level, s.location, len(s.quests), "quests,", len(s.inventory), "items")
