"""Builders for tiny synthetic plugin / string / BA2 files used by the tests."""
import os
import struct
import zlib


def sub(t: bytes, data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        return b"XXXX" + struct.pack("<HI", 4, len(data)) + t + struct.pack("<H", 0) + data
    return t + struct.pack("<H", len(data)) + data


def zs(s: str) -> bytes:
    return s.encode("utf-8") + b"\0"


def record(rtype: bytes, fid: int, body: bytes, compress=False, flags=0) -> bytes:
    if compress:
        body = struct.pack("<I", len(body)) + zlib.compress(body)
        flags |= 0x00040000
    return rtype + struct.pack("<IIIIHH", len(body), flags, fid, 0, 131, 0) + body


def group(label: bytes, content: bytes) -> bytes:
    return b"GRUP" + struct.pack("<I", 24 + len(content)) + label + struct.pack("<iHHHH", 0, 0, 0, 0, 0) + content


def tes4(masters=(), localized=False, light=False) -> bytes:
    body = sub(b"HEDR", struct.pack("<fII", 1.0, 0, 0x800))
    for m in masters:
        body += sub(b"MAST", zs(m)) + sub(b"DATA", b"\0" * 8)
    flags = (0x80 if localized else 0) | (0x200 if light else 0) | 1
    return record(b"TES4", 0, body, flags=flags)


class Strings:
    def __init__(self):
        self.tables = {"strings": {}, "dlstrings": {}, "ilstrings": {}}
        self.next = 1

    def add(self, text, kind="strings"):
        sid = self.next
        self.next += 1
        self.tables[kind][sid] = text
        return struct.pack("<I", sid)

    def build(self, kind):
        entries = self.tables[kind]
        data = b""
        directory = b""
        for sid, text in entries.items():
            directory += struct.pack("<II", sid, len(data))
            raw = text.encode("utf-8") + b"\0"
            data += raw if kind == "strings" else struct.pack("<I", len(raw)) + raw
        return struct.pack("<II", len(entries), len(data)) + directory + data


def quest(fid, edid, name, qtype, stages, objectives, lstr=None, compress=True):
    """stages: {idx: [(flags, text)]}, objectives: {idx: text}"""
    L = (lambda t, k="strings": lstr.add(t, k)) if lstr else (lambda t, k="strings": zs(t))
    body = sub(b"EDID", zs(edid)) + sub(b"FULL", L(name))
    body += sub(b"DNAM", struct.pack("<HBBfB3x", 0, 50, 0, 0.0, qtype))
    body += sub(b"NEXT", b"")
    for idx, logs in stages.items():
        body += sub(b"INDX", struct.pack("<HBB", idx, 0, 0))
        for fl, text in logs:
            body += sub(b"QSDT", bytes([fl]))
            body += sub(b"CTDA", b"\0" * 32)
            if text:
                body += sub(b"CNAM", L(text, "dlstrings"))
    for idx, text in objectives.items():
        body += sub(b"QOBJ", struct.pack("<H", idx)) + sub(b"FNAM", struct.pack("<I", 0))
        body += sub(b"NNAM", L(text))
        body += sub(b"QSTA", struct.pack("<iI", 0, 0))
    body += sub(b"ANAM", struct.pack("<I", 1))
    body += sub(b"ALST", struct.pack("<I", 0)) + sub(b"ALID", zs("Thing")) + sub(b"FNAM", struct.pack("<I", 0))
    return record(b"QUST", fid, body, compress=compress)


def item(rtype, fid, edid, name, lstr=None):
    L = (lambda t: lstr.add(t)) if lstr else zs
    return record(rtype, fid, sub(b"EDID", zs(edid)) + sub(b"FULL", L(name)))


def ba2(files: dict) -> bytes:
    """GNRL archive; files = {path: bytes}. Every other file is zlib-packed."""
    names = list(files)
    header_len = 24 + 36 * len(names)
    blobs = []
    offset = header_len
    entries = b""
    for i, n in enumerate(names):
        raw = files[n]
        packed = zlib.compress(raw) if i % 2 == 0 else raw
        entries += struct.pack("<I4sIIQIII", 0, b"strs", 0, 0, offset,
                               len(packed) if i % 2 == 0 else 0, len(raw), 0xBAADF00D)
        blobs.append(packed)
        offset += len(packed)
    name_table = b"".join(struct.pack("<H", len(n)) + n.encode() for n in names)
    head = b"BTDX" + struct.pack("<I", 1) + b"GNRL" + struct.pack("<IQ", len(names), offset)
    return head + entries + b"".join(blobs) + name_table


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


# ---------------------------------------------------------------------------
# Synthetic game Data folder
# ---------------------------------------------------------------------------

def build_data_dir(root):
    """Write a tiny but realistic Data folder: a localized Fallout4.esm with
    its string tables in a BA2, a DLC master with odd filename case, and a
    light (ESL) Creation Club plugin."""
    s = Strings()
    big_vmad = sub(b"VMAD", b"\x01" * 70000)
    # splice an XXXX-sized VMAD into MQ106 (uncompressed to keep it simple)
    mq106_plain = quest(0x229E7, "MQ106", "Reunions", 1,
                        {10: [(0, "I need to find Kellogg.")], 1000: [(0, "I tracked Kellogg to Fort Hagen.")]},
                        {200: "Find Kellogg in <Alias=FortHagen>"}, lstr=s, compress=False)
    hdr, body = mq106_plain[:24], mq106_plain[24:]
    edid_len = 6 + struct.unpack_from("<H", body, 4)[0]
    body = body[:edid_len] + big_vmad + body[edid_len:]
    mq106 = b"QUST" + struct.pack("<I", len(body)) + hdr[8:] + body
    ms17 = quest(0x22A08, "MS17", "Human Error", 7,
                 {900: [(1, "I helped Amelia escape the Compound.")]}, {200: "Talk to Honest Dan"}, lstr=s)
    mq205 = quest(0x229EB, "MQ205", "Hunter/Hunted", 1, {10: [(0, "")]}, {10: "Hunt a Courser"}, lstr=s)
    mq206 = quest(0x0B1752, "MQ206", "The Molecular Level", 1, {10: [(0, "")]}, {10: "Build it"}, lstr=s)
    dialogue = quest(0x21AD3, "DialogueGenericRaider", "Raider Combat Lines", 0, {}, {}, lstr=s)
    esm = (tes4(localized=True) +
           group(b"GLOB", record(b"GLOB", 0x39, sub(b"EDID", zs("GameDaysPassed")))) +
           group(b"QUST", mq106 + ms17 + mq205 + mq206 + dialogue +
                 group(b"\xe7\x29\x02\x00", b"")) +          # nested child group is skipped
           group(b"WEAP", item(b"WEAP", 0x14831C, "CombatShotgun", "Combat Shotgun", s)) +
           group(b"MISC", item(b"MISC", 0x0F, "Caps001", "Bottle Cap", s)) +
           group(b"ALCH", item(b"ALCH", 0x23736, "Stimpak", "Stimpak", s) +
                 item(b"ALCH", 0x23742, "RadAway", "RadAway", s)) +
           group(b"OMOD", item(b"OMOD", 0x1234AB, "mod_LongBarrel", "Long Barrel", s)) +
           group(b"STAT", record(b"STAT", 0x1234, sub(b"EDID", zs("Rock")))))
    write(os.path.join(root, "Fallout4.esm"), esm)
    write(os.path.join(root, "Fallout4 - Interface.ba2"), ba2({
        "Strings\\Fallout4_en.STRINGS": s.build("strings"),
        "Strings\\Fallout4_en.DLSTRINGS": s.build("dlstrings"),
        "Strings\\Fallout4_en.ILSTRINGS": s.build("ilstrings"),
    }))
    robot = tes4(masters=["Fallout4.esm"]) + group(b"QUST", quest(
        0x01000801, "DLC01MQ01", "Mechanical Menace", 8, {10: [(0, "A robot attack!")]},
        {10: "Investigate the distress call"}))
    write(os.path.join(root, "dlcrobot.ESM"), robot)          # odd case on purpose
    cc = tes4(masters=["Fallout4.esm"], light=True) + group(b"QUST", quest(
        0x010000E7, "ccOTM_Quest", "Remnants Quest", 7, {10: [(1, "Done.")]}, {0: "Do the thing"}))
    write(os.path.join(root, "ccOTMFO4001-Remnants.esl"), cc)


# ---------------------------------------------------------------------------
# Synthetic save games
# ---------------------------------------------------------------------------

def wstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def vsval(n: int) -> bytes:
    if n < 0x40:
        return bytes([n << 2])
    if n < 0x4000:
        return struct.pack("<H", (n << 2) | 1)
    v = (n << 2) | 2
    return bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])


def refid(raw: int) -> bytes:
    return bytes([(raw >> 16) & 0xFF, (raw >> 8) & 0xFF, raw & 0xFF])


def esm_ref(local: int) -> int:
    """Save RefID pointing straight at a Fallout4.esm form."""
    return (1 << 22) | local


def change_form(raw_ref, change_flags, cf_type, data, compress=False):
    l2 = 0
    if compress:
        l2 = len(data)
        data = zlib.compress(data)
    return (refid(raw_ref) + struct.pack("<I", change_flags) + bytes([(2 << 6) | cf_type, 1]) +
            struct.pack("<II", len(data), l2) + data)


def quest_change(raw_ref, flags=0x0001, stages=None, objectives=None, compress=False):
    """stages: {idx: done}, objectives: {idx: state}"""
    cflags = 1 << 1
    body = struct.pack("<H", flags)
    if stages:
        cflags |= 1 << 31
        body += vsval(len(stages)) + b"".join(struct.pack("<HB", i, int(d)) for i, d in stages.items())
    if objectives:
        cflags |= 1 << 29
        body += vsval(len(objectives)) + b"".join(struct.pack("<II", i, s) for i, s in objectives.items())
    return change_form(raw_ref, cflags, 8, body, compress)


def stack(count, flags=0, extras=()):
    return vsval(len(extras)) + b"".join(extras) + struct.pack("<iH", count, flags)


def extra_name(name):
    return b"\x99" + b"\0" * 6 + struct.pack("<i", -2) + b"\0\0" + wstr(name) + b"\0"


def extra_mods(*raws):
    return b"\x35" + struct.pack("<HI", 0, len(raws)) + b"".join(refid(r) + b"\0\0\0" for r in raws)


def inv_item(raw, *stacks):
    return refid(raw) + struct.pack("<I", len(stacks)) + b"".join(stacks)


def player_blob(items):
    return (b"\0" * 16 + struct.pack("<I", len(items)) + b"".join(items) +
            b"\0" * 10 + b"BShkbAnimationGraph" + b"\0" * 8)


def filetime(year, month, day, hour=0, minute=0):
    import datetime as dt
    delta = dt.datetime(year, month, day, hour, minute) - dt.datetime(1601, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 10_000_000


def build_save(*, name="Nora", level=25, location="Sanctuary Hills",
               playtime="1d.16h.43m.1 days.16 hours.43 minutes", plugins=("Fallout4.esm",),
               light_plugins=(), formids=(), misc_stats=(), globals_=(), change_forms=(),
               shot=(4, 2), form_version=69, game_version="1.11.169.0", save_number=89,
               saved=(2026, 11, 1, 18, 30)) -> bytes:
    w, h = shot
    header = (struct.pack("<II", 15, save_number) + wstr(name) + struct.pack("<I", level) +
              wstr(location) + wstr(playtime) + wstr("HumanRace") +
              struct.pack("<HffQII", 1, 1234.0, 2000.0, filetime(*saved), w, h))
    pixels = bytes((x * 37 + y) & 0xFF for y in range(h) for x in range(w * 4))
    pinfo = bytes([len(plugins)]) + b"".join(wstr(p) for p in plugins)
    if form_version >= 68:
        pinfo += struct.pack("<H", len(light_plugins)) + b"".join(wstr(p) for p in light_plugins)
    head = (b"FO4_SAVEGAME" + struct.pack("<I", len(header)) + header + pixels +
            bytes([form_version]) + wstr(game_version) + struct.pack("<I", len(pinfo)) + pinfo)

    stats = struct.pack("<I", len(misc_stats)) + b"".join(
        wstr(n) + bytes([c]) + struct.pack("<i", v) for c, n, v in misc_stats)
    globs = vsval(len(globals_)) + b"".join(refid(r) + struct.pack("<f", v) for r, v in globals_)
    gd1 = (struct.pack("<II", 0, len(stats)) + stats + struct.pack("<II", 3, len(globs)) + globs)
    cfs = b"".join(change_forms)
    fids = struct.pack("<I", len(formids)) + b"".join(struct.pack("<I", f) for f in formids)

    flt_len = 40
    gd1_off = len(head) + flt_len
    cf_off = gd1_off + len(gd1)
    fid_off = cf_off + len(cfs)
    flt = struct.pack("<10I", fid_off, 0, gd1_off, fid_off, cf_off, fid_off,
                      2, 0, 0, len(change_forms))
    return head + flt + gd1 + cfs + fids


def build_cosave(plugin_names=("Fallout4.esm",)) -> bytes:
    plgn = struct.pack("<H", len(plugin_names)) + b"".join(bytes([i]) + wstr(n)
                                                         for i, n in enumerate(plugin_names))
    chunk = struct.pack("<III", 0x504C474E, 1, len(plgn)) + plgn
    plugin = struct.pack("<III", 0, 1, len(chunk)) + chunk
    return (b"F4SE" + struct.pack("<IIII", 2, (0 << 24) | (6 << 16) | (23 << 4), (1 << 24) | (10 << 16) | (163 << 4), 1)
            + plugin)


# Standard scenario used by most tests ---------------------------------------
CAPS, STIMPAK, RADAWAY, SHOTGUN, LONG_BARREL = 0x0F, 0x23736, 0x23742, 0x14831C, 0x1234AB
MISSING_MOD = "MyQuestMod.esp"


def standard_save(**overrides) -> bytes:
    """A mid-game save touching every feature the tracker reports on."""
    inventory = player_blob([
        inv_item(esm_ref(CAPS), stack(1234)),
        inv_item(esm_ref(STIMPAK), stack(7)),
        # An extra-data type the parser doesn't know: forces the recovery path.
        inv_item(esm_ref(RADAWAY), stack(3, extras=[b"\x77" + b"\xff" * 5])),
        inv_item(esm_ref(SHOTGUN),
                 stack(1, flags=1, extras=[extra_mods(esm_ref(LONG_BARREL)), b"\x49\x00"]),
                 stack(1, extras=[extra_name("Boomstick <3")])),
    ])
    kw = dict(
        plugins=("Fallout4.esm", "DLCRobot.esm", MISSING_MOD),
        light_plugins=("ccOTMFO4001-Remnants.esl",),
        formids=(0x01000801, 0xFE0000E7, 0x02000ABC),
        misc_stats=[(1, "Game Difficulty", 2), (0, "Days Passed", 12), (1, "Main Quests Completed", 5),
                    (1, "Brotherhood of Steel Quests Completed", 3), (0, "Most Caps Carried", 5000),
                    (2, "People Killed", 100), (0, "Locations Discovered", 40)],
        globals_=[(esm_ref(0x39), 12.0)],
        change_forms=[
            change_form(esm_ref(0x9999), 0, 0, b"\0" * 12),                       # REFR: skipped
            quest_change(esm_ref(0x229E7), stages={10: True}, objectives={200: 1}),  # MQ106 active
            quest_change(esm_ref(0x22A08), flags=0x0002, stages={900: True}),      # MS17 done
            quest_change(esm_ref(0x229EB), flags=0x0002, stages={10: True}, compress=True),  # MQ205
            quest_change(esm_ref(0x21AD3)),                                        # dialogue: hidden
            quest_change(1, stages={10: True}, objectives={10: 1}),               # DLC01MQ01 active
            quest_change(2, stages={10: True}),                                   # CC quest done
            quest_change(3, objectives={0: 1}),                                   # quest from a missing mod
            change_form(esm_ref(0x14), 0, 1, inventory, compress=True),           # player
        ],
    )
    kw.update(overrides)
    return build_save(**kw)


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------

class IsolatedDirs:
    """Point the config and cache folders at a temp dir so tests never read or
    write the developer's real settings. Use as a context manager or call
    start()/stop() from setUp/tearDown."""

    VARS = ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA")

    def __init__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.config = os.path.join(self.root, "config")
        self.cache = os.path.join(self.root, "cache")
        self._old = {}

    def start(self):
        for v in self.VARS:
            self._old[v] = os.environ.get(v)
        os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"] = self.config
        os.environ["XDG_CACHE_HOME"] = os.environ["LOCALAPPDATA"] = self.cache
        return self

    def stop(self):
        for v, old in self._old.items():
            if old is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = old
        self._tmp.cleanup()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def write_standard(folder, cosave=True, **overrides):
    """Write the standard save (and a matching F4SE co-save) into folder; return its path."""
    path = os.path.join(folder, "Save89_ABCD1234_0_4E6F7261_SanctuaryHills_000140_20261101183000_25_1.fos")
    write(path, standard_save(**overrides))
    if cosave:
        write(os.path.splitext(path)[0] + ".f4se", build_cosave(["Fallout4.esm", "DLCRobot.esm"]))
    return path
