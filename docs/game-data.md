# Game data: plugins, strings and archives

`gamedata.py` reads just enough of three Bethesda formats to put names to the numbers in a save.

## Plugins (`.esm` / `.esp` / `.esl`)

Plugins use the TES4 record format: a `TES4` header record followed by top-level `GRUP`s.

- **Header flags**: `0x80` = localized (strings live in `.STRINGS` files, and text fields hold a
  `u32` string ID), `0x200` = light plugin (form IDs use only the low 12 bits).
- **Masters**: the `MAST` subrecords in the header. The top byte of a record's form ID indexes this
  list. An index past the end means the record belongs to the plugin itself.
- **Records**: 24-byte header (`type, dataSize, flags, formID, …`). Flag `0x20` means deleted
  (skipped). Flag `0x40000` means the body is `u32 size` + zlib data.
- **Subrecords**: `type (4), u16 size, data`. An `XXXX` subrecord holds a `u32` size for the next
  subrecord when it exceeds 64 KiB.

Only these top-level groups are walked:

| Group | Fields used |
|---|---|
| `QUST` | `EDID`, `FULL` (name), `DNAM` (flags, type), `FLTR`, `INDX` (stage), `QSDT` (log entry flags: `0x01` completes the quest, `0x02` fails it), `CNAM` (journal text), `QOBJ`/`NNAM` (objective index and text) |
| `GLOB` | `EDID`, so `<Global=…>` tokens in quest text can be filled in from the save |
| `WEAP` `ARMO` `AMMO` `ALCH` `MISC` `BOOK` `KEYM` `NOTE` `OMOD` | `EDID`, `FULL`, for inventory names and categories |

The `DNAM` type byte gives the quest's Pip-Boy group (1 Main, 2 Brotherhood, 3 Institute,
4 Minutemen, 5 Railroad, 6 Misc, 7 Side, 8+ DLC). `analysis.quest_line` combines it with the editor ID
prefix and the owning plugin to pick a questline.

## String tables

Each localized plugin has three tables, named `<plugin>_<language>.STRINGS`, `.DLSTRINGS` and `.ILSTRINGS`:

```
u32 count
u32 dataSize
count × (u32 id, u32 offset)     offsets are relative to the start of the data block
data
```

`.STRINGS` entries are null-terminated. `.DLSTRINGS` and `.ILSTRINGS` entries are `u32 length` + text.
Text is decoded as UTF-8, with cp1252 as the fallback.

Tables are taken from `Data/Strings/` if they exist there as loose files. Otherwise they come from
any general (`GNRL`) BA2 archive in `Data`. Texture, mesh, sound and voice archives are skipped.

## BA2 archives

Only general archives are read:

```
"BTDX", u32 version, "GNRL", u32 fileCount, u64 nameTableOffset
[version 2/3: 8 extra bytes; version 3: 4 more]
fileCount × 36-byte entries: u32 nameHash, char[4] ext, u32 dirHash, u32 flags,
                             u64 offset, u32 packedSize (0 = stored), u32 size, u32 align
name table: fileCount × (u16 length, path)
```

Packed entries are zlib streams. Version 1 (original release) and versions 7/8 (next-gen update)
share the version-1 layout.

## The cache

Reading `Fallout4.esm` takes a while, so each plugin's index is cached as JSON in the cache folder:

```
<plugin filename>_<size>_<mtime>_<language>_v<PARSER_VERSION>.json
```

A changed plugin gets a new key, and older entries for the same plugin are deleted when the new one is
written. Writes are atomic (temp file + rename), and a damaged cache file is simply rebuilt. Bump
`PARSER_VERSION` in `gamedata.py` whenever the index structure or the parsing rules change.

## Locating things

- **Steam libraries**: well-known Steam roots on Linux (native, Debian, Flatpak, Snap), Windows and macOS,
  plus every `path` listed in their `steamapps/libraryfolders.vdf`.
- **Data folder**: `<library>/steamapps/common/Fallout 4/Data` containing `Fallout4.esm`, plus common GOG paths.
- **Saves**: the Proton prefix for app 377160 in each library, `~/Documents/My Games/Fallout4/Saves`
  and its OneDrive equivalent.

All filename lookups inside `Data` are case-insensitive, because Linux filesystems are case-sensitive
and plugin names in a save don't always match the case on disk.
