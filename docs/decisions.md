# Key decisions

The **Decisions** tab and the "Key decisions" part of the summary come from a list of quests that
contain important story choices. The list ships in `fo4tracker/decisions.json`, and you can add
to it or override it with your own file.

## Your own file

| OS | Location |
|---|---|
| Linux | `~/.config/fo4tracker/decisions.json` (or `$XDG_CONFIG_HOME/fo4tracker/`) |
| Windows | `%APPDATA%\fo4tracker\decisions.json` |
| macOS | `~/.config/fo4tracker/decisions.json` |

In the window, **Help ▸ Custom decisions file…** shows the exact path.

Your entries are merged with the built-in ones. If an entry has the same `edid` as a built-in one,
yours replaces it. If your file can't be read, the report says so in its **Notes**, and the built-in
list is used.

## Format

```json
{
  "decisions": [
    {
      "edid": "DLC03MQ06",
      "line": "Far Harbor",
      "kind": "decision",
      "about": "Decide the island's future.",
      "after": ["DLC03MQ05"]
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `edid` | yes | The quest's editor ID (case-insensitive). You can find it in the quest's detail pane, in xEdit or in the Creation Kit |
| `about` | recommended | One or two sentences on what the choice is and why it matters |
| `kind` | no | `decision` (default), `point_of_no_return` or `ending`. Points of no return are flagged with ⚠ in the summary |
| `after` | no | Editor IDs of the quests that lead into this one. When all of them are complete and this quest hasn't started, it shows as **coming up** |
| `line` | no | For your own reference. The questline shown comes from the game data |

## How states are worked out

| State | When |
|---|---|
| **in progress** (*Decision ahead*) | The quest is active or started |
| **coming up** | The quest hasn't started, and every quest in `after` is completed |
| **decided** | The quest is completed or failed. The last journal entry and any failed objectives ("route not taken") are shown as evidence |
| **not reached** | Anything else (listed separately, collapsed) |

The tool also flags any active quest that isn't in the list when its current objectives look like a
choice: they contain words such as *or*, *decide*, *choose*, *side with*, *spare* or *betray*.

## Built-in entries

The main quest faction split (`MQ206`, `MQ207`); the Institute, Railroad and Brotherhood points
of no return and endings (`InstMassFusion`, `Inst302`, `Inst306`–`Inst308`, `RR201`, `RR302`,
`RR303`, `BoS302`, `BoS302B`, `MQ302*`); and notable side-quest choices (`MS17` Covenant, `MS11` the
U.S.S. Constitution, `MS09` Cabot House, `MS01` the kid in the fridge, `MS16` Bobbi No-Nose's heist, `MS19` the Vault 81 cure).

Contributions of well-sourced entries (especially for the DLC) are welcome. See
[CONTRIBUTING.md](../CONTRIBUTING.md).
