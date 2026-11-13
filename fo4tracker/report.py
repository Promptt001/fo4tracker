"""Export a Report as a standalone HTML page or plain text."""
from __future__ import annotations

import base64
import datetime as _dt
import html
import struct
import zlib

from . import __version__

STATUS_LABEL = {"active": "Active", "started": "Started", "completed": "Completed",
                "failed": "Failed", "not started": "Not started"}
DECISION_LABEL = {"in progress": "Decision ahead", "coming up": "Coming up",
                  "decided": "Decided", "not reached": "Not reached"}


def rgba_to_png(width: int, height: int, rgba: bytes) -> bytes:
    """Tiny PNG encoder (stdlib only). Drops alpha, which saves always fill with 0."""
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        row = rgba[y * stride:(y + 1) * stride]
        rows.append(0)
        rgb = bytearray(width * 3)
        rgb[0::3] = row[0::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[2::4]
        rows += rgb

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


def screenshot_png(save) -> bytes | None:
    if not save.screenshot_rgba or not save.shot_width:
        return None
    return rgba_to_png(save.shot_width, save.shot_height, save.screenshot_rgba)


def to_text(rep) -> str:
    s = rep.save
    when = f"{s.saved_at:%d %b %Y %H:%M}" if s.saved_at else "unknown date"
    out = [f"{s.player_name} — level {s.level} — save #{s.save_number}, {when}", ""]
    for heading, paras in rep.summary():
        out.append(heading.upper())
        out.extend(paras)
        out.append("")
    out.append("QUESTLINES")
    for line, d in rep.lines.items():
        out.append(f"{line}: {d['completed']} completed, {d['active']} active, "
                   f"{d['started']} started, {d['failed']} failed, {d['not started']} not started")
        for q in d["quests"]:
            if q.status == "not started":
                continue
            out.append(f"  [{STATUS_LABEL[q.status]}] {q.name}")
            for o in q.active_objectives:
                out.append(f"      → {o.text}")
    out.append("")
    out.append("INVENTORY")
    for r in rep.inventory:
        out.append(f"  {r.category:18} {r.count:>9,}  {r.name}{'  [equipped]' if r.equipped else ''}")
    return "\n".join(out)


CSS = """
:root{--bg:#f6f7f4;--fg:#1d2320;--muted:#5f6b64;--card:#fff;--line:#dfe3dc;--accent:#1f7a4c;
--warn:#a65c00;--bad:#a12a2a;--chip:#eef2ec}
@media(prefers-color-scheme:dark){:root{--bg:#121513;--fg:#e4e9e5;--muted:#9aa69f;--card:#1b1f1c;
--line:#2c332e;--accent:#5cc98e;--warn:#e0a24a;--bad:#e07070;--chip:#232a25}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1050px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;gap:22px;align-items:center;flex-wrap:wrap;margin-bottom:22px}
header img{width:320px;max-width:100%;border-radius:8px;border:1px solid var(--line)}
h1{margin:0 0 4px;font-size:28px}h2{font-size:19px;margin:30px 0 10px;border-bottom:1px solid var(--line);
padding-bottom:6px}h3{font-size:16px;margin:18px 0 6px}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 18px;margin:10px 0}
.chip{display:inline-block;padding:1px 9px;border-radius:999px;background:var(--chip);font-size:12.5px;
margin-right:6px;white-space:nowrap}
.s-active{color:var(--accent);font-weight:600}.s-failed{color:var(--bad)}.s-started{color:var(--warn)}
.s-completed{color:var(--muted)}.s-not{color:var(--muted);opacity:.7}
.warn{border-left:4px solid var(--warn)}.ok{border-left:4px solid var(--accent)}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:5px 8px;border-bottom:1px solid var(--line);
text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}
details{margin:4px 0}summary{cursor:pointer}ul{margin:6px 0 6px 18px;padding:0}
.bar{height:8px;background:var(--chip);border-radius:6px;overflow:hidden;margin:4px 0 2px}
.bar>span{display:block;height:100%;background:var(--accent)}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 8px}nav a{color:var(--fg);text-decoration:none}
nav a:hover{text-decoration:underline}a{color:var(--accent)}
footer{margin-top:36px;font-size:13px}
@media print{body{background:#fff;color:#000}nav{display:none}.card{break-inside:avoid}}
"""

# Open every collapsed section before printing, so nothing is hidden on paper.
PRINT_JS = ("<script>addEventListener('beforeprint',function(){"
            "document.querySelectorAll('details').forEach(function(d){d.open=true})})</script>")

SECTIONS = [("summary", "Summary"), ("decisions", "Key decisions"), ("quests", "Questlines"),
            ("inventory", "Inventory"), ("stats", "Statistics"), ("load-order", "Load order")]


def _e(s):
    return html.escape(str(s))


def to_html(rep) -> str:
    s = rep.save
    png = screenshot_png(s)
    img = f'<img alt="Save screenshot" src="data:image/png;base64,{base64.b64encode(png).decode()}">' if png else ""
    when = f"{s.saved_at:%d %b %Y %H:%M}" if s.saved_at else "unknown date"
    parts = [f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<meta name='generator' content='fo4tracker {_e(__version__)}'>"
             f"<title>{_e(s.player_name)} — Fallout 4 progress</title><style>{CSS}</style>{PRINT_JS}"
             f"</head><body><main>"]
    parts.append(f"<header>{img}<div><h1>{_e(s.player_name)}</h1>"
                 f"<div>Level {s.level} · {_e(s.location)} · {rep.caps():,} caps</div>"
                 f"<div class='muted'>{_e(s.playtime_pretty)} played · save #{s.save_number} · "
                 f"{when} · game {_e(s.game_version)}</div></div></header>")
    parts.append("<nav>" + "".join(f"<a class='chip' href='#{sid}'>{_e(label)}</a>"
                                   for sid, label in SECTIONS) + "</nav>")

    parts.append("<h2 id='summary'>Executive summary</h2>")
    for heading, paras in rep.summary():
        if heading == "Key decisions":
            continue            # has its own, more detailed section below
        parts.append(f"<h3>{_e(heading)}</h3>")
        bullets = [p for p in paras if p.startswith("•")]
        plain = [p for p in paras if not p.startswith("•")]
        for p in plain:
            parts.append(f"<p>{_e(p)}</p>")
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{_e(b[1:].strip())}</li>" for b in bullets) + "</ul>")

    parts.append("<h2 id='decisions'>Key decisions</h2>")
    if not rep.decisions:
        parts.append("<p class='muted'>None of the tracked decision quests are in this save yet.</p>")
    for d in rep.decisions:
        if d.state == "not reached":
            continue
        cls = "warn" if d.state in ("in progress", "coming up") else "ok"
        kind = {"point_of_no_return": "Point of no return", "ending": "Ending",
                "possible": "Possible choice"}.get(d.kind, "Decision")
        ev = "".join(f"<li>{_e(x)}</li>" for x in d.evidence)
        parts.append(f"<div class='card {cls}'><span class='chip'>{_e(DECISION_LABEL[d.state])}</span>"
                     f"<span class='chip'>{_e(kind)}</span><b>{_e(d.quest.name)}</b> "
                     f"<span class='muted'>· {_e(d.quest.line)}</span><div>{_e(d.about)}</div>"
                     f"{'<ul>' + ev + '</ul>' if ev else ''}</div>")
    later = [d for d in rep.decisions if d.state == "not reached"]
    if later:
        parts.append("<details><summary>Later decision points not reached yet "
                     f"({len(later)})</summary><ul>" +
                     "".join(f"<li><b>{_e(d.quest.name)}</b> ({_e(d.quest.line)}): {_e(d.about)}</li>"
                             for d in later) + "</ul></details>")

    parts.append("<h2 id='quests'>Questlines</h2>")
    for line, d in rep.lines.items():
        total = len(d["quests"])
        pct = int(100 * d["completed"] / total) if total else 0
        parts.append(f"<div class='card'><b>{_e(line)}</b> <span class='muted'>· {d['completed']} of {total} "
                     f"completed, {d['active']} active</span><div class='bar'><span style='width:{pct}%'></span></div>")
        for q in d["quests"]:
            cls = "s-not" if q.status == "not started" else f"s-{q.status}"
            body = ""
            if q.objectives:
                body += "<ul>" + "".join(f"<li>{_e(o.text)} <span class='muted'>({_e(o.label)})</span></li>"
                                         for o in q.objectives) + "</ul>"
            if q.journal:
                body += "<div class='muted'>Journal:</div><ul>" + "".join(
                    f"<li>{_e(t)}{' <span class=muted>(one of several versions)</span>' if n > 1 else ''}</li>"
                    for _st, t, n in q.journal) + "</ul>"
            label = f"<span class='{cls}'>{_e(STATUS_LABEL[q.status])}</span> — {_e(q.name)}"
            if body:
                parts.append(f"<details><summary>{label}</summary>{body}</details>")
            else:
                parts.append(f"<div>{label}</div>")
        parts.append("</div>")

    parts.append("<h2 id='inventory'>Inventory</h2>")
    if not rep.inventory:
        parts.append("<p class='muted'>No inventory could be read from this save.</p>")
    parts.append("<table><tr><th>Category</th><th>Item</th><th class='n'>Count</th></tr>")
    for r in rep.inventory:
        extra = " <span class='chip'>equipped</span>" if r.equipped else ""
        mods = f"<div class='muted'>{_e(', '.join(r.mods))}</div>" if r.mods else ""
        parts.append(f"<tr><td>{_e(r.category)}</td><td>{_e(r.name)}{extra}{mods}</td>"
                     f"<td class='n'>{r.count:,}</td></tr>")
    parts.append("</table>")

    parts.append("<h2 id='stats'>Statistics</h2><table>")
    for _cat, name, val in s.misc_stats:
        if val:
            parts.append(f"<tr><td>{_e(name)}</td><td class='n'>{val:,}</td></tr>")
    parts.append("</table>")
    plug = ", ".join(s.plugins + s.light_plugins)
    parts.append(f"<h2 id='load-order'>Load order</h2><p class='muted'>{_e(plug)}</p>")
    if rep.gd and rep.gd.missing_plugins:
        parts.append("<p>Not found in the Data folder: <span class='muted'>"
                     f"{_e(', '.join(rep.gd.missing_plugins))}</span></p>")
    if s.cosave:
        parts.append(f"<p class='muted'>F4SE {_e(s.cosave.f4se_version)} co-save present.</p>")
    generated = _dt.datetime.now().strftime("%d %b %Y %H:%M")
    parts.append(f"<footer class='muted'>Generated {generated} by Fallout 4 Save Tracker "
                 f"{_e(__version__)}. Decision notes are a starter list and can be extended. "
                 "Not affiliated with Bethesda Softworks.</footer></main></body></html>")
    return "".join(parts)
