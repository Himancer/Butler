import assert from "node:assert/strict";
import { test } from "node:test";
import { marketplaceLinks, similarProductQuery, budgetMinor, comparisonRows, durationLabel, escapeHtml, formatMoney, mediaSetupMessage, parseHashtags, previewWindow, selectedIds } from "../static/ui.mjs";
import { defaultEdit, jobMatchesAsset, studioSelection } from "../static/studio.mjs";

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

test("a new clip never restores another clip's successful render", () => {
  const assets = [{ id: "old", duration_seconds: 30 }, { id: "new", duration_seconds: 8 }];
  const plans = [{ id: "old-plan", asset_id: "old", trim_start_seconds: 3, trim_end_seconds: 20 }];
  const jobs = [{ id: "old-output", plan_id: "old-plan", status: "succeeded" }];
  const selected = studioSelection(assets, plans, jobs, "new");
  assert.equal(selected.asset.id, "new");
  assert.equal(selected.job, null);
  assert.equal(selected.plan, null);
  assert.equal(selected.videoWorkflow, null);
  assert.equal(selected.edit.trim_start_seconds, 0);
  assert.equal(selected.edit.trim_end_seconds, 8);
  assert.equal(jobMatchesAsset(jobs[0], assets[1], plans), false);
  assert.equal(jobMatchesAsset(jobs[0], assets[0], plans), true);
});

test("opening a historical render restores that render's exact plan", () => {
  const assets = [{ id: "clip", duration_seconds: 30 }];
  const plans = [
    { id: "first", asset_id: "clip", trim_start_seconds: 2, trim_end_seconds: 7, title_text: "First edit", created_at: 1 },
    { id: "second", asset_id: "clip", trim_start_seconds: 0, trim_end_seconds: 30, shorten_duration_seconds: 12, title_text: "New edit", created_at: 2 }
  ];
  const jobs = [{ id: "first-job", plan_id: "first", created_at: 1 }, { id: "second-job", plan_id: "second", created_at: 2 }];
  const historical = studioSelection(assets, plans, jobs, "clip", "first-job");
  assert.equal(historical.job.id, "first-job");
  assert.equal(historical.plan.id, "first");
  assert.equal(historical.edit.title_text, "First edit");
  assert.equal(historical.edit.trim_start_seconds, 2);
  assert.equal(historical.edit.trim_end_seconds, 7);
  assert.equal(historical.videoWorkflow, "trim");
  const recent = studioSelection(assets, plans, jobs, "clip");
  assert.equal(recent.job.id, "second-job");
  assert.equal(recent.videoWorkflow, "summarize");
});

test("clearing selection and requesting an unrelated job cannot revive old output", () => {
  const assets = [{ id: "clip", duration_seconds: 8 }];
  const plans = [{ id: "plan", asset_id: "clip" }, { id: "other-plan", asset_id: "other" }];
  const jobs = [{ id: "job", plan_id: "plan" }, { id: "other-job", plan_id: "other-plan" }];
  const cleared = studioSelection(assets, plans, jobs, null);
  assert.equal(cleared.asset, null);
  assert.equal(cleared.job, null);
  assert.equal(cleared.plan, null);
  assert.equal(cleared.edit.title_text, "");
  assert.equal(studioSelection(assets, plans, jobs, "clip", "other-job").job, null);
  assert.equal(jobMatchesAsset(null, assets[0], plans), false);
  assert.equal(jobMatchesAsset(jobs[0], null, plans), false);
});

test("new edit defaults reset clip-specific fields and keep trims inside the clip", () => {
  const first = defaultEdit({ duration_seconds: 3.5 });
  assert.equal(first.trim_start_seconds, 0);
  assert.equal(first.trim_end_seconds, 3.5);
  assert.equal(first.shorten_target_seconds, 3.5);
  first.title_text = "Previous title";
  first.transcript = "Previous transcript";
  const second = defaultEdit({ duration_seconds: 60 });
  assert.equal(second.title_text, "");
  assert.equal(second.transcript, "");
  assert.equal(second.trim_end_seconds, 20);
  const restored = defaultEdit({ duration_seconds: 8 }, { trim_start_seconds: -2, trim_end_seconds: 50 });
  assert.equal(restored.trim_start_seconds, 0);
  assert.equal(restored.trim_end_seconds, 8);
});

import './test_studio_runtime.mjs';
