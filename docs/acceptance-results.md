# Local Acceptance Results

Verified on 14 October 2026. This records observed local behavior, not certification of the full cloud BRD.

## Environment

| Capability | Observed state |
| --- | --- |
| OS | Windows corporate laptop |
| Python | 3.14.0, configured system interpreter |
| Node | 18.20.8 |
| Docker / gcloud | Not installed |
| FFmpeg / FFprobe | Not installed |
| FastAPI / React / npm Playwright packages | Not installed |
| Browser validation | VS Code integrated Playwright tools available and used |
| Infrastructure | Real local SQLite and local files; no cloud infrastructure |
| Source package installation | Not attempted against public registries; approved internal source not provided |
| PowerShell script execution | A local asset script returned PSSecurityException / UnauthorizedAccess. No policy bypass attempted; the script was removed and bitmap generation moved to browser Canvas. |

## Executable Checks

Command: `python run.py --test` from `butler/`.

| Check | Result | Exit code | Evidence |
| --- | --- | --- | --- |
| Python unit and real-HTTP integration suite | 45 passed, 0 failed, 1 skipped | 0 | 46 discovered tests across catalog/session/plan, API, and media tests |
| JavaScript syntax | Passed | 0 | Application module and artwork module |
| Node UI helper suite | 6 passed, 0 failed | 0 | Escaping, money, comparison, selection limit, hashtags, durations |
| Editor diagnostics | None reported | n/a | Workspace diagnostics checked after implementation |
| Startup | Passed | n/a | Runner printed readiness URL and browser/API requests succeeded |

**Total: 51 passing automated tests; one explicit real-render skip.**

The skipped test is `RealRenderTests.test_real_trim_render_subtitles_and_edited_caption_package`. It requires approved FFmpeg/FFprobe and a font. Metadata/subprocess stand-ins in unit tests are explicitly not evidence that a real render works.

## Browser Journeys

The following checks used the running application, real HTTP requests, real session cookies, real SQLite, and actual rendered UI. Assertions completed successfully except the download-manager boundary noted below.

| Journey | Observed result |
| --- | --- |
| Discover -> shopping chat -> save -> select two -> compare | Three products within the INR budget; saved count one; comparison count two; unknown facts explicitly retained |
| Create -> selected-product caption -> edit -> save -> reload | Generated text included selected catalog names; edited caption and hashtags survived reload |
| Caption export endpoint | HTTP 200; `Content-Disposition: attachment; filename="caption.txt"`; exact edited text and hashtags returned |
| Saved -> reload and server restart | Saved product retained |
| Consent -> synthetic fake-MP4 upload -> inspection -> render guard | `INVALID_MEDIA`; asset state invalid; server preview returned 409; review/render remained disabled |
| Mobile workspace status | Dialog opened from header control and accurately showed both FFmpeg and FFprobe missing |
| Responsive navigation | Discover, Create, Saved, Activity checked at widths 320, 375, 768, and 1440; no horizontal document overflow or clipped visible button/heading text detected |
| Visual assets | Six native-canvas PNG illustrations decoded at 640 x 480; each had over 20,000 nontransparent pixels |
| Icons | Bundled font loaded; search/edit/send/sparkle/upload glyphs matched verified library registrations |
| Screenshots | Desktop and mobile viewport screenshots visually inspected through integrated browser tools |

The browser download button was exercised, but the integrated browser did **not emit a Playwright download event**. The attachment endpoint/content is verified; actual saving through the browser download manager remains unverified. No file-save success is claimed.

Browser upload data was deliberately synthetic and invalid. No customer, personal, or copyrighted media was used. Screenshot and transient tool evidence lives in the VS Code session, not in a checked-in browser report.

## Boundaries Still Unverified

- Live Vertex AI authentication, model availability, billing, quotas, generated output quality, and provider latency.
- Live Google Speech recognition/language/timestamp quality.
- Real FFprobe validation and FFmpeg rendering, playback, font/libass support, and performance on five supported clips.
- Real-render failure/recovery under process termination. SQLite ownership/approval/retry/attempt guards were verified independently.
- Cloud Run, Firestore, Cloud Storage, IAM, signed cloud uploads, cloud retention lifecycle, and cloud cost controls.
- Cleanup timing and OS file-deletion behavior over a 24-hour session.
- Actual browser download-manager saving, screen-reader assessment, and non-Chromium browser behavior.
- Original BRD business targets, ten verified-retailer shopping cases, and three full shopping/video/export rehearsals.

## Verdict

```text
environment:
  docker: UNAVAILABLE - command not found
  node: AVAILABLE - v18.20.8
  playwright: AVAILABLE - integrated browser tools; no npm package install
  infra-tier: LOCAL - real SQLite/local files; cloud fidelity unverified
  browser-tier: PRIMARY - actual integrated Playwright-driven browser
startup: PASS - python run.py; readiness URL http://127.0.0.1:8765
integration: PASS - exit_code: 0; Python passed: 45, failed: 0, skipped: 1
ui-helpers: PASS - exit_code: 0; passed: 6, failed: 0
browser: PARTIAL - assertions passed for local flows; download-manager saving unverified
overall: NEEDS_SIGNOFF - local discovery/caption prototype works; live cloud and real video prerequisites remain
```

The app is a usable local prototype, not a production deployment or a completed cloud hackathon submission. The README names the exact setup prerequisites and remaining BRD work.

## Cloud Path Workstation Check (17 October 2026)

This is a current command/path check only; it does not mean any cloud service is configured or that a container image was built.

| Tool/configuration | Observed state |
| --- | --- |
| Docker CLI | Available, version 24.0.2; daemon/build not exercised |
| gcloud / Firebase CLI | Not found on PATH |
| FFmpeg / FFprobe | Not found on PATH; Butler path variables are empty |
| Windows Arial font | Present at `C:\Windows\Fonts\arial.ttf`; Butler font variable is empty |
| Cloud resources / credentials | Not configured or verified |

## Cloud Lane Branch Check (17 October 2026)

| Check | Result |
| --- | --- |
| Branch base | `codex/saniya-cloud` from fetched `origin/main` at `a2e49ef` |
| Python suite | 53 passed, 0 failed, 1 real FFmpeg render skipped because tools are not configured |
| JavaScript checks | 6 passed; syntax checks passed |
| Firestore adapter tests | 4 passed against an in-memory SDK stand-in; no live Firestore read/write |
| Firebase Hosting config | `firebase.json` parses; static root and Cloud Run rewrites inspected |
| Docker build | Not run; Docker daemon not exercised |
| Cloud deployment / URL | Not deployed; gcloud and Firebase CLI unavailable |
| Gemini | Cloud container configured for Vertex AI; no live Gemini request verified |
| GCS | Adapter configured for private bucket use; IAM/object persistence not live-verified |

The Cloud Run, Firestore, GCS, Firebase Hosting, and Gemini paths are implemented/configured in code, but none is reported as live. No project credentials were present, and no deploy URL or cloud-side read/write evidence is available from this workstation.
