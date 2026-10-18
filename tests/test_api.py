import http.client
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.server import ButlerApplication, ButlerHTTPServer


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.environment = patch.dict(os.environ, {"BUTLER_MODEL": "local", "BUTLER_TRANSCRIPTION": "manual", "BUTLER_DEMO_PASSCODE": ""})
        cls.environment.start()
        cls.app = ButlerApplication(Path(cls.directory.name))
        cls.server = ButlerHTTPServer(("127.0.0.1", 0), cls.app)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.app.shutdown()
        cls.environment.stop()
        cls.directory.cleanup()

    def setUp(self):
        status, data, headers = self.request("POST", "/api/v1/sessions", {})
        self.assertEqual(status, 201)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = data["session"]["csrf_token"]
        self.session_id = data["session"]["id"]

    def request(self, method, path, data=None, cookie=None, csrf=None, origin=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Origin": origin or self.origin}
        if cookie is not None:
            headers["Cookie"] = cookie
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        if raw is not None:
            body = raw
            headers["Content-Type"] = "video/mp4"
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        else:
            body = None
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        content_type = response.getheader("Content-Type", "")
        result = json.loads(payload) if "application/json" in content_type else payload
        status, returned_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, result, returned_headers

    def own(self, method, path, data=None, **keywords):
        return self.request(method, path, data, cookie=self.cookie, csrf=self.csrf, **keywords)

    def test_missing_session_and_csrf_are_rejected(self):
        status, data, _ = self.request("GET", "/api/v1/products")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["code"], "NO_SESSION")
        status, _, _ = self.request("PUT", "/api/v1/session", {}, cookie=self.cookie)
        self.assertEqual(status, 403)

    def test_foreign_origin_is_rejected(self):
        status, data, _ = self.own("PUT", "/api/v1/session", {}, origin="https://untrusted.invalid")
        self.assertEqual(status, 403)
        self.assertEqual(data["error"]["code"], "INVALID_ORIGIN")

    def test_public_preview_is_reported_as_public(self):
        with patch.dict(os.environ, {"BUTLER_PUBLIC_ORIGIN": "https://preview.example"}, clear=False):
            self.assertEqual(self.app.capabilities()["access_mode"], "Public preview")

    def test_shopping_chat_persists_and_duplicate_id_does_not_repeat(self):
        values = {"message": "Find a microphone under 5000 INR", "client_message_id": uuid.uuid4().hex}
        status, first, _ = self.own("POST", "/api/v1/chat", values)
        self.assertEqual(status, 200)
        self.assertEqual(len(first["products"]), 3)
        self.assertTrue(all(product["price_minor"] <= 500000 for product in first["products"]))
        self.assertTrue(all(product["sample"] for product in first["products"]))
        _, repeated, _ = self.own("POST", "/api/v1/chat", values)
        self.assertEqual(first, repeated)
        _, bootstrap, _ = self.own("GET", "/api/v1/bootstrap")
        self.assertEqual(len(bootstrap["messages"]), 2)
        self.assertEqual(bootstrap["session"]["preferences"]["budget_minor"], 500000)

    def test_product_search_api_filters_by_keyword(self):
        status, data, _ = self.own("GET", "/api/v1/products?category=microphones&currency=INR&max_price_minor=350000&q=USB-C")
        self.assertEqual(status, 200)
        self.assertEqual({product["id"] for product in data["products"]}, {"mic-01", "mic-03", "mic-10"})

    def test_compound_demo_request_starts_with_shopping_not_caption(self):
        _, response, _ = self.own("POST", "/api/v1/chat", {"message": "Find a microphone under 5000 INR, compare three options, and help turn my uploaded clip into a reel with a caption.", "client_message_id": uuid.uuid4().hex})
        self.assertEqual(response["intent"], "shopping")
        self.assertEqual(len(response["products"]), 3)
        self.assertIsNone(response["caption"])

    def test_caption_product_ids_must_be_a_list(self):
        status, _, _ = self.own("POST", "/api/v1/captions", {"product_ids": "", "user_context": "cooking"})
        self.assertEqual(status, 422)

    def test_creator_pack_accepts_transcript_and_returns_editable_fields(self):
        transcript = "I made a tiny lamp from colored paper."
        status, caption, _ = self.own("POST", "/api/v1/captions", {
            "product_ids": [], "user_context": "A playful craft video", "transcript": transcript, "tone": "playful",
        })
        self.assertEqual(status, 201)
        self.assertTrue(caption["hook"])
        self.assertTrue(caption["title"])
        self.assertTrue(caption["text"])
        self.assertTrue(caption["hashtags"])
        self.assertNotIn("transcript", caption)

    def test_missing_budget_clarifies_and_currency_is_preserved(self):
        _, response, _ = self.own("POST", "/api/v1/chat", {"message": "Find a microphone in USD", "client_message_id": uuid.uuid4().hex})
        self.assertIn("budget in USD", response["reply"])
        self.assertEqual(response["products"], [])
        _, response, _ = self.own("POST", "/api/v1/chat", {"message": "Find a mic under 50 USD", "client_message_id": uuid.uuid4().hex})
        self.assertEqual(response["products"], [])
        self.assertIn("No sample products", response["reply"])

    def test_saved_selection_survives_refresh(self):
        status, _, _ = self.own("PUT", "/api/v1/session", {"saved_product_ids": ["mic-01"], "selected_product_ids": ["mic-01", "mic-02"]})
        self.assertEqual(status, 200)
        _, bootstrap, _ = self.own("GET", "/api/v1/bootstrap")
        self.assertEqual(bootstrap["session"]["saved_product_ids"], ["mic-01"])
        self.assertEqual(len(bootstrap["session"]["selected_product_ids"]), 2)

    def test_caption_edit_is_retained_for_download_and_owned(self):
        status, caption, _ = self.own("POST", "/api/v1/captions", {"product_ids": ["mic-01"], "user_context": "cooking videos", "tone": "friendly"})
        self.assertEqual(status, 201)
        caption_path = "/api/v1/captions/" + caption["id"]
        status, _, _ = self.own("PUT", caption_path, {"text": "My edited caption", "hashtags": ["#Cooking"]})
        self.assertEqual(status, 200)
        status, contents, _ = self.own("GET", caption_path + "/download")
        self.assertEqual(status, 200)
        self.assertIn(b"My edited caption", contents)
        _, other, headers = self.request("POST", "/api/v1/sessions", {})
        status, _, _ = self.request("PUT", caption_path, {"text": "stolen", "hashtags": []}, cookie=headers["Set-Cookie"].split(";", 1)[0], csrf=other["session"]["csrf_token"])
        self.assertEqual(status, 404)

    def test_upload_consent_size_and_single_use_are_enforced(self):
        payload = b"not a real clip"
        values = {"filename": "clip.mp4", "size_bytes": len(payload), "content_type": "video/mp4", "consent": False}
        status, _, _ = self.own("POST", "/api/v1/assets/upload-url", values)
        self.assertEqual(status, 422)
        status, asset, _ = self.own("POST", "/api/v1/assets/upload-url", {**values, "consent": True})
        self.assertEqual(status, 201)
        status, _, _ = self.own("PUT", asset["upload_url"], raw=payload)
        self.assertEqual(status, 200)
        status, _, _ = self.own("PUT", asset["upload_url"], raw=payload)
        self.assertEqual(status, 409)
        source = self.app.media.asset_path(self.app.store.get("assets", asset["id"], self.session_id))
        self.assertEqual(source.read_bytes(), payload)

    def test_unvalidated_upload_cannot_create_plan(self):
        _, asset, _ = self.own("POST", "/api/v1/assets/upload-url", {"filename": "clip.mp4", "size_bytes": 12, "content_type": "video/mp4", "consent": True})
        status, response, _ = self.own("POST", "/api/v1/edit-plans", {"asset_id": asset["id"]})
        self.assertEqual(status, 409)
        self.assertEqual(response["error"]["code"], "ASSET_NOT_READY")

    def test_protected_asset_is_inaccessible_to_other_session(self):
        _, asset, _ = self.own("POST", "/api/v1/assets/upload-url", {"filename": "clip.mp4", "size_bytes": 12, "content_type": "video/mp4", "consent": True})
        _, _, headers = self.request("POST", "/api/v1/sessions", {})
        status, _, _ = self.request("GET", "/api/v1/assets/" + asset["id"], cookie=headers["Set-Cookie"].split(";", 1)[0])
        self.assertEqual(status, 404)

    def test_approval_and_job_are_idempotent_over_http(self):
        asset_id = uuid.uuid4().hex
        self.app.store.put("assets", {"id": asset_id, "session_id": self.session_id, "validation_status": "valid", "duration_seconds": 30, "has_audio": True})
        status, plan, _ = self.own("POST", "/api/v1/edit-plans", {"asset_id": asset_id, "title_text": "My setup"})
        self.assertEqual(status, 201)
        values = {"plan_version": plan["version"], "idempotency_key": uuid.uuid4().hex}
        with patch.object(self.app.tools, "status", return_value={"ready": True}), patch.object(self.app.media, "dispatch") as dispatch:
            status, job, _ = self.own("POST", f"/api/v1/edit-plans/{plan['id']}/approve", values)
            self.assertEqual(status, 202)
            _, repeated, _ = self.own("POST", f"/api/v1/edit-plans/{plan['id']}/approve", values)
            self.assertEqual(repeated["id"], job["id"])
            self.assertEqual(dispatch.call_count, 1)
        status, _, _ = self.own("GET", f"/api/v1/jobs/{job['id']}/downloads")
        self.assertEqual(status, 409)

    def test_unsafe_json_numbers_are_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("PUT", "/api/v1/session", body=b'{"budget":NaN}', headers={"Origin": self.origin, "Cookie": self.cookie, "X-CSRF-Token": self.csrf, "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        connection.close()

    def test_passcode_is_required_when_configured(self):
        with patch.object(self.app, "passcode", "test-only-demo-code"):
            status, response, _ = self.request("POST", "/api/v1/sessions", {})
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "ACCESS_CODE_REQUIRED")


if __name__ == "__main__":
    unittest.main()
