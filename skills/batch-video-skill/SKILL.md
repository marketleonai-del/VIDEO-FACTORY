---
name: batch-video-skill
description: "Route product images across 840 verified Gemini Gems, preserve the selected Gem's raw script, compile traceable Image2 storyboard panels, block product or mechanism drift with Gemini visual QC, and submit only approved reference frames to Grok or Omni for resumable batch ecommerce video production."
---

# Batch Video Skill V5

Use this Skill for one product image or a folder of product images that must
become ecommerce videos. The default is planning and storyboard generation;
video generation requires an explicit live request.

## Fixed Boundary

1. Analyze the product locally and route exact Top-N across the verified
   `G001-G200 + U201-U840` Gem registry.
2. Send text only to the selected Gem. Preserve its complete raw response and
   SHA256; do not force the 840 Gems into one replacement schema.
3. Parse timed shots into `StoryboardPlanV1`. Every shot retains source line
   numbers, raw text and source hash.
4. Image2 creates one clean vertical panel per shot, using the product image
   and previous panel as appearance/continuity references.
5. Local code composes labeled director sheets. Gemini visual QC compares each
   risky sheet against the original product image.
6. Only `ready_for_video=true` segments expose an `input_reference` to Grok or
   Omni. Failed sheets remain visible but cannot be submitted.
7. Local JSON/JSONL is the source of truth. Feishu receives asynchronous status,
   hashes, short summaries and paths; it never blocks generation.

## Hard Rules

- Never call Grok/Omni unless the user explicitly requests live video or passes
  `--live`. Storyboard generation may call Gem, Image2 and Gemini visual QC.
- The product image is not sent to the creative Gem. Gem receives product facts
  and user requirements as text.
- Preserve `gem-raw-output.md` unchanged. Derived plans must bind its SHA256.
- One panel represents one physically possible state and one primary action.
- Generate individual 9:16 panels. Build contact sheets locally; do not ask
  Image2 to render grids, captions or storyboard text.
- Current Grok/Omni accepts one reference image per segment. Upload the clean
  `input_reference`, not the labeled contact sheet.
- Risk QC is fail-closed. Authentication errors, malformed QC JSON, product
  artwork loss, crank/hinge drift, clipping or wrong mechanics block submission.
- Persist paid `task_id` immediately and resume it. Never duplicate a paid POST.
- Keys and cookies remain in environment/private config. Never write them to
  prompts, manifests, Feishu or `.skill` archives.

## Storyboard Workflow

First call the routed Gem once and save its unmodified output. Then build the
storyboard. For Grok use 15-second sheets; for Omni use 10-second sheets.

```powershell
batch-video-skill storyboard-build "D:\products\item.jpg" `
  --gem-output "D:\job\gem-raw-output.md" `
  --gem-id "5f8406c9660c" --gem-name "U206 POV第一视角" `
  --product-name "手摇刨冰机" --market "墨西哥" `
  --sheet-seconds 15 --qc-mode risk --output-dir "D:\job\storyboard"

# Compile only. This spends no video credit.
batch-video-skill storyboard-video "D:\job\storyboard" --provider grok

# Explicit paid submission after reviewing storyboard-qc.json.
batch-video-skill storyboard-video "D:\job\storyboard" --provider grok --live
```

For Omni, build with `--sheet-seconds 10` and then use `--provider omni`.
`storyboard-video` rejects a provider-duration mismatch and any failed QC.

Use `--plan-only` to parse the real Gem response without Image2. Use
`--qc-mode local` only for offline development; production defaults to `risk`.

## Outputs

```text
storyboard/
  storyboard-plan.json
  panels.jsonl
  panels/shot-NNN-start.png
  prompts/shot-NNN-start.txt
  sheets/sheet-NNN.jpg
  storyboard-qc.json
  video-reference-manifest.json
  feishu_outbox.jsonl
  grok-video-requests.json | omni-video-requests.json
  state/          # paid task IDs and resume state
  videos/         # live segment outputs
```

`video-reference-manifest.json` contains both
`input_reference_candidate` and gated `input_reference`. On QC failure the
candidate remains available for diagnosis, while `input_reference` becomes
empty and `ready_for_video=false`.

## Folder Batches

For batch production, generate one storyboard package per product/template
variant, stop failed packages, and submit approved packages with product
concurrency 2 and per-provider segment concurrency 1. Keep the existing V4
command for zero-cost full-folder planning:

```powershell
batch-video-skill batch-folder "D:\products" `
  --video-provider auto --duration 60 --variants 1 --auto-gem-route `
  --physical-mode strict --reference-strategy image2-chain `
  --qc-mode risk --feishu-sync async --dry-run
```

## References

- `references/storyboard-v5.md`: parser, panel, QC and provider contracts.
- `references/v4-architecture.md`: resumable provider and Feishu architecture.
- `references/gemcli-stability-notes.md`: login refresh and one-retry boundary.
- `references/feishu-cli-link-design.md`: asynchronous Feishu bridge.
- `HANDOFF.md`: cross-conversation continuation card.
