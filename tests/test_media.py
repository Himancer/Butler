import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.core import ButlerError, Store, new_id, validate_plan
from app.media import MediaEngine, MediaTools, hybrid_intervals, manual_subtitles, subtitle_clock, write_srt


class MemoryMediaObjects:
    mode = "Google Cloud Storage"

    def __init__(self):
        self.objects = {}

    def upload(self, key, source):
        self.objects[key] = source.read_bytes()

    def download(self, key, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects[key])

    def delete_prefix(self, prefix):
        for key in list(self.objects):
            if key.startswith(prefix.rstrip("/") + "/"):
                del self.objects[key]


class MediaObjectStoreTests(unittest.TestCase):
    def test_assets_and_artifacts_can_be_restored_from_object_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / "test.sqlite3")
            objects = MemoryMediaObjects()
            engine = MediaEngine(store, root / "media", MediaTools(), objects=objects)
            try:
                asset = {"id": "asset-one", "session_id": "session-one"}
                source = engine.asset_path(asset)
                source.parent.mkdir(parents=True)
                source.write_bytes(b"source video")
                engine.persist_asset(asset, source)
                source.unlink()
                self.assertEqual(engine.materialize_asset(asset).read_bytes(), b"source video")

                job = {"id": "job-one", "session_id": "session-one", "attempt_count": 1}
                artifact = engine.attempt_path(job) / "output.mp4"
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(b"rendered video")
                objects.upload(engine.artifact_key(job, "output.mp4"), artifact)
                artifact.unlink()
                self.assertEqual(engine.materialize_artifact(job, "output.mp4").read_bytes(), b"rendered video")

                engine.delete_session("session-one")
                self.assertEqual(objects.objects, {})
            finally:
                engine.shutdown()


class SubtitleTests(unittest.TestCase):
    def test_manual_timing_is_output_relative(self):
        segments = manual_subtitles("First line\nSecond line", 20)
        self.assertEqual(segments[0]["start"], 0)
        self.assertEqual(segments[-1]["end"], 20)
        output = write_srt(segments, 20)
        self.assertIn("00:00:00,000 --> 00:00:10,000", output)
        self.assertIn("00:00:10,000 --> 00:00:20,000", output)

    def test_empty_and_out_of_range_transcripts_are_rejected(self):
        with self.assertRaises(ButlerError):
            manual_subtitles("", 20)
        with self.assertRaises(ButlerError):
            write_srt([{"start": 0, "end": 31, "text": "hello"}], 20)

    def test_markup_cannot_inject_extra_subtitle_blocks(self):
        output = write_srt(manual_subtitles("<b>Hello</b> --> there", 5), 5)
        self.assertNotIn("<b>", output)
        self.assertEqual(output.count("-->"), 1)

    def test_clock_rolls_over_correctly(self):
        self.assertEqual(subtitle_clock(59.9996), "00:01:00,000")


class SmartShortenTests(unittest.TestCase):
    def test_hybrid_preserves_full_length_when_keep_original_is_selected(self):
        intervals, speed = hybrid_intervals(30.0, 30.0, [])
        self.assertEqual(intervals, [(0.0, 30.0)])
        self.assertAlmostEqual(sum(end - start for start, end in intervals) / speed, 30.0)

    def test_hybrid_excerpts_and_speed_match_requested_length(self):
        for target in (24.0, 12.0, 5.0):
            with self.subTest(target=target):
                intervals, speed = hybrid_intervals(30.0, target, [4.0, 10.0, 17.0, 24.0])
                self.assertTrue(all(0 <= start < end <= 30.0 for start, end in intervals))
                self.assertAlmostEqual(sum(end - start for start, end in intervals) / speed, target)
                ordered = sorted(intervals)
                self.assertTrue(all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:])))


class MediaValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "source.mp4"
        self.path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20)
        self.tools = MediaTools()

    def tearDown(self):
        self.directory.cleanup()

    def metadata(self, **updates):
        video = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
        video.update(updates)
        return {"format": {"duration": "30"}, "streams": [video, {"codec_type": "audio"}]}

    def test_fake_extension_is_rejected_before_probe(self):
        self.path.write_bytes(b"not a video")
        with self.assertRaises(ButlerError) as caught:
            self.tools.inspect(self.path)
        self.assertEqual(caught.exception.code, "INVALID_MEDIA")

    def test_valid_metadata_is_extracted(self):
        with patch.object(self.tools, "probe", return_value=self.metadata()):
            result = self.tools.inspect(self.path)
        self.assertEqual(result["duration_seconds"], 30)
        self.assertTrue(result["has_audio"])

    def test_61_seconds_is_rejected(self):
        metadata = self.metadata()
        metadata["format"]["duration"] = "61"
        with patch.object(self.tools, "probe", return_value=metadata):
            with self.assertRaises(ButlerError) as caught:
                self.tools.inspect(self.path)
        self.assertEqual(caught.exception.code, "DURATION_LIMIT")

    def test_excessive_dimensions_and_audio_only_are_rejected(self):
        for metadata in (self.metadata(width=8000), {"format": {"duration": "10"}, "streams": [{"codec_type": "audio"}]}):
            with patch.object(self.tools, "probe", return_value=metadata):
                with self.assertRaises(ButlerError):
                    self.tools.inspect(self.path)

    def test_title_is_never_interpolated_into_filter(self):
        title = "';movie=https://example.invalid/payload"
        filters = self.tools.filters({"fit_mode": "letterbox", "title_text": title}, True)
        self.assertNotIn(title, filters)
        self.assertIn("textfile=title.txt:expansion=none", filters)
        self.assertIn("subtitles=subtitles.srt", filters)

    def test_no_shell_execution(self):
        self.tools.ffmpeg = "approved-ffmpeg"
        with patch("app.media.subprocess.run") as run:
            run.return_value.returncode = 0
            self.tools.run(["-i", "source.mp4", "output.mp4"], Path(self.directory.name), 10)
        arguments, keywords = run.call_args
        self.assertIsInstance(arguments[0], list)
        self.assertNotIn("shell", keywords)


class RetryTests(unittest.TestCase):
    def test_retry_is_bounded_and_keeps_same_job(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "test.sqlite3")
            _, session = store.create_session()
            store.put("plans", {"id": "plan-one", "session_id": session["id"], "version": 1, "status": "proposed"})
            job, _ = store.approve(session["id"], "plan-one", 1, "approval-key-one")
            store.claim_job(job["id"], session["id"])
            store.update_job(job["id"], session["id"], 1, {"status": "failed", "error_code": "RENDER_TIMEOUT"})
            retried = store.retry_job(job["id"], session["id"])
            self.assertEqual(retried["id"], job["id"])
            self.assertIsNone(store.update_job(job["id"], session["id"], 1, {"status": "succeeded"}))
            store.claim_job(job["id"], session["id"])
            store.update_job(job["id"], session["id"], 2, {"status": "failed", "error_code": "RENDER_TIMEOUT"})
            with self.assertRaises(ButlerError):
                store.retry_job(job["id"], session["id"])

    def test_stale_worker_cannot_publish_after_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "test.sqlite3")
            _, session = store.create_session()
            store.put("plans", {"id": "plan-one", "session_id": session["id"], "version": 1, "status": "proposed"})
            job, _ = store.approve(session["id"], "plan-one", 1, "approval-key-one")
            store.claim_job(job["id"], session["id"])
            self.assertIsNone(store.update_job(job["id"], session["id"], 0, {"status": "succeeded"}))
            self.assertIsNotNone(store.update_job(job["id"], session["id"], 1, {"status": "succeeded"}))
            self.assertIsNone(store.update_job(job["id"], session["id"], 1, {"status": "failed"}))


class RealRenderTests(unittest.TestCase):
    @unittest.skipUnless(MediaTools().status()["ready"], "Approved FFmpeg/FFprobe tools are not configured")
    def test_real_trim_render_subtitles_and_edited_caption_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / "test.sqlite3")
            tools = MediaTools()
            engine = MediaEngine(store, root / "media", tools)
            try:
                _, session = store.create_session()
                asset = {"id": new_id(), "session_id": session["id"], "original_filename": "synthetic-test.mp4", "validation_status": "valid"}
                source = engine.asset_path(asset)
                source.parent.mkdir(parents=True)
                tools.run(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], source.parent, 30)
                asset.update(tools.inspect(source))
                store.put("assets", asset)
                values = {"trim_start_seconds": 0.2, "trim_end_seconds": 1.2, "fit_mode": "letterbox", "title_text": "A" * 80, "subtitles_enabled": True, "transcript": "Hello world\nA short test"}
                plan = {**validate_plan(asset, values), "id": new_id(), "session_id": session["id"], "version": 1, "status": "proposed", "created_at": time.time()}
                store.put("plans", plan)
                caption = {"id": new_id(), "session_id": session["id"], "text": "Original caption", "hashtags": ["#Test"]}
                store.put("captions", caption)
                job, _ = store.approve(session["id"], plan["id"], 1, new_id())
                engine.process(job["id"], session["id"])
                completed = store.get("jobs", job["id"], session["id"])
                self.assertEqual(completed["status"], "succeeded", completed.get("error_message"))
                caption["text"] = "Edited before export"
                store.put("captions", caption)
                artifacts = engine.package(completed)
                self.assertIn("subtitles.srt", artifacts)
                with zipfile.ZipFile(engine.attempt_path(completed) / "post-package.zip") as archive:
                    self.assertIn(b"Edited before export", archive.read("caption.txt"))
                    self.assertIn(b"00:00:00,000", archive.read("subtitles.srt"))
                engine.process(job["id"], session["id"])
                self.assertEqual(store.get("jobs", job["id"], session["id"])["attempt_count"], 1)
            finally:
                engine.shutdown()


if __name__ == "__main__":
    unittest.main()
