# Butler: product story and demo run-of-show

## Product promise

**Your creative sidekick. Never the boss.**

Butler helps a creator get from footage they own to a post-ready draft. Give it a brief and, if useful, a transcript. It proposes an opening hook, an on-screen title, a caption, and hashtags. The creator can rewrite every word, shape the vertical edit, and explicitly approve rendering. Butler does not publish on the creator's behalf.

## The problem we chose

Making a short video involves several small, disconnected jobs: choosing a useful moment, framing it vertically, writing an opening line, drafting a caption, and packaging the post. A generic text generator can sound polished while guessing what the footage shows. Butler's Creator Pack is grounded in the brief and transcript the creator provides; it does not claim to see or understand visuals that it has not analyzed.

## The memorable moment

The creator types what the clip is about, optionally adds a transcript, and presses **Make a Creator Pack**. Butler returns four editable pieces: hook, title, caption, and hashtags. The suggested title appears in the vertical preview. The creator changes it, adjusts the trim or framing, reviews the plan, and approves the render. The creative decision stays with the person who made the clip.

## Three-minute demo plan

Target about 2 minutes 45 seconds. The submission portal says the video is **up to 3 minutes**, so keep a small buffer and use that limit unless organizers confirm otherwise.

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:15 | Open the Creator Studio with a real, consented clip ready. | “AI can make more content. We wanted it to give creators more control over the content they already make.” |
| 0:15–0:35 | Show the clip upload and inspection result. | “Butler starts with footage the creator owns. The clip is inspected before an edit can be approved.” |
| 0:35–1:05 | Enter a short creator brief and a transcript that accurately matches the clip. Generate the pack. | “Gemini turns the creator's own brief and words into a hook, title, caption, and tags. It isn't asked to invent what it sees.” |
| 1:05–1:30 | Change the title or caption; show the title in the vertical preview. | “The first draft is a starting point. The creator can rewrite it before anything is rendered.” |
| 1:30–2:05 | Adjust trim/framing, review the plan, and explicitly approve it. | “Butler previews the proposed edit and waits for the creator's approval.” |
| 2:05–2:30 | Show real render progress and the completed video. | “The approved edit becomes a vertical video, with subtitles when configured.” |
| 2:30–2:45 | Download the post package and close on the product promise. | “One creator-owned clip, a useful first draft, and the final say stays with the creator.” |

## Demo setup

- Prepare one short MP4 that the team created or has permission to use. Keep a backup clip locally.
- Use a brief and transcript that exactly match that clip. Avoid personal or sensitive material.
- Confirm the app status visibly says Gemini on Vertex AI before describing generated output as Gemini output. In local mode, call it a deterministic sample.
- Confirm the title, caption, and hashtags are editable. Demonstrate the plan approval step.
- Render and download the output before recording. If the render tool or cloud deployment is unavailable, do not stage a fake “completed” render or claim that it worked.
- The catalog is fictional and its prices are illustrative. Keep it out of the central demo and do not call it live shopping data.

## Technology narration: only claim what the live build proves

- **Gemini on Vertex AI:** the optional Creator Pack generation and explanations, when the app is configured for Vertex mode and a live response has been observed.
- **Cloud Run:** the live application/API deployment, once deployed and opened from its public service URL.
- **Firestore:** durable application records, once the active create/read/update flow is wired to Firestore and verified.
- **Firebase:** name the Firebase product actually used (for example, Hosting or Authentication) after the team verifies its deployed path.

The current local prototype uses SQLite and local files. Its Cloud Run entry mode is not, by itself, proof of a cloud deployment, and local persistence is not proof of Firestore use. Update this section after Saniya's cloud integration is merged and smoke-checked.

## Short pitch

Butler is the creator's sidekick, never the boss. It turns a creator-owned clip and their brief into an editable hook, title, caption, and hashtag set, then helps shape a vertical edit the creator explicitly approves. Gemini works from supplied context instead of making claims about unseen footage. The person who made the video keeps the final say.
