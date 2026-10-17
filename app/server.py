from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from app.core import ButlerError, CATEGORIES, Catalog, MAX_UPLOAD_BYTES, SESSION_TTL, Store, TONES, bounded_text, new_id, parse_shopping_request, validate_plan
from app.media import MediaEngine, MediaTools
from app.providers import VertexAdapter, local_caption, valid_hashtags


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RETRYABLE = {"RENDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "WORKER_RESTART", "DISPATCH_BUSY", "TOOLS_REQUIRED"}


class ButlerApplication:
    def __init__(self, data_dir: Path | None = None) -> None:
        default_base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
        self.data_dir = (data_dir or Path(os.environ.get("BUTLER_DATA_DIR", str(default_base / "ButlerPrototype")))).resolve()
        self.store = Store(self.data_dir / "butler.sqlite3")
        self.catalog = Catalog(PROJECT_ROOT / "data" / "products.json")
        self.tools = MediaTools()
        self.provider_mode = os.environ.get("BUTLER_MODEL", "local")
        if self.provider_mode not in ("local", "vertex"):
            raise ValueError("BUTLER_MODEL must be local or vertex")
        self.vertex = VertexAdapter()
        self.transcription_mode = os.environ.get("BUTLER_TRANSCRIPTION", "manual")
        if self.transcription_mode not in ("manual", "google"):
            raise ValueError("BUTLER_TRANSCRIPTION must be manual or google")
        self.media = MediaEngine(
            self.store, self.data_dir / "media", self.tools,
            self.vertex.transcribe if self.transcription_mode == "google" else None,
            self.vertex.select_video_moments if self.provider_mode == "vertex" else None,
        )
        self.passcode = os.environ.get("BUTLER_DEMO_PASSCODE", "")
        self.signing_key = secrets.token_bytes(32)
        self.mutation_lock = RLock()
        self.rate_lock = Lock()
        self.rates: dict[str, deque[float]] = defaultdict(deque)
        self.stop_event = Event()
        self.media.recover()
        self.cleanup_thread = Thread(target=self.cleanup_loop, daemon=True, name="butler-retention")
        self.cleanup_thread.start()

    def rate_limit(self, identity: str, limit: int = 60) -> None:
        with self.rate_lock:
            now = time.monotonic()
            history = self.rates[identity]
            while history and history[0] < now - 60:
                history.popleft()
            if len(history) >= limit:
                raise ButlerError("RATE_LIMITED", "Too many requests. Please try again in a minute.", 429)
            history.append(now)

    def capabilities(self) -> dict[str, Any]:
        return {"assistant": "Gemini on Vertex AI" if self.provider_mode == "vertex" else "Local rules", "model_mode": self.provider_mode, "catalog": self.catalog.metadata, "media": self.tools.status(), "transcription": self.transcription_mode, "retention_hours": 24, "storage": "Local SQLite", "max_upload_bytes": MAX_UPLOAD_BYTES}

    def product(self, product: dict[str, Any]) -> dict[str, Any]:
        return {**product, "source_label": self.catalog.metadata["source"], "source_url": None, "price_observed_at": None, "availability_note": "Not checked", "catalog_version": self.catalog.metadata["version"], "sample": True}

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        public = {key: value for key, value in record.items() if key not in ("session_id", "idempotency_key", "object_key", "lease_until")}
        if "attempt_count" in record:
            public["retryable"] = record["status"] == "failed" and record["error_code"] in RETRYABLE and record.get("retry_count", 0) < 1
        return public

    def bootstrap(self, session: dict[str, Any]) -> dict[str, Any]:
        collections = {kind: [self.public_record(record) for record in self.store.list(kind, session["id"])] for kind in ("messages", "assets", "plans", "jobs", "captions")}
        collections["messages"] = collections["messages"][-40:]
        return {"session": session, "capabilities": self.capabilities(), **collections}

    def preferences(self, session: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        updates = {}
        if "preferences" in values:
            candidate = values["preferences"]
            if not isinstance(candidate, dict):
                raise ButlerError("INVALID_INPUT", "Preferences must be an object.")
            preferences = {**session["preferences"], **{key: candidate[key] for key in ("budget_minor", "currency", "category", "tone") if key in candidate}}
            if preferences["tone"] not in TONES or preferences["category"] not in CATEGORIES:
                raise ButlerError("INVALID_INPUT", "Choose a supported tone and product category.")
            self.catalog.search(preferences["category"], preferences["budget_minor"], preferences["currency"])
            if preferences["budget_minor"] is not None and preferences["budget_minor"] > 100_000_000:
                raise ButlerError("INVALID_BUDGET", "Budget exceeds the supported limit.")
            updates["preferences"] = preferences
        for key, maximum in (("selected_product_ids", 3), ("saved_product_ids", 30)):
            if key in values:
                product_ids = values[key]
                if not isinstance(product_ids, list) or len(product_ids) > maximum or any(not isinstance(product_id, str) or product_id not in self.catalog.by_id for product_id in product_ids):
                    raise ButlerError("UNKNOWN_PRODUCT", "Use valid catalog IDs within the selection limit.")
                if len(set(product_ids)) != len(product_ids):
                    raise ButlerError("INVALID_INPUT", "Product selections must be distinct.")
                updates[key] = product_ids
        return self.store.update_session(session["id"], updates)

    def message(self, session_id: str, role: str, content: str, product_ids: list[str] | None = None) -> dict[str, Any]:
        record = {"id": new_id(), "session_id": session_id, "role": role, "content": content, "created_at": time.time(), "referenced_product_ids": product_ids or []}
        return self.store.put("messages", record)

    def chat(self, session: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        message = bounded_text(values.get("message"), "Message", 2000, False)
        key = bounded_text(values.get("client_message_id"), "Message ID", 128, False)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise ButlerError("INVALID_KEY", "Provide a valid client message ID.")
        with self.mutation_lock:
            cached = self.store.cached_chat(session["id"], key)
            if cached:
                return cached
            if len(self.store.list("messages", session["id"])) >= 100:
                raise ButlerError("SESSION_LIMIT", "This session has reached its conversation limit.", 429)
            products = []
            caption = None
            lowered = message.lower()
            intent = "shopping"
            shopping_requested = bool(re.search(r"\b(find|compare|search|recommend|shop|under|budget)\b", lowered))
            if re.search(r"\b(caption|hashtags?|social post)\b", lowered) and not shopping_requested:
                intent = "caption"
                caption = self.create_caption(session, {"product_ids": session["selected_product_ids"], "tone": session["preferences"]["tone"], "user_context": re.sub(r"^(?:write|draft|generate|create|make)\s+(?:a\s+)?(?:caption|social post)\s*(?:for|about)?\s*", "", message, flags=re.I)[:1000]})
                reply = "Your caption draft is ready in Create. It uses the selected catalog names, not unverified product claims. You can edit it before export."
            elif re.search(r"\b(upload|edit|video|reel|clip|render)\b", lowered) and not re.search(r"\b(find|mic|microphone|shop|compare|under|budget|light|tripod|headphone)\b", lowered):
                intent = "create"
                reply = "Let's work on your clip. Rendering stays separate from chat and requires your approval of the final edit plan."
            elif lowered.strip(" .!?") in ("hi", "hello", "hey", "help"):
                reply = "What are we making today? Tell me the equipment you're looking for and your budget, or start with your own clip."
            else:
                parsed = parse_shopping_request(message, session["preferences"])
                preferences = {**session["preferences"], **{name: parsed[name] for name in ("category", "currency", "budget_minor")}}
                self.store.update_session(session["id"], {"preferences": preferences})
                if parsed["budget_minor"] is None and not parsed["no_budget"]:
                    reply = f"What is your budget in {parsed['currency']}? You can also choose a no-budget search."
                else:
                    products = self.catalog.search(parsed["category"], parsed["budget_minor"], parsed["currency"])[:3]
                    if not products:
                        reply = f"No sample products match those {parsed['currency']} constraints. I haven't converted currencies or invented a match."
                    elif self.provider_mode == "vertex":
                        reply = self.vertex.explain(message, products) + " These are fictional sample catalog entries, not live retailer offers."
                    else:
                        names = ", ".join(product["name"] for product in products)
                        boundary = "without a price ceiling" if parsed["budget_minor"] is None else f"within your {parsed['currency']} budget"
                        reply = f"I found {names} {boundary}. They are ordered by sample price. Compare their confirmed catalog attributes below; unknown details stay unknown. These are illustrative prices, not live retailer offers."
            user_message = self.message(session["id"], "user", message)
            assistant_message = self.message(session["id"], "assistant", reply, [product["id"] for product in products])
            response = {"reply": reply, "intent": intent, "products": [self.product(product) for product in products], "caption": self.public_record(caption) if caption else None, "messages": [self.public_record(user_message), self.public_record(assistant_message)], "provider": self.capabilities()["assistant"]}
            self.store.save_chat(session["id"], key, response)
            return response

    def create_caption(self, session: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        product_ids = values.get("product_ids", [])
        if not isinstance(product_ids, list):
            raise ButlerError("INVALID_COMPARISON", "Product IDs must be a list.")
        products = self.catalog.compare(product_ids) if product_ids else []
        tone = values.get("tone", session["preferences"]["tone"])
        if tone not in TONES:
            raise ButlerError("INVALID_TONE", "Choose a supported caption tone.")
        context = bounded_text(values.get("user_context", ""), "Context", 1000)
        transcript = bounded_text(values.get("transcript", ""), "Transcript", 6000)
        generated = self.vertex.caption(products, context, tone, transcript) if self.provider_mode == "vertex" else local_caption(products, context, tone)
        record = {"id": new_id(), "session_id": session["id"], "selected_product_ids": product_ids, "tone": tone, "user_context": context, **generated, "created_at": time.time(), "updated_at": time.time(), "provider": self.capabilities()["assistant"]}
        return self.store.put("captions", record)

    def new_asset(self, session: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        filename = bounded_text(values.get("filename"), "Filename", 180, False)
        size = values.get("size_bytes")
        if not filename.lower().endswith(".mp4") or values.get("content_type") not in ("video/mp4", "application/octet-stream"):
            raise ButlerError("UNSUPPORTED_FILE", "Choose an MP4 video.")
        if type(size) is not int or not 0 < size <= MAX_UPLOAD_BYTES:
            raise ButlerError("UPLOAD_TOO_LARGE", "Choose one MP4 file of at most 50 MB.", 413)
        if values.get("consent") is not True:
            raise ButlerError("CONSENT_REQUIRED", "Confirm that you own this clip or have permission to use it.")
        if len(self.store.list("assets", session["id"])) >= 5:
            raise ButlerError("ASSET_LIMIT", "The five-upload session limit has been reached.", 429)
        if any(job["status"] in ("queued", "processing") for job in self.store.list("jobs", session["id"])):
            raise ButlerError("ASSET_BUSY", "Wait for your active render before uploading another clip.", 409)
        asset = {"id": new_id(), "session_id": session["id"], "original_filename": Path(filename.replace("\\", "/")).name, "declared_size_bytes": size, "size_bytes": None, "validation_status": "reserved", "created_at": time.time(), "expires_at": session["expires_at"], "error_code": None, "error_message": None}
        self.store.put("assets", asset)
        return {**self.public_record(asset), "upload_url": f"/api/v1/assets/{asset['id']}/upload"}

    def complete_asset(self, session: dict[str, Any], asset_id: str) -> dict[str, Any]:
        with self.mutation_lock:
            asset = self.store.get("assets", asset_id, session["id"])
            if asset["validation_status"] in ("valid", "validating"):
                return self.public_record(asset)
            if asset["validation_status"] not in ("uploaded", "needs_tools"):
                raise ButlerError("ASSET_NOT_UPLOADED", "Upload the clip before starting inspection.", 409)
            asset.update(validation_status="validating", error_code=None, error_message=None)
            self.store.put("assets", asset)
            if not self.media.submit(self.media.validate_asset, asset):
                asset.update(validation_status="uploaded")
                self.store.put("assets", asset)
                raise ButlerError("QUEUE_BUSY", "The inspection queue is full. Retry when a slot is available.", 503)
            return self.public_record(asset)

    def new_plan(self, session: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        asset_id = bounded_text(values.get("asset_id"), "Asset ID", 64, False)
        asset = self.store.get("assets", asset_id, session["id"])
        plan = validate_plan(asset, values)
        if plan["subtitles_enabled"] and not plan["transcript"] and self.transcription_mode != "google":
            raise ButlerError("TRANSCRIPT_REQUIRED", "Automatic transcription is not configured. Provide a manual transcript or disable subtitles.")
        plan.update(id=new_id(), session_id=session["id"], version=1, status="proposed", approved_at=None, created_at=time.time())
        return self.store.put("plans", plan)

    def download_signature(self, session_id: str, job_id: str, filename: str, expires: int) -> str:
        return hmac.new(self.signing_key, f"{session_id}:{job_id}:{filename}:{expires}".encode(), hashlib.sha256).hexdigest()

    def cleanup_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                for session_id in self.store.expire_sessions():
                    shutil.rmtree(self.data_dir / "media" / session_id, ignore_errors=True)
                for job in self.store.list("jobs"):
                    if job["status"] == "processing" and job.get("lease_until", time.time() + 1) < time.time():
                        self.store.update_job(job["id"], job["session_id"], job["attempt_count"], {"status": "failed", "phase": "failed", "error_code": "RENDER_TIMEOUT", "error_message": "The worker lease expired. No partial output is available.", "lease_until": None})
                    elif job["status"] == "queued" and job["updated_at"] < time.time() - 600:
                        self.store.update_job(job["id"], job["session_id"], job["attempt_count"], {"status": "failed", "phase": "dispatch", "error_code": "DISPATCH_BUSY", "error_message": "The dispatch deadline expired. Retry this same job once."}, expected_status="queued")
            except Exception:
                pass
            self.stop_event.wait(300)

    def shutdown(self) -> None:
        self.stop_event.set()
        self.cleanup_thread.join(timeout=2)
        self.media.shutdown()


class ButlerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], app: ButlerApplication) -> None:
        self.app = app
        super().__init__(address, ButlerHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(30)
        return connection, address


class ButlerHandler(BaseHTTPRequestHandler):
    server: ButlerHTTPServer
    server_version = "Butler/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def app(self) -> ButlerApplication:
        return self.server.app

    def security_headers(self, content_type: str, size: int, cache: str = "no-store") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://images.unsplash.com; font-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def send_json(self, data: dict[str, Any], status: int = 200, cookie: str | None = None) -> None:
        payload = json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.security_headers("application/json; charset=utf-8", len(payload))
        if cookie:
            secure = "; Secure" if os.environ.get("BUTLER_COOKIE_SECURE") == "1" else ""
            self.send_header("Set-Cookie", f"butler_session={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}{secure}")
        self.end_headers()
        self.wfile.write(payload)

    def json_body(self) -> dict[str, Any]:
        length = self.body_length()
        if length > 32 * 1024:
            raise ButlerError("REQUEST_TOO_LARGE", "The request exceeds the allowed size.", 413)
        if length == 0:
            return {}
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ButlerError("INVALID_CONTENT_TYPE", "JSON content is required.", 400)
        try:
            value = json.loads(self.rfile.read(length), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise ButlerError("INVALID_JSON", "The request contains invalid JSON.", 400) from error
        if not isinstance(value, dict):
            raise ButlerError("INVALID_JSON", "A JSON object is required.", 400)
        return value

    def body_length(self) -> int:
        if self.headers.get("Transfer-Encoding"):
            raise ButlerError("INVALID_UPLOAD", "Chunked requests are not supported.", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ButlerError("INVALID_LENGTH", "Content length is invalid.", 400) from error
        if length < 0:
            raise ButlerError("INVALID_LENGTH", "Content length is invalid.", 400)
        return length

    def check_host(self) -> str:
        host = self.headers.get("Host", "")
        port = self.server.server_port
        public_origin = os.environ.get("BUTLER_PUBLIC_ORIGIN", "").rstrip("/")
        if public_origin:
            parsed_origin = urlsplit(public_origin)
            if parsed_origin.scheme not in ("https", "http") or not parsed_origin.netloc or parsed_origin.path not in ("", "/") or parsed_origin.query or parsed_origin.fragment:
                raise ButlerError("INVALID_HOST_CONFIG", "The configured public origin is invalid.", 500)
            allowed = {parsed_origin.netloc.lower()}
            allowed.update(value.strip().lower() for value in os.environ.get("BUTLER_ALLOWED_HOSTS", "").split(",") if value.strip())
            if host.lower() not in allowed:
                raise ButlerError("INVALID_HOST", "The request host is not configured for this Butler deployment.", 403)
            return public_origin

        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if host not in allowed:
            raise ButlerError("INVALID_HOST", "Butler accepts only its loopback origin unless a public origin is configured.", 403)
        return f"http://{host}"

    def session(self) -> dict[str, Any]:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["butler_session"].value if "butler_session" in cookie else ""
        except Exception:
            token = ""
        session = self.app.store.session_for_token(token)
        if not session:
            raise ButlerError("NO_SESSION", "Your session is missing or expired.", 401)
        return session

    def mutation_security(self, session: dict[str, Any] | None) -> None:
        if self.headers.get("Origin") != self.check_host():
            raise ButlerError("INVALID_ORIGIN", "The request origin is not allowed.", 403)
        if session and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf_token"]):
            raise ButlerError("INVALID_CSRF", "The request security token is missing or expired.", 403)

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def dispatch(self, method: str) -> None:
        self.request_id = new_id()
        try:
            self.check_host()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if method == "GET" and path == "/health":
                self.send_json({"status": "ok", "version": "1.0"})
                return
            if method == "GET" and not path.startswith("/api/"):
                self.static_file(path)
                return
            if method == "POST" and path == "/api/v1/sessions":
                self.mutation_security(None)
                self.app.rate_limit(f"login:{self.client_address[0]}", 20)
                values = self.json_body()
                if self.app.passcode and not secrets.compare_digest(bounded_text(values.get("passcode", ""), "Access code", 256), self.app.passcode):
                    raise ButlerError("ACCESS_CODE_REQUIRED", "Enter the demo access code.", 401)
                try:
                    existing = self.session()
                except ButlerError:
                    existing = None
                if existing:
                    self.send_json({"session": existing})
                else:
                    token, session = self.app.store.create_session()
                    self.send_json({"session": session}, 201, token)
                return
            session = self.session()
            if method != "GET":
                self.mutation_security(session)
                self.app.rate_limit(session["id"])
            if method == "PUT" and re.fullmatch(r"/api/v1/assets/[a-f0-9]{32}/upload", path):
                self.upload(session, path.split("/")[4])
                return
            values = self.json_body() if method != "GET" else {}
            self.api_route(method, path, query, values, session)
        except ButlerError as error:
            self.send_json({"error": {"code": error.code, "message": error.message, "request_id": self.request_id}}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            return
        except TimeoutError:
            self.send_json({"error": {"code": "REQUEST_TIMEOUT", "message": "The request did not finish in time.", "request_id": self.request_id}}, 408)
        except Exception:
            self.send_json({"error": {"code": "INTERNAL_ERROR", "message": "Butler could not complete this request. Your existing work is retained.", "request_id": self.request_id}}, 500)

    def api_route(self, method: str, path: str, query: dict[str, list[str]], values: dict[str, Any], session: dict[str, Any]) -> None:
        if method == "GET" and path == "/api/v1/bootstrap":
            self.send_json(self.app.bootstrap(session))
        elif method == "PUT" and path == "/api/v1/session":
            self.send_json({"session": self.app.preferences(session, values)})
        elif method == "GET" and path == "/api/v1/products":
            raw_budget = query.get("max_price_minor", [""])[0]
            try:
                budget = int(raw_budget) if raw_budget else None
            except ValueError as error:
                raise ButlerError("INVALID_BUDGET", "Budget must be an integer in minor units.") from error
            products = self.app.catalog.search(query.get("category", [None])[0], budget, query.get("currency", ["INR"])[0])
            self.send_json({"products": [self.app.product(product) for product in products], "metadata": self.app.catalog.metadata})
        elif method == "POST" and path == "/api/v1/products/compare":
            self.send_json({"products": [self.app.product(product) for product in self.app.catalog.compare(values.get("product_ids"))], "metadata": self.app.catalog.metadata})
        elif method == "POST" and path == "/api/v1/chat":
            self.send_json(self.app.chat(session, values))
        elif method == "POST" and path == "/api/v1/captions":
            if len(self.app.store.list("captions", session["id"])) >= 50:
                raise ButlerError("SESSION_LIMIT", "This session has reached its caption limit.", 429)
            self.send_json(self.app.public_record(self.app.create_caption(session, values)), 201)
        elif method == "PUT" and re.fullmatch(r"/api/v1/captions/[a-f0-9]{32}", path):
            caption = self.app.store.get("captions", path.split("/")[4], session["id"])
            caption.update(text=bounded_text(values.get("text"), "Caption", 2000), hashtags=valid_hashtags(values.get("hashtags", [])), updated_at=time.time(), edited=True)
            self.send_json(self.app.public_record(self.app.store.put("captions", caption)))
        elif method == "GET" and re.fullmatch(r"/api/v1/captions/[a-f0-9]{32}/download", path):
            caption = self.app.store.get("captions", path.split("/")[4], session["id"])
            self.send_bytes((caption["text"] + "\n\n" + " ".join(caption["hashtags"]) + "\n").encode(), "text/plain; charset=utf-8", "caption.txt")
        elif method == "POST" and path == "/api/v1/assets/upload-url":
            with self.app.mutation_lock:
                self.send_json(self.app.new_asset(session, values), 201)
        elif method == "POST" and re.fullmatch(r"/api/v1/assets/[a-f0-9]{32}/complete", path):
            self.send_json(self.app.complete_asset(session, path.split("/")[4]), 202)
        elif method == "GET" and re.fullmatch(r"/api/v1/assets/[a-f0-9]{32}", path):
            self.send_json(self.app.public_record(self.app.store.get("assets", path.split("/")[4], session["id"])))
        elif method == "GET" and re.fullmatch(r"/api/v1/assets/[a-f0-9]{32}/preview", path):
            asset = self.app.store.get("assets", path.split("/")[4], session["id"])
            if asset["validation_status"] != "valid":
                raise ButlerError("ASSET_NOT_READY", "The clip is not ready for playback.", 409)
            self.send_file(self.app.media.asset_path(asset), "video/mp4")
        elif method == "POST" and path == "/api/v1/edit-plans":
            if len(self.app.store.list("plans", session["id"])) >= 50:
                raise ButlerError("SESSION_LIMIT", "This session has reached its edit-plan limit.", 429)
            self.send_json(self.app.public_record(self.app.new_plan(session, values)), 201)
        elif method == "POST" and re.fullmatch(r"/api/v1/edit-plans/[a-f0-9]{32}/approve", path):
            if not self.app.tools.status()["ready"]:
                raise ButlerError("TOOLS_REQUIRED", "Rendering requires FFmpeg, FFprobe, and a font from an approved installation.", 503)
            job, created = self.app.store.approve(session["id"], path.split("/")[4], values.get("plan_version"), values.get("idempotency_key"))
            if created:
                self.app.media.dispatch(job)
            self.send_json(self.app.public_record(self.app.store.get("jobs", job["id"], session["id"])), 202)
        elif method == "GET" and re.fullmatch(r"/api/v1/jobs/[a-f0-9]{32}", path):
            self.send_json(self.app.public_record(self.app.store.get("jobs", path.split("/")[4], session["id"])))
        elif method == "POST" and re.fullmatch(r"/api/v1/jobs/[a-f0-9]{32}/retry", path):
            if not self.app.tools.status()["ready"]:
                raise ButlerError("TOOLS_REQUIRED", "Configure approved media tools before retrying.", 503)
            job = self.app.store.retry_job(path.split("/")[4], session["id"])
            self.app.media.dispatch(job)
            self.send_json(self.app.public_record(job), 202)
        elif method == "GET" and re.fullmatch(r"/api/v1/jobs/[a-f0-9]{32}/downloads", path):
            job = self.app.store.get("jobs", path.split("/")[4], session["id"])
            filenames = self.app.media.package(job)
            expires = int(time.time()) + 600
            artifacts = [{"name": filename, "url": f"/api/v1/files/{job['id']}/{quote(filename)}?expires={expires}&signature={self.app.download_signature(session['id'], job['id'], filename, expires)}"} for filename in filenames]
            self.send_json({"artifacts": artifacts, "expires_at": expires})
        elif method == "GET" and re.fullmatch(r"/api/v1/jobs/[a-f0-9]{32}/preview", path):
            job = self.app.store.get("jobs", path.split("/")[4], session["id"])
            if job["status"] != "succeeded":
                raise ButlerError("JOB_NOT_FINISHED", "Preview is available only after a verified render.", 409)
            self.send_file(self.app.media.attempt_path(job) / "output.mp4", "video/mp4")
        elif method == "GET" and re.fullmatch(r"/api/v1/files/[a-f0-9]{32}/(?:output\.mp4|caption\.txt|subtitles\.srt|post-package\.zip)", path):
            parts = path.split("/")
            job = self.app.store.get("jobs", parts[4], session["id"])
            try:
                expires = int(query.get("expires", ["0"])[0])
            except ValueError:
                expires = 0
            signature = query.get("signature", [""])[0]
            if not time.time() < expires <= time.time() + 601 or not secrets.compare_digest(signature, self.app.download_signature(session["id"], job["id"], parts[5], expires)):
                raise ButlerError("DOWNLOAD_EXPIRED", "This download link has expired. Request a new one.", 403)
            if job["status"] != "succeeded":
                raise ButlerError("JOB_NOT_FINISHED", "The job is not complete.", 409)
            self.send_file(self.app.media.attempt_path(job) / parts[5], mimetypes.guess_type(parts[5])[0] or "application/octet-stream", parts[5])
        else:
            raise ButlerError("NOT_FOUND", "This route is unavailable.", 404)

    def upload(self, session: dict[str, Any], asset_id: str) -> None:
        length = self.body_length()
        if length > MAX_UPLOAD_BYTES:
            raise ButlerError("UPLOAD_TOO_LARGE", "The clip exceeds the 50 MB limit.", 413)
        with self.app.mutation_lock:
            asset = self.app.store.get("assets", asset_id, session["id"])
            if asset["validation_status"] != "reserved":
                raise ButlerError("UPLOAD_ALREADY_USED", "This upload location has already been used.", 409)
            if length != asset["declared_size_bytes"]:
                raise ButlerError("SIZE_MISMATCH", "Upload size does not match the declared file size.", 400)
            asset.update(validation_status="uploading")
            self.app.store.put("assets", asset)
        path = self.app.media.asset_path(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        try:
            remaining = length
            with partial.open("wb") as destination:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 256 * 1024))
                    if not chunk:
                        raise ButlerError("UPLOAD_INTERRUPTED", "The upload stopped before the file was complete.", 400)
                    destination.write(chunk)
                    remaining -= len(chunk)
            partial.replace(path)
            asset.update(validation_status="uploaded", size_bytes=length)
            self.app.store.put("assets", asset)
            self.send_json(self.app.public_record(asset))
        except Exception:
            partial.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            asset.update(validation_status="invalid", error_code="UPLOAD_INTERRUPTED", error_message="The upload did not finish. Choose the clip again.")
            self.app.store.put("assets", asset)
            raise

    def static_file(self, path: str) -> None:
        if path == "/":
            file = PROJECT_ROOT / "static" / "index.html"
        elif path.startswith("/static/"):
            root = (PROJECT_ROOT / "static").resolve()
            file = (root / path.removeprefix("/static/")).resolve()
            if not file.is_relative_to(root) or any(part.startswith(".") for part in Path(path).parts):
                raise ButlerError("NOT_FOUND", "This file is unavailable.", 404)
        else:
            raise ButlerError("NOT_FOUND", "This page is unavailable.", 404)
        self.send_file(file, mimetypes.guess_type(file.name)[0] or "application/octet-stream", cache="no-cache")

    def send_bytes(self, payload: bytes, content_type: str, download_name: str | None = None) -> None:
        self.send_response(200)
        self.security_headers(content_type, len(payload))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path: Path, content_type: str, download_name: str | None = None, cache: str = "no-store") -> None:
        if not path.is_file():
            raise ButlerError("NOT_FOUND", "This file is unavailable or has expired.", 404)
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header and content_type == "video/mp4":
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                raise ButlerError("INVALID_RANGE", "Unsupported playback range.", 416)
            start = int(match.group(1))
            end = min(size - 1, int(match.group(2))) if match.group(2) else size - 1
            if not 0 <= start <= end < size:
                raise ButlerError("INVALID_RANGE", "Playback range is outside the clip.", 416)
            status = 206
        self.send_response(status)
        self.security_headers(content_type, end - start + 1, cache)
        if content_type == "video/mp4":
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(remaining, 256 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
