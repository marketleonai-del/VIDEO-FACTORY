# Feishu CLI Link Design

## Goal

Make Feishu the control plane for batch video production:

- One Feishu Base can show every source product image, Gem prompt, generated
  first-frame prompt, video prompt, provider task ID, local output path, Drive
  file link, failure reason, and final status.
- Codex still runs the local production workflow.
- `lark-cli`/Feishu CLI handles Base records and Drive uploads.
- Local files remain the source of truth during generation; Feishu is updated
  incrementally so long batches can be monitored and resumed.

## Architecture

```text
Product image folder
  -> batch-video-skill batch-folder
    -> gem-preflight
    -> gemcli chat --gem
    -> local state/*.json + manifests
    -> HFSY/GPT-image first frame
    -> Kuaizi/Seedance video task
    -> local videos/*.mp4
    -> lark-cli bridge
      -> Feishu Drive batch folder
      -> Feishu Base task rows
```

Feishu should be used in two layers:

1. `Task Control Table`: one row per image variant/job.
2. `Asset Library Table`: one row per final uploaded video asset.

The current D-drive project `D:\Feishu\video-feishu-sync` can remain the asset
sync backend, but batch-video-skill needs a direct bridge for task/control rows.

## Tables

### Table 1: 视频生成任务队列

One row per image variant. This is the main dashboard.

Recommended fields:

- `job_id`: stable ID, e.g. `sha1(source_path)::v01`
- `batch_id`: timestamp or user-specified batch name
- `源图片路径`
- `源图片文件名`
- `产品名称`
- `品类`
- `目标市场`
- `版本号`
- `Gem ID`
- `Gem 名称`
- `Gem 请求`
- `Gem 原始输出`
- `首帧提示词`
- `视频提示词`
- `负面提示词`
- `风格说明`
- `首帧模式`
- `首帧本地路径`
- `首帧URL`
- `视频模型`
- `视频任务ID`
- `视频本地路径`
- `飞书视频链接`
- `飞书文件Token`
- `状态`
- `错误信息`
- `开始时间`
- `更新时间`
- `完成时间`
- `耗时秒`

Suggested status values:

- `queued`
- `gem_checking`
- `gem_prompt_generating`
- `gem_prompt_ready`
- `first_frame_generating`
- `first_frame_ready`
- `video_submitted`
- `video_polling`
- `video_ready`
- `uploading_to_feishu`
- `completed`
- `failed`
- `skipped`
- `dry_run`

### Table 2: 视频资源表

This can reuse the existing table from `D:\Feishu\video-feishu-sync`, but it
should add batch-production fields so videos can be traced back to generation:

- `job_id`
- `batch_id`
- `产品名称`
- `源图片路径`
- `Gem ID`
- `视频提示词`
- `视频模型`
- `视频任务ID`
- existing metadata fields such as local path, file size, duration, resolution,
  Feishu link, upload status, sync status.

### Table 3: 批次运行表

One row per batch run.

Recommended fields:

- `batch_id`
- `源文件夹`
- `输出文件夹`
- `总图片数`
- `总任务数`
- `成功数`
- `失败数`
- `跳过数`
- `dry_run`
- `Gem ID`
- `视频模型`
- `开始时间`
- `完成时间`
- `状态`
- `失败摘要`

## Link Strategy

### Phase 1: Post-run sync, lowest risk

Add a `feishu-sync-batch` command:

```bash
batch-video-skill feishu-sync-batch "D:\images\batch_video_outputs_YYYYMMDD_HHMMSS" --dry-run
batch-video-skill feishu-sync-batch "D:\images\batch_video_outputs_YYYYMMDD_HHMMSS" --real
```

It reads:

- `batch_manifest.jsonl`
- `failures.csv`
- `state/*.json`
- `gem_requests/*.txt`
- `gem_outputs/*.md`
- `prompts/*.txt`
- `first_frames/*`
- `videos/*.mp4`

Then it:

- upserts one task row per job into `视频生成任务队列`
- uploads final videos to Feishu Drive
- writes final video links and tokens back to task rows
- optionally mirrors final assets into `视频资源表`

This phase is stable because it does not affect the generation loop.

### Phase 2: Live incremental sync

Add optional flags to `batch-folder`:

```bash
batch-video-skill batch-folder "D:\images" --gem-id 11b104fbb186 --feishu-live --feishu-batch-name "厨房清洁剂-美国-001"
```

Behavior:

- create/update a batch row at start
- create task rows immediately after job planning
- update the row after every state transition
- upload final videos at completion
- mark failed rows immediately with error details

This gives a live dashboard during 100-image runs.

### Phase 3: Feishu-driven queue

Add a command that reads rows from Feishu and runs local production only for
rows with `状态 = queued`:

```bash
batch-video-skill run-feishu-queue --limit 10 --concurrency 1
```

This makes Feishu the operator UI:

- user edits product names/categories/target markets in Feishu
- Codex pulls queued rows
- Codex writes outputs/status back

## Stability Rules

- Always run `gem-preflight` before live Gem usage.
- Do not put Google cookies, video API keys, or Feishu OAuth tokens in Feishu
  rows, manifests, prompts, or reports.
- Use stable `job_id` as the upsert key. Never rely on row order.
- Every local state update should be append-safe: local `state/*.json` is written
  first, Feishu update second. If Feishu fails, generation should continue and
  the next sync pass can repair rows.
- Upload final MP4 once. Store `飞书文件Token` locally so repeated sync does not
  duplicate uploads.
- Dry-run must still create Feishu task rows only when explicitly requested with
  `--feishu-dry-run-write`; normal dry-run should avoid cloud writes.

## Recommended Implementation

Create these modules inside `batch_video_skill`:

- `feishu_bridge.py`
  - `FeishuBridgeConfig`
  - `FeishuBridge`
  - `upsert_batch_run`
  - `upsert_task`
  - `upload_video`
  - `sync_batch_output`
- `feishu_schema.py`
  - field definitions for task/batch/asset tables
  - field-name mapping from `JobRecord`
- `feishu_cli.py`
  - small wrapper around `D:\agent\PilotDeck\lark-cli.cmd`
  - uses `--json @relative-file.json` to avoid PowerShell JSON quoting issues

Extend `batch-folder` with:

- `--feishu-live`
- `--feishu-sync-final`
- `--feishu-config D:\Feishu\video-feishu-sync\config\config.yaml`
- `--feishu-batch-name TEXT`

Add a standalone command:

- `feishu-init-schema`: create/patch Feishu Base fields and views.
- `feishu-sync-batch`: sync an existing local batch output folder.
- `run-feishu-queue`: pull queued rows from Feishu and generate.

## UX Result

For a 100-image folder, the user can open Feishu and see:

- every planned video task
- which product/image each task belongs to
- what Gem wrote
- what prompt was sent to video provider
- which task is running, failed, skipped, or complete
- final Feishu Drive video link
- failure reasons that can be edited/retried

This turns the workflow from a local black box into an operator-friendly batch
production dashboard.
