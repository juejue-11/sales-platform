# 开发验收工具

这里保存当前采购工作台的可复用验收脚本。启动平台不会运行这些工具；后端日常回归仍使用 `backend/tests`。

| 文件 | 用途 |
| --- | --- |
| `workbench_mock.cjs` | 拦截全部 API，验证筛选、详情页签、采购要求默认可读与分类切换、跟进任务、材料和提交确认，以及阅读失败重试 |
| `serve_fixture.py` | 使用公开 #224 样本启动隔离的真实后端，关闭模型和自动导入 |
| `workbench_api.cjs` | 针对上述隔离后端，验证日期、包号、资格例外及跟进提交的真实 HTTP 保存流程 |
| `procurement_grouping.cjs` | 验证物料分组、数量精度、单位冲突与来源保留，不启动浏览器 |
| `opportunity_text.cjs` | 验证需求摘要中的型号、金额、数量和日期处理，不启动浏览器 |
| `browser_support.cjs` | 共用的 Playwright 加载和截图输出配置，不单独执行 |
| `serve_collection_fixture.py` | 复制采集源码到临时目录，提供本机模拟公告和独立数据库，关闭模型与 Windows 任务访问 |
| `collection_api.cjs` | 验证真实采集 CLI、自动导入、重复运行互斥、来源启停、部分成功、重复导入和手机页面 |
| `collection_stream.cjs` | 将第二条本机模拟公告阻塞，验证第一条在整批结束前可见，第二条随后自动出现且筛选草稿保留 |

使用项目要求的 Node.js 22.19 或更新版本，并先安装前端开发依赖。浏览器脚本还需要 Playwright 和 Microsoft Edge。优先使用开发环境中安装的 Playwright；也可设置 `PLAYWRIGHT_MODULE_PATH` 指向模块目录。本机已有的 Codex Playwright 可直接识别。

截图和临时验收数据库统一放在项目的 `tmp/qa/`，该目录不进入 Git。`QA_OUTPUT_DIR` 可以覆盖浏览器截图根目录。临时产物不写入业务 `data/`。

## 前端规则检查

在项目根目录执行，不需要启动平台或浏览器：

```powershell
node tools/qa/procurement_grouping.cjs
node tools/qa/opportunity_text.cjs
```

物料检查使用 `frontend/node_modules/typescript`；开发依赖安装在其他位置时可设置 `TYPESCRIPT_MODULE_PATH`。

## 页面与交互回归

在项目根目录打开两个终端。先启动已有前端构建的独立预览：

```powershell
cd frontend
pnpm exec vite preview --host 127.0.0.1 --port 8878 --strictPort
```

在项目根目录运行：

```powershell
node tools/qa/workbench_mock.cjs http://127.0.0.1:8878
```

如果修改过前端源码，应先在前端开发流程中构建对应版本。这个脚本的 API 请求全部由 Mock 接管，不调用真实模型。

## 真实 HTTP 保存流程

在项目根目录为此次验收创建新的临时数据库，并启动独立后端：

```powershell
$qaRun = Join-Path (Get-Location).Path ('tmp/qa/api-' + [guid]::NewGuid().ToString('N'))
$env:SCREENING_DB_PATH = Join-Path $qaRun 'screening.sqlite3'
$env:SCREENING_IMPORT_PATH = Join-Path $qaRun 'records.jsonl'
$env:SCREENING_ARCHIVE_ROOT = $qaRun
$env:SCREENING_QA_PORT = '8877'
$env:OPENAI_API_KEY = ''
$env:OPENAI_ENABLED = '0'
.\.venv\Scripts\python.exe -m tools.qa.serve_fixture
```

在另一个终端的项目根目录运行：

```powershell
node tools/qa/workbench_api.cjs http://127.0.0.1:8877
```

验收服务拒绝使用业务 `data/` 内的数据库。浏览器提交前检查本机地址、专用端口和验收服务标识；只在临时数据库中保存跟进记录。前端静态页面由已有的 `frontend/dist` 提供。结束后按 `Ctrl+C` 停止验收服务，并关闭专用终端，避免将验收环境变量带入日常平台启动。

## 采集到导入的真实流程

先构建当前前端，再使用全新的验收目录启动（不要复用已有业务目录）：

```powershell
$env:SCREENING_QA_ROOT = Join-Path (Get-Location).Path ('tmp/qa/collection-' + [guid]::NewGuid().ToString('N'))
$env:SCREENING_QA_COLLECTOR_SOURCE = Join-Path (Split-Path -Parent (Get-Location).Path) '销售平台信息采集系统'
$env:SCREENING_QA_COLLECTOR_PYTHON = (Get-Content (Join-Path $env:SCREENING_QA_COLLECTOR_SOURCE 'data/python_path.txt') -Raw).Trim()
$env:SCREENING_QA_PORT = '8879'
.\.venv\Scripts\python.exe -m tools.qa.serve_collection_fixture
```

在另一终端执行 `node tools/qa/collection_api.cjs http://127.0.0.1:8879`。脚本先核对隔离服务标识；来源配置和数据库均在临时副本，所有采集请求只访问本机模拟站点，不访问真实采购站点，也不修改真实任务计划。验收结束后停止服务。

逐条展示验收另用全新的临时目录，并在启动前设置 `$env:SCREENING_QA_STREAM = '1'`，运行 `node tools/qa/collection_stream.cjs http://127.0.0.1:8879`。此模式提供两条公告，第二条等到测试显式释放才响应；自动导入关闭、模型关闭，仍必须在采集进程运行中看到第一条。普通整合验收不设置此变量。

## 已清理的内容

原 `qa/` 中针对旧机会池页面、特定项目编号、旧 SDK 迁移及单次 JSON 故障的脚本不再作为维护入口；历史报告中出现的旧 `qa/...` 路径仅记录当时的验证过程。真实模型调用和生产数据修复脚本不纳入日常验收。

后端业务回归使用 `python -m pytest backend/tests -q`；SDK 的本地 SSE、工具协议和超时等检查在 `agent-runtime/` 执行 `pnpm run build` 后运行 `pnpm test`。这些仍由各自模块维护。

新增验收工具应能说明当前覆盖的功能，并使用独立数据库或完整 Mock；一次性排查结束后删除临时脚本，避免再次平铺堆积。
