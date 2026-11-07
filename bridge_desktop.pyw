"""Native Tk Windows interface for the local AI Bridge."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import NamedTuple

from ai_bridge import (BASE, BridgeError, Mailbox, atomic_json, auth_status, clean_env,
                       config_for, executable_for)
from lead_run import launch, lead_timeout, recover, tail
from workspace_state import FIELDS, Store

NAMES = {"claude": "Claude Code", "chatgpt": "ChatGPT / Codex"}
BG, CARD, INK, QUIET, ACCENT = "#eff3f8", "#ffffff", "#18263b", "#53647b", "#2154ad"
SHORT = {"claude": "Claude", "chatgpt": "Codex"}
PILLS = {"green": ("#d6f0de", "#14532d"), "amber": ("#fdebc8", "#7a4a05"), "slate": ("#dbe5f1", "#33475f"),
         "purple": ("#e7dcf8", "#4a2a86"), "blue": ("#dce9ff", "#1c4590"), "grey": ("#e6e9ee", "#4a5568"),
         "red": ("#f8d7d7", "#8a1f1f")}
INLINE_NOTE =re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")


def stamp(value):
    if not value:
        return ""
    try:
        return dt.datetime.fromisoformat(value).astimezone().strftime("%d %b %H:%M:%S")
    except ValueError:
        return value


class Activity(NamedTuple):
    key: str
    time: str
    title: str
    actor: str
    sections: tuple[tuple[str, str], ...]


def display_value(value):
    if isinstance(value, dict):
        return "\n".join(f"{key}: {display_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(f"• {display_value(item)}" for item in value)
    return "" if value is None else str(value)


def task_activity(task, requests):
    records = []
    for index, event in enumerate(task["events"]):
        kind = event["kind"]
        title = {
            "created": "Task created", "checkpoint": "Checkpoint updated",
            "file written": "File updated", "lead transferred": "Lead switched",
        }.get(kind, kind.replace("run ", "turn ", 1).capitalize() if kind.startswith("run ") else kind.capitalize())
        actor = NAMES.get(event.get("lead"), "")
        fields = event.get("fields", {})
        sections = [(FIELDS.get(key, key), display_value(value)) for key, value in fields.items()]
        for key, label in (
            ("detail", "Summary"), ("path", "File"), ("metrics", "Measured results"),
            ("generation", "Generation"), ("experiment", "Experiment ID"), ("run", "Run ID"),
        ):
            if key in event:
                sections.append((label, display_value(event[key])))
        if event.get("outgoing") and event.get("incoming"):
            actor = " → ".join(NAMES.get(event[key], event[key]) for key in ("outgoing", "incoming"))
        records.append(Activity(f"{task['id']}-event-{index}", event["at"], title, actor, tuple(sections)))
    for item in requests:
        if item["task_id"] != task["id"]:
            continue
        request_id = item["id"]
        sender = NAMES.get(item["sender"], "You")
        adviser = NAMES[item["recipient"]]
        sections = [("Question", item["question"])]
        if item["context"]:
            sections.append(("Shared context", item["context"]))
        if item["status"] in {"queued", "running"}:
            sections.append(("Status", "Awaiting a reply."))
        records.append(Activity(f"{request_id}-question", item["created_at"], "Review requested",
                                f"{sender} → {adviser}", tuple(sections)))
        if item["status"] in {"answered", "failed", "cancelled"}:
            title = "Adviser reply" if item["answer"] else f"Review {item['status']}"
            sections = ([("Reply", item["answer"])] if item["answer"] else
                        [("Status", item["error"] or title + ".")])
            sections.append(("Original question", item["question"]))
            records.append(Activity(f"{request_id}-reply", item["updated_at"], title, adviser, tuple(sections)))
    # Two entries can share a timestamp on Windows. Show the later entry first.
    return sorted(reversed(records), key=lambda entry: entry.time, reverse=True)


def start_blocker(task, state):
    if not task:
        return "Select or add a task to start."
    if task["status"] == "completed":
        return "This task is complete. Add a task to continue the project."
    if task["transfer"]:
        return "A lead switch is in progress."
    if task["active"]:
        return "This turn is already in progress."
    running = next((other for other in state["tasks"].values() if other["active"]), None)
    if running:
        name = state["projects"][running["project"]]["name"]
        return f"A turn in {name} is in progress. Wait for it to finish or stop it from that task."
    if state["limited"].get(task["lead"]):
        return f"{NAMES[task['lead']]} reached a usage limit. Switch leads or check Connections after it resets."
    return ""


def task_status(task):
    """Return (pill text, (background, foreground), plain-language line) for a task."""
    lead = NAMES[task["lead"]]
    active, status = task["active"], task["status"]
    if task["transfer"]:
        text = f"{lead} → {NAMES[task['transfer']['to']]} when the current work stops"
        return "Waiting for handover", PILLS["purple"], text
    if active and active["control"]:
        stopping = active["control"] == "stop"
        return ("Stopping" if stopping else "Finishing"), PILLS["amber"], (
            "Stopping the current work" if stopping else "Pausing after the current work")
    if status in {"working", "queued"} or active:
        return "Running", PILLS["green"], f"{lead} is " + ("working" if status == "working" else "starting")
    known = {"ready": ("Ready", "blue", "Start when ready"), "paused": ("Paused", "amber", "Resume when ready"),
             "stopped": ("Stopped", "grey", "Resume when ready"),
             "completed": ("Completed", "slate", ""),
             "failed": ("Failed", "red", "Check the Timeline, then resume"),
             "limit reached": ("Usage limit", "amber", f"{lead} reached its limit"),
             "interrupted": ("Interrupted", "amber", "Recover from the More menu, then resume")}
    label, colour, text = known.get(status, (status.capitalize(), "grey", ""))
    return label, PILLS[colour], text


def insert_note(area, note):
    code = False
    for line in note.split("\n"):
        if line.strip().startswith("```"):
            code = not code
            continue
        if code:
            area.insert("end", line + "\n", "code")
            continue
        heading = re.match(r"^#{1,6}\s+(.+)", line)
        if heading:
            area.insert("end", heading[1] + "\n", "section")
            continue
        quote = line.startswith("> ")
        if quote:
            line = line[2:]
        line = re.sub(r"^(\s*)[-*] ", r"\1• ", line)
        for index, part in enumerate(INLINE_NOTE.split(line)):
            if index % 2 == 0:
                area.insert("end", part, "muted" if quote else ())
            elif part.startswith("**"):
                area.insert("end", part[2:-2], "emphasis")
            else:
                area.insert("end", part[1:-1], "code")
        area.insert("end", "\n")


class Form(tk.Toplevel):
    def __init__(self, parent, title, fields, submit, caption=""):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.configure(bg=BG)
        self.geometry("+%d+%d" % (parent.winfo_rootx() + 80, parent.winfo_rooty() + 65))
        self.widgets = {}
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="Title.TLabel").pack(anchor="w", pady=(0, 7))
        if caption:
            ttk.Label(body, text=caption, wraplength=535, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        for key, label, kind, value in fields:
            ttk.Label(body, text=label, style="Strong.TLabel").pack(anchor="w", pady=(8, 4))
            if kind == "text":
                widget = tk.Text(body, height=4, width=65, wrap="word", font=("Segoe UI", 10),
                                 background=CARD, foreground=INK, relief="flat", padx=7, pady=5, undo=True)
                widget.insert("1.0", value)
            elif kind == "choice":
                widget = ttk.Combobox(body, values=value, state="readonly", width=63)
                widget.current(0)
            else:
                widget = ttk.Entry(body, width=65)
                widget.insert(0, value)
            widget.pack(fill="x")
            self.widgets[key] = widget
        bar = ttk.Frame(body)
        bar.pack(anchor="e", pady=(15, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=5)

        def save():
            values = {k: (w.get("1.0", "end-1c") if isinstance(w, tk.Text) else w.get())
                      for k, w in self.widgets.items()}
            try:
                submit(values)
            except (BridgeError, OSError, ValueError) as exc:
                messagebox.showerror("AI Bridge", str(exc), parent=self)
            else:
                self.destroy()

        ttk.Button(bar, text="Save", style="Primary.TButton", command=save).pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        if self.widgets:
            next(iter(self.widgets.values())).focus_set()


class App(tk.Tk):
    def __init__(self, base):
        super().__init__()
        self.title("AI Bridge | Collaboration workspace")
        self.geometry("1150x760")
        self.minsize(930, 625)
        self.configure(bg=BG)
        self.store = Store(base)
        self.state = None
        self.project_id = self.task_id = None
        self.results = queue.Queue()
        self.connection_busy = False
        self.connection_pending = False
        self.connection_results = {}
        self.login_processes = {}
        self.last_activity = None
        self.activity_rows = {}
        self.activity_selection = None
        self.style()
        self.layout()
        self.refresh()
        self.after(180, self.drain_results)
        self.after(350, self.refresh_connections)

    def style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 6))
        style.configure("Primary.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT)
        style.map("Primary.TButton", background=[("active", "#18418b"), ("disabled", "#a7b4ca")])
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 19))
        style.configure("Muted.TLabel", foreground=QUIET)
        style.configure("Strong.TLabel", font=("Segoe UI Semibold", 10))
        style.configure("Badge.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 11))
        style.configure("Treeview", background=CARD, fieldbackground=CARD, rowheight=31,
                        borderwidth=0, relief="flat")
        style.map("Treeview", background=[("selected", "#dce9ff")], foreground=[("selected", INK)])
        style.configure("Treeview.Heading", background="#e4ebf5", font=("Segoe UI Semibold", 10))
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(13, 8))
        style.map("TNotebook.Tab", background=[("selected", CARD)])

    def layout(self):
        mast = ttk.Frame(self, padding=(20, 16, 20, 12))
        mast.pack(fill="x")
        ttk.Label(mast, text="AI Bridge", style="Title.TLabel").pack(side="left")
        ttk.Label(mast, text="LOCAL COLLABORATION WORKSPACE", style="Muted.TLabel").pack(side="left", padx=17)
        ttk.Button(mast, text="Connections", command=self.show_connections).pack(side="right")
        ttk.Button(mast, text="Mailbox history", command=lambda: self.guard(self.show_mailbox)).pack(side="right", padx=7)
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        side = ttk.Frame(body, padding=(0, 0, 12, 0))
        detail = ttk.Frame(body, padding=(9, 0, 0, 0))
        body.add(side, weight=1)
        body.add(detail, weight=4)
        side.configure(width=265)
        ttk.Label(side, text="PROJECTS", style="Strong.TLabel").pack(anchor="w", pady=(0, 5))
        self.projects = ttk.Treeview(side, show="tree", height=6, selectmode="browse")
        self.projects.column("#0", width=240, minwidth=180)
        self.projects.pack(fill="both", expand=True)
        self.projects.bind("<<TreeviewSelect>>", self.select_project)
        row = ttk.Frame(side)
        row.pack(fill="x", pady=(7, 15))
        ttk.Button(row, text="+ Project", command=self.add_project).pack(side="left")
        ttk.Button(row, text="Approved commands", command=self.edit_commands).pack(side="right")
        ttk.Label(side, text="TASKS", style="Strong.TLabel").pack(anchor="w", pady=(0, 5))
        self.tasks = ttk.Treeview(side, show="tree", height=7, selectmode="browse")
        self.tasks.column("#0", width=240, minwidth=180)
        self.tasks.pack(fill="both", expand=True)
        self.tasks.bind("<<TreeviewSelect>>", self.select_task)
        ttk.Button(side, text="+ Task", command=self.add_task).pack(anchor="w", pady=(7, 0))

        self.title_label = ttk.Label(detail, text="Select a project", style="Title.TLabel", wraplength=760)
        self.title_label.pack(anchor="w", pady=(0, 4))
        self.path_label = ttk.Label(detail, style="Muted.TLabel", wraplength=730)
        self.path_label.pack(anchor="w")
        status_row = ttk.Frame(detail)
        status_row.pack(fill="x", pady=(6, 1))
        self.pill = tk.Label(status_row, font=("Segoe UI Semibold", 9), padx=10, pady=2)
        self.badge = ttk.Label(status_row, style="Badge.TLabel", wraplength=650)
        self.badge.pack(side="left", padx=(0, 0))
        self.next_label = ttk.Label(detail, style="Muted.TLabel", wraplength=750)
        self.next_label.pack(anchor="w", pady=(0, 6))
        self.start_hint = ttk.Label(detail, style="Muted.TLabel", wraplength=640)
        self.primary_actions = bar = ttk.Frame(detail)
        bar.pack(fill="x", pady=(0, 5))
        self.primary_button = ttk.Button(bar, style="Primary.TButton")
        self.stop_button = ttk.Button(bar, text="Stop", command=lambda: self.guard(lambda: self.control("stop")))
        self.handover_button = ttk.Button(bar, command=lambda: self.guard(self.switch))
        self.opinion_button = ttk.Button(bar, text="Get a second opinion", command=lambda: self.guard(self.review))
        self.more_menu = tk.Menu(bar, tearoff=0)
        for key, label, command in (("edit", "Edit checkpoint…", self.edit_checkpoint),
                                    ("open_checkpoint", "Open checkpoint file", self.open_checkpoint),
                                    ("folder", "Open project folder", self.open_workspace),
                                    ("recover", "Recover interrupted run", self.recover)):
            self.more_menu.add_command(label=label, command=lambda fn=command: self.guard(fn))
        self.more_keys = ("edit", "open_checkpoint", "folder", "recover")
        self.more_button = ttk.Menubutton(bar, text="More ▾", menu=self.more_menu)
        self.action_widgets = (self.primary_button, self.stop_button, self.handover_button,
                               self.opinion_button, self.more_button)
        self.tabs = ttk.Notebook(detail)
        self.tabs.pack(fill="both", expand=True, pady=(5, 0))
        self.pages = {}
        for label in ("Checkpoint", "Timeline", "Experiments", "Diagnostics"):
            page = ttk.Frame(self.tabs)
            self.tabs.add(page, text=label)
            if label == "Diagnostics":
                ttk.Button(page, text="Refresh run logs", command=lambda: self.guard(self.logs)).pack(anchor="w", padx=10, pady=8)
            if label == "Timeline":
                self.layout_timeline(page)
                continue
            self.pages[label] = self.reader(page)
        self.pages["Checkpoint"].tag_configure("section", foreground=ACCENT, font=("Segoe UI Semibold", 12))
        foot = ttk.Frame(self, padding=(20, 4, 20, 11))
        foot.pack(fill="x")
        self.notice = ttk.Label(foot, text="Local state and transcripts stay in this bridge folder.", style="Muted.TLabel")
        self.notice.pack(side="left")
        self.health = ttk.Label(foot, text="Checking CLI connections…", style="Muted.TLabel")
        self.health.pack(side="right")

    def layout_timeline(self, page):
        header = ttk.Frame(page, padding=(1, 8, 1, 7))
        header.pack(fill="x")
        ttk.Label(header, text="Task activity", style="Strong.TLabel").pack(side="left")
        ttk.Label(header, text="Newest first · Select an entry to read it",
                  style="Muted.TLabel").pack(side="left", padx=12)
        split = ttk.PanedWindow(page, orient="horizontal")
        split.pack(fill="both", expand=True)
        index, detail = ttk.Frame(split), ttk.Frame(split)
        split.add(index, weight=0)
        split.add(detail, weight=1)
        scroll = ttk.Scrollbar(index)
        scroll.pack(side="right", fill="y")
        self.activity_list = ttk.Treeview(index, columns=("time", "activity"), show="headings",
                                          selectmode="browse", yscrollcommand=scroll.set)
        self.activity_list.heading("time", text="Time")
        self.activity_list.heading("activity", text="Activity")
        self.activity_list.column("time", width=103, minwidth=90, stretch=False)
        self.activity_list.column("activity", width=127, minwidth=105)
        self.activity_list.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.activity_list.yview)
        self.activity_list.bind("<<TreeviewSelect>>", self.read_activity)
        area = self.pages["Timeline"] = self.reader(detail)
        area.tag_configure("activity_title", font=("Segoe UI Semibold", 17), foreground=INK)
        area.tag_configure("section", foreground=ACCENT, font=("Segoe UI Semibold", 11), spacing3=4)
        area.tag_configure("emphasis", font=("Segoe UI Semibold", 11))
        area.tag_configure("code", font=("Consolas", 10), foreground="#253b6d")
        area.tag_configure("muted", foreground=QUIET)

    def reader(self, parent):
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(holder)
        scroll.pack(side="right", fill="y")
        text = tk.Text(holder, wrap="word", state="disabled", yscrollcommand=scroll.set, height=5,
                       font=("Segoe UI", 11), background=CARD, foreground=INK, relief="flat",
                       padx=16, pady=13, spacing1=2, spacing3=4)
        text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=text.yview)
        return text

    def show(self, area, value):
        if area.get("1.0", "end-1c") == value:
            return
        position = area.yview()[0]
        area.configure(state="normal")
        area.delete("1.0", "end")
        area.insert("1.0", value)
        area.configure(state="disabled")
        area.yview_moveto(position)

    def guard(self, action):
        try:
            return action()
        except (BridgeError, OSError, ValueError) as exc:
            messagebox.showerror("AI Bridge", str(exc), parent=self)

    def apply(self, action, message):
        action()
        self.notice.configure(text=message)
        self.sync()

    def refresh(self):
        try:
            self.sync()
        except (OSError, ValueError, BridgeError) as exc:
            self.notice.configure(text=str(exc)[:170])
        self.after(2300, self.refresh)

    def rows(self, tree, items, selected):
        current = tree.get_children()
        if current != tuple(items):
            tree.delete(*current)
            for key, name in items.items():
                tree.insert("", "end", iid=key, text=name)
            if selected in items:
                tree.selection_set(selected)
        else:
            for key, name in items.items():
                if tree.item(key, "text") != name:
                    tree.item(key, text=name)

    def sync(self):
        new = self.store.read()
        changed = new != self.state
        self.state = new
        projects = new["projects"]
        if self.project_id not in projects:
            self.project_id = next(iter(projects), None)
        tasks = {key: task for key, task in new["tasks"].items() if task["project"] == self.project_id}
        if self.task_id not in tasks:
            self.task_id = next(reversed(tasks), None)
        self.rows(self.projects, {
            key: (("●  " if any(t["project"] == key and t["active"] for t in new["tasks"].values())
                   else "") + p["name"]) for key, p in projects.items()}, self.project_id)
        self.rows(self.tasks, {key: f"{t['status'].capitalize()}  |  {t['objective'][:75]}"
                               for key, t in tasks.items()}, self.task_id)
        if self.project_id and self.projects.selection() != (self.project_id,):
            self.projects.selection_set(self.project_id)
        if self.task_id and self.tasks.selection() != (self.task_id,):
            self.tasks.selection_set(self.task_id)
        if changed or self.last_activity != self.task_id:
            self.render()
        self.timeline()

    def project(self):
        if not self.project_id:
            raise BridgeError("Add or select a project first.")
        return self.store.project(self.store.read(), self.project_id)

    def task(self):
        if not self.task_id:
            raise BridgeError("Add or select a task first.")
        return self.store.task(self.store.read(), self.task_id)

    def select_project(self, _):
        selection = self.projects.selection()
        if selection and selection[0] != self.project_id:
            self.project_id, self.task_id, self.last_activity = selection[0], None, False
            self.sync()

    def select_task(self, _):
        selection = self.tasks.selection()
        if selection and selection[0] != self.task_id:
            self.task_id = selection[0]
            self.sync()

    def render(self):
        project = self.state["projects"].get(self.project_id)
        task = self.state["tasks"].get(self.task_id)
        self.title_label.configure(text=project["name"] if project else "Set up your first project")
        self.path_label.configure(text=project["path"] if project else "Choose the folder where the assistants will work.")
        self.badge.configure(text="")
        self.pill.pack_forget()
        self.next_label.configure(text="")
        if self.last_activity != self.task_id:
            self.show(self.pages["Diagnostics"], "Run logs stay local. Select Refresh run logs to inspect the latest turn.")
        self.last_activity = self.task_id
        self.render_actions(project, task)
        terminal = task and task["status"] == "completed"
        if not task:
            self.show(self.pages["Checkpoint"], "Add a project and a task to begin.")
            self.show(self.pages["Experiments"], "No experiments recorded.")
            return
        label, colours, text = task_status(task)
        self.pill.configure(text=label, bg=colours[0], fg=colours[1])
        self.pill.pack(side="left", padx=(0, 10), before=self.badge)
        self.badge.configure(text=text)
        next_step = task["checkpoint"]["next"] or task["objective"]
        self.next_label.configure(text="Task complete" if terminal else "Next: " + next_step[:350])
        self.render_checkpoint(task)
        if task["experiments"]:
            rows = []
            for exp in reversed(task["experiments"]):
                rows.append(f"{exp['status'].capitalize()}  |  {exp['command']}  |  {stamp(exp['started_at'])}\n"
                            f"Split: {exp['split']}\nArtifacts: {exp['artifact']}\n"
                            f"Observed results: {json.dumps(exp['metrics'], ensure_ascii=False)}\n"
                            f"Configuration: {json.dumps(exp['metadata'], ensure_ascii=False, indent=2)}\n"
                            f"Environment: {json.dumps(exp.get('environment', {}), ensure_ascii=False)}\n"
                            f"Code: {json.dumps(exp['code'], ensure_ascii=False)}")
            self.show(self.pages["Experiments"], "\n\n".join(rows))
        else:
            self.show(self.pages["Experiments"], "No experiments recorded.")

    def render_actions(self, project, task):
        """Show only the actions that apply to the selected task."""
        active = task["active"] if task else None
        completed = bool(task) and task["status"] == "completed"
        pending = bool(task) and bool(active and active["control"])
        reason = start_blocker(task, self.state)
        visible = []
        if active:
            self.primary_button.configure(text="Pause", command=lambda: self.guard(lambda: self.control("pause")),
                                          state="disabled" if pending else "normal")
            visible += [self.primary_button, self.stop_button]
            reason = ""
        elif not completed:
            fresh = bool(task) and task["status"] == "ready"
            self.primary_button.configure(text="Start" if fresh or not task else "Resume",
                                          command=lambda: self.guard(self.start),
                                          state="disabled" if reason else "normal")
            visible.append(self.primary_button)
        else:
            reason = ""
        if task and not completed:
            other = "claude" if task["lead"] == "chatgpt" else "chatgpt"
            self.handover_button.configure(text="Hand over to " + SHORT[other])
            if not task["transfer"]:
                visible.append(self.handover_button)
        if task and not pending:
            visible.append(self.opinion_button)
        if project:
            visible.append(self.more_button)
        for widget in self.action_widgets:
            widget.pack_forget()
        for widget in visible:
            widget.pack(side="left", padx=(0, 5))
        allowed = {"edit": task and not active, "open_checkpoint": task, "folder": project, "recover": active}
        for index, key in enumerate(self.more_keys):
            self.more_menu.entryconfigure(index, state="normal" if allowed[key] else "disabled")
        if reason:
            self.start_hint.configure(text=reason)
            if not self.start_hint.winfo_manager():
                self.start_hint.pack(after=self.primary_actions, anchor="w", pady=(0, 5))
        else:
            self.start_hint.pack_forget()

    def render_checkpoint(self, task):
        sections = [
            ("Objective", task["objective"]),
            ("User constraints", task["constraints"]),
            *[(label, task["checkpoint"][key]) for key, label in FIELDS.items()],
            ("Working tree", "\n".join(f"{key.capitalize()}: {value}" for key, value in task["files"].items()
                                      if value)),
        ]
        area = self.pages["Checkpoint"]
        content = "\n\n".join(title + "\n" + (value or "None recorded.") for title, value in sections)
        if area.get("1.0", "end-1c") != content:
            self.show(area, content)
            line = 1
            for _, value in sections:
                area.tag_add("section", f"{line}.0", f"{line}.end")
                line += (value or "None recorded.").count("\n") + 3

    def timeline(self):
        task = self.state["tasks"].get(self.task_id) if self.state else None
        records = []
        if task:
            box = Mailbox(self.store.base, config_for(self.store.base), external_workers=True)
            records = task_activity(task, box.listing(limit=None))
        rows = {entry.key: entry for entry in records}
        if tuple(rows.items()) != tuple(self.activity_rows.items()):
            selected = next(iter(self.activity_list.selection()), None)
            follow_latest = selected == next(iter(self.activity_rows), None)
            self.activity_rows = rows
            self.activity_list.delete(*self.activity_list.get_children())
            for entry in records:
                self.activity_list.insert("", "end", iid=entry.key, values=(stamp(entry.time), entry.title))
            if records:
                choice = selected if selected in rows and not follow_latest else records[0].key
                self.activity_list.selection_set(choice)
                self.activity_list.focus(choice)
                self.activity_list.see(choice)
        self.read_activity()

    def read_activity(self, _event=None):
        key = next(iter(self.activity_list.selection()), None)
        entry = self.activity_rows.get(key)
        area = self.pages["Timeline"]
        if entry is None:
            self.activity_selection = None
            empty = "No activity yet." if self.task_id else "Select a task to view its history."
            self.show(area, empty)
            return
        if entry == self.activity_selection:
            return
        self.activity_selection = entry
        area.configure(state="normal")
        area.delete("1.0", "end")
        area.insert("end", entry.title + "\n", "activity_title")
        byline = "   ·   ".join(part for part in (entry.actor, stamp(entry.time)) if part)
        area.insert("end", byline + "\n\n", "muted")
        for label, note in entry.sections:
            if note:
                area.insert("end", label + "\n", "section")
                insert_note(area, note)
                area.insert("end", "\n")
        area.configure(state="disabled")
        area.yview_moveto(0)

    def add_project(self):
        folder = filedialog.askdirectory(parent=self, mustexist=True, title="Choose a local project workspace")
        if not folder:
            return
        def save(v):
            self.project_id = self.store.add_project(v["name"], folder)
            self.sync()
        Form(self, "Add project", [("name", "Project name", "entry", Path(folder).name)], save, folder)

    def add_task(self):
        def show():
            project = self.project()
            def save(v):
                self.task_id = self.store.add_task(project["id"], v["objective"],
                                                  next(k for k, name in NAMES.items() if name == v["lead"]),
                                                  v["constraints"])
                self.sync()
            Form(self, "New task", [("objective", "Objective", "text", ""),
                                    ("constraints", "Constraints", "text", ""),
                                    ("lead", "Starting lead", "choice", tuple(NAMES.values()))], save)
        self.guard(show)

    def edit_commands(self):
        def show():
            project = self.project()
            Form(self, "Approved commands",
                 [("commands", "JSON: name → [absolute executable, argument, …]", "text",
                   json.dumps(project["commands"], ensure_ascii=False, indent=2))],
                 lambda v: self.apply(lambda: self.store.set_commands(project["id"], json.loads(v["commands"])),
                                      "Approved commands saved."),
                 "Commands run with your Windows account in this project. Approve only trusted scripts. "
                 "Use AI_BRIDGE_RUN_DIR for run artifacts and optional metrics.json.")
        self.guard(show)

    def start(self):
        task = self.task()
        self.apply(lambda: launch(self.store, task["id"], self.store.start(task["id"])), "Lead turn scheduled.")

    def control(self, action):
        task = self.task()
        if action == "stop" and not messagebox.askyesno("Stop execution", "Stop the active turn and its local jobs?", parent=self):
            return
        self.apply(lambda: self.store.control(task["id"], action), action.capitalize() + " requested.")

    def switch(self):
        task = self.task()
        incoming = "claude" if task["lead"] == "chatgpt" else "chatgpt"
        choices = ("Finish current work first", "Stop current work immediately")
        after = ("Continue under the new lead", "Switch and leave paused")
        advisers = ("Continue from disk only", "Allow questions to the outgoing lead")
        first = after if not self.state["limited"].get(incoming) else tuple(reversed(after))
        def save(v):
            self.apply(lambda: launch(self.store, task["id"], self.store.control(
                task["id"], "switch", incoming=incoming, resume=v["after"] == after[0],
                interrupt=v["timing"] == choices[1], allow_outgoing_review=v["advice"] == advisers[1])),
                "Takeover requested.")
        Form(self, "Switch lead to " + NAMES[incoming],
             [("timing", "If a turn is active", "choice", choices),
              ("after", "After saving the checkpoint", "choice", first),
              ("advice", "Outgoing assistant", "choice", advisers)], save,
             "The new lead receives the saved task and file state. The outgoing assistant does not have to reply.")

    def review(self):
        task = self.task()
        peer = "claude" if task["lead"] == "chatgpt" else "chatgpt"
        def save(v):
            if self.store.read()["limited"].get(peer):
                raise BridgeError("The adviser reported a usage limit. Wait for the reset or continue without advice.")
            box = Mailbox(self.store.base, config_for(self.store.base), external_workers=True)
            self.apply(lambda: box.submit({"sender": "user", "recipient": peer, "mode": v["mode"],
                                            "task_id": task["id"], "question": v["question"],
                                            "context": v["context"]}), "Review request saved.")
        Form(self, "Ask " + NAMES[peer],
             [("question", "Question", "text", ""), ("context", "Relevant context (optional)", "text", ""),
              ("mode", "Delivery", "choice", ("auto", "manual"))], save,
             "Automatic uses the adviser’s subscription. Manual waits for an assistant to check its inbox.")

    def edit_checkpoint(self):
        task = self.task()
        Form(self, "Update checkpoint",
             [("field", "Checkpoint section", "choice", tuple(FIELDS.values())),
              ("text", "Add a note to the existing section", "text", "")],
             lambda v: self.append_checkpoint(task, v))

    def append_checkpoint(self, task, v):
        field = next(k for k, label in FIELDS.items() if label == v["field"])
        value = (task["checkpoint"][field] + "\n" + v["text"].strip()).strip()
        self.apply(lambda: self.store.update(task["id"], {field: value}, revision=task["revision"]),
                   "Checkpoint saved.")

    def open_checkpoint(self):
        os.startfile(self.store.repair_checkpoint(self.task()["id"]))

    def open_workspace(self):
        os.startfile(self.project()["path"])

    def recover(self):
        task = self.task()
        if messagebox.askyesno("Recover interrupted run",
                               "Check that the old worker is gone and recover its saved state without rerunning it?",
                               parent=self):
            self.apply(lambda: recover(self.store, task["id"]), "Recovery checked. Review the checkpoint before resuming.")

    def logs(self):
        task = self.task()
        latest = next((e["run"] for e in reversed(task["events"]) if "run" in e), None)
        if not latest:
            self.show(self.pages["Diagnostics"], "There are no lead turns yet.")
            return
        directory = self.store.run_dir(latest)
        self.show(self.pages["Diagnostics"], f"Run {latest}\n{directory}\n\nSTDERR (tail)\n"
                  + (tail(directory / "stderr.log", 7000) or "No stderr.")
                  + "\n\nPROVIDER OUTPUT (tail)\n" + (tail(directory / "provider.jsonl", 18000) or "No output.")
                  + "\n\nSUPERVISOR LOG (tail)\n" + (tail(self.store.home / "supervisor.log", 7000) or "No errors."))

    def refresh_connections(self):
        popup = getattr(self, "connection_window", None)
        focused_and_disconnected = self.focus_displayof() is not None and any(
            not result[0] for result in self.connection_results.values()
        )
        if not self.connection_busy and (
            not self.connection_results or self.login_processes or
            focused_and_disconnected or
            (popup and popup.winfo_exists() and popup.winfo_viewable())
        ):
            self.connections()
        self.after(5000, self.refresh_connections)

    def connections(self):
        if self.connection_busy:
            self.connection_pending = True
            return
        self.connection_busy = True
        def check():
            found = {}
            for peer in NAMES:
                command = []
                try:
                    command = executable_for(peer, self.store.base, config_for(self.store.base))
                    auth_status(peer, command, self.store.base)
                    found[peer] = (True, command, "Subscription login detected.")
                except (BridgeError, OSError, ValueError) as exc:
                    found[peer] = (False, command, str(exc))
            self.results.put(found)
        threading.Thread(target=check, daemon=True).start()

    def drain_results(self):
        try:
            self.connection_results = self.results.get_nowait()
            self.connection_busy = False
            self.health.configure(text="   ·   ".join(f"{NAMES[k]}: {'ready' if v[0] else 'disconnected'}"
                                                       for k, v in self.connection_results.items()))
            self.update_connections_panel()
            if self.connection_pending:
                self.connection_pending = False
                self.connections()
        except queue.Empty:
            pass
        finally:
            self.after(180, self.drain_results)

    def show_connections(self):
        if getattr(self, "connection_window", None) and self.connection_window.winfo_exists():
            self.connection_window.lift()
            self.connections()
            return
        popup = tk.Toplevel(self)
        self.connection_window = popup
        popup.title("AI Bridge | Connections")
        popup.geometry("790x440")
        row = ttk.Frame(popup, padding=10)
        row.pack(fill="x")
        for label, cmd in [
            ("Check connections", self.connections), ("Sign in to Claude", lambda: self.sign_in("claude")),
            ("Sign in to Codex", lambda: self.sign_in("chatgpt")), ("CLI settings", self.edit_paths),
        ]:
            ttk.Button(row, text=label, command=lambda fn=cmd: self.guard(fn)).pack(side="left", padx=4)
        options = ttk.Frame(popup, padding=(10, 0, 10, 8))
        options.pack(fill="x")
        for label, cmd in [
            ("Clear Claude limit", lambda: self.clear_limit("claude")),
            ("Clear Codex limit", lambda: self.clear_limit("chatgpt")),
        ]:
            ttk.Button(options, text=label, command=lambda fn=cmd: self.guard(fn)).pack(side="left", padx=4)
        self.connection_text = self.reader(popup)
        self.connections()
        self.update_connections_panel()

    def sign_in(self, peer):
        if peer in self.login_processes:
            raise BridgeError("A sign-in window for this assistant is already open.")
        try:
            command = executable_for(peer, self.store.base, config_for(self.store.base))
        except BridgeError as exc:
            raise BridgeError(f"{NAMES[peer]} isn't installed or detected. Install its CLI, "
                              "or use CLI settings to point to an existing installation.") from exc
        env = clean_env()
        env.pop("AI_BRIDGE_WORKER", None)
        args = ["auth", "login"] if peer == "claude" else ["login"]
        self.login_processes[peer] = subprocess.Popen(command + args, cwd=self.store.base, env=env,
                                                       creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.update_connections_panel()
        self.after(500, lambda: self.watch_sign_in(peer))

    def watch_sign_in(self, peer):
        if self.login_processes[peer].poll() is None:
            self.after(500, lambda: self.watch_sign_in(peer))
        else:
            del self.login_processes[peer]
            self.connections()
            self.update_connections_panel()

    def show_mailbox(self):
        popup = tk.Toplevel(self)
        popup.title("AI Bridge | Mailbox history")
        popup.geometry("890x580")
        header = ttk.Frame(popup, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="Mailbox history", style="Title.TLabel").pack(side="left")
        panel = self.reader(popup)
        def refresh():
            box = Mailbox(self.store.base, config_for(self.store.base), external_workers=True)
            items = box.listing(limit=None)
            self.show(panel, "\n\n".join(
                f"{stamp(i['created_at'])}  |  {i['task_id']}  |  {i['status'].capitalize()}\n"
                f"{NAMES.get(i['sender'], 'You')} → {NAMES[i['recipient']]}  |  {i['id']}\n"
                f"{i['question']}" + (f"\n\nContext: {i['context']}" if i["context"] else "") +
                (f"\n\nAdviser suggestion: {i['answer']}" if i["answer"] else "") +
                (f"\n\nError: {i['error']}" if i["error"] else "")
                for i in items) or "No mailbox requests yet.")
        ttk.Button(header, text="Refresh", command=lambda: self.guard(refresh)).pack(side="right")
        refresh()

    def update_connections_panel(self):
        if not hasattr(self, "connection_text") or not self.connection_text.winfo_exists():
            return
        limits = (self.state or {}).get("limited", {})
        values = ["If both assistants show Ready, you can start a task. If one is signed out, "
                  "use its Sign in button and choose your subscription account."]
        for peer, name in NAMES.items():
            ready, cmd, detail = self.connection_results.get(peer, (False, [], "Checking connection…"))
            status = ("Limit reached" if ready and peer in limits else
                      "Ready" if ready else "Signing in…" if peer in self.login_processes else
                      "Sign-in required" if cmd else
                      "Checking…" if self.connection_busy else "CLI not found")
            if not ready and not cmd and not self.connection_busy:
                detail = "Install its CLI or select the existing executable under CLI settings."
            values.append(f"{name}  |  {status}\n"
                          + ("CLI: " + subprocess_display(cmd) + "\n" if cmd else "")
                          + detail + ("\nLast limit report: " + stamp(limits[peer]) if peer in limits else ""))
        values.append("Remaining subscription usage is unavailable.")
        self.show(self.connection_text, "\n\n".join(values))

    def edit_paths(self):
        config = config_for(self.store.base)
        def save(v):
            latest = config_for(self.store.base)
            latest.update({k: v[k].strip() or None for k in ("claude_path", "codex_path")})
            latest["lead_timeout_seconds"] = int(v["lead_timeout_seconds"])
            lead_timeout(latest)
            atomic_json(self.store.base / "bridge-config.json", latest)
            self.connections()
        Form(self, "CLI settings",
             [("claude_path", "Claude Code executable", "entry", config.get("claude_path") or ""),
              ("codex_path", "Codex executable", "entry", config.get("codex_path") or ""),
              ("lead_timeout_seconds", "Lead turn limit in seconds", "entry",
               str(config.get("lead_timeout_seconds", config["timeout_seconds"])))],
             save, "Leave executable paths blank for automatic detection. Raise the turn limit for longer checks.")

    def clear_limit(self, peer):
        self.store.clear_limit(peer)
        self.sync()
        self.update_connections_panel()


def subprocess_display(args):
    return "  ".join(args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--smoke", action="store_true", help="Create and render the window, then close it.")
    args = parser.parse_args()
    app = App(args.base)
    if args.smoke:
        app.after(1400, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
