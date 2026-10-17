from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import textwrap
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Callable

from app.core import ButlerError, MAX_DURATION, MAX_UPLOAD_BYTES, Store, validate_plan


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


class MediaEngine:
    def __init__(self, store: Store, root: Path, tools: MediaTools, transcriber: Callable[[Path, float], list[dict[str, Any]]] | None = None) -> None:
        self.store = store
        self.root = root
        self.tools = tools
        self.transcriber = transcriber
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="butler-media")
        self.slots = BoundedSemaphore(4)
        self.export_lock = Lock()

    def asset_path(self, asset: dict[str, Any]) -> Path:
        return self.root / asset["session_id"] / "assets" / asset["id"] / "source.mp4"

    def attempt_path(self, job: dict[str, Any]) -> Path:
        return self.root / job["session_id"] / "jobs" / job["id"] / f"attempt-{job['attempt_count']}"

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
            metadata = self.tools.inspect(self.asset_path(asset))
            asset.update(metadata, validation_status="valid", error_code=None, error_message=None)
        except ButlerError as error:
            asset.update(validation_status="needs_tools" if error.code == "TOOLS_REQUIRED" else "invalid", error_code=error.code, error_message=error.message)
            if error.code != "TOOLS_REQUIRED":
                self.asset_path(asset).unlink(missing_ok=True)
        except Exception:
            asset.update(validation_status="invalid", error_code="INSPECTION_FAILED", error_message="The clip could not be inspected.")
            self.asset_path(asset).unlink(missing_ok=True)
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
            inspected = self.tools.inspect(self.asset_path(asset))
            validate_plan({**asset, **inspected}, plan)
            duration = plan["trim_end_seconds"] - plan["trim_start_seconds"]
            if not self.tools.font:
                raise ButlerError("TOOLS_REQUIRED", "A subtitle/title font is missing. Configure BUTLER_FONT_PATH.", 503)
            shutil.copyfile(self.tools.font, directory / "font.ttf")
            (directory / "title.txt").write_text(textwrap.fill(plan["title_text"], width=28), encoding="utf-8")
            transcript_source = None
            if plan["subtitles_enabled"]:
                self.store.update_job(job_id, session_id, attempt, {"phase": "transcribing"})
                if plan["transcript"]:
                    segments = manual_subtitles(plan["transcript"], duration)
                    transcript_source = "manual_approximate_timing"
                elif self.transcriber:
                    self.tools.run(["-i", str(self.asset_path(asset)), "-ss", str(plan["trim_start_seconds"]), "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "audio.wav"], directory, 30)
                    segments = self.transcriber(directory / "audio.wav", duration)
                    transcript_source = "google_speech"
                else:
                    raise ButlerError("TRANSCRIPT_REQUIRED", "Automatic transcription is not configured. Provide a manual transcript or disable subtitles.")
                (directory / "subtitles.srt").write_text(write_srt(segments, duration), encoding="utf-8")
            self.store.update_job(job_id, session_id, attempt, {"phase": "rendering"})
            self.tools.run([
                "-i", str(self.asset_path(asset)), "-ss", str(plan["trim_start_seconds"]), "-t", str(duration),
                "-map", "0:v:0", "-map", "0:a:0?", "-vf", self.tools.filters(plan, plan["subtitles_enabled"]),
                "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "output.mp4",
            ], directory, 120)
            self.store.update_job(job_id, session_id, attempt, {"phase": "verifying"})
            output = self.tools.probe(directory / "output.mp4")
            video = next((stream for stream in output.get("streams", []) if stream.get("codec_type") == "video"), {})
            output_duration = float(output.get("format", {}).get("duration", 0))
            if (video.get("width"), video.get("height")) != (720, 1280) or not math.isfinite(output_duration) or abs(output_duration - duration) > 0.5:
                raise ButlerError("OUTPUT_INVALID", "The rendered output did not pass playback validation.")
            required = ["output.mp4"] + (["subtitles.srt"] if plan["subtitles_enabled"] else [])
            if not all((directory / filename).is_file() and (directory / filename).stat().st_size for filename in required):
                raise ButlerError("OUTPUT_INVALID", "The render did not produce all required artifacts.")
            (directory / "caption.txt").write_text(self.caption_text(session_id), encoding="utf-8")
            self.store.update_job(job_id, session_id, attempt, {"status": "succeeded", "phase": "complete", "lease_until": None, "output_keys": [*required, "caption.txt"], "transcript_source": transcript_source, "completed_at": time.time()})
        except ButlerError as error:
            self.store.update_job(job_id, session_id, attempt, {"status": "failed", "phase": "failed", "lease_until": None, "error_code": error.code, "error_message": error.message})
            shutil.rmtree(directory, ignore_errors=True)
        except Exception:
            self.store.update_job(job_id, session_id, attempt, {"status": "failed", "phase": "failed", "lease_until": None, "error_code": "WORKER_FAILED", "error_message": "The worker stopped unexpectedly. No partial output has been published."})
            shutil.rmtree(directory, ignore_errors=True)

    def caption_text(self, session_id: str) -> str:
        captions = self.store.list("captions", session_id)
        if not captions:
            return ""
        caption = captions[-1]
        return caption["text"].strip() + ("\n\n" + " ".join(caption["hashtags"]) if caption["hashtags"] else "") + "\n"

    def package(self, job: dict[str, Any]) -> list[str]:
        if job["status"] != "succeeded":
            raise ButlerError("JOB_NOT_FINISHED", "Downloads are available only after a verified render.", 409)
        directory = self.attempt_path(job)
        with self.export_lock:
            (directory / "caption.txt").write_text(self.caption_text(job["session_id"]), encoding="utf-8")
            filenames = job["output_keys"]
            if not all((directory / filename).is_file() for filename in filenames):
                raise ButlerError("ARTIFACT_EXPIRED", "The output files have expired or are unavailable.", 404)
            with zipfile.ZipFile(directory / "post-package.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for filename in filenames:
                    archive.write(directory / filename, filename)
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