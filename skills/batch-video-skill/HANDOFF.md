# Batch Video Skill V5 Handoff

> Version: V5.0 | Updated: 2026-07-17 | Cross-conversation continuation

## New Conversation Prompt

```text
Read C:\Users\Lenovo\.codex\skills\batch-video-skill\HANDOFF.md and SKILL.md.
Continue $batch-video-skill V5 from the current state. Preserve this boundary:
local product analysis -> exact Top-N of 840 verified Gems -> one immutable raw Gem response -> traceable StoryboardPlanV1 -> Image2 panels -> Gemini product/mechanism QC -> approved panel to Grok/Omni -> video QC/stitch -> local manifest -> async Feishu outbox.
Do not create a paid video unless I explicitly request --live. Never expose keys or cookies.
```

## Context Card

### Completed

1. Added raw Gem parser, Image2 panel chain, local director sheets, semantic
   storyboard QC, gated video manifest and Feishu `故事板` outbox table.
2. Added `storyboard-build` and `storyboard-video`; the latter defaults to a
   zero-cost compile and persists paid task IDs when `--live` is explicit.
3. Added transparent-proxy-safe HFSY image download without weakening default
   cross-origin video peer checks.
4. Added Grok 15-second and Omni 10-second capability gates.

### Verified

- Real Gem: U206, ID `5f8406c9660c`.
- Real raw output: 19,985 characters, 60 seconds, 16 shots, parser coverage 1.0.
- Real Image2 test: four 941x1672 panels and one local director sheet.
- Gemini QC: rejected product drift and wrong crank mechanics; ready segments 0.
- `storyboard-video --provider grok` correctly refuses the failed sheet.
- No Grok/Omni video was generated in this iteration.

### Artifacts

- Work copy: `D:\codex claude 搭子\batch-video-skill-v4`
- Test job: `D:\video-jobs\storyboard-skill-test\shaved-ice-20260717-022520`
- Storyboard: `...\storyboard-image2-sheet2`
- Raw Gem output: `...\gem-raw-output.md`
- QC: `...\storyboard-image2-sheet2\storyboard-qc.json`

### Next Priority

Improve Image2 product-identity retention before any video generation. Prefer a
canonical product plate or product mask and regenerate only the rejected sheet;
do not recall the creative Gem. Submit a short high-risk video only after the
storyboard reports `ready_for_video=true`.

## Security

`NEWAPI_API_KEY` is environment-only. HFSY and Gemini credentials remain in
private local config. Full prompts stay local; Feishu receives hashes, status,
paths and short summaries through a durable outbox.
