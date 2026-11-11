from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, TypeVar

import demucs.apply as demucs_apply
import lameenc
import numpy as np
import torch
from demucs.apply import BagOfModels, apply_model
from demucs.htdemucs import HTDemucs
from demucs.pretrained import get_model

T = TypeVar("T")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trình chạy Demucs bằng mã Python, giải mã audio qua FFmpeg.")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--vocals-output", type=Path)
    parser.add_argument("--mode", choices=("music", "vocals", "both"), default="music")
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--jobs", type=int, default=0)
    parser.add_argument("--bitrate", type=int, default=320)
    return parser


def main() -> int:
    _configure_console_streams()
    args = build_parser().parse_args()
    if args.mode in {"vocals", "both"} and args.vocals_output is None:
        raise SystemExit("Thiếu --vocals-output để lưu phần giọng hát/lời nói.")
    if args.vocals_output is not None and args.vocals_output == args.output:
        raise SystemExit("Hai file đầu ra phải khác tên.")

    emit_status("Đang nạp model...")
    model = get_model(args.model)
    max_allowed_segment = float("inf")
    if isinstance(model, HTDemucs):
        max_allowed_segment = float(model.segment)
    elif isinstance(model, BagOfModels):
        max_allowed_segment = model.max_allowed_segment

    model.cpu()
    model.eval()

    emit_status("Đang đọc audio...")
    wav = load_audio_ffmpeg(
        ffmpeg_path=args.ffmpeg,
        input_path=args.input,
        samplerate=model.samplerate,
        channels=model.audio_channels,
    )

    ref = wav.mean(0)
    ref_std = ref.std()
    if float(ref_std) <= 1e-8:
        ref_std = torch.tensor(1.0, dtype=wav.dtype)

    wav = wav - ref.mean()
    wav = wav / ref_std
    device = "cuda" if torch.cuda.is_available() else "cpu"
    emit_status("Đang tách giọng hát và nhạc nền...")
    emit_progress(0)
    original_tqdm = demucs_apply.tqdm.tqdm
    demucs_apply.tqdm.tqdm = progress_iterator_factory
    try:
        sources = apply_model(
            model,
            wav[None],
            device=device,
            shifts=1,
            split=True,
            overlap=0.25,
            progress=True,
            num_workers=args.jobs,
            segment=max_allowed_segment if max_allowed_segment != float("inf") else None,
        )[0]
    finally:
        demucs_apply.tqdm.tqdm = original_tqdm
    sources *= ref_std
    sources += ref.mean()

    try:
        vocals_index = model.sources.index("vocals")
    except ValueError as exc:
        raise SystemExit(f"Model {args.model} không có stem vocals.") from exc

    if args.mode in {"music", "both"}:
        no_vocals = torch.zeros_like(sources[vocals_index])
        for index, source in enumerate(sources):
            if index != vocals_index:
                no_vocals += source
        emit_status("Đang lưu MP3 nhạc không lời...")
        save_mp3(args.output, no_vocals, samplerate=model.samplerate, bitrate=args.bitrate)
        del no_vocals

    if args.mode in {"vocals", "both"}:
        emit_status("Đang lưu MP3 chỉ có lời...")
        save_mp3(args.vocals_output, sources[vocals_index], samplerate=model.samplerate, bitrate=args.bitrate)

    emit_progress(100)
    return 0


def load_audio_ffmpeg(
    *,
    ffmpeg_path: str,
    input_path: Path,
    samplerate: int,
    channels: int,
) -> torch.Tensor:
    command = [
        ffmpeg_path,
        "-i",
        str(input_path),
        "-vn",
        "-f",
        "f32le",
        "-ac",
        str(channels),
        "-ar",
        str(samplerate),
        "-hide_banner",
        "-loglevel",
        "error",
        "pipe:1",
    ]
    completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise SystemExit(f"FFmpeg không đọc được file đầu vào: {stderr.strip()}")

    data = np.frombuffer(completed.stdout, dtype=np.float32)
    if data.size == 0:
        raise SystemExit("FFmpeg trả về audio rỗng.")
    if data.size % channels != 0:
        raise SystemExit("FFmpeg trả về buffer audio không hợp lệ.")

    array = data.reshape(-1, channels).T.copy()
    return torch.from_numpy(array)


def save_mp3(path: Path, wav_tensor: torch.Tensor, *, samplerate: int, bitrate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    channels, _ = wav_tensor.shape
    pcm = wav_tensor.detach().cpu().clamp(-1, 1).transpose(0, 1).numpy()
    pcm_i16 = (pcm * 32767.0).astype(np.int16)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(samplerate)
    encoder.set_channels(channels)
    encoder.set_quality(2)
    encoder.silence()

    mp3_data = encoder.encode(pcm_i16.tobytes())
    mp3_data += encoder.flush()
    path.write_bytes(mp3_data)


def emit_status(message: str) -> None:
    print(f"STATUS: {message}", flush=True)


def emit_progress(percent: int) -> None:
    bounded = max(0, min(100, percent))
    print(f"PROGRESS: {bounded}", flush=True)


def _configure_console_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except OSError:
                pass


def progress_iterator_factory(iterable: Iterable[T], **_: object) -> Iterator[T]:
    items = list(iterable)
    total = len(items)
    if total == 0:
        emit_progress(95)
        return iter(())
    return ProgressIterator(items)


class ProgressIterator(Iterator[T]):
    def __init__(self, items: list[T]) -> None:
        self._items = items
        self._index = 0
        self._total = len(items)
        self._last_percent = -1

    def __iter__(self) -> "ProgressIterator[T]":
        return self

    def __next__(self) -> T:
        if self._index >= self._total:
            raise StopIteration

        item = self._items[self._index]
        self._index += 1
        percent = 5 + int((self._index * 90) / self._total)
        if percent != self._last_percent:
            emit_progress(percent)
            self._last_percent = percent
        return item


if __name__ == "__main__":
    raise SystemExit(main())
