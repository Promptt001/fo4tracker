# The `.fos` save format (as used here)

This documents the parts of the Fallout 4 save format that `savefile.py` reads. Integers are
little-endian unless noted. `wstring` = `u16 length` + that many UTF-8 bytes, without a terminator.
Some parts were reverse-engineered from real saves and are marked as such.

## Layout

```
"FO4_SAVEGAME"            12 bytes magic
u32  headerSize
     ── header (headerSize bytes) ──
     u32      version
     u32      saveNumber
     wstring  playerName
     u32      playerLevel
     wstring  playerLocation
     wstring  playtime            e.g. "1d.16h.43m.1 days.16 hours.43 minutes"
     wstring  playerRace          editor ID
     u16      playerSex           0 = male, 1 = female
     f32      currentXP
     f32      xpForNextLevel
     u64      FILETIME            100 ns ticks since 1601-01-01
     u32      screenshotWidth
     u32      screenshotHeight
u8[w*h*4] screenshot          RGBA, alpha unused
u8   formVersion              68+ adds the light-plugin list (69 = current)
wstring gameVersion           e.g. "1.11.169.0"
u32  pluginInfoSize
     u8  count, wstring[count]   full plugins (ESM/ESP)
     u16 count, wstring[count]   light plugins (ESL), formVersion >= 68 only
── file location table ──
     u32 formIDArrayCountOffset
     u32 unknownTable3Offset
     u32 globalDataTable1Offset
     u32 globalDataTable2Offset
     u32 changeFormsOffset
     u32 globalDataTable3Offset
     u32 globalDataTable1Count
     u32 globalDataTable2Count
     u32 globalDataTable3Count
     u32 changeFormCount
     …   (more fields, unused here)
```

The rest of the file is reached through the offsets in the file location table.

## Form ID array

`u32 count` followed by `count × u32` load-order form IDs. Save RefIDs of kind 0 index into this
array (1-based).

## RefIDs and `vsval`

- **RefID**: 3 bytes, **big-endian**. The top 2 bits are the kind and the low 22 bits are the value:
  - `0`: index into the form ID array (1-based)
  - `1`: form in `Fallout4.esm` (the value is the form ID)
  - `2`: created at runtime (`0xFF……`)
- **vsval**: the low 2 bits of the first byte give the width (`0` = 1 byte, `1` = 2 bytes, `2` = 3 bytes).
  The value is the little-endian integer shifted right by 2.

## Global data

Each entry is `u32 type, u32 length, length bytes`. Two types are read:

| Type | Contents |
|---|---|
| 0, misc stats | `u32 count`, then `count × (wstring name, u8 category, i32 value)` |
| 3, globals | `vsval count`, then `count × (RefID, f32 value)` |

## Change forms

Each change form is:

```
RefID      formID
u32        changeFlags
u8         type          low 6 bits = form type, top 2 bits = width of the length fields
u8         version
u8/u16/u32 length1       stored size
u8/u16/u32 length2       uncompressed size, 0 = not compressed (zlib otherwise)
u8[length1] data
```

Only two types are decoded. Everything else is skipped by `length1`.

### Quests (type 8)

The data contains fields in this order, each present only if its change flag is set:

| Flag | Field |
|---|---|
| `1 << 0` | 4 bytes of generic form flags (skipped) |
| `1 << 1` | `u16` runtime quest flags: `0x0001` running, `0x0002` completed, `0x0040` failed, `0x0800` tracked |
| `1 << 2` | 4 bytes script delay (skipped) |
| `1 << 31` | stages: `vsval count`, `count × (u16 stage, u8 flags)`, where bit 0 means the stage is done |
| `1 << 30` | script data: layout unknown, so parsing stops here |
| `1 << 29` | objectives: `vsval count`, `count × (u32 index, u32 state)` |

Objective states: `0` dormant, `1` displayed (active), `2`/`3` completed, `4`/`5` failed.

### Player inventory (type 1, the `ACHR` with RefID `0x400014`, reverse-engineered)

The inventory sits inside the player's change form, before the animation-graph block (the
`BShkbAnimationGraph` marker). Its start is found by scanning for a plausible item count followed
by a plausible first item. It is then decoded as:

```
u32 itemCount
item × itemCount:
    RefID  form
    u32    stackCount
    stack × stackCount:
        vsval  extraCount
        extra × extraCount     type-tagged, see below
        i32    count
        u16    flags            bit 0 = equipped
```

| Extra type | Size / meaning |
|---|---|
| `0x1C` `0x9F` `0xA0` `0x49` `0x2E` `0x25` `0x21` `0x48` `0x8E` `0x2F` | Fixed sizes (see `_FIXED_EXTRA`), skipped |
| `0x88` | `vsval n`, then `n × 7` bytes |
| `0x35` | Object instance: `u16`, `u32 n`, `n × (RefID mod, 3 bytes)`, which are the attached OMODs |
| `0x99` | Text display data: 6 bytes, `i32 kind`, 2 bytes, and then if `kind == -2` a `wstring` custom name, then 1 byte |

If the parser meets an extra type it doesn't know, it scans forward for a `(count, flags)` trailer
from which the rest of the inventory parses cleanly. That item is then kept without the unknown
details, and the report notes this.

## F4SE co-save (`.f4se`)

`"F4SE"`, `u32 formatVersion`, `u32 f4seVersion`, `u32 runtimeVersion`, `u32 pluginCount`, then for
each plugin `u32 signature, u32 chunkCount, u32 length` and its chunks
(`u32 type, u32 version, u32 length, data`). The `PLGN` chunk of F4SE itself lists the plugins
that were loaded.

## Further reading

- [UESP: Fallout 4 save file format](https://en.uesp.net/wiki/Fallout4Mod:Save_File_Format)
- [UESP: Skyrim save file format](https://en.uesp.net/wiki/Skyrim_Mod:Save_File_Format), which uses many of the same structures
