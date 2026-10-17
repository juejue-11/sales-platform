# 筛选平台接口约定

## 2026-10-14 采购工作台扩展

项目列表与详情新增 `procurement_reading`，采购说明独立于研判保存。字段包括 `version`、`material_version`、`status`、`stale`、`summary`、`demand_type`、`stage`、`buyer`、`region`、`category`、`sections`、`milestones`、`coverage`、`conflicts`、`unresolved`。采购类型为 `confirmed|lead|unknown`；处理状态为 `pending|complete|partial|failed|not_configured`。

每章 `sections:[{id,title,requirements:[{id,text,explanation,sources,lot?,status?}]}]` 保留原始要求与解释；sources 使用采购来源协议。时间节点 `milestones:[{id,kind,label,at,sources}]` 的 kind 为 `document_access|registration|question|submission|opening|other`，获取文件截止和提交截止分别保存。卡片与详情使用同一说明；旧 `procurement`、`briefing` 仍兼容返回。

新生成的每条要求必须包含非空字符串 `text`（要求正文）；`explanation` 只作补充解释，不能代替正文，缺少有效 `text` 的模型结果按格式错误处理。历史结果曾因输出字段约定不明确而只保存了解释；页面在这些旧条目的 `text` 为空时显示已有 `explanation`，不重复展示，也不改写历史数据。

`GET /api/projects` 新增 `category`、`region`、`buyer`、`procurement_stage`、`demand_type`、`followup_stage` 筛选。关键截止使用有来源且没有未解决冲突的提交节点，不把获取文件截止当投标截止。

采购需求目录支持 `published_days=0..365`（0 为不限），按上海时区的公告发布日期统计今天及此前 N−1 个日历日；缺失、无效或未来发布日期不进入限定区间。`sort=published` 在分页前按发布日期降序排列，缺失日期排在最后；`updated`、`deadline` 排序继续支持。`GET /api/dashboard` 的 `active_demand_counts` 返回当前有效项目按阅读 `demand_type` 分类的计数；“已研判”使用 `ai_counts.succeeded`，对应列表筛选 `ai_status=succeeded`。

| 方法与路径（以下均以 `/api/projects/{project_id}` 为前缀） | 输入与行为 |
| --- | --- |
| `GET /followup` | 返回用户跟进状态及当前建议计划；查看不等于采用计划 |
| `PUT /followup` | 更新阶段、跟进选择、负责人、下一步、结束原因；提交须填写实际时间及渠道 |
| `POST /followup/adopt` | `{plan_id?,owner?}` 采用服务器计划，重复采用不覆盖用户操作 |
| `PATCH /followup/tasks/{task_id}` | 编辑任务负责人、时间、状态、完成说明；跳过必须理由 |
| `PATCH /followup/materials/{material_id}` | 编辑材料准备与核对状态，不自动形成外部提交 |
| `POST /documents/supplement` | `{record_id,name,content_base64}`，本项目资料记录、PDF/DOCX/DOC/UTF-8 TXT、最多 12 MiB |
| `GET /documents/{document_id}/uploaded-file` | 下载本项目用户补充文件的原始字节 |
| `GET /reading/corrections` | 返回人工纠正历史 |
| `POST /reading/corrections` | `{requirement_id,text,reason}`；要求 ID 必须存在，遗漏使用 `omission`，追加留痕并触发重新核对 |

跟进阶段为 `evaluating|working|submitted|awaiting_result|finished`，跟进选择为 `following|paused|abandoned`，结束原因 `won|lost|abandoned|other`。材料状态为 `not_prepared|preparing|provided|pending_check|needs_supplement`；任务状态为 `todo|doing|done|skipped`。来源必办、路径条件、额外建议分别用 `required|conditional|suggested` 标识。

工作台输入错误返回 400，项目、任务或材料不属于请求项目返回 404，进度冲突返回 409，HTTP 结构错误返回 422。用户不能通过提交自定义 plan/reading 把无来源任务冒充服务器生成的计划。采购资料变化标记复核并保留用户完成动作及提交记录。

本文件是实现约定。采集系统与原报告只读。前后端实现分别由独立 worktree 完成。所有时间使用带时区 ISO 字符串，业务日期用 Asia/Shanghai。

## 运行

- 后端 `backend/main.py` 导出 `app`，FastAPI，默认127.0.0.1:8766；启动自动导入和定期检查文件。
- 前端 `frontend/` 为 React、TypeScript、Vite、Ant Design，开发代理 `/api` 到8766，生产 dist由后端同源提供。
- 后端单独 SQLite 存储 `data/screening.sqlite3`，不修改采集系统数据。
- 默认文件路径：`C:/Users/cgr/Desktop/new/销售平台信息采集系统/data/export/records.jsonl`。
- 设置、API密钥只落后端忽略的data目录；服务返回has_api_key，不返回密钥。模型未配置时显示not_configured，不伪造AI方案。
- 自动导入仅为本次新增、实质变化或关联上下文变化的项目排队；相同文件重复导入不启动历史待分析队列。`/api/analyze-pending` 仍是明确触发全量待分析的入口。

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
opportunity_signals: AgentOpportunity[] // 仅当前有效报告的明确需求和潜在线索
analysis_meta: {semantic_version:string,decision_scope:'market',company_match:'not_evaluated'|'partial'|'assessed'}|null
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
reasons: {text:string,evidence:Evidence[]}[]
unknowns:string[], next_actions:string[]
options:{id:string,title:string,description:string,suitable_for:string,tradeoffs:string[],unknowns:string[],next_action:string}[]
analysis_meta:{semantic_version:'market_agent_v2',decision_scope:'market',company_match:'not_evaluated'|'partial'|'assessed'}|null
agent_run_id:number|null, investigation:Investigation|null
report:AgentReport|null
```
`decision`表示矿山设备市场关注价值，建议关注不等于特定企业具备供货或投标资格。`options`为可选行动/条件性参与方向，可为空；`next_actions`仍为展示建议，人工可直接填写下一步。企业资料为空或仅有名称/偏好时仍可保存有证据的正向市场判断，企业匹配为`not_evaluated`；资料可用性不表示资格已确认。新闻可有基于来源的市场关注判断，不能把潜在线索当明确采购或生成采购性交付方案。

`analysis_meta`持久化，版本和结论范围由后端生成；模型只描述企业评估覆盖状态，无实质资料不得返回`partial/assessed`。旧历史没有元数据时返回null并视为旧语义/待复核，不重解释旧文本。升级迁移保留人工决定与旧报告，不自动调用模型；重分析仍使用现有队列。项目摘要的`analysis_meta`只来自当前有效判断，旧判断不显示为当前市场推荐。新结果仍须通过引用、字段、方案数量及企业断言检查；自然语言守卫只捕获明确的无资料自称能力，不替代业务评测。

## Agent 自选报告

新分析返回 `decision/summary/report`，`options` 是可选的人工行动入口；兼容字段 `demand_summary/reasons/unknowns/next_actions` 由报告派生或补充，不决定章节组织。旧报告 `report=null`，保留旧格式全文和原人工选择。

正式 Pi SDK 和单次 HTTP 生成入口必须提交新版 `report`；缺失时走一次输出纠错，持续缺失则记录失败任务。旧格式校验仍用于历史及原调用方兼容，不能让正式新分析静默回到固定栏目。

```text
AgentReport = {version:1,sections:ReportSection[],opportunities:AgentOpportunity[],gaps:ReportGap[]}
ReportSection = {id:string,title:string,blocks:ReportBlock[]}
ReportBlock = {type:'paragraph',text:string,evidence:Evidence[]}
            | {type:'list',items:{text:string,evidence:Evidence[]}[]}
            | {type:'table',columns:string[],rows:{cells:string[],evidence:Evidence[]}[]}
AgentOpportunity = {id:string,kind:'explicit_demand'|'potential_opportunity',title:string,
                    description:string,basis:string,uncertainties:string[],next_actions:string[],evidence:Evidence[]}
ReportGap = {id:string,description:string,status:'not_stated'|'not_read'|'read_failed'|'not_applicable',
             impact:string,next_action:string,worth_researching:boolean}
```

章节标题、顺序、栏目和数量由 Agent 根据实际资料选择，没有按招标类别预设的报告模板。表格每行单独引用，空单元格允许表示未说明。机会可以为空；明确需求必须有当前采购证据，`related_project/buyer_background` 调查资料不能当明确采购。潜在机会必须有证据、推断依据、未确定内容及核实行动。`not_applicable` 不派生为未解决事项。

报告 ID 唯一；至多 32 章，每章 64 个区块，列表与表格各 128 行，表格最多 32 列，机会和缺口各 64 项，每处最多 32 条引用；规范化后的报告至多 256000 UTF-8 字节。所有引用均走同一后端校验，报告里的调查引用也参与来源版本过期检查。无实质企业资料时，企业断言校验覆盖章节、表格、机会和缺口文本。

`GET /api/projects/{project_id}/documents/{document_id}/snapshot?sha256={hash}` 返回该项目保存的附件版本：元信息、`text`、`page_texts:[{page,text}]` 和 `items`，允许读取历史版本。文档不属于项目或哈希不符返回 404。附件 Evidence 的 `snapshot_url`、`source_url`、`document_sha256` 均由服务器派生，模型不能控制快照地址或源文件哈希。失败尝试独立保存，不覆盖上次成功解析的正文、哈希和明细；无附件结果与实际下载失败分开处理。

AgentRun:
```
id:number, job_id:number, engine:'pi_sdk', status:running|succeeded|failed
started_at:string, finished_at:string|null, error:string|null
steps:{stage:started|model|tool_start|tool_end|completed,label:string,tool?:string,turn?:number,at:string}[]
usage:{model_calls:number,tool_calls:number,input_tokens:number,output_tokens:number}
limits:{max_turns:number,max_tool_calls:number,timeout_ms:number,max_output_tokens:number}
investigation:Investigation|null
```
`steps`只保存受信任的阶段和工具标签（读取项目资料、读取公司能力、读取关联资料及其开始/完成/失败状态），不保存模型思维、正文输出或密钥；运行时的`completed`事件会被忽略，只有判定结果通过验证并落库后才追加`方案已通过校验`，运行状态不会提前显示为`succeeded`。发送给模型的每条正文最多30000字符，截断正文保留`body_text_sha256`用于完整输入指纹。

`idle`表示符合条件但尚未排队，`skipped`表示规则排除/截止等无需AI。只有真实任务才可显示queued/running。人工选择绑定当时judgment_id，新判断option_id相同也必须重新确认；旧选择和旧下一步保留可追溯。

## 项目调查与版本引用

默认 Pi SDK 可按缺口执行 `search_project_sources`、`read_research_source`、`assess_source_relation`、`search_research_content`。仅搜索命中不会建立采购事实，必须实际读取、核实关系才能引用。显式单次模型入口标记 `not_performed`，不声称已执行外部搜索。

```text
Evidence = {record_id:number,quote:string,document_id?:string,page?:number,...服务器附件元数据}
         | {source_ref:string,source_version:string,quote:string,page?:number,
            title:string,source_url:string,fetched_at:string|null,relation:string,snapshot_url:string}
ResearchSource = {source_ref,source_version?,title,url,published_at?,fetched_at?,kind:'page'|'document',
                  status:'candidate'|'read'|'failed'|'unsupported'|'auth_required'|'unreadable',
                  relation:'unconfirmed'|'same_procurement'|'related_project'|'buyer_background'|'unrelated'|'conflict',
                  relation_status:'unconfirmed'|'verified'|'rejected',pages?,parent_source_ref?,error?}
Investigation = {policy_version,status:'not_started'|'running'|'completed'|'limited'|'failed'|'not_performed',
                 stop_reason?,events:{at,tool,purpose,query?,status,sources:string[],message?}[],
                 sources:ResearchSource[],unresolved:string[]}
```

模型仅提交调查证据的 `source_ref/source_version/quote/page`；其余引用字段由后端生成。新来源引用与 `record_id/document_id` 互斥，版本和引用必须存在于本任务实际读取的片段 registry，禁止跨项目或直接引用搜索摘要。引用存在不证明推断成立；来源关系已核实不表示企业资格或所有采购事实通过核验。

| 工具 | 参数 |
| --- | --- |
| `search_project_sources` | `{purpose,query,source_ids?:string[],limit?:number,offset?:number,published_after?:string,published_before?:string}` |
| `read_research_source` | `{source_ref,purpose,page?:number,offset?:number,limit?:number,refresh?:boolean}` |
| `assess_source_relation` | `{source_ref,source_version,relation,quote,reason}` |
| `search_research_content` | `{source_ref,source_version,query,purpose,limit?:number}` |

整数参数、字符串长度和未知参数由运行时与后端分别验证。返回状态区分正常空结果、未配置来源、访问阻断、读取/解析失败和预算停止；搜索返回具体来源覆盖范围，不保证全网覆盖。来源注册和允许主机由平台配置，候选/文档 ID 由后端分配；页面文本不能扩大工具权限。文档默认有限片段，后端保留完整版本用于证据回溯。

搜索 `limit` 为 1–50（另受来源配置默认 20 条总结果上限约束），`offset` 为 0–10000；读取 `limit` 为 1–4000、`offset` 为 0–240000、`page=0` 或省略表示首个可读页，正页码限定对应文档页；内容定位 `limit` 为 1–20。字符串目的/查询/关系依据上限 4000 字，ID 上限 256 字，`source_ids` 最多 20 个。ISO 日期范围必须按先后排列，日期形式的上下界包含整个日历日；发布时间未知的候选保留未知状态。网络响应逐块读取并在超出累计下载预算时关闭，不先下载完整超大文件。

调查来源与采集 `records` 独立保存，不自动归并项目。指纹包含调查策略、来源配置和报告实际引用的版本/关系；候选新增不导致运行任务自我失效，外部正文、配置与所依赖版本变化仍使结果待复核。旧报告、旧人工决定与旧证据保持可查看，旧策略结果不能直接复用为新版调查。

摘要增加可空 `research_summary:{status,source_count,stop_reason?,updated_at?}`，详情增加 `research_sources:ResearchSource[]`。`agent_run` 表示最近运行；`judgment.agent_run_id/investigation` 固定绑定生成该报告的运行，重新分析时不得混用。`sources` 是调查期间发现的资料，不意味着每个候选均被报告采用；采用关系以理由中的有效引用为准。

搜索响应 `coverage` 包含实际查询的 `source_ids`、列表扫描标志及 `uncovered_origins`（本项目原公告平台未被本次检索覆盖的域名）。这些范围和失败限制同时写入调查事件，`no_results` 仅表示已查范围内无结果。

- `GET /api/projects/{id}/research/runs` => `{items:AgentRun[],total:number}`，最近 50 次运行；包括失败/受限运行。
- `GET /api/projects/{id}/research/runs/{run_id}` => `AgentRun`，必须属于该项目。
- `GET /api/projects/{id}/research/sources/{source_ref}/versions/{version}?page=&offset=0&limit=4000` => `{source:ResearchSource,text:string,page?:number|null,offset:number,total_chars:number,has_more:boolean}`，限定当前项目已登记来源，返回保存版本而非重新读取在线页面。`limit` 为 1–8000；不存在/越权返回 404，无效查询参数返回 422。

调查记录仅保存目的、规范化请求、来源、状态和未解决事项；不保存模型内部思维过程或原始模型输出。前端按来源关系和引用类型展示，在线原文与保存快照分别提供入口。

## API

- `GET /api/health` => `{ok:true,app:"sales-screening"}`
- `GET /api/dashboard` => `{total_records,total_projects,gate_counts,ai_counts,ai_decision_counts,decision_counts,deadline_soon:ProjectSummary[],deadline_soon_count,data_updated_at:string|null,last_import_at,import_error:string|null,file_stale,import_pending,source_notes,ai_configured:boolean}`。统计覆盖全部项目，截止预览最多30条；推荐仅使用有效AI结论。
- `GET /api/projects?q=&gate=&ai_status=&ai_decision=&decision=&kind=&source_id=&deadline_soon=false&active_only=false&page=1&page_size=30&sort=updated` => `{items:ProjectSummary[],total:number,page:number,page_size:number}`。过滤空值表示全部；`active_only=true`返回`candidate`/`review`/`signal`项目；sort支持updated、deadline；deadline_soon筛选未来7天截止且可继续处理的项目。`q`同时搜索标题、采购方、设备和`briefing.scope_excerpt`。
- `GET /api/projects/{id}` => summary字段加 `{records:Record[],judgment:Judgment|null,judgment_history:Judgment[],decision_history:object[],related_projects:ProjectSummary[],versions:object[],agent_run:AgentRun|null}`。Record保留id/title/body_text/facts/inferences/original_url/canonical_url/archive_path/parse_status/possible_duplicate_of/related_record_id等采集字段。关联提示不自动合并，只有可靠标识才能合并；人工关联另外留痕。
- 项目摘要与详情包含 `procurement`：`{status:not_checked|partial|complete|unavailable,scope_summary,items,delivery_period,delivery_location,missing_fields,documents,updated_at,attachment_status,attachment_note}`。已知公告正文事实在读取详情时直接汇总，不依赖手动获取附件；正文项标记 `source_kind:'announcement',source_document_id:'',page:0`，保留 `record_id,quote` 原文来源，不能从标题推测数量、规格或材质。附件项标记 `source_kind:'attachment'`，解析结果持久化在独立文档表，不覆盖记录正文；`documents` 仅返回元数据，明细项保留 `source_document_id,record_id,page,quote` 来源定位。
- `attachment_status` 为 `not_checked|none_public|restricted|available|failed`，独立描述公开附件检查状态；`attachment_note` 描述检查结果或原公告载明的访问条件。没有公开附件、需登记购买招标文件与真实获取/解析失败分别展示；附件状态不否定已知正文事实。
- `POST /api/projects/{id}/documents/refresh` => `{procurement,message}`。该接口只抓取并解析当前项目的公开附件，不调用模型；存在未解析附件或缺少明细/数量单位/规格材质/交期交地时不能返回 `complete`。
- Agent protocol 2：Python 首行发送 `{protocol:2,...}`；运行时通过 stdout 请求 `{type:"tool_request",id,tool,arguments}`。后端往返工具只允许 `fetch_project_documents` 与四个调查工具，分别校验参数。通过 stdin 返回 `{type:"tool_response",id,ok:true,data}` 或 `{type:"tool_response",id,ok:false,error:<safe enum>}`。不得由模型传入 URL、路径、凭据或请求头。旧 protocol 1 保持首行后 EOF 兼容，不能执行主动调查。
- 附件参数为 `{record_id?:number,document_id?:string,page?:number,query?:string,offset?:number,limit?:number}`，只接受当前项目的记录及可信目录中的文档编号。默认目录包含短文全文或长文元信息，连续片段默认 8000、最多 16000 字符，offset 上限 500000，page 为正整数。同一任务缓存已取得的完整资料，引用只校验实际返回过的连续片段。
- Agent context 同时包含`analysis_goal`、`analysis_semantic_version`、`customer_preferences:{customer_role,equipment_categories,mineral_types,demand_focus,target_keywords,region_preferences}`、`company_profile_present`和归一化的`company_capability_card`。身份、偏好与企业材料分别解释；附件聚合状态不表示正文全部未知，附件失败只限制依赖它的结论。默认目标和归一化输入参加分析指纹。
- Judgment evidence：正文证据为 `{record_id,quote}`；附件证据输入为 `{record_id,document_id,quote[,page]}`，服务端会从已读取文档派生 `document_name,source_url,document_sha256`，模型不得提供或覆盖这些字段；未提供 `page` 时按 `page_texts` 首个匹配页补齐。`quote` 必须连续匹配服务端已读取的 document `text/page_texts`，未在本次输入或工具响应中的文档 ID 一律拒绝。
- `POST /api/projects/{id}/analyze` body `{force?:boolean}` => `{job_id:number|null,status:string,message:string}`。排队异步执行。
- `POST /api/projects/{id}/decision` body `{status,owner,next_action,note,selected_option_id:string|null,judgment_id?:number|null}` => 更新后ProjectSummary。选择方案时必须附judgment_id且等于当前非过期判断的id，否则409；纯人工决定无需id。人工决定保存历史，重判不覆盖。
- `POST /api/projects/{id}/gate` body `{action:restore|clear,reason:string}` => ProjectSummary。规则排除可恢复，恢复不修改源事实。
- `POST /api/projects/{id}/link` body `{target_project_id:number,relation:same_project|duplicate|dismiss,note:string}` => `{ok:true,message:string}`。人工确认关联或排除提示，保留双方源记录和决定。
- `GET /api/settings` => `{business:{company_name,scope_description,target_keywords:string[],exclude_keywords:string[],supported_products,capabilities,solution_examples,region_preferences,customer_role,equipment_categories,mineral_types,demand_focus},ai:{base_url,model,enabled:boolean,has_api_key:boolean,engine:'pi_sdk'|'single_call',sdk_ready:boolean},import:{path,auto_import:boolean,poll_seconds:number},version:number}`。默认 `engine` 为 `pi_sdk`；只有明确设置 `SCREENING_AI_ENGINE=single_call` 时才运行旧的单次 HTTP provider。`sdk_ready` 只检查 Node.js 与 `agent-runtime/dist/runner.js` 是否存在，不调用模型；缺少运行时会返回可操作错误，不会静默降级。字符串默认为空；关键词为空时不得凭空认定公司能力。
- `PUT /api/settings` body与GET相同（version无需传），ai可多传`api_key`；空字符串表示保留现有密钥，`clear_api_key:true`可移除。`ai.engine`与`ai.sdk_ready`为只读派生字段，写入时忽略；旧`single_call`引擎仅由环境变量`SCREENING_AI_ENGINE=single_call`显式启用。返回脱敏settings。改变业务配置后重算规则、标记旧AI结果待复核；合格且AI已配置的项目自动排队。
- Settings另有只读`analysis:{goal:string,semantic_version:'market_agent_v2',company_profile_present:boolean}`，描述平台默认目标与实质企业资料可用性。仅`scope_description/supported_products/capabilities/solution_examples`参与材料可用性判断，公司名称、角色、设备类别、矿种、需求方向、关注关键词和地区偏好均不作为能力。兼容原业务字段，客户端写入`analysis`时忽略。
- `GET /api/status` => `{data_updated_at,last_import_at,import_error,file_stale:boolean,import_pending:boolean,source_notes:string,imports:object[],jobs:{id,project_id,status,attempts,error,created_at,updated_at}[],job_counts:object,ai_configured:boolean}`。data_updated_at为源导出文件更新时间，last_import_at为平台导入时间；file_stale表示源文件缺失或超过48小时未更新，import_pending表示新文件尚未导入。
- `POST /api/import` => `{inserted:number,updated:number,unchanged:number,total:number,message:string}`。完整校验文件后事务导入，无变化不重判，导入失败不得删除已有项目。
- `POST /api/analyze-pending` => `{queued:number,message:string}`
- `POST /api/jobs/retry-failed` => `{queued:number,message:string}`
- `GET /api/records/{record_id}/archive` => 下载原件，安全限定于采集归档目录。

错误返回FastAPI `detail`字段。所有写接口JSON请求；密钥不进入URL、日志或AI结果。

## 验收

重复导入无重复；字段缺失不淘汰；疑似重复不合并；规则可恢复；模型未配置不伪造判断；失败可重试；正文/结构化重要字段/关联/配置变化使旧AI结果待复核；仅网页模板变化不触发AI；人工选择不被覆盖；选择过期方案拒绝；前端真实API并展示空/加载/失败状态；浏览器完成筛选、详情、选择/记录决定、配置和任务重试。
## 采集管理接口（2026-10-16）

原有接口保持兼容。新增接口均位于 `/api/collection`：

| 请求 | 行为 |
| --- | --- |
| `GET /api/collection` | 返回接入状态、来源、最近采集运行、Windows 自动采集设置与平台当前任务 |
| `POST /api/collection/run` | 输入 `{source_id?: string|null}`，异步执行全部启用来源或指定来源，返回 `{message, task}` |
| `PATCH /api/collection/sources/{source_id}` | 输入 `{enabled: boolean}`，由采集控制入口原子更新单个来源 |
| `PUT /api/collection/schedule` | 输入 `{enabled: boolean, at: "HH:MM"}`，由采集模块管理既有 Windows 每日任务 |

状态返回 `{available, message, running, sources, runs, schedule, task}`。来源包含配置、最近成功/失败时间、最近错误及 `last_run`；执行历史含 `id/started_at/finished_at/status/stats`。`schedule` 包含 `available/registered/enabled/at/state/message`。

状态另含 `import_connected:boolean`，表示当前平台导入路径与采集输出一致。模块可用但此值为 `false` 时，来源管理仍可使用，采集按钮禁用并引导到数据接入设置；不能把模块可用当作采集流程已连接。仅更改数据接入设置不会自动启动采集。

`task` 含 `id/status/source_id/started_at/finished_at/error/log_tail/import_result`。状态为 `running/succeeded/partial/failed`；`import_result` 保留现有导入入口的统计。采集部分成功也导入已取得资料；导入失败须与采集结果分别说明。繁忙返回 409；缺失模块、无效来源和计划任务权限错误返回明确消息。控制接口不接受任意命令或任意来源 URL。

`task.status=running` 时 `import_result` 也可已有结果：手动采集过程中持续导入原子发布的已提交快照。`inserted/updated` 累计本次任务的导入变更次数，`total/unchanged` 表示最近一次快照，不累计快照总行数。后续采集失败、超时或单个来源中断不会清除此前导入结果。采购列表和首页后台刷新，不以模型阅读/研判是否完成为出现条件。
