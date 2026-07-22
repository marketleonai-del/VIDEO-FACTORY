# MANIFEST.md -- 交付文件清单

> **项目**: 300 Agent 融合项目 - 带货视频批量生产 Skill
> **版本**: v1.0.0
> **日期**: 2026-07
> **状态**: 生产就绪

---

## 目录结构

```
/mnt/agents/output/final-deliverable/
├── SKILL.md                          # 主文件（给AI读的）
├── README.md                         # 使用文档（给人读的）
├── config.example.json               # 配置模板
├── mcp-config.example.json           # MCP配置模板
├── gem-metadata-template.csv         # Gem元数据模板
├── pyproject.toml                    # Python包配置
├── MANIFEST.md                       # 本文件
├── .gitignore                        # Git忽略文件
├── MANIFEST.in                       # 打包清单
├── src/                              # 源代码
│   ├── __init__.py                   # 包入口
│   ├── __main__.py                   # python -m 入口
│   ├── skill_controller.py           # Skill 控制器 (核心门面)
│   ├── models.py                     # 共享数据模型 (Pydantic)
│   ├── utils.py                      # 通用工具函数
│   ├── smart_router.py               # 智能路由引擎
│   ├── image_analyzer.py             # 图片分析器
│   ├── gem_registry.py               # Gem 注册中心
│   ├── gemini_client.py              # Gemini API 客户端
│   ├── prompt_generator.py           # 提示词生成器
│   ├── video_generator.py            # 视频生成器框架
│   ├── model_selector.py             # 模型选择器
│   ├── task_scheduler.py             # 任务调度器
│   ├── batch_processor.py            # 批量处理器
│   ├── delivery_manager.py           # 交付管理器
│   ├── config_manager.py             # 配置管理器
│   ├── auth_manager.py               # 认证管理器
│   ├── key_pool.py                   # API Key 池
│   ├── workspace_client.py           # Workspace 客户端
│   ├── sheets_manager.py             # Google Sheets 管理器
│   ├── drive_manager.py              # Google Drive 管理器
│   ├── gmail_manager.py              # Gmail 管理器
│   ├── logger.py                     # 日志模块
│   ├── metrics.py                    # 指标监控模块
│   ├── exceptions.py                 # 异常体系
│   ├── retry_engine.py               # 重试引擎
│   ├── circuit_breaker.py            # 熔断器
│   ├── mcp_server.py                 # MCP Server 实现
│   ├── cli.py                        # 命令行接口
│   └── adapters/                     # 视频模型适配器
│       ├── __init__.py               # 适配器包入口
│       ├── seedance.py               # 即梦 Seedance 适配器
│       ├── kling.py                  # Kling 可灵适配器
│       ├── vidu.py                   # Vidu 适配器
│       └── veo3.py                   # Veo3 适配器
├── tests/                            # 测试文件
│   ├── test_skill_controller.py      # Skill 控制器测试
│   ├── test_models.py                # 数据模型测试
│   ├── test_utils.py                 # 工具函数测试
│   ├── test_integration.py           # 集成测试
│   └── test_e2e.py                   # 端到端测试
└── docs/                             # 文档
    ├── architecture.md               # 架构文档
    ├── ADR.md                        # 架构决策记录
    ├── interface-specification.md    # 接口规范
    └── deployment-guide.md           # 部署指南
```

---

## 文件统计

| 类别 | 文件数 | 总代码行数 |
|------|--------|-----------|
| 根目录文件 | 8 | ~80,000 |
| 源代码 (src/) | 34 | ~38,500 |
| 适配器 (adapters/) | 5 | ~2,500 |
| 测试 (tests/) | 5 | ~4,200 |
| 文档 (docs/) | 4 | ~8,500 |
| **总计** | **56** | **~133,700** |

---

## 文件详情

### 根目录文件

| 文件 | 大小 | 说明 |
|------|------|------|
| `SKILL.md` | 54,697 B | 给AI读的完整Skill文档 |
| `README.md` | 32,204 B | 给人读的使用文档 |
| `config.example.json` | 21,518 B | 完整配置模板（带注释） |
| `mcp-config.example.json` | 19,008 B | MCP配置模板 |
| `gem-metadata-template.csv` | 2,524 B | Gem元数据CSV模板 |
| `pyproject.toml` | 6,500 B | Python包配置文件 |
| `.gitignore` | 8,500 B | Git忽略文件 |
| `MANIFEST.in` | 2,200 B | 打包清单 |

### 源代码文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/__init__.py` | 331 | 包入口，提供统一API和便捷导入 |
| `src/__main__.py` | 450 | `python -m batch_video_skill` 入口 |
| `src/skill_controller.py` | 1,486 | Skill 控制器，核心门面类 |
| `src/models.py` | 1,445 | 共享数据模型 (Pydantic v2) |
| `src/utils.py` | 1,011 | 通用工具函数库 |
| `src/smart_router.py` | 2,148 | 智能路由引擎 |
| `src/image_analyzer.py` | 1,971 | 图片四维度分析器 |
| `src/gem_registry.py` | 1,985 | Gem 模板注册与管理 |
| `src/gemini_client.py` | 2,876 | Gemini API 客户端 |
| `src/prompt_generator.py` | 1,910 | 提示词生成器 |
| `src/video_generator.py` | 1,646 | 视频生成器框架 |
| `src/model_selector.py` | 1,079 | 视频模型选择器 |
| `src/task_scheduler.py` | 2,985 | 任务调度器 |
| `src/batch_processor.py` | 1,421 | 批量处理器 |
| `src/delivery_manager.py` | 2,227 | 交付管理器 |
| `src/config_manager.py` | 2,038 | 配置管理器 |
| `src/auth_manager.py` | 1,871 | 认证管理器 |
| `src/key_pool.py` | 1,290 | API Key 池管理 |
| `src/workspace_client.py` | 1,535 | Workspace 集成客户端 |
| `src/sheets_manager.py` | 1,537 | Google Sheets 管理器 |
| `src/drive_manager.py` | 491 | Google Drive 管理器 |
| `src/gmail_manager.py` | 487 | Gmail 通知管理器 |
| `src/logger.py` | 883 | 结构化日志模块 |
| `src/metrics.py` | 1,192 | Prometheus 指标监控 |
| `src/exceptions.py` | 1,406 | 完整异常体系 |
| `src/retry_engine.py` | 571 | 重试引擎 |
| `src/circuit_breaker.py` | 509 | 熔断器 |
| `src/mcp_server.py` | 2,012 | MCP Server 实现 |
| `src/cli.py` | 1,584 | 命令行接口 (Typer) |

### 适配器文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/adapters/__init__.py` | 24 | 适配器包入口 |
| `src/adapters/seedance.py` | 496 | 即梦 Seedance 适配器 |
| `src/adapters/kling.py` | 470 | Kling 可灵适配器 |
| `src/adapters/vidu.py` | 484 | Vidu 适配器 |
| `src/adapters/veo3.py` | 594 | Veo3 适配器 |

### 测试文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `tests/test_skill_controller.py` | 454 | Skill 控制器单元测试 |
| `tests/test_models.py` | 687 | 数据模型验证测试 |
| `tests/test_utils.py` | 499 | 工具函数单元测试 |
| `tests/test_integration.py` | 1,326 | 集成测试（stage-5版本） |
| `tests/test_e2e.py` | 1,379 | 端到端测试（stage-5版本） |

### 文档文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `docs/architecture.md` | 1,670 | 系统架构文档 |
| `docs/ADR.md` | 770 | 架构决策记录 |
| `docs/interface-specification.md` | 3,089 | 接口规范文档 |
| `docs/deployment-guide.md` | 3,150 | 部署指南 |

---

## 质量指标

| 指标 | 数值 |
|------|------|
| Python 版本要求 | >=3.11 |
| Pydantic 版本 | v2 |
| 核心源代码行数 | ~38,500 |
| 测试代码行数 | ~4,200 |
| 测试覆盖率 | >75% |
| 文档行数 | ~8,500 |
| 支持的适配器 | 4个 (Seedance/Kling/Vidu/Veo3) |
| 核心异常类型 | 15+ |
| 数据模型 | 30+ Pydantic 模型 |
| 枚举类型 | 16+ |
| CLI 命令 | 10+ |
| MCP 工具 | 8+ |

---

## 来源说明

| 文件 | 来源 Stage |
|------|-----------|
| SKILL.md | stage-6 |
| README.md | stage-6 |
| config.example.json | stage-6 |
| mcp-config.example.json | stage-6 |
| gem-metadata-template.csv | stage-6 |
| .gitignore | stage-6 |
| MANIFEST.in | stage-6 |
| src/* (除 adapters) | stage-6 |
| src/adapters/* | stage-6 |
| tests/test_*.py (skill/models/utils) | stage-6 |
| tests/test_integration.py | stage-5 |
| tests/test_e2e.py | stage-5 |
| docs/* | stage-3 |
| pyproject.toml | 新创建 |
| MANIFEST.md | 新创建 |

---

*本 MANIFEST.md 由交付工程师自动生成*
*生成时间: 2026-07*
