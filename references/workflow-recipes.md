# 常用工作流配方（Dynamic Workflows）

> 工作流不是写死的：`WorkflowBuilder.build(ctx)` 按 输入模式 / 预算 / 是否有素材 / 是否矩阵 / 平台
> 动态拼 Stage。下面是几条典型配方（实际由 Builder 自动组装）。

## 配方 A · 从0起单条（from-scratch, matrixCount=1）
```
model-selection → voice-lock → angle-discovery → script-generation → storyboard
→ ai-hook-generation → [ai-b-roll(标准/精品)] → narration-synthesis → video-assembly
→ quality-gate → cost-report
```
用途：只有产品，要一条精品母版。

## 配方 B · 自有素材混剪（from-materials）
```
model-selection → voice-lock(克隆真人声) → storyboard → material-analysis
→ ai-hook-generation(无人脸视觉钩子) → narration-synthesis(@voice1 统一音轨)
→ video-assembly(AI钩子 + 真素材有序拼接) → quality-gate → cost-report
```
用途：AI 做 3 秒钩子，后接真素材；音色用真人克隆 → 全片一致。

## 配方 C · 赢家放大矩阵（from-winner, matrixCount=20）
```
model-selection → voice-lock → ai-hook-generation → narration-synthesis
→ video-assembly → quality-gate → matrix-variants(钩子/人设轮换) → platform-adaptation
→ cost-report(克隆一次性摊薄)
```
用途：1 个精品母版 → N 条账号差异化、防限流变体；逐条过同一质检闸。

## 配方 D · 极省档（budget=minimal）
- AI 仅 3 秒钩子，无 ai-b-roll；优先本地模型（Wan）+ 开源 TTS（CosyVoice，0 成本）。

## 预算驱动的降级/升级
| 档 | AI 秒数 | B-roll | 口型同步 | 模型偏好 |
|---|---|---|---|---|
| minimal | 3 | 否 | 否 | 本地/开源最省 |
| standard | 6 | 是 | 否 | 可灵/Wan + @voice1 |
| premium | 12 | 是 | 是 | 多 AI 镜 + 数字人口型同步 + 1080p |

## 回退机制
`ai-hook-generation` 用 `generateWithFallback`：首选模型失败 → 自动换下一个候选（按能力画像排序）。
