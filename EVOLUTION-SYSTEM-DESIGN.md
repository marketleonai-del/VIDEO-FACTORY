# 视频工厂 Skill 自进化系统 · 设计方案（v3.0）

> 对应你 8 项交付要求；融合 **SkillOpt**（SKILL.md 即可训练参数 + 验证门控 + 文本学习率）、**Hermes**（用后即学的学习循环）、**GenericAgent**（小种子长出能力）的核心思想，并解决「用户不打分、不贡献、要知道进化方向、隐私优先」四大核心挑战。代码已落地在本项目（v3.0，tsc 0 错 + 46 单测通过）。

## 1. 系统设计（交付物1）
三端架构：
```
用户端 Skill（开源，自带 API Key）
  ├─ 视频引擎：模型/TTS 适配器 + 动态工作流（短视频矩阵）+ 长视频管线（多段续帧+拼接）
  ├─ SkillParams：可训练参数（角度权重/模型偏置/提示后缀/质检门）——SkillOpt 思路
  ├─ Telemetry：匿名、可开关、无 PII 的隐式遥测
  ├─ EvolutionEngine：本地用后即学（Hermes）：隐式质量 → 候选参数 → 验证门控 → 学习率采纳 → 可回滚
  └─ VersionChecker：拉总部最优参数，本地再门控才采纳
        │  (HTTPS，匿名批量，opt-in)
        ▼
总部端 HQ（你部署，MVP 已给）
  ├─ collector：/telemetry 收、/params/latest 发、/admin/promote、/admin/dashboard
  ├─ aggregate：按 paramsVersion 聚合隐式质量 → 晋升/回滚决策（验证门控）
  └─ 发布：把"经数据验证更优"的 SkillParams 设为冠军，灰度下发
```
数据流：用户生成视频 → 本地产生隐式信号（成功/重试/重生成/质检/拼接）→ 本地进化（即时变好）→ 匿名聚合上报 → HQ 跨用户聚合找全局最优 → 下发新参数 → 客户端门控采纳。**双层进化**：本地快（个体适配）+ 总部慢（全局最优）。
技术选型：TypeScript、零运行时依赖（仅 Node 内置 + ffmpeg 系统二进制）、参数即 JSON、HQ 用 node:http MVP（生产换 DB/数仓）。理由：开源友好、易部署、用户零配置依赖、模型无关。

## 2. 用户端 Skill（交付物2）= 本项目 universal-video-generator
开源即用：填 `.env`（自己的 Key）→ `npm i && npm run build` → CLI/HTTP/库三种用法。内置：①两种生成模式（全 AI / 混合用户素材）②长视频多段拼接 ③自进化（本地）④数据上报开关（默认**关**，opt-in）⑤版本检查 ⑥README/API 文档。`.skill` 一键安装包亦可。

## 3. 总部端系统（交付物3，MVP 已给）
`hq/collector.ts` + `hq/aggregate.ts`：收匿名遥测、按版本聚合隐式质量、`/admin/dashboard` 看每版本变好/变坏、`/admin/promote` 晋升经验证的更优版本、冠军参数落盘并由 `/params/latest` 下发。部署：`ts-node hq/collector.ts`（或容器）。生产化：遥测入数仓、加鉴权网关、灰度/AB 下发。

## 4. 质量评估体系（交付物4）——不需要用户打分
用**隐式信号**（`QualitySignals.computeQuality`）：成功率、**重生成率**（用户对同需求重做=不满意，↓更好）、质检通过率、拼接成功率、平均重试，合成 0-1 的 ImplicitQuality。判断"变好/变坏"= 版本间 ImplicitQuality 的差值（HQ 按 paramsVersion 聚合对比）。长视频另加"段间一致性/拼接成功率"。**全程不需要用户主动评分或上传成片**。

## 5. 进化机制（交付物5）
- **可训练参数**（SkillOpt）：`SkillParams`（角度权重/模型偏置/提示后缀/质检门）版本化。
- **用后即学**（Hermes）：每次生成产隐式信号 → `EvolutionEngine.evolveRound`。
- **bandit 探索**：按角度隐式质量把权重推向赢家（ε 探索防欠采样饿死）。
- **验证门控**（SkillOpt 核心）：候选必须**严格优于**当前（`improvement>0`，no ties → 杜绝静默漂移）；样本不足/给零质量角度加权 → 拒绝；被拒进**负样本缓冲**避免反复提坏候选。
- **文本学习率**：候选只朝当前移动 lr 比例（默认 0.3），限制每轮漂移。
- **回滚**：保留 last-good，坏进化一键 `rollback()`。
- **总部聚合进化**：跨用户按版本比质量，晋升/回滚（`aggregate.decide`，含 margin + 最小样本）。
- **速度控制**：学习率 + 最小样本量 + margin + 灰度。**防跑偏**：clamp + 门控 + 负样本 + 回滚 + 总部冠军兜底。

## 6. 部署与运营（交付物6）
- 开源：GitHub 公开仓库（含 LICENSE/README/CONTRIBUTING/CI），用户 clone 填 Key 即用。
- HQ：一台小机器跑 collector（或 Serverless）；成本极低（只收匿名小 JSON）。
- 冷启动获客：在 AI/出海/电商社群发"开源免费视频工厂 + 自进化"；以"省 90% 成本 + 长视频 + 越用越好"为钩子。
- 社区：Issue/PR 模板、适配器贡献指南（加模型只写一个 Adapter）、排行榜（哪些参数版本质量最高，匿名）。
- 激励参与：开数据上报的用户可"优先享受全局进化后的最优参数"（利他即利己）。

## 7. 风险与应对（交付物7）
| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| 用户不开上报 | 高 | 中 | 默认关但透明告知"开了能更快变好"；本地进化即使不上报也有效 |
| 进化跑偏/越用越差 | 中 | 高 | 验证门控(严格胜出)+学习率+负样本+回滚+总部冠军兜底 |
| 隐私质疑 | 中 | 高 | 匿名 id(哈希)、白名单只采隐式信号、开源可审计、一键关 |
| 隐式信号≠真实质量 | 中 | 中 | 多信号合成 + 重生成率作强不满意代理 + 可接 Supermetrics 真实 CTR/GMV 校准 |
| 各家 API 变更 | 中 | 中 | 适配器隔离 + 能力画像 + `// VERIFY` + 回退 |
| 长视频漂移 | 中 | 中 | 续帧 + 主体恒定 + 每 N 段再锚定 + 拼接归一化 |

## 8. 分阶段落地（交付物8）
- **P0（已完成）**：本地引擎 + 长视频 + 本地自进化 + 遥测/上报骨架 + HQ MVP + 测试/文档。开源可用。
- **P1（1-2 月）**：HQ 遥测入库 + 灰度下发 + dashboard 可视化；接 Supermetrics 把真实投放表现纳入质量信号；P0 适配器接通真实 API。
- **P2（3-6 月）**：自动晋升/回滚闭环（少人工）；按品类/平台分群进化；社区适配器市场；多区域/多副本。

## 9. 可接入的 Skill / Agent / 连接器（"寻找所有可完善本项目"）
| 用途 | 接什么 | 怎么用 |
|---|---|---|
| **真实效果信号**（强化自进化质量判定） | **Supermetrics** 连接器 | 拉 Google/FB/TikTok Ads 的 CTR/完播/GMV/ROAS → 作为 ImplicitQuality 的强监督校准 |
| **行为遥测后端** | **Amplitude** 连接器 | 承接匿名事件、做漏斗/留存/版本对比，替代自建 dashboard |
| **进化/质量周报** | `marketing:performance-report` 技能 | 把 HQ 聚合数据写成"哪些变好/变坏 + 下一步"报告 |
| **多角色协作开发** | 本地 `orchestrator` / `deep-research` / `self-evolve` 技能 | 架构/调研/自进化方法论沉淀（本次已内联完成） |
| **创意业务层** | `ugc-creative-amplifier`（已建，保留） | 角度/钩子/混剪策略，填充工作流业务 Stage |
| **设计/分发** | Canva / Cloudinary 连接器 | 封面/素材设计、成片转码分发 |
| **打包成 MCP** | `mcp-builder` | 若要把引擎暴露为标准 MCP 供更多客户端调用 |

> 注：连接器需你在卡片里 Connect 后才生效；本项目对它们是"可插拔增强"，不连也能跑（本地隐式信号即可自进化）。

参考依据：SkillOpt（Microsoft，2026-05 开源，SKILL.md 即可训练参数+验证门控+文本学习率）；Hermes Agent（Nous Research，用后即学、抽取可复用技能）；长视频续帧/一致性（Veo/Seedance Extend、Continuity Frame Marker、ffmpeg xfade）。
