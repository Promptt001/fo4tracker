"""Read quest and item definitions from the game's own data files.

The save only stores numbers (form IDs, stage numbers, objective indexes).
Names, objective text and journal entries live in Fallout4.esm, the DLC/
Creation Club plugins and their localized string tables, which ship inside
the .ba2 archives. This module reads just enough of those formats:

* ESM/ESP/ESL plugins (TES4 record format, zlib-compressed records)
* .STRINGS / .DLSTRINGS / .ILSTRINGS localized string tables
* BA2 "GNRL" archives (to pull the string tables out)

Results are cached per plugin (as JSON in the user cache folder), so only the
first run is slow.
"""
from __future__ import annotations

import json
import mmap
import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field

# Bump when the cached index format or parsing rules change; old cache files
# are then ignored and pruned.
PARSER_VERSION = 4

ITEM_TYPES = {
    b"WEAP": "Weapons", b"ARMO": "Apparel", b"AMMO": "Ammo", b"ALCH": "Aid",
    b"MISC": "Misc & Junk", b"BOOK": "Books & Magazines", b"KEYM": "Keys",
    b"NOTE": "Holotapes & Notes", b"OMOD": "Mods",
}
WANTED_GROUPS = set(ITEM_TYPES) | {b"QUST", b"GLOB"}

# QUST DNAM "Type" field -> Pip-Boy grouping
QUEST_TYPES = {
    1: "Main Quest", 2: "Brotherhood of Steel", 3: "Institute", 4: "Minutemen",
    5: "Railroad", 6: "Miscellaneous", 7: "Side Quests", 8: "Automatron",
    9: "Wasteland Workshop", 10: "Far Harbor", 11: "Nuka-World",
    12: "Contraptions Workshop", 13: "Vault-Tec Workshop",
}

LOG_COMPLETE = 0x01
LOG_FAIL = 0x02


@dataclass
class QuestDef:
    edid: str = ""
    name: str = ""
    qtype: int = 0
    dnam_flags: int = 0
    filter: str = ""
    stages: dict = field(default_factory=dict)      # idx -> list[(flags, text)]
    objectives: dict = field(default_factory=dict)  # idx -> text
    plugin: str = ""

    @property
    def has_journal(self):
        return bool(self.objectives) or any(t for logs in self.stages.values() for _f, t in logs)


# ---------------------------------------------------------------------------
# Locating the game
# ---------------------------------------------------------------------------

def _steam_roots():
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, ".steam", "debian-installation"),
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
        os.path.join(home, "snap", "steam", "common", ".local", "share", "Steam"),
        os.path.join(home, "Library", "Application Support", "Steam"),
    ]
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(os.path.join(os.environ[env], "Steam"))
    libs = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith('"path"'):
                        p = line.split('"')[3].replace("\\\\", "\\")
                        libs.append(p)
        except OSError:
            pass
    seen, out = set(), []
    for lib in libs:
        k = os.path.realpath(lib)
        if k not in seen and os.path.isdir(lib):
            seen.add(k)
            out.append(lib)
    return out


def find_data_dirs():
    found = []
    for lib in _steam_roots():
        d = os.path.join(lib, "steamapps", "common", "Fallout 4", "Data")
        if os.path.isfile(os.path.join(d, "Fallout4.esm")):
            found.append(d)
    for extra in (r"C:\GOG Games\Fallout 4\Data", r"C:\Program Files (x86)\GOG Galaxy\Games\Fallout 4\Data"):
        if os.path.isfile(os.path.join(extra, "Fallout4.esm")):
            found.append(extra)
    return found


def find_save_dirs():
    found = []
    for lib in _steam_roots():
        d = os.path.join(lib, "steamapps", "compatdata", "377160", "pfx", "drive_c", "users",
                         "steamuser", "Documents", "My Games", "Fallout4", "Saves")
        if os.path.isdir(d):
            found.append(d)
    win = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Fallout4", "Saves")
    if os.path.isdir(win):
        found.append(win)
    od = os.path.join(os.path.expanduser("~"), "OneDrive", "Documents", "My Games", "Fallout4", "Saves")
    if os.path.isdir(od):
        found.append(od)
    return found


def latest_save(dirs=None):
    """Newest .fos file in the given folders (default: the auto-detected save folders)."""
    best = None
    for d in (find_save_dirs() if dirs is None else dirs):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(".fos"):
                continue
            f = os.path.join(d, name)
            try:
                m = os.path.getmtime(f)
            except OSError:      # deleted while we were looking
                continue
            if best is None or m > best[0]:
                best = (m, f)
    return best[1] if best else None


def cache_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base, "fo4tracker")
    os.makedirs(d, exist_ok=True)
    return d


_CACHE_NAME = re.compile(r"(?P<plugin>.+)_\d+_\d+_[^_]+_v\d+\.(?:pkl|json)$")


def clear_cache():
    """Delete every cached plugin index. Returns the number of files removed."""
    n = 0
    try:
        d = cache_dir()
        names = os.listdir(d)
    except OSError:
        return 0
    for f in names:
        if _CACHE_NAME.match(f):
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except OSError:
                pass
    return n


def _prune_cache(d, plugin, keep):
    """Remove older cache files for this plugin (other sizes, dates or versions)."""
    try:
        names = os.listdir(d)
    except OSError:
        return
    for f in names:
        m = _CACHE_NAME.match(f)
        if f != keep and m and m.group("plugin").lower() == plugin.lower():
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def _index_to_json(idx):
    return {
        "version": PARSER_VERSION,
        "quests": [[k[0], k[1], q.edid, q.name, q.qtype, q.dnam_flags, q.filter, q.plugin,
                    [[st, [list(x) for x in logs]] for st, logs in q.stages.items()],
                    [[o, t] for o, t in q.objectives.items()]]
                   for k, q in idx["quests"].items()],
        "items": [[k[0], k[1], *v] for k, v in idx["items"].items()],
        "globals": [[k[0], k[1], v] for k, v in idx["globals"].items()],
    }


def _index_from_json(d):
    if d.get("version") != PARSER_VERSION:
        raise ValueError("cache version mismatch")
    quests = {}
    for plug, local, edid, name, qtype, dflags, filt, plugin, stages, objectives in d["quests"]:
        quests[(plug, local)] = QuestDef(
            edid=edid, name=name, qtype=qtype, dnam_flags=dflags, filter=filt, plugin=plugin,
            stages={st: [tuple(x) for x in logs] for st, logs in stages},
            objectives={o: t for o, t in objectives})
    return {"quests": quests,
            "items": {(p, n): (t, e, nm) for p, n, t, e, nm in d["items"]},
            "globals": {(p, n): e for p, n, e in d["globals"]}}


# ---------------------------------------------------------------------------
# BA2 archives
# ---------------------------------------------------------------------------

class BA2:
    """Minimal reader for Fallout 4 GNRL archives."""

    def __init__(self, path):
        self.path = path
        self.files = {}   # normalized lowercase path -> (offset, packed, unpacked)
        with open(path, "rb") as fh:
            head = fh.read(24)
            if len(head) < 24 or head[:4] != b"BTDX":
                return
            version = struct.unpack_from("<I", head, 4)[0]
            kind = head[8:12]
            nfiles, name_off = struct.unpack_from("<IQ", head, 12)
            if kind != b"GNRL":
                return
            extra = 8 if version in (2, 3) else 0
            if version == 3:
                extra += 4
            fh.seek(24 + extra)
            entries = fh.read(36 * nfiles)
            fh.seek(name_off)
            names = fh.read()
        p = 0
        for i in range(nfiles):
            if p + 2 > len(names):
                break
            ln = struct.unpack_from("<H", names, p)[0]
            name = names[p + 2:p + 2 + ln].decode("utf-8", "replace")
            p += 2 + ln
            _nh, _ext, _dh, _fl, off, packed, unpacked, _al = struct.unpack_from("<I4sIIQIII", entries, 36 * i)
            self.files[name.replace("\\", "/").lower()] = (off, packed, unpacked)

    def read(self, name):
        off, packed, unpacked = self.files[name]
        with open(self.path, "rb") as fh:
            fh.seek(off)
            if packed:
                return zlib.decompress(fh.read(packed))
            return fh.read(unpacked)


# ---------------------------------------------------------------------------
# Localized strings
# ---------------------------------------------------------------------------

def _decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


def parse_string_table(data: bytes, kind: str) -> dict:
    """kind: 'strings' (null-terminated) or 'dlstrings'/'ilstrings' (length-prefixed)."""
    count, _size = struct.unpack_from("<II", data, 0)
    base = 8 + 8 * count
    out = {}
    for i in range(count):
        sid, off = struct.unpack_from("<II", data, 8 + 8 * i)
        p = base + off
        if kind == "strings":
            e = data.find(b"\0", p)
            out[sid] = _decode(data[p:e if e >= 0 else len(data)])
        else:
            ln = struct.unpack_from("<I", data, p)[0]
            out[sid] = _decode(data[p + 4:p + 4 + ln].rstrip(b"\0"))
    return out


class StringSource:
    """Finds string tables loose in Data/Strings or inside any GNRL BA2."""

    def __init__(self, data_dir, language="en", log=None):
        self.data_dir = data_dir
        self.language = language
        self.log = log or (lambda m: None)
        self._index = None

    def _build(self):
        idx = {}
        try:
            names = sorted(os.listdir(self.data_dir))
        except OSError:
            names = []
        # os.listdir rather than glob: install paths like "[GOG] Fallout 4" would
        # otherwise be read as glob patterns and match nothing.
        for fname in names:
            low = fname.lower()
            if not low.endswith(".ba2"):
                continue
            ba2 = os.path.join(self.data_dir, fname)
            if "texture" in low or "sound" in low or "voice" in low or "mesh" in low:
                continue   # never contain string tables; skip big name tables
            try:
                arc = BA2(ba2)
            except (OSError, struct.error):
                continue
            for name in arc.files:
                if name.startswith("strings/"):
                    idx.setdefault(name, arc)
        self._index = idx

    def tables_for(self, plugin):
        stem = os.path.splitext(plugin)[0].lower()
        out = {}
        for kind in ("strings", "dlstrings", "ilstrings"):
            fname = f"{stem}_{self.language}.{kind}"
            loose = _ci_path(os.path.join(self.data_dir, "Strings"), fname)
            data = None
            if loose:
                with open(loose, "rb") as fh:
                    data = fh.read()
            else:
                if self._index is None:
                    self._build()
                arc = self._index.get("strings/" + fname)
                if arc:
                    try:
                        data = arc.read("strings/" + fname)
                    except (OSError, zlib.error) as e:
                        self.log(f"could not extract {fname} from {os.path.basename(arc.path)}: {e}")
            if data:
                try:
                    out[kind] = parse_string_table(data, kind)
                except struct.error:
                    self.log(f"could not read {fname}")
        return out


def _ci_path(folder, name):
    """Case-insensitive file lookup (Linux filesystems are case-sensitive)."""
    p = os.path.join(folder, name)
    if os.path.exists(p):
        return p
    try:
        for f in os.listdir(folder):
            if f.lower() == name.lower():
                return os.path.join(folder, f)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Plugin reader
# ---------------------------------------------------------------------------

def _subrecords(data):
    p, n = 0, len(data)
    big = None
    while p + 6 <= n:
        t = data[p:p + 4]
        size = struct.unpack_from("<H", data, p + 4)[0]
        p += 6
        if t == b"XXXX":
            big = struct.unpack_from("<I", data, p)[0]
            p += size
            continue
        if big is not None:
            size, big = big, None
        yield t, data[p:p + size]
        p += size


def _zstr(b):
    return _decode(b.split(b"\0", 1)[0])


class PluginReader:
    def __init__(self, path, light_names=(), strings: StringSource | None = None, log=None):
        self.path = path
        self.name = os.path.basename(path)
        self.light_names = {n.lower() for n in light_names}
        self.strings = strings
        self.log = log or (lambda m: None)
        self.masters = []
        self.localized = False
        self.is_light = self.name.lower().endswith(".esl")
        self.tables = {}

    def _lstr(self, b, prefer="strings"):
        if not self.localized:
            return _zstr(b)
        if len(b) < 4:
            return ""
        sid = struct.unpack_from("<I", b, 0)[0]
        if sid == 0:
            return ""
        order = [prefer] + [k for k in ("strings", "dlstrings", "ilstrings") if k != prefer]
        for k in order:
            t = self.tables.get(k)
            if t and sid in t:
                return t[sid]
        return ""

    def _key(self, fid):
        top = fid >> 24
        owner = self.masters[top] if top < len(self.masters) else self.name
        low = owner.lower()
        light = low.endswith(".esl") or low in self.light_names or (owner == self.name and self.is_light)
        return low, fid & (0xFFF if light else 0xFFFFFF)

    def read(self):
        out = {"quests": {}, "items": {}, "globals": {}}
        with open(self.path, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                self._read(mm, out)
            finally:
                mm.close()
        return out

    def _read(self, mm, out):
        if mm[:4] != b"TES4":
            raise ValueError(f"{self.name} is not a plugin file")
        size, flags = struct.unpack_from("<II", mm, 4)
        if flags & 0x200:
            self.is_light = True
        self.localized = bool(flags & 0x80)
        for t, b in _subrecords(mm[24:24 + size]):
            if t == b"MAST":
                self.masters.append(_zstr(b))
        if self.localized and self.strings:
            self.tables = self.strings.tables_for(self.name)
            if not self.tables:
                self.log(f"no string tables found for {self.name}; names will be missing")
        p = 24 + size
        n = len(mm)
        while p + 24 <= n:
            if mm[p:p + 4] != b"GRUP":
                break
            gsize = struct.unpack_from("<I", mm, p + 4)[0]
            label = mm[p + 8:p + 12]
            if label in WANTED_GROUPS:
                self._walk(mm, p + 24, p + gsize, out)
            p += gsize

    def _walk(self, mm, p, end, out):
        while p + 24 <= end:
            rtype = mm[p:p + 4]
            if rtype == b"GRUP":
                p += struct.unpack_from("<I", mm, p + 4)[0]
                continue
            dsize, rflags, fid = struct.unpack_from("<III", mm, p + 4)
            body = mm[p + 24:p + 24 + dsize]
            p += 24 + dsize
            if rflags & 0x20:      # deleted
                continue
            if rflags & 0x00040000:
                try:
                    body = zlib.decompress(body[4:])
                except zlib.error:
                    continue
            try:
                if rtype == b"QUST":
                    q = self._quest(body)
                    out["quests"][self._key(fid)] = q
                elif rtype == b"GLOB":
                    for t, b in _subrecords(body):
                        if t == b"EDID":
                            out["globals"][self._key(fid)] = _zstr(b)
                            break
                elif rtype in ITEM_TYPES:
                    edid = name = ""
                    for t, b in _subrecords(body):
                        if t == b"EDID":
                            edid = _zstr(b)
                        elif t == b"FULL":
                            name = self._lstr(b)
                    out["items"][self._key(fid)] = (rtype.decode(), edid, name)
            except (struct.error, IndexError):
                continue

    def _quest(self, body):
        q = QuestDef(plugin=self.name)
        ctx = None
        stage = None
        obj = None
        log_flags = 0
        for t, b in _subrecords(body):
            if t == b"EDID":
                q.edid = _zstr(b)
            elif t == b"FULL" and ctx is None and not q.name:
                q.name = self._lstr(b)
            elif t == b"DNAM" and ctx is None and len(b) >= 2:
                q.dnam_flags = struct.unpack_from("<H", b, 0)[0]
                if len(b) >= 9:
                    q.qtype = b[8]      # u16 flags, u8 priority, u8 unused, f32 delay, u8 type
            elif t == b"FLTR":
                q.filter = _zstr(b)
            elif t == b"INDX" and len(b) >= 2:
                stage = struct.unpack_from("<H", b, 0)[0]
                q.stages.setdefault(stage, [])
                ctx = "stage"
            elif t == b"QSDT" and stage is not None:
                log_flags = b[0] if b else 0
                q.stages[stage].append([log_flags, ""])
                ctx = "log"
            elif t == b"CNAM" and ctx == "log" and q.stages[stage]:
                q.stages[stage][-1][1] = self._lstr(b, "dlstrings")
            elif t == b"QOBJ" and len(b) >= 2:
                obj = struct.unpack_from("<H", b, 0)[0]
                q.objectives.setdefault(obj, "")
                ctx = "obj"
            elif t == b"NNAM" and ctx == "obj" and obj is not None and not q.objectives[obj]:
                q.objectives[obj] = self._lstr(b)
            elif t in (b"ANAM", b"ALST", b"ALLS", b"ALCS"):
                ctx = None
                stage = obj = None
        q.stages = {k: [tuple(x) for x in v] for k, v in q.stages.items()}
        return q


# ---------------------------------------------------------------------------
# Combined game data for one load order
# ---------------------------------------------------------------------------

class GameData:
    def __init__(self):
        self.quests = {}     # (plugin_lower, local) -> QuestDef
        self.items = {}      # (plugin_lower, local) -> (type, edid, name)
        self.globals = {}    # (plugin_lower, local) -> edid
        self.missing_plugins = []
        self.loaded_plugins = []
        self.data_dir = None

    @classmethod
    def load(cls, data_dir, plugins, light_plugins=(), language="en", progress=None, use_cache=True):
        gd = cls()
        gd.data_dir = data_dir
        progress = progress or (lambda msg: None)
        strings = StringSource(data_dir, language, log=progress)
        cdir = None
        if use_cache:
            try:
                cdir = cache_dir()
            except OSError:          # read-only home etc.: just don't cache
                cdir = None
        order = list(plugins) + list(light_plugins)
        for name in order:
            path = _ci_path(data_dir, name)
            if not path:
                gd.missing_plugins.append(name)
                continue
            st = os.stat(path)
            base = os.path.basename(path)
            ckey = f"{base}_{st.st_size}_{int(st.st_mtime)}_{language}_v{PARSER_VERSION}.json"
            cpath = os.path.join(cdir, ckey) if cdir else None
            idx = None
            if cpath and os.path.exists(cpath):
                try:
                    with open(cpath, encoding="utf-8") as fh:
                        idx = _index_from_json(json.load(fh))
                except (OSError, ValueError, KeyError, TypeError):
                    idx = None       # damaged or old cache: rebuild it
            if idx is None:
                progress(f"Reading {name} (first time only)…")
                try:
                    idx = PluginReader(path, light_plugins, strings, log=progress).read()
                except Exception as e:  # damaged or unexpected plugin
                    progress(f"Skipped {name}: {e}")
                    continue
                if cpath:
                    tmp = f"{cpath}.{os.getpid()}.tmp"
                    try:
                        with open(tmp, "w", encoding="utf-8") as fh:
                            json.dump(_index_to_json(idx), fh, ensure_ascii=False, separators=(",", ":"))
                        os.replace(tmp, cpath)      # atomic: never leave a half-written cache
                        _prune_cache(cdir, base, ckey)
                    except OSError:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            gd.quests.update(idx["quests"])
            gd.items.update(idx["items"])
            gd.globals.update(idx["globals"])
            gd.loaded_plugins.append(name)
        return gd
