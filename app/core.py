from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DURATION = 60
SESSION_TTL = 24 * 60 * 60
CATEGORIES = ("microphones", "lighting", "tripods", "headphones")
TONES = ("friendly", "professional", "playful")


class ButlerError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def new_id() -> str:
    return uuid.uuid4().hex


def bounded_text(value: Any, name: str, maximum: int, allow_empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ButlerError("INVALID_INPUT", f"{name} must be text of at most {maximum} characters.")
    value = value.strip()
    if not allow_empty and not value:
        raise ButlerError("INVALID_INPUT", f"{name} is required.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ButlerError("INVALID_INPUT", f"{name} contains unsupported control characters.")
    return value


def money_minor(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ButlerError("INVALID_BUDGET", "Budget must be a non-negative amount.")
    try:
        amount = Decimal(str(value).replace(",", ""))
    except InvalidOperation as error:
        raise ButlerError("INVALID_BUDGET", "Enter a valid budget.") from error
    if not amount.is_finite() or amount < 0 or amount > 1_000_000:
        raise ButlerError("INVALID_BUDGET", "Budget must be between 0 and 1,000,000.")
    return int((amount * 100).quantize(Decimal("1")))


def parse_shopping_request(message: str, preferences: dict[str, Any]) -> dict[str, Any]:
    lowered = message.lower()
    aliases = {
        "microphones": r"\b(mic|mics|microphone|microphones|audio recording)\b",
        "lighting": r"\b(light|lights|lighting|softbox|ring light)\b",
        "tripods": r"\b(tripod|tripods|stand|stands)\b",
        "headphones": r"\b(headphone|headphones|headset|headsets)\b",
    }
    category = next((name for name, pattern in aliases.items() if re.search(pattern, lowered)), None)
    category = category or preferences.get("category", "microphones")
    currency = preferences.get("currency", "INR")
    if re.search(r"\b(usd|dollars?)\b|\$", lowered):
        currency = "USD"
    elif re.search(r"\b(eur|euros?)\b|\u20ac", lowered):
        currency = "EUR"
    elif re.search(r"\b(inr|rupees?|rs\.?)\b|\u20b9", lowered):
        currency = "INR"
    amount_match = re.search(
        r"(?:under|below|up to|upto|budget(?:\s+of)?|less than|max(?:imum)?(?:\s+of)?|[\u20b9$\u20ac])"
        r"\s*(?:inr\s*|rs\.?\s*|usd\s*|eur\s*)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?",
        lowered,
    )
    if not amount_match:
        amount_match = re.search(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?\s*(?:inr|rupees?|usd|dollars?|eur|euros?)\b", lowered)
    budget = preferences.get("budget_minor") if currency == preferences.get("currency", "INR") else None
    if amount_match:
        amount = Decimal(amount_match.group(1).replace(",", ""))
        if amount_match.group(2):
            amount *= 1000
        budget = money_minor(amount)
    no_budget = bool(re.search(r"\b(no budget|any budget|no limit|all prices|all budgets)\b", lowered))
    if no_budget:
        budget = None
    return {"category": category, "currency": currency, "budget_minor": budget, "no_budget": no_budget}


class Catalog:
    def __init__(self, path: Path) -> None:
        with path.open(encoding="utf-8") as source:
            catalog = json.load(source)
        self.metadata = catalog["metadata"]
        self.products = catalog["products"]
        self.by_id = {product["id"]: product for product in self.products}
        if len(self.by_id) != len(self.products):
            raise ValueError("Duplicate catalog IDs")
        for product in self.products:
            if type(product["price_minor"]) is not int or product["price_minor"] < 0:
                raise ValueError("Prices must be non-negative integer minor units")
            if product["category"] not in CATEGORIES:
                raise ValueError("Unsupported catalog category")

    def search(self, category: str | None = None, max_price_minor: int | None = None, currency: str = "INR", text_query: str | None = None) -> list[dict[str, Any]]:
        if category is not None and category not in CATEGORIES:
            raise ButlerError("INVALID_CATEGORY", "Choose a supported product category.")
        if max_price_minor is not None and (type(max_price_minor) is not int or max_price_minor < 0):
            raise ButlerError("INVALID_BUDGET", "Budget must be an integer amount in minor units.")
        if currency not in ("INR", "USD", "EUR"):
            raise ButlerError("INVALID_CURRENCY", "Supported currencies are INR, USD, and EUR.")
        if text_query is not None and (not isinstance(text_query, str) or len(text_query) > 120):
            raise ButlerError("INVALID_QUERY", "Search terms must be text of at most 120 characters.")
        terms = text_query.casefold().split() if text_query else []
        def matches_text(product: dict[str, Any]) -> bool:
            if not terms:
                return True
            searchable = " ".join([
                product["name"], product["category"], product["tag"], product["description"],
                *product["specifications"].keys(), *product["specifications"].values()
            ]).casefold()
            return all(term in searchable for term in terms)

        matches = [
            product for product in self.products
            if (category is None or product["category"] == category)
            and product["currency"] == currency
            and (max_price_minor is None or product["price_minor"] <= max_price_minor)
            and matches_text(product)
        ]
        return sorted(matches, key=lambda product: (product["price_minor"], product["id"]))

    def compare(self, product_ids: Any) -> list[dict[str, Any]]:
        if not isinstance(product_ids, list) or not 1 <= len(product_ids) <= 3:
            raise ButlerError("INVALID_COMPARISON", "Select between one and three products.")
        if any(not isinstance(product_id, str) or product_id not in self.by_id for product_id in product_ids):
            raise ButlerError("UNKNOWN_PRODUCT", "A selected product is not in the catalog.")
        if len(set(product_ids)) != len(product_ids):
            raise ButlerError("INVALID_COMPARISON", "Select distinct products.")
        return [self.by_id[product_id] for product_id in product_ids]


def validate_plan(asset: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    if asset.get("validation_status") != "valid":
        raise ButlerError("ASSET_NOT_READY", "This clip has not passed server-side inspection.", 409)
    shorten_duration = values.get("shorten_duration_seconds", 0)
    if type(shorten_duration) not in (int, float) or not math.isfinite(shorten_duration) or shorten_duration < 0 or shorten_duration > 60:
        raise ButlerError("INVALID_SHORTEN_DURATION", "Choose a target between 0 and 60 seconds.")
    start = 0 if shorten_duration else values.get("trim_start_seconds", 0)
    end = asset["duration_seconds"] if shorten_duration else values.get("trim_end_seconds", min(20, asset["duration_seconds"]))
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end)):
        raise ButlerError("INVALID_TRIM", "Trim bounds must be finite numbers.")
    if not 0 <= start < end <= asset["duration_seconds"]:
        raise ButlerError("INVALID_TRIM", "Trim end must follow the start and not exceed the clip duration.")
    fit_mode = values.get("fit_mode", "letterbox")
    if fit_mode not in ("letterbox", "center_crop"):
        raise ButlerError("INVALID_FIT", "Choose letterbox or center crop.")
    subtitles = values.get("subtitles_enabled", False)
    if type(subtitles) is not bool:
        raise ButlerError("INVALID_SUBTITLES", "Subtitle setting must be true or false.")
    transcript = bounded_text(values.get("transcript", ""), "Transcript", 6000)
    if subtitles and not transcript and not asset.get("has_audio"):
        raise ButlerError("NO_AUDIO", "This clip has no audio. Disable subtitles or provide a manual transcript.")
    shorten_strategy = values.get("shorten_strategy", "smart_highlights")
    shorten_audio = values.get("shorten_audio", "preserve")
    shorten_focus = values.get("shorten_focus", "balanced")
    if shorten_strategy not in ("smart_highlights", "uniform_speed", "hybrid"):
        raise ButlerError("INVALID_SHORTEN_STRATEGY", "Choose a supported Smart Shorten strategy.")
    if shorten_audio not in ("preserve", "music", "mute"):
        raise ButlerError("INVALID_SHORTEN_AUDIO", "Choose a supported Smart Shorten audio setting.")
    if shorten_focus not in ("balanced", "speech", "visual"):
        raise ButlerError("INVALID_SHORTEN_FOCUS", "Choose a supported highlight focus.")
    if shorten_duration and shorten_duration > asset["duration_seconds"]:
        raise ButlerError("SHORTEN_TARGET_TOO_LONG", "The Smart Shorten target cannot exceed the video duration.")
    return {
        "asset_id": asset["id"],
        "trim_start_seconds": float(start),
        "trim_end_seconds": float(end),
        "fit_mode": fit_mode,
        "title_text": bounded_text(values.get("title_text", ""), "Title", 80),
        "subtitles_enabled": subtitles,
        "transcript": transcript,
        "shorten_duration_seconds": shorten_duration,
        "shorten_strategy": shorten_strategy,
        "shorten_audio": shorten_audio,
        "shorten_focus": shorten_focus,
        "output_preset": "vertical_720p",
    }


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    csrf TEXT NOT NULL, expires_at REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, session_id TEXT NOT NULL,
                    data TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(kind, id)
                );
                CREATE INDEX IF NOT EXISTS owner_records ON records(session_id, kind);
                CREATE TABLE IF NOT EXISTS approvals (
                    session_id TEXT NOT NULL, key TEXT NOT NULL, plan_id TEXT NOT NULL,
                    version INTEGER NOT NULL, job_id TEXT NOT NULL,
                    PRIMARY KEY(session_id, key)
                );
                CREATE TABLE IF NOT EXISTS chat_requests (
                    session_id TEXT NOT NULL, key TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(session_id, key)
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_session(self) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        session = {
            "id": new_id(), "csrf_token": secrets.token_urlsafe(32),
            "created_at": time.time(), "expires_at": time.time() + SESSION_TTL,
            "preferences": {"budget_minor": None, "currency": "INR", "category": "microphones", "tone": "friendly"},
            "selected_product_ids": [], "saved_product_ids": [],
        }
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (session["id"], hashlib.sha256(token.encode()).hexdigest(), session["csrf_token"], session["expires_at"], json.dumps(session)),
            )
        return token, session

    def session_for_token(self, token: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT data FROM sessions WHERE token_hash=? AND expires_at>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT data FROM sessions WHERE id=? AND expires_at>?", (session_id, time.time())).fetchone()
            if not row:
                raise ButlerError("NO_SESSION", "Your session has expired.", 401)
            session = json.loads(row["data"])
            for key in ("preferences", "selected_product_ids", "saved_product_ids"):
                if key in updates:
                    session[key] = updates[key]
            connection.execute("UPDATE sessions SET data=? WHERE id=?", (json.dumps(session), session_id))
        return session

    @staticmethod
    def write_record(connection: sqlite3.Connection, kind: str, record: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (kind, record["id"], record["session_id"], json.dumps(record, allow_nan=False), time.time()),
        )

    def put(self, kind: str, record: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            self.write_record(connection, kind, record)
        return record

    def get(self, kind: str, record_id: str, session_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT data FROM records WHERE kind=? AND id=? AND session_id=?", (kind, record_id, session_id)).fetchone()
        if not row:
            raise ButlerError("NOT_FOUND", "This resource is unavailable.", 404)
        return json.loads(row["data"])

    def list(self, kind: str, session_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if session_id:
                rows = connection.execute("SELECT data FROM records WHERE kind=? AND session_id=? ORDER BY updated_at", (kind, session_id)).fetchall()
            else:
                rows = connection.execute("SELECT data FROM records WHERE kind=? ORDER BY updated_at", (kind,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def cached_chat(self, session_id: str, key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT response FROM chat_requests WHERE session_id=? AND key=?", (session_id, key)).fetchone()
        return json.loads(row["response"]) if row else None

    def save_chat(self, session_id: str, key: str, response: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO chat_requests VALUES (?, ?, ?)", (session_id, key, json.dumps(response)))

    def approve(self, session_id: str, plan_id: str, version: int, key: str) -> tuple[dict[str, Any], bool]:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise ButlerError("INVALID_KEY", "Provide a valid approval idempotency key.")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM approvals WHERE session_id=? AND key=?", (session_id, key)).fetchone()
            if existing:
                if existing["plan_id"] != plan_id or existing["version"] != version:
                    raise ButlerError("KEY_CONFLICT", "This approval key already belongs to another plan.", 409)
                row = connection.execute("SELECT data FROM records WHERE kind='jobs' AND id=? AND session_id=?", (existing["job_id"], session_id)).fetchone()
                return json.loads(row["data"]), False
            row = connection.execute("SELECT data FROM records WHERE kind='plans' AND id=? AND session_id=?", (plan_id, session_id)).fetchone()
            if not row:
                raise ButlerError("NOT_FOUND", "This resource is unavailable.", 404)
            plan = json.loads(row["data"])
            if type(version) is not int or plan["version"] != version:
                raise ButlerError("STALE_PLAN", "The plan changed. Review its latest version before approving.", 409)
            if plan["status"] != "proposed":
                raise ButlerError("ALREADY_APPROVED", "This plan is already approved.", 409)
            plan.update(status="approved", approved_at=time.time())
            job = {
                "id": new_id(), "session_id": session_id, "plan_id": plan_id, "plan_version": version,
                "status": "queued", "phase": "waiting", "idempotency_key": key,
                "attempt_count": 0, "output_keys": [], "error_code": None, "error_message": None,
                "created_at": time.time(), "updated_at": time.time(), "lease_until": None,
            }
            self.write_record(connection, "plans", plan)
            self.write_record(connection, "jobs", job)
            connection.execute("INSERT INTO approvals VALUES (?, ?, ?, ?, ?)", (session_id, key, plan_id, version, job["id"]))
        return job, True

    def claim_job(self, job_id: str, session_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT data FROM records WHERE kind='jobs' AND id=? AND session_id=?", (job_id, session_id)).fetchone()
            if not row:
                return None
            job = json.loads(row["data"])
            if job["status"] != "queued":
                return None
            job.update(status="processing", phase="validating", attempt_count=job["attempt_count"] + 1, updated_at=time.time(), lease_until=time.time() + 600)
            self.write_record(connection, "jobs", job)
        return job

    def retry_job(self, job_id: str, session_id: str) -> dict[str, Any]:
        retryable = {"RENDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "WORKER_RESTART", "DISPATCH_BUSY", "TOOLS_REQUIRED", "MEDIA_STORAGE_UNAVAILABLE"}
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT data FROM records WHERE kind='jobs' AND id=? AND session_id=?", (job_id, session_id)).fetchone()
            if not row:
                raise ButlerError("NOT_FOUND", "This resource is unavailable.", 404)
            job = json.loads(row["data"])
            if job["status"] != "failed" or job["error_code"] not in retryable or job.get("retry_count", 0) >= 1:
                raise ButlerError("RETRY_NOT_ALLOWED", "This job is not eligible for another retry.", 409)
            job.update(status="queued", phase="waiting", retry_count=1, error_code=None, error_message=None, updated_at=time.time(), lease_until=None)
            self.write_record(connection, "jobs", job)
        return job

    def update_job(self, job_id: str, session_id: str, attempt: int, updates: dict[str, Any], expected_status: str = "processing") -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT data FROM records WHERE kind='jobs' AND id=? AND session_id=?", (job_id, session_id)).fetchone()
            if not row:
                return None
            job = json.loads(row["data"])
            if job["status"] != expected_status or job["attempt_count"] != attempt:
                return None
            job.update(updates, updated_at=time.time())
            self.write_record(connection, "jobs", job)
        return job

    def expire_sessions(self) -> list[str]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT id FROM sessions WHERE expires_at<=?", (time.time(),)).fetchall()
            expired = [row["id"] for row in rows]
            for session_id in expired:
                for table in ("records", "approvals", "chat_requests"):
                    connection.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return expired
