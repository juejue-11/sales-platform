# 后端目录导航

启动入口是 `main.py`，继续使用 `backend.main:app`。根目录的 `research_sources.json` 是运行配置，保持路径以供现有服务读取。

| 目录 | 从这里查找 |
| --- | --- |
| `application/` | 平台业务编排、任务与导入流程 |
| `analysis/` | AI 调用、判断策略、报告校验、初筛与摘要 |
| `procurement/` | 采购事实提取、文档归一化与采购资料聚合 |
| `documents/` | 公开附件、分页片段与 Word 转换 |
| `research/` | 项目补充调查、来源及调查历史 |
| `storage/` | SQLite 表结构与连接 |
| `core/` | 项目路径、配置与共同基础函数 |
| `tests/` | 后端行为回归与运行路径检查 |

内部导入使用真实职责目录，例如 `backend.application.service`、`backend.analysis.ai`。`__init__.py` 不批量导入其他职责，也不注册导入别名。

职责与运行约定见 [项目架构说明](../docs/architecture.md)。
