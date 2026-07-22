# VIDEO-FACTORY 交接说明（给接手的 AI）

生成时间：2026-07-22　来源分支：`merge-gems-and-skills`（GitHub 上 PR #2，尚未合入 main）

## 一句话现状

产品图 → 路由到最匹配的带货 Gem → 出分镜/提示词 → 故事板 → 视频，这条链路已经打通，且**出提示词那一步不再依赖 Gemini 网页 cookie**。剩下的是补数据和跑真实付费链路。

## 目录地图

```
gems/                       Gem 指令总库（640 条）
  G001-G100/  G101-G200/  U401-U600/  U601-U840/
  knowledge/                《万能引擎手册》v2.4（archive/ 存旧版 v2.0-v2.2）
  index/gem_texts.jsonl     ← 机器可读索引，本次新建，LocalGemClient 读它
  tools/build_gem_index.py  ← 重建索引的脚本
  提示词品类适配对照表.xlsx  主力Gem四件套_直接复制.md  提示词库V2/

skills/batch-video-skill/   主 skill（V5）
  router840/                840 条注册表 + 9 维加权路由（离线确定性，含 U201-U400）
  batch_video_skill/
    local_gem_client.py     ★本次新增：用大模型跑 Gem，GemCliClient 的 drop-in 替代
    batch_folder_runner.py  批量链路（新增 --gem-backend local 分支）
    storyboard_plan.py      分镜文本解析器（校验秒数连续、逐段 SHA256）
    storyboard_runtime.py   Image2 故事板生成 + 视觉 QC
  scripts/run_gem.py        ★本次新增：路由 → 出稿 一条命令
  tests/test_local_gem_client.py  ★6 条用例，全过
  references/local-gem-backend.md ★用法/配置/边界文档

skills/ecom-long-video-stitch/  长视频无缝拼接（转场/一致性/FFmpeg）

core/ workflows/ adapters/ live-server.js  原有的 TS 引擎与本地服务（本次未改）
```

## 本次做了什么（关键改动）

原链路的「出提示词」必须走 `gemcli` → Gemini 网页版 Gem，依赖登录 cookie，cookie 过期即断，无法无人值守。

现在改成：**把 gem 指令原文 + 对应版本《万能引擎手册》当作 system prompt，发给任意 OpenAI 兼容模型**。喂进模型的字节 = 人手工粘进 Gemini「指令栏 + 知识栏」的字节，所以出稿规格对齐在线 Gem。

两种输出契约：
- `storyboard`（默认）→ `分镜N（起-止秒）` 定时分镜文本 → 喂 `storyboard-build`
- `json` → `first_frame_prompt` / `video_prompt` / … → 喂 `batch-folder` 批量链路

## 怎么跑

```bash
# 0) 建索引（改过 gems/ 就重跑）
python gems/tools/build_gem_index.py

# 1) 只看路由，不花钱
python skills/batch-video-skill/scripts/run_gem.py \
  --product "手摇刨冰机" --category 厨房 --market 墨西哥 \
  --facts "夏天在家做冰沙，儿童也能摇" --route-only

# 2) 出稿
export GEM_TEXT_API_KEY=sk-...        # 或 DEEPSEEK_API_KEY / MOONSHOT_API_KEY / OPENAI_API_KEY
python skills/batch-video-skill/scripts/run_gem.py \
  --product "手摇刨冰机" --category 厨房 --market 墨西哥 \
  --duration 60 --mode storyboard --out ./job/gem-raw-output.md

# 3) 故事板（原流程未改）
batch-video-skill storyboard-build ./products/item.jpg \
  --gem-output ./job/gem-raw-output.md --gem-id U601 \
  --product-name "手摇刨冰机" --market "墨西哥" --sheet-seconds 15 --output-dir ./job/storyboard

# 批量模式
batch-video-skill batch-folder ./products --gem-backend local --auto-gem-route ...

# 测试
python -m pytest skills/batch-video-skill/tests/test_local_gem_client.py -q
```

## 验证到什么程度（别高估）

**已验证**：6 条测试通过 —— 索引加载 640 条、编号/名称解析、system prompt 确实原样带上 gem 指令+手册+契约、mock API 完整往返、缺 key 明确报错、**storyboard 模式产出能被真实的 `parse_storyboard_plan` 解析成 4 段 0–60 秒连续、逐段带 SHA256 的 plan**。路由脚本用真实参数跑通（手摇刨冰机 → U626/U666/U663…）。

**未验证**：没有用真 API key 调过一次真实大模型；没跑过真实付费的图/视频生成；端到端出片没做过。**接手第一件事应该是拿真 key 跑一条完整的，而不是继续加功能。**

## 已知缺口 / 坑

1. **gem 原文只有 640 条**，注册表却是 840 条。缺 U201–U400（200 条）和 U841–U1000（160 条）。路由若命中缺失编号，`LocalGemClient` 会明确报错，不静默降级——补齐原文丢进 `gems/` 对应目录再跑一次建索引即可。
2. **`cli.py` 需要 Python 3.12+**（用了 PEP 701 嵌套引号 f-string）。这是仓库原有状况，不是本次引入；3.10/3.11 下 `import cli` 会 SyntaxError。`local_gem_client.py` / `run_gem.py` / `batch_folder_runner.py` 兼容 3.10。
3. **prompt 很长**：手册 + gem 指令约 1.5–2 万字符。模型上下文 <32k 要设 `include_manual: false`，质量会掉，仅应急。
4. **PR #2 卡在分支保护**：main 要求 1 个批准，仓库只有一个 owner，自己不能批自己。要么把 ruleset 的「所需审批」改成 0，要么加协作者批准。
5. 密钥只从环境变量/私有 config 读，不写进 prompt、manifest、`.skill` 包。别破坏这条。

## 建议的下一步（按优先级）

1. 拿真 key 跑通一条真实成片，记录失败点 —— 这比任何新功能都重要
2. 补 U201–U400、U841–U1000 原文入库
3. 把 `run_gem.py` 的能力并进 `cli.py`（等 3.12 环境就绪）
4. 接 `ecom-long-video-stitch` 做多段拼接的端到端验证
