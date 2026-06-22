# 新适配器开发指南（插件式架构）

> 三原则：**插件式**（核心不依赖任何具体模型）· **能力驱动**（调度只读能力画像）· **动态工作流**（流程按上下文组装）。
> 新增一个模型 = 新写一个文件，核心代码零改动。

## 加一个「视频模型」
1. 新建 `adapters/video/XxxAdapter.ts`，继承 `BaseVideoModel`：
   ```ts
   export class XxxAdapter extends BaseVideoModel {
     readonly modelId = "xxx";
     readonly modelName = "Xxx";
     readonly capabilities: ModelCapabilities = { /* 精确填写！见下 */ };
     constructor(private cfg: { apiKey?: string; baseUrl?: string } = {}) { super(); }
     protected async doGenerate(p, params) { /* fetch 你的 API，返回 GenerateResult */ }
     protected async doGetStatus(taskId) { /* 轮询 */ }
     async healthCheck() { return !!this.cfg.apiKey; }
   }
   ```
2. 在 `index.ts` 的 `createEngine` 里 `models.register(new XxxAdapter({...}))`，或在你自己的代码里注册。
3. 完成。选型/成本/回退会自动把它纳入——**不用改任何核心代码**。

## 加一个「TTS / 克隆」
继承 `BaseTTSProvider`，实现 `synthesize` + （可选）`cloneVoice`/`listVoices`，填 `capabilities`，注册即可。

## 能力画像填写红线（决定选型与成本，**严禁瞎填**）
- `costPerSecond` / `costPerThousandChars`：直接影响成本估算与"省钱优先"选型。
- `generateModes` / `aspectRatios` / `maxDuration`：错填会导致候选过滤错误。
- `audioSupport=true`：表示模型出原生音频 → 混剪时本系统会静音它，改用 `@voice1`。
- `voiceCloneSupport`：只有 true 的 provider 才会被 `VoiceLockManager` 选去做克隆。
- `deploymentType="local-self-hosted"`：会被"省钱/本地优先"加权。

## 自检
- [ ] 能力画像每个字段都按真实情况填，未知的标注"待调研"而非乱填
- [ ] `healthCheck` 真实反映"是否可用"（缺 Key 返回 false 或进 demo）
- [ ] 真实 API 字段名已按官方文档核对（搜 `// VERIFY`）
- [ ] 缺 Key 有合理降级（demo 占位或明确报错 `ADAPTER_NOT_IMPLEMENTED`）
