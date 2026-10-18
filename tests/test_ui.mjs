import assert from "node:assert/strict";
import { test } from "node:test";
import { marketplaceLinks, similarProductQuery, budgetMinor, comparisonRows, durationLabel, escapeHtml, formatMoney, mediaSetupMessage, parseHashtags, previewWindow, selectedIds } from "../static/ui.mjs";

test("untrusted product names and messages are escaped", () => {
  assert.equal(escapeHtml('<img onerror="bad">&'), "&lt;img onerror=&quot;bad&quot;&gt;&amp;");
});

test("budgets become minor units without silently converting currencies", () => {
  assert.equal(budgetMinor("4,999.50"), 499950);
  assert.equal(budgetMinor(""), null);
  assert.throws(() => budgetMinor("oops"));
  assert.throws(() => budgetMinor(-1));
  assert.match(formatMoney(499900, "INR"), /4,999/);
});

test("comparison preserves unknown facts", () => {
  assert.deepEqual(comparisonRows([{ specifications: { Connection: "USB-C" } }, { specifications: { Pattern: "Cardioid" } }]), [
    { name: "Connection", values: ["USB-C", "Unknown"] },
    { name: "Pattern", values: ["Unknown", "Cardioid"] }
  ]);
});

test("comparison selection cannot exceed three", () => {
  assert.deepEqual(selectedIds(["one", "two"], "three", true), ["one", "two", "three"]);
  assert.throws(() => selectedIds(["one", "two", "three"], "four", true));
  assert.deepEqual(selectedIds(["one", "two"], "one", false), ["two"]);
});

test("hashtags are validated and deduplicated", () => {
  assert.deepEqual(parseHashtags("Cooking #Creator Cooking"), ["#Cooking", "#Creator"]);
  assert.throws(() => parseHashtags("<script>"));
});

test("clip durations are stable and human readable", () => {
  assert.equal(durationLabel(20), "20.0 s");
  assert.equal(durationLabel(NaN), "Unknown");
});

test("trim preview respects the selected range while summarize preview keeps the full clip", () => {
  assert.deepEqual(previewWindow("trim", 4, 18, 25), { start: 4, end: 18 });
  assert.deepEqual(previewWindow("summarize", 4, 18, 25), { start: 0, end: 25 });
  assert.deepEqual(previewWindow("trim", -3, 80, 25), { start: 0, end: 25 });
  assert.deepEqual(previewWindow("trim", 20, 10, 25), { start: 20, end: 20 });
});

test("media setup explains exactly which creator features are blocked", () => {
  assert.equal(
    mediaSetupMessage({ ffmpeg: false, ffprobe: false, font: true, ready: false }),
    "Missing FFmpeg and FFprobe. Clip inspection and video rendering are unavailable. Creator Pack captions and caption downloads still work. Configure the approved tools and restart Butler."
  );
  assert.equal(
    mediaSetupMessage({ ffmpeg: true, ffprobe: true, font: false, ready: false }),
    "Missing a title/subtitle font. Video rendering is unavailable. Creator Pack captions and caption downloads still work. Configure the approved tools and restart Butler."
  );
  assert.equal(mediaSetupMessage({ ffmpeg: true, ffprobe: true, font: true, ready: true }), "");
});


test("marketplace searches encode input and keep fictional names out of retailer queries", () => {
  const query = '<script>& headphones / USB-C';
  const links = marketplaceLinks(query);
  assert.equal(new URL(links[0].url).hostname, "www.amazon.in");
  assert.equal(new URL(links[0].url).searchParams.get("k"), query);
  assert.equal(new URL(links[1].url).hostname, "www.flipkart.com");
  assert.equal(new URL(links[1].url).searchParams.get("q"), query);
  assert.equal(similarProductQuery({name: "Imaginary Name", image: "microphone", specifications: {Connection: "USB-C"}}), "microphone USB-C");
  assert.equal(new URL(marketplaceLinks("")[0].url).searchParams.get("k"), "creator equipment");
});
