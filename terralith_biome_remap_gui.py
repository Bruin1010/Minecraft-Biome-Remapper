from __future__ import annotations

import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
import sys
import webbrowser
import tkinter.font as tkfont
import platform

import multiprocessing

import terralith_biome_remap_standalone as core


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _show(self, event=None):
        if self.tip or not self.text:
            return
        # Prefer mouse position for accurate placement on container widgets (eg Labelframe).
        if event is not None and getattr(event, "x_root", None) is not None and getattr(event, "y_root", None) is not None:
            x = int(event.x_root) + 12
            y = int(event.y_root) + 12
        else:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        lbl = ttk.Label(self.tip, text=self.text, justify="left", padding=8)
        lbl.configure(style="Tooltip.TLabel")
        lbl.pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def add_tooltip(widget: tk.Widget, text: str) -> None:
    ToolTip(widget, text)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Minecraft Biome Remapper V1.1")
        self.geometry("1100x650")
        self.minsize(900, 520)

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._is_running = tk.BooleanVar(value=False)

        self.world_path = tk.StringVar(value="")
        # Start blank. If blank, the program uses the built-in default mapping.
        self.mapping_ini = tk.StringVar(value="")
        self.dimension = tk.StringVar(value="overworld")
        self.processes = tk.IntVar(value=(os.cpu_count() or 1))
        self.backups = tk.BooleanVar(value=True)
        self.dry_run = tk.BooleanVar(value=False)
        self.unmapped_fallback = tk.StringVar(value="")

        self.use_y_filter = tk.BooleanVar(value=False)
        self.y_min = tk.IntVar(value=0)
        self.y_max = tk.IntVar(value=320)

        self.probe_prefix = tk.StringVar(value="")
        self.probe_max_regions = tk.IntVar(value=200)
        self.probe_max_chunks = tk.IntVar(value=200000)

        # Debug is CLI-only (intentionally not exposed in the GUI)

        self._set_app_icon()
        self._build_ui()
        self.after(50, self._drain_log_queue)
        self.after(0, self._show_launch_disclaimer)

    def _set_app_icon(self) -> None:
        """
        Set window/taskbar icon.
        - In a packaged EXE, use the EXE's embedded icon (no extra files needed).
        - When running from source, use app.ico (if present) next to the script.
        """
        if platform.system().lower() != "windows":
            return
        try:
            if getattr(sys, "frozen", False):
                # Use the executable icon if possible.
                try:
                    self.iconbitmap(default=sys.executable)
                    return
                except Exception:
                    return
            else:
                ico = Path(__file__).resolve().parent / "app.ico"
                if ico.exists():
                    self.iconbitmap(default=str(ico))
        except Exception:
            # If this fails, Tk will fall back to its default icon.
            return

    def _show_launch_disclaimer(self) -> None:
        """
        Show a modal disclaimer at startup. User must accept to continue.
        """
        dlg = tk.Toplevel(self)
        dlg.title("Disclaimer")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(True, True)

        # Reasonable default size; user can resize.
        dlg.geometry("780x520")

        frame = ttk.Frame(dlg, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        title = ttk.Label(frame, text="Minecraft Biome Remapper V1.1 — Read This First", font=("Segoe UI", 12, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 8))

        text = tk.Text(frame, wrap="word", height=10)
        text.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=yscroll.set)

        base_font = tkfont.nametofont(text.cget("font"))
        bold_font = base_font.copy()
        bold_font.configure(weight="bold")

        text.tag_configure("bold", font=bold_font)
        text.tag_configure("link", foreground="#1a5fb4", underline=True)

        url = "https://sawdust.catter1.com/tools/level-editor"

        msg_parts = [
            "Welcome to the Minecraft Biome Remapper tool by Bruin1010.\n\n",
            "Tested successfully for removing the Terralith datapack from Minecraft Java 1.21.5 (tested 12/14/2025).\n\n",
            "This tool is ",
            ("EXPERIMENTAL", "bold"),
            ". Use it at your own risk, and always make a ",
            ("BACKUP OF YOUR WORLD FILE", "bold"),
            " before running it.\n\n",
            "What it does:\n"
            "- Scans your world’s region files (.mca) and remaps biome IDs using either a built-in Terralith removal mapping or a custom mapping INI.\n"
            "- It does NOT delete chunks and does NOT modify blocks; it only changes biome IDs (what F3 shows, and biome-based mechanics).\n\n",
            "What it does NOT do:\n"
            "- It is not meant for removing mods/datapacks that add custom blocks/items.\n\n",
            "Important:\n"
            "- If you are removing any world-generation mod/datapack (including Terralith), you WILL need to update your world’s level.dat using the Sawdust Labs level editor:\n",
            (url, "link"),
            "\n\n",
            'This tool is provided "as-is" without warranty. The author is not responsible for any damage to your PC or Minecraft world.\n',
        ]

        for part in msg_parts:
            if isinstance(part, tuple):
                s, tag = part
                start = text.index("end-1c")
                text.insert("end", s)
                end = text.index("end-1c")
                text.tag_add(tag, start, end)
            else:
                text.insert("end", part)

        def open_link(_event=None):
            webbrowser.open(url)

        text.tag_bind("link", "<Button-1>", open_link)
        text.tag_bind("link", "<Enter>", lambda _e: text.config(cursor="hand2"))
        text.tag_bind("link", "<Leave>", lambda _e: text.config(cursor=""))

        text.configure(state="disabled")

        sep = ttk.Separator(frame, orient="horizontal")
        sep.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 10))

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)

        accepted = {"ok": False}

        def on_exit():
            accepted["ok"] = False
            dlg.destroy()

        def on_understand():
            accepted["ok"] = True
            dlg.destroy()

        ttk.Button(buttons, text="Exit", command=on_exit).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="I Understand", command=on_understand).grid(row=0, column=1, sticky="e")

        dlg.protocol("WM_DELETE_WINDOW", on_exit)

        # Center roughly over main window
        try:
            self.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() // 2) - 390
            y = self.winfo_rooty() + (self.winfo_height() // 2) - 260
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self.wait_window(dlg)
        if not accepted["ok"]:
            # Close the app if not accepted
            self.destroy()

    def _build_ui(self) -> None:
        # A tiny tooltip style
        style = ttk.Style(self)
        try:
            style.configure("Tooltip.TLabel", background="#ffffe0", relief="solid", borderwidth=1)
        except Exception:
            pass

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(0, weight=1)
        root.rowconfigure(1, weight=0)

        # Left: output "terminal"
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Output").grid(row=0, column=0, sticky="w", pady=(0, 6))

        # Wrap long lines so the user doesn't need horizontal scrolling.
        self.output = tk.Text(left, wrap="word", height=20)
        self.output.grid(row=1, column=0, sticky="nsew")
        self.output.configure(state="disabled")

        yscroll = ttk.Scrollbar(left, orient="vertical", command=self.output.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        self.output.configure(yscrollcommand=yscroll.set)

        # No horizontal scrollbar needed when wrapping is enabled.

        # Right: controls
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        # World folder
        lf_world = ttk.Labelframe(right, text="World")
        lf_world.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        lf_world.columnconfigure(0, weight=1)

        ttk.Label(lf_world, text="World folder").grid(row=0, column=0, sticky="w")
        row = ttk.Frame(lf_world)
        row.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.world_path).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="Browse…", command=self._browse_world).grid(row=0, column=1, padx=(6, 0))

        # Mapping INI
        lf_map = ttk.Labelframe(right, text="Mapping")
        lf_map.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        lf_map.columnconfigure(0, weight=1)

        mapping_label = ttk.Label(lf_map, text="Mapping INI (optional)")
        mapping_label.grid(row=0, column=0, sticky="w")
        add_tooltip(
            mapping_label,
            "Optional mapping INI.\n"
            "If left blank, the program uses a built-in default mapping designed to remap Terralith biomes to vanilla.\n"
            "INI format:\n"
            "  [mapping]\n"
            "  from_namespace:biome = to_namespace:biome\n"
            "Example:\n"
            "  terralith:yellowstone = minecraft:badlands",
        )
        row2 = ttk.Frame(lf_map)
        row2.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        row2.columnconfigure(0, weight=1)
        ttk.Entry(row2, textvariable=self.mapping_ini).grid(row=0, column=0, sticky="ew")
        ttk.Button(row2, text="Browse…", command=self._browse_ini).grid(row=0, column=1, padx=(6, 0))

        # Unmapped fallback (keep near mapping)
        unmapped_label = ttk.Label(lf_map, text="Unmapped terralith:* -> (optional)")
        unmapped_label.grid(row=2, column=0, sticky="w", pady=(6, 0))
        unmapped_entry = ttk.Entry(lf_map, textvariable=self.unmapped_fallback)
        unmapped_entry.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        add_tooltip(
            unmapped_label,
            "Optional fallback rule.\n"
            "If set, any biome starting with terralith: that is NOT listed in your mapping\n"
            "will be replaced with this biome (example: minecraft:plains).\n"
            "Leave blank to only replace biomes that are explicitly mapped.",
        )
        add_tooltip(
            unmapped_entry,
            "Example: minecraft:plains\n"
            "Leave blank to only replace biomes explicitly mapped.",
        )

        # Options
        lf_opts = ttk.Labelframe(right, text="Options")
        lf_opts.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        lf_opts.columnconfigure(0, weight=1)

        backups_cb = ttk.Checkbutton(lf_opts, text="Create .bak backups (recommended)", variable=self.backups)
        backups_cb.grid(row=0, column=0, sticky="w")
        add_tooltip(
            backups_cb,
            "If enabled, changed region files get a backup copy next to them:\n"
            "  world\\region\\r.X.Z.mca.bak\n"
            "Restore idea (advanced): delete the modified .mca and rename .mca.bak back to .mca.\n"
            "Tip: always keep a full world backup too.",
        )

        dryrun_cb = ttk.Checkbutton(lf_opts, text="Dry-run (no files modified)", variable=self.dry_run)
        dryrun_cb.grid(row=1, column=0, sticky="w")
        add_tooltip(dryrun_cb, "Does not write any files. Shows what would change.")

        row3 = ttk.Frame(lf_opts)
        row3.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        row3.columnconfigure(1, weight=1)
        ttk.Label(row3, text="Dimension").grid(row=0, column=0, sticky="w")
        ttk.Combobox(row3, textvariable=self.dimension, values=["overworld", "nether", "end"], state="readonly").grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )

        row4 = ttk.Frame(lf_opts)
        row4.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        row4.columnconfigure(1, weight=1)
        cpu_threads = max(1, os.cpu_count() or 1)
        workers_label = ttk.Label(row4, text="Workers (CPU threads)")
        workers_label.grid(row=0, column=0, sticky="w")
        workers_spin = ttk.Spinbox(row4, from_=1, to=cpu_threads, textvariable=self.processes)
        workers_spin.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        add_tooltip(
            workers_label,
            f"How many worker processes to use.\n"
            f"This is typically set to your CPU logical thread count.\n"
            f"Detected CPU threads: {cpu_threads}",
        )
        add_tooltip(
            workers_spin,
            f"How many worker processes to use.\n"
            f"Detected CPU threads: {cpu_threads}",
        )

        # Y filter (optional)
        y_row = ttk.Frame(lf_opts)
        y_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        y_row.columnconfigure(2, weight=1)
        y_cb = ttk.Checkbutton(y_row, text="Enable Y filter", variable=self.use_y_filter)
        y_cb.grid(row=0, column=0, sticky="w")
        add_tooltip(y_cb, "Optional. Only change biomes between Y min and Y max. Leave off to change all heights.")
        ttk.Label(y_row, text="Y min").grid(row=1, column=0, sticky="w", pady=(6, 0))
        y_min_spin = ttk.Spinbox(y_row, from_=-128, to=512, textvariable=self.y_min, width=10)
        y_min_spin.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(y_row, text="Y max").grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        y_max_spin = ttk.Spinbox(y_row, from_=-128, to=512, textvariable=self.y_max, width=10)
        y_max_spin.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))
        add_tooltip(y_min_spin, "Minimum Y (inclusive) for filtering.")
        add_tooltip(y_max_spin, "Maximum Y (inclusive) for filtering.")

        # Probe
        lf_probe = ttk.Labelframe(right, text="Probe (optional)")
        lf_probe.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        lf_probe.columnconfigure(1, weight=1)
        add_tooltip(
            lf_probe,
            "Probe mode does NOT modify your world.\n"
            "It scans chunks and reports the first region/chunk where a biome palette entry starts with a prefix.\n"
            "Example prefix: terralith:",
        )
        probe_label = ttk.Label(lf_probe, text="Prefix")
        probe_label.grid(row=0, column=0, sticky="w")
        probe_entry = ttk.Entry(lf_probe, textvariable=self.probe_prefix)
        probe_entry.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        add_tooltip(probe_entry, "Example: minecraft:plains")
        ttk.Label(lf_probe, text="Max regions").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(lf_probe, from_=0, to=100000, textvariable=self.probe_max_regions).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
        ttk.Label(lf_probe, text="Max chunks").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(lf_probe, from_=0, to=10_000_000, increment=1000, textvariable=self.probe_max_chunks).grid(
            row=2, column=1, sticky="ew", padx=(6, 0), pady=(6, 0)
        )

        # Actions
        actions = ttk.Frame(right)
        actions.grid(row=4, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.start_btn = ttk.Button(actions, text="Run", command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="Clear output", command=self._clear_output).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        # Bottom: progress bar
        bottom = ttk.Frame(root)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bottom, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(bottom, text="Idle")
        self.progress_label.grid(row=0, column=1, sticky="e", padx=(10, 0))

        # Parse: "Progress: regions X/Y ..."
        self._progress_re = re.compile(r"Progress: regions (\d+)/(\d+)")

    def _browse_world(self) -> None:
        p = filedialog.askdirectory(title="Select Minecraft world folder")
        if p:
            self.world_path.set(p)

    def _browse_ini(self) -> None:
        p = filedialog.askopenfilename(
            title="Select mapping INI file",
            filetypes=[("INI files", "*.ini"), ("All files", "*.*")],
        )
        if p:
            self.mapping_ini.set(p)

    def _default_mapping_ini(self) -> Path:
        # Prefer mapping.ini next to the executable when packaged,
        # otherwise next to this GUI script.
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "mapping.ini"
        return Path(__file__).resolve().parent / "mapping.ini"

    def _append_output(self, line: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", line + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._append_output(line)
                m = self._progress_re.search(line)
                if m:
                    done = int(m.group(1))
                    total = int(m.group(2))
                    if total > 0:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=total)
                        self.progress["value"] = done
                        self.progress_label.configure(text=f"{done}/{total} regions")
        except queue.Empty:
            pass
        self.after(50, self._drain_log_queue)

    def _set_running(self, running: bool) -> None:
        self._is_running.set(running)
        state = "disabled" if running else "normal"
        self.start_btn.configure(state=state)
        if running:
            self.progress.configure(mode="indeterminate")
            self.progress["value"] = 0
            self.progress.start(15)
            self.progress_label.configure(text="Running…")
        else:
            self.progress.stop()
            self.progress_label.configure(text="Idle")

    def _start(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return

        world = self.world_path.get().strip()
        if not world:
            self._append_output("ERROR: Please select a world folder.")
            return

        argv: list[str] = [world, "--dimension", self.dimension.get(), "--processes", str(int(self.processes.get()))]

        if self.dry_run.get():
            argv.append("--dry-run")
        if not self.backups.get():
            argv.append("--no-backup")

        ini = self.mapping_ini.get().strip()
        if ini:
            if not Path(ini).exists():
                self._append_output(f"ERROR: Mapping INI not found: {ini}")
                return
            argv.extend(["--mapping-ini", ini])
        if self.use_y_filter.get():
            argv.extend(["--y", str(int(self.y_min.get())), str(int(self.y_max.get()))])

        fb = self.unmapped_fallback.get().strip()
        if fb:
            argv.extend(["--unmapped-terralith-to", fb])

        pref = self.probe_prefix.get().strip()
        if pref:
            argv.extend(
                [
                    "--probe-prefix",
                    pref,
                    "--probe-max-regions",
                    str(int(self.probe_max_regions.get())),
                    "--probe-max-chunks",
                    str(int(self.probe_max_chunks.get())),
                ]
            )

        # Debug options are intentionally CLI-only.

        self._append_output("Running with args: " + " ".join(argv))
        self._set_running(True)

        def worker() -> None:
            try:
                rc = core.run(argv, log=self._log_queue.put)
                self._log_queue.put(f"Done. Exit code: {rc}")
            except Exception as e:
                self._log_queue.put(f"ERROR: {type(e).__name__}: {e}")
            finally:
                self.after(0, lambda: self._set_running(False))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()


def main() -> None:
    multiprocessing.freeze_support()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()


