"""Firestore adapter contract tests using an in-memory SDK stand-in.

These tests exercise persistence and ownership behavior; they do not prove a
Google Cloud project, IAM policy, or live Firestore database is configured.
"""
from __future__ import annotations

import sys
import types
import unittest
import os
import hashlib
from unittest.mock import patch

from app.core import ButlerError


class AlreadyExists(Exception):
    pass


class Snapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self.id = reference.id
        self._data = data
        self.exists = data is not None

    def get(self, key):
        return self._data[key]

    def to_dict(self):
        return dict(self._data)


class Document:
    def __init__(self, collection, identifier):
        self.collection = collection
        self.id = identifier

    def get(self, transaction=None):
        return Snapshot(self, self.collection.rows.get(self.id))

    def set(self, data):
        self.collection.rows[self.id] = dict(data)

    def create(self, data):
        if self.id in self.collection.rows:
            raise AlreadyExists()
        self.set(data)

    def update(self, values):
        self.collection.rows[self.id].update(values)

    def delete(self):
        self.collection.rows.pop(self.id, None)


class Query:
    def __init__(self, collection, filters=()):
        self.collection = collection
        self.filters = filters

    def where(self, field, operator, value):
        return Query(self.collection, (*self.filters, (field, operator, value)))

    def stream(self):
        for identifier, data in list(self.collection.rows.items()):
            if all(data.get(field) <= value if operator == "<=" else data.get(field) == value for field, operator, value in self.filters):
                yield Snapshot(Document(self.collection, identifier), data)


class Collection(Query):
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else {}
        super().__init__(self)

    def document(self, identifier):
        return Document(self, identifier)


class Batch:
    def __init__(self):
        self.operations = []

    def set(self, reference, data):
        self.operations.append((reference.set, (data,)))

    def delete(self, reference):
        self.operations.append((reference.delete, ()))

    def commit(self):
        for operation, arguments in self.operations:
            operation(*arguments)


class Transaction:
    def get(self, reference):
        return reference.get(transaction=self)

    def update(self, reference, values):
        reference.update(values)

    def set(self, reference, data):
        reference.set(data)

    def create(self, reference, data):
        reference.create(data)


class Client:
    def __init__(self, project=None):
        self.project = project
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, Collection())

    def batch(self):
        return Batch()

    def transaction(self):
        return Transaction()


def fake_google_modules():
    google = types.ModuleType("google")
    google.__path__ = []
    cloud = types.ModuleType("google.cloud")
    cloud.__path__ = []
    firestore = types.ModuleType("google.cloud.firestore")
    firestore.Client = Client
    firestore.transactional = lambda function: function
    api_core = types.ModuleType("google.api_core")
    api_core.__path__ = []
    exceptions = types.ModuleType("google.api_core.exceptions")
    exceptions.AlreadyExists = AlreadyExists
    return {
        "google": google,
        "google.cloud": cloud,
        "google.cloud.firestore": firestore,
        "google.api_core": api_core,
        "google.api_core.exceptions": exceptions,
    }


class FirestoreStoreTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, fake_google_modules())
        self.modules.start()
        from app.firestore_store import FirestoreStore
        self.store = FirestoreStore("demo-project")

    def tearDown(self):
        self.modules.stop()

    def test_session_token_and_records_survive_as_owned_firestore_documents(self):
        token, session = self.store.create_session()
        self.assertEqual(self.store.client.project, "demo-project")
        self.assertEqual(self.store.session_for_token(token)["id"], session["id"])
        record = {"id": "asset-1", "session_id": session["id"], "name": "owned clip"}
        self.store.put("assets", record)
        self.assertEqual(self.store.get("assets", "asset-1", session["id"]), record)
        with self.assertRaises(ButlerError) as error:
            self.store.get("assets", "asset-1", "another-session")
        self.assertEqual(error.exception.code, "NOT_FOUND")

    def test_session_updates_and_chat_idempotency_are_persisted(self):
        _, session = self.store.create_session()
        updated = self.store.update_session(session["id"], {"saved_product_ids": ["mic-01"]})
        self.assertEqual(updated["saved_product_ids"], ["mic-01"])
        response = {"reply": "saved"}
        self.store.save_chat(session["id"], "request_123", response)
        self.assertEqual(self.store.cached_chat(session["id"], "request_123"), response)

    def test_expiry_removes_only_the_expired_session_records_and_token(self):
        token, session = self.store.create_session()
        self.store.put("assets", {"id": "asset-2", "session_id": session["id"]})
        token_hash = next(iter(self.store.client.collection("session_tokens").rows))
        self.store.client.collection("sessions").rows[session["id"]]["expires_at"] = 0
        self.assertEqual(self.store.expire_sessions(), [session["id"]])
        self.assertFalse(self.store.client.collection("session_tokens").rows)
        self.assertFalse(self.store.client.collection("sessions").rows)
        self.assertFalse(self.store.list("assets", session["id"]))
        self.assertEqual(token_hash, hashlib.sha256(token.encode()).hexdigest())

    def test_cloud_run_refuses_sqlite_instead_of_silently_using_ephemeral_storage(self):
        from app.server import ButlerApplication
        with patch.dict(os.environ, {"K_SERVICE": "butler-api", "BUTLER_STORAGE": "sqlite"}):
            with self.assertRaisesRegex(ValueError, "requires BUTLER_STORAGE=firestore"):
                ButlerApplication()


if __name__ == "__main__":
    unittest.main()
