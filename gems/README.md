# Gem 提示词库（带货视频智能体指令总库）

配合 `skills/batch-video-skill`（840 Gem 路由）与 `gems/knowledge/万能引擎手册` 使用。每个 Gem = 一条可直接导入 Google Gemini 的带货视频智能体指令。

## 覆盖范围

| 目录 | 编号段 | 数量 | 内容 |
|---|---|---|---|
| `G001-G100/` | G001–G100 | 100 | 10 载体 × 10 角度（日记连载/双人关系/盲测挑战/沉浸感官/算账证据/翻车修复等），含完整导入包 v2 |
| `G101-G200/` | G101–G200 | 100 | 连环悬念/极限挑战/时间竞速/多人接力/竞猜互动/沉浸POV/对比擂台/层层剥/直播切片/系列剧集，含长视频钩子链版 zip |
| `U401-U600/` | U401–U600 | 200 | 决策旅程/情绪曲线/感官节奏/平台生态 四轴通用带货框架（Fable5 迭代版 zip 附带） |
| `U601-U840/` | U601–U840 | 240 | 四新族：E 品类实战（601–700）/ F 人群心智（701–800）/ G 场景时刻（801–840，本批到「日内时刻」） |

**当前共 640 条。** 缺口：U201–U400（200 条）与 U841–U1000（U601 批的 11–16 分卷）不在本次导入包内，后续补齐后放入对应目录即可。

## 知识文件（传 Gem「知识」栏）

- `knowledge/万能引擎手册v2.4_U601-U840_传Gem知识栏.md` — 当前版本，U601–U840 专用（E/F/G 三族协议）
- `knowledge/archive/` — v2.0 / v2.1 / v2.2 及最初版，仅存档

## 其他

- `主力Gem四件套_直接复制.md` — 主力四件套，开箱即用
- `提示词品类适配对照表.xlsx` — 品类 → Gem 编号适配表
- `提示词库V2/` — Gemini 带货智能体提示词库 V2（docx + pdf，30 品类新款）

## 使用方式

1. 在 Gemini 创建 Gem，把对应编号的指令全文粘贴到指令栏。
2. 把 `knowledge/` 中对应版本手册上传到该 Gem 的「知识」栏。
3. 由 `skills/batch-video-skill` 的 router840 按产品自动路由 Top-N Gem，或按 `提示词品类适配对照表.xlsx` 手选。

## 与路由核心的关系

真正的匹配核心在 `skills/batch-video-skill/router840/`：
- `data/gem_registry.json` — 840 个已验证 Gem（G001–G200 + U201–U840），每条含 gem_id
- `data/templates_index.json` + `data/router_config.json` — 类目/人群/需求/视觉/词法等 9 维加权
- `scripts/route.py` — 离线确定性路由：规则 + BM25 + 质量门控 MMR，输出精确 Top-N

`gems/` 目录是这 840 个 Gem 的**提示词源文本**（备份与再部署用）。路由按 gem_id 命中 Gemini 上的在线 Gem，不依赖本地文本，因此 U201–U400 原文暂缺不影响匹配。

## 直接用大模型跑 Gem（不依赖 Gemini 网页）

`gems/tools/build_gem_index.py` 把本目录所有 gem 指令解析成 `gems/index/gem_texts.jsonl`，
`skills/batch-video-skill` 的本地后端读它，把「gem 指令原文 + 对应手册」作为 system prompt 发给
DeepSeek/Kimi/OpenAI 等任意 OpenAI 兼容模型，产出与在线 Gem 同规格的分镜与提示词。

详见 [skills/batch-video-skill/references/local-gem-backend.md](../skills/batch-video-skill/references/local-gem-backend.md)。
