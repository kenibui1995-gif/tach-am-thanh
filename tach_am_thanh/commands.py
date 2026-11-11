from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, TextIO
from urllib.parse import parse_qsl, urlparse


class ToolError(RuntimeError):
    """Raised when a user-facing command cannot be completed."""


VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
}

INVALID_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
YTDLP_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
DEFAULT_OUTPUT_SUFFIX = "_khong_loi.mp3"
VOCALS_OUTPUT_SUFFIX = "_chi_loi.mp3"
SEPARATION_MODES = ("music", "vocals", "both")
DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
TIME_TOKEN_PATTERN = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>hours?|hrs?|gio|h|minutes?|mins?|minute|min|phut|ph|m|seconds?|secs?|second|sec|giay|s)"
)
PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

def _detect_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _detect_bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _detect_app_root()
BUNDLE_ROOT = _detect_bundle_root()
WORKER_SCRIPT_PATH = BUNDLE_ROOT / "tach_am_thanh" / "demucs_worker.py"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
RUNTIME_PATHS_FILE = RUNTIME_DIR / "runtime_paths.json"


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int | None


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_url_source(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_youtube_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    host = parsed.netloc.lower()
    return host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def sanitize_filename_component(value: str) -> str:
    cleaned = INVALID_FILENAME_PATTERN.sub("_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    if not cleaned:
        cleaned = "output"
    return cleaned[:180]


def normalize_source_value(value: str) -> str:
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()
    return normalized


def normalize_media_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = parsed.netloc.lower()

    if host in {"youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        return value

    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        return value

    if parsed.path == "/watch":
        params = dict(parse_qsl(parsed.query, keep_blank_values=False))
        video_id = params.get("v", "").strip()
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        return value

    if parsed.path.startswith("/shorts/"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            return f"https://www.youtube.com/watch?v={parts[1]}"

    return value


def parse_time_value(value: str) -> float:
    normalized = _normalize_time_text(value)
    if not normalized:
        raise ToolError("Mốc thời gian không được để trống.")

    if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        seconds = float(normalized)
    elif ":" in normalized and re.fullmatch(r"\d+(?::\d+){1,2}(?:\.\d+)?", normalized):
        parts = [float(part) for part in normalized.split(":")]
        if len(parts) == 2:
            minutes, seconds_part = parts
            seconds = (minutes * 60.0) + seconds_part
        elif len(parts) == 3:
            hours, minutes, seconds_part = parts
            seconds = (hours * 3600.0) + (minutes * 60.0) + seconds_part
        else:
            raise ToolError(f"Mốc thời gian không hợp lệ: {value}")
    else:
        seconds = _parse_time_tokens(normalized, original=value)

    if seconds <= 0:
        raise ToolError(f"Mốc thời gian phải lớn hơn 0: {value}")
    return seconds


def parse_segment_end_times(values: Sequence[str], *, expected_count: int | None = None) -> list[float]:
    cleaned_values = [value.strip() for value in values if value.strip()]
    if expected_count is not None:
        if expected_count <= 0:
            raise ToolError("Số đoạn cắt phải lớn hơn 0.")
        if len(cleaned_values) != expected_count:
            raise ToolError(f"Cần nhập đủ {expected_count} mốc kết thúc cho {expected_count} đoạn.")
    elif not cleaned_values:
        return []

    end_times: list[float] = []
    previous = 0.0
    for index, raw_value in enumerate(cleaned_values, start=1):
        current = parse_time_value(raw_value)
        if current <= previous:
            raise ToolError(f"Mốc kết thúc đoạn {index} phải lớn hơn mốc trước đó.")
        end_times.append(current)
        previous = current
    return end_times


def format_ffmpeg_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remain = seconds - (hours * 3600) - (minutes * 60)
    return f"{hours:02d}:{minutes:02d}:{remain:06.3f}"


def format_duration_display(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    hours = rounded // 3600
    minutes = (rounded % 3600) // 60
    remain = rounded % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remain:02d}"
    return f"{minutes:02d}:{remain:02d}"


def parse_duration_output(output: str) -> float | None:
    match = DURATION_PATTERN.search(output)
    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return (hours * 3600.0) + (minutes * 60.0) + seconds


def command_to_string(command: Sequence[str]) -> str:
    if platform.system().lower() == "windows":
        return subprocess.list2cmdline(list(command))
    return " ".join(_shell_quote(part) for part in command)


def safe_console_text(text: str, encoding: str | None) -> str:
    if not encoding:
        return text

    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding)


def console_print(text: str, *, file: TextIO | None = None) -> None:
    stream = file or sys.stdout
    safe_text = safe_console_text(text, getattr(stream, "encoding", None))
    stream.write(safe_text + "\n")
    stream.flush()


def resolve_executable(
    command: str,
    label: str,
    install_hint: str,
    runtime_candidates: Sequence[Path] = (),
    which_names: Sequence[str] | None = None,
) -> str:
    if command.strip().lower() == "auto":
        runtime_candidate = _first_existing(runtime_candidates)
        if runtime_candidate:
            return str(runtime_candidate)

        for name in which_names or (label.lower(),):
            resolved = shutil.which(name)
            if resolved:
                return resolved

        raise ToolError(
            f"Không tìm thấy {label}.\n"
            f"{install_hint}\n"
            f"Tool ưu tiên runtime local tại: {RUNTIME_DIR}"
        )

    candidate = Path(command)
    if candidate.exists():
        return str(candidate)

    resolved = shutil.which(command)
    if resolved:
        return resolved

    raise ToolError(
        f"Không tìm thấy {label}: {command}\n"
        f"{install_hint}\n"
        "Nếu đã có sẵn, truyền đường dẫn bằng tùy chọn tương ứng."
    )


def resolve_ffmpeg(command: str = "auto") -> str:
    return resolve_executable(
        command,
        "FFmpeg",
        "Chạy `setup_local.bat` để cài FFmpeg portable vào `.runtime` trên ổ hiện tại, không cài vào ổ C.",
        _ffmpeg_candidates(),
        which_names=("ffmpeg",),
    )


def resolve_demucs(command: str = "auto") -> str:
    return resolve_executable(
        command,
        "Demucs",
        "Chạy `setup_local.bat` để cài Demucs vào `.runtime\\venv` trên ổ hiện tại, không cài vào ổ C.",
        _demucs_candidates(),
        which_names=("demucs",),
    )


def resolve_ytdlp(command: str = "auto") -> str:
    return resolve_executable(
        command,
        "YT-DLP",
        "Chạy `setup_local.bat` để cài yt-dlp vào `.runtime\\venv` trên ổ hiện tại, không cài vào ổ C.",
        _ytdlp_candidates(),
        which_names=("yt-dlp", "yt_dlp"),
    )


def runtime_status() -> dict[str, str | None]:
    ffmpeg = _safe_resolve(resolve_ffmpeg)
    demucs = _safe_resolve(resolve_demucs)
    ytdlp = _safe_resolve(resolve_ytdlp)
    return {
        "runtime_dir": str(RUNTIME_DIR),
        "ffmpeg": ffmpeg,
        "demucs": demucs,
        "ytdlp": ytdlp,
    }


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    cache_dir = RUNTIME_DIR / "cache"
    temp_dir = RUNTIME_DIR / "tmp"
    torch_home = RUNTIME_DIR / "torch"
    hf_home = RUNTIME_DIR / "huggingface"
    mpl_dir = RUNTIME_DIR / "matplotlib"

    for path in (cache_dir, temp_dir, torch_home, hf_home, mpl_dir):
        path.mkdir(parents=True, exist_ok=True)

    env["XDG_CACHE_HOME"] = str(cache_dir)
    env["TORCH_HOME"] = str(torch_home)
    env["HF_HOME"] = str(hf_home)
    env["HUGGINGFACE_HUB_CACHE"] = str(hf_home / "hub")
    env["MPLCONFIGDIR"] = str(mpl_dir)
    env["PIP_CACHE_DIR"] = str(RUNTIME_DIR / "pip-cache")
    env["PYTHONUSERBASE"] = str(RUNTIME_DIR / "python-user")
    env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
    env["PYTHONUTF8"] = "1"
    env["TMP"] = str(temp_dir)
    env["TEMP"] = str(temp_dir)

    for key in PROXY_ENV_NAMES:
        proxy_value = env.get(key)
        if proxy_value and should_bypass_proxy_env(proxy_value):
            env.pop(key, None)
    return env


def should_bypass_proxy_env(value: str) -> bool:
    raw_value = str(value or "").strip()
    if not raw_value:
        return False

    candidate = raw_value if "://" in raw_value else f"http://{raw_value}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False

    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    return hostname in {"127.0.0.1", "localhost", "::1"} and port == 9


def require_input_file(path: Path, label: str) -> None:
    if not path.exists():
        raise ToolError(f"Không tìm thấy {label}: {path}")
    if not path.is_file():
        raise ToolError(f"{label} không phải file: {path}")


def ensure_output_parent(path: Path) -> None:
    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def probe_media_duration(ffmpeg: str, input_path: Path) -> float | None:
    if not input_path.exists() or not input_path.is_file():
        return None

    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(input_path),
        ],
        check=False,
        env=runtime_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return parse_duration_output(completed.stdout or "")


def summarize_command_failure(output: str, returncode: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return f"Lệnh thất bại với mã lỗi {returncode}"

    prioritized = [line for line in lines if line.upper().startswith("ERROR:")]
    detail = prioritized[-1] if prioritized else lines[-1]
    friendly = _friendly_external_error(detail)
    if friendly:
        return friendly
    return _clean_external_error_detail(detail)


def _friendly_external_error(detail: str) -> str | None:
    normalized = detail.lower()

    if "unable to connect to proxy" in normalized or ("transporterror" in normalized and "10061" in normalized):
        return "Tool đang bị chặn bởi cấu hình proxy lỗi của môi trường chạy. Tôi đã vá để bỏ qua proxy 127.0.0.1:9."
    if "video unavailable" in normalized:
        return "Video YouTube này hiện không khả dụng, có thể đã bị xóa, đặt riêng tư hoặc bị chặn."
    if "this video is not available" in normalized:
        return "Video YouTube này hiện không khả dụng, có thể đã bị xóa, đặt riêng tư hoặc bị chặn."
    if "private video" in normalized:
        return "Video YouTube này đang để riêng tư nên tool không thể tải."
    if "members-only" in normalized or "membership" in normalized:
        return "Video YouTube này dành cho thành viên nên tool không thể tải trực tiếp."
    if "not available in your country" in normalized or "not available in your region" in normalized:
        return "Video YouTube này đang bị chặn theo khu vực nên tool không thể tải ở máy hiện tại."
    if "sign in to confirm your age" in normalized or "age-restricted" in normalized:
        return "Video YouTube này bị giới hạn độ tuổi nên cần cookie đăng nhập mới tải được."
    if "sign in to confirm you're not a bot" in normalized:
        return "YouTube đang yêu cầu xác minh đăng nhập hoặc cookie nên tool chưa tải được link này."
    if "unable to extract" in normalized:
        return "yt-dlp không đọc được dữ liệu video từ YouTube. Có thể link lỗi hoặc YouTube đổi định dạng."
    return None


def _clean_external_error_detail(detail: str) -> str:
    cleaned = detail.strip()
    if cleaned.upper().startswith("ERROR:"):
        cleaned = cleaned[6:].strip()
    return cleaned


def run_command(command: Sequence[str], dry_run: bool = False) -> CommandResult:
    command_list = [str(part) for part in command]

    if dry_run:
        console_print(command_to_string(command_list))
        return CommandResult(command=command_list, returncode=None)

    completed = subprocess.run(command_list, check=False, env=runtime_env())
    if completed.returncode != 0:
        raise ToolError(f"Lệnh thất bại với mã lỗi {completed.returncode}")

    return CommandResult(command=command_list, returncode=completed.returncode)


def run_command_capture(
    command: Sequence[str],
    dry_run: bool = False,
    *,
    echo_output: bool = False,
) -> tuple[CommandResult, str]:
    command_list = [str(part) for part in command]

    if dry_run:
        console_print(command_to_string(command_list))
        return CommandResult(command=command_list, returncode=None), ""

    completed = subprocess.run(
        command_list,
        check=False,
        env=runtime_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout or ""
    if echo_output:
        for line in output.splitlines():
            console_print(line)
    if completed.returncode != 0:
        raise ToolError(summarize_command_failure(output, completed.returncode))
    return CommandResult(command=command_list, returncode=completed.returncode), output


def build_replace_audio_command(
    ffmpeg: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    overwrite: bool = True,
    shortest: bool = True,
    audio_bitrate: str = "192k",
) -> list[str]:
    command = [
        ffmpeg,
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
    ]

    if shortest:
        command.append("-shortest")
    if overwrite:
        command.append("-y")

    command.append(str(output_path))
    return command


def build_extract_audio_command(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    *,
    sample_rate: int = 44100,
) -> list[str]:
    return [
        ffmpeg,
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        "-y",
        str(output_path),
    ]


def build_demucs_command(
    demucs: str,
    input_path: Path,
    output_dir: Path,
    *,
    model: str = "htdemucs",
    jobs: int | None = None,
) -> list[str]:
    command = [
        demucs,
        "--two-stems=vocals",
        "-n",
        model,
        "-o",
        str(output_dir),
    ]
    if jobs is not None:
        command.extend(["-j", str(jobs)])
    command.append(str(input_path))
    return command


def build_demucs_worker_command(
    python_executable: str,
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    *,
    model: str = "htdemucs",
    jobs: int | None = None,
    bitrate_kbps: int = 320,
    mode: str = "music",
    vocals_output_path: Path | None = None,
) -> list[str]:
    if mode not in SEPARATION_MODES:
        raise ToolError(f"Chế độ tách âm thanh không hợp lệ: {mode}")
    if mode in {"vocals", "both"} and vocals_output_path is None:
        raise ToolError("Thiếu đường dẫn xuất MP3 giọng hát/lời nói.")
    command = [
        python_executable,
        str(WORKER_SCRIPT_PATH),
        "--ffmpeg",
        ffmpeg,
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--model",
        model,
        "--bitrate",
        str(bitrate_kbps),
    ]
    if jobs is not None:
        command.extend(["--jobs", str(jobs)])
    if mode != "music":
        command.extend(["--mode", mode, "--vocals-output", str(vocals_output_path)])
    return command


def build_ytdlp_download_command(
    ytdlp: str,
    source_url: str,
    download_dir: Path,
    *,
    ffmpeg_location: str | None = None,
    use_youtube_ejs: bool = False,
) -> list[str]:
    command = [
        ytdlp,
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--windows-filenames",
        "-f",
        "bestaudio/best",
        "--print",
        "after_move:filepath",
        "-P",
        str(download_dir),
        "-o",
        YTDLP_OUTPUT_TEMPLATE,
    ]
    if ffmpeg_location:
        command.extend(["--ffmpeg-location", ffmpeg_location])
    if use_youtube_ejs and is_youtube_url(source_url):
        command.extend(["--js-runtimes", "node", "--remote-components", "ejs:github"])
    command.append(source_url)
    return command


def build_karaoke_command(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    *,
    overwrite: bool = True,
) -> list[str]:
    command = [
        ffmpeg,
        "-i",
        str(input_path),
        "-af",
        "pan=stereo|c0=c0-c1|c1=c1-c0",
    ]
    if overwrite:
        command.append("-y")
    command.append(str(output_path))
    return command


def build_audio_segment_command(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    bitrate: str = "320k",
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-ss",
        format_ffmpeg_timestamp(start_seconds),
        "-to",
        format_ffmpeg_timestamp(end_seconds),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-y",
        str(output_path),
    ]


def build_segment_output_path(total_output_path: Path, segment_index: int) -> Path:
    return total_output_path.with_name(f"{total_output_path.stem}_{segment_index}{total_output_path.suffix}")


def normalize_segment_end_times(
    values: Sequence[str],
    *,
    split_count: int,
    total_duration: float,
) -> list[float]:
    if split_count <= 0:
        return []
    if total_duration <= 0:
        raise ToolError("Không đọc được tổng thời lượng để cắt đoạn.")

    expected_boundaries = max(split_count - 1, 0)
    cleaned_values = [value.strip() for value in values if value.strip()]
    if len(cleaned_values) != expected_boundaries:
        if expected_boundaries == 0:
            raise ToolError("Không cần nhập mốc cắt khi chỉ có 1 đoạn.")
        raise ToolError(
            f"Chỉ cần nhập {expected_boundaries} mốc đầu theo giây. "
            f"Đoạn {split_count} mặc định đến hết file {format_duration_display(total_duration)}."
        )

    end_times = parse_segment_end_times(cleaned_values, expected_count=expected_boundaries) if cleaned_values else []
    for index, end_time in enumerate(end_times, start=1):
        if end_time >= total_duration:
            raise ToolError(
                f"Mốc kết thúc đoạn {index} phải nhỏ hơn tổng thời lượng {format_duration_display(total_duration)}."
            )

    return [*end_times, total_duration]


def replace_video_audio(
    *,
    ffmpeg: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    dry_run: bool = False,
    shortest: bool = True,
) -> CommandResult:
    require_input_file(video_path, "video dau vao")
    require_input_file(audio_path, "audio nen dau vao")
    ensure_output_parent(output_path)

    resolved_ffmpeg = resolve_ffmpeg(ffmpeg)
    command = build_replace_audio_command(
        resolved_ffmpeg,
        video_path,
        audio_path,
        output_path,
        shortest=shortest,
    )
    return run_command(command, dry_run=dry_run)


def karaoke_reduce_vocal(
    *,
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    dry_run: bool = False,
) -> CommandResult:
    require_input_file(input_path, "audio/video dau vao")
    ensure_output_parent(output_path)
    resolved_ffmpeg = resolve_ffmpeg(ffmpeg)
    return run_command(
        build_karaoke_command(resolved_ffmpeg, input_path, output_path),
        dry_run=dry_run,
    )


def separate_with_demucs(
    *,
    ffmpeg: str,
    demucs: str,
    ytdlp: str,
    input_source: str,
    output_dir: Path,
    model: str = "htdemucs",
    jobs: int | None = None,
    split_count: int | None = None,
    segment_ends: Sequence[str] = (),
    mode: str = "music",
    dry_run: bool = False,
) -> list[CommandResult]:
    if mode not in SEPARATION_MODES:
        raise ToolError(f"Chế độ tách âm thanh không hợp lệ: {mode}")
    source_value = normalize_source_value(input_source)
    if not source_value:
        raise ToolError("Hãy nhập file đầu vào hoặc link YouTube.")
    if is_url_source(source_value):
        source_value = normalize_media_url(source_value)

    if split_count is None:
        split_count = (len(segment_ends) + 1) if segment_ends else 0

    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_ffmpeg = resolve_ffmpeg(ffmpeg)
    resolved_demucs = resolve_demucs(demucs)
    demucs_python = _demucs_python_path(Path(resolved_demucs))
    if demucs_python is None:
        raise ToolError("Không tìm thấy Python runtime của Demucs. Hãy chạy `setup_local.bat`.")

    work_dir = RUNTIME_DIR / "tmp" / "jobs" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    commands: list[CommandResult] = []

    try:
        if is_url_source(source_value):
            resolved_ytdlp = resolve_ytdlp(ytdlp)
            console_print("STATUS: Đang tải media từ link...")
            download_dir = work_dir / "downloads"
            media_path: Path
            if dry_run:
                commands.append(
                    run_command(
                        build_ytdlp_download_command(
                            resolved_ytdlp,
                            source_value,
                            download_dir,
                            ffmpeg_location=resolved_ffmpeg,
                            use_youtube_ejs=bool(shutil.which("node")),
                        ),
                        dry_run=True,
                    )
                )
                media_path = download_dir / "downloaded_input.media"
            else:
                download_result, media_path = download_source_media(
                    ytdlp=resolved_ytdlp,
                    source_url=source_value,
                    download_dir=download_dir,
                    ffmpeg_location=resolved_ffmpeg,
                )
                commands.append(download_result)
                console_print("STATUS: Đã tải xong.")
        else:
            media_path = Path(source_value).expanduser()
            require_input_file(media_path, "file đầu vào")

        stem = sanitize_filename_component(media_path.stem)
        music_output_path = output_dir / f"{stem}{DEFAULT_OUTPUT_SUFFIX}"
        vocals_output_path = output_dir / f"{stem}{VOCALS_OUTPUT_SUFFIX}"
        staged_music_path = work_dir / music_output_path.name
        staged_vocals_path = work_dir / vocals_output_path.name
        selected_outputs = []
        if mode in {"music", "both"}:
            selected_outputs.append(("Nhạc không lời", music_output_path, staged_music_path))
        if mode in {"vocals", "both"}:
            selected_outputs.append(("Giọng hát/lời nói", vocals_output_path, staged_vocals_path))
        reference_duration = probe_media_duration(resolved_ffmpeg, media_path) if dry_run and split_count else None
        commands.append(
            run_command(
                build_demucs_worker_command(
                    str(demucs_python),
                    resolved_ffmpeg,
                    media_path,
                    staged_music_path,
                    model=model,
                    jobs=jobs,
                    mode=mode,
                    vocals_output_path=staged_vocals_path if mode != "music" else None,
                ),
                dry_run=dry_run,
            )
        )

        if dry_run:
            if split_count:
                if reference_duration is None:
                    raise ToolError("Không đọc được tổng thời lượng để nội suy đoạn cuối trong `dry-run`.")
                segment_end_times = normalize_segment_end_times(
                    segment_ends,
                    split_count=split_count,
                    total_duration=reference_duration,
                )
                for _, output_path, _ in selected_outputs:
                    commands.extend(
                        split_audio_segments(
                            ffmpeg=resolved_ffmpeg,
                            input_path=output_path,
                            end_times=segment_end_times,
                            dry_run=True,
                        )
                    )
            return commands

        # Chỉ công bố kết quả sau khi mọi file được tạo thành công, tránh nhận nhầm file cũ.
        for _, output_path, staged_path in selected_outputs:
            if not staged_path.is_file() or staged_path.stat().st_size == 0:
                raise ToolError(f"Không tạo được MP3 đầu ra: {output_path.name}")
        for label, output_path, staged_path in selected_outputs:
            staged_path.replace(output_path)
            console_print(f"RESULT: {label}: {output_path.name}")

        total_duration = probe_media_duration(resolved_ffmpeg, selected_outputs[0][1])
        if total_duration is not None:
            console_print(f"DURATION: {format_duration_display(total_duration)}")

        if split_count:
            if total_duration is None:
                raise ToolError("Không đọc được thời lượng file MP3 đã tách để cắt đoạn.")
            segment_end_times = normalize_segment_end_times(
                segment_ends,
                split_count=split_count,
                total_duration=total_duration,
            )
            console_print(f"STATUS: Đang cắt {len(selected_outputs)} file MP3 thành {split_count} đoạn/file...")
            for _, output_path, _ in selected_outputs:
                commands.extend(
                    split_audio_segments(
                        ffmpeg=resolved_ffmpeg,
                        input_path=output_path,
                        end_times=segment_end_times,
                        dry_run=False,
                    )
                )
        console_print("STATUS: Hoàn tất.")
        return commands
    finally:
        if work_dir.exists() and not dry_run:
            shutil.rmtree(work_dir, ignore_errors=True)


def download_source_media(
    *,
    ytdlp: str,
    source_url: str,
    download_dir: Path,
    ffmpeg_location: str | None = None,
) -> tuple[CommandResult, Path]:
    download_dir.mkdir(parents=True, exist_ok=True)
    result, output = run_command_capture(
        build_ytdlp_download_command(
            ytdlp,
            source_url,
            download_dir,
            ffmpeg_location=ffmpeg_location,
            use_youtube_ejs=bool(shutil.which("node")),
        ),
        echo_output=False,
    )
    downloaded_path = extract_downloaded_media_path(output)
    require_input_file(downloaded_path, "file vừa tải")
    return result, downloaded_path


def split_audio_segments(
    *,
    ffmpeg: str,
    input_path: Path,
    end_times: Sequence[float],
    dry_run: bool = False,
) -> list[CommandResult]:
    commands: list[CommandResult] = []
    start_time = 0.0
    for index, end_time in enumerate(end_times, start=1):
        output_path = build_segment_output_path(input_path, index)
        ensure_output_parent(output_path)
        commands.append(
            run_command(
                build_audio_segment_command(
                    ffmpeg,
                    input_path,
                    output_path,
                    start_seconds=start_time,
                    end_seconds=end_time,
                ),
                dry_run=dry_run,
            )
        )
        console_print(f"RESULT: Đoạn {index}: {output_path.name}")
        start_time = end_time
    return commands


def extract_downloaded_media_path(output: str) -> Path:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        candidate = Path(line.strip('"'))
        if candidate.exists():
            return candidate
    raise ToolError("Không xác định được file media vừa tải từ yt-dlp.")


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-=.,/:")
    if all(char in safe for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _first_existing(paths: Sequence[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _safe_resolve(resolver: Any) -> str | None:
    try:
        return str(resolver("auto"))
    except ToolError:
        return None


def _runtime_paths() -> dict[str, str]:
    if not RUNTIME_PATHS_FILE.exists():
        return {}

    try:
        import json

        data = json.loads(RUNTIME_PATHS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}

    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}
    original_runtime_dir = str(data.get("runtime_dir") or "").strip()
    current_runtime_dir = str(RUNTIME_DIR)
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _remap_runtime_path(value, original_runtime_dir, current_runtime_dir)
    return result


def _remap_runtime_path(value: str, original_runtime_dir: str, current_runtime_dir: str) -> str:
    text = str(value or "").strip()
    if not text or not original_runtime_dir or not current_runtime_dir:
        return text

    original_norm = os.path.normcase(os.path.normpath(original_runtime_dir))
    current_norm = os.path.normpath(current_runtime_dir)
    text_norm = os.path.normcase(os.path.normpath(text))

    if not text_norm.startswith(original_norm):
        return text

    suffix = text[len(original_runtime_dir) :].lstrip("\\/")
    if not suffix:
        return current_norm
    return str(Path(current_norm) / Path(suffix))


def _ffmpeg_candidates() -> list[Path]:
    paths = _runtime_paths()
    candidates: list[Path] = []
    if paths.get("ffmpeg"):
        candidates.append(Path(paths["ffmpeg"]))

    candidates.extend(
        [
            RUNTIME_DIR / "ffmpeg" / "bin" / "ffmpeg.exe",
            RUNTIME_DIR / "ffmpeg.exe",
            RUNTIME_DIR / "venv" / "Scripts" / "ffmpeg.exe",
        ]
    )

    imageio_ffmpeg = _imageio_ffmpeg_candidate()
    if imageio_ffmpeg:
        candidates.append(imageio_ffmpeg)

    return candidates


def _demucs_candidates() -> list[Path]:
    paths = _runtime_paths()
    candidates: list[Path] = []
    if paths.get("demucs"):
        candidates.append(Path(paths["demucs"]))

    candidates.extend(
        [
            RUNTIME_DIR / "venv" / "Scripts" / "demucs.exe",
            RUNTIME_DIR / "venv" / "Scripts" / "demucs",
        ]
    )
    return candidates


def _ytdlp_candidates() -> list[Path]:
    paths = _runtime_paths()
    candidates: list[Path] = []
    if paths.get("ytdlp"):
        candidates.append(Path(paths["ytdlp"]))

    candidates.extend(
        [
            RUNTIME_DIR / "venv" / "Scripts" / "yt-dlp.exe",
            RUNTIME_DIR / "venv" / "Scripts" / "yt_dlp.exe",
        ]
    )
    return candidates


def _imageio_ffmpeg_candidate() -> Path | None:
    python_path = RUNTIME_DIR / "venv" / "Scripts" / "python.exe"
    if not python_path.exists():
        return None

    try:
        completed = subprocess.run(
            [
                str(python_path),
                "-c",
                "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=runtime_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    value = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    if not value:
        return None

    path = Path(value)
    if path.exists():
        return path
    return None


def _demucs_python_path(demucs_path: Path) -> Path | None:
    scripts_dir = demucs_path.parent
    python_path = scripts_dir / "python.exe"
    if python_path.exists():
        return python_path
    return None


def _normalize_time_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = normalized.encode("ascii", errors="ignore").decode("ascii")
    normalized = normalized.lower().strip()
    return normalized.replace(",", ".")


def _parse_time_tokens(normalized: str, *, original: str) -> float:
    total_seconds = 0.0
    position = 0
    matched = False
    for match in TIME_TOKEN_PATTERN.finditer(normalized):
        between = normalized[position:match.start()]
        if between.strip():
            raise ToolError(f"Mốc thời gian không hợp lệ: {original}")

        value = float(match.group("value"))
        unit = match.group("unit")
        total_seconds += value * _unit_multiplier(unit)
        position = match.end()
        matched = True

    if not matched or normalized[position:].strip():
        raise ToolError(f"Mốc thời gian không hợp lệ: {original}")
    return total_seconds


def _unit_multiplier(unit: str) -> float:
    if unit in {"h", "hr", "hrs", "hour", "hours", "gio"}:
        return 3600.0
    if unit in {"m", "min", "mins", "minute", "minutes", "ph", "phut"}:
        return 60.0
    return 1.0
