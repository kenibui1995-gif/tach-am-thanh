from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from tach_am_thanh import commands as command_module
from tach_am_thanh.commands import (
    PROXY_ENV_NAMES,
    PROJECT_ROOT,
    ToolError,
    build_audio_segment_command,
    build_demucs_command,
    build_demucs_worker_command,
    build_extract_audio_command,
    build_karaoke_command,
    build_replace_audio_command,
    build_segment_output_path,
    build_ytdlp_download_command,
    format_duration_display,
    is_youtube_url,
    is_url_source,
    is_video_file,
    normalize_media_url,
    normalize_segment_end_times,
    parse_duration_output,
    parse_segment_end_times,
    parse_time_value,
    safe_console_text,
    sanitize_filename_component,
    runtime_env,
    should_bypass_proxy_env,
    summarize_command_failure,
    _remap_runtime_path,
)


class CommandBuilderTests(unittest.TestCase):
    def test_replace_audio_command_matches_gist_flow(self) -> None:
        command = build_replace_audio_command(
            "ffmpeg",
            Path("video_dau_vao.mp4"),
            Path("audio_dau_vao.mp3"),
            Path("video_dau_ra.mp4"),
        )

        self.assertEqual(command[:7], ["ffmpeg", "-i", "video_dau_vao.mp4", "-i", "audio_dau_vao.mp3", "-map", "0:v:0"])
        self.assertIn("1:a:0", command)
        self.assertIn("-c:v", command)
        self.assertIn("copy", command)
        self.assertEqual(command[-1], "video_dau_ra.mp4")

    def test_demucs_command_uses_two_stems_vocals(self) -> None:
        command = build_demucs_command(
            "demucs",
            Path("song.wav"),
            Path("outputs"),
            model="htdemucs",
            jobs=2,
        )

        self.assertEqual(
            command,
            [
                "demucs",
                "--two-stems=vocals",
                "-n",
                "htdemucs",
                "-o",
                "outputs",
                "-j",
                "2",
                "song.wav",
            ],
        )

    def test_demucs_worker_command_outputs_single_mp3(self) -> None:
        command = build_demucs_worker_command(
            "F:/runtime/venv/Scripts/python.exe",
            "F:/runtime/ffmpeg.exe",
            Path("song.wav"),
            Path("outputs/song_khong_loi.mp3"),
            model="htdemucs",
            jobs=2,
        )

        self.assertEqual(
            command,
            [
                "F:/runtime/venv/Scripts/python.exe",
                str(PROJECT_ROOT / "tach_am_thanh" / "demucs_worker.py"),
                "--ffmpeg",
                "F:/runtime/ffmpeg.exe",
                "--input",
                "song.wav",
                "--output",
                str(Path("outputs") / "song_khong_loi.mp3"),
                "--model",
                "htdemucs",
                "--bitrate",
                "320",
                "--jobs",
                "2",
            ],
        )

    def test_worker_command_supports_all_export_modes(self) -> None:
        base = (str(PROJECT_ROOT / "tach_am_thanh" / "demucs_worker.py"))
        for mode in ("music", "vocals", "both"):
            with self.subTest(mode=mode):
                command = build_demucs_worker_command(
                    "python", "ffmpeg", Path("song.wav"), Path("song_khong_loi.mp3"),
                    mode=mode,
                    vocals_output_path=Path("song_chi_loi.mp3") if mode != "music" else None,
                )
                self.assertEqual(command[1], base)
                if mode == "music":
                    self.assertNotIn("--mode", command)
                    self.assertNotIn("--vocals-output", command)
                else:
                    self.assertEqual(command[command.index("--mode") + 1], mode)
                    self.assertEqual(command[command.index("--vocals-output") + 1], "song_chi_loi.mp3")

        with self.assertRaises(ToolError):
            build_demucs_worker_command("python", "ffmpeg", Path("song.wav"), Path("x.mp3"), mode="both")
        with self.assertRaises(ToolError):
            build_demucs_worker_command("python", "ffmpeg", Path("song.wav"), Path("x.mp3"), mode="invalid")

    def test_modes_create_only_requested_files_and_split_both(self) -> None:
        with TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            source = temp_root / "song.wav"
            source.write_bytes(b"sample input for mocked Demucs test")
            invocations: list[list[str]] = []

            def fake_run(command, dry_run=False):
                command = list(command)
                invocations.append(command)
                if "demucs_worker.py" in command[1]:
                    mode = command[command.index("--mode") + 1] if "--mode" in command else "music"
                    if mode in {"music", "both"}:
                        Path(command[command.index("--output") + 1]).write_bytes(b"ID3-MUSIC")
                    if mode in {"vocals", "both"}:
                        Path(command[command.index("--vocals-output") + 1]).write_bytes(b"ID3-VOCALS")
                else:
                    # Simulate FFmpeg cutting an audio segment.
                    Path(command[-1]).write_bytes(b"ID3-SEGMENT")
                return command_module.CommandResult(command=command, returncode=0)

            with (
                mock.patch.object(command_module, "resolve_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(command_module, "resolve_demucs", return_value="demucs"),
                mock.patch.object(command_module, "_demucs_python_path", return_value=Path("python")),
                mock.patch.object(command_module, "probe_media_duration", return_value=12.0),
                mock.patch.object(command_module, "RUNTIME_DIR", temp_root / "runtime"),
                mock.patch.object(command_module, "run_command", side_effect=fake_run),
            ):
                for mode in ("music", "vocals", "both"):
                    with self.subTest(mode=mode):
                        invocations.clear()
                        destination = temp_root / mode
                        results = command_module.separate_with_demucs(
                            ffmpeg="auto", demucs="auto", ytdlp="auto",
                            input_source=str(source), output_dir=destination,
                            mode=mode, split_count=2 if mode == "both" else None,
                            segment_ends=["6"] if mode == "both" else (),
                        )
                        expected_files = {
                            "music": {"song_khong_loi.mp3"},
                            "vocals": {"song_chi_loi.mp3"},
                            "both": {
                                "song_khong_loi.mp3", "song_chi_loi.mp3",
                                "song_khong_loi_1.mp3", "song_khong_loi_2.mp3",
                                "song_chi_loi_1.mp3", "song_chi_loi_2.mp3",
                            },
                        }[mode]
                        self.assertEqual({p.name for p in destination.iterdir()}, expected_files)
                        self.assertEqual(len([cmd for cmd in invocations if "demucs_worker.py" in cmd[1]]), 1)
                        self.assertEqual(len(results), 5 if mode == "both" else 1)

                with self.assertRaises(ToolError):
                    command_module.separate_with_demucs(
                        ffmpeg="auto", demucs="auto", ytdlp="auto",
                        input_source=str(source), output_dir=temp_root / "invalid",
                        mode="other",
                    )

    def test_ytdlp_download_command_targets_audio_only_download(self) -> None:
        command = build_ytdlp_download_command(
            "yt-dlp",
            "https://www.youtube.com/watch?v=abc123",
            Path("downloads"),
        )

        self.assertEqual(
            command,
            [
                "yt-dlp",
                "--quiet",
                "--no-warnings",
                "--no-playlist",
                "--windows-filenames",
                "-f",
                "bestaudio/best",
                "--print",
                "after_move:filepath",
                "-P",
                "downloads",
                "-o",
                "%(title)s [%(id)s].%(ext)s",
                "https://www.youtube.com/watch?v=abc123",
            ],
        )

    def test_ytdlp_download_command_can_enable_youtube_ejs_and_ffmpeg(self) -> None:
        command = build_ytdlp_download_command(
            "yt-dlp",
            "https://www.youtube.com/watch?v=abc123",
            Path("downloads"),
            ffmpeg_location="F:/ffmpeg.exe",
            use_youtube_ejs=True,
        )

        self.assertIn("--ffmpeg-location", command)
        self.assertIn("F:/ffmpeg.exe", command)
        self.assertIn("--js-runtimes", command)
        self.assertIn("node", command)
        self.assertIn("--remote-components", command)
        self.assertIn("ejs:github", command)

    def test_extract_audio_command_outputs_stereo_wav_settings(self) -> None:
        command = build_extract_audio_command(
            "ffmpeg",
            Path("clip.mp4"),
            Path("clip.wav"),
        )

        self.assertEqual(command, ["ffmpeg", "-i", "clip.mp4", "-vn", "-ac", "2", "-ar", "44100", "-y", "clip.wav"])

    def test_karaoke_command_uses_center_cancel_filter(self) -> None:
        command = build_karaoke_command("ffmpeg", Path("song.mp3"), Path("karaoke.wav"))

        self.assertEqual(command[0:3], ["ffmpeg", "-i", "song.mp3"])
        self.assertIn("pan=stereo|c0=c0-c1|c1=c1-c0", command)
        self.assertEqual(command[-1], "karaoke.wav")

    def test_video_extension_detection(self) -> None:
        self.assertTrue(is_video_file(Path("input.MP4")))
        self.assertFalse(is_video_file(Path("input.wav")))

    def test_url_source_detection(self) -> None:
        self.assertTrue(is_url_source("https://www.youtube.com/watch?v=test"))
        self.assertFalse(is_url_source("C:/Users/PC/song.mp3"))

    def test_is_youtube_url_matches_watch_shorts_and_youtu_be(self) -> None:
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=abc123"))
        self.assertTrue(is_youtube_url("https://www.youtube.com/shorts/abc123"))
        self.assertTrue(is_youtube_url("https://youtu.be/abc123"))
        self.assertFalse(is_youtube_url("https://example.com/demo.mp4"))

    def test_normalize_media_url_strips_radio_playlist_params(self) -> None:
        self.assertEqual(
            normalize_media_url("https://www.youtube.com/watch?v=abc123&list=RDabc123&start_radio=1"),
            "https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(
            normalize_media_url("https://www.youtube.com/shorts/abc123?feature=share"),
            "https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(
            normalize_media_url("https://youtu.be/abc123?si=test"),
            "https://www.youtube.com/watch?v=abc123",
        )

    def test_filename_sanitization_preserves_unicode_but_removes_invalid_chars(self) -> None:
        value = sanitize_filename_component('Con Cò: Bé? <test> * / demo')
        self.assertEqual(value, "Con Cò_ Bé_ _test_ _ _ demo")

    def test_safe_console_text_escapes_unicode_for_cp1252(self) -> None:
        value = "C:/Users/PC/Downloads/Di duong nao \u266a.mp3"
        safe_value = safe_console_text(value, "cp1252")

        safe_value.encode("cp1252")
        self.assertIn("\\u266a", safe_value)

    def test_parse_time_value_supports_seconds_minutes_and_colon_formats(self) -> None:
        self.assertEqual(parse_time_value("30s"), 30.0)
        self.assertEqual(parse_time_value("1 phut"), 60.0)
        self.assertEqual(parse_time_value("1:30"), 90.0)
        self.assertEqual(parse_time_value("01:02:03"), 3723.0)

    def test_parse_segment_end_times_returns_increasing_seconds(self) -> None:
        values = parse_segment_end_times(["30s", "1 phut", "1:30"], expected_count=3)
        self.assertEqual(values, [30.0, 60.0, 90.0])

    def test_parse_segment_end_times_rejects_invalid_sequences(self) -> None:
        with self.assertRaises(ToolError):
            parse_segment_end_times(["30s", "20s"], expected_count=2)

        with self.assertRaises(ToolError):
            parse_segment_end_times(["30s"], expected_count=2)

    def test_normalize_segment_end_times_appends_last_segment_to_end(self) -> None:
        values = normalize_segment_end_times(["30", "60", "70", "90"], split_count=5, total_duration=200.0)
        self.assertEqual(values, [30.0, 60.0, 70.0, 90.0, 200.0])

    def test_normalize_segment_end_times_rejects_boundary_past_total_duration(self) -> None:
        with self.assertRaises(ToolError):
            normalize_segment_end_times(["30", "210"], split_count=3, total_duration=200.0)

    def test_parse_duration_output_reads_ffmpeg_duration(self) -> None:
        output = "Input #0, mp3, from 'song.mp3':\n  Duration: 00:03:20.50, start: 0.000000, bitrate: 320 kb/s\n"
        self.assertEqual(parse_duration_output(output), 200.5)

    def test_format_duration_display_uses_mm_ss_for_short_media(self) -> None:
        self.assertEqual(format_duration_display(200.0), "03:20")
        self.assertEqual(format_duration_display(3723.0), "01:02:03")

    def test_audio_segment_command_uses_start_end_and_mp3_encoder(self) -> None:
        command = build_audio_segment_command(
            "ffmpeg",
            Path("song_khong_loi.mp3"),
            Path("song_khong_loi_1.mp3"),
            start_seconds=30.0,
            end_seconds=60.0,
        )

        self.assertEqual(
            command,
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "song_khong_loi.mp3",
                "-ss",
                "00:00:30.000",
                "-to",
                "00:01:00.000",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-y",
                "song_khong_loi_1.mp3",
            ],
        )

    def test_build_segment_output_path_numbers_each_segment(self) -> None:
        output = build_segment_output_path(Path("outputs/song_khong_loi.mp3"), 5)
        self.assertEqual(output, Path("outputs/song_khong_loi_5.mp3"))

    def test_summarize_command_failure_maps_youtube_unavailable_to_vietnamese(self) -> None:
        message = summarize_command_failure(
            "ERROR: [youtube] MUN4Ihu42J0: Video unavailable\n",
            1,
        )

        self.assertIn("không khả dụng", message)
        self.assertNotIn("Video unavailable", message)

    def test_summarize_command_failure_maps_youtube_not_available_phrase_to_vietnamese(self) -> None:
        message = summarize_command_failure(
            "ERROR: [youtube] xiWqWA9QLhk: This video is not available\n",
            1,
        )

        self.assertIn("không khả dụng", message)
        self.assertNotIn("This video is not available", message)

    def test_should_bypass_proxy_env_only_for_dead_loopback_proxy(self) -> None:
        self.assertTrue(should_bypass_proxy_env("http://127.0.0.1:9"))
        self.assertTrue(should_bypass_proxy_env("localhost:9"))
        self.assertFalse(should_bypass_proxy_env("http://127.0.0.1:7890"))
        self.assertFalse(should_bypass_proxy_env("http://10.0.0.1:8080"))

    def test_runtime_env_removes_dead_loopback_proxy_settings(self) -> None:
        proxy_env = {name: "http://127.0.0.1:9" for name in PROXY_ENV_NAMES}
        with mock.patch.dict("os.environ", proxy_env, clear=False):
            env = runtime_env()

        for name in PROXY_ENV_NAMES:
            self.assertNotIn(name, env)

    def test_remap_runtime_path_moves_absolute_runtime_paths_to_current_runtime(self) -> None:
        remapped = _remap_runtime_path(
            r"F:\old\tool\.runtime\venv\Scripts\demucs.exe",
            r"F:\old\tool\.runtime",
            r"F:\old\tool\dist\OnixTachLoi\.runtime",
        )

        self.assertEqual(
            remapped,
            r"F:\old\tool\dist\OnixTachLoi\.runtime\venv\Scripts\demucs.exe",
        )


if __name__ == "__main__":
    unittest.main()
