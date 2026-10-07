#!/usr/bin/env python3
"""Convert common video formats with FFmpeg. Run without arguments for the GUI."""

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
    "fast": ("快速", "28", "veryfast"),
    "balanced": ("平衡（推荐）", "23", "medium"),
    "quality": ("高质量", "18", "slow"),
}
LABEL_TO_MODE = {settings[0]: key for key, settings in MODES.items()}


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("找不到 FFmpeg。请先安装 FFmpeg，并将 ffmpeg 加入系统 PATH。")
    return path


def duration_seconds(path: Path) -> float | None:
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(result.stdout.strip())
        return duration if duration > 0 else None
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def build_command(ffmpeg: str, source: Path, target: Path, fmt: str, mode: str) -> list[str]:
    fmt = fmt.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"不支持的输出格式：{fmt}")
    if mode not in MODES:
        raise ValueError(f"不支持的转换模式：{mode}")

    _, crf, preset = MODES[mode]
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        # 4:2:0 chroma formats require even dimensions. Round odd input dimensions
        # down by one pixel so portrait and screen recordings also encode correctly.
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ]
    if fmt == "webm":
        cmd += ["-c:v", "libvpx-vp9", "-crf", crf, "-b:v", "0",
                "-c:a", "libopus", "-b:a", "128k", "-f", "webm"]
    elif fmt == "avi":
        q = {"fast": "6", "balanced": "4", "quality": "2"}[mode]
        cmd += ["-c:v", "mpeg4", "-q:v", q, "-c:a", "libmp3lame",
                "-b:a", "192k", "-f", "avi"]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-f", "matroska" if fmt == "mkv" else fmt]
        if fmt in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
    return cmd + ["-progress", "pipe:1", str(target)]


def convert(
    source: Path,
    target: Path,
    fmt: str,
    mode: str = "balanced",
    on_progress=None,
    cancel: threading.Event | None = None,
) -> None:
    source, target = source.expanduser().resolve(), target.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入文件不存在：{source}")
    if source == target:
        raise ValueError("输出文件不能与输入文件相同。")
    target.parent.mkdir(parents=True, exist_ok=True)
    duration = duration_seconds(source)
    process = subprocess.Popen(
        build_command(ffmpeg_path(), source, target, fmt, mode),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    assert process.stdout is not None and process.stderr is not None
    errors: list[str] = []

    def read_errors() -> None:
        errors.extend(line.rstrip() for line in process.stderr)

    error_reader = threading.Thread(target=read_errors, daemon=True)
    error_reader.start()
    for line in process.stdout:
        if cancel and cancel.is_set():
            process.terminate()
            break
        if duration and on_progress and line.startswith("out_time_ms="):
            try:
                seconds = int(line.partition("=")[2]) / 1_000_000
                on_progress(min(100, seconds / duration * 100))
            except ValueError:
                pass
    return_code = process.wait()
    error_reader.join(timeout=2)
    if cancel and cancel.is_set():
        if process.poll() is None:
            process.kill()
        target.unlink(missing_ok=True)
        raise RuntimeError("转换已取消。")
    if return_code:
        target.unlink(missing_ok=True)
        raise RuntimeError("\n".join(errors[-8:]) or f"FFmpeg 退出码：{return_code}")
    if on_progress:
        on_progress(100)


def default_output(source: Path, fmt: str) -> Path:
    return source.with_name(f"{source.stem}_converted.{fmt}")


class ConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("视频格式转换器")
        self.geometry("650x310")
        self.minsize(600, 290)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value="mp4")
        self.mode_var = tk.StringVar(value=MODES["balanced"][0])
        self.status_var = tk.StringVar(value="请选择输入视频。")
        self.progress_var = tk.DoubleVar(value=0)
        self._build_ui()
        self.after(100, self._poll)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="输入视频：").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=8, pady=7)
        ttk.Button(frame, text="选择文件", command=self._choose_input).grid(row=0, column=2)
        ttk.Label(frame, text="输出格式：").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Combobox(frame, textvariable=self.format_var, values=FORMATS,
                     state="readonly", width=18).grid(row=1, column=1, sticky="w", padx=8, pady=7)
        ttk.Label(frame, text="可选：MP4 / WebM / AVI / MKV / MOV").grid(row=1, column=2, sticky="w")
        ttk.Label(frame, text="转换模式：").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Combobox(frame, textvariable=self.mode_var,
                     values=[value[0] for value in MODES.values()],
                     state="readonly", width=18).grid(row=2, column=1, sticky="w", padx=8, pady=7)
        ttk.Label(frame, text="快速 / 平衡 / 高质量").grid(row=2, column=2, sticky="w")
        ttk.Label(frame, text="输出文件：").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=3, column=1, sticky="ew", padx=8, pady=7)
        ttk.Button(frame, text="选择位置", command=self._choose_output).grid(row=3, column=2)
        ttk.Progressbar(frame, variable=self.progress_var, maximum=100).grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(18, 5))
        ttk.Label(frame, textvariable=self.status_var).grid(row=5, column=0, columnspan=3, sticky="w", pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, pady=(12, 0))
        self.start_button = ttk.Button(buttons, text="开始转换", command=self._start)
        self.start_button.pack(side="left", padx=5)
        self.cancel_button = ttk.Button(buttons, text="取消", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=5)

    def _choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="选择输入视频",
            filetypes=[("视频文件", "*.mp4 *.webm *.avi *.mkv *.mov *.flv *.wmv *.m4v"), ("所有文件", "*.*")],
        )
        if path:
            source = Path(path)
            self.input_var.set(path)
            self.output_var.set(str(default_output(source, self.format_var.get())))

    def _choose_output(self) -> None:
        fmt = self.format_var.get()
        path = filedialog.asksaveasfilename(
            title="选择输出文件", defaultextension=f".{fmt}",
            filetypes=[(f"{fmt.upper()} 文件", f"*.{fmt}"), ("所有文件", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        source = Path(self.input_var.get().strip())
        fmt = self.format_var.get()
        if not source.is_file():
            messagebox.showerror("无法开始", "请选择一个存在的输入视频文件。")
            return
        target = Path(self.output_var.get().strip()) if self.output_var.get().strip() else default_output(source, fmt)
        if target.suffix.lower() != f".{fmt}":
            target = target.with_suffix(f".{fmt}")
            self.output_var.set(str(target))
        if target.exists() and not messagebox.askyesno("确认覆盖", f"文件已存在，是否覆盖？\n{target}"):
            return
        self.cancel_event.clear()
        self.progress_var.set(0)
        self.status_var.set("正在转换……")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        mode = LABEL_TO_MODE.get(self.mode_var.get(), "balanced")
        self.worker = threading.Thread(target=self._run, args=(source, target, fmt, mode), daemon=True)
        self.worker.start()

    def _run(self, source: Path, target: Path, fmt: str, mode: str) -> None:
        try:
            convert(source, target, fmt, mode,
                    on_progress=lambda value: self.events.put(("progress", value)),
                    cancel=self.cancel_event)
            self.events.put(("done", target))
        except Exception as error:
            self.events.put(("error", str(error)))

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("正在取消……")
        self.cancel_button.configure(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.progress_var.set(float(value))
                else:
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if kind == "done":
                        self.status_var.set("转换完成。")
                        messagebox.showinfo("转换完成", f"输出文件：\n{value}")
                    else:
                        self.status_var.set("转换失败。")
                        messagebox.showerror("转换失败", str(value))
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 FFmpeg 转换常见视频格式")
    parser.add_argument("input", nargs="?", help="输入视频路径；不提供时打开图形界面")
    parser.add_argument("-f", "--format", choices=FORMATS, help="输出格式")
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径")
    parser.add_argument("-m", "--mode", choices=tuple(MODES), default="balanced", help="转换模式")
    args = parser.parse_args()
    if not args.input:
        try:
            ffmpeg_path()
        except RuntimeError as error:
            print(error, file=sys.stderr)
            return 1
        ConverterApp().mainloop()
        return 0
    if not args.format:
        parser.error("命令行模式需要提供 --format，例如：-f webm")
    source = Path(args.input)
    target = args.output or default_output(source, args.format)
    try:
        print(f"正在转换：{source} -> {target}")
        convert(source, target, args.format, args.mode,
                on_progress=lambda value: print(f"进度：{value:.1f}%", end="\r", flush=True))
        print(f"\n转换完成：{target}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"转换失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
