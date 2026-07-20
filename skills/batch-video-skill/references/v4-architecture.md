# V4 Architecture

## Data Flow

```text
product folder
  -> local SHA256 and product profile
  -> bundled 840-Gem Top-N router
  -> one Gem CreativePlanV2 per product/template/version
  -> prompt cache
  -> physical action validation
  -> provider compiler (Grok 15s / Omni 10s)
  -> canonical Image2 product reference
  -> per-segment Image2 keyframe + previous tail reference
  -> high-risk visual gate
  -> paid video task with immediate task_id persistence
  -> local FFmpeg QC + risk semantic QC
  -> at most one local prompt simplification/regeneration
  -> stitch only passed segments
  -> local manifest and Feishu outbox
```

## Sources Of Truth

- Local `creative_plan.json`, `segments.jsonl`, `state/` and `qc/` are the
  execution truth.
- Feishu is an eventually consistent console. Failure to sync creates or keeps
  pending outbox events and never changes video status.
- Stable event IDs and stable primary IDs prevent duplicate rows.

## Physical Gate

The action library currently recognizes grasp, place, rotate, pour, rinse,
press, slide and showcase. Strict validation rejects composite actions, invalid
hand occupancy, invalid axes and broken end-state/start-state continuity.

Contact shots use locked cameras. A high-risk segment must pass keyframe QC
before paid submission and video QC after download. Detected clipping, extra
fingers, product drift or wrong mechanics always override a model-provided
`passed=true`.

## Recovery

- Gem auth: refresh once and retry the current Gem; do not reroute.
- Gem cache hit: no Gem call.
- NewAPI network interruption: poll the stored `task_id`; do not POST again.
- Segment QC fail: simplify only that provider prompt and retry once by default.
- Second fail: `needs_review`, stop the branch, do not stitch.
- Feishu fail: append failed status to the outbox status log and continue.

## Temporary Reference Hosting

`CloudflareQuickTunnel` wraps a local random-prefix whitelist server. Only exact
registered files are served; directory browsing and predictable paths are not
available. The process closes after Image2/tail references are no longer needed.
Strict mode treats missing cloudflared or tunnel startup failure as fatal.

