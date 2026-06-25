# VIDEO-FACTORY v3.0 Stable 蜂群审核报告

审核日期: 2026-06-25
审核分支: `v3.0-stable`
基线提交: `c61893f7417ebc91fe6fe1c44536c2176d99febb`

## 结论

不推荐直接发布为“真实出片稳定版”。

代码层面的发布阻塞已基本修复，`npm test` 已从 1/10 提升到 10/10，服务、Web UI、CLI、健康检查和 300-Agent dry-run 均可运行。但真实出片 smoke test 未拿到最终 `final.mp4`: 丽帧任务创建成功后轮询状态持续为空直到超时，Agnes 回退请求在 120 秒超时。因此当前可作为 `v3.0-stable` 候选修复分支，不应标记为已完成真实出片发布版。

代码质量评分: 8.0/10

## 蜂群审核摘要

本次按 300-Agent 审核矩阵落地为四组自动检查和人工审查:

| 组别 | 范围 | 结论 |
| --- | --- | --- |
| A 架构审核 | 模块边界、导入导出、端口、路由、部署一致性 | 发现并修复 CommonJS 导出、端口硬编码、缺失健康检查、UI 根路由缺失等问题 |
| B 功能审核 | live-server、CLI、creative、quality、evolution、audio、swarm | 单测 10/10 通过；CLI 状态命令可用；生成链路可进入外部供应商 |
| C 代码质量审核 | 错误处理、配置、重复、敏感信息、安全边界 | 增加 `.env` 本地加载、端点环境变量覆盖、静态文件路径保护；未提交密钥 |
| D 可运行性审核 | 语法、测试、服务、UI、CLI、真实 smoke | 除真实最终 mp4 外均通过 |

## 问题统计

发现问题: 18 项

严重级别分布:

| 级别 | 数量 | 状态 |
| --- | ---: | --- |
| P0 发布阻塞 | 6 | 修复 4，外部供应商未闭环 2 |
| P1 高风险 | 8 | 修复 8 |
| P2 可维护性 | 4 | 修复 4 |

已修复: 16 项
未修复/外部阻塞: 2 项

## 已修复问题

1. `reqJSON()` 带请求体时仍默认 `GET`，导致视频供应商创建任务接口被错误调用。
2. `live-server.js` 端口硬编码 `8088`，无法用 `WEB_PORT`/`PORT` 切换。
3. Docker healthcheck 指向 `/api/health`，但服务未实现该路由。
4. 根路径 `/` 未绑定 Web UI，浏览器打开服务会 404。
5. 本地 `.env` 不会自动加载，导致本机验收需要手动注入环境变量。
6. 多个模块仅命名导出，测试和默认导入调用失败。
7. `VoiceLockManager.selfCheck()` 在无 TTS adapter 时访问 `undefined.constructor` 崩溃。
8. `HookEngine.generateAngleCard()` 返回报告对象，外部调用期望数组。
9. `MatrixEngine.generateMatrixTable()` 对简化 winner 输入过严，且成功结果缺少顶层 `variants`。
10. CLI `generate`/`matrix` 只接受 HTTP 200，不接受服务端创建任务的 202。
11. Agnes 回退仍使用旧模型 `agnes-video-v1` 和旧参数。
12. Agnes/丽帧响应解析只读 `data` 包装，无法兼容顶层 JSON 响应。
13. 外部 API endpoint 不支持环境变量覆盖。
14. DeepSeek 返回分镜数量可能超过 `segments` 参数，导致单段 smoke 被放大为多段任务。
15. 视频提示词过长，触发丽帧 `request_body` 字段长度错误。
16. 静态资源 MIME 类型缺少 HTML/CSS/JS 支持。

## 验证结果

| 检查项 | 命令/方式 | 结果 |
| --- | --- | --- |
| JS 语法检查 | `node --check` 覆盖 tracked `*.js` | 通过，28 个 JS 文件 |
| 单元测试 | `npm test` | 通过，10/10 |
| 服务启动 | `WEB_PORT=18088 node live-server.js` | 通过 |
| 配置接口 | `GET /api/config` | 200 |
| 健康检查 | `GET /api/health` | 200 |
| Web UI | Chrome headless 打开 `/web/public/index.html` 截图 | 通过，页面渲染成功 |
| CLI | `VF_API_URL=http://127.0.0.1:18088 node bin/cli.js status` | 通过 |
| 蜂群协议 | `node start-swarm.js --agents=300 --mode=dry-run` | 通过 |
| 敏感信息 | `.env`/生成产物未纳入 git | 通过 |

## 真实出片 smoke test

输入:

- 图片: `https://picsum.photos/seed/video-factory-smoke/720/1280.jpg`
- 参数: `segments=1`
- 本地密钥来源: `D:\videofactory\(1).env` 临时复制为工作区 `.env`，未加入 git

结果:

- `/api/health` 显示 DeepSeek、hfsy、丽帧、Agnes、Kimi 均已配置。
- `/api/generate` 返回 `202`，任务创建成功。
- 产品洞察、分镜生成可运行，`segments=1` 已生效。
- hfsy 图片阶段在最终单段 smoke 中未生成关键帧。
- 丽帧创建任务成功，但 `/status` 轮询 60 次状态为空，最终超时。
- Agnes 回退 `POST https://apihub.agnes-ai.com/v1/videos` 120 秒超时。
- 最终未生成 `videoUrl`，无本地 `final.mp4`，`ffprobe` 未执行到。

判定: 代码链路已进入真实供应商，但真实出片未通过。

参考: Agnes 当前公开视频接口文档说明视频模型为 `agnes-video-v2.0`，创建任务为 `POST /v1/videos`，轮询为 `GET /v1/videos/{task_id}`: https://agnes-ai.com/doc/agnes-video-v20

## 已知问题

1. 丽帧任务状态接口返回结构与当前解析预期仍不匹配，任务创建成功后状态字段为空，需要拿供应商真实响应样例继续适配。
2. Agnes 视频接口在本机 smoke 中 120 秒超时，可能是供应商排队、额度、网络或模型可用性问题。
3. hfsy 图片接口偶发超时或 socket hang up，生成链路已有降级，但最终视频质量依赖上游稳定性。
4. `live-server.js` 仍是大文件，长期建议拆分为 server/router/providers/jobs 四层；本次未做大重构以保持稳定。

## 发布建议

当前不建议合并到 `main` 或对外宣称稳定出片版。

建议下一步:

1. 向丽帧拿 `/status` 的真实响应格式，修正 `pollKuaiziTask()` 状态和视频 URL 解析。
2. 对 Agnes 单独做最小 curl 级验证，确认 key、额度、模型排队和可用参数。
3. 增加 provider-level mock/fixture 测试，避免供应商响应结构变化再次破坏核心流程。
4. 真实出片成功并通过 `ffprobe` 后，再把推荐发布改为“是”。
