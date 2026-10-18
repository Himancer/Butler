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

### 9. Keep the hosted test preview separate from the GCP submission deployment

Use the Render Free service only to let the team open and exercise the app. It deploys the candidate Docker branch with local rules, the fictional catalog, SQLite, local media files, manual transcription, and a demo passcode. Its health endpoint and page load can verify the hosted app shell and API boot. Free-tier storage is ephemeral, and the instance may sleep; do not use it for important uploads or claim it proves Cloud Run, Firestore, Cloud Storage, Firebase Hosting, or Gemini.

## Review log

### 2026-10-18

- Confirmed `Butler-final-ui` is on `codex/creator-studio-ui`, based on `origin/main` `2a14ec08`; the UI changes start at `7ddf1cb4` and the documentation follow-up remains on the same branch.
- Ran `python run.py --test`: 58 Python checks passed, one real-render check skipped because approved FFmpeg/FFprobe are unavailable, zero failed.
- Confirmed all 8 Node UI helper tests passed.
- Rechecked current source for Google API key-shaped values; none were found. GitHub's earlier alerts still require revocation and resolution.
- Added `flow.md` to document what users can do and the verified/unverified deployment paths.
- Added this file to record the product, UX, security, and release decisions behind the candidate.
- Pushed the documentation follow-up to the existing branch and attached open PR #4; GitHub reported no commit status checks at review time.
- Created the separate Render Free test service `butler-preview-test` from `codex/creator-studio-ui`. Commit `267eebc8` is live. `/health` returned HTTP 200; the passcode gate, sample catalog, comparison, clip inspection, and a real 8-second trim all worked in the preview. The Creator Pack ran in deterministic local-rules mode.
- Corrected access-mode reporting so a configured `BUTLER_PUBLIC_ORIGIN` is shown as “Public preview,” and added a regression check. `python run.py --test` passed 60 tests with one local real-render test skipped; Node UI helper tests passed 8/8; `npm run check` and `git diff --check` passed.
- Render's Docker image installed FFmpeg and FFprobe. The bundled 12-second sample completed an 8-second trim, but larger or user-supplied videos still need testing. This Render check is not evidence for Cloud Run, Firestore, Cloud Storage, Firebase Hosting, or Gemini.
- Next priority: verify Meenakshi's GCP deployment and service calls, test with the team's authorized clip, and resolve the earlier GitHub secret alerts by rotating the old keys.


## October 18: marketplace navigation and cloud error diagnosis

Provide real retailer search destinations while retaining explicit fictional-catalog labels. Never send a fictional sample name as if it were a real purchasable SKU; build similar-item searches from category/type and connection. Retailer prices and availability require checking on their sites.

Live tests confirmed both cloud upload and AI generation failures. Add distinct messages for storage permission, missing bucket, throttling and generic failures, and Google AI status categories. Log only exception type/status, not credentials or raw upstream responses. These changes improve diagnosis; they do not repair cloud IAM or prove uploads/Gemini work. Preserve cloud persistence requirements rather than silently falling back to ephemeral local files.

Regression verification: 62 Python tests passed, 1 real-render test skipped, and 9 Node tests passed. Await bucket name and runtime identity before selecting the cloud repair. The working cloud service must be redeployed from this branch to receive these code changes.

## October 18: safe provider reason codes

Use an explicit allowlist of Google ErrorInfo enums rather than displaying raw error bodies. The shared HTTP 403 message prevents distinguishing configuration causes. Known reason codes can guide the deployment owner while unknown upstream content remains private. No change to product fundamentals, IAM, model selection or cloud configuration.

## October 18: keep each creator draft tied to one clip

A clip selection is an asset/plan/job unit. Restoring latest asset and latest job independently could show another clip's output. A shared selection helper and per-selection async version prevent cross-clip state. Remove means remove from the current draft, preserving uploaded history and running server work. Persist the cleared selection in sessionStorage so refresh cannot resurrect it in that tab. Explicit activity reopening restores context deliberately.

Captions need an optional owned asset association; never attach the latest arbitrary session caption to a video export. Legacy standalone captions remain separately accessible. Suggested titles require an explicit click so existing approved output cannot silently become stale. No new product scope was introduced. Regression and local browser evidence are in flow.md; cloud rendering and AI are not claimed verified.
