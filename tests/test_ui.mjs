import assert from "node:assert/strict";
import { test } from "node:test";
import { budgetMinor, comparisonRows, durationLabel, escapeHtml, formatMoney, parseHashtags, selectedIds } from "../static/ui.mjs";

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