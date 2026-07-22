# 本地 Gem 后端（用大模型替代 Gemini 网页 Gem）

## 解决什么问题

原链路里「出提示词」这一步必须走 `gemcli` → Gemini 网页版 Gem，依赖登录 cookie、Gem 必须在线存在、cookie 过期就断，无法无人值守。

本后端把同一步改成：**把 gem 指令原文 + 对应版本《万能引擎手册》当作 system prompt，发给任意 OpenAI 兼容的大模型 API**。喂给模型的字节，和人手工粘进 Gemini「指令栏 + 知识栏」的完全一致，所以产出质量对齐在线 Gem，而不再需要 cookie。

## 三步链路

```
产品 → router840 路由出最匹配的 gem → 本地大模型按该 gem 指令出分镜/提示词 → 故事板 → 视频
```

### 1. 建索引（一次即可）

```bash
python gems/tools/build_gem_index.py
# → gems/index/gem_texts.jsonl（当前 640 条：G001-G200 + U401-U840）
```

### 2. 路由 + 出稿

```bash
# 只看路由，不花钱
python skills/batch-video-skill/scripts/run_gem.py \
  --product "手摇刨冰机" --category 厨房 --market 墨西哥 \
  --facts "夏天在家做冰沙，儿童也能摇" --route-only

# 真出稿（分镜文本，喂给 storyboard-build）
export GEM_TEXT_API_KEY=sk-...
python skills/batch-video-skill/scripts/run_gem.py \
  --product "手摇刨冰机" --category 厨房 --market 墨西哥 \
  --duration 60 --mode storyboard --out ./job/gem-raw-output.md

# 想先看喂进模型的完整 system prompt（不需要 key）
python skills/batch-video-skill/scripts/run_gem.py --gem U601 \
  --product x --market 墨西哥 --show-system-prompt
```

### 3. 接回原有流程（完全不变）

```bash
batch-video-skill storyboard-build "D:\products\item.jpg" \
  --gem-output ./job/gem-raw-output.md --gem-id U601 --gem-name "U601 尺码翻译官体" \
  --product-name "手摇刨冰机" --market "墨西哥" --sheet-seconds 15 --output-dir ./job/storyboard
```

批量文件夹模式直接加开关即可，无需 cookie：

```bash
batch-video-skill batch-folder ./products --gem-backend local --auto-gem-route ...
```

## 两种输出模式

| `--mode` | 产出 | 下游 |
|---|---|---|
| `storyboard`（默认） | `分镜N（起-止秒）` 定时分镜文本 | `storyboard-build` → Image2 故事板 → 视觉 QC → 视频 |
| `json` | `first_frame_prompt` / `video_prompt` / `negative_prompt` 等 | `batch-folder` 批量出片链路 |

## 配置

环境变量（任选其一）：`GEM_TEXT_API_KEY`、`DEEPSEEK_API_KEY`、`MOONSHOT_API_KEY`、`OPENAI_API_KEY`。

或写进 config：

```json
{
  "text_models": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.8,
    "include_manual": true
  }
}
```

预置 provider：`deepseek`（默认）/ `kimi` / `moonshot` / `openai`。任何 OpenAI 兼容网关填 `base_url` 即可。

## 边界与已知缺口

- **模型要吃得下长 prompt**：手册 + gem 指令约 1.5–2 万字符。上下文小于 32k 的模型请设 `include_manual: false`（质量会下降，仅应急）。
- **索引里只有 640 条**：U201–U400、U841–U1000 的原文尚未入库；router840 注册表里这些编号仍在（840 条），路由到它们时本地后端会明确报错，不会静默降级。补齐原文放进 `gems/` 再跑一次建索引即可。
- **铁律不靠模型自觉**：输出契约里重申了画面零文字、时长连续、单段 ≤15 秒；`storyboard-build` 的解析器会再校验一次，秒数不连续或缺分镜会直接报错而不是放行。
- 密钥只从环境变量/私有配置读取，不写进 prompt、manifest 或 `.skill` 包。

## 测试

```bash
python -m pytest skills/batch-video-skill/tests/test_local_gem_client.py -q
```

6 条用例覆盖：索引加载、编号/名称解析、system prompt 是否原样带上 gem 指令+手册+契约、mock API 往返、缺 key 报错、以及 storyboard 模式产出能否被真实解析器 `parse_storyboard_plan` 解析成 4 段 0–60 秒的 plan。
