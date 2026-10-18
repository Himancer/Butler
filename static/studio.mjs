// Keep an asset and its edit/output selection together. These helpers do not
// mutate history; clearing the studio leaves existing Activity records intact.
export function defaultEdit(asset = null, plan = null) {
  const duration = Number(asset?.duration_seconds);
  const maximum = Number.isFinite(duration) && duration > 0 ? duration : 60;
  const defaults = {
    trim_start_seconds: 0, trim_end_seconds: Math.min(20, maximum),
    fit_mode: "letterbox", title_text: "", subtitles_enabled: false, transcript: "",
    shorten_duration_seconds: 12, shorten_target_seconds: Math.min(12, maximum),
    shorten_strategy: "smart_highlights", shorten_audio: "preserve", shorten_focus: "balanced"
  };
  if (!plan) return defaults;
  const numberWithinClip = (value, fallback) => Number.isFinite(Number(value))
    ? Math.max(0, Math.min(maximum, Number(value))) : fallback;
  const start = numberWithinClip(plan.trim_start_seconds, 0);
  const end = numberWithinClip(plan.trim_end_seconds, defaults.trim_end_seconds);
  return {
    ...defaults,
    trim_start_seconds: start < end ? start : 0,
    trim_end_seconds: end > 0 ? end : defaults.trim_end_seconds,
    fit_mode: plan.fit_mode || defaults.fit_mode,
    title_text: plan.title_text || "",
    subtitles_enabled: Boolean(plan.subtitles_enabled),
    transcript: plan.transcript || "",
    shorten_duration_seconds: numberWithinClip(plan.shorten_duration_seconds || 0, 0),
    shorten_target_seconds: numberWithinClip(plan.shorten_duration_seconds || defaults.shorten_target_seconds, defaults.shorten_target_seconds),
    shorten_strategy: plan.shorten_strategy || defaults.shorten_strategy,
    shorten_audio: plan.shorten_audio || defaults.shorten_audio,
    shorten_focus: plan.shorten_focus || defaults.shorten_focus
  };
}

export function jobMatchesAsset(job, asset, plans) {
  return Boolean(job && asset && plans.some(plan => plan.id === job.plan_id && plan.asset_id === asset.id));
}

function latest(items) {
  return items.reduce((chosen, item) => !chosen || Number(item.created_at || 0) >= Number(chosen.created_at || 0) ? item : chosen, null);
}

export function studioSelection(assets, plans, jobs, assetId, jobId = null) {
  const asset = assets.find(candidate => candidate.id === assetId) || null;
  if (!asset) return { asset: null, plan: null, job: null, videoWorkflow: null, edit: defaultEdit() };
  const relatedJobs = jobs.filter(candidate => jobMatchesAsset(candidate, asset, plans));
  const job = jobId === null ? latest(relatedJobs) : relatedJobs.find(candidate => candidate.id === jobId) || null;
  const plan = (job ? plans.find(candidate => candidate.id === job.plan_id) : null)
    || latest(plans.filter(candidate => candidate.asset_id === asset.id));
  return {
    asset, plan, job,
    videoWorkflow: plan ? plan.shorten_duration_seconds ? "summarize" : "trim" : null,
    edit: defaultEdit(asset, plan)
  };
}
