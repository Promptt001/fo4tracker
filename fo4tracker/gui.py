"""Tkinter front end."""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

from . import __version__, gamedata, report, savefile
from .analysis import Report, _config_dir
from .binutil import ParseError

APP = "Fallout 4 Save Tracker"
CONFIG = os.path.join(_config_dir(), "config.json")
QUEST_FILTERS = ["Started or done", "Active only", "Completed", "Not started", "Everything"]


def safe_filename(name, fallback="report"):
    """Character names can contain anything; keep them usable as a file name."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name or "").strip(" ._")
    return name[:80] or fallback


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        tmp = CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        os.replace(tmp, CONFIG)
    except OSError:
        pass        # settings are a convenience; never fail because of them


class App:
    def __init__(self, root, save_path=None, data_dir=None):
        self.root = root
        self.cfg = load_config()
        if data_dir:
            self.cfg["data_dir"] = data_dir
        if not self.cfg.get("data_dir"):
            found = gamedata.find_data_dirs()
            if found:
                self.cfg["data_dir"] = found[0]
        self.rep = None
        self.q = queue.Queue()
        self._gd_cache = {}
        self._gd_lock = threading.Lock()
        self._load_id = 0          # newest load request; older results are discarded
        self._img = None
        self._dec_by_iid = {}
        root.title(APP)
        root.geometry(self.cfg.get("geometry") or "1180x800")
        root.minsize(900, 600)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self._style()
        self._build()
        self._enable_dnd()
        self._after = root.after(100, self._poll)
        root.bind("<Destroy>", self._on_destroy, add="+")
        if save_path:
            self.open_save(save_path)
        else:
            self._set_status("Open a save (File ▸ Open, Ctrl+O) or load your latest save (Ctrl+L).")

    # ---- look & feel ----------------------------------------------------
    def _style(self):
        st = ttk.Style(self.root)
        if "clam" in st.theme_names():
            st.theme_use("clam")
        base = font.nametofont("TkDefaultFont")
        self.f_title = base.copy()
        self.f_title.configure(size=18, weight="bold")
        self.f_head = base.copy()
        self.f_head.configure(size=13, weight="bold")
        self.f_body = base.copy()
        self.f_body.configure(size=11)
        st.configure("Treeview", rowheight=max(24, self.f_body.metrics("linespace") + 6))
        st.configure("Head.TLabel", font=self.f_title)
        st.configure("Sub.TLabel", foreground="#4a5650")
        st.configure("Status.TLabel", foreground="#4a5650", padding=(8, 4))

    def _build(self):
        r = self.root
        menu = tk.Menu(r)
        fm = tk.Menu(menu, tearoff=False)
        fm.add_command(label="Open save…", accelerator="Ctrl+O", command=self.choose_save)
        fm.add_command(label="Open latest save", accelerator="Ctrl+L", command=self.open_latest)
        fm.add_command(label="Reload", accelerator="F5", command=self.reload)
        fm.add_separator()
        fm.add_command(label="Set Fallout 4 Data folder…", command=self.choose_data_dir)
        fm.add_command(label="Clear game data cache", command=self.clear_cache)
        fm.add_separator()
        fm.add_command(label="View report in browser", accelerator="Ctrl+B", command=self.view_html)
        fm.add_command(label="Export HTML report…", accelerator="Ctrl+E", command=self.export_html)
        fm.add_command(label="Export text report…", command=self.export_text)
        fm.add_separator()
        fm.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit)
        menu.add_cascade(label="File", menu=fm)
        hm = tk.Menu(menu, tearoff=False)
        hm.add_command(label="Custom decisions file…", command=self.open_config_folder)
        hm.add_command(label="About", command=self.about)
        menu.add_cascade(label="Help", menu=hm)
        r.config(menu=menu)
        for key, fn in (("o", self.choose_save), ("l", self.open_latest), ("e", self.export_html),
                        ("b", self.view_html), ("q", self.quit)):
            # bind both cases so shortcuts still work with Caps Lock on
            for k in (key, key.upper()):
                r.bind(f"<Control-{k}>", lambda e, f=fn: f())
        r.bind("<F5>", lambda e: self.reload())

        top = ttk.Frame(r, padding=(10, 8))
        top.pack(fill="x")
        ttk.Button(top, text="Open save…", command=self.choose_save).pack(side="left")
        ttk.Button(top, text="Latest save", command=self.open_latest).pack(side="left", padx=6)
        ttk.Button(top, text="Export HTML…", command=self.export_html).pack(side="left")
        self.data_lbl = ttk.Label(top, style="Sub.TLabel")
        self.data_lbl.pack(side="right")
        ttk.Button(top, text="Data folder…", command=self.choose_data_dir).pack(side="right", padx=6)
        self._update_data_label()

        head = ttk.Frame(r, padding=(10, 0, 10, 8))
        head.pack(fill="x")
        self.shot = ttk.Label(head)
        self.shot.pack(side="left")
        info = ttk.Frame(head, padding=(14, 0))
        info.pack(side="left", fill="both", expand=True)
        self.h_name = ttk.Label(info, style="Head.TLabel")
        self.h_name.pack(anchor="w")
        self.h_line1 = ttk.Label(info)
        self.h_line1.pack(anchor="w")
        self.h_line2 = ttk.Label(info, style="Sub.TLabel")
        self.h_line2.pack(anchor="w")
        self.h_line3 = ttk.Label(info, style="Sub.TLabel", wraplength=700, justify="left")
        self.h_line3.pack(anchor="w", pady=(6, 0))

        self.nb = ttk.Notebook(r)
        self.nb.pack(fill="both", expand=True, padx=10)
        self.tab_summary = self._text_tab("Summary")
        self._quest_tab()
        self._decision_tab()
        self._inventory_tab()
        self._stats_tab()
        self.tab_plugins = self._text_tab("Load order")

        self.status = ttk.Label(r, style="Status.TLabel", anchor="w")
        self.status.pack(fill="x")

    def _text_tab(self, title):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text=title)
        t = tk.Text(f, wrap="word", padx=14, pady=10, relief="flat", font=self.f_body)
        sb = ttk.Scrollbar(f, command=t.yview)
        t.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.tag_configure("h", font=self.f_head, spacing1=10, spacing3=4)
        t.tag_configure("b", lmargin1=12, lmargin2=28, spacing1=2)
        t.tag_configure("p", spacing1=2, spacing3=2)
        t.tag_configure("warn", foreground="#9a5200")
        t.tag_configure("muted", foreground="#5f6b64")
        return t

    def _tree(self, parent, cols, widths, show="headings"):
        frame = ttk.Frame(parent)
        tv = ttk.Treeview(frame, columns=cols, show=show, selectmode="browse")
        for c, w in zip(cols, widths):
            tv.heading(c, text=c.title(), command=lambda c=c, tv=tv: self._sort(tv, c))
            tv.column(c, width=w, anchor="e" if c in ("count", "value") else "w",
                      stretch=c in ("name", "quest", "item"))
        sb = ttk.Scrollbar(frame, command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)
        return frame, tv

    def _sort(self, tv, col):
        def key(x):
            v = x[0].replace(",", "")
            return (0, float(v)) if v.replace(".", "", 1).isdigit() else (1, v.lower())
        last_col, rev = getattr(tv, "_sort_state", (None, True))
        rev = not rev if last_col == col else False
        tv._sort_state = (col, rev)
        # Sort the top level, and the rows inside each group (questline tree).
        for parent in ("",) + tv.get_children(""):
            items = [(tv.set(k, col), k) for k in tv.get_children(parent)]
            if parent and not items:
                continue
            if parent == "" and any(tv.get_children(k) for _v, k in items):
                continue        # keep questlines in story order; only sort within them
            items.sort(key=key, reverse=rev)
            for i, (_v, k) in enumerate(items):
                tv.move(k, parent, i)
        for c in tv["columns"]:
            arrow = (" ▼" if rev else " ▲") if c == col else ""
            tv.heading(c, text=c.title() + arrow)

    def _quest_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Questlines")
        bar = ttk.Frame(f, padding=(0, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Show:").pack(side="left")
        self.q_filter = ttk.Combobox(bar, state="readonly", width=22, values=QUEST_FILTERS)
        self.q_filter.current(0)
        self.q_filter.pack(side="left", padx=6)
        self.q_filter.bind("<<ComboboxSelected>>", lambda e: self._fill_quests())
        ttk.Label(bar, text="Search:").pack(side="left", padx=(12, 0))
        self.q_search = tk.StringVar()
        self.q_search.trace_add("write", lambda *a: self._fill_quests())
        ttk.Entry(bar, textvariable=self.q_search, width=30).pack(side="left", padx=6)
        pw = self.q_pw = ttk.Panedwindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True)
        pw.bind("<Configure>", lambda e, pw=pw: self._init_sash(pw))
        left, self.q_tree = self._tree(pw, ("quest", "status"), (300, 110), show="tree headings")
        self.q_tree.heading("#0", text="Questline")
        self.q_tree.column("#0", width=170, stretch=False)
        self.q_tree.column("status", stretch=False)
        pw.add(left, weight=3)
        self.q_detail = tk.Text(pw, wrap="word", padx=12, pady=8, relief="flat", font=self.f_body,
                                state="disabled")
        self.q_detail.tag_configure("h", font=self.f_head, spacing3=4)
        self.q_detail.tag_configure("muted", foreground="#5f6b64")
        self.q_detail.tag_configure("act", foreground="#1f7a4c")
        self.q_detail.tag_configure("bad", foreground="#a12a2a")
        pw.add(self.q_detail, weight=2)
        self.q_tree.bind("<<TreeviewSelect>>", self._show_quest)
        self.q_tree.tag_configure("active", foreground="#1f7a4c")
        self.q_tree.tag_configure("failed", foreground="#a12a2a")
        self.q_tree.tag_configure("muted", foreground="#7b8680")
        self._quest_by_iid = {}

    def _init_sash(self, pw):
        def apply():
            w = pw.winfo_width()
            if w > 100 and not getattr(pw, "_sash_done", False):
                pw.sashpos(0, int(w * 0.55))
                pw._sash_done = True
        pw.after_idle(apply)

    def _decision_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Decisions")
        pw = self.d_pw = ttk.Panedwindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=(6, 0))
        pw.bind("<Configure>", lambda e, pw=pw: self._init_sash(pw))
        left, self.d_tree = self._tree(pw, ("state", "quest", "questline"), (110, 260, 150))
        pw.add(left, weight=3)
        self.d_detail = tk.Text(pw, wrap="word", padx=12, pady=8, relief="flat", font=self.f_body,
                                state="disabled")
        self.d_detail.tag_configure("h", font=self.f_head, spacing3=4)
        self.d_detail.tag_configure("muted", foreground="#5f6b64")
        pw.add(self.d_detail, weight=2)
        self.d_tree.bind("<<TreeviewSelect>>", self._show_decision)
        self.d_tree.tag_configure("warn", foreground="#9a5200")
        self.d_tree.tag_configure("muted", foreground="#7b8680")

    def _inventory_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Inventory")
        bar = ttk.Frame(f, padding=(0, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Search:").pack(side="left")
        self.i_search = tk.StringVar()
        self.i_search.trace_add("write", lambda *a: self._fill_inventory())
        ttk.Entry(bar, textvariable=self.i_search, width=30).pack(side="left", padx=6)
        self.i_total = ttk.Label(bar, style="Sub.TLabel")
        self.i_total.pack(side="right")
        frame, self.i_tree = self._tree(f, ("category", "item", "count", "equipped", "mods"),
                                        (140, 360, 80, 80, 360))
        frame.pack(fill="both", expand=True)

    def _stats_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Stats")
        frame, self.s_tree = self._tree(f, ("group", "name", "value"), (140, 360, 120))
        frame.pack(fill="both", expand=True, pady=(6, 0))

    # ---- drag & drop (optional) ----------------------------------------
    def _enable_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, e):
        files = [f for f in self.root.tk.splitlist(e.data) if f.lower().endswith(".fos")]
        if files:
            self.open_save(files[0])
        else:
            self._set_status("Drop a Fallout 4 save (.fos) to open it.")

    # ---- actions --------------------------------------------------------
    def _set_status(self, msg):
        self.status.configure(text=msg)

    def _update_data_label(self):
        d = self.cfg.get("data_dir")
        self.data_lbl.configure(text=f"Game data: {d}" if d else "Game data: not set (names will be limited)")

    def choose_save(self):
        init = self.cfg.get("save_dir") or next(iter(gamedata.find_save_dirs()), os.path.expanduser("~"))
        p = filedialog.askopenfilename(title="Open Fallout 4 save", initialdir=init,
                                       filetypes=[("Fallout 4 saves", "*.fos"), ("All files", "*")])
        if p:
            self.open_save(p)

    def open_latest(self):
        p = gamedata.latest_save()
        if p:
            self.open_save(p)
        else:
            messagebox.showinfo(APP, "Couldn't find your Fallout 4 save folder automatically. "
                                     "Use “Open save…” instead.")

    def reload(self):
        if self.rep:
            self.open_save(self.rep.save.path)

    def choose_data_dir(self):
        p = filedialog.askdirectory(title="Select the Fallout 4 'Data' folder (contains Fallout4.esm)",
                                    initialdir=self.cfg.get("data_dir") or os.path.expanduser("~"))
        if not p:
            return
        if not gamedata._ci_path(p, "Fallout4.esm"):
            if not messagebox.askyesno(APP, "Fallout4.esm isn't in that folder. Use it anyway?"):
                return
        self.cfg["data_dir"] = p
        save_config(self.cfg)
        self._gd_cache.clear()
        self._update_data_label()
        self.reload()

    def clear_cache(self):
        n = gamedata.clear_cache()
        with self._gd_lock:
            self._gd_cache.clear()
        self._set_status(f"Removed {n} cached plugin index file{'' if n == 1 else 's'}. "
                         "The next save you open will re-read the game data.")

    def open_save(self, path):
        self.cfg["save_dir"] = os.path.dirname(os.path.abspath(path))
        save_config(self.cfg)
        self._set_status(f"Reading {os.path.basename(path)}…")
        self.root.configure(cursor="watch")
        self._load_id += 1
        threading.Thread(target=self._load_worker, args=(self._load_id, path, self.cfg.get("data_dir")),
                         daemon=True).start()

    def _load_worker(self, load_id, path, data_dir):
        try:
            save = savefile.load_save(path)
            gd = None
            if data_dir and os.path.isdir(data_dir):
                key = (data_dir, tuple(save.plugins), tuple(save.light_plugins))
                # One loader at a time: two saves opened in a row share the same work.
                with self._gd_lock:
                    gd = self._gd_cache.get(key)
                    if gd is None:
                        gd = gamedata.GameData.load(data_dir, save.plugins, save.light_plugins,
                                                    progress=lambda m: self.q.put((load_id, "status", m)))
                        self._gd_cache[key] = gd
            rep = Report(save, gd)
            self.q.put((load_id, "done", rep))
        except ParseError as e:
            self.q.put((load_id, "error", (f"{os.path.basename(path)} couldn't be read.", str(e))))
        except OSError as e:
            self.q.put((load_id, "error", (f"Couldn't open {os.path.basename(path)}.", str(e.strerror or e))))
        except Exception as e:
            self.q.put((load_id, "error", (f"Something went wrong reading {os.path.basename(path)}: {e}",
                                           traceback.format_exc())))

    def _poll(self):
        try:
            while True:
                load_id, kind, val = self.q.get_nowait()
                if load_id != self._load_id:
                    continue            # result of a load the user has since replaced
                if kind == "status":
                    self._set_status(val)
                elif kind == "done":
                    self.root.configure(cursor="")
                    self.show(val)
                elif kind == "error":
                    self.root.configure(cursor="")
                    self._set_status("Couldn't read that save.")
                    msg, detail = val
                    messagebox.showerror(APP, msg, detail=detail)
        except queue.Empty:
            pass
        self._after = self.root.after(100, self._poll)

    def _on_destroy(self, e):
        if e.widget is self.root and self._after:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _need_report(self):
        if not self.rep:
            self._set_status("Open a save first (Ctrl+O, or Ctrl+L for your latest save).")
            return False
        return True

    def _write_file(self, path, text):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as e:
            messagebox.showerror(APP, f"Couldn't save {os.path.basename(path)}.", detail=str(e.strerror or e))
            return False
        self.cfg["export_dir"] = os.path.dirname(path)
        save_config(self.cfg)
        self._set_status(f"Saved {path}")
        return True

    def _export_name(self, ext):
        s = self.rep.save
        return f"{safe_filename(s.player_name, 'fallout4')}_save{s.save_number}_progress{ext}"

    def export_html(self):
        if not self._need_report():
            return
        p = filedialog.asksaveasfilename(defaultextension=".html", initialfile=self._export_name(".html"),
                                         initialdir=self.cfg.get("export_dir") or os.path.expanduser("~"),
                                         filetypes=[("HTML", "*.html")])
        if p:
            self._write_file(p, report.to_html(self.rep))

    def export_text(self):
        if not self._need_report():
            return
        p = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=self._export_name(".txt"),
                                         initialdir=self.cfg.get("export_dir") or os.path.expanduser("~"),
                                         filetypes=[("Text", "*.txt")])
        if p:
            self._write_file(p, report.to_text(self.rep))

    def view_html(self):
        """Write the report to the cache folder and open it in the default browser."""
        if not self._need_report():
            return
        try:
            path = os.path.join(gamedata.cache_dir(), "last_report.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report.to_html(self.rep))
        except OSError as e:
            messagebox.showerror(APP, "Couldn't write the report.", detail=str(e))
            return
        webbrowser.open(Path(path).resolve().as_uri())
        self._set_status(f"Opened {path} in your browser.")

    def open_config_folder(self):
        from .analysis import user_decisions_path
        path = user_decisions_path()
        messagebox.showinfo(APP, "Add your own key decisions in this file (same format as the built-in "
                                 "decisions.json; see the README). They're merged in and yours win:",
                            detail=path)

    def about(self):
        messagebox.showinfo(APP, f"{APP} {__version__}",
                            detail="Reads Fallout 4 saves and reports quest progress, key decisions, "
                                   "inventory and stats. Read-only: it never changes your saves.\n\n"
                                   "Not affiliated with or endorsed by Bethesda Softworks.")

    def quit(self):
        try:
            if self.root.state() == "normal":
                self.cfg["geometry"] = self.root.geometry()
                save_config(self.cfg)
        except tk.TclError:
            pass
        self.root.destroy()

    # ---- rendering ------------------------------------------------------
    def show(self, rep):
        self.rep = rep
        s = rep.save
        self.root.title(f"{s.player_name} · {os.path.basename(s.path)} — {APP}")
        png = report.screenshot_png(s)
        if png:
            try:
                img = tk.PhotoImage(data=base64.b64encode(png).decode())
                factor = max(1, img.width() // 320)
                self._img = img.subsample(factor, factor)
                self.shot.configure(image=self._img)
            except tk.TclError:
                self.shot.configure(image="")
        else:
            self.shot.configure(image="")
        self.h_name.configure(text=f"{s.player_name} · Level {s.level}")
        self.h_line1.configure(text=f"{s.location} · {s.playtime_pretty} played · {self.rep.caps():,} caps")
        when = f"{s.saved_at:%d %b %Y %H:%M}" if s.saved_at else "?"
        self.h_line2.configure(text=f"Save #{s.save_number} · {when} · game {s.game_version} · "
                                    f"{os.path.basename(s.path)}")
        self.h_line3.configure(text="  ".join(rep.notes))
        self._fill_summary()
        self._fill_quests()
        self._fill_decisions()
        self._fill_inventory()
        self._fill_stats()
        self._fill_plugins()
        gd = rep.gd
        self._set_status(f"Loaded {len(rep.quests)} quests, {len(rep.inventory)} inventory lines" +
                         (f", game data from {len(gd.loaded_plugins)} plugins." if gd else
                          " (no game data loaded)."))

    def _write(self, t, chunks):
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in chunks:
            t.insert("end", text, tag)
        t.configure(state="disabled")

    def _fill_summary(self):
        chunks = []
        for heading, paras in self.rep.summary():
            chunks.append((heading + "\n", "h"))
            for p in paras:
                tags = ("b",) if p.startswith("•") else ("p",)
                if "⚠" in p or p.startswith("• Decision ahead") or p.startswith("• Coming up"):
                    tags += ("warn",)
                chunks.append((p + "\n", tags))
        self._write(self.tab_summary, chunks)

    def _fill_quests(self):
        if not self.rep:
            return
        tv = self.q_tree
        tv.delete(*tv.get_children())
        self._quest_by_iid.clear()
        mode = self.q_filter.get()
        needle = self.q_search.get().strip().lower()
        for line, d in self.rep.lines.items():
            qs = d["quests"]
            if mode == "Active only":
                qs = [q for q in qs if q.status == "active"]
            elif mode == "Completed":
                qs = [q for q in qs if q.status == "completed"]
            elif mode == "Not started":
                qs = [q for q in qs if q.status == "not started"]
            elif mode == "Started or done":
                qs = [q for q in qs if q.status != "not started"]
            if needle:
                qs = [q for q in qs if needle in q.name.lower() or needle in q.edid.lower()
                      or any(needle in o.text.lower() for o in q.objectives)]
            if not qs:
                continue
            total = len(d["quests"])
            parent = tv.insert("", "end", text=line, open=True,
                               values=(f"{d['completed']}/{total} completed", f"{d['active']} active"))
            for q in qs:
                tag = {"active": "active", "failed": "failed", "not started": "muted",
                       "completed": "muted"}.get(q.status, "")
                iid = tv.insert(parent, "end", text="", values=(q.name, report.STATUS_LABEL[q.status]),
                                tags=(tag,))
                self._quest_by_iid[iid] = q

    def _show_quest(self, _e=None):
        sel = self.q_tree.selection()
        if not sel:
            return
        q = self._quest_by_iid.get(sel[0])
        if not q:
            self._show_questline(self.q_tree.item(sel[0], "text"))
            return
        chunks = [(q.name + "\n", "h"),
                  (f"{q.line} · {report.STATUS_LABEL[q.status]}" +
                   (" · tracked" if q.tracked else "") +
                   (f" · {q.edid}" if q.edid else "") + "\n\n", "muted")]
        if q.objectives:
            chunks.append(("Objectives\n", "h"))
            for o in q.objectives:
                tag = "act" if o.state == 1 else ("bad" if o.state >= 4 else "muted")
                chunks.append((f"  {'▶' if o.state == 1 else '✓' if o.state in (2, 3) else '✗'} {o.text}\n", tag))
            chunks.append(("\n", ""))
        if q.journal:
            chunks.append(("Journal\n", "h"))
            for st, text, n in q.journal:
                chunks.append((f"  [{st}] {text}" + (" (one of several versions)" if n > 1 else "") + "\n", ""))
        elif q.done_stages:
            chunks.append((f"Stages reached: {', '.join(map(str, q.done_stages))}\n", "muted"))
        dec = next((d for d in self.rep.decisions if d.quest is q), None)
        if dec:
            chunks.append(("\nWhy it matters\n", "h"))
            chunks.append((dec.about + "\n", ""))
        self._write(self.q_detail, chunks)

    def _show_questline(self, line):
        d = self.rep.lines.get(line) if self.rep else None
        if not d:
            return
        total = len(d["quests"])
        chunks = [(line + "\n", "h"),
                  (f"{d['completed']} of {total} completed · {d['active']} active · {d['started']} started · "
                   f"{d['failed']} failed · {d['not started']} not started\n\n", "muted")]
        active = [q for q in d["quests"] if q.status == "active"]
        if active:
            chunks.append(("Active now\n", "h"))
            for q in active:
                objs = "; ".join(o.text for o in q.active_objectives)
                chunks.append((f"  ▶ {q.name}" + (f" — {objs}" if objs else "") + "\n", "act"))
        self._write(self.q_detail, chunks)

    def _fill_decisions(self):
        tv = self.d_tree
        tv.delete(*tv.get_children())
        self._dec_by_iid = {}
        for d in self.rep.decisions:
            tag = "warn" if d.state in ("in progress", "coming up") else ("muted" if d.state == "not reached" else "")
            iid = tv.insert("", "end", values=(report.DECISION_LABEL[d.state], d.quest.name, d.quest.line),
                            tags=(tag,))
            self._dec_by_iid[iid] = d
        self._write(self.d_detail, [("Select a decision to see details.\n\n", "muted"),
                                    ("Decision ahead = you're in that quest now. Coming up = the quests "
                                     "leading into it are done. Decided = the quest is finished; the "
                                     "journal line shows what happened.\n", "muted")])

    def _show_decision(self, _e=None):
        sel = self.d_tree.selection()
        d = self._dec_by_iid.get(sel[0]) if sel else None
        if not d:
            return
        kind = {"point_of_no_return": "Point of no return", "ending": "Ending",
                "possible": "Possible choice"}.get(d.kind, "Decision")
        chunks = [(d.quest.name + "\n", "h"),
                  (f"{d.quest.line} · {kind} · {report.DECISION_LABEL[d.state]}\n\n", "muted"),
                  (d.about + "\n\n", "")]
        for e in d.evidence:
            chunks.append(("• " + e + "\n", ""))
        if d.quest.active_objectives:
            chunks.append(("\nCurrent objectives\n", "h"))
            for o in d.quest.active_objectives:
                chunks.append(("  ▶ " + o.text + "\n", ""))
        self._write(self.d_detail, chunks)

    def _fill_inventory(self):
        if not self.rep:
            return
        tv = self.i_tree
        tv.delete(*tv.get_children())
        needle = self.i_search.get().strip().lower()
        n = 0
        for r in self.rep.inventory:
            if needle and needle not in r.name.lower() and needle not in r.category.lower():
                continue
            tv.insert("", "end", values=(r.category, r.name, f"{r.count:,}", "yes" if r.equipped else "",
                                         ", ".join(r.mods)))
            n += 1
        self.i_total.configure(text=f"{n} lines · {self.rep.caps():,} caps")

    def _fill_stats(self):
        tv = self.s_tree
        tv.delete(*tv.get_children())
        groups = {0: "General", 1: "Quests", 2: "Combat", 3: "Crafting", 4: "Crime", 5: "Workshops", 7: "DLC/Other"}
        for cat, name, val in self.rep.save.misc_stats:
            tv.insert("", "end", values=(groups.get(cat, str(cat)), name, f"{val:,}"))

    def _fill_plugins(self):
        s = self.rep.save
        chunks = [("Plugins in this save\n", "h")]
        for i, p in enumerate(s.plugins):
            chunks.append((f"  {i:02X}  {p}\n", ""))
        for i, p in enumerate(s.light_plugins):
            chunks.append((f"  FE:{i:03X}  {p}\n", ""))
        if self.rep.gd and self.rep.gd.missing_plugins:
            chunks.append(("\nNot found in your Data folder\n", "h"))
            for p in self.rep.gd.missing_plugins:
                chunks.append((f"  {p}\n", "warn"))
        if s.cosave:
            chunks.append(("\nF4SE co-save\n", "h"))
            chunks.append((f"  F4SE {s.cosave.f4se_version} for game {s.cosave.runtime}\n", ""))
        if s.warnings:
            chunks.append(("\nParser warnings\n", "h"))
            for w in s.warnings:
                chunks.append((f"  {w}\n", "muted"))
        self._write(self.tab_plugins, chunks)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=APP)
    ap.add_argument("save", nargs="?", help=".fos save file to open")
    ap.add_argument("--data", help="Fallout 4 Data folder")
    args = ap.parse_args(argv)
    try:
        from tkinterdnd2 import TkinterDnD  # type: ignore
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    App(root, args.save, args.data)
    root.mainloop()


if __name__ == "__main__":
    main()
