# 筛选平台接口约定

本文件是实现约定。采集系统与原报告只读。前后端实现分别由独立 worktree 完成。所有时间使用带时区 ISO 字符串，业务日期用 Asia/Shanghai。

## 运行

- 后端 `backend/main.py` 导出 `app`，FastAPI，默认127.0.0.1:8766；启动自动导入和定期检查文件。
- 前端 `frontend/` 为 React、TypeScript、Vite、Ant Design，开发代理 `/api` 到8766，生产 dist由后端同源提供。
- 后端单独 SQLite 存储 `data/screening.sqlite3`，不修改采集系统数据。
- 默认文件路径：`C:/Users/cgr/Desktop/new/销售平台信息采集系统/data/export/records.jsonl`。
- 设置、API密钥只落后端忽略的data目录；服务返回has_api_key，不返回密钥。模型未配置时显示not_configured，不伪造AI方案。

## 公共字段

ProjectSummary:
```
id: number
title: string
buyer: string|null
equipment: string[]
kind: string
source_id: string
record_ids: number[]
published_at: string|null
deadline_at: string|null
updated_at: string
gate_status: candidate|review|excluded|expired|closed|signal|duplicate
gate_reasons: string[]
ai_status: not_configured|idle|skipped|queued|running|succeeded|failed|stale
ai_decision: recommend|consider|verify|not_recommended|null
ai_summary: string|null
demand_summary: string|null
ai_next_actions: string[]
briefing: {
  record_id: number|null
  scope_excerpt: string
  delivery_excerpt: string
  qualification_excerpt: string
  contact_excerpt: string
  original_url: string|null
}
needs_review: boolean
decision: {status: new|following|verify|closed, owner: string, next_action: string, note: string, selected_option_id: string|null, judgment_id?:number|null, updated_at: string|null}
```

`briefing`来自项目最新记录的`body_text`确定性原文摘录，不调用AI；未知字段为空字符串，新闻仅允许正文实质范围摘录，资格与交付字段为空。摘要中的`demand_summary`和`ai_next_actions`仅来自当前未过期、未阻塞的真实判断。

Judgment:
```
id:number, created_at:string, model:string, input_fingerprint:string, stale:boolean
decision:recommend|consider|verify|not_recommended
summary:string, demand_summary:string
reasons: {text:string,evidence:{record_id:number,quote:string}[]}[]
unknowns:string[], next_actions:string[]
options:{id:string,title:string,description:string,suitable_for:string,tradeoffs:string[],unknowns:string[],next_action:string}[]
```
资料不足允许无options。新闻只能输出核实方向，不能把推测当采购事实。模型结果验证证据、项目ID、字段和方案数量后才能入库。

AgentRun:
```
id:number, job_id:number, engine:'pi_sdk', status:running|succeeded|failed
started_at:string, finished_at:string|null, error:string|null
steps:{stage:started|model|tool_start|tool_end|completed,label:string,tool?:string,turn?:number,at:string}[]
usage:{model_calls:number,tool_calls:number,input_tokens:number,output_tokens:number}
limits:{max_turns:number,max_tool_calls:number,timeout_ms:number,max_output_tokens:number}
```
`steps`只保存受信任的阶段和工具标签（读取项目资料、读取公司能力、读取关联资料及其开始/完成/失败状态），不保存模型思维、正文输出或密钥；运行时的`completed`事件会被忽略，只有判定结果通过验证并落库后才追加`方案已通过校验`，运行状态不会提前显示为`succeeded`。发送给模型的每条正文最多30000字符，截断正文保留`body_text_sha256`用于完整输入指纹。

`idle`表示符合条件但尚未排队，`skipped`表示规则排除/截止等无需AI。只有真实任务才可显示queued/running。人工选择绑定当时judgment_id，新判断option_id相同也必须重新确认；旧选择和旧下一步保留可追溯。

## API

- `GET /api/health` => `{ok:true,app:"sales-screening"}`
- `GET /api/dashboard` => `{total_records,total_projects,gate_counts,ai_counts,ai_decision_counts,decision_counts,deadline_soon:ProjectSummary[],deadline_soon_count,data_updated_at:string|null,last_import_at,import_error:string|null,file_stale,import_pending,source_notes,ai_configured:boolean}`。统计覆盖全部项目，截止预览最多30条；推荐仅使用有效AI结论。
- `GET /api/projects?q=&gate=&ai_status=&ai_decision=&decision=&kind=&source_id=&deadline_soon=false&active_only=false&page=1&page_size=30&sort=updated` => `{items:ProjectSummary[],total:number,page:number,page_size:number}`。过滤空值表示全部；`active_only=true`仅返回`candidate`/`review`项目；sort支持updated、deadline；deadline_soon筛选未来7天截止且可继续处理的项目。`q`同时搜索标题、采购方、设备和`briefing.scope_excerpt`。
- `GET /api/projects/{id}` => summary字段加 `{records:Record[],judgment:Judgment|null,judgment_history:Judgment[],decision_history:object[],related_projects:ProjectSummary[],versions:object[],agent_run:AgentRun|null}`。Record保留id/title/body_text/facts/inferences/original_url/canonical_url/archive_path/parse_status/possible_duplicate_of/related_record_id等采集字段。关联提示不自动合并，只有可靠标识才能合并；人工关联另外留痕。
- 项目摘要与详情包含 `procurement`：`{status:not_checked|partial|complete|unavailable,scope_summary,items,delivery_period,delivery_location,missing_fields,documents,updated_at}`。采购附件解析结果持久化在独立文档表，不覆盖记录正文；`documents` 仅返回元数据，明细项保留 `source_document_id,record_id,page,quote` 来源定位。
- `POST /api/projects/{id}/documents/refresh` => `{procurement,message}`。该接口只抓取并解析当前项目的公开附件，不调用模型；存在未解析附件或缺少明细/数量单位/规格材质/交期交地时不能返回 `complete`。
- Agent protocol 2：Python 首行发送 `{protocol:2,...}`；运行时通过 stdout 请求 `{type:"tool_request",id,tool:"fetch_project_documents",arguments:{record_id?:number}}`，服务端只接受当前任务项目的可信 `record_id`，并通过 stdin 返回 `{type:"tool_response",id,ok:true,data:{documents,procurement}}` 或 `{type:"tool_response",id,ok:false,error:<safe enum>}`。不得由模型传入 URL；旧 protocol 1 保持首行后 EOF 兼容。
- Judgment evidence：正文证据为 `{record_id,quote}`；附件证据输入为 `{record_id,document_id,quote[,page]}`，服务端会从已读取文档派生 `document_name,source_url,document_sha256`，模型不得提供或覆盖这些字段；未提供 `page` 时按 `page_texts` 首个匹配页补齐。`quote` 必须连续匹配服务端已读取的 document `text/page_texts`，未在本次输入或工具响应中的文档 ID 一律拒绝。
- `POST /api/projects/{id}/analyze` body `{force?:boolean}` => `{job_id:number|null,status:string,message:string}`。排队异步执行。
- `POST /api/projects/{id}/decision` body `{status,owner,next_action,note,selected_option_id:string|null,judgment_id?:number|null}` => 更新后ProjectSummary。选择方案时必须附judgment_id且等于当前非过期判断的id，否则409；纯人工决定无需id。人工决定保存历史，重判不覆盖。
- `POST /api/projects/{id}/gate` body `{action:restore|clear,reason:string}` => ProjectSummary。规则排除可恢复，恢复不修改源事实。
- `POST /api/projects/{id}/link` body `{target_project_id:number,relation:same_project|duplicate|dismiss,note:string}` => `{ok:true,message:string}`。人工确认关联或排除提示，保留双方源记录和决定。
- `GET /api/settings` => `{business:{company_name,scope_description,target_keywords:string[],exclude_keywords:string[],supported_products,capabilities,solution_examples,region_preferences},ai:{base_url,model,enabled:boolean,has_api_key:boolean,engine:'pi_sdk'|'single_call',sdk_ready:boolean},import:{path,auto_import:boolean,poll_seconds:number},version:number}`。默认 `engine` 为 `pi_sdk`；只有明确设置 `SCREENING_AI_ENGINE=single_call` 时才运行旧的单次 HTTP provider。`sdk_ready` 只检查 Node.js 与 `agent-runtime/dist/runner.js` 是否存在，不调用模型；缺少运行时会返回可操作错误，不会静默降级。字符串默认为空；关键词为空时不得凭空认定公司能力。
- `PUT /api/settings` body与GET相同（version无需传），ai可多传`api_key`；空字符串表示保留现有密钥，`clear_api_key:true`可移除。`ai.engine`与`ai.sdk_ready`为只读派生字段，写入时忽略；旧`single_call`引擎仅由环境变量`SCREENING_AI_ENGINE=single_call`显式启用。返回脱敏settings。改变业务配置后重算规则、标记旧AI结果待复核；合格且AI已配置的项目自动排队。
- `GET /api/status` => `{data_updated_at,last_import_at,import_error,file_stale:boolean,import_pending:boolean,source_notes:string,imports:object[],jobs:{id,project_id,status,attempts,error,created_at,updated_at}[],job_counts:object,ai_configured:boolean}`。data_updated_at为源导出文件更新时间，last_import_at为平台导入时间；file_stale表示源文件缺失或超过48小时未更新，import_pending表示新文件尚未导入。
- `POST /api/import` => `{inserted:number,updated:number,unchanged:number,total:number,message:string}`。完整校验文件后事务导入，无变化不重判，导入失败不得删除已有项目。
- `POST /api/analyze-pending` => `{queued:number,message:string}`
- `POST /api/jobs/retry-failed` => `{queued:number,message:string}`
- `GET /api/records/{record_id}/archive` => 下载原件，安全限定于采集归档目录。

错误返回FastAPI `detail`字段。所有写接口JSON请求；密钥不进入URL、日志或AI结果。

## 验收

重复导入无重复；字段缺失不淘汰；疑似重复不合并；规则可恢复；模型未配置不伪造判断；失败可重试；正文/结构化重要字段/关联/配置变化使旧AI结果待复核；仅网页模板变化不触发AI；人工选择不被覆盖；选择过期方案拒绝；前端真实API并展示空/加载/失败状态；浏览器完成筛选、详情、选择/记录决定、配置和任务重试。
