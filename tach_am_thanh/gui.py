from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .commands import (
    BUNDLE_ROOT,
    PROJECT_ROOT,
    format_duration_display,
    normalize_segment_end_times,
    normalize_source_value,
    probe_media_duration,
    resolve_ffmpeg,
    runtime_env,
)


SEGMENT_COLUMNS = 5
SEGMENT_CANVAS_HEIGHT = 150
ASSETS_DIR = BUNDLE_ROOT / "assets"
ICON_ICO_PATH = ASSETS_DIR / "app.ico"
ICON_PNG_PATH = ASSETS_DIR / "app_icon.png"
LOGO_PNG_PATH = ASSETS_DIR / "logo.png"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tách Âm Thanh - Trường Mầm Non Vĩnh Hòa")
        self.geometry("900x720")
        self.minsize(820, 620)

        self._messages: queue.Queue[str] = queue.Queue()
        self._running = False

        self.source_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(Path("outputs")))
        self.split_enabled_var = tk.BooleanVar(value=False)
        self.separation_vars = {
            "music": tk.BooleanVar(value=True),
            "vocals": tk.BooleanVar(value=False),
            "both": tk.BooleanVar(value=False),
        }
        self.segment_count_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(value="Sẵn sàng.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="0%")
        self.duration_var = tk.StringVar(value="Tổng thời lượng dự kiến: chưa biết.")
        self.segment_help_var = tk.StringVar(value="Đoạn cuối mặc định cắt đến hết file.")

        self._segment_end_vars: list[tk.StringVar] = []
        self._progress_active = False
        self._segment_canvas_window_id: int | None = None
        self._source_duration_seconds: float | None = None
        self._duration_probe_after_id: str | None = None
        self._icon_image: tk.PhotoImage | None = None
        self._logo_image: tk.PhotoImage | None = None
        self._header_logo_image: tk.PhotoImage | None = None

        self._build()
        self._apply_window_icon()

        self.split_enabled_var.trace_add("write", self._toggle_segment_section)
        self.segment_count_var.trace_add("write", self._refresh_segment_entries)
        self.source_var.trace_add("write", self._schedule_source_duration_probe)

        self.after(100, self._drain_messages)
        self._toggle_segment_section()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(6, weight=1)

        self._build_header(root)

        form = ttk.Frame(root, padding=12)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Nguồn đầu vào").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.source_var).grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        ttk.Button(form, text="Chọn file...", command=self._choose_source_file).grid(
            row=0,
            column=2,
            sticky="e",
            pady=6,
        )

        ttk.Label(form, text="Thư mục kết quả").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.output_dir_var).grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        ttk.Button(form, text="Chọn...", command=self._choose_output_dir).grid(row=1, column=2, sticky="e", pady=6)

        ttk.Label(
            form,
            text="Dán link YouTube hoặc chọn file trên máy, sau đó chọn loại MP3 cần tách.",
            foreground="#555555",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(form, textvariable=self.duration_var, foreground="#555555").grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(6, 0),
        )

        mode_box = ttk.LabelFrame(root, text="Chọn loại âm thanh cần xuất", padding=(10, 6))
        mode_box.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for column, (mode, title) in enumerate((
            ("music", "Chỉ tách nhạc (không lời)"),
            ("vocals", "Chỉ tách lời (giọng hát/giọng nói)"),
            ("both", "Tách cả lời + nhạc (2 file MP3)"),
        )):
            ttk.Checkbutton(
                mode_box,
                text=title,
                variable=self.separation_vars[mode],
                command=lambda selected=mode: self._select_separation_mode(selected),
            ).grid(row=0, column=column, sticky="w", padx=(0, 16))

        split_toggle = ttk.Frame(root)
        split_toggle.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(
            split_toggle,
            text="Cắt MP3 tổng thành nhiều đoạn đánh số",
            variable=self.split_enabled_var,
        ).grid(row=0, column=0, sticky="w")

        segment_box = ttk.LabelFrame(root, text="Cấu hình đoạn cắt", padding=12)
        segment_box.grid(row=4, column=0, sticky="ew", pady=(8, 8))
        segment_box.columnconfigure(0, weight=1)
        self.segment_box = segment_box

        header = ttk.Frame(segment_box)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(3, weight=1)

        ttk.Label(header, text="Số đoạn").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.segment_count_input = ttk.Spinbox(header, from_=1, to=20, textvariable=self.segment_count_var, width=6)
        self.segment_count_input.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        ttk.Label(
            header,
            text="Nhập số giây cho các mốc đầu. Đoạn cuối tự động đến hết file.",
            foreground="#555555",
        ).grid(row=0, column=2, sticky="w", padx=(12, 0), pady=(0, 4))

        ttk.Label(segment_box, textvariable=self.segment_help_var, foreground="#555555").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(6, 0),
        )

        scroll_frame = ttk.Frame(segment_box)
        scroll_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        scroll_frame.columnconfigure(0, weight=1)
        scroll_frame.rowconfigure(0, weight=1)

        self.segment_canvas = tk.Canvas(scroll_frame, height=SEGMENT_CANVAS_HEIGHT, highlightthickness=0)
        self.segment_canvas.grid(row=0, column=0, sticky="ew")

        self.segment_scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=self.segment_canvas.yview)
        self.segment_scrollbar.grid(row=0, column=1, sticky="ns")
        self.segment_canvas.configure(yscrollcommand=self.segment_scrollbar.set)

        self.segment_rows = ttk.Frame(self.segment_canvas)
        self.segment_rows.bind("<Configure>", self._on_segment_rows_configure)
        self.segment_canvas.bind("<Configure>", self._on_segment_canvas_configure)
        self._segment_canvas_window_id = self.segment_canvas.create_window((0, 0), window=self.segment_rows, anchor="nw")

        actions = ttk.Frame(root)
        actions.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Bắt đầu tách âm thanh", command=self._separate).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Kiểm tra runtime", command=self._doctor).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Xóa log", command=self._clear_log).grid(row=0, column=2, padx=(8, 0))

        output = ttk.Frame(root)
        output.grid(row=6, column=0, sticky="nsew")
        output.columnconfigure(0, weight=1)
        output.rowconfigure(1, weight=1)

        progress_box = ttk.Frame(output)
        progress_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        progress_box.columnconfigure(0, weight=1)

        ttk.Label(progress_box, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(progress_box, textvariable=self.progress_text_var).grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(progress_box, maximum=100, variable=self.progress_var)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        log_frame = ttk.LabelFrame(output, text="Nhật ký")
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = tk.Text(log_frame, wrap="word", height=14)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def _build_header(self, root: ttk.Frame) -> None:
        header = ttk.Frame(root, padding=(12, 6, 12, 2))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        if LOGO_PNG_PATH.exists():
            try:
                self._logo_image = tk.PhotoImage(file=str(LOGO_PNG_PATH))
                shrink_x = max(1, self._logo_image.width() // 72)
                shrink_y = max(1, self._logo_image.height() // 72)
                self._header_logo_image = self._logo_image.subsample(shrink_x, shrink_y)
                ttk.Label(header, image=self._header_logo_image).grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))
            except tk.TclError:
                self._logo_image = None
                self._header_logo_image = None

        ttk.Label(header, text="Tách Âm Thanh", font=("Segoe UI", 15, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Trường Mầm Non Vĩnh Hòa", foreground="#666666").grid(row=1, column=1, sticky="w")

    def _apply_window_icon(self) -> None:
        try:
            if ICON_ICO_PATH.exists():
                self.iconbitmap(default=str(ICON_ICO_PATH))
        except tk.TclError:
            pass

        try:
            if ICON_PNG_PATH.exists():
                self._icon_image = tk.PhotoImage(file=str(ICON_PNG_PATH))
                self.iconphoto(True, self._icon_image)
        except tk.TclError:
            self._icon_image = None

    def _select_separation_mode(self, selected: str) -> None:
        # Dùng các ô vuông nhưng chỉ cho chọn đúng một chế độ, kể cả khi bỏ tích ô đang chọn.
        for mode, variable in self.separation_vars.items():
            variable.set(mode == selected)

    def _selected_separation_mode(self) -> str:
        return next(mode for mode, variable in self.separation_vars.items() if variable.get())

    def _choose_source_file(self) -> None:
        value = filedialog.askopenfilename(
            filetypes=[("Media", "*.mp3 *.wav *.m4a *.aac *.flac *.mp4 *.mov *.mkv *.webm *.avi"), ("All", "*.*")]
        )
        if value:
            self.source_var.set(value)

    def _choose_output_dir(self) -> None:
        value = filedialog.askdirectory()
        if value:
            self.output_dir_var.set(value)

    def _toggle_segment_section(self, *_: object) -> None:
        enabled = self.split_enabled_var.get()
        if enabled:
            self.segment_count_input.state(["!disabled"])
            self.segment_box.grid()
            self._refresh_segment_entries()
        else:
            self.segment_count_input.state(["disabled"])
            self.segment_box.grid_remove()
            for child in self.segment_rows.winfo_children():
                child.destroy()
            self._segment_end_vars = []
            self._update_segment_help()

    def _refresh_segment_entries(self, *_: object) -> None:
        count = self._safe_segment_count()
        old_values = [variable.get() for variable in self._segment_end_vars]

        self._update_segment_help()

        if not self.split_enabled_var.get():
            return

        for child in self.segment_rows.winfo_children():
            child.destroy()
        self._segment_end_vars = []

        if count is None:
            return

        for column in range(SEGMENT_COLUMNS):
            self.segment_rows.columnconfigure(column, weight=1)

        input_count = max(count - 1, 0)
        for index in range(input_count):
            variable = tk.StringVar(value=old_values[index] if index < len(old_values) else "")
            self._segment_end_vars.append(variable)
            row = index // SEGMENT_COLUMNS
            column = index % SEGMENT_COLUMNS
            cell = ttk.Frame(self.segment_rows, padding=(0, 0, 8, 8))
            cell.grid(row=row, column=column, sticky="ew")
            cell.columnconfigure(0, weight=1)
            ttk.Label(cell, text=f"Đoạn {index + 1} kết thúc (giây)").grid(row=0, column=0, sticky="w")
            ttk.Entry(cell, textvariable=variable, width=10).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        last_row = (count - 1) // SEGMENT_COLUMNS
        last_column = (count - 1) % SEGMENT_COLUMNS
        last_cell = ttk.Frame(self.segment_rows, padding=(0, 0, 8, 8))
        last_cell.grid(row=last_row, column=last_column, sticky="ew")
        last_cell.columnconfigure(0, weight=1)
        ttk.Label(last_cell, text=f"Đoạn {count}").grid(row=0, column=0, sticky="w")
        ttk.Label(last_cell, text=self._last_segment_text(), foreground="#555555").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(4, 0),
        )

        self.segment_canvas.update_idletasks()
        self.segment_canvas.configure(scrollregion=self.segment_canvas.bbox("all"))

    def _safe_segment_count(self) -> int | None:
        raw_value = self.segment_count_var.get().strip()
        if not raw_value:
            return None
        if not raw_value.isdigit():
            return None
        count = int(raw_value)
        if count < 1:
            return None
        return min(count, 20)

    def _segment_args(self) -> list[str]:
        if not self.split_enabled_var.get():
            return []

        count = self._safe_segment_count()
        if count is None:
            raise ValueError("Nhập số đoạn hợp lệ từ 1 đến 20.")

        expected_inputs = max(count - 1, 0)
        if len(self._segment_end_vars) != expected_inputs:
            raise ValueError("Chưa tạo đủ ô nhập mốc cắt.")

        values = [variable.get().strip() for variable in self._segment_end_vars]
        if any(not value for value in values):
            raise ValueError("Nhập đầy đủ số giây cho các mốc đầu.")

        parsed_values: list[str] = []
        previous = 0.0
        for value in values:
            try:
                current = float(value)
            except ValueError as exc:
                raise ValueError("Nhập mốc theo số giây tăng dần, ví dụ 30, 60, 90, 120.") from exc
            if current <= 0:
                raise ValueError("Mốc cắt phải lớn hơn 0 giây.")
            if current <= previous:
                raise ValueError("Nhập mốc theo số giây tăng dần, ví dụ 30, 60, 90, 120.")
            if self._source_duration_seconds is not None and current >= self._source_duration_seconds:
                raise ValueError(
                    f"Mốc cắt phải nhỏ hơn tổng thời lượng {format_duration_display(self._source_duration_seconds)}."
                )
            parsed_values.append(str(current).rstrip("0").rstrip("."))
            previous = current

        if self._source_duration_seconds is not None:
            normalize_segment_end_times(parsed_values, split_count=count, total_duration=self._source_duration_seconds)

        args = ["--split-count", str(count)]
        for value in parsed_values:
            args.extend(["--segment-end", value])
        return args

    def _separate(self) -> None:
        source_value = self.source_var.get().strip()
        output_dir = self.output_dir_var.get().strip()
        if not source_value or not output_dir:
            messagebox.showwarning("Thiếu thông tin", "Nhập file hoặc link YouTube và thư mục kết quả.")
            return

        try:
            segment_args = self._segment_args()
        except ValueError as exc:
            messagebox.showwarning("Thiếu thông tin", str(exc))
            return

        command = [
            "separate",
            source_value,
            "--output-dir",
            output_dir,
            "--ffmpeg",
            "auto",
            "--demucs",
            "auto",
            "--yt-dlp",
            "auto",
            "--mode",
            self._selected_separation_mode(),
            *segment_args,
        ]
        self._run_command(command, start_message="Đang xử lý tách âm thanh...", use_progress=True)

    def _doctor(self) -> None:
        self._run_command(["doctor"], start_message="Đang kiểm tra runtime...", use_progress=False)

    def _run_command(self, args: list[str], *, start_message: str, use_progress: bool) -> None:
        if self._running:
            messagebox.showinfo("Đang chạy", "Đang có lệnh đang chạy. Đợi lệnh hiện tại kết thúc.")
            return

        self._running = True
        self._progress_active = use_progress
        self.status_var.set(start_message)
        self.progress_var.set(0.0)
        self.progress_text_var.set("0%")

        if getattr(sys, "frozen", False):
            command = [sys.executable, *args]
        else:
            command = [sys.executable, "-m", "tach_am_thanh", *args]
        thread = threading.Thread(target=self._worker, args=(command,), daemon=True)
        thread.start()

    def _worker(self, command: list[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=runtime_env(),
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._messages.put(line.rstrip("\r\n"))
            returncode = process.wait()
            self._messages.put(f"__RETURN__:{returncode}")
        except Exception as exc:
            self._messages.put(f"Lỗi khi chạy lệnh: {exc}")
            self._messages.put("__RETURN__:1")

    def _drain_messages(self) -> None:
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            self._handle_message(message)

        self.after(100, self._drain_messages)

    def _handle_message(self, message: str) -> None:
        if not message:
            return

        if message.startswith("__RETURN__:"):
            returncode = int(message.split(":", 1)[1])
            self._running = False
            if returncode == 0:
                if self._progress_active:
                    self.progress_var.set(100.0)
                    self.progress_text_var.set("100%")
                self.status_var.set("Hoàn tất.")
            else:
                self.status_var.set("Có lỗi.")
            self._append_log_line(f"Kết thúc với mã: {returncode}")
            return

        if message.startswith("PROGRESS:"):
            try:
                percent = float(message.split(":", 1)[1].strip())
            except ValueError:
                return
            self.progress_var.set(percent)
            self.progress_text_var.set(f"{int(percent)}%")
            return

        if message.startswith("STATUS:"):
            status = message.split(":", 1)[1].strip()
            self.status_var.set(status)
            self._append_log_line(status)
            return

        if message.startswith("DURATION:"):
            duration_text = message.split(":", 1)[1].strip()
            self.duration_var.set(f"Tổng thời lượng MP3: {duration_text}.")
            return

        if message.startswith("RESULT:"):
            result = message.split(":", 1)[1].strip()
            self._append_log_line(result)
            return

        self._append_log_line(message)

    def _append_log_line(self, message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        self.log.insert("end", clean + "\n")
        self.log.see("end")

    def _on_segment_rows_configure(self, _: tk.Event) -> None:
        self.segment_canvas.configure(scrollregion=self.segment_canvas.bbox("all"))

    def _on_segment_canvas_configure(self, event: tk.Event) -> None:
        if self._segment_canvas_window_id is not None:
            self.segment_canvas.itemconfigure(self._segment_canvas_window_id, width=event.width)

    def _schedule_source_duration_probe(self, *_: object) -> None:
        if self._duration_probe_after_id is not None:
            self.after_cancel(self._duration_probe_after_id)
        self._duration_probe_after_id = self.after(350, self._refresh_source_duration)

    def _refresh_source_duration(self) -> None:
        self._duration_probe_after_id = None
        source_value = normalize_source_value(self.source_var.get())
        if not source_value:
            self._source_duration_seconds = None
            self.duration_var.set("Tổng thời lượng dự kiến: chưa biết.")
            self._refresh_segment_entries()
            return

        if source_value.startswith("http://") or source_value.startswith("https://"):
            self._source_duration_seconds = None
            self.duration_var.set("Tổng thời lượng dự kiến: sẽ hiện sau khi tải link.")
            self._refresh_segment_entries()
            return

        source_path = Path(source_value).expanduser()
        if not source_path.exists():
            self._source_duration_seconds = None
            self.duration_var.set("Tổng thời lượng dự kiến: chưa biết.")
            self._refresh_segment_entries()
            return

        try:
            resolved_ffmpeg = resolve_ffmpeg("auto")
            duration = probe_media_duration(resolved_ffmpeg, source_path)
        except Exception:
            duration = None

        self._source_duration_seconds = duration
        if duration is None:
            self.duration_var.set("Tổng thời lượng dự kiến: chưa biết.")
        else:
            self.duration_var.set(f"Tổng thời lượng dự kiến: {format_duration_display(duration)}.")
        self._refresh_segment_entries()

    def _update_segment_help(self) -> None:
        count = self._safe_segment_count()
        if not self.split_enabled_var.get() or count is None:
            self.segment_help_var.set("Đoạn cuối mặc định cắt đến hết file.")
            return

        if self._source_duration_seconds is None:
            self.segment_help_var.set(
                f"Nhập {max(count - 1, 0)} mốc đầu theo giây. Đoạn {count} từ mốc trước đến hết file."
            )
            return

        self.segment_help_var.set(
            f"Nhập {max(count - 1, 0)} mốc đầu theo giây. Đoạn {count} từ mốc trước đến {format_duration_display(self._source_duration_seconds)}."
        )

    def _last_segment_text(self) -> str:
        count = self._safe_segment_count() or 1
        if self._source_duration_seconds is None:
            return f"Từ mốc trước đến hết file (Đoạn {count})"
        return f"Từ mốc trước đến {format_duration_display(self._source_duration_seconds)}"

    def _clear_log(self) -> None:
        self.log.delete("1.0", "end")
        self.status_var.set("Sẵn sàng.")
        self.progress_var.set(0.0)
        self.progress_text_var.set("0%")


def main() -> None:
    app = App()
    app.mainloop()
