# Butler Decisions and Change Log

Last reviewed: 2026-10-18

## Product and engineering decisions

### 1. Make Creator Studio the demo's main story

Butler's clearest working journey is helping a creator turn an authorized clip into an editable, approved post package. The product-discovery catalog remains supporting context. This keeps the demonstration focused on an end-to-end user outcome rather than presenting an unfinished collection of unrelated agents.

### 2. Keep the interface expressive but readable

The candidate UI uses a distinctive violet, mint, and warm-white palette, a Butler mark, and a small animated Studio illustration. The artwork supports the product personality; upload, preview, edit, review, and export controls remain the main visual hierarchy. Responsive layouts and reduced-motion handling stay part of the design.

### 3. Keep preview behavior tied to the selected edit

Trim preview is constrained to the selected start/end range. Smart Shorten preview covers the full source because its selection process can choose moments from anywhere in the clip. This avoids showing a preview window that contradicts what the chosen operation will inspect.

### 4. Be exact about shopping capability

The catalog is fictional and its prices are illustrative. Search and sort work against that sample data. Google Shopping opens as an external handoff; Butler does not currently scrape or validate retailer prices. Do not pitch it as a Buyhatke-equivalent live price-comparison engine.

### 5. Keep local and cloud evidence separate

SQLite and local files support the current local prototype. Cloud Run, Firestore, Cloud Storage, Firebase Hosting, and Vertex AI adapters are configured in the repository, but a deployment and live service calls must be verified separately. Do not infer a working deployment from a project ID, billing setup, or configuration files.

### 6. Keep creators in control

Require permission confirmation before upload, server-side media validation, explicit edit-plan approval before rendering, and a user-controlled export. Avoid automatic publishing or actions that spend money or use a creator's media without a clear approval step.

### 7. Treat earlier secret alerts as a rotation task

No Google API key-shaped value was found in this checkout's working files during this review. GitHub previously reported public-leak alerts for Google API keys. Removing a value from current files does not invalidate it or remove it from repository history: revoke the old keys in Google Cloud, configure replacements outside source control, and then resolve the alerts.

### 8. Use the clean candidate branch as the release base

`codex/creator-studio-ui` is based on the current `origin/main` commit and has a clean working tree. The separate `Butler` checkout is one commit behind and contains uncommitted, overlapping edits. Keep those edits intact; do not overwrite the candidate branch with that stale diff. If a unique change from the older checkout is needed, compare and port it deliberately.

## Review log

### 2026-10-18

- Confirmed `Butler-final-ui` is on `codex/creator-studio-ui`, based on `origin/main` `2a14ec08`; the UI changes start at `7ddf1cb4` and the documentation follow-up remains on the same branch.
- Ran `python run.py --test`: 58 Python checks passed, one real-render check skipped because approved FFmpeg/FFprobe are unavailable, zero failed.
- Confirmed all 8 Node UI helper tests passed.
- Rechecked current source for Google API key-shaped values; none were found. GitHub's earlier alerts still require revocation and resolution.
- Added `flow.md` to document what users can do and the verified/unverified deployment paths.
- Added this file to record the product, UX, security, and release decisions behind the candidate.
- Pushed the documentation follow-up to the existing branch and attached open PR #4; GitHub reported no commit status checks at review time.
- Next priority: verify the deployed URL and cloud services, then perform a real video render; avoid adding more features until these demo-critical paths work.
