import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.core import ButlerError, Catalog, Store, money_minor, parse_shopping_request, validate_plan


ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(ROOT / "data" / "products.json")

    def test_catalog_contains_thirty_explicitly_sample_products(self):
        self.assertEqual(len(self.catalog.products), 30)
        self.assertEqual(self.catalog.metadata["mode"], "sample")

    def test_budget_is_enforced_in_minor_units(self):
        products = self.catalog.search("microphones", 500000, "INR")
        self.assertEqual(len(products), 8)
        self.assertTrue(all(product["price_minor"] <= 500000 for product in products))
        self.assertEqual(products[0]["id"], "mic-05")

    def test_currency_is_not_converted(self):
        self.assertEqual(self.catalog.search("microphones", 500000, "USD"), [])

    def test_empty_results_are_honest(self):
        self.assertEqual(self.catalog.search("microphones", 500, "INR"), [])

    def test_unknown_and_duplicate_comparison_ids_are_rejected(self):
        for product_ids in (["invented"], ["mic-01", "mic-01"], ["mic-01"] * 4):
            with self.assertRaises(ButlerError):
                self.catalog.compare(product_ids)

    def test_unknown_attributes_stay_unknown(self):
        self.assertEqual(self.catalog.compare(["mic-01"])[0]["specifications"]["Monitoring"], "Unknown")

    def test_money_and_chat_budget_parsing(self):
        self.assertEqual(money_minor("4,999.50"), 499950)
        preferences = {"currency": "INR", "budget_minor": None}
        for message in ("Find a microphone under 5k", "Find a mic under \u20b95,000", "microphones budget 5000"):
            parsed = parse_shopping_request(message, preferences)
            self.assertEqual(parsed["budget_minor"], 500000)
            self.assertEqual(parsed["category"], "microphones")

    def test_currency_change_clears_old_budget(self):
        parsed = parse_shopping_request("Find headphones in USD", {"currency": "INR", "budget_minor": 500000})
        self.assertEqual(parsed["currency"], "USD")
        self.assertIsNone(parsed["budget_minor"])

    def test_invalid_money_rejected(self):
        for value in (True, "NaN", "Infinity", -1, "not money"):
            with self.assertRaises(ButlerError):
                money_minor(value)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "test.sqlite3")
        self.token, self.session = self.store.create_session()
        self.other_token, self.other_session = self.store.create_session()

    def tearDown(self):
        self.directory.cleanup()

    def plan(self):
        return self.store.put("plans", {"id": "plan-one", "session_id": self.session["id"], "version": 1, "status": "proposed"})

    def test_session_survives_store_restart(self):
        restarted = Store(self.store.path)
        self.assertEqual(restarted.session_for_token(self.token)["id"], self.session["id"])
        self.assertIsNone(restarted.session_for_token("made-up-token"))

    def test_resources_are_owned_by_session(self):
        self.plan()
        with self.assertRaises(ButlerError) as caught:
            self.store.get("plans", "plan-one", self.other_session["id"])
        self.assertEqual(caught.exception.status, 404)

    def test_approval_is_idempotent_under_concurrency(self):
        self.plan()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.store.approve(self.session["id"], "plan-one", 1, "approval-key-one"), range(4)))
        self.assertEqual(len({result[0]["id"] for result in results}), 1)
        self.assertEqual(sum(result[1] for result in results), 1)
        self.assertEqual(len(self.store.list("jobs", self.session["id"])), 1)

    def test_approved_plan_cannot_create_second_job(self):
        self.plan()
        self.store.approve(self.session["id"], "plan-one", 1, "approval-key-one")
        with self.assertRaises(ButlerError):
            self.store.approve(self.session["id"], "plan-one", 1, "different-key")

    def test_stale_approval_is_rejected(self):
        self.plan()
        with self.assertRaises(ButlerError) as caught:
            self.store.approve(self.session["id"], "plan-one", 2, "approval-key-one")
        self.assertEqual(caught.exception.code, "STALE_PLAN")

    def test_worker_can_claim_job_only_once(self):
        self.plan()
        job, _ = self.store.approve(self.session["id"], "plan-one", 1, "approval-key-one")
        self.assertIsNotNone(self.store.claim_job(job["id"], self.session["id"]))
        self.assertIsNone(self.store.claim_job(job["id"], self.session["id"]))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.asset = {"id": "asset-one", "validation_status": "valid", "duration_seconds": 30, "has_audio": True}

    def test_trim_is_validated(self):
        for start, end in ((-1, 20), (20, 10), (0, 31), (float("nan"), 20), (True, 20)):
            with self.assertRaises(ButlerError):
                validate_plan(self.asset, {"trim_start_seconds": start, "trim_end_seconds": end})

    def test_unvalidated_asset_is_rejected(self):
        with self.assertRaises(ButlerError):
            validate_plan({**self.asset, "validation_status": "pending"}, {})

    def test_no_audio_has_explicit_behavior(self):
        with self.assertRaises(ButlerError) as caught:
            validate_plan({**self.asset, "has_audio": False}, {"subtitles_enabled": True})
        self.assertEqual(caught.exception.code, "NO_AUDIO")

    def test_title_is_data_not_a_command(self):
        plan = validate_plan(self.asset, {"title_text": "hello'; shell command", "fit_mode": "center_crop"})
        self.assertEqual(plan["title_text"], "hello'; shell command")
        self.assertEqual(plan["output_preset"], "vertical_720p")


if __name__ == "__main__":
    unittest.main()