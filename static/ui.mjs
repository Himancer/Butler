export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

export function formatMoney(minor, currency = "INR") {
  return new Intl.NumberFormat("en-IN", {
    style: "currency", currency, minimumFractionDigits: minor % 100 ? 2 : 0,
    maximumFractionDigits: 2
  }).format(minor / 100);
}

export function budgetMinor(value) {
  if (value === "" || value === null || value === undefined) return null;
  const number = Number(String(value).replaceAll(",", ""));
  if (!Number.isFinite(number) || number < 0 || number > 1000000) {
    throw new Error("Budget must be between 0 and 1,000,000.");
  }
  return Math.round(number * 100);
}

export function comparisonRows(products) {
  const names = [...new Set(products.flatMap(product => Object.keys(product.specifications)))];
  return names.map(name => ({ name, values: products.map(product => product.specifications[name] || "Unknown") }));
}

export function parseHashtags(value) {
  const tags = [...new Set(String(value).trim().split(/\s+/).filter(Boolean).map(tag => tag.startsWith("#") ? tag : `#${tag}`))];
  if (tags.length > 10 || tags.some(tag => !/^#[\p{L}\p{N}_]{1,40}$/u.test(tag))) {
    throw new Error("Use up to ten hashtags containing letters, numbers, or underscores.");
  }
  return tags;
}

export function selectedIds(current, productId, selected) {
  const result = current.filter(identifier => identifier !== productId);
  if (selected) {
    if (result.length >= 3) throw new Error("You can compare up to three products.");
    result.push(productId);
  }
  return result;
}

export function shortTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

export function durationLabel(seconds) {
  if (!Number.isFinite(Number(seconds))) return "Unknown";
  return `${Number(seconds).toFixed(1)} s`;
}

export function previewWindow(workflow, trimStart, trimEnd, duration) {
  const total = Number(duration);
  const clipEnd = Number.isFinite(total) && total > 0 ? total : 0;
  if (workflow !== "trim" || !clipEnd) return { start: 0, end: clipEnd };
  const startValue = Number(trimStart);
  const endValue = Number(trimEnd);
  const start = Number.isFinite(startValue) ? Math.max(0, Math.min(clipEnd, startValue)) : 0;
  const boundedEnd = Number.isFinite(endValue) ? Math.max(0, Math.min(clipEnd, endValue)) : clipEnd;
  return { start, end: Math.max(start, boundedEnd) };
}

export function mediaSetupMessage(media) {
  if (media?.ready) return "";

  const missing = [
    !media?.ffmpeg && "FFmpeg",
    !media?.ffprobe && "FFprobe",
    !media?.font && "a title/subtitle font"
  ].filter(Boolean);
  const unavailable = [];
  if (!media?.ffprobe) unavailable.push("clip inspection");
  if (!media?.ffmpeg || !media?.ffprobe || !media?.font) unavailable.push("video rendering");
  const missingLabel = missing.length ? missing.join(" and ") : "a required media tool";
  const unavailableLabel = unavailable.join(" and ");
  const unavailableSentence = unavailableLabel.charAt(0).toUpperCase() + unavailableLabel.slice(1);

  return `Missing ${missingLabel}. ${unavailableSentence} ${unavailable.length === 1 ? "is" : "are"} unavailable. Creator Pack captions and caption downloads still work. Configure the approved tools and restart Butler.`;
}
