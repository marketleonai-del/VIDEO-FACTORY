# Doubao Switcher CLI Provider

Use this reference when `$batch-video-skill` should generate videos through the local Doubao account switcher package at `D:\号池\豆包账号切换器3.0-特色定制干净包`.

## Installed Paths

```text
D:\tools\CLI-Anything
D:\tools\venvs\cli-anything
D:\tools\doubao-switcher-cli
D:\tools\venvs\cli-anything\Scripts\doubao-switcher-cli.exe
```

## Commands

```powershell
doubao-switcher-cli doctor --json
doubao-switcher-cli state --json
doubao-switcher-cli launch --platform doubao --json
doubao-switcher-cli smoke-test --prompt "厨房清理剂，15秒痛点救场带货视频，竖屏，真实家庭厨房油污场景，前3秒强痛点，结尾展示清洁后对比。" --output-dir D:\tmp\doubao-switcher-cli-test --timeout-min 20 --generation-wait-sec 260 --json
```

Use from batch video skill:

```powershell
batch-video-skill batch-folder D:\path\to\images --max-items 1 --variants 1 --video-provider doubao-switcher --first-frame-mode none
```

## Behavior

- The CLI is an external harness. It does not edit `app.asar`, account JSON, cookies, or the original exe.
- `doctor` and `state` only print non-sensitive counts and status.
- `generate-video` focuses the Electron window, selects video generation mode, pastes the prompt, clicks send, tries download click candidates, and waits for a new MP4.
- `$batch-video-skill` records `video_provider=doubao-switcher` in manifests.

## Current Validation Result

The CLI bridge is installed and callable, but the real smoke test did not produce MP4. The prompt reached Dola, but the page showed a red failure marker and the download folder stayed empty.

Observed account/page symptoms:

- Current account pool is Dola-only.
- The app log includes Dola API error `code=710012014, message=账户不存在`.
- The app log also includes Google auto-login failure: `找不到 Google 登录入口，请手动点 Google 登录后再试`.

Treat this as an account/page-side generation blocker. Retry real video generation only after the same Dola/Doubao account can manually create one video in the app UI.
