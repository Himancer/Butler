"""Durable media-object adapters used by local and Cloud Run deployments."""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Protocol


class MediaStorageError(RuntimeError):
    pass


def storage_failure(error: Exception, operation: str) -> MediaStorageError:
    # Avoid logging provider messages, which can contain signed URLs.
    code = getattr(error, "code", None)
    logging.getLogger(__name__).error("Cloud Storage %s failed: %s (status=%s)", operation, type(error).__name__, code if isinstance(code, int) else "unknown")
    if code in (401, 403):
        return MediaStorageError("Video storage access is denied. The deployment owner must grant the Cloud Run service account access to the media bucket, then retry the upload.")
    if code == 404:
        return MediaStorageError("The video storage bucket was not found. The deployment owner must check the configured bucket name, then retry the upload.")
    if code == 429:
        return MediaStorageError("Video storage is temporarily rate limited. Please wait a moment and choose the clip again.")
    return MediaStorageError("Video storage is temporarily unavailable. Please retry. If it continues, ask the deployment owner to check Cloud Storage logs and configuration.")


class MediaObjectStore(Protocol):
    mode: str

    def upload(self, key: str, source: Path) -> None: ...
    def download(self, key: str, destination: Path) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalMediaObjectStore:
    mode = "Local files"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Invalid media object key")
        return target

    def upload(self, key: str, source: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target:
            shutil.copyfile(source, target)

    def download(self, key: str, destination: Path) -> None:
        source = self._path(key)
        if source.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def delete_prefix(self, prefix: str) -> None:
        target = self._path(prefix)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)


class GCSMediaObjectStore:
    mode = "Google Cloud Storage"

    def __init__(self, bucket_name: str) -> None:
        if not bucket_name:
            raise ValueError("BUTLER_MEDIA_BUCKET is required for GCS media storage")
        try:
            from google.cloud import storage
        except ImportError as error:
            raise RuntimeError("Install requirements.txt to use Google Cloud Storage") from error
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def upload(self, key: str, source: Path) -> None:
        try:
            self.bucket.blob(key).upload_from_filename(str(source), timeout=120)
        except Exception as error:
            raise storage_failure(error, "upload") from error

    def download(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".partial")
        try:
            self.bucket.blob(key).download_to_filename(str(partial), timeout=120)
            partial.replace(destination)
        except Exception as error:
            partial.unlink(missing_ok=True)
            raise MediaStorageError("Could not retrieve this media object from Cloud Storage") from error

    def delete_prefix(self, prefix: str) -> None:
        try:
            for blob in self.client.list_blobs(self.bucket, prefix=prefix.rstrip("/") + "/"):
                blob.delete()
        except Exception as error:
            raise MediaStorageError("Could not delete expired media from Cloud Storage") from error


def create_media_store(local_root: Path) -> MediaObjectStore:
    mode = os.environ.get("BUTLER_MEDIA_STORAGE", "local").lower()
    if mode == "local":
        return LocalMediaObjectStore(local_root)
    if mode == "gcs":
        return GCSMediaObjectStore(os.environ.get("BUTLER_MEDIA_BUCKET", ""))
    raise ValueError("BUTLER_MEDIA_STORAGE must be local or gcs")
