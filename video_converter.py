#!/usr/bin/env python3
"""Common video format converter.

Run without arguments to open a small Tkinter GUI, or use the command line:

    python3 src/video_converter.py input.mp4 -f webm

FFmpeg must be installed and available on PATH.
"""

from __future__ import annotations

import argparse
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


FORMATS = ("mp4", "webm", "avi", "mkv", "mov")
MODES = {
    "fast": {"label": "快速", "crf": "28", "preset": "veryfast"},
    "balanced": {"label": "平衡（推荐）", "crf": "23", "preset": "medium"},
    "quality": {"label": "高质量", "crf": "18", "preset": "slow"},
}
MODE_LABEL_TO_KEY = {settings["label"]: key for key, settings in MODES.items()}


def find_ffmpeg() -> str:
    """Return the FFmpeg executable or raise a useful error."""
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError(
            "找不到 FFmpeg。请先安装 FFmpeg，并将 ffmpeg 加入系统 PATH。"
        )
    return executable


def find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def get_duration(input_path: Path) -> float | None:
    """Read duration in seconds when ffprobe is available."""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(result.stdout.strip())
        return duration if duration > 0 else None
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def build_command(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    output_format: str,
    mode: str = "balanced",
) -> list[str]:
    """Build a compatible FFmpeg command for the selected output container."""
    output_format = output_format.lower().lstrip(".")
    if output_format not in FORMATS:
        raise ValueError(f"不支持的输出格式：{output_format}")
    if mode not in MODES:
        raise ValueError(f"不支持的转换模式：{mode}")

    quality = MODES[mode]
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
    ]

    if output_format == "webm":
        command += [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            quality["crf"],
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
            "-f",
            "webm",
        ]
    elif output_format == "avi":
        # AVI has broad playback support; MP3 avoids AAC-in-AVI compatibility issues.
        qvalue = {"fast": "6", "balanced": "4", "quality": "2"}[mode]
        command += [
            "-c:v",
            "mpeg4",
            "-q:v",
            qvalue,
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-f",
            "avi",
        ]
    else:
        command += [
            "-c:v",
            "libx264",
            "-preset",
            quality["preset"],
            "-crf",
            quality["crf"],
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-f",
            "matroska" if output_format == "mkv" else output_format,
        ]
        if output_format in {"mp4", "mov"}:
            command += ["-movflags", "+faststart"]

    command += ["-progress", "pipe:1", str(output_path)]
    return command


def convert_video(
    input_path: Path,
    output_path: Path,
    output_format: str,
    mode: str = "balanced",
    progress_callback=None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Convert one video and raise RuntimeError with FFmpeg's error text on failure."""
    input_path = input_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"输入文件不存在：{input_path}")
    if input_path == output_path:
        raise ValueError("输出文件不能与输入文件相同。")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = build_command(find_ffmpeg(), input_path, output_path, output_format, mode)
    duration = get_duration(input_path)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_lines: list[str] = []
    assert process.stdout is not None
    assert process.stderr is not None

    def collect_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line.rstrip())

    stderr_thread = threading.Thread(target=collect_stderr, daemon=True)
    stderr_thread.start()
    for line in process.stdout:
        if cancel_event and cancel_event.is_set():
            process.terminate()
            break
        if line.startswith("out_time_ms=") and duration and progress_callback:
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
                progress_callback(min(100.0, seconds / duration * 100))
            except ValueError:
                pass

    return_code = process.wait()
    stderr_thread.join(timeout=2)
    if cancel_event and cancel_event.is_set():
        if process.poll() is None:
            process.kill()
        output_path.unlink(missing_ok=True)
        raise RuntimeError("转换已取消。")
    if return_code != 0:
        output_path.unlink(missing_ok=True)
        details = "\n".join(stderr_lines[-8:]).strip()
        raise RuntimeError(details or f"FFmpeg 转换失败，退出码：{return_code}")
    if progress_callback:
        progress_callback(100.0)


def default_output_path(input_path: Path, output_format: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_converted.{output_format}")


class ConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("视频格式转换器")
        self.geometry("650x310")
        self.minsize(600, 290)
        self.resizable(True, False)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value="mp4")
        self.mode_var = tk.StringVar(value=MODES["balanced"]["label"])
        self.status_var = tk.StringVar(value="请选择输入视频。")
        self.progress_var = tk.DoubleVar(value=0)
        self._build_ui()
        self.after(100, self._poll_events)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="输入视频：").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.input_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=7
        )
        ttk.Button(frame, text="选择文件", command=self._choose_input).grid(
            row=0, column=2, pady=7
        )

        ttk.Label(frame, text="输出格式：").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Combobox(
            frame,
            textvariable=self.format_var,
            values=FORMATS,
            state="readonly",
            width=18,
        ).grid(row=1, column=1, sticky="w", padx=8, pady=7)
        ttk.Label(frame, text="可选：MP4 / WebM / AVI / MKV / MOV").grid(
            row=1, column=2, sticky="w", pady=7
        )

        ttk.Label(frame, text="转换模式：").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Combobox(
            frame,
            textvariable=self.mode_var,
            values=[settings["label"] for settings in MODES.values()],
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=7)
        ttk.Label(frame, text="快速 / 平衡 / 高质量").grid(
            row=2, column=2, sticky="w", pady=7
        )

        ttk.Label(frame, text="输出文件：").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.output_var).grid(
            row=3, column=1, sticky="ew", padx=8, pady=7
        )
        ttk.Button(frame, text="选择位置", command=self._choose_output).grid(
            row=3, column=2, pady=7
        )

        self.progress = ttk.Progressbar(
            frame, variable=self.progress_var, maximum=100, mode="determinate"
        )
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(18, 5))
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=5
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, pady=(12, 0))
        self.start_button = ttk.Button(buttons, text="开始转换", command=self._start)
        self.start_button.pack(side="left", padx=5)
        self.cancel_button = ttk.Button(
            buttons, text="取消", command=self._cancel, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=5)

    def _choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="选择输入视频",
            filetypes=[
                ("视频文件", "*.mp4 *.webm *.avi *.mkv *.mov *.flv *.wmv *.m4v"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            input_path = Path(path)
            self.input_var.set(path)
            self.output_var.set(str(default_output_path(input_path, self.format_var.get())))

    def _choose_output(self) -> None:
        output_format = self.format_var.get()
        path = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=f".{output_format}",
            filetypes=[(f"{output_format.upper()} 文件", f"*.{output_format}"), ("所有文件", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        input_path = Path(self.input_var.get().strip())
        output_format = self.format_var.get().lower()
        output_text = self.output_var.get().strip()
        if not input_path.is_file():
            messagebox.showerror("无法开始", "请选择一个存在的输入视频文件。")
            return
        output_path = Path(output_text) if output_text else default_output_path(input_path, output_format)
        if output_path.suffix.lower() != f".{output_format}":
            output_path = output_path.with_suffix(f".{output_format}")
            self.output_var.set(str(output_path))
        if output_path.exists() and not messagebox.askyesno("确认覆盖", f"文件已存在，是否覆盖？\n{output_path}"):
            return

        self.cancel_event.clear()
        self.progress_var.set(0)
        self.status_var.set("正在转换……")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        mode = MODE_LABEL_TO_KEY.get(self.mode_var.get(), "balanced")
        self.worker = threading.Thread(
            target=self._run_conversion,
            args=(input_path, output_path, output_format, mode),
            daemon=True,
        )
        self.worker.start()

    def _run_conversion(self, input_path: Path, output_path: Path, output_format: str, mode: str) -> None:
        try:
            convert_video(
                input_path,
                output_path,
                output_format,
                mode,
                progress_callback=lambda value: self.events.put(("progress", value)),
                cancel_event=self.cancel_event,
            )
            self.events.put(("done", output_path))
        except Exception as error:  # surface conversion errors in the GUI
            self.events.put(("error", str(error)))

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("正在取消……")
        self.cancel_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "progress":
                    self.progress_var.set(float(value))
                elif event == "done":
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status_var.set("转换完成。")
                    messagebox.showinfo("转换完成", f"输出文件：\n{value}")
                elif event == "error":
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status_var.set("转换失败。")
                    messagebox.showerror("转换失败", str(value))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 FFmpeg 转换常见视频格式")
    parser.add_argument("input", nargs="?", help="输入视频路径；不提供时打开图形界面")
    parser.add_argument("-f", "--format", choices=FORMATS, help="输出格式")
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径")
    parser.add_argument("-m", "--mode", choices=tuple(MODES), default="balanced", help="转换模式")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input:
        try:
            find_ffmpeg()
        except RuntimeError as error:
            print(error, file=sys.stderr)
            return 1
        app = ConverterApp()
        app.mainloop()
        return 0

    if not args.format:
        print("命令行模式需要提供 --format，例如：-f webm", file=sys.stderr)
        return 2
    input_path = Path(args.input)
    output_path = args.output or default_output_path(input_path, args.format)
    try:
        print(f"正在转换：{input_path} -> {output_path}")
        convert_video(
            input_path,
            output_path,
            args.format,
            args.mode,
            progress_callback=lambda value: print(f"进度：{value:.1f}%", end="\r", flush=True),
        )
        print(f"\n转换完成：{output_path}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"转换失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
