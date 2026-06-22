# 贡献指南

## 开发
```bash
npm install
npm run typecheck   # 类型检查
npm test            # 单元测试（core + runtime + commercial + server）
npm run example     # 离线 demo
```

## 加一个模型/TTS 适配器（插件式）
1. 在 `adapters/video/` 或 `adapters/tts/` 新建 `XxxAdapter.ts`，继承 `BaseVideoModel`/`BaseTTSProvider`。
2. **精确填写能力画像**（价格/时长/模式/音频/克隆…），未知项标注"待调研"而非乱填。
3. 实现 `doGenerate`/`synthesize`（+可选 `cloneVoice`），按官方文档核对端点（搜 `// VERIFY`）。
4. 在 `index.ts` 的 `createEngine` 注册。核心代码零改动。
5. 补一条单测，跑 `npm test`。

详见 `references/adapter-guide.md`。

## 代码规范
- TypeScript strict；公共 API 带 JSDoc；错误用 `ApiError`/`UVGError` 类型化。
- 零运行时依赖（仅 Node 内置）——新增依赖需讨论。
