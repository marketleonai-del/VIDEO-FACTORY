# Iteration Log

This Skill is intentionally iterative. Keep each upgrade small, verified, and
recoverable.

## 2026-07-15 - V4

- Bundled the verified 840-Gem V3 router and added exact Top-N routing.
- Replaced verbose per-provider Gem prompts with cached `CreativePlanV2`.
- Added physical actions, state continuity, hand occupancy and axis validation.
- Added Grok/Omni capability compilation and shared-plan behavior.
- Added Image2 canonical/keyframe/tail reference-chain runtime.
- Added NewAPI immediate task-ID persistence and resume-only polling.
- Added local FFmpeg QC, Gemini Flash semantic gates and one local regeneration.
- Added non-blocking Feishu outbox plus three-table console schema.
- Added arbitrary V4 duration, `qc-video`, `feishu-console-init` and
  `feishu-flush` CLI interfaces.
- Validation: 254 tests passed and a zero-cost shaved-ice 60-second dry-run
  returned U206 first. No new paid video was generated.
- Independent correctness and security reviews hardened strict chain order,
  execution-fingerprint recovery, `--no-resume`, one-retry enforcement,
  recursive Feishu redaction, SSRF-safe downloads, prompt-file transport and
  the portable archive allowlist.
- Official cloudflared 2026.7.1 is installed and signature-verified on D drive.
  The remaining external blocker is Feishu Bot scope `base:table:read`
  (`99991672`).

## 2026-07-02

Stabilized the Gem-first batch workflow:

- Added `gemcli_stability.py`.
- `gem-preflight` now self-heals stale Gemini cookies/tokens with `gemcli login`.
- `batch-folder` preflights Gemini Gem access before live Gem calls.
- Verified text Gem calls against `11b104fbb186`.

Integrated Feishu CLI as the management layer:

- Added `feishu_bridge.py`.
- Added `feishu-init-schema`.
- Added `feishu-sync-batch`.
- Expanded the existing Feishu table to support batch task management fields:
  `job_id`, `batch_id`, source image, Gem output paths, video prompt, provider
  task ID, local video path, Feishu file token/link, status, error, timestamps,
  and elapsed seconds.
- Verified `feishu-sync-batch` in dry-run mode against a local batch output.

Current stable boundary:

- Safe default is still dry-run.
- Gemini Gem text prompt generation is the stable Gem dependency.
- Feishu schema initialization is real and verified.
- Feishu row/video sync is available but should be run with dry-run first.
- Gemini native Create video tool remains outside the stable path unless
  BotGuard/Token Factory is separately verified.

## Upgrade Rules

- Add a command only when it has a dry-run or read-only mode.
- Every real cloud write must be opt-in (`--real`, `--feishu-live`, or equivalent).
- Local state/manifests are written before remote Feishu updates.
- Do not claim a production batch completed unless local MP4 files or explicit
  failure rows exist.
- Do not store cookies, passwords, API keys, or OAuth tokens in Skill files,
  manifests, prompts, reports, or Feishu rows.
