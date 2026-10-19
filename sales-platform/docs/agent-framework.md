# Agent 框架实现约定

默认任务面向矿山设备市场开发。企业资料可选；客户关注方向调整分析重点，不能替代产品、资质和交付能力证明。采集系统负责持续发现线索，筛选平台负责调查、判断、保存证据和提供人工行动入口。

## 模块职责

| 位置 | 职责 |
| --- | --- |
| `backend/analysis/analysis_policy.py` | 平台目标、客户偏好、企业资料的分离及语义版本 |
| `backend/research/session.py` | 单次调查授权、预算与检索/读取/关系核实模块的组合 |
| `backend/research/search.py`、`reading.py`、`relations.py` | 受控来源检索、读取和关系核实 |
| `backend/research/network.py`、`storage.py`、`evidence.py` | 网络限制、不可变来源版本和实际读取证据 |
| `backend/documents/document_access.py` | 当前项目附件的目录、按页及关键词片段读取，统一返回大小限制 |
| `backend/analysis/agent_report.py` | Agent 自选报告的结构约束、逐项语义和引用规范化 |
| `backend/analysis/providers.py`、`runtime_bridge.py` | 模型适配和 Python/Node 子进程协议 |
| `backend/analysis/prompts.py`、`validation.py`、`errors.py` | 输入与纠错提示、报告/引用校验、错误脱敏 |
| `backend/application/service.py` | 应用组合、生命周期、导入和队列协调 |
| `backend/application/analysis_context.py`、`analysis_jobs.py`、`agent_runs.py` | 输入快照、分析及一次输出纠错、结果存储和运行历史 |
| `backend/application/reading_store.py`、`reading_jobs.py`、`documents.py` | 阅读版本、阅读任务和附件获取 |
| `agent-runtime/src/runner.ts`、`request.ts`、`rpc.ts` | 进程入口、请求校验和双工协议 |
| `agent-runtime/src/execution.ts`、`tools.ts`、`tool-arguments.ts` | Pi SDK 多轮执行、工具权限、次数及时间预算 |
| `frontend/src/components/report/` | 通用章节、段落、列表、表格、引用与缺口呈现 |
| `backend/documents/public_documents.py` | 公开文件解析与经目录、哈希校验的采集归档复用 |

不新增按公告类别选择的业务模板。协议限制内容类型、大小、来源和执行权限；章节标题、顺序、补查目的以及停止时机由 Agent 根据资料决定。运行记录仅保存可审阅的工具活动，不保存内部思维过程。

## 结果与历史

`market_agent_v2` 表示新框架的判断语义。报告通过 `report.version` 标识结构版本。结果同时保留精简的结论与可选人工行动入口；旧格式仍能读取，不把旧文本重新解释为新报告。

明确需求必须能返回当前采购依据。潜在机会需要项目依据、推断关系、未确定内容及核实行动。无依据时允许没有机会。资料缺口分别表示原文未说明、尚未读取、读取失败或项目不适用；局部资料失败不覆盖正文与其他成功资料。

来源版本、引用片段、输入指纹、历史判断与人工选择一起保存。资料或偏好变化后旧结果标记待复核，原报告和人工选择保留；迁移本身不启动付费分析。

## 验证范围

工程验收包括后端全量测试、运行时构建与协议测试、前端构建，以及使用隔离样本的浏览器检查。样本覆盖无企业资料、仅有偏好、企业资料增强、正文充分而附件失败、建设线索、无依据机会、引用伪造、分页资料和历史报告。

工程验收证明协议、状态和展示闭环。真实模型对项目的理解、推断成立与行动价值需要真实项目审阅；离线样本不作为模型业务准确率。来源检索仅覆盖配置中的公开站点，不保证全网覆盖，也不会取得登录、登记或购买后才可读的文件。

## 运行时与工具边界

`agent-runtime/package.json` 固定 Pi SDK 依赖版本。每次分析创建独立的内存会话，配置与凭据由 Python 宿主传入；不加载 Pi 用户配置、项目指令或默认文件、Shell 工具。初始输入提供采购摘要和文档索引，完整清单通过受控工具按需读取。

Python 与 Node 子进程通过标准输入、输出传递 JSON。协议 1 使用已有快照；协议 2 通过 `tool_request` / `tool_response` 请求宿主读取附件与调查来源。模型只能提供允许的记录编号、来源编号和有界查询，不能指定本地路径、凭据或任意下载 URL。下载、解析、版本和权限由宿主管理。

采购预读不调用工具；使用官方 DeepSeek 时关闭思考模式，市场调查使用低强度思考。预读中同一要求只放在最相关的章节，白话解释仅补充必要信息，引用采用足以支持该要求的连续原文；精简重复表述时仍须保留数字、日期、包号、限制和例外。

研判正文、附件及调查资料提供原文片段编号。模型选择来源和片段编号，后端从本任务实际读取的窗口还原原文；原有逐字引用继续严格验证。片段编号不扩大记录归属、附件读取范围、调查关系和版本权限。报告内容最多纠正一次，官方 DeepSeek 另允许一次格式恢复，统一使用 YAML 序列化通道，只使用已有资料，不调用工具、不重新执行调查。内容纠正后不追加格式恢复。

进度事件只包含可审阅的工具活动和用量，不输出工具参数、原始模型内容或密钥。调用次数、时间、输出大小及 RPC 帧大小均受限；取消、超时或协议失败会结束子进程。接口字段与引用格式以 [API_CONTRACT.md](../API_CONTRACT.md) 为准。

## 输出校验与失败处理

工具参数在执行前按原始 JSON 严格解析，并检查类型、允许字段与项目归属。宽松解析出的对象不能绕过校验。报告保存前继续校验结构、语义、原文引用和实际读取版本；不自动补括号或接受半截结果。

区分 token 截断、网络中断、模型格式错误和工具参数错误。官方 DeepSeek 的格式恢复由 `backend/analysis/provider_finalizer.py` 处理：在有界纠正流程中进行非流式序列化，严格读取单份 YAML，再转为 JSON 并重新校验。禁止标签、锚点、别名、重复键和多文档；格式恢复不重新执行整套调查，也不替代业务证据校验。

失败诊断保留有界、脱敏的信息。回归用例在 `backend/tests/test_json_failures.py` 与 `agent-runtime/src/runner.test.ts`；具体构建和验收命令见[开发验收工具说明](../tools/qa/README.md)。
