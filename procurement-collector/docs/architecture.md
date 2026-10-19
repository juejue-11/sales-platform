# 模块与目录约定

采集系统通过 `python -m collector` 提供稳定命令入口，通过 `data/export/records.jsonl` 向筛选平台提供数据。内部解耦不能改变来源登记、去重阈值、版本历史、附件证据、导出字段或定时任务路径。

## 目录职责

| 位置 | 职责 |
| --- | --- |
| `collector/__main__.py` | 命令参数、默认路径和输出；保持现有子命令与退出码 |
| `collector/runner.py` | 定时运行入口和运行日志 |
| `collector/control.py` | 独立 JSON 管理入口，读取状态并原子修改来源启用设置；状态只读 SQLite |
| `collector/locking.py` | 数据目录级操作系统锁，协调 CLI、平台手动采集、定时采集和配置变更 |
| `collector/scheduler.py` | 管理既有 Windows 每日任务，固定脚本和参数、超时与权限失败反馈 |
| `collector/pipeline.py` | 采集、提取、入库、版本处理和重处理的流程编排 |
| `collector/identity.py` | 同源记录识别、公告关联、重关联和跨源重复识别；不访问网络 |
| `collector/fetch.py` | 来源访问、列表与详情解析 |
| `collector/extract.py`、`ai_extract.py` | 规则字段提取和可选模型提取 |
| `collector/eligibility.py` | 发布时间窗口、明确提交截止及设备采购范围的纯规则判断；不访问网络或数据库 |
| `collector/attachments.py`、`legacy_word.py` | 附件发现、读取、转换和归档 |
| `collector/export.py` | 面向筛选系统的 JSONL 输出契约和原子文件替换 |
| `collector/report.py` | 日报和连续试运行状态；兼容旧 `export_jsonl` 导入入口 |
| `collector/db.py` | 数据库结构与连接 |
| `tests/` | 临时数据库、替代网络响应和导出契约回归 |
| `integration/` | 跨系统接入说明和样例，不存真实运行数据 |
| `docs/` | 架构、迁移和开发约定 |
| `data/` | 数据库、原文/附件归档、导出、日报和日志；保持原位置 |

根目录保留来源配置、依赖清单、README 和定时/安装脚本。目录管理按职责执行，不为了缩短文件长度新增通用 `utils` 或逐层转发。身份识别和导出不得反向依赖采集编排；网络访问留在来源读取职责内。

## 渐进迁移

本轮分离记录匹配和数据导出，保留 `collector.pipeline` 的既有函数入口与 `collector.report.export_jsonl`。CLI 的默认来源和数据根路径、SQL、提交时机、阈值及定时入口保持现有机制。

后续可以分别考虑流程事务、附件存储和来源解析。跨系统共享 Word 转换实现需要独立打包与发布方案；本轮不通过相邻目录的 `sys.path` 拼接制造新的部署依赖。

## 安全验证

1. 使用 `data/python_path.txt` 指定的运行环境，测试全部使用临时数据目录和替代网络响应。
2. 禁用测试进程的 `OPENAI_API_KEY` 和 `OPENAI_MODEL`，执行 `python -m unittest discover -s tests -v`。
3. 用临时配置和临时数据目录验证 CLI 子命令及 JSONL；不要把真实 `run`、`reprocess`、`export` 当作普通测试。
4. 采集与筛选使用各自的 Git 仓库，主分支保存通过验证的完整源码。并行写入使用隔离 worktree，合入后移除临时工作树；数据库、密钥和运行数据不提交到 Git。
5. 确认没有正在运行的采集进程后合入，保持 `run_daily.ps1`、任务计划与默认路径。出现回归时从源码备份恢复改动文件。

SQLite 数据库和原文证据不是源码回滚对象。JSONL 持续使用临时文件完成写入后原子替换，写入失败应保留原导出文件。

正式采集在列表阶段过滤已知超龄公告，在详情解析后、附件下载／AI 抽取／原文入库前执行完整采集规则。被过滤条目单独记录运行、来源、标题、链接、日期与原因，日报可追溯；不把主动过滤当作抓取失败。低级 `ingest` 仍可用于历史归档验证，`run` 统一传入日期窗口并强制执行新公告采集规则；历史重提取和全量导出不清理旧记录。

## 统一平台入口

筛选平台通过子进程调用 `python -m collector.control --config CONFIG --data DATA`，标准输入为 JSON：`status` 读取来源、运行与每日任务；`source` 修改单来源启用状态；`schedule` 修改每日任务。实际采集继续调用既有 `python -m collector ... run`。不把平台编排、采购阅读或企业研判放进采集模块。

所有写库 CLI 与管理修改使用同一数据根锁，进程退出自动释放。繁忙返回 3，来源部分失败继续返回 2 并保留导出。状态请求不创建业务数据库；失去锁的历史运行标为中断。`COLLECTOR_SCHEDULER_DISABLED=1` 完全关闭 Windows 任务访问，用于隔离验收。

CLI 在启动时发布已提交快照，通过 `pipeline.run(on_record=...)` 在每条成功提交后调用现有原子导出。回调失败作为发布错误抛出，不算网络/条目采集失败，也不能报告整批成功。`source_runs` 和 `runs.stats_json` 随每条处理提交进度。发布快照保持原格式，消费模块不依赖采集流程实现。
