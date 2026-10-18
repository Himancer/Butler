# Butler Flow and Release Watch

Last reviewed: 2026-10-18
Candidate checkout: `Butler-final-ui`, branch `codex/creator-studio-ui`
Candidate code: `7ddf1cb4` on top of `origin/main` at `2a14ec08`.
Documentation follow-up is on the same branch. Pull request: [#4](https://github.com/Himancer/Butler/pull/4), open.

## Product flows

### Discover and compare

1. The user asks Butler for gear ideas or searches the sample catalog.
2. Butler narrows the fictional 30-item catalog by category, currency, budget, and keywords; results can be sorted by price or name.
3. The user can save items and compare up to three at a time. Unknown product facts stay explicitly unknown.
4. “Open Google Shopping” passes the query and optional budget to a separate Google Shopping tab. Butler does not scrape retailer pages or verify live prices, stock, shipping, or seller claims.

### Create a video post

1. The creator confirms they own the clip or have permission to use it.
2. The browser uploads an MP4 through a server-issued, single-use upload reservation.
3. Butler inspects the media with FFprobe. Missing or invalid media tools leave the clip at **Tools required** or reject it; Butler does not fabricate a playable result.
4. The creator previews the clip, chooses trim or Smart Shorten, edits title/subtitle/caption text, and reviews the output settings. Trim preview stays within its in/out points; Smart Shorten previews the full source.
5. The server validates an immutable edit plan. The creator must approve it explicitly before a render job can start.
6. When approved FFmpeg/FFprobe tools are available, Butler renders the output and offers the MP4, captions, and post package. The creator controls downloads and publishing; Butler does not publish on their behalf.

### Runtime and Google services

- **Local prototype:** Python HTTP server, SQLite, and local files. This is the mode used by local tests.
- **Intended cloud route:** Firebase Hosting serves the UI and rewrites API requests to Cloud Run; the service is configured to use Firestore, Cloud Storage, and optional Gemini on Vertex AI.
- Code and configuration are not proof of a live deployment. A public URL, Cloud Run health response, Firestore and Storage operations, IAM, and a real Gemini request still need live verification.
- **Render test preview:** https://butler-preview-test.onrender.com runs the candidate Docker branch separately from GCP. It uses local rules, a fictional sample catalog, SQLite and files under ephemeral `/tmp`, and manual transcription. It has a demo passcode. The Render `/health` endpoint returned HTTP 200 on 2026-10-18. This confirms the web process starts; it does not verify GCP services or real retailer data.
- The Render Free instance can sleep when idle and has no persistent disk. Sessions, uploads, and rendered files may be lost after a restart, deploy, or sleep cycle. Keep it for temporary app review; do not use it as the submission deployment or for durable creator media.
- Voice interaction and a team of autonomous personal-assistant agents are future scope, not current working functionality.

## Current verification

Reviewed on 2026-10-18:

- `python run.py --test`: 60 Python tests passed; one real-render integration test skipped; zero failures.
- Node UI helper tests: 8 passed; zero failures.
- `npm run check`: passed.
- Render test preview `/health`: HTTP 200. The app loaded in a fresh browser, the demo access-code gate accepted the configured code, and the sample catalog rendered. The badge initially said “Loopback only”; a fix now reports “Public preview” when `BUTLER_PUBLIC_ORIGIN` is configured and is being deployed.
- The skipped render test needs approved FFmpeg, FFprobe, and a font. Unit-test stand-ins do not prove that a real clip renders.
- The candidate UI branch already includes the refreshed theme, Butler mark, studio artwork, improved preview controls, and product search/sort.
- A scan of the current checkout's working files found no Google API key-shaped values. This does not revoke a key or close a GitHub secret-scanning alert from an earlier commit.
- No Cloud Run deployment, Firestore/GCS operation, or live Google API call is verified by the Render test preview or this local test run.
- GitHub reported no commit status checks for the documentation follow-up at the time of review.

## Submission blockers to watch

1. Confirm the active Cloud Run/Firebase Hosting URL and test it from a fresh browser without local credentials.
2. Verify the live Firestore, Cloud Storage, and Gemini paths using the deployed service identity; record which services were actually exercised.
3. Run one real render with approved FFmpeg/FFprobe and inspect the resulting video and downloadable package.
4. Revoke any Google API keys from GitHub's earlier secret alerts, replace them through the approved secret configuration, and verify the alerts are resolved.
5. Check that the final pitch PDF, public repository, deployed prototype, and demo video links are accessible and fit the portal's stated limits before submission.

Do not describe a blocker as complete until the corresponding live check has evidence recorded here.
