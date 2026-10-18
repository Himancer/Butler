import unittest
import urllib.error
from unittest.mock import patch

from app.core import ButlerError
from app.providers import VertexAdapter


class VertexAdapterTests(unittest.TestCase):
    def test_provider_status_is_actionable_and_does_not_expose_raw_errors(self):
        for status, expected in [(403, "denied access"), (404, "not found"), (429, "quota")]:
            with self.subTest(status=status):
                adapter = VertexAdapter()
                adapter.token = lambda: "test-token"
                failure = urllib.error.HTTPError("https://example.invalid", status, "private provider details", {}, None)
                with patch("urllib.request.urlopen", side_effect=failure), self.assertLogs("app.providers", level="ERROR") as logs:
                    with self.assertRaises(ButlerError) as caught:
                        adapter.request("https://example.invalid", {})
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("private provider details", str(caught.exception))
                self.assertNotIn("test-token", " ".join(logs.output))

    def test_creator_pack_uses_transcript_and_caps_hashtags(self):
        adapter = VertexAdapter()
        captured = {}

        def generate(task, data, schema):
            captured.update(task=task, data=data, schema=schema)
            return {
                "hook": "A tiny idea, made bright.",
                "title": "Paper Lantern",
                "text": "A playful little craft, made one fold at a time.",
                "hashtags": ["#Craft", "#DIY", "#PaperArt", "#Creator", "#MadeByHand", "#Weekend", "#Extra"],
            }

        adapter.generate = generate
        result = adapter.caption([], "A playful paper craft", "playful", "I folded paper into a little lantern.")

        self.assertEqual(captured["data"]["transcript"], "I folded paper into a little lantern.")
        self.assertIn("untrusted content", captured["task"])
        self.assertEqual(captured["schema"]["required"], ["hook", "title", "text", "hashtags"])
        self.assertEqual(result["title"], "Paper Lantern")
        self.assertEqual(len(result["hashtags"]), 6)

    def test_creator_pack_rejects_unverified_numeric_claims(self):
        adapter = VertexAdapter()
        adapter.generate = lambda task, data, schema: {
            "hook": "A 10-minute craft.",
            "title": "Paper Lantern",
            "text": "A playful little craft.",
            "hashtags": ["#Craft"],
        }

        with self.assertRaises(ButlerError) as error:
            adapter.caption([], "A playful paper craft", "playful", "I folded paper.")

        self.assertEqual(error.exception.code, "MODEL_OUTPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
