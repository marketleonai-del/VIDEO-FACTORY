# Storyboard V5 Contract

## Why This Layer Exists

Text-to-video receives too many actions and no stable product geometry. V5
turns a real Gem script into inspectable still states before video submission.
It does not rewrite the Gem's creative logic.

## Traceability

`StoryboardPlanV1` stores the raw Gem path and SHA256. Every shot stores its
source line range, source block hash, exact raw block, timing, visual, audio,
dialogue, camera, primary action and physical risk. Parser coverage must be 1.0.

The parser groups adjacent shots greedily without splitting a shot. Sheets are
at most 15 seconds for Grok and 10 seconds for Omni.

## Image2 Panel Rules

- One full-bleed 9:16 image per shot; no grid or generated labels.
- Product image is the authoritative appearance reference.
- From panel two onward, also reference the previous panel.
- Keep housing, printed artwork, transparent parts, blade/shaft, lid, crank,
  handle orientation, proportions, hands, scene and light stable.
- A start panel depicts the valid state immediately before the shot action.

The local compositor creates a labeled director sheet. This sheet is for QC,
Feishu and human review. Current Grok/Omni receives only the clean first panel.

## Quality Gate

Local checks verify decode, minimum dimensions, variance and vertical aspect.
For `risk`, any sheet containing a high-risk shot is reviewed once with Gemini
Flash using the original product plus the director sheet. The full sheet fails
if any panel has product drift, missing artwork, wrong mechanism, clipping,
floating parts or extra fingers.

On failure:

```json
{
  "ready_for_video": false,
  "qc_status": "needs_review",
  "input_reference": "",
  "input_reference_candidate": "D:\\job\\panels\\shot-005-start.png"
}
```

Authentication expiration and malformed QC output are errors, never passes.
Refresh `gemcli` and retry only the current QC once.

## Provider Handoff

`storyboard-video` reads only `video-reference-manifest.json`. Default mode
writes a redacted request plan with hashes and paths but excludes full prompts.
It recomputes the approved panel SHA256 and rejects files changed after QC.
`--live` starts a temporary whitelist tunnel, registers the approved clean
panel, submits one segment, persists `task_id`, and resumes that ID after a
network interruption.

Provider limits in V5:

- Grok: `grok-imagine-video-cli`, up to 15 seconds per sheet.
- Omni: `omni-fast`, up to 10 seconds per sheet.

## 2026-07-17 Shaved-Ice Test

The real routed U206 Gem returned 19,985 characters, 60 seconds and 16 shots.
The parser retained all 16 shots with coverage 1.0. One 15-second sheet produced
four 941x1672 panels. Gemini QC rejected all four because Image2 removed the
printed cup artwork and changed/missed the yellow crank connection. The video
manifest correctly reports zero ready segments; no Grok/Omni video was created.
