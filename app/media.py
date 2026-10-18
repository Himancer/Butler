from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import textwrap
import time
import wave
import struct
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Callable

from app.core import ButlerError, MAX_DURATION, MAX_UPLOAD_BYTES, Store, validate_plan
from app.storage import LocalMediaObjectStore, MediaObjectStore, MediaStorageError


def executable(name: str) -> str | None:
    configured = os.environ.get(f"BUTLER_{name.upper()}_PATH")
    if configured:
        path = Path(configured)
        return str(path.resolve()) if path.is_file() else None
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path.home() / "tools" / "ffmpeg-9.0.2-essentials_build" / "bin" / f"{name}.exe",
        Path.home() / "tools" / "ffmpeg" / "bin" / f"{name}.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            for exe in winget_pkgs.glob(f"**/{name}.exe"):
                if exe.is_file():
                    return str(exe.resolve())
    return None


def subtitle_clock(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{fraction:03}"


def manual_subtitles(transcript: str, duration: float) -> list[dict[str, Any]]:
    cleaned = re.sub(r"<[^>]*>", "", transcript).replace("-->", "-").strip()
    phrases = [part for line in cleaned.splitlines() for part in textwrap.wrap(line.strip(), width=44, break_long_words=False, break_on_hyphens=False)]
    if not phrases:
        raise ButlerError("EMPTY_TRANSCRIPT", "Enter a transcript or disable subtitles.")
    if len(phrases) > 120:
        raise ButlerError("TRANSCRIPT_TOO_LONG", "Use at most 120 subtitle lines.")
    interval = duration / len(phrases)
    return [{"start": index * interval, "end": (index + 1) * interval, "text": phrase} for index, phrase in enumerate(phrases)]


def write_srt(segments: list[dict[str, Any]], duration: float) -> str:
    blocks = []
    previous_start = 0.0
    for segment in segments:
        start, end = segment["start"], segment["end"]
        if not all(type(value) in (int, float) and math.isfinite(value) for value in (start, end)) or not previous_start <= start < end <= duration + 0.1:
            raise ButlerError("INVALID_TRANSCRIPT_TIMING", "Transcript timestamps do not match the selected clip.")
        previous_start = start
        text = re.sub(r"<[^>]*>", "", str(segment["text"])).replace("-->", "-").strip()
        if text:
            blocks.append(f"{len(blocks) + 1}\n{subtitle_clock(start)} --> {subtitle_clock(min(end, duration))}\n{text}\n")
    if not blocks:
        raise ButlerError("NO_SPEECH", "No speech was detected. Provide a transcript or disable subtitles.")
    return "\n".join(blocks)


class MediaTools:
    def __init__(self) -> None:
        self.ffmpeg = executable("ffmpeg")
        self.ffprobe = executable("ffprobe")
        font_candidates = [
            os.environ.get("BUTLER_FONT_PATH", ""),
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        self.font = next((Path(candidate) for candidate in font_candidates if candidate and Path(candidate).is_file()), None)

    def status(self) -> dict[str, Any]:
        return {"ffmpeg": bool(self.ffmpeg), "ffprobe": bool(self.ffprobe), "font": bool(self.font), "ready": bool(self.ffmpeg and self.ffprobe and self.font)}

    def probe(self, path: Path) -> dict[str, Any]:
        if not self.ffprobe:
            raise ButlerError("TOOLS_REQUIRED", "FFprobe is not installed. Configure an approved FFmpeg distribution and restart Butler.", 503)
        try:
            result = subprocess.run(
                [self.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams", "-of", "json", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=False,
            )
            if result.returncode != 0:
                raise ButlerError("INVALID_MEDIA", "This file cannot be decoded as supported video.")
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
            raise ButlerError("INVALID_MEDIA", "Video inspection did not finish successfully.") from error

    def inspect(self, path: Path) -> dict[str, Any]:
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ButlerError("UPLOAD_TOO_LARGE", "The clip exceeds the 50 MB limit.", 413)
        with path.open("rb") as source:
            header = source.read(32)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise ButlerError("INVALID_MEDIA", "The file is not an MP4 container.")
        metadata = self.probe(path)
        video = next((stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")), None)
        if not video:
            raise ButlerError("NO_VIDEO", "The file has no video stream.")
        try:
            duration = float(metadata.get("format", {}).get("duration", video.get("duration", 0)))
            width, height = int(video.get("width", 0)), int(video.get("height", 0))
        except (ValueError, TypeError) as error:
            raise ButlerError("INVALID_MEDIA", "Video metadata could not be validated.") from error
        if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
            raise ButlerError("DURATION_LIMIT", "Clips must be between 0 and 60 seconds long.")
        if min(width, height) <= 0 or max(width, height) > 3840 or min(width, height) > 2160:
            raise ButlerError("DIMENSION_LIMIT", "Video dimensions must fit within 3840 x 2160, in either orientation.")
        if video.get("codec_name") not in ("h264", "hevc", "mpeg4", "av1", "vp9"):
            raise ButlerError("UNSUPPORTED_CODEC", "The video codec is not supported by this prototype.")
        return {"size_bytes": size, "duration_seconds": duration, "width": width, "height": height, "has_audio": any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", [])), "detected_type": "video/mp4", "codec": video["codec_name"]}

    def filters(self, plan: dict[str, Any], subtitles: bool) -> str:
        if plan["fit_mode"] == "center_crop":
            filters = ["scale=720:1280:force_original_aspect_ratio=increase", "crop=720:1280", "setsar=1"]
        else:
            filters = ["scale=720:1280:force_original_aspect_ratio=decrease", "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1"]
        if plan["title_text"]:
            filters.append("drawtext=fontfile=font.ttf:textfile=title.txt:expansion=none:fontsize=38:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=16:x=(w-text_w)/2:y=60")
        if subtitles:
            filters.append("subtitles=subtitles.srt:fontsdir=.:force_style='FontName=Arial,FontSize=20,Outline=2,MarginV=70'")
        return ",".join(filters)

    def run(self, arguments: list[str], workdir: Path, timeout: int) -> None:
        if not self.ffmpeg:
            raise ButlerError("TOOLS_REQUIRED", "FFmpeg is not installed. Configure an approved distribution and restart Butler.", 503)
        try:
            result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe", "-y", *arguments], cwd=workdir, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise ButlerError("RENDER_TIMEOUT", "The render exceeded its time limit. A single retry is available.", 503) from error
        except OSError as error:
            raise ButlerError("TOOLS_REQUIRED", "The media tools could not be started.", 503) from error
        if result.returncode != 0:
            raise ButlerError("RENDER_FAILED", "FFmpeg could not process this edit. Check codec and font support in the configured distribution.")

    def scene_changes(self, path: Path) -> list[float]:
        if not self.ffmpeg:
            return []
        try:
            result = subprocess.run(
                [self.ffmpeg, "-nostdin", "-hide_banner", "-protocol_whitelist", "file,pipe", "-i", str(path), "-vf", "select='gt(scene,0.16)',showinfo", "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=45, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        return [float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr) if value]

    def audio_activity(self, path: Path, duration: float) -> list[dict[str, float]]:
        """Return non-silent audio windows as local cues (not speech transcription)."""
        if not self.ffmpeg:
            return []
        try:
            result = subprocess.run(
                [self.ffmpeg, "-nostdin", "-hide_banner", "-protocol_whitelist", "file,pipe", "-i", str(path), "-vn", "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"],
                capture_output=True, text=True, timeout=45, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        windows: list[dict[str, float]] = []
        cursor = 0.0
        for line in result.stderr.splitlines():
            start = re.search(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", line)
            end = re.search(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)", line)
            if start:
                silence_start = min(duration, float(start.group(1)))
                if silence_start > cursor:
                    windows.append({"start": cursor, "end": silence_start})
            if end:
                cursor = min(duration, float(end.group(1)))
        if cursor < duration:
            windows.append({"start": cursor, "end": duration})
        return windows


def highlight_intervals(duration: float, target: float, scene_times: list[float], speech: list[dict[str, Any]] | None = None, moments: list[float] | None = None, focus: str = "balanced") -> list[tuple[float, float]]:
    """Choose four chronological excerpts using scene changes and speech activity."""
    if target >= duration - 1 / 30:
        return [(0.0, duration)]
    count = 4
    piece = target / count
    speech = speech or []
    candidates = [round(index * 0.25, 2) for index in range(max(1, int((duration - piece) * 4) + 1))]
    moments = moments or []
    scene_weight, speech_weight = {"speech": (1.5, 8.0), "visual": (8.0, 1.5)}.get(focus, (3.0, 2.0))
    chosen: list[float] = []
    for _ in range(count):
        best_start, best_score = 0.0, float("-inf")
        for start in candidates:
            end = start + piece
            if any(not (end <= prior or start >= prior + piece) for prior in chosen):
                continue
            scene_score = sum(start <= point <= end for point in scene_times)
            speech_score = sum(max(0.0, min(end, segment["end"]) - max(start, segment["start"])) for segment in speech)
            visual_score = sum(max(0.0, 1.0 - abs((start + end) / 2 - point) / max(piece, 1.0)) for point in moments)
            spacing_score = min((abs(start - prior) for prior in chosen), default=duration)
            score = visual_score * 8.0 + scene_score * scene_weight + min(speech_score, piece) * speech_weight + spacing_score * 0.05
            if score > best_score:
                best_start, best_score = start, score
        chosen.append(best_start)
    return [(start, start + piece) for start in sorted(chosen)]


def hybrid_intervals(
    duration: float,
    target: float,
    scene_times: list[float],
    speech: list[dict[str, Any]] | None = None,
    moments: list[float] | None = None,
    focus: str = "balanced",
) -> tuple[list[tuple[float, float]], float]:
    """Select excerpts and playback speed that produce the requested output length."""
    speed = min(1.25, duration / target)
    selected_duration = target * speed
    selected = highlight_intervals(duration, selected_duration, scene_times, speech, moments, focus)
    if len(selected) == 1 and selected[0][0] == 0 and selected[0][1] >= duration - 1 / 30:
        return selected, speed
    piece = selected_duration / 4
    return [(start, start + piece) for start, _ in selected], speed


def make_music(path: Path, duration: float) -> None:
    """Create a gentle, low-volume instrumental bed without requiring external media."""
    sample_rate = 22050
    count = int(sample_rate * duration)
    chords = [(261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(count):
            t = index / sample_rate
            chord = chords[int(t / 3) % len(chords)]
            envelope = min(1.0, t * 5, max(0.0, (duration - t) * 3))
            value = sum(math.sin(2 * math.pi * frequency * t) for frequency in chord) / len(chord)
            frames.extend(struct.pack("<h", int(32767 * 0.22 * envelope * value)))
        output.writeframes(frames)


def map_speech_to_output(segments: list[dict[str, Any]], intervals: list[tuple[float, float]], speed: float) -> list[dict[str, Any]]:
    mapped = []
    output_offset = 0.0
    for start, end in intervals:
        for segment in segments:
            left, right = max(start, segment["start"]), min(end, segment["end"])
            if right > left:
                mapped.append({"start": output_offset + (left - start) / speed, "end": output_offset + (right - start) / speed, "text": segment["text"]})
        output_offset += (end - start) / speed
    return mapped


class MediaEngine:
    def __init__(self, store: Store, root: Path, tools: MediaTools, transcriber: Callable[[Path, float], list[dict[str, Any]]] | None = None, moment_selector: Callable[[Path, float], list[float]] | None = None, objects: MediaObjectStore | None = None) -> None:
        self.store = store
        self.root = root
        self.tools = tools
        self.objects = objects or LocalMediaObjectStore(root)
        self.transcriber = transcriber
        self.moment_selector = moment_selector
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="butler-media")
        self.slots = BoundedSemaphore(4)
        self.export_lock = Lock()

    def asset_path(self, asset: dict[str, Any]) -> Path:
        return self.root / asset["session_id"] / "assets" / asset["id"] / "source.mp4"

    def attempt_path(self, job: dict[str, Any]) -> Path:
        return self.root / job["session_id"] / "jobs" / job["id"] / f"attempt-{job['attempt_count']}"

    @staticmethod
    def asset_key(asset: dict[str, Any]) -> str:
        return f"{asset['session_id']}/assets/{asset['id']}/source.mp4"

    @staticmethod
    def artifact_key(job: dict[str, Any], filename: str) -> str:
        return f"{job['session_id']}/jobs/{job['id']}/attempt-{job['attempt_count']}/{filename}"

    def materialize_asset(self, asset: dict[str, Any]) -> Path:
        path = self.asset_path(asset)
        if not path.is_file():
            try:
                self.objects.download(asset.get("object_key") or self.asset_key(asset), path)
            except MediaStorageError as error:
                raise ButlerError("MEDIA_STORAGE_UNAVAILABLE", "The uploaded clip could not be retrieved from media storage. Retry inspection when storage is available.", 503) from error
        return path

    def materialize_artifact(self, job: dict[str, Any], filename: str) -> Path:
        path = self.attempt_path(job) / filename
        if not path.is_file():
            key = job.get("artifact_objects", {}).get(filename) or self.artifact_key(job, filename)
            try:
                self.objects.download(key, path)
            except MediaStorageError as error:
                raise ButlerError("MEDIA_STORAGE_UNAVAILABLE", "The export could not be retrieved from media storage. Try again when storage is available.", 503) from error
        return path

    def persist_asset(self, asset: dict[str, Any], path: Path | None = None) -> None:
        key = self.asset_key(asset)
        self.objects.upload(key, path or self.asset_path(asset))
        asset["object_key"] = key

    def delete_session(self, session_id: str) -> None:
        self.objects.delete_prefix(session_id)
        shutil.rmtree(self.root / session_id, ignore_errors=True)

    def submit(self, function: Callable[..., None], *arguments: Any) -> bool:
        if not self.slots.acquire(blocking=False):
            return False
        try:
            future = self.executor.submit(function, *arguments)
            future.add_done_callback(lambda _: self.slots.release())
            return True
        except RuntimeError:
            self.slots.release()
            return False

    def validate_asset(self, asset: dict[str, Any]) -> None:
        try:
            metadata = self.tools.inspect(self.materialize_asset(asset))
            asset.update(metadata, validation_status="valid", error_code=None, error_message=None)
        except ButlerError as error:
            state = "needs_tools" if error.code == "TOOLS_REQUIRED" else "uploaded" if error.code == "MEDIA_STORAGE_UNAVAILABLE" else "invalid"
            asset.update(validation_status=state, error_code=error.code, error_message=error.message)
            if error.code not in ("TOOLS_REQUIRED", "MEDIA_STORAGE_UNAVAILABLE"):
                self.asset_path(asset).unlink(missing_ok=True)
                self.objects.delete_prefix(f"{asset['session_id']}/assets/{asset['id']}")
        except Exception:
            asset.update(validation_status="invalid", error_code="INSPECTION_FAILED", error_message="The clip could not be inspected.")
            self.asset_path(asset).unlink(missing_ok=True)
            self.objects.delete_prefix(f"{asset['session_id']}/assets/{asset['id']}")
        self.store.put("assets", asset)

    def dispatch(self, job: dict[str, Any]) -> None:
        if not self.submit(self.process, job["id"], job["session_id"]):
            self.store.update_job(job["id"], job["session_id"], job["attempt_count"], {"status": "failed", "phase": "dispatch", "error_code": "DISPATCH_BUSY", "error_message": "The media queue is full. Retry this same job when a slot is available."}, expected_status="queued")

    def process(self, job_id: str, session_id: str) -> None:
        job = self.store.claim_job(job_id, session_id)
        if not job:
            return
        attempt = job["attempt_count"]
        directory = self.attempt_path(job)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            plan = self.store.get("plans", job["plan_id"], session_id)
            asset = self.store.get("assets", plan["asset_id"], session_id)
            if plan["status"] != "approved" or plan["version"] != job["plan_version"]:
                raise ButlerError("STALE_PLAN", "The approved plan does not match this job.", 409)
            asset_path = self.materialize_asset(asset)
            inspected = self.tools.inspect(asset_path)
            validate_plan({**asset, **inspected}, plan)
            shortening = bool(plan.get("shorten_duration_seconds"))
            duration = float(plan["shorten_duration_seconds"] if shortening else plan["trim_end_seconds"] - plan["trim_start_seconds"])
            if not self.tools.font:
                raise ButlerError("TOOLS_REQUIRED", "A subtitle/title font is missing. Configure BUTLER_FONT_PATH.", 503)
            shutil.copyfile(self.tools.font, directory / "font.ttf")
            (directory / "title.txt").write_text(textwrap.fill(plan["title_text"], width=28), encoding="utf-8")
            transcript_source = None
            scene_times: list[float] = []
            speech_segments: list[dict[str, Any]] = []
            audio_activity: list[dict[str, Any]] = []
            visual_moments: list[float] = []
            source_duration = float(inspected["duration_seconds"])
            if shortening:
                self.store.update_job(job_id, session_id, attempt, {"phase": "analyzing"})
                if plan["shorten_strategy"] != "uniform_speed":
                    scene_times = self.tools.scene_changes(asset_path)
                    if self.moment_selector:
                        self.tools.run(["-i", str(asset_path), "-vf", "fps=1,scale=192:108,tile=5x12", "-frames:v", "1", "-q:v", "4", "contact-sheet.jpg"], directory, 30)
                        visual_moments = self.moment_selector(directory / "contact-sheet.jpg", source_duration)
                if self.transcriber and inspected["has_audio"]:
                    self.tools.run(["-i", str(asset_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "analysis.wav"], directory, 40)
                    speech_segments = self.transcriber(directory / "analysis.wav", source_duration)
                    transcript_source = "google_speech_analysis"
                if plan.get("shorten_focus") == "speech" and inspected["has_audio"]:
                    audio_activity = speech_segments or self.tools.audio_activity(asset_path, source_duration)
                if plan["subtitles_enabled"] and shortening and not plan["transcript"] and not speech_segments:
                    raise ButlerError("NO_SPEECH", "No speech was detected for subtitles. Add a manual transcript or turn subtitles off.")
            if plan["subtitles_enabled"]:
                self.store.update_job(job_id, session_id, attempt, {"phase": "transcribing"})
                if plan["transcript"]:
                    segments = manual_subtitles(plan["transcript"], duration)
                    transcript_source = "manual_approximate_timing"
                elif self.transcriber:
                    if not speech_segments and not shortening:
                        self.tools.run(["-i", str(asset_path), "-ss", str(plan["trim_start_seconds"]), "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "audio.wav"], directory, 30)
                        speech_segments = self.transcriber(directory / "audio.wav", duration)
                        if not shortening:
                            segments = speech_segments
                    if shortening:
                        transcript_source = "google_speech"
                    transcript_source = "google_speech"
                    if shortening:
                        segments = speech_segments
                else:
                    raise ButlerError("TRANSCRIPT_REQUIRED", "Automatic transcription is not configured. Provide a manual transcript or disable subtitles.")
                if not shortening or plan["transcript"]:
                    (directory / "subtitles.srt").write_text(write_srt(segments, duration), encoding="utf-8")
            self.store.update_job(job_id, session_id, attempt, {"phase": "rendering"})
            args = ["-i", str(asset_path)]
            if shortening:
                target = duration
                strategy = plan["shorten_strategy"]
                intervals = [(0.0, source_duration)]
                speed = source_duration / target if strategy == "uniform_speed" else (min(1.25, source_duration / target) if strategy == "hybrid" else 1.0)
                if strategy == "smart_highlights":
                    intervals = highlight_intervals(source_duration, target, scene_times, audio_activity or speech_segments, visual_moments, plan.get("shorten_focus", "balanced"))
                elif strategy == "hybrid":
                    intervals, speed = hybrid_intervals(source_duration, target, scene_times, audio_activity or speech_segments, visual_moments, plan.get("shorten_focus", "balanced"))
                if plan["subtitles_enabled"] and not plan["transcript"] and speech_segments:
                    (directory / "subtitles.srt").write_text(write_srt(map_speech_to_output(speech_segments, intervals, speed), target), encoding="utf-8")
                if strategy == "uniform_speed":
                    ratio = source_duration / target
                    pts = 1 / ratio
                    audio_filters = []
                    remaining = ratio
                    while remaining > 2:
                        audio_filters.append("atempo=2")
                        remaining /= 2
                    audio_filters.append(f"atempo={remaining:.8f}")
                    graph = f"[0:v]setpts=(PTS-STARTPTS)*{pts:.10f},{self.tools.filters(plan, plan['subtitles_enabled'])}[v]"
                    if plan["shorten_audio"] != "mute" and inspected["has_audio"]:
                        graph += f";[0:a]asetpts=PTS-STARTPTS,{','.join(audio_filters)},apad=whole_dur={target}[a]"
                    args += ["-filter_complex", graph, "-map", "[v]"]
                    if plan["shorten_audio"] != "mute" and inspected["has_audio"]:
                        args += ["-map", "[a]"]
                else:
                    selections = "+".join(f"between(t,{start:.6f},{end:.6f})" for start, end in intervals)
                    video_timing = f"setpts=N/FRAME_RATE/TB/{speed:.2f}"
                    graph = f"[0:v]select='{selections}',{video_timing},{self.tools.filters(plan, plan['subtitles_enabled'])}[v]"
                    if plan["shorten_audio"] != "mute" and inspected["has_audio"]:
                        audio_timing = f",atempo={speed:.8f}" if strategy == "hybrid" else ""
                        graph += f";[0:a]aselect='{selections}',asetpts=N/SR/TB{audio_timing},apad=whole_dur={target}[a]"
                    args += ["-filter_complex", graph, "-map", "[v]"]
                    if plan["shorten_audio"] != "mute" and inspected["has_audio"]:
                        args += ["-map", "[a]"]
                if plan["shorten_audio"] == "mute":
                    args += ["-an"]
                elif plan["shorten_audio"] == "music":
                    make_music(directory / "music.wav", target)
                    # FFmpeg needs every input declared before output maps/filter outputs.
                    args[2:2] = ["-i", str(directory / "music.wav")]
                    if inspected["has_audio"]:
                        graph += f";[a]volume=0.8[original];[1:a]volume=0.22[music];[original][music]amix=inputs=2:duration=longest:dropout_transition=0,atrim=duration={target}[mix]"
                    else:
                        graph += f";[1:a]atrim=duration={target}[mix]"
                    args[args.index("-filter_complex") + 1] = graph
                    if inspected["has_audio"]:
                        args[args.index("[a]")] = "[mix]"
                    else:
                        args += ["-map", "[mix]"]
                args += ["-t", str(target)]
            else:
                args += ["-ss", str(plan["trim_start_seconds"]), "-t", str(duration), "-map", "0:v:0", "-map", "0:a:0?", "-vf", self.tools.filters(plan, plan["subtitles_enabled"])]
                if plan.get("shorten_audio") == "mute":
                    args += ["-an"]
            args += [
                "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "output.mp4",
            ]
            self.tools.run(args, directory, 120)
            self.store.update_job(job_id, session_id, attempt, {"phase": "verifying"})
            output = self.tools.probe(directory / "output.mp4")
            video = next((stream for stream in output.get("streams", []) if stream.get("codec_type") == "video"), {})
            output_duration = float(output.get("format", {}).get("duration", 0))
            duration_tolerance = (1 / 30) if plan.get("shorten_duration_seconds") else 0.5
            if (video.get("width"), video.get("height")) != (720, 1280) or not math.isfinite(output_duration) or abs(output_duration - duration) > duration_tolerance:
                raise ButlerError("OUTPUT_INVALID", "The rendered output did not pass playback validation.")
            required = ["output.mp4"] + (["subtitles.srt"] if plan["subtitles_enabled"] else [])
            if not all((directory / filename).is_file() and (directory / filename).stat().st_size for filename in required):
                raise ButlerError("OUTPUT_INVALID", "The render did not produce all required artifacts.")
            (directory / "caption.txt").write_text(self.caption_text(session_id, plan["asset_id"]), encoding="utf-8")
            output_keys = [*required, "caption.txt"]
            artifact_objects = {}
            for filename in output_keys:
                key = self.artifact_key(job, filename)
                self.objects.upload(key, directory / filename)
                artifact_objects[filename] = key
            self.store.update_job(job_id, session_id, attempt, {"status": "succeeded", "phase": "complete", "lease_until": None, "output_keys": output_keys, "artifact_objects": artifact_objects, "transcript_source": transcript_source, "completed_at": time.time()})
        except ButlerError as error:
            self.store.update_job(job_id, session_id, attempt, {"status": "failed", "phase": "failed", "lease_until": None, "error_code": error.code, "error_message": error.message})
            shutil.rmtree(directory, ignore_errors=True)
        except MediaStorageError as error:
            self.store.update_job(job_id, session_id, attempt, {"status": "failed", "phase": "failed", "lease_until": None, "error_code": "MEDIA_STORAGE_UNAVAILABLE", "error_message": "Media storage could not complete this render. Retry this same job when storage is available."})
            shutil.rmtree(directory, ignore_errors=True)
        except Exception:
            self.store.update_job(job_id, session_id, attempt, {"status": "failed", "phase": "failed", "lease_until": None, "error_code": "WORKER_FAILED", "error_message": "The worker stopped unexpectedly. No partial output has been published."})
            shutil.rmtree(directory, ignore_errors=True)

    def caption_text(self, session_id: str, asset_id: str) -> str:
        # Standalone shopping captions remain downloadable, but are never
        # silently attached to an unrelated video export.
        captions = [caption for caption in self.store.list("captions", session_id) if caption.get("asset_id") == asset_id]
        if not captions:
            return ""
        caption = captions[-1]
        return caption["text"].strip() + ("\n\n" + " ".join(caption["hashtags"]) if caption["hashtags"] else "") + "\n"

    def package(self, job: dict[str, Any]) -> list[str]:
        if job["status"] != "succeeded":
            raise ButlerError("JOB_NOT_FINISHED", "Downloads are available only after a verified render.", 409)
        directory = self.attempt_path(job)
        plan = self.store.get("plans", job["plan_id"], job["session_id"])
        with self.export_lock:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "caption.txt").write_text(self.caption_text(job["session_id"], plan["asset_id"]), encoding="utf-8")
            filenames = job["output_keys"]
            for filename in filenames:
                self.materialize_artifact(job, filename)
            caption_key = job.get("artifact_objects", {}).get("caption.txt") or self.artifact_key(job, "caption.txt")
            self.objects.upload(caption_key, directory / "caption.txt")
            with zipfile.ZipFile(directory / "post-package.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for filename in filenames:
                    archive.write(directory / filename, filename)
            self.objects.upload(self.artifact_key(job, "post-package.zip"), directory / "post-package.zip")
        return [*filenames, "post-package.zip"]

    def recover(self) -> None:
        for asset in self.store.list("assets"):
            if asset["validation_status"] == "validating":
                asset.update(validation_status="uploaded", error_message="Inspection was interrupted. Retry inspection.")
                self.store.put("assets", asset)
        for job in self.store.list("jobs"):
            if job["status"] == "processing":
                job.update(status="failed", phase="failed", lease_until=None, error_code="WORKER_RESTART", error_message="Butler restarted during this render. One deliberate retry is available.")
                self.store.put("jobs", job)
            elif job["status"] == "queued":
                self.dispatch(job)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

