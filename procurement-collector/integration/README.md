# 判断平台接入说明

## 数据入口

- 文件：采集模块内的 `data/export/records.jsonl`；从仓库根目录访问时为 `procurement-collector/data/export/records.jsonl`。
- 格式：UTF-8 JSONL，每行一条 JSON；采集开始及每条记录提交后原子更新，内容为全量记录的最新版本。接收方按 ID 更新，不将每份快照视为新增整批资料。
- 当前接口是文件读取；尚无 HTTP API。判断平台部署在其他机器时，需要同步该文件或另行提供只读 API。
- 真实样例：[sample-record.json](sample-record.json)。

## 主要字段

| 用途 | 字段 |
| --- | --- |
| 唯一标识、版本 | `id`、`source_id`、`current_version`、`content_sha256` |
| 信息内容 | `kind`、`title`、`project_name`、`buyer`、`company`、`equipment` |
| 业务时间、编号 | `published_at`、`deadline_at`、`notice_number` |
| 判断依据 | `body_text`、`facts`、`inferences` |
| 原文追溯 | `canonical_url`、`original_url`、`archive_path`、`fetched_at` |
| 去重、项目关联 | `duplicate_of`、`possible_duplicate_of`、`related_record_id` |
| 解析质量 | `parse_status` |
| 采购附件 | `attachments`：独立于公告正文的附件数组 |

`kind`：`tender` 招采、`inquiry` 询价、`correction` 更正、`award` 成交/中标、`news` 新闻。缺失值为 `null`；设备、事实、推测为数组。时间戳中的 `+00:00` 表示 UTC；发布时间和截止时间是来源标示的日期或本地时间。

`facts` 元素含 `text`、`evidence`；`inferences` 元素含 `text`、`evidence`、`status`、`trigger`，其中推测标为“待核实”。判断平台应结合来源和正文区分矿业权动态、企业新闻与设备采购。

## 接入规则

1. 按 `id` 更新或插入，保存版本和哈希，避免每次全量读取都重复创建项目。
2. `current_version`、正文哈希或附件哈希变化时检查是否需要重新判断；网页模板变化也可能改变哈希，应结合 `body_text` 比对。
3. `duplicate_of` 有值时关联原记录；`possible_duplicate_of` 只表示疑似重复；`related_record_id` 表示同项目的其他公告。
4. `parse_status` 非 `ok` 时检查解析问题；`ai_fallback` 表示模型调用失败后保留了规则结果。推测不能当作已发布采购事实。
5. `archive_path` 相对采集系统的 `data` 目录。当前导出只含最新版本；历史原件在 `data/archive/`，历史元数据在 `data/collector.sqlite3`。

## 最小读取示例

```python
import json
from pathlib import Path

# 在仓库根目录执行。
path = Path("procurement-collector/data/export/records.jsonl")
with path.open(encoding="utf-8") as f:
    for line in f:
        record = json.loads(line)
        # 按 record["id"] 更新或插入，再按正文和证据做判断。
```

每天 07:30 计划采集，完成后更新导出；电脑关机时无法按时执行。判断平台应检查文件更新时间，配合 `data/trial-status.md` 和 `data/reports/` 的异常栏识别缺失来源。

## 采购附件（2026-10-10 增补）

采购公告会发现正文区域外的公开附件，包括鞍钢平台 `ztbfjyz(...)` 链接；不执行网页 JavaScript。最多读取同来源的 4 个公开文件，每文件 12 MB。需要登录或无法解析的文件保留状态，不能当作已获取完整采购信息。

`attachments` 每项含 `name`、`url`、`status`、`sha256`、`mime_type`、`byte_size`、`fetched_at`；下载成功另含 `archive_path`；可解析 PDF 另含 `text` 和 `pages`。`status` 为 `parsed`、`unsupported`、`auth_required`、`failed` 或 `unreadable`，失败原因在 `error`。旧记录尚未补采时为 `[]`。

附件正文不会拼入 `body_text`。原件按记录和版本保存在 `archive/{id}/v{version}-attachments/`，元数据在 `version_attachments` 表。公告网页未变化但附件内容变化，也会产生新版本，保留前一版文件。判断平台应保留这些字段，并在需要采购明细时进一步解析表格；`parsed` 仅说明有可读文本，不代表已识别所有业务字段。旧版 `.doc` 暂不自动解析，不能把这一缺口标为完成。
