# 矿山设备采购工作台

采购公告采集、资料阅读、AI 研判与销售跟进的本地网页平台。两个独立模块由同一个 Git 仓库管理，目录与运行脚本使用英文命名。

## 目录

| 目录 | 职责 | 文档 |
| --- | --- | --- |
| `sales-platform/` | 平台后端、前端、采购阅读、Agent 研判及跟进 | [使用说明](sales-platform/README.md) · [架构](sales-platform/docs/architecture.md) |
| `procurement-collector/` | 来源配置、公告采集、设备采购筛选、证据归档及 JSONL 导出 | [使用说明](procurement-collector/README.md) · [数据交接](procurement-collector/integration/README.md) |

采集模块默认只接收近 30 天有效的设备整机或备件采购需求。平台通过采集 CLI 管理来源与任务，并从 `procurement-collector/data/export/records.jsonl` 只读导入。

## 安装与启动

Windows 需要 Python、Node.js 22.19 或更新版本和 pnpm。在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\sales-platform\install-dependencies.ps1
powershell -ExecutionPolicy Bypass -File .\sales-platform\start-platform.ps1 -OpenBrowser
```

也可以双击 `sales-platform/start-platform.cmd`。默认地址为 <http://127.0.0.1:8766>；双击 `sales-platform/stop-platform.cmd` 停止服务。

首次使用时，在平台配置页填写模型接口、模型名称和密钥。平台设置、密钥、数据库、证据与日志仅保存在各模块的 `data/` 内，不提交到 Git。独立采集和 Windows 每日任务的设置见采集模块说明。

## 验证

```powershell
Push-Location sales-platform
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest backend/tests -q
Pop-Location

Push-Location procurement-collector
..\sales-platform\.venv\Scripts\python.exe -m unittest discover -s tests -q
Pop-Location

pnpm --dir sales-platform/agent-runtime build
pnpm --dir sales-platform/agent-runtime test
pnpm --dir sales-platform/frontend build
```

浏览器及隔离 HTTP 验收见 [开发验收工具](sales-platform/tools/qa/README.md)。临时结果放入模块内的 `tmp/qa/`。

## 本地资料与版本历史

原两个项目的提交历史保留在本仓库中。2026-10-19 整理前的源码、Git 历史与数据库恢复点保存在本地 `archives/github-migration-2026-10-19/`。

`resources/`（设计、会议和参考演示文稿）、`assets/`（参考素材）、`archives/`（历史备份）仅保留本地。运行数据、依赖环境、构建产物和临时文件也由 `.gitignore` 排除；新克隆需安装依赖并配置业务数据。
