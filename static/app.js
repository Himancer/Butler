import { budgetMinor, comparisonRows, durationLabel, escapeHtml, formatMoney, parseHashtags, selectedIds, shortTime } from "./ui.mjs";
import { productArt } from "./art.mjs";

const element = identifier => document.getElementById(identifier);
const icon = name => `<span class="icon icon-${name}" aria-hidden="true"></span>`;
const state = {
  page: "discover", session: null, capabilities: null, products: [], allProducts: [],
  messages: [], assets: [], plans: [], jobs: [], captions: [], asset: null, plan: null,
  job: null, caption: null, busyChat: false, uploading: false, localPreview: null,
  approvalKey: null, pendingMessage: null, polling: null,
  edit: { trim_start_seconds: 0, trim_end_seconds: 20, fit_mode: "letterbox", title_text: "", subtitles_enabled: false, transcript: "" }
};
let toastTimer;

function toast(message) {
  clearTimeout(toastTimer);
  element("toast-region").innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  toastTimer = setTimeout(() => { element("toast-region").textContent = ""; }, 4500);
}

async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (state.session && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.session.csrf_token;
  let response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options, headers, credentials: "same-origin",
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined
    });
  } catch {
    throw new Error("Butler is unreachable. Check that the local server is running.");
  }
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || "This request could not be completed.");
    error.code = data.error?.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

async function persistSession(updates) {
  const result = await api("/session", { method: "PUT", body: updates });
  state.session = result.session;
  element("saved-count").textContent = state.session.saved_product_ids.length;
}

function setPage(page) {
  if (!["discover", "create", "saved", "activity"].includes(page)) return;
  state.page = page;
  for (const section of document.querySelectorAll("main > .page")) section.hidden = section.id !== `page-${page}`;
  for (const button of document.querySelectorAll("[data-page]")) {
    const active = button.dataset.page === page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  element("page-name").textContent = page[0].toUpperCase() + page.slice(1);
  history.replaceState(null, "", `#${page}`);
  if (page === "saved") renderSaved();
  if (page === "activity") renderActivity();
  if (page === "create") renderCreate();
}

function renderMessages() {
  const messages = state.messages.length ? state.messages : [{
    role: "assistant", content: "A new project? A better setup? Tell me what you're looking for and your budget. We'll start there.", created_at: Date.now() / 1000, referenced_product_ids: []
  }];
  element("chat-stream").innerHTML = messages.map(message => `<article class="message ${message.role === "user" ? "user" : "assistant"}"><div class="message-meta">${message.role === "user" ? icon("message") : icon("spark")}<strong>${message.role === "user" ? "You" : "Butler"}</strong><time>${shortTime(message.created_at)}</time></div><div class="message-text">${escapeHtml(message.content)}</div>${message.referenced_product_ids?.length ? `<div class="message-products">${message.referenced_product_ids.map(identifier => `<button class="message-product" data-message-product="${escapeHtml(identifier)}">${escapeHtml(state.allProducts.find(product => product.id === identifier)?.name || identifier)}</button>`).join("")}</div>` : ""}</article>`).join("") + (state.busyChat ? `<div class="thinking">Working on that...</div>` : "");
  element("chat-stream").scrollTop = element("chat-stream").scrollHeight;
  element("quick-prompts").hidden = state.messages.length > 0;
  element("send-message").disabled = state.busyChat || !state.session;
}

function productCard(product) {
  const saved = state.session?.saved_product_ids.includes(product.id);
  const selected = state.session?.selected_product_ids.includes(product.id);
  const fact = product.specifications.Connection || product.specifications.Mount || product.specifications.Power || "Details unknown";
  return `<article class="product-card ${selected ? "selected" : ""}" data-product-id="${escapeHtml(product.id)}"><div class="product-visual ${["mint", "peach", "lavender"].includes(product.accent) ? product.accent : "mint"}"><div class="product-visual-top"><label class="product-checkbox" title="Select ${escapeHtml(product.name)} for comparison"><input type="checkbox" data-compare-id="${escapeHtml(product.id)}" aria-label="Compare ${escapeHtml(product.name)}" ${selected ? "checked" : ""}></label><button class="icon-button bookmark-button ${saved ? "saved" : ""}" data-save-id="${escapeHtml(product.id)}" aria-label="${saved ? "Unsave" : "Save"} ${escapeHtml(product.name)}" title="${saved ? "Unsave" : "Save"} ${escapeHtml(product.name)}" aria-pressed="${Boolean(saved)}">${icon("bookmark")}</button></div><img src="${productArt(product.image)}" alt="Illustration of fictional ${escapeHtml(product.name)}" width="640" height="480"><span class="product-tag">${escapeHtml(product.tag)}</span></div><div class="product-body"><div class="product-category">${escapeHtml(product.category)}</div><h3>${escapeHtml(product.name)}</h3><p class="product-fact">${escapeHtml(fact)}</p><div class="product-bottom"><strong class="product-price">${escapeHtml(formatMoney(product.price_minor, product.currency))}</strong><button class="icon-button" data-detail-id="${escapeHtml(product.id)}" aria-label="View ${escapeHtml(product.name)} details" title="View details">${icon("arrow-right")}</button></div></div></article>`;
}

function emptyState(symbol, heading, detail = "") {
  return `<div class="empty-state">${icon(symbol)}<h3>${escapeHtml(heading)}</h3>${detail ? `<p>${escapeHtml(detail)}</p>` : ""}</div>`;
}

function renderProducts() {
  element("product-grid").innerHTML = state.products.length ? state.products.map(productCard).join("") : emptyState("search", "No match this time.", "No sample products meet these filters.");
  element("result-count").textContent = `${state.products.length} ${state.products.length === 1 ? "result" : "results"}`;
  const count = state.session?.selected_product_ids.length || 0;
  element("compare-toolbar").hidden = count === 0;
  element("compare-count").textContent = `${count} selected`;
  element("open-comparison").disabled = count === 0;
}

function renderSaved() {
  const saved = state.allProducts.filter(product => state.session?.saved_product_ids.includes(product.id));
  element("saved-grid").innerHTML = saved.length ? saved.map(productCard).join("") : emptyState("bookmark", "A little room for good finds.", "Your shortlist is empty.");
  element("saved-total").textContent = `${saved.length} products`;
  element("saved-count").textContent = saved.length;
}

async function loadProducts() {
  const budget = budgetMinor(element("budget-filter").value);
  const category = element("category-filter").value;
  const currency = element("currency-filter").value;
  const query = new URLSearchParams({ currency });
  if (category) query.set("category", category);
  if (budget !== null) query.set("max_price_minor", budget);
  const data = await api(`/products?${query}`);
  state.products = data.products;
  if (state.session) await persistSession({ preferences: { ...state.session.preferences, category: category || state.session.preferences.category, budget_minor: budget, currency } });
  renderProducts();
}

async function openComparison(productIds = state.session.selected_product_ids) {
  const data = await api("/products/compare", { method: "POST", body: { product_ids: productIds } });
  const products = data.products;
  const rows = comparisonRows(products);
  element("comparison-content").innerHTML = `<div class="comparison-table-wrap"><table class="comparison-table"><thead><tr><th scope="col">Catalog facts</th>${products.map(product => `<th scope="col"><div class="comparison-product-image"><img src="${productArt(product.image)}" alt="Illustration of ${escapeHtml(product.name)}" width="640" height="480"></div>${escapeHtml(product.name)}<span class="comparison-price">${escapeHtml(formatMoney(product.price_minor, product.currency))}</span></th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.name)}</td>${row.values.map(value => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}<tr><td>Source</td>${products.map(() => "<td>Fictional sample catalog</td>").join("")}</tr><tr><td>Price observed</td>${products.map(() => "<td>Unknown</td>").join("")}</tr><tr><td>Availability</td>${products.map(() => "<td>Not checked</td>").join("")}</tr></tbody></table></div>`;
  element("use-selection").dataset.productIds = JSON.stringify(productIds);
  element("compare-dialog").showModal();
}

async function sendChat(message) {
  if (state.busyChat || !message.trim()) return;
  state.busyChat = true;
  if (!state.pendingMessage || state.pendingMessage.message !== message.trim()) state.pendingMessage = { message: message.trim(), client_message_id: crypto.randomUUID() };
  renderMessages();
  try {
    const response = await api("/chat", { method: "POST", body: state.pendingMessage });
    state.messages.push(...response.messages);
    state.pendingMessage = null;
    element("chat-input").value = "";
    const bootstrap = await api("/bootstrap");
    state.session = bootstrap.session;
    if (response.products.length) {
      state.products = response.products;
      element("category-filter").value = state.session.preferences.category;
      element("budget-filter").value = state.session.preferences.budget_minor === null ? "" : state.session.preferences.budget_minor / 100;
      element("currency-filter").value = state.session.preferences.currency;
    } else if (response.intent === "shopping") {
      state.products = [];
      element("currency-filter").value = state.session.preferences.currency;
      element("budget-filter").value = state.session.preferences.budget_minor === null ? "" : state.session.preferences.budget_minor / 100;
    }
    if (response.caption) {
      state.caption = response.caption;
      state.captions = bootstrap.captions;
      renderCaption();
    }
    renderProducts();
    if (["caption", "create"].includes(response.intent)) setPage("create");
  } catch (error) {
    toast(error.message);
  } finally {
    state.busyChat = false;
    renderMessages();
  }
}

function collectEdit() {
  state.edit = {
    trim_start_seconds: Number(element("trim-start").value), trim_end_seconds: Number(element("trim-end").value),
    fit_mode: document.querySelector('input[name="fit_mode"]:checked').value,
    title_text: element("video-title").value, subtitles_enabled: element("subtitles-enabled").checked,
    transcript: element("transcript-input").value
  };
  element("transcript-field").hidden = !state.edit.subtitles_enabled;
  state.plan = null;
  state.approvalKey = null;
  renderPreview();
}

function renderPreview() {
  const finished = state.job?.status === "succeeded";
  const source = finished ? `/api/v1/jobs/${state.job.id}/preview` : state.localPreview || (state.asset?.validation_status === "valid" ? `/api/v1/assets/${state.asset.id}/preview` : "");
  const video = element("video-preview");
  if (source && video.getAttribute("src") !== source) video.src = source;
  video.hidden = !source;
  element("preview-empty").hidden = Boolean(source);
  element("video-frame").classList.toggle("crop", !finished && state.edit.fit_mode === "center_crop");
  element("video-title-overlay").textContent = state.edit.title_text;
  element("video-title-overlay").hidden = !source || !state.edit.title_text || finished;
  element("preview-kind").textContent = finished ? "Rendered output" : "Draft framing";
  element("preview-duration").textContent = state.asset?.duration_seconds ? durationLabel(state.edit.trim_end_seconds - state.edit.trim_start_seconds) : "720 \u00d7 1280";
}

function renderCreate() {
  if (!state.capabilities) return;
  element("media-setup").hidden = state.capabilities.media.ready;
  const asset = state.asset;
  const active = state.job && ["queued", "processing"].includes(state.job.status);
  element("asset-status").textContent = asset ? ({ valid: "Inspected", needs_tools: "Tools required", invalid: "Rejected", validating: "Inspecting", uploaded: "Uploaded", reserved: "Waiting" }[asset.validation_status] || asset.validation_status) : "No clip selected";
  element("upload-title").textContent = asset ? asset.original_filename : "Start with your footage.";
  element("upload-detail").textContent = asset?.duration_seconds ? `${durationLabel(asset.duration_seconds)} \u00b7 ${asset.width} \u00d7 ${asset.height}` : `MP4 \u00b7 up to ${Math.floor(state.capabilities.max_upload_bytes / 1000000)} MB \u00b7 60 seconds`;
  element("choose-file").disabled = Boolean(active || state.uploading);
  element("asset-error").hidden = !asset?.error_message;
  element("asset-error").textContent = asset?.error_message || "";
  element("retry-inspection").hidden = !asset || !["needs_tools", "uploaded"].includes(asset.validation_status);
  element("retry-inspection").disabled = !state.capabilities.media.ffprobe;
  element("trim-start").value = state.edit.trim_start_seconds;
  element("trim-end").value = state.edit.trim_end_seconds;
  element("trim-start").max = asset?.duration_seconds || 60;
  element("trim-end").max = asset?.duration_seconds || 60;
  element("video-title").value = state.edit.title_text;
  element("subtitles-enabled").checked = state.edit.subtitles_enabled;
  element("transcript-input").value = state.edit.transcript;
  for (const radio of document.querySelectorAll('input[name="fit_mode"]')) radio.checked = radio.value === state.edit.fit_mode;
  element("transcript-field").hidden = !state.edit.subtitles_enabled;
  element("transcript-note").textContent = state.capabilities.transcription === "google" ? "Google Speech \u00b7 manual text overrides automatic transcription" : "Manual transcript \u00b7 evenly spaced, approximate timing";
  element("review-plan").disabled = asset?.validation_status !== "valid" || !state.capabilities.media.ready || Boolean(active);
  renderPreview();
  renderJob();
}

function uploadBytes(url, file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    request.timeout = 120000;
    request.setRequestHeader("Content-Type", "video/mp4");
    request.setRequestHeader("X-CSRF-Token", state.session.csrf_token);
    request.upload.addEventListener("progress", event => {
      if (event.lengthComputable) element("upload-progress").value = Math.round(event.loaded / event.total * 100);
    });
    request.addEventListener("load", () => {
      try {
        const result = JSON.parse(request.responseText);
        if (request.status >= 200 && request.status < 300) resolve(result);
        else reject(new Error(result.error?.message || "Upload failed."));
      } catch { reject(new Error("The upload response could not be read.")); }
    });
    request.addEventListener("error", () => reject(new Error("The upload connection failed.")));
    request.addEventListener("timeout", () => reject(new Error("The upload timed out.")));
    request.send(file);
  });
}

async function uploadFile(file) {
  if (!file || state.uploading) return;
  if (!element("media-consent").checked) { toast("Confirm that you own the clip or have permission to use it."); return; }
  const maxUploadBytes = state.capabilities?.max_upload_bytes || 50 * 1024 * 1024;
  if (!file.name.toLowerCase().endsWith(".mp4") || !file.size || file.size > maxUploadBytes) { toast(`Choose one MP4 file of at most ${Math.floor(maxUploadBytes / 1000000)} MB.`); return; }
  state.uploading = true;
  element("upload-progress").value = 0;
  element("upload-progress").hidden = false;
  element("choose-file").disabled = true;
  try {
    const reservation = await api("/assets/upload-url", { method: "POST", body: { filename: file.name, size_bytes: file.size, content_type: "video/mp4", consent: true } });
    state.asset = await uploadBytes(reservation.upload_url, file);
    if (state.localPreview) URL.revokeObjectURL(state.localPreview);
    state.localPreview = URL.createObjectURL(file);
    state.plan = null;
    state.job = null;
    state.approvalKey = null;
    state.asset = await api(`/assets/${reservation.id}/complete`, { method: "POST", body: {} });
    state.assets.push(state.asset);
    renderCreate();
    startPolling();
    toast("Clip uploaded. Server-side inspection started.");
  } catch (error) { toast(error.message); }
  finally {
    state.uploading = false;
    element("upload-progress").hidden = true;
    element("video-file").value = "";
    renderCreate();
  }
}

async function reviewPlan() {
  if (!state.asset || state.asset.validation_status !== "valid") return;
  collectEdit();
  const start = state.edit.trim_start_seconds;
  const end = state.edit.trim_end_seconds;
  if (!Number.isFinite(start) || !Number.isFinite(end) || !(0 <= start && start < end && end <= state.asset.duration_seconds)) { toast("Trim end must follow the start and stay within the clip duration."); return; }
  element("review-plan").disabled = true;
  try {
    state.plan = await api("/edit-plans", { method: "POST", body: { asset_id: state.asset.id, ...state.edit } });
    state.plans.push(state.plan);
    state.approvalKey = crypto.randomUUID();
    const entries = [
      ["Clip", state.asset.original_filename], ["Trim", `${durationLabel(start)} to ${durationLabel(end)}`],
      ["Framing", state.edit.fit_mode === "center_crop" ? "Center crop" : "Letterbox"],
      ["Title", state.edit.title_text || "None"], ["Subtitles", state.edit.subtitles_enabled ? state.edit.transcript ? "Manual transcript" : "Google Speech" : "Off"],
      ["Output", "720 x 1280 \u00b7 H.264/AAC \u00b7 30 fps"], ["Plan version", state.plan.version]
    ];
    element("plan-summary").innerHTML = dataList(entries) + (state.edit.subtitles_enabled && state.edit.transcript ? '<p class="plan-note">Manual transcript timestamps are approximate, not speech-aligned.</p>' : "");
    element("plan-dialog").showModal();
  } catch (error) { toast(error.message); }
  finally { renderCreate(); }
}

async function approvePlan() {
  if (!state.plan || !state.approvalKey) return;
  element("approve-plan").disabled = true;
  try {
    state.job = await api(`/edit-plans/${state.plan.id}/approve`, { method: "POST", body: { plan_version: state.plan.version, idempotency_key: state.approvalKey } });
    state.jobs.push(state.job);
    element("plan-dialog").close();
    renderCreate();
    startPolling();
    toast("Your approved edit is queued.");
  } catch (error) { toast(error.message); }
  finally { element("approve-plan").disabled = false; }
}

function renderJob() {
  const job = state.job;
  element("job-panel").hidden = !job;
  element("export-package").disabled = job?.status !== "succeeded";
  if (!job) { element("artifact-links").innerHTML = ""; return; }
  const active = ["queued", "processing"].includes(job.status);
  const detail = job.error_message || (job.status === "succeeded" ? "Verified output is ready." : job.phase);
  element("job-panel").innerHTML = `<div class="job-heading"><strong>${escapeHtml(job.status)}</strong><span class="subtle-label">Attempt ${job.attempt_count || 1}</span></div><p>${escapeHtml(detail)}</p>${active ? '<div class="job-progress"></div>' : ""}${job.retryable ? `<button class="button button-white" id="retry-job">${icon("refresh")}Retry once</button>` : ""}${job.transcript_source === "manual_approximate_timing" ? '<p class="plan-note">Manual subtitle timing</p>' : ""}`;
  element("artifact-links").innerHTML = job.status === "succeeded" ? job.output_keys.map(filename => `<button data-download-file="${escapeHtml(filename)}"><span>${escapeHtml(filename)}</span>${icon("download")}</button>`).join("") : "";
}

function startPolling() {
  clearTimeout(state.polling);
  state.polling = setTimeout(pollWork, 1000);
}

async function pollWork() {
  let pending = false;
  try {
    if (state.asset?.validation_status === "validating") {
      state.asset = await api(`/assets/${state.asset.id}`);
      state.assets = state.assets.map(asset => asset.id === state.asset.id ? state.asset : asset);
      pending = state.asset.validation_status === "validating";
      if (state.asset.validation_status === "valid") {
        state.edit.trim_end_seconds = Math.min(20, state.asset.duration_seconds);
        toast("Clip inspected. Your edit plan is ready to prepare.");
      } else if (["invalid", "needs_tools"].includes(state.asset.validation_status)) {
        if (state.asset.validation_status === "invalid" && state.localPreview) {
          URL.revokeObjectURL(state.localPreview);
          state.localPreview = null;
          element("video-preview").removeAttribute("src");
        }
        toast(state.asset.error_message);
      }
    }
    if (state.job && ["queued", "processing"].includes(state.job.status)) {
      const previous = state.job.status;
      state.job = await api(`/jobs/${state.job.id}`);
      state.jobs = state.jobs.map(job => job.id === state.job.id ? state.job : job);
      pending = pending || ["queued", "processing"].includes(state.job.status);
      if (previous !== state.job.status && state.job.status === "succeeded") toast("Your creation is ready.");
      if (previous !== state.job.status && state.job.status === "failed") toast(state.job.error_message);
    }
    if (state.page === "create") renderCreate();
    if (state.page === "activity") renderActivity();
  } catch (error) { toast(error.message); pending = true; }
  if (pending) state.polling = setTimeout(pollWork, 2000);
}

function renderCaption() {
  const caption = state.caption;
  const usesGemini = state.capabilities?.model_mode === "vertex";
  element("caption-text").value = caption?.text || "";
  element("caption-hashtags").value = caption?.hashtags.join(" ") || "";
  element("caption-context").value = caption?.user_context || "";
  element("caption-tone").value = caption?.tone || state.session?.preferences.tone || "friendly";
  element("creator-pack-note").textContent = usesGemini
    ? "Gemini uses your brief + transcript to draft the hook, video title, caption and tags. Nothing publishes for you."
    : "Local rules mode creates a deterministic example. Configure Gemini on Vertex AI for transcript-aware Creator Packs.";
  element("creator-privacy").textContent = usesGemini
    ? "When you press Make a Creator Pack, your brief and transcript are sent to Gemini. Review every word."
    : "Local rules mode does not send the brief or transcript to Gemini.";
  element("creator-hook").hidden = !caption?.hook;
  element("creator-hook").textContent = caption?.hook ? `OPENING HOOK  /  ${caption.hook}` : "";
  element("creator-title-suggestion").hidden = !caption?.title;
  element("creator-title-text").textContent = caption?.title || "";
  if (caption?.title && !element("video-title").value.trim()) {
    state.edit.title_text = caption.title;
    element("video-title").value = caption.title;
    renderPreview();
  }
  element("caption-state").textContent = caption ? caption.edited ? "Edited" : caption.provider : "Draft";
}

function creatorPackRequest() {
  return {
    product_ids: state.session.selected_product_ids,
    user_context: element("caption-context").value,
    transcript: element("transcript-input").value,
    tone: element("caption-tone").value
  };
}

async function generateCaption() {
  element("generate-caption").disabled = true;
  try {
    state.caption = await api("/captions", { method: "POST", body: creatorPackRequest() });
    state.captions.push(state.caption);
    renderCaption();
    toast("Creator Pack ready. The hook, title, and caption are all editable.");
  } catch (error) { toast(error.message); }
  finally { element("generate-caption").disabled = false; }
}

async function saveCaption() {
  const text = element("caption-text").value;
  const hashtags = parseHashtags(element("caption-hashtags").value);
  if (!text.trim() && !hashtags.length) throw new Error("Add caption text before saving.");
  if (!state.caption) {
    state.caption = await api("/captions", { method: "POST", body: creatorPackRequest() });
    state.captions.push(state.caption);
  }
  state.caption = await api(`/captions/${state.caption.id}`, { method: "PUT", body: { text, hashtags } });
  state.captions = state.captions.map(caption => caption.id === state.caption.id ? state.caption : caption);
  element("caption-state").textContent = "Saved";
}

async function downloadArtifact(filename) {
  if (element("caption-text").value.trim()) await saveCaption();
  const downloads = await api(`/jobs/${state.job.id}/downloads`);
  const artifact = downloads.artifacts.find(item => item.name === filename);
  if (!artifact) throw new Error("The requested artifact is unavailable.");
  const link = document.createElement("a");
  link.href = artifact.url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
}

function renderActivity() {
  const events = [
    ...state.assets.map(asset => ({ type: "asset", id: asset.id, title: asset.original_filename, detail: "Video upload", status: asset.validation_status, created_at: asset.created_at, symbol: "upload" })),
    ...state.jobs.map(job => ({ type: "job", id: job.id, title: "Vertical video", detail: job.phase, status: job.status, created_at: job.created_at, symbol: "play" })),
    ...state.captions.map(caption => ({ type: "caption", id: caption.id, title: caption.text.slice(0, 75), detail: "Caption draft", status: caption.edited ? "edited" : "draft", created_at: caption.updated_at || caption.created_at, symbol: "file" }))
  ].sort((first, second) => second.created_at - first.created_at);
  element("activity-list").innerHTML = events.length ? events.map(event => `<div class="activity-row"><span class="activity-symbol">${icon(event.symbol)}</span><div class="activity-body"><strong>${escapeHtml(event.title)}</strong><small>${escapeHtml(event.detail)} \u00b7 ${shortTime(event.created_at)}</small></div><span class="status-pill ${["failed", "invalid", "needs_tools"].includes(event.status) ? event.status : ""}">${escapeHtml(event.status.replaceAll("_", " "))}</span><button class="icon-button" data-activity-type="${event.type}" data-activity-id="${event.id}" title="Open item" aria-label="Open ${escapeHtml(event.title)}">${icon("arrow-right")}</button></div>`).join("") : emptyState("history", "A fresh start.", "No uploads, renders, or captions yet.");
}

function dataList(entries) {
  return `<dl class="data-list">${entries.map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
}

function renderStatus() {
  const capabilities = state.capabilities;
  if (!capabilities) return;
  element("status-content").innerHTML = dataList([
    ["Assistant", capabilities.assistant], ["Catalog", "30 fictional sample products"],
    ["FFmpeg", capabilities.media.ffmpeg ? "Configured" : "Not installed"],
    ["FFprobe", capabilities.media.ffprobe ? "Configured" : "Not installed"],
    ["Font", capabilities.media.font ? "Available" : "Not configured"],
    ["Transcription", capabilities.transcription === "google" ? "Google Speech" : "Manual"],
    ["App records", capabilities.storage], ["Video storage", capabilities.media_storage], ["Retention", "24-hour sessions; periodic cleanup"],
    ["Access", capabilities.access_mode], ["Currency", state.session.preferences.currency]
  ]);
  element("access-mode").textContent = capabilities.access_mode;
}

async function refreshWorkspace(initial = false) {
  const data = await api("/bootstrap");
  state.session = data.session;
  state.capabilities = data.capabilities;
  state.messages = data.messages;
  state.assets = data.assets;
  state.plans = data.plans;
  state.jobs = data.jobs;
  state.captions = data.captions;
  if (initial || !state.asset) state.asset = state.assets.at(-1) || null;
  else state.asset = state.assets.find(asset => asset.id === state.asset.id) || null;
  if (initial || !state.job) state.job = state.jobs.at(-1) || null;
  else state.job = state.jobs.find(job => job.id === state.job.id) || null;
  if (initial) {
    state.caption = state.captions.at(-1) || null;
    const plan = state.asset ? [...state.plans].reverse().find(candidate => candidate.asset_id === state.asset.id) : null;
    if (plan) state.edit = { trim_start_seconds: plan.trim_start_seconds, trim_end_seconds: plan.trim_end_seconds, fit_mode: plan.fit_mode, title_text: plan.title_text, subtitles_enabled: plan.subtitles_enabled, transcript: plan.transcript };
    else if (state.asset?.duration_seconds) state.edit.trim_end_seconds = Math.min(20, state.asset.duration_seconds);
    element("category-filter").value = state.session.preferences.category;
    element("currency-filter").value = state.session.preferences.currency;
    element("budget-filter").value = state.session.preferences.budget_minor === null ? "" : state.session.preferences.budget_minor / 100;
    const all = await api("/products");
    state.allProducts = all.products;
    const query = new URLSearchParams({ category: state.session.preferences.category, currency: state.session.preferences.currency });
    if (state.session.preferences.budget_minor !== null) query.set("max_price_minor", state.session.preferences.budget_minor);
    state.products = (await api(`/products?${query}`)).products;
    renderCaption();
  }
  element("provider-badge").innerHTML = `<span class="connection-dot"></span><span>${escapeHtml(state.capabilities.assistant)}</span>`;
  element("session-mode").textContent = state.capabilities.access_mode;
  element("chat-mode").textContent = state.capabilities.assistant;
  element("chat-footnote").textContent = `${state.capabilities.assistant} \u00b7 private session`;
  element("saved-count").textContent = state.session.saved_product_ids.length;
  element("catalog-total").textContent = `${state.allProducts.length} essentials`;
  element("fatal-error").hidden = true;
  renderMessages();
  renderProducts();
  renderSaved();
  renderCreate();
  renderActivity();
  renderStatus();
  if (state.asset?.validation_status === "validating" || ["queued", "processing"].includes(state.job?.status)) startPolling();
}

async function initialize(passcode = "") {
  try {
    const sessionResponse = await api("/sessions", { method: "POST", body: { passcode } });
    state.session = sessionResponse.session;
    if (element("access-dialog").open) element("access-dialog").close();
    element("access-code").value = "";
    await refreshWorkspace(true);
    setPage(location.hash.slice(1) || "discover");
  } catch (error) {
    if (error.code === "ACCESS_CODE_REQUIRED") {
      if (!element("access-dialog").open) element("access-dialog").showModal();
      element("access-error").hidden = !passcode;
      element("access-error").textContent = error.message;
    } else {
      element("fatal-error").hidden = false;
      element("fatal-error").textContent = error.message;
      element("provider-badge").textContent = "Disconnected";
    }
  }
}

document.addEventListener("click", async event => {
  const button = event.target.closest("button");
  if (!button) return;
  try {
    if (button.dataset.page) setPage(button.dataset.page);
    if (button.hasAttribute("data-close-dialog")) button.closest("dialog").close();
    if (button.dataset.prompt) await sendChat(button.dataset.prompt);
    if (button.dataset.saveId) {
      const identifier = button.dataset.saveId;
      const saved = state.session.saved_product_ids;
      await persistSession({ saved_product_ids: saved.includes(identifier) ? saved.filter(item => item !== identifier) : [...saved, identifier] });
      renderProducts(); renderSaved();
      toast(state.session.saved_product_ids.includes(identifier) ? "Added to your shortlist." : "Removed from your shortlist.");
    }
    if (button.dataset.detailId) await openComparison([button.dataset.detailId]);
    if (button.dataset.messageProduct) {
      state.products = state.allProducts.filter(product => product.id === button.dataset.messageProduct);
      renderProducts();
    }
    if (button.dataset.downloadFile) await downloadArtifact(button.dataset.downloadFile);
    if (button.dataset.activityId) {
      if (button.dataset.activityType === "asset") {
        state.asset = state.assets.find(asset => asset.id === button.dataset.activityId);
        if (state.localPreview) URL.revokeObjectURL(state.localPreview);
        state.localPreview = null;
        state.job = state.jobs.find(job => state.plans.find(plan => plan.id === job.plan_id)?.asset_id === state.asset.id) || null;
        state.edit.trim_end_seconds = Math.min(20, state.asset.duration_seconds || 20);
      }
      if (button.dataset.activityType === "job") state.job = state.jobs.find(job => job.id === button.dataset.activityId);
      if (button.dataset.activityType === "caption") { state.caption = state.captions.find(caption => caption.id === button.dataset.activityId); renderCaption(); }
      setPage("create");
    }
    if (button.id === "retry-job") {
      state.job = await api(`/jobs/${state.job.id}/retry`, { method: "POST", body: {} });
      renderCreate(); startPolling();
    }
  } catch (error) { toast(error.message); }
});

document.addEventListener("change", async event => {
  if (!event.target.dataset.compareId) return;
  try {
    const selection = selectedIds(state.session.selected_product_ids, event.target.dataset.compareId, event.target.checked);
    await persistSession({ selected_product_ids: selection });
    renderProducts(); renderSaved();
  } catch (error) { event.target.checked = false; toast(error.message); }
});

element("filter-form").addEventListener("submit", event => { event.preventDefault(); loadProducts().catch(error => toast(error.message)); });
element("category-filter").addEventListener("change", () => loadProducts().catch(error => toast(error.message)));
element("currency-filter").addEventListener("change", () => loadProducts().catch(error => toast(error.message)));
element("chat-form").addEventListener("submit", event => { event.preventDefault(); sendChat(element("chat-input").value); });
element("chat-input").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendChat(event.target.value); } });
element("open-comparison").addEventListener("click", () => openComparison().catch(error => toast(error.message)));
element("clear-comparison").addEventListener("click", async () => { try { await persistSession({ selected_product_ids: [] }); renderProducts(); renderSaved(); } catch (error) { toast(error.message); } });
element("use-selection").addEventListener("click", async () => { try { await persistSession({ selected_product_ids: JSON.parse(element("use-selection").dataset.productIds) }); element("compare-dialog").close(); setPage("create"); toast("Selection added to your caption context."); } catch (error) { toast(error.message); } });
element("choose-file").addEventListener("click", () => { if (!element("media-consent").checked) toast("Confirm that you own the clip or have permission to use it."); else element("video-file").click(); });
element("video-file").addEventListener("change", event => uploadFile(event.target.files[0]));
element("upload-zone").addEventListener("dragover", event => { event.preventDefault(); element("upload-zone").classList.add("dragging"); });
element("upload-zone").addEventListener("dragleave", () => element("upload-zone").classList.remove("dragging"));
element("upload-zone").addEventListener("drop", event => { event.preventDefault(); element("upload-zone").classList.remove("dragging"); if (event.dataTransfer.files.length !== 1) toast("Choose one clip at a time."); else uploadFile(event.dataTransfer.files[0]); });
element("edit-form").addEventListener("input", collectEdit);
element("edit-form").addEventListener("submit", event => { event.preventDefault(); reviewPlan(); });
element("approve-plan").addEventListener("click", approvePlan);
element("retry-inspection").addEventListener("click", async () => {
  try {
    state.asset = await api(`/assets/${state.asset.id}/complete`, { method: "POST", body: {} });
    renderCreate(); startPolling();
  } catch (error) { toast(error.message); }
});
element("video-preview").addEventListener("loadedmetadata", () => { if (state.job?.status !== "succeeded" && state.edit.trim_start_seconds) element("video-preview").currentTime = state.edit.trim_start_seconds; });
element("video-preview").addEventListener("timeupdate", () => { if (state.job?.status !== "succeeded" && element("video-preview").currentTime >= state.edit.trim_end_seconds) { element("video-preview").pause(); element("video-preview").currentTime = state.edit.trim_start_seconds; } });
element("generate-caption").addEventListener("click", generateCaption);
element("use-suggested-title").addEventListener("click", () => {
  if (!state.caption?.title) return;
  element("video-title").value = state.caption.title;
  collectEdit();
  toast("Title idea added to your edit. Keep it or make it yours.");
});
element("save-caption").addEventListener("click", () => saveCaption().then(() => toast("Caption saved.")).catch(error => toast(error.message)));
element("download-caption").addEventListener("click", () => saveCaption().then(() => { const link = document.createElement("a"); link.href = `/api/v1/captions/${state.caption.id}/download`; link.download = "caption.txt"; document.body.append(link); link.click(); link.remove(); }).catch(error => toast(error.message)));
element("export-package").addEventListener("click", () => downloadArtifact("post-package.zip").catch(error => toast(error.message)));
element("workspace-status").addEventListener("click", () => { renderStatus(); element("status-dialog").showModal(); });
element("provider-badge").addEventListener("click", () => { if (state.capabilities) { renderStatus(); element("status-dialog").showModal(); } });
element("status-refresh").addEventListener("click", () => refreshWorkspace().catch(error => toast(error.message)));
element("refresh-workspace").addEventListener("click", () => refreshWorkspace().then(() => toast("Workspace refreshed.")).catch(error => toast(error.message)));
element("access-form").addEventListener("submit", event => { event.preventDefault(); initialize(element("access-code").value); });
element("access-dialog").addEventListener("cancel", event => event.preventDefault());
window.addEventListener("hashchange", () => setPage(location.hash.slice(1)));
initialize();
