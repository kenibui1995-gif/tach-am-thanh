from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .commands import (
    ToolError,
    console_print,
    karaoke_reduce_vocal,
    replace_video_audio,
    runtime_status,
    separate_with_demucs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tach-am-thanh",
        description="Công cụ tách lời khỏi nhạc và ghép âm thanh nền vào video.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    replace_parser = subparsers.add_parser(
        "replace-video-audio",
        aliases=["replace"],
        help="Ghép video gốc với âm thanh nền đã tách, theo luồng FFmpeg của tài liệu gốc.",
    )
    replace_parser.add_argument("--video", required=True, type=Path, help="Video đầu vào.")
    replace_parser.add_argument(
        "--audio",
        required=True,
        type=Path,
        help="File âm thanh nền đã tách lời, ví dụ file lấy từ vocalremover.org.",
    )
    replace_parser.add_argument("--output", required=True, type=Path, help="Video đầu ra.")
    replace_parser.add_argument("--ffmpeg", default="auto", help="Đường dẫn FFmpeg hoặc `auto`.")
    replace_parser.add_argument(
        "--no-shortest",
        action="store_true",
        help="Không cắt theo stream ngắn nhất khi video và âm thanh lệch độ dài.",
    )
    replace_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="In lệnh sẽ chạy nhưng không thực thi.",
    )

    separate_parser = subparsers.add_parser(
        "separate",
        help="Tách nhạc không lời, giọng hát/lời nói hoặc xuất cả 2 file MP3.",
    )
    separate_parser.add_argument("input", help="File local hoặc link YouTube đầu vào.")
    separate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="Thư mục chứa các MP3 đầu ra.",
    )
    separate_parser.add_argument("--ffmpeg", default="auto", help="Đường dẫn FFmpeg hoặc `auto`.")
    separate_parser.add_argument("--demucs", default="auto", help="Đường dẫn Demucs hoặc `auto`.")
    separate_parser.add_argument("--yt-dlp", dest="ytdlp", default="auto", help="Đường dẫn yt-dlp hoặc `auto`.")
    separate_parser.add_argument("--model", default="htdemucs", help="Model Demucs.")
    separate_parser.add_argument(
        "--mode", choices=("music", "vocals", "both"), default="music",
        help="music: chỉ nhạc (mặc định); vocals: chỉ lời; both: xuất hai MP3.",
    )
    separate_parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="Số tiến trình Demucs sử dụng.",
    )
    separate_parser.add_argument(
        "--split-count",
        type=int,
        default=None,
        help="Nếu có, cắt MP3 tổng thành số đoạn tương ứng. Đoạn cuối mặc định đến hết file.",
    )
    separate_parser.add_argument(
        "--segment-end",
        action="append",
        default=[],
        help="Mốc kết thúc cho các đoạn đầu. Lặp lại cho từng mốc, đoạn cuối tự động đến hết file.",
    )
    separate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="In lệnh sẽ chạy nhưng không thực thi.",
    )

    karaoke_parser = subparsers.add_parser(
        "karaoke",
        help="Giảm vocal nhanh bằng FFmpeg center-cancel. Chất lượng phụ thuộc bản mix.",
    )
    karaoke_parser.add_argument("--input", required=True, type=Path, help="Audio/video đầu vào.")
    karaoke_parser.add_argument("--output", required=True, type=Path, help="Audio đầu ra.")
    karaoke_parser.add_argument("--ffmpeg", default="auto", help="Đường dẫn FFmpeg hoặc `auto`.")
    karaoke_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="In lệnh sẽ chạy nhưng không thực thi.",
    )

    subparsers.add_parser("doctor", help="Kiểm tra FFmpeg và Demucs local/PATH.")
    subparsers.add_parser("gui", help="Mở giao diện chọn file.")

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console_streams()

    if argv is None and len(sys.argv) == 1:
        from .gui import main as gui_main

        _hide_console_window()
        gui_main()
        return 0

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command in {"replace-video-audio", "replace"}:
            replace_video_audio(
                ffmpeg=args.ffmpeg,
                video_path=args.video,
                audio_path=args.audio,
                output_path=args.output,
                dry_run=args.dry_run,
                shortest=not args.no_shortest,
            )
            return 0

        if args.command == "separate":
            separate_with_demucs(
                ffmpeg=args.ffmpeg,
                demucs=args.demucs,
                ytdlp=args.ytdlp,
                input_source=args.input,
                output_dir=args.output_dir,
                model=args.model,
                jobs=args.jobs,
                split_count=args.split_count,
                segment_ends=args.segment_end,
                mode=args.mode,
                dry_run=args.dry_run,
            )
            return 0

        if args.command == "karaoke":
            karaoke_reduce_vocal(
                ffmpeg=args.ffmpeg,
                input_path=args.input,
                output_path=args.output,
                dry_run=args.dry_run,
            )
            return 0

        if args.command == "doctor":
            return _doctor()

        if args.command == "gui":
            from .gui import main as gui_main

            _hide_console_window()
            gui_main()
            return 0

    except ToolError as exc:
        console_print(f"Lỗi: {exc}", file=sys.stderr)
        return 2

    parser.error("Lệnh không hợp lệ")
    return 2


def _doctor() -> int:
    status = runtime_status()
    ffmpeg = status["ffmpeg"]
    demucs = status["demucs"]
    ytdlp = status["ytdlp"]

    console_print(f"Python: {sys.version.split()[0]}")
    console_print(f"Runtime local: {status['runtime_dir']}")
    console_print(f"FFmpeg: {ffmpeg or 'chưa tìm thấy'}")
    console_print(f"Demucs: {demucs or 'chưa tìm thấy'}")
    console_print(f"YT-DLP: {ytdlp or 'chưa tìm thấy'}")

    if not ffmpeg:
        console_print("Cần FFmpeg để ghép video/audio và xử lý file video.")

    if not demucs:
        console_print("Cần Demucs khi dùng lệnh `separate` để tách lời bằng AI.")

    if not ytdlp:
        console_print("Cần yt-dlp nếu muốn dán link YouTube để tải tự động.")

    if not ffmpeg or not demucs or not ytdlp:
        console_print("Chạy `setup_local.bat` để cài dependency vào `.runtime` trong thư mục này. Không cài vào ổ C.")
    return 0


def _configure_console_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except OSError:
                pass


def _hide_console_window() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        window = kernel32.GetConsoleWindow()
        if window:
            user32.ShowWindow(window, 0)
    except Exception:
        pass
