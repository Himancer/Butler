import unittest
from unittest.mock import Mock
from pathlib import Path
from app.storage import GCSMediaObjectStore, MediaStorageError


class StorageTests(unittest.TestCase):
    def test_upload_classifies_failures_without_exposing_provider_details(self):
        for status, expected in [(403, "access is denied"), (404, "bucket was not found"), (429, "rate limited"), (500, "temporarily unavailable")]:
            with self.subTest(status=status):
                store = GCSMediaObjectStore.__new__(GCSMediaObjectStore)
                store.bucket = Mock()
                error = RuntimeError("private signed URL and credentials")
                error.code = status
                store.bucket.blob.return_value.upload_from_filename.side_effect = error
                with self.assertLogs("app.storage", level="ERROR") as logged:
                    with self.assertRaises(MediaStorageError) as caught:
                        store.upload("session/source.mp4", Path("clip.mp4"))
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("credentials", str(caught.exception))
                self.assertNotIn("credentials", " ".join(logged.output))

    def test_successful_upload_is_not_changed(self):
        store = GCSMediaObjectStore.__new__(GCSMediaObjectStore)
        store.bucket = Mock()
        store.upload("session/source.mp4", Path("clip.mp4"))
        store.bucket.blob.return_value.upload_from_filename.assert_called_once_with("clip.mp4", timeout=120)
