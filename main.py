import ctypes
import json
import os
import queue
import re
import threading
from datetime import datetime
from pathlib import Path
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 1
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

DATE_FORMATS = (
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m.%d.%Y",
    "%m/%d/%y",
    "%m-%d-%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%m%d%Y",
    "%Y%m%d",
)
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
PHONE_PATTERN = re.compile(
    r"(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}"
)


def classify_clipboard_value(value):
    text = value.strip()
    if not text:
        return None
    if EMAIL_PATTERN.fullmatch(text):
        return "email"
    if PHONE_PATTERN.fullmatch(text):
        return "phone"
    if re.fullmatch(r"\d{1,7}", text):
        return "mrn"

    for date_format in DATE_FORMATS:
        try:
            datetime.strptime(text, date_format)
            return "dob"
        except ValueError:
            continue
    return None


def normalize_date_value(value):
    text = value.strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", text):
        date_format = "%m/%d/%y"
    elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
        date_format = "%m/%d/%Y"
    elif re.fullmatch(r"\d{8}", text):
        date_format = "%m%d%Y"
    elif re.fullmatch(r"\d{6}", text):
        date_format = "%m%d%y"
    else:
        return None

    try:
        return datetime.strptime(text, date_format).strftime("%m/%d/%Y")
    except ValueError:
        return None


def normalize_dob_value(value):
    normalized = normalize_date_value(value)
    if normalized is not None:
        return normalized
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).strftime(
                "%m/%d/%Y"
            )
        except ValueError:
            continue
    return None


def format_phone_number(value):
    digits = re.sub(r"\D", "", value)
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def choose_provider(exp, to, kp):
    exp_value = exp.strip()
    to_value = to.strip()
    kp_value = kp.strip()
    if is_character_only_value(exp_value):
        return exp_value

    to_is_special = (
        re.fullmatch(r"(?:bg|fl)", to_value, re.IGNORECASE) is not None
        or "borland" in to_value.casefold()
    )
    kp_is_special = (
        re.fullmatch(r"(?:bg|fl)", kp_value, re.IGNORECASE) is not None
        or re.search(r"\b(?:borland|groover)\b", kp_value, re.IGNORECASE)
        is not None
    )

    if is_character_only_value(to_value) and not to_is_special:
        return to_value
    if is_character_only_value(kp_value) and (
        not kp_is_special or (not exp_value and not to_value)
    ):
        return kp_value
    return to_value if is_character_only_value(to_value) else ""


def is_character_only_value(value):
    text = value.strip()
    return bool(text) and any(character.isalpha() for character in text) and not any(
        character.isdigit() for character in text
    )


class NotepadApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ODB Helper")
        self.root.geometry("700x450")
        self.root.minsize(1, 200)
        self.show_odb_completion_popup = tk.BooleanVar(root, value=True)
        self.global_dunk_shortcut = tk.BooleanVar(root, value=True)
        self.odb_cancel_event = None
        self.odb_prompt_open = False
        self.hotkey_event_queue = queue.Queue()
        self.hotkey_thread = None
        self.hotkey_thread_id = None
        self.hotkey_stop_event = None
        self.window_state_path = self.get_window_state_path()
        self.restore_window_geometry()
        self.current_file = None
        self.font_family = "Consolas"
        self.font_size = 11
        self.block_colors = ("#6A9955", "#C586C0", "#CE9178")
        self.block_labels = (
            ("MRN", "NAME", "DOB", "CEL", "EMAIL"),
            ("PRAC", "FROM", "TO", "EXP", "KP", "DX"),
            ("NOTES",),
        )
        self.default_line_color = "#858585"
        self.background_color = "#1E1E1E"
        self.gutter_color = "#252526"
        self.foreground_color = "#D4D4D4"
        self.last_clipboard_sequence = self.read_clipboard_sequence()
        self.last_clipboard = self.read_clipboard()
        self.last_auto_capture_value = None
        self.last_auto_capture_sequence = None
        self.last_text_capture = None
        self.name_first_capture = None
        self.odb_result_queue = queue.Queue()
        self.odb_running = False
        self.configure_dark_theme()

        self.menu_bar = tk.Menu(
            root,
            background=self.gutter_color,
            foreground=self.foreground_color,
            activebackground="#094771",
            activeforeground="#FFFFFF",
            borderwidth=0,
        )

        menu_options = {
            "tearoff": 0,
            "background": self.gutter_color,
            "foreground": self.foreground_color,
            "activebackground": "#094771",
            "activeforeground": "#FFFFFF",
            "borderwidth": 0,
        }
        self.file_menu = tk.Menu(self.menu_bar, **menu_options)
        self.file_menu.add_command(label="New", command=self.new_file, accelerator="Ctrl+N")
        self.file_menu.add_command(label="Open...", command=self.open_file, accelerator="Ctrl+O")
        self.file_menu.add_command(label="Save", command=self.save_file, accelerator="Ctrl+S")
        self.file_menu.add_command(label="Save As...", command=self.save_as)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_close)

        self.edit_menu = tk.Menu(self.menu_bar, **menu_options)
        self.edit_menu.add_command(label="Cut", command=self.cut_selected, accelerator="Ctrl+X")
        self.edit_menu.add_command(label="Copy", command=self.copy_selected, accelerator="Ctrl+C")
        self.edit_menu.add_command(label="Paste", command=self.paste_selected, accelerator="Ctrl+V")
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Select All", command=self.select_all, accelerator="Ctrl+A")

        self.view_menu = tk.Menu(self.menu_bar, **menu_options)
        self.view_menu.add_command(label="Zoom In", command=self.zoom_in, accelerator="Ctrl++")
        self.view_menu.add_command(label="Zoom Out", command=self.zoom_out, accelerator="Ctrl+-")
        self.view_menu.add_command(
            label="Restore Default Zoom",
            command=self.restore_zoom,
            accelerator="Ctrl+0",
        )

        self.options_menu = tk.Menu(self.menu_bar, **menu_options)
        self.options_menu.add_checkbutton(
            label="Show ODB completion popup",
            variable=self.show_odb_completion_popup,
            command=self.save_window_geometry,
        )
        self.options_menu.add_checkbutton(
            label="Enable global Ctrl+Shift+F shortcut",
            variable=self.global_dunk_shortcut,
            command=self.toggle_global_dunk_shortcut,
        )

        self.actions_menu = tk.Menu(self.menu_bar, **menu_options)
        self.actions_menu.add_command(label="Clear", command=self.clear_text, underline=0)
        self.actions_menu.add_command(
            label="Dunk on ODB",
            command=self.dunk_on_odb,
            underline=0,
        )

        self.source_var = tk.StringVar(value="Source")
        self.date_var = tk.StringVar(value="MM/DD/YYYY")
        self.new_pt_var = tk.StringVar(value="New PT")
        self.ref_type_var = tk.StringVar(value="Ref Type")
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)
        self.menu_bar.add_cascade(label="Edit", menu=self.edit_menu)
        self.menu_bar.add_cascade(label="View", menu=self.view_menu)
        self.menu_bar.add_cascade(label="Options", menu=self.options_menu)
        self.menu_bar.add_cascade(label="Actions", menu=self.actions_menu)
        root.config(menu=self.menu_bar)

        self.options_row = ttk.Frame(root)
        self.options_row.pack(fill="x", padx=6, pady=(4, 4))

        self.source_combo = ttk.Combobox(
            self.options_row,
            textvariable=self.source_var,
            values=("CCD", "FAX"),
            state="readonly",
            width=9,
            style="Placeholder.TCombobox",
        )
        self.source_combo.pack(side="left", padx=(0, 4))
        self.source_combo.bind(
            "<<ComboboxSelected>>",
            lambda event: self.source_combo.configure(style="TCombobox"),
        )

        self.date_entry = ttk.Entry(
            self.options_row,
            textvariable=self.date_var,
            width=13,
            style="Placeholder.TEntry",
        )
        self.date_entry.pack(side="left", padx=(0, 4))
        self.date_entry.bind("<FocusIn>", self.clear_date_placeholder)
        self.date_entry.bind("<FocusOut>", lambda event: self.validate_date_entry())
        self.date_entry.bind("<Return>", self.on_date_enter)

        self.new_pt_combo = ttk.Combobox(
            self.options_row,
            textvariable=self.new_pt_var,
            values=("Yes", "No"),
            state="readonly",
            width=9,
            style="Placeholder.TCombobox",
        )
        self.new_pt_combo.pack(side="left", padx=(0, 4))
        self.new_pt_combo.bind(
            "<<ComboboxSelected>>",
            lambda event: self.new_pt_combo.configure(style="TCombobox"),
        )

        self.ref_type_combo = ttk.Combobox(
            self.options_row,
            textvariable=self.ref_type_var,
            values=("OV", "COL"),
            state="readonly",
            width=9,
            style="Placeholder.TCombobox",
        )
        self.ref_type_combo.pack(side="left")
        self.ref_type_combo.bind(
            "<<ComboboxSelected>>",
            lambda event: self.ref_type_combo.configure(style="TCombobox"),
        )

        self.root.update_idletasks()
        minimum_width = self.options_row.winfo_reqwidth() + 12
        self.root.minsize(minimum_width, 200)

        self.content = ttk.Frame(root)
        self.content.pack(fill="both", expand=True)

        self.line_numbers = tk.Canvas(
            self.content,
            width=48,
            bg=self.gutter_color,
            highlightthickness=0,
            bd=0,
        )
        self.line_numbers.pack(side="left", fill="y")

        self.text_frame = ttk.Frame(self.content)
        self.text_frame.pack(side="left", fill="both", expand=True)

        self.text = tk.Text(
            self.text_frame,
            wrap="none",
            undo=True,
            font=(self.font_family, self.font_size),
            padx=8,
            pady=6,
            borderwidth=0,
            background=self.background_color,
            foreground=self.foreground_color,
            insertbackground=self.foreground_color,
            selectbackground="#264F78",
            selectforeground="#FFFFFF",
        )
        self.text.pack(side="left", fill="both", expand=True)

        self.vertical_scroll = ttk.Scrollbar(
            self.text_frame,
            orient="vertical",
            command=self.on_scroll,
        )
        self.vertical_scroll.pack(side="right", fill="y")

        self.horizontal_scroll = ttk.Scrollbar(
            self.content,
            orient="horizontal",
            command=self.text.xview,
        )
        self.horizontal_scroll.pack(side="bottom", fill="x")

        self.text.configure(
            yscrollcommand=self.vertical_scroll.set,
            xscrollcommand=self.horizontal_scroll.set,
        )

        self.text.bind("<KeyRelease>", self.on_text_key_release)
        self.text.bind("<ButtonRelease>", self.update_line_numbers)
        self.text.bind("<Configure>", self.update_line_numbers)
        self.text.bind("<MouseWheel>", self.on_mouse_wheel)
        self.text.bind("<Control-BackSpace>", self.delete_word_backward)
        self.text.bind("<Control-Delete>", self.delete_word_forward)
        self.text.bind("<Control-Left>", self.move_word_backward)
        self.text.bind("<Control-Right>", self.move_word_forward)
        self.text.bind("<Control-Home>", self.move_to_document_start)
        self.text.bind("<Control-End>", self.move_to_document_end)
        self.text.bind("<Control-z>", self.undo_text)
        self.text.bind("<Control-y>", self.redo_text)
        self.text.bind("<Control-Shift-z>", self.redo_text)
        self.text.bind("<Control-v>", lambda event: self.paste_selected())
        self.root.bind("<Control-n>", lambda event: self.new_file())
        self.root.bind("<Control-o>", lambda event: self.open_file())
        self.root.bind("<Escape>", self.cancel_odb_automation)
        self.root.bind("<Control-s>", lambda event: self.save_file())
        self.root.bind("<Control-a>", lambda event: self.select_all())
        self.root.bind("<Control-x>", lambda event: self.cut_selected())
        self.root.bind("<Control-c>", lambda event: self.copy_selected())
        self.root.bind("<F1>", lambda event: self.move_last_text_capture())
        self.root.bind("<Control-plus>", lambda event: self.zoom_in())
        self.root.bind("<Control-equal>", lambda event: self.zoom_in())
        self.root.bind("<Control-minus>", lambda event: self.zoom_out())
        self.root.bind("<Control-0>", lambda event: self.restore_zoom())

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.ensure_line_exists(12)
        self.text.edit_reset()
        self.update_line_numbers()
        self.root.after(100, self.check_global_hotkey_events)
        self.root.after(250, self.check_clipboard)
        if self.global_dunk_shortcut.get():
            self.start_global_dunk_shortcut()

    def on_scroll(self, *args):
        self.text.yview(*args)
        self.update_line_numbers()

    def on_mouse_wheel(self, event):
        self.text.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.update_line_numbers()
        return "break"

    def configure_dark_theme(self):
        self.root.configure(background=self.background_color)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.background_color)
        style.configure(
            "TLabel",
            background=self.background_color,
            foreground=self.foreground_color,
        )
        style.configure(
            "TEntry",
            fieldbackground="#FFFFFF",
            foreground="#000000",
            insertcolor="#000000",
        )
        style.configure(
            "TCombobox",
            fieldbackground="#FFFFFF",
            background="#3C3C3C",
            foreground="#000000",
            arrowcolor=self.foreground_color,
        )
        style.configure(
            "Placeholder.TCombobox",
            fieldbackground="#2D2D30",
            background="#3C3C3C",
            foreground="#858585",
            arrowcolor=self.foreground_color,
        )
        style.configure(
            "Placeholder.TEntry",
            fieldbackground="#2D2D30",
            foreground="#858585",
            insertcolor=self.foreground_color,
        )
        style.configure(
            "TButton",
            background="#3C3C3C",
            foreground=self.foreground_color,
        )
        style.configure(
            "ODBPopup.TButton",
            background="#3C3C3C",
            foreground=self.foreground_color,
            focusthickness=2,
            focuscolor="#0E639C",
        )
        style.map(
            "ODBPopup.TButton",
            background=[
                ("pressed", "#005A9E"),
                ("active", "#0E639C"),
                ("focus", "#0E639C"),
            ],
            foreground=[
                ("pressed", "#FFFFFF"),
                ("active", "#FFFFFF"),
                ("focus", "#FFFFFF"),
            ],
        )
        style.configure(
            "TScrollbar",
            background="#3C3C3C",
            troughcolor=self.gutter_color,
            bordercolor=self.gutter_color,
            arrowcolor=self.foreground_color,
            darkcolor="#3C3C3C",
            lightcolor="#3C3C3C",
        )

    def get_window_state_path(self):
        app_data = os.environ.get("APPDATA")
        base_directory = (
            Path(app_data)
            if app_data
            else Path.home() / ".config"
        )
        return base_directory / "ODB Paster" / "window.json"

    def restore_window_geometry(self):
        try:
            data = json.loads(self.window_state_path.read_text(encoding="utf-8"))
            geometry = data.get("geometry")
            geometry_match = (
                re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
                if isinstance(geometry, str)
                else None
            )
            if geometry_match:
                width, height, x, y = (
                    int(value) for value in geometry_match.groups()
                )
            else:
                width = int(data["width"])
                height = int(data["height"])
                x = int(data["x"])
                y = int(data["y"])
            saved_state = data.get("state", "normal")
            show_popup = data.get("show_odb_completion_popup", True)
            global_shortcut = data.get("global_dunk_shortcut", True)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError) as error:
            messagebox.showwarning(
                "Window Settings",
                f"Could not restore the previous window position and size: {error}",
                parent=self.root,
            )
            return

        if isinstance(show_popup, bool):
            self.show_odb_completion_popup.set(show_popup)
        if isinstance(global_shortcut, bool):
            self.global_dunk_shortcut.set(global_shortcut)

        virtual_x = self.root.winfo_vrootx()
        virtual_y = self.root.winfo_vrooty()
        virtual_width = self.root.winfo_vrootwidth()
        virtual_height = self.root.winfo_vrootheight()
        width = min(max(1, width), virtual_width)
        height = min(max(200, height), virtual_height)
        x = min(max(virtual_x, x), virtual_x + virtual_width - width)
        y = min(max(virtual_y, y), virtual_y + virtual_height - height)
        self.root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        if saved_state == "zoomed":
            self.root.state("zoomed")

    def save_window_geometry(self):
        if self.root.state() == "iconic":
            return

        geometry = self.root.geometry()
        geometry_match = re.fullmatch(
            r"(\d+)x(\d+)([+-]\d+)([+-]\d+)",
            geometry,
        )
        if geometry_match:
            width, height, x, y = (
                int(value) for value in geometry_match.groups()
            )
        else:
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
        data = {
            "width": width,
            "height": height,
            "x": x,
            "y": y,
            "geometry": geometry,
            "state": self.root.state(),
            "show_odb_completion_popup": self.show_odb_completion_popup.get(),
            "global_dunk_shortcut": self.global_dunk_shortcut.get(),
        }
        temporary_path = self.window_state_path.with_suffix(".tmp")
        try:
            self.window_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temporary_path, self.window_state_path)
        except OSError as error:
            messagebox.showwarning(
                "Window Settings",
                f"Could not save the window position and size: {error}",
                parent=self.root,
            )

    def toggle_global_dunk_shortcut(self):
        if self.global_dunk_shortcut.get():
            self.start_global_dunk_shortcut()
        else:
            self.stop_global_dunk_shortcut()
        self.save_window_geometry()

    def start_global_dunk_shortcut(self):
        if os.name != "nt":
            self.global_dunk_shortcut.set(False)
            self.save_window_geometry()
            messagebox.showerror(
                "Shortcut unavailable",
                "The global Ctrl+Shift+F shortcut is only available on Windows.",
                parent=self.root,
            )
            return
        if self.hotkey_thread is not None and self.hotkey_thread.is_alive():
            return

        self.hotkey_stop_event = threading.Event()
        self.hotkey_thread = threading.Thread(
            target=self.listen_for_global_dunk_shortcut,
            args=(self.hotkey_stop_event,),
            name="global-dunk-shortcut",
            daemon=True,
        )
        self.hotkey_thread.start()

    def listen_for_global_dunk_shortcut(self, stop_event):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.hotkey_thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(
            ctypes.byref(message),
            None,
            0,
            0,
            0,
        )
        if stop_event.is_set():
            self.hotkey_thread_id = None
            return

        modifiers = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, ord("F")):
            self.hotkey_event_queue.put(
                ("error", kernel32.GetLastError())
            )
            self.hotkey_thread_id = None
            return

        try:
            while True:
                result = user32.GetMessageW(
                    ctypes.byref(message),
                    None,
                    0,
                    0,
                )
                if result == 0:
                    return
                if result == -1:
                    self.hotkey_event_queue.put(
                        ("error", kernel32.GetLastError())
                    )
                    return
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    self.hotkey_event_queue.put(("trigger", None))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.hotkey_thread_id = None

    def stop_global_dunk_shortcut(self):
        if self.hotkey_stop_event is not None:
            self.hotkey_stop_event.set()
        if os.name == "nt" and self.hotkey_thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(
                self.hotkey_thread_id,
                WM_QUIT,
                0,
                0,
            )

        thread = self.hotkey_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        if thread is None or not thread.is_alive():
            self.hotkey_thread = None
            self.hotkey_thread_id = None
            self.hotkey_stop_event = None

    def check_global_hotkey_events(self):
        while True:
            try:
                event, details = self.hotkey_event_queue.get_nowait()
            except queue.Empty:
                break

            if event == "trigger":
                if self.global_dunk_shortcut.get():
                    self.activate_dunk_from_global_shortcut()
            elif event == "error":
                self.global_dunk_shortcut.set(False)
                self.stop_global_dunk_shortcut()
                self.save_window_geometry()
                messagebox.showerror(
                    "Shortcut unavailable",
                    "Windows could not register Ctrl+Shift+F. "
                    f"Another app may already use it (error {details}).",
                    parent=self.root,
                )

        self.root.after(100, self.check_global_hotkey_events)

    def activate_dunk_from_global_shortcut(self):
        if self.odb_running or self.odb_prompt_open:
            return
        self.root.deiconify()
        self.root.lift()
        self.dunk_on_odb()

    def update_line_numbers(self, event=None):
        self.line_numbers.delete("all")
        line_index = self.text.index("@0,0")

        number_font = tkfont.Font(
            root=self.root,
            family=self.font_family,
            size=max(8, self.font_size - 1),
        )
        line_count = int(self.text.index("end-1c").split(".")[0])
        notes_end_line = self.get_notes_end_line(line_count)

        displayed_line_count = max(12, line_count)
        gutter_width = max(
            48,
            number_font.measure(f"NOTES {displayed_line_count}") + 10,
        )
        self.line_numbers.configure(width=gutter_width)

        last_line_number = None
        last_line_y = None
        line_height = number_font.metrics("linespace")
        while True:
            line_info = self.text.dlineinfo(line_index)
            if line_info is None:
                break
            line_number = int(line_index.split(".")[0])
            y = line_info[1]
            label, color = self.get_line_label(line_number, notes_end_line)
            number_text = f"{label} {line_number}" if label else str(line_number)

            self.line_numbers.create_text(
                gutter_width - 5,
                y,
                anchor="ne",
                text=number_text,
                fill=color,
                font=number_font,
            )
            last_line_number = line_number
            last_line_y = y
            line_height = line_info[3]
            line_index = self.text.index(f"{line_index}+1line")

        if line_count < 12 and last_line_number == line_count:
            for line_number in range(line_count + 1, 13):
                y = last_line_y + line_height
                if y >= self.line_numbers.winfo_height():
                    break
                label, color = self.get_line_label(line_number, notes_end_line)
                self.line_numbers.create_text(
                    gutter_width - 5,
                    y,
                    anchor="ne",
                    text=f"{label} {line_number}" if label else str(line_number),
                    fill=color,
                    font=number_font,
                )
                last_line_y = y

    def on_text_key_release(self, event=None):
        dob_value = self.line_value(3)
        normalized_dob = normalize_dob_value(dob_value)
        if normalized_dob is not None and dob_value != normalized_dob:
            insertion = self.text.index("insert")
            insertion_line, insertion_column = map(int, insertion.split("."))
            self.text.edit_separator()
            self.text.delete("3.0", "3.end")
            self.text.insert("3.0", normalized_dob)
            if insertion_line == 3:
                self.text.mark_set(
                    "insert",
                    f"3.{min(insertion_column, len(normalized_dob))}",
                )
            self.text.edit_separator()
        self.update_line_numbers(event)

    def get_notes_end_line(self, line_count):
        notes_end_line = 11
        for line_number in range(12, line_count + 1):
            content = self.text.get(
                f"{line_number}.0",
                f"{line_number}.0 lineend",
            )
            if not content.strip():
                break
            notes_end_line = line_number
        return notes_end_line

    def get_line_label(self, line_number, notes_end_line):
        if line_number <= 5:
            return self.block_labels[0][line_number - 1], self.block_colors[0]
        if line_number <= 11:
            return self.block_labels[1][line_number - 6], self.block_colors[1]
        if line_number <= notes_end_line:
            return self.block_labels[2][0], self.block_colors[2]
        return None, self.default_line_color

    def set_font_size(self, size):
        self.font_size = max(6, min(72, size))
        self.text.configure(font=(self.font_family, self.font_size))
        self.update_line_numbers()

    def zoom_in(self):
        self.set_font_size(self.font_size + 1)

    def zoom_out(self):
        self.set_font_size(self.font_size - 1)

    def restore_zoom(self):
        self.set_font_size(11)

    def read_clipboard(self):
        try:
            return self.root.clipboard_get()
        except tk.TclError:
            return None

    def read_clipboard_sequence(self):
        if os.name == "nt":
            return ctypes.windll.user32.GetClipboardSequenceNumber()
        return None

    def check_clipboard(self):
        sequence = self.read_clipboard_sequence()
        clipboard_text = self.read_clipboard()
        clipboard_changed = (
            sequence != self.last_clipboard_sequence
            if sequence is not None
            else clipboard_text != self.last_clipboard
        )
        self.last_clipboard_sequence = sequence
        self.last_clipboard = clipboard_text

        if clipboard_changed and clipboard_text is not None:
            candidate = clipboard_text.strip()
            self.last_text_capture = None
            self.last_auto_capture_value = None
            self.last_auto_capture_sequence = None
            value_type = classify_clipboard_value(candidate)
            if value_type:
                captured = self.capture_recognized_value(candidate, value_type)
            elif (
                is_character_only_value(candidate)
                and "\n" not in candidate
                and "\r" not in candidate
            ):
                captured = self.capture_text_value(candidate)
            else:
                captured = False
            if captured:
                self.last_auto_capture_value = candidate
                self.last_auto_capture_sequence = sequence
        self.root.after(250, self.check_clipboard)

    def prompt_odb_values(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("ODB referral options")
        dialog.transient(self.root)
        dialog.resizable(False, False)

        new_pt_value = self.new_pt_var.get()
        if new_pt_value not in ("Yes", "No"):
            new_pt_value = ""
        ref_type_value = self.ref_type_var.get()
        if ref_type_value not in ("OV", "COL"):
            ref_type_value = ""
        new_pt_var = tk.StringVar(dialog, value=new_pt_value)
        ref_type_var = tk.StringVar(dialog, value=ref_type_value)

        ttk.Label(dialog, text="New PT (Yes/No)").grid(
            row=0, column=0, padx=10, pady=6, sticky="w"
        )
        new_pt_combo = ttk.Combobox(
            dialog,
            textvariable=new_pt_var,
            values=("Yes", "No"),
            state="readonly",
            width=18,
            takefocus=True,
        )
        new_pt_combo.grid(row=0, column=1, padx=10, pady=6)

        ttk.Label(dialog, text="Ref Type (OV/COL)").grid(
            row=1, column=0, padx=10, pady=6, sticky="w"
        )
        ref_type_combo = ttk.Combobox(
            dialog,
            textvariable=ref_type_var,
            values=("OV", "COL"),
            state="readonly",
            width=18,
            takefocus=True,
        )
        ref_type_combo.grid(row=1, column=1, padx=10, pady=6)

        result = {}

        def accept():
            result.update(
                new_pt=new_pt_var.get(),
                ref_type=ref_type_var.get(),
            )
            dialog.destroy()
        ok_button = ttk.Button(
            dialog,
            text="OK",
            command=accept,
                style="ODBPopup.TButton",
            takefocus=True,
                state=(
                    "normal"
                    if new_pt_value and ref_type_value
                    else "disabled"
                ),
        )
        ok_button.grid(
            row=2,
            column=0,
            columnspan=2,
            padx=10,
            pady=(8, 10),
            sticky="e",
        )

        def update_ok_state(event=None):
            is_ready = (
                new_pt_var.get() in ("Yes", "No")
                and ref_type_var.get() in ("OV", "COL")
            )
            ok_button.configure(
                state="normal" if is_ready else "disabled"
            )

        new_pt_combo.bind("<<ComboboxSelected>>", update_ok_state)
        ref_type_combo.bind("<<ComboboxSelected>>", update_ok_state)

        def accept_on_button_focus(event):
            if dialog.focus_get() == ok_button:
                accept()
                return "break"
            return None

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.bind("<Return>", accept_on_button_focus)
        dialog.bind("<KP_Enter>", accept_on_button_focus)
        self.position_dialog_near_root(dialog)
        dialog.grab_set()
        new_pt_combo.focus_set()
        dialog.wait_window()

        if not result:
            return None
        self.new_pt_var.set(result["new_pt"])
        self.ref_type_var.set(result["ref_type"])
        self.new_pt_combo.configure(style="TCombobox")
        self.ref_type_combo.configure(style="TCombobox")
        return result

    def position_dialog_near_root(self, dialog):
        self.root.update_idletasks()
        dialog.update_idletasks()
        parent_width = max(self.root.winfo_width(), self.root.winfo_reqwidth())
        parent_height = max(self.root.winfo_height(), self.root.winfo_reqheight())
        virtual_x = self.root.winfo_vrootx()
        virtual_y = self.root.winfo_vrooty()
        virtual_width = self.root.winfo_vrootwidth()
        virtual_height = self.root.winfo_vrootheight()

        if self.root.winfo_viewable():
            parent_x = self.root.winfo_rootx()
            parent_y = self.root.winfo_rooty()
        else:
            parent_x = virtual_x + max(0, (virtual_width - parent_width) // 2)
            parent_y = virtual_y + max(0, (virtual_height - parent_height) // 2)
            self.root.geometry(f"+{parent_x}+{parent_y}")

        dialog_width = dialog.winfo_reqwidth()
        dialog_height = dialog.winfo_reqheight()
        x = min(
            max(virtual_x, parent_x),
            virtual_x + max(0, virtual_width - dialog_width),
        )
        y = min(
            max(virtual_y, parent_y),
            virtual_y + max(0, virtual_height - dialog_height),
        )
        dialog.geometry(f"+{x}+{y}")

    def dunk_on_odb(self):
        if self.odb_running or self.odb_prompt_open:
            return

        referral_source = self.source_var.get()
        referral_date = normalize_date_value(self.date_var.get())
        if referral_source not in ("CCD", "FAX"):
            messagebox.showerror(
                "Missing Source",
                "Select CCD or FAX before sending the referral.",
                parent=self.root,
            )
            return
        if referral_date is None:
            messagebox.showerror(
                "Invalid Date",
                "Enter a valid referral date before sending the referral.",
                parent=self.root,
            )
            return

        self.odb_prompt_open = True
        try:
            odb_options = self.prompt_odb_values()
        finally:
            self.odb_prompt_open = False
        if odb_options is None:
            return
        ref_type = odb_options["ref_type"]

        provider = choose_provider(
            self.line_value(9),
            self.line_value(8),
            self.line_value(10),
        )
        referral_data = {
            "record_Number": self.line_value(1).strip(),
            "patient": (
                self.line_value(2).strip()
                if is_character_only_value(self.line_value(2))
                else ""
            ),
            "dob": normalize_dob_value(self.line_value(3))
            or self.line_value(3).strip(),
            "phone": self.line_value(4).strip(),
            "email": self.line_value(5).strip(),
            "New_Patient": odb_options["new_pt"],
            "referral_receipt_date": referral_date,
            "provider": provider,
            "Diagnostic": self.line_value(11).strip(),
        }

        self.odb_running = True
        self.odb_cancel_event = threading.Event()
        cancel_event = self.odb_cancel_event
        self.actions_menu.entryconfigure("Dunk on ODB", state="disabled")

        def run_automation():
            try:
                from webpaster import (
                    AutomationCancelled,
                    ejecutar_automatizacion,
                )
            except Exception as error:
                self.odb_result_queue.put(("error", str(error)))
                return

            try:
                ejecutar_automatizacion(
                    referral_data,
                    referral_source,
                    ref_type,
                    cancel_event=cancel_event,
                )
            except AutomationCancelled as error:
                self.odb_result_queue.put(("cancelled", str(error)))
            except Exception as error:
                self.odb_result_queue.put(("error", str(error)))
            else:
                self.odb_result_queue.put(("success", "Referral sent successfully."))

        threading.Thread(
            target=run_automation,
            name="odb-automation",
            daemon=True,
        ).start()
        self.root.after(100, self.check_odb_result)

    def cancel_odb_automation(self, event=None):
        if self.odb_running and self.odb_cancel_event is not None:
            self.odb_cancel_event.set()
            self.actions_menu.entryconfigure(
                "Dunk on ODB",
                label="Cancelling ODB...",
                state="disabled",
            )
        return "break"

    def check_odb_result(self):
        try:
            succeeded, message = self.odb_result_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self.check_odb_result)
            return

        self.odb_running = False
        self.odb_cancel_event = None
        self.actions_menu.entryconfigure(
            "Dunk on ODB",
            label="Dunk on ODB",
            state="normal",
        )
        if succeeded == "success" and self.show_odb_completion_popup.get():
            messagebox.showinfo("Dunk on ODB", message, parent=self.root)
        elif succeeded == "error":
            messagebox.showerror("Dunk on ODB", message, parent=self.root)

    def line_value(self, line_number):
        return self.text.get(
            f"{line_number}.0",
            f"{line_number}.0 lineend",
        )

    def ensure_line_exists(self, line_number):
        while int(self.text.index("end-1c").split(".")[0]) < line_number:
            self.text.insert("end-1c", "\n")

    def put_value_in_empty_line(self, line_number, value):
        if self.line_value(line_number).strip():
            return False
        self.ensure_line_exists(line_number)
        self.text.insert(f"{line_number}.0", value)
        self.text.mark_set("insert", f"{line_number}.{len(value)}")
        self.text.see("insert")
        self.update_line_numbers()
        return True

    def capture_recognized_value(self, value, value_type):
        target_lines = {
            "mrn": 1,
            "dob": 3,
            "phone": 4,
            "email": 5,
        }
        if value_type == "dob":
            value = normalize_dob_value(value) or value
        if value_type == "phone":
            value = format_phone_number(value)
        source_line = int(self.text.index("insert").split(".")[0])
        self.name_first_capture = None
        if self.put_value_in_empty_line(target_lines[value_type], value):
            self.advance_to_next_text_line(source_line)
            return True
        return False

    def capture_text_value(self, value):
        if not is_character_only_value(value):
            return False
        line_number = int(self.text.index("insert").split(".")[0])
        if line_number == 2:
            current_value = self.line_value(2)
            if not current_value:
                if self.put_value_in_empty_line(2, value):
                    self.name_first_capture = value
                    self.last_text_capture = {
                        "line": 2,
                        "start": 0,
                        "value": value,
                        "separator_before": False,
                    }
                    return True
                return False

            if (
                self.name_first_capture
                and current_value == self.name_first_capture
            ):
                separator = " "
                start = len(current_value) + len(separator)
                self.text.insert(f"2.{len(current_value)}", separator + value)
                self.ensure_line_exists(3)
                self.text.mark_set("insert", "3.0")
                self.text.see("insert")
                self.update_line_numbers()
                self.last_text_capture = {
                    "line": 2,
                    "start": start,
                    "value": value,
                    "separator_before": True,
                }
                self.name_first_capture = None
                self.advance_to_next_text_line(2)
                return True
            return False

        if 6 <= line_number <= 11:
            if self.put_value_in_empty_line(line_number, value):
                self.last_text_capture = {
                    "line": line_number,
                    "start": 0,
                    "value": value,
                    "separator_before": False,
                }
                self.advance_to_next_text_line(line_number)
                return True
        return False

    def advance_to_next_text_line(self, source_line):
        target_line = self.next_empty_text_line(source_line)
        if target_line is not None:
            self.text.mark_set("insert", f"{target_line}.0")
            self.text.see("insert")

    def next_empty_text_line(self, source_line):
        text_lines = tuple(range(6, 12))
        source_index = (
            text_lines.index(source_line) if source_line in text_lines else -1
        )
        for offset in range(1, len(text_lines) + 1):
            line_number = text_lines[(source_index + offset) % len(text_lines)]
            if line_number == source_line:
                continue
            if not self.line_value(line_number).strip():
                return line_number
        return None

    def move_last_text_capture(self):
        capture = self.last_text_capture
        if capture is None:
            return "break"

        source_line = capture["line"]
        start = capture["start"]
        value = capture["value"]
        end = start + len(value)
        if self.text.get(f"{source_line}.{start}", f"{source_line}.{end}") != value:
            self.last_text_capture = None
            return "break"

        target_line = self.next_empty_text_line(source_line)
        if target_line is None:
            return "break"

        delete_start = start - 1 if capture["separator_before"] else start
        self.text.delete(
            f"{source_line}.{delete_start}",
            f"{source_line}.{end}",
        )
        self.ensure_line_exists(target_line)
        self.text.insert(f"{target_line}.0", value)
        self.mark_and_advance_to_next_text_line(target_line)
        self.text.see("insert")
        self.update_line_numbers()
        self.name_first_capture = None
        self.last_text_capture = {
            "line": target_line,
            "start": 0,
            "value": value,
            "separator_before": False,
        }
        return "break"

    def mark_and_advance_to_next_text_line(self, source_line):
        target_line = self.next_empty_text_line(source_line)
        if target_line is None:
            target_line = source_line
        self.text.mark_set("insert", f"{target_line}.0")

    def delete_word_backward(self, event=None):
        selection = self.text.tag_ranges("sel")
        if selection:
            self.text.delete("sel.first", "sel.last")
        else:
            start = self.text.index("insert -1c wordstart")
            self.text.delete(start, "insert")
        self.update_line_numbers()
        return "break"

    def delete_word_forward(self, event=None):
        selection = self.text.tag_ranges("sel")
        if selection:
            self.text.delete("sel.first", "sel.last")
        else:
            end = self.text.index("insert wordend")
            if end == self.text.index("insert"):
                end = self.text.index("insert +1c wordend")
            self.text.delete("insert", end)
        self.update_line_numbers()
        return "break"

    def move_word_backward(self, event=None):
        self.text.mark_set("insert", "insert -1c wordstart")
        self.text.see("insert")
        return "break"

    def move_word_forward(self, event=None):
        target = self.text.index("insert wordend")
        if target == self.text.index("insert"):
            target = self.text.index("insert +1c wordend")
        self.text.mark_set("insert", target)
        self.text.see("insert")
        return "break"

    def move_to_document_start(self, event=None):
        self.text.mark_set("insert", "1.0")
        self.text.see("insert")
        return "break"

    def move_to_document_end(self, event=None):
        self.text.mark_set("insert", "end-1c")
        self.text.see("insert")
        return "break"

    def undo_text(self, event=None):
        if self.text.tk.getboolean(self.text.edit("canundo")):
            self.text.edit_undo()
            self.update_line_numbers()
        return "break"

    def redo_text(self, event=None):
        if self.text.tk.getboolean(self.text.edit("canredo")):
            self.text.edit_redo()
            self.update_line_numbers()
        return "break"

    def remember_clipboard(self):
        self.last_clipboard_sequence = self.read_clipboard_sequence()
        self.last_clipboard = self.read_clipboard()
        self.last_auto_capture_value = None
        self.last_auto_capture_sequence = None

    def reset_clipboard_capture_state(self):
        self.last_text_capture = None
        self.name_first_capture = None
        self.last_auto_capture_value = None
        self.last_auto_capture_sequence = None

    def new_file(self):
        self.text.delete("1.0", "end")
        self.ensure_line_exists(12)
        self.text.edit_reset()
        self.current_file = None
        self.reset_clipboard_capture_state()
        self.root.title("ODB Helper")
        self.update_line_numbers()

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return

        with open(path, "r", encoding="utf-8") as file:
            contents = file.read()

        self.text.delete("1.0", "end")
        self.text.insert("1.0", contents)
        self.ensure_line_exists(12)
        self.text.edit_reset()
        self.current_file = path
        self.reset_clipboard_capture_state()
        self.root.title("ODB Helper")
        self.update_line_numbers()

    def save_file(self):
        if self.current_file:
            with open(self.current_file, "w", encoding="utf-8") as file:
                file.write(self.file_contents())
            return
        self.save_as()

    def save_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        self.current_file = path
        with open(path, "w", encoding="utf-8") as file:
            file.write(self.file_contents())
        self.root.title("ODB Helper")

    def file_contents(self):
        contents = self.text.get("1.0", "end-1c").rstrip("\n")
        return contents + "\n"

    def cut_selected(self):
        self.text.event_generate("<<Cut>>")
        self.root.after_idle(self.remember_clipboard)

    def copy_selected(self):
        self.text.event_generate("<<Copy>>")
        self.root.after_idle(self.remember_clipboard)

    def paste_selected(self):
        clipboard_text = self.read_clipboard()
        sequence = self.read_clipboard_sequence()
        already_captured = (
            clipboard_text is not None
            and clipboard_text.strip() == self.last_auto_capture_value
            and (
                self.last_auto_capture_sequence is None
                or sequence == self.last_auto_capture_sequence
            )
        )
        self.remember_clipboard()
        if already_captured:
            return "break"
        candidate = clipboard_text.strip() if clipboard_text else ""
        line_number = int(self.text.index("insert").split(".")[0])
        value_type = classify_clipboard_value(candidate)
        if value_type:
            self.last_text_capture = None
            self.capture_recognized_value(candidate, value_type)
            return "break"
        if line_number == 2 or 6 <= line_number <= 11:
            self.last_text_capture = None
            if not is_character_only_value(candidate):
                return "break"
            self.capture_text_value(candidate)
            return "break"
        self.text.event_generate("<<Paste>>")
        return "break"

    def select_all(self):
        self.text.tag_add("sel", "1.0", "end")
        self.text.mark_set("insert", "1.0")
        self.text.see("insert")
        return "break"

    def clear_text(self):
        self.text.edit_separator()
        self.text.delete("1.0", "end")
        self.ensure_line_exists(12)
        self.text.edit_separator()
        self.reset_clipboard_capture_state()
        self.update_line_numbers()

    def clear_date_placeholder(self, event=None):
        if self.date_var.get() == "MM/DD/YYYY":
            self.date_var.set("")
            self.date_entry.configure(style="TEntry")

    def validate_date_entry(self):
        value = self.date_var.get().strip()
        if not value:
            self.date_var.set("MM/DD/YYYY")
            self.date_entry.configure(style="Placeholder.TEntry")
            return True
        normalized = normalize_date_value(value)
        if normalized is None:
            self.date_var.set("MM/DD/YYYY")
            self.date_entry.configure(style="Placeholder.TEntry")
            return False
        self.date_var.set(normalized)
        self.date_entry.configure(style="TEntry")
        return True

    def on_date_enter(self, event=None):
        self.validate_date_entry()
        return "break"

    def on_close(self):
        if self.text.get("1.0", "end-1c").strip():
            response = messagebox.askyesnocancel(
                "Save changes?",
                "Do you want to save your changes before closing?",
                parent=self.root,
            )
            if response is None:
                return
            if response:
                self.save_file()
        self.save_window_geometry()
        self.stop_global_dunk_shortcut()
        self.root.destroy()


def main():
    root = tk.Tk()
    NotepadApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
