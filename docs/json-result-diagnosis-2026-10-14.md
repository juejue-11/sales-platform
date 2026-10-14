# Agent JSON 结果诊断与最小修复

## 结论与边界

当前路径是官方 `api.deepseek.com` / `deepseek-flash` → Pi SDK 1.1.0，并非本机 vLLM。已定位两个不同问题，不将所有缺括号都归因于模型或流式解析器。

1. 对项目 216 的真实报告请求，直接 HTTP Streaming 已返回不完整 JSON；`finish_reason=stop`、`[DONE]` 均存在。将真实 SSE 回放给原 Pi SDK，SDK 的最终文本与原始 delta 拼接结果逐字相同。此次缺少最外层右括号发生在客户端收到响应之前，不能解释成客户端丢失最后 chunk。接口返回边界已定位，但没有服务端原始生成 token/内部 parser 日志，不能进一步断言是模型权重、服务端 parser 或某个版本缺陷。
2. Pi SDK 在工具调用结束时继续使用 `parseStreamingJson`。当原始参数缺 `}` 而 `finish_reason=tool_calls` 时，宽松解析可返回有效对象，工具实际执行。已有 SDK 能阻止 `finish_reason=length` 的工具执行；其 `tool_execution_start` 事件不等于业务工具已执行。

历史 #382 无原始响应，因此新复现解释当前链路的缺陷，不能声称还原了历史 #382 的具体响应内容。

上线验证的项目 216 / job 385 再次捕获相同现象：报告原始 delta 拼接为 9936 字符，SDK 最终文本也是 9936 字符，SHA-256 均为 `9eefc661e16821415bcfc646f5564a30f6350b56b556843b100f9bb3f900bcad`；最后一条数据 chunk 和 `finish_reason=stop` chunk 均保留。单轮输出 10968 tokens，小于 16384 上限。缺少的是最外层闭合括号；不能归类为本次客户端拼接丢失或 token 截断。

该次还发现后端恢复分支的遗漏：原有内容校验纠正已进入第二次 Agent 调用后，第二次的 JSON 语法错误被 `if correction: raise` 提前退出，跳过独立序列化修复。修复将这两种失败分开处理，格式修复仍限定一次且不能绕过内容和证据校验。

job 386 的实际验证首先返回 7208 字符的非法 JSON（正常 `stop`，原始与 SDK 文本仍一致）。独立序列化已将其转换为可解析的报告，随后原有证据校验拒绝了关系未确认的附件 `research_angang_4301721a6b428de8333538d26b033e84`。这证明语法恢复生效且没有绕过证据校验，也暴露了反向组合的恢复缺口：格式恢复后的内容错误应保留既有一次内容纠正机会。恢复预算按每个任务一次格式纠正、一次既有内容纠正分别计数；格式恢复本身失败时立即终止。

job 387 的诊断进一步定位了通用 `MODEL_ERROR`：第 6 轮接口正常 `stop`，实际报告只含空白字符；SDK 未丢失字符，程序在 `trim()` 后抛出 `NO_FINAL`，随后又改写成通用错误。修复后，运行时只在已确认正常结束时发送带 `finish_reason=stop` 的原始空草稿，后端严格 JSON 校验将其归为格式错误并使用同一有限恢复预算。缺少结束原因、长度截断或异常终止仍不能进入此恢复路径；空草稿本身永远不能直接保存。

## 完整链路

`ScreeningService.run_job` 建立输入和本次已读证据快照 → `build_messages` 构造协议及资料 → `PiSDKProvider.analyze` 经 stdin 启动 Node → `runner.runRequest` 创建 Pi SDK 会话 → `ModelRuntime` / `pi-ai openai-completions` 发送 Chat Completions → SSE delta 按 content 与 tool_call index/id 累积 → SDK 临时参数解析与 Schema 校验 → 工具执行前的原始 JSON 校验 → 受控本地工具或 host RPC → 最终报告文本 → 后端 `validate_judgment` 的结构、原文引用和资料关系校验 → 事务保存。

修复后，报告的格式纠正使用一次独立的非流式序列化请求，只带原资料、旧草稿和已读证据，不重新开放调查工具。模型返回以 `---` / `...` 明确起止的单份 YAML 文档，文字使用块字符串承载；程序严格解析成数据对象，再用 `json.dumps` 生成完整 JSON，最后仍走原有 `validate_judgment`。

## 同请求对照实测

两种模式的请求 fingerprint 排除 `stream` / `stream_options`；其余模型、资料、提示词和输出预算相同。由于模型生成并非确定性，两次请求不是同一条服务端生成 token 流；不能仅凭两次输出差异证明服务端 parser bug。

| 调用配置 | Streaming | 非 Streaming |
| --- | --- | --- |
| 原报告 JSON Output，thinking enabled/low | 缺最外层 `}`；6214 字符；stop + DONE；8404 输出 tokens | 缺最外层 `}`；6596 字符；stop；15647 输出 tokens |
| 同报告 JSON Output，thinking disabled | JSON 字符串引号未转义；stop + DONE | JSON 语法错误；stop |
| 简单强制业务工具，thinking disabled | 参数为合法 JSON，tool_calls + DONE | 参数为合法 JSON，tool_calls |
| 完整报告普通强制工具提交，thinking disabled | 参数为合法 JSON | 参数存在未转义引号 |
| 完整报告 strict Beta 工具提交，thinking disabled | 参数仍有转义错误，不能直接执行 | 参数为合法 JSON；5931 字符；原报告及原文证据校验通过 |
| 同一真实坏草稿的 YAML 块字符串序列化，thinking disabled | 10107 字符；stop + DONE；文档起止齐全；全部报告及原文引用校验通过 | 9070 字符；stop；文档起止齐全；全部报告及原文引用校验通过 |

随后将真实坏草稿放入隔离任务测试，strict Beta 非流式修正也返回了未转义引号（位置 1879），被本地校验拒绝。因此没有把单次成功的 Beta 工具提交作为上线修复。有效的兼容修复是将自然语言和原文引文的生成，与 JSON 字符串转义、括号闭合分开：模型生成块字符串，程序负责 JSON 序列化。不是给坏字符串追加 `}`，也不依赖服务端工具 parser 对报告内容的重建。

上述是有意选择的诊断样本，不是生产失败率。thinking enabled + 强制具名 tool_choice 的组合返回 HTTP 400，官方文档明确不支持该组合；结构化纠正阶段关闭 thinking，调查阶段保持原有配置。

真实 Streaming 的 6214 字符经原 SDK 回放后保持不变，SHA-256：`e1b8413a12dd5b35bedbe95796c898656d5015c2e236248822683fa0b5426828`。8404 是该次 API 调用的总输出 token 数，包含思考；不得用多轮累计 output_tokens 推断某一轮达到 16384 上限。

原始响应、parsed chunks、组装文本、finish_reason、请求 fingerprint 与 SDK 回放结果位于本机 `data/json-transport-diagnosis-20261014/`。API 密钥不保存；这些运行资料不提交 Git。

## 最小修改

- 不修改模型服务配置、不升级未经验证的 SDK、不改业务分析模板或证据标准。
- 使用 SDK 公开 provider-event 和 before-tool 回调，按 index/id 保留原始参数，在执行前严格解析及校验类型/允许字段。多个调用交错不会合并为一个参数对象；发现 SDK 与原始合法 JSON 不一致时分类为 parser mismatch。
- 保留有界、脱敏的失败诊断，包括结束原因、原始 chunks 的头尾、完整有界组装结果及 SDK 最终消息。诊断裁剪单独标注，不能当作实际响应截断。
- JSON 格式失败最多一次针对格式的非流式序列化请求。YAML 必须是一个完整文档；禁止标签、锚点、别名、重复键及非字符串键，拒绝缺少真实结束标记的部分结果。使用 SafeLoader 读取数据，`json.dumps(allow_nan=False)` 序列化，再继续本地结构及证据校验。
- 原始字符串缺 `}` 时不直接追加，不用宽松解析结果执行工具或保存报告。

| 错误 | 判定依据 | 恢复边界 |
| --- | --- | --- |
| Token 截断 | 原始 finish_reason=length | 不执行工具、不接受部分报告；明确提示预算限制 |
| 网络/流中断 | 缺结束原因或可识别传输失败 | 尚未执行任何工具时，预算内最多重试一次 |
| 模型/接口格式不合格 | 正常结束但原始 JSON 语法无效 | 不认定为 token 截断；最多一次序列化纠正 |
| 工具参数不完整/Schema 无效 | 原始参数严格解析或类型检查失败 | 禁止执行，最多一次参数纠正 |
| Parser 数据不一致 | 原始参数合法，但 SDK 参数内容不同 | 禁止执行并保存证据，不补括号掩盖问题 |

## 社区案例的适用范围

- [vLLM #45256](https://github.com/vllm-project/vllm/issues/45256)：模型特定 streaming parser 在同一 chunk 处理参数结束和函数结束时过早 return；原始模型文本完整。适合设计“最后 chunk 同时含结束标记”的回归样本，不证明本系统使用同一 parser。
- [Hermes #7680](https://github.com/NousResearch/hermes-agent/issues/7680)：截断检查未覆盖带 tool_calls 的响应；还涉及结束原因被改写的情形。这里已证实的是正常 tool_calls 结束下的原始参数严格校验缺口。
- [vLLM #40195](https://github.com/vllm-project/vllm/issues/40195)：流式参数差分不满足前缀假设，导致引号或数组损坏；非流式对照是排查方法，不代表任意非流式调用都保证有效。

DeepSeek 依据：[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[工具调用及 strict Beta](https://api-docs.deepseek.com/guides/tool_calls/)、[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Thinking 模式](https://api-docs.deepseek.com/guides/thinking_mode/)。JSON Output 文档明确提示偶尔可能返回空内容；这与本次空白响应的实测相符。没有确认本系统服务端 parser 的实现或可控版本，因此没有声称升级某个 vLLM 版本即可修复本系统。

## 验证

变更前：实际 SSE fixture 中缺尾括号的参数产生一次真实工具执行；length fixture 产生零次真实执行。真实报告 SSE 经 SDK 回放仍为同一错误文本。

SDK 集成验收 45 项通过，包含最后一 chunk 中同时出现结束字符与 finish_reason、多工具交错 index、大型嵌套 JSON、length、无 finish_reason/断流、错误参数不触发 host RPC、一次纠正上限、错误分类与脱敏，以及 HTTP 400/500 通用错误的诊断保留、正常结束的空白/空报告。最终后端全量 240 项通过，额外覆盖 YAML 引号、换行、反斜杠、真实文档结束标记、重复键、别名、标签、多文档、错误分类及失败用量，以及格式/内容错误两种顺序的一次恢复、禁止重复 formatter、空响应必须携带正常结束原因与失败不入库。

真实坏草稿经隔离 `run_job` 重放成功：`status=succeeded`、`attempts=2`、调查调用 1 次（旧草稿回放）、实际格式纠正调用 1 次、`finish_reason=stop`，报告经全部结构和原文引用校验后保存。验收快照在 `data/json-transport-diagnosis-20261014/repair-check-yaml/`。

最终实际 Agent 全链路验证：项目 216（历史 #382 对应项目）的新任务 #388 在 `2026-10-14 02:07:35+08:00` 成功完成并保存报告；`attempts=2`、模型调用 5 次、工具实际执行 9 次。该次经过既有一次内容纠正后返回有效报告，无需格式纠正。此前真实坏草稿的格式纠正验收与新增空响应集成测试独立验证格式恢复路径，不能将本次无需 formatter 的成功宣称为再次实测了 formatter。最终快照为 `data/json-transport-diagnosis-20261014/live-after-216-job388.json` 和 `final-verification-summary.json`。

在无排队/运行任务时应用经过校验的源码及 SDK 构建产物，重启本地服务，健康检查通过。当前版本增加 `PyYAML>=6.0.3,<7`，本机独立环境已安装 6.0.3。失败诊断默认写入 `data/ai-traces/`，可用 `SCREENING_AI_TRACE_DIR` 指定目录；只清理本组件生成的记录，不清理目录内其他 JSON 文件。
