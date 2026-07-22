# gemcli Stability Notes

Use `batch-video-skill gem-preflight` as the stability gate for Codex -> Gemini
Gem prompt generation.

Current verified path:

```bash
batch-video-skill gem-preflight --gem-id 11b104fbb186 --timeout 180 --smoke-prompt "厨房清理剂，输出一句话测试"
```

What the preflight fixes/checks:

- Ensures `~/.gemini-web-mcp-cli/profiles/default/auth.json` has
  `chrome_profile_path`.
- Runs `gemcli login --check`.
- If auth is stale, runs `gemcli login` and checks again.
- Stops gemcli's source Chrome daemon before `gems list`, because on Windows it
  can lock Chrome profile files while Token Factory copies the profile.
- Verifies `gemcli gems list`.
- Optionally verifies `gemcli chat --gem`.

Important boundary:

- For text Gem prompt templates, success means `login --check`, `gems list`, and
  `chat --gem` all return exit code 0 and useful text.
- A Token Factory warning can still appear in gemcli output. It is degraded but
  non-blocking for text Gem prompt generation when `chat --gem` succeeds.
- Gemini's native Create video tool may still require BotGuard/Token Factory
  capture. Do not treat text Gem success as proof that Gemini native video
  generation is stable.
- Do not trust process exit code alone. `gemcli chat` can return code `0` while
  stdout says `Auth error: Session expired`. Treat either `Auth error` or
  `Session expired` as authentication failure, run `gemcli login`, and retry
  only the current Gem/QC call once.
- Attachment responses may be terminal-wrapped inside JSON strings or keys.
  Strip ANSI codes and repair wrapped JSON whitespace before parsing, while
  keeping QC fail-closed when no valid object is recovered.
