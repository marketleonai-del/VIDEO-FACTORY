# Batch Folder Production

Use this workflow when the user gives one local folder containing product images
and expects a video output folder in the same directory.

## Command

Preflight the Codex -> gemcli -> Gem channel:

```bash
batch-video-skill gem-preflight --gem-id 11b104fbb186 --timeout 180
```

Optional real text smoke call, still no video generation:

```bash
batch-video-skill gem-preflight --gem-id 11b104fbb186 --smoke-prompt "厨房清理剂，美国市场，生成一个15秒痛点救场视频提示词"
```

`gem-preflight` self-heals the local gemcli channel: it fills a missing
`chrome_profile_path` in gemcli's local auth profile, refreshes stale
cookies/tokens with `gemcli login`, and stops any gemcli Chrome daemon that
would lock the source profile on Windows. It does not run an expensive full
`gems list` unless `--check-gems` is explicitly supplied.

```bash
batch-video-skill batch-folder "D:\path\to\images" --gem-id 11b104fbb186 --variants 1 --duration 15 --concurrency 1 --gem-retries 2
```

Safe smoke test:

```bash
batch-video-skill batch-folder "D:\path\to\images" --dry-run --max-items 3 --variants 2
```

Prepare Feishu management fields:

```bash
batch-video-skill feishu-init-schema
batch-video-skill feishu-init-schema --real
```

Sync a completed batch output folder to Feishu. The first command is dry-run and
does not upload videos or write rows:

```bash
batch-video-skill feishu-sync-batch "D:\path\to\images\batch_video_outputs_YYYYMMDD_HHMMSS"
batch-video-skill feishu-sync-batch "D:\path\to\images\batch_video_outputs_YYYYMMDD_HHMMSS" --real
```

Multiple versions per image:

```bash
batch-video-skill batch-folder "D:\path\to\images" --variants 3 --duration 15 --concurrency 1
```

## Output

The command creates a subfolder in the source image folder:

```text
batch_video_outputs_YYYYMMDD_HHMMSS/
  prompts/
    product-a__v01.txt
  gem_requests/
    product-a__v01.txt
  gem_outputs/
    product-a__v01.md
  first_frames/
    product-a__v01.jpg
  videos/
    product-a__v01.mp4
  state/
    product-a__v01.json
  batch_manifest.csv
  batch_manifest.jsonl
  failures.csv
```

`gem_requests/` stores the text prompt sent to Gem. `gem_outputs/` stores the raw
Gem response. `prompts/` stores the parsed video prompt that is sent to the video
provider. `batch_manifest.csv` and `batch_manifest.jsonl` are appended after every
job, so long batches can be inspected while still running. `failures.csv` only
contains failed jobs and error messages.

`state/*.json` is updated during the job. It records intermediate states such as
`first_frame_ready` and `video_submitted`, including the provider task ID as soon
as the video task is created.

## Resume

Resume is enabled by default. If the target video file already exists and is not
empty, the job is marked `skipped`.

```bash
batch-video-skill batch-folder "D:\path\to\images" --output-subdir batch_video_outputs_20260630_120000
```

Use the same `--output-subdir` to continue a previous batch.

## Gem Prompt Generation

Gem is the primary prompt generator. The batch command sends a text request to
Gem for each source image and variant. It does not upload the local product image
to Gem. The request includes product name, image filename, category, target
country, duration, and variant number. Gem should return structured prompts:

- `first_frame_prompt`: HFSY/GPT-image-2 lifestyle first-frame prompt.
- `video_prompt`: Kuaizi video prompt.
- `negative_prompt`: constraints to append to both prompts.
- `style_notes`: production style and camera notes.

Disable Gem only for fallback/debug work:

```bash
batch-video-skill batch-folder "D:\path\to\images" --no-gem
```

## First-Frame Modes

`generated` is the default. It uses the Gem-produced `first_frame_prompt` to
generate a vertical HFSY/GPT-image-2 lifestyle first frame, then sends that
generated first-frame URL to Kuaizi for video generation.

`url-base` means the product images have public URLs and can be passed to HFSY as
`reference_images` while still generating a new lifestyle first frame. It does
not use the original product image as the video first frame:

```bash
batch-video-skill batch-folder "D:\path\to\images" --first-frame-mode url-base --image-url-base "https://cdn.example.com/products"
```

`none` skips the first frame and sends text prompts to Kuaizi:

```bash
batch-video-skill batch-folder "D:\path\to\images" --first-frame-mode none
```

Kuaizi cannot fetch local Windows file paths directly. If exact product appearance
must be preserved, use a public `--image-url-base` or add an upload backend so
HFSY can receive product images as references before generating lifestyle first
frames.

## Production Defaults

- Start with `--concurrency 1` for real API runs.
- Use `--max-items 1` or `--max-items 3` before launching a 100-image batch.
- Keep `--duration 15` for the verified pain-rescue flow.
- Use `--variants N` when the user wants more than one video per source image.
- Do not write API keys, cookies, or Google credentials to manifests or prompts.

For V4 Grok/Omni batches, use the V4 commands in `SKILL.md`. V4 supports
arbitrary target duration, bundled 840-Gem Top-N routing, `CreativePlanV2`,
Image2 reference chains, segment QC and asynchronous Feishu outbox. The legacy
Kuaizi/Doubao path documented above remains restricted to 5/10/15 seconds.
