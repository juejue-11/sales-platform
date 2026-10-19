# 项目架构与开发约定

本约定用于管理目录和依赖。重构以现有业务行为、接口协议和数据历史为验收依据。移动代码时，先建立运行基线，再在独立 Git worktree 中改动和验证。

## 目录职责

后端根目录只保留包定义、启动入口和稳定的调查来源配置。源码按职责放入下列目录：

```text
backend/
├── __init__.py
├── main.py                  # backend.main:app
├── research_sources.json    # 保持正在运行的进程的读取路径
├── application/             # 平台编排、导入
├── analysis/                # 分析策略、报告、模型协议、规则与摘要
├── documents/               # 附件读取、片段访问、Word 转换
├── research/                # 项目调查
├── storage/                 # 数据库结构与连接
├── core/                    # 项目配置、共同基础函数
├── procurement/             # 采购事实、归一化与聚合
└── tests/                   # 回归测试
```

| 目录 | 职责 | 依赖规则 |
| --- | --- | --- |
| `backend/main.py` | HTTP 入口、应用生命周期和静态页面入口 | 调用平台应用逻辑；保留 `backend.main:app` 启动入口 |
| `backend/application/` | 应用编排、事务、导入与任务状态 | 负责获取数据并调用业务模块；纯计算逐步移出 |
| `backend/collection/`、`backend/routes/collection.py` | 采集模块的进程调用、异步任务、导入衔接和平台入口 | 只通过采集 CLI 控制来源与任务，不导入相邻源码，不直接写采集数据库或配置 |
| `backend/procurement/` | 采购资料模型、阅读、事实校验与补充资料 | `model`、`facts`、`reading`、`milestones` 纯计算；`supplements` 单独管理文件及纠正持久化。均不依赖 `service.py` |
| `backend/procurement/facts.py` | 从公告正文提取明确采购事实 | 不将未核实推测写成采购事实 |
| `backend/documents/` | 公开附件读取、归档复用、片段读取权限与文档转换 | 保留来源、哈希、页码和访问限制 |
| `backend/research/` | 项目调查及历史证据 | 后续拆分其内部网络与持久化职责，保持任务授权和证据版本 |
| `backend/analysis/`、`agent-runtime/` | 分析策略、报告、规则、摘要、模型调用及 Agent 执行 | 通过已有进程协议通信；密钥不进入命令行 |
| `backend/storage/` | SQLite 结构与连接 | 数据位置仍由项目配置指定 |
| `backend/core/` | 项目路径、配置与共同基础函数 | 不依赖应用编排；不把业务职责继续堆进通用函数 |
| `frontend/src/pages/` | 页面导航、请求与用户动作编排 | 组合展示模块；不供展示模块反向导入 |
| `frontend/src/components/procurement/` | 采购信息展示与展示规则 | 通过明确的 props 接收数据；不管理页面轮询或人工决策表单 |
| `frontend/src/components/report/`、`research/` | 分析报告与项目调查展示 | 保持各自展示职责 |
| `frontend/src/utils/` | 多处调用的纯计算 | 不执行网络请求、不管理页面状态 |
| `backend/tests/` | 临时数据库和替代外部依赖的自动测试 | 不连接用户数据库或真实付费模型 |
| `backend/followup/` | 计划提议、用户采用、任务、材料与真实提交留痕 | 接收数据库和采购说明，不依赖采集、模型或 HTTP |
| `backend/routes/` | 工作台 HTTP 输入与错误映射 | 调用 application module，不在路由实现采购阅读和跟进规则 |
| `tools/qa/` | 当前工作台的浏览器与隔离 HTTP 验收工具 | 只用完整 Mock 或独立数据库；运行方式见目录 README，产物写入 `tmp/qa/` |
| `docs/` | 契约、模块职责、验收与开发说明 | 不保存运行日志、截图和用户数据 |
| `data/` | 主数据库、运行记录、近期诊断与业务恢复点 | 业务资料与配置保存在主数据库；保持运行路径，不提交到 Git，不存放验收数据库或截图 |
| `tmp/` | 临时验证产物 | 不作为程序代码或文档的长期入口 |

根目录保留 README、依赖清单、接口约定、启动/停止/安装脚本。不要为了目录整齐搬动运行入口。`node_modules/`、`dist/`、`.venv/` 是依赖或构建产物，不放业务代码。

## 验证和切换

1. 开发与验证使用隔离工作区、临时数据库和 Mock 模型；显式配置 `SCREENING_DB_PATH`、`SCREENING_IMPORT_PATH` 和 `SCREENING_ARCHIVE_ROOT`。
2. 按改动范围运行后端回归、Agent 协议测试、前端类型检查与构建。浏览器和隔离 HTTP 验收入口见 [tools/qa](../tools/qa/README.md)，产物统一输出到 `tmp/qa/`。
3. 保持 `backend.main:app`、公开接口和采集交接协议兼容；重构内部导入时使用真实职责目录，不注册旧模块别名。
4. 启动脚本未开启自动重载。需要切换运行版本时，验证新进程启动与关闭，再正常停止旧服务并启动新版，避免两个后台消费者连接同一业务数据库。

整理源码和文档时不改业务数据、归档或正在使用的静态页面。`backend/research_sources.json` 保持原位置；运行中的进程可能继续读取它。

## 两个系统的交接

采集系统输出 `data/export/records.jsonl`，筛选系统只读导入。字段、正文、版本、附件及归档路径是跨系统契约。采集系统内部模块拆分不改变导出文件位置；筛选系统模块拆分不改变采集定时任务和归档位置。

两个模块位于同一仓库的 `sales-platform/` 和 `procurement-collector/`。默认采集路径按平台目录定位到相邻模块，不依赖本机用户名。环境变量与平台设置可指定其他位置；移动已有部署时须同步迁移保存的导入路径和 Windows 任务操作。

统一平台的采集管理见 [采集接入说明](collection-integration.md)。采集 CLI 持有数据目录的操作系统锁，平台手动执行与原 Windows 定时任务共用此锁。每日任务仍由 Windows 任务计划负责，不在 Web 进程另建定时器。平台发起的采集完成后立即执行既有导入；定时任务的导出由既有自动导入轮询接收。

采购阅读、用户补充资料、人工纠正与真实跟进的具体职责见 [采购工作台说明](procurement-workbench.md)；模型工具、报告及引用约束见 [Agent 实现约定](agent-framework.md)。
