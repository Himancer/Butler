"""Firestore implementation of the persistence contract used by ButlerApplication."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Any

from app.core import ButlerError, SESSION_TTL, new_id


class FirestoreStore:
    def __init__(self, project: str = "") -> None:
        try:
            from google.cloud import firestore
            from google.api_core.exceptions import AlreadyExists
        except ImportError as error:
            raise RuntimeError("Install requirements.txt to use Firestore") from error
        self.firestore = firestore
        self.already_exists = AlreadyExists
        self.client = firestore.Client(project=project or None)

    @staticmethod
    def _record_key(kind: str, identifier: str) -> str:
        return f"{kind}_{identifier}"

    def create_session(self) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        session = {
            "id": new_id(), "csrf_token": secrets.token_urlsafe(32),
            "created_at": time.time(), "expires_at": time.time() + SESSION_TTL,
            "preferences": {"budget_minor": None, "currency": "INR", "category": "microphones", "tone": "friendly"},
            "selected_product_ids": [], "saved_product_ids": [],
        }
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        batch = self.client.batch()
        batch.set(self.client.collection("sessions").document(session["id"]), {"data": json.dumps(session), "token_hash": token_hash, "expires_at": session["expires_at"]})
        batch.set(self.client.collection("session_tokens").document(token_hash), {"session_id": session["id"], "expires_at": session["expires_at"]})
        batch.commit()
        return token, session

    def session_for_token(self, token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        index = self.client.collection("session_tokens").document(token_hash).get()
        if not index.exists:
            return None
        session = self.client.collection("sessions").document(index.get("session_id")).get()
        if not session.exists or session.get("expires_at") <= time.time():
            return None
        return json.loads(session.get("data"))

    def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        reference = self.client.collection("sessions").document(session_id)
        transaction = self.client.transaction()

        @self.firestore.transactional
        def update(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.get("expires_at") <= time.time():
                raise ButlerError("NO_SESSION", "Your session has expired.", 401)
            session = json.loads(snapshot.get("data"))
            for key in ("preferences", "selected_product_ids", "saved_product_ids"):
                if key in updates:
                    session[key] = updates[key]
            transaction.update(reference, {"data": json.dumps(session)})
            return session

        return update(transaction)

    def put(self, kind: str, record: dict[str, Any]) -> dict[str, Any]:
        payload = {"kind": kind, "id": record["id"], "session_id": record["session_id"], "data": json.dumps(record, allow_nan=False), "updated_at": time.time()}
        self.client.collection("records").document(self._record_key(kind, record["id"])).set(payload)
        return record

    def get(self, kind: str, record_id: str, session_id: str) -> dict[str, Any]:
        snapshot = self.client.collection("records").document(self._record_key(kind, record_id)).get()
        if not snapshot.exists or snapshot.get("session_id") != session_id or snapshot.get("kind") != kind:
            raise ButlerError("NOT_FOUND", "This resource is unavailable.", 404)
        return json.loads(snapshot.get("data"))

    def list(self, kind: str, session_id: str | None = None) -> list[dict[str, Any]]:
        query = self.client.collection("records")
        if session_id:
            query = query.where("session_id", "==", session_id)
        else:
            query = query.where("kind", "==", kind)
        rows = []
        for snapshot in query.stream():
            payload = snapshot.to_dict()
            if payload.get("kind") == kind and (session_id is None or payload.get("session_id") == session_id):
                rows.append((payload.get("updated_at", 0), payload.get("data"), payload.get("session_id")))
        rows.sort(key=lambda row: row[0])
        return [json.loads(row[1]) for row in rows]

    def cached_chat(self, session_id: str, key: str) -> dict[str, Any] | None:
        reference = self.client.collection("chat_requests").document(f"{session_id}_{key}")
        snapshot = reference.get()
        return json.loads(snapshot.get("response")) if snapshot.exists else None

    def save_chat(self, session_id: str, key: str, response: dict[str, Any]) -> None:
        reference = self.client.collection("chat_requests").document(f"{session_id}_{key}")
        try:
            reference.create({"session_id": session_id, "key": key, "response": json.dumps(response)})
        except self.already_exists:
            return

    def approve(self, session_id: str, plan_id: str, version: int, key: str) -> tuple[dict[str, Any], bool]:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise ButlerError("INVALID_KEY", "Provide a valid approval idempotency key.")
        approval_ref = self.client.collection("approvals").document(f"{session_id}_{key}")
        plan_ref = self.client.collection("records").document(self._record_key("plans", plan_id))
        transaction = self.client.transaction()

        @self.firestore.transactional
        def commit(transaction):
            existing = approval_ref.get(transaction=transaction)
            if existing.exists:
                data = existing.to_dict()
                if data["plan_id"] != plan_id or data["version"] != version:
                    raise ButlerError("KEY_CONFLICT", "This approval key already belongs to another plan.", 409)
                job_snapshot = self.client.collection("records").document(self._record_key("jobs", data["job_id"])).get(transaction=transaction)
                return json.loads(job_snapshot.get("data")), False
            plan_snapshot = plan_ref.get(transaction=transaction)
            if not plan_snapshot.exists or plan_snapshot.get("session_id") != session_id:
                raise ButlerError("NOT_FOUND", "This resource is unavailable.", 404)
            plan = json.loads(plan_snapshot.get("data"))
            if type(version) is not int or plan["version"] != version:
                raise ButlerError("STALE_PLAN", "The plan changed. Review its latest version before approving.", 409)
            if plan["status"] != "proposed":
                raise ButlerError("ALREADY_APPROVED", "This plan is already approved.", 409)
            plan.update(status="approved", approved_at=time.time())
            job = {"id": new_id(), "session_id": session_id, "plan_id": plan_id, "plan_version": version, "status": "queued", "phase": "waiting", "idempotency_key": key, "attempt_count": 0, "output_keys": [], "error_code": None, "error_message": None, "created_at": time.time(), "updated_at": time.time(), "lease_until": None}
            job_ref = self.client.collection("records").document(self._record_key("jobs", job["id"]))
            transaction.update(plan_ref, {"data": json.dumps(plan), "updated_at": time.time()})
            transaction.set(job_ref, {"kind": "jobs", "id": job["id"], "session_id": session_id, "data": json.dumps(job), "updated_at": job["updated_at"]})
            transaction.create(approval_ref, {"session_id": session_id, "plan_id": plan_id, "version": version, "job_id": job["id"]})
            return job, True

        return commit(transaction)

    def claim_job(self, job_id: str, session_id: str) -> dict[str, Any] | None:
        reference = self.client.collection("records").document(self._record_key("jobs", job_id))
        transaction = self.client.transaction()

        @self.firestore.transactional
        def claim(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.get("session_id") != session_id:
                return None
            job = json.loads(snapshot.get("data"))
            if job["status"] != "queued":
                return None
            job.update(status="processing", phase="validating", attempt_count=job["attempt_count"] + 1, updated_at=time.time(), lease_until=time.time() + 240)
            transaction.update(reference, {"data": json.dumps(job), "updated_at": job["updated_at"]})
            return job

        return claim(transaction)

    def retry_job(self, job_id: str, session_id: str) -> dict[str, Any]:
        retryable = {"RENDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "WORKER_RESTART", "DISPATCH_BUSY", "TOOLS_REQUIRED", "MEDIA_STORAGE_UNAVAILABLE"}
        reference = self.client.collection("records").document(self._record_key("jobs", job_id))
        transaction = self.client.transaction()

        @self.firestore.transactional
        def retry(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.get("session_id") != session_id:
                raise ButlerError("NOT_FOUND", "This resource is unavailable.", 404)
            job = json.loads(snapshot.get("data"))
            if job["status"] != "failed" or job["error_code"] not in retryable or job.get("retry_count", 0) >= 1:
                raise ButlerError("RETRY_NOT_ALLOWED", "This job is not eligible for another retry.", 409)
            job.update(status="queued", phase="waiting", retry_count=1, error_code=None, error_message=None, updated_at=time.time(), lease_until=None)
            transaction.update(reference, {"data": json.dumps(job), "updated_at": job["updated_at"]})
            return job

        return retry(transaction)

    def update_job(self, job_id: str, session_id: str, attempt: int, updates: dict[str, Any], expected_status: str = "processing") -> dict[str, Any] | None:
        reference = self.client.collection("records").document(self._record_key("jobs", job_id))
        transaction = self.client.transaction()

        @self.firestore.transactional
        def apply(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.get("session_id") != session_id:
                return None
            job = json.loads(snapshot.get("data"))
            if job["status"] != expected_status or job["attempt_count"] != attempt:
                return None
            job.update(updates, updated_at=time.time())
            transaction.update(reference, {"data": json.dumps(job, allow_nan=False), "updated_at": job["updated_at"]})
            return job

        return apply(transaction)

    def expire_sessions(self) -> list[str]:
        now = time.time()
        expired = []
        for session_doc in self.client.collection("sessions").where("expires_at", "<=", now).stream():
            session_id = session_doc.id
            token_hash = session_doc.get("token_hash")
            references = []
            for collection in ("records", "approvals", "chat_requests"):
                references.extend(doc.reference for doc in self.client.collection(collection).where("session_id", "==", session_id).stream())
            if token_hash:
                references.append(self.client.collection("session_tokens").document(token_hash))
            # Keep each commit below Firestore's 500-write batch limit. Delete
            # the session marker last so a partial cleanup can retry next pass.
            references.append(session_doc.reference)
            for offset in range(0, len(references), 450):
                batch = self.client.batch()
                for reference in references[offset:offset + 450]:
                    batch.delete(reference)
                batch.commit()
            expired.append(session_id)
        return expired
