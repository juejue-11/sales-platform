import { InMemoryCredentialStore, type AssistantMessage, type Model } from "@earendil-works/pi-ai";
import { createAgentSession, ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import {
  DUPLEX_PROTOCOL_VERSION,
  MAX_OUTPUT_BYTES,
  DUPLEX_SYSTEM_INSTRUCTION,
  MARKET_SYSTEM_INSTRUCTION,
  PROVIDER_ID,
  READING_SYSTEM_INSTRUCTION,
  TOOL_NAME_SET,
} from "./constants.js";
import { makeTools } from "./tools.js";
import {
  addUsage,
  emptyResourceLoader,
  isRetryableTransportFailure,
  modelDefinition,
  progress,
  runtimeContextInstruction,
  seedHistory,
  traceCodeForFailure,
  traceFailureDetails,
  TOOL_PROGRESS_LABELS,
} from "./execution-support.js";
import { parseStrictToolArguments, RuntimeBoundaryError, StreamBoundaryTrace, validateFinalReport } from "./json-boundary.js";
import { validateRawToolSchema } from "./tool-arguments.js";
import {
  LimitError,
  ProtocolError,
  RpcError,
  isObject,
  type HostToolBridge,
  type ResultEvent,
  type RuntimeEvent,
  type RuntimeRequest,
  type RuntimeUsage,
  type ToolName,
} from "./types.js";
export async function runRequest(
  request: RuntimeRequest,
  emit: (event: RuntimeEvent) => void,
  rpc?: HostToolBridge,
): Promise<ResultEvent> {
  if (request.protocol === DUPLEX_PROTOCOL_VERSION && !rpc) {
    throw new ProtocolError("protocol 2 requires host bridge");
  }
  emit(progress("started", "启动销售分析"));
  const usage: RuntimeUsage = { model_calls: 0, tool_calls: 0, input_tokens: 0, output_tokens: 0 };
  const modelRuntime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(),
    modelsPath: null,
    allowModelNetwork: false,
    refreshOnCreate: false,
  });
  const model = modelDefinition(request.config, request.limits.max_output_tokens);
  modelRuntime.registerProvider(PROVIDER_ID, {
    name: "Sales screening OpenAI-compatible model",
    baseUrl: request.config.base_url,
    apiKey: request.config.api_key,
    authHeader: true,
    api: "openai-completions",
    models: [model],
  });
  const selectedModel = modelRuntime.getModel(PROVIDER_ID, request.config.model) as Model<"openai-completions"> | undefined;
  if (!selectedModel) throw new Error("MODEL_CONFIG");

  const sessionManager = SessionManager.inMemory("sales-screening-agent");
  const prompt = seedHistory(sessionManager, request.messages, request.config);
  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: false },
    retry: { enabled: false },
  });
  const systemPrompt = [
    request.messages[0].content,
    runtimeContextInstruction(request.context),
    request.task_kind === "reading" ? READING_SYSTEM_INSTRUCTION : MARKET_SYSTEM_INSTRUCTION,
    request.task_kind === "market" && request.protocol === DUPLEX_PROTOCOL_VERSION ? DUPLEX_SYSTEM_INSTRUCTION : undefined,
    request.task_kind === "reading"
      ? `本次最多 ${request.limits.max_turns} 轮模型调用，最后输出完整采购阅读 JSON；不得调用任何工具。`
      : `本次最多 ${request.limits.max_turns} 轮模型调用，最后一轮专用于生成完整 JSON 报告，届时工具将关闭。已有事实可支持结论时提前结束；不得为了凑齐潜在线索追查页面导航中的无关公告。`,
  ].filter((value): value is string => Boolean(value)).join("\n\n");
  const resourceLoader = emptyResourceLoader(systemPrompt);
  let limitError: LimitError | undefined;
  let rpcError: RpcError | undefined;
  let abortSession: (() => void) | undefined;
  let boundaryError: RuntimeBoundaryError | undefined;
  let toolRepairAttempts = 0;
  const handledInvalidToolCalls = new Set<string>();
  const streamTrace = new StreamBoundaryTrace();
  const reserveToolCall = (): boolean => {
    if (usage.tool_calls >= request.limits.max_tool_calls) {
      limitError ??= new LimitError("tool");
      abortSession?.();
      return false;
    }
    usage.tool_calls += 1;
    return true;
  };
  const tools = request.task_kind === "reading" || request.limits.max_tool_calls === 0
    ? []
    : makeTools(request.context, reserveToolCall, request.protocol === DUPLEX_PROTOCOL_VERSION ? rpc : undefined);
  const { session } = await createAgentSession({
    cwd: "sales-screening-agent",
    agentDir: "sales-screening-agent",
    modelRuntime,
    model: selectedModel,
    thinkingLevel: selectedModel.reasoning && request.task_kind !== "reading" && request.limits.max_tool_calls > 0
      ? "low"
      : "off",
    settingsManager,
    sessionManager,
    resourceLoader,
    noTools: "all",
    tools: tools.map((tool) => tool.name),
    excludeTools: ["read", "bash", "edit", "write", "grep", "find", "ls", "powershell"],
    customTools: tools,
  });
  // Keep the custom loadout explicit after session construction as well as in
  // createAgentSession options. This public SDK API is the authoritative
  // activation path when a session starts from seeded messages.
  session.setActiveToolsByName(tools.map((tool) => tool.name));
  const previousOnPayload = session.agent.onPayload;
  session.agent.onPayload = async (payload, modelValue) => {
    const transformed = await previousOnPayload?.(payload, modelValue);
    const effectivePayload = transformed === undefined ? payload : transformed;
    if (new URL(request.config.base_url).hostname === "api.deepseek.com" && isObject(effectivePayload)) {
      // The official endpoint supports JSON Output together with thinking and
      // tools. Keep arbitrary compatible providers on their existing contract.
      const deepSeekPayload = { ...effectivePayload, response_format: { type: "json_object" } };
      streamTrace.captureRequestMetadata(deepSeekPayload);
      return deepSeekPayload;
    }
    streamTrace.captureRequestMetadata(effectivePayload);
    return effectivePayload;
  };
  const previousOnProviderStreamEvent = session.agent.onProviderStreamEvent;
  session.agent.onProviderStreamEvent = async (data, modelValue) => {
    // Capture before any extension callback can inspect or mutate the object.
    // pi-ai documents this as the parsed provider event, not raw HTTP bytes.
    streamTrace.captureProviderEvent(data);
    await previousOnProviderStreamEvent?.(data, modelValue);
  };
  const previousBeforeToolCall = session.agent.beforeToolCall;
  session.agent.beforeToolCall = async (toolContext, signal) => {
    streamTrace.setAssistantMessage(toolContext.assistantMessage);
    const rawArguments = streamTrace.readToolArguments(
      toolContext.toolCall.id,
      toolContext.toolCall.name,
      toolContext.toolCall.arguments,
    );
    let invalid: RuntimeBoundaryError | undefined;
    if (rawArguments instanceof RuntimeBoundaryError) {
      invalid = rawArguments;
    } else {
      try {
        // JSON.parse is deliberately applied to the raw concatenation. The SDK's
        // parseStreamingJson result is only a UI/progressive value and may have
        // silently supplied missing closing braces.
        parseStrictToolArguments(rawArguments.raw);
        validateRawToolSchema(toolContext.toolCall.name, rawArguments.value);
      } catch (error) {
        invalid = error instanceof RuntimeBoundaryError
          ? error
          : new RuntimeBoundaryError("TOOL_SCHEMA", "Tool arguments did not satisfy the runtime schema.", {
            tool_name: toolContext.toolCall.name,
          });
      }
    }
    if (invalid) {
      streamTrace.recordBoundary(invalid.code, {
        tool_name: toolContext.toolCall.name,
        tool_call_id: toolContext.toolCall.id,
        attempt: toolRepairAttempts + 1,
      });
      handledInvalidToolCalls.add(toolContext.toolCall.id);
      toolRepairAttempts += 1;
      const terminate = toolRepairAttempts > 1;
      if (terminate) {
        boundaryError = invalid;
        abortSession?.();
      }
      return {
        block: true,
        terminate,
        reason: terminate
          ? "Tool arguments remained invalid after one repair attempt."
          : invalid.code === "TOOL_SCHEMA"
            ? "invalid_request: tool arguments were outside the runtime schema; re-issue the tool call with complete arguments."
            : "Tool arguments were invalid JSON or could not be assembled; re-issue the tool call with complete arguments.",
      };
    }
    return previousBeforeToolCall?.(toolContext, signal);
  };

  abortSession = () => {
    void session.abort();
  };
  rpc?.setAbort?.((error) => {
    rpcError ??= error;
    abortSession?.();
  });
  if (rpcError) {
    session.dispose();
    throw rpcError;
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastRunAssistant: AssistantMessage | undefined;
  let outputBytes = 0;
  const unsubscribe = session.subscribe((event) => {
    if (event.type === "turn_start") {
      streamTrace.resetTurn();
      // A bounded investigation still needs one request to synthesize what
      // was learned. The SDK refreshes its tool loadout before each request.
      if (request.limits.max_turns === 1) {
        session.setActiveToolsByName([]);
      }
      // turn_start is emitted before the provider request. Abort here so a
      // turn limit never allows an extra HTTP request to leave the process.
      if (usage.model_calls + 1 > request.limits.max_turns && !limitError) {
        limitError = new LimitError("turn");
        void session.abort();
      }
    } else if (event.type === "turn_end" && usage.model_calls >= request.limits.max_turns - 1) {
      session.setActiveToolsByName([]);
    } else if (event.type === "message_start" && event.message.role === "assistant") {
      usage.model_calls += 1;
      addUsage(usage, event.message);
      emit(progress("model", "请求模型", undefined, usage.model_calls));
      if (usage.model_calls > request.limits.max_turns && !limitError) {
        limitError = new LimitError("turn");
        void session.abort();
      }
    } else if (event.type === "message_update" && event.message.role === "assistant") {
      if (event.assistantMessageEvent.type === "text_delta") {
        outputBytes += Buffer.byteLength(event.assistantMessageEvent.delta, "utf8");
        if (outputBytes > MAX_OUTPUT_BYTES && !limitError) {
          limitError = new LimitError("output");
          void session.abort();
        }
      }
    } else if (event.type === "message_end" && event.message.role === "assistant") {
      lastRunAssistant = event.message;
      streamTrace.setAssistantMessage(event.message);
      addUsage(usage, event.message);
    } else if (event.type === "tool_execution_start" && TOOL_NAME_SET.has(event.toolName)) {
      const tool = event.toolName as ToolName;
      emit(progress("tool_start", TOOL_PROGRESS_LABELS[tool], tool));
    } else if (event.type === "tool_execution_end" && TOOL_NAME_SET.has(event.toolName)) {
      // SDK schema failures happen before beforeToolCall and therefore do not
      // reach that hook. Count those failed preparations for the same one
      // repair budget, while durationMs distinguishes them from a real tool
      // execution that returned an error.
      if (event.isError && event.durationMs === undefined && !handledInvalidToolCalls.has(event.toolCallId)) {
        const rawArguments = streamTrace.readToolArguments(event.toolCallId, event.toolName);
        let invalid: RuntimeBoundaryError | undefined;
        if (rawArguments instanceof RuntimeBoundaryError) {
          invalid = rawArguments;
        } else {
          try {
            parseStrictToolArguments(rawArguments.raw);
            validateRawToolSchema(event.toolName, rawArguments.value);
          } catch (error) {
            invalid = error instanceof RuntimeBoundaryError
              ? error
              : new RuntimeBoundaryError("TOOL_SCHEMA", "Tool arguments did not satisfy the runtime schema.", {
                tool_name: event.toolName,
              });
          }
        }
        if (invalid) {
          streamTrace.recordBoundary(invalid.code, {
            tool_name: event.toolName,
            tool_call_id: event.toolCallId,
            attempt: toolRepairAttempts + 1,
          });
          handledInvalidToolCalls.add(event.toolCallId);
          toolRepairAttempts += 1;
          if (toolRepairAttempts > 1) {
            boundaryError = invalid;
            abortSession?.();
          }
        }
      }
      const tool = event.toolName as ToolName;
      emit(progress("tool_end", TOOL_PROGRESS_LABELS[tool], tool, undefined, event.isError === true));
    }
  });

  timer = setTimeout(() => {
    if (!limitError) limitError = new LimitError("timeout");
    void session.abort();
  }, request.limits.timeout_ms);
  try {
    let networkRetries = 0;
    let content = "";
    for (;;) {
      lastRunAssistant = undefined;
      streamTrace.resetTurn();
      try {
        await session.prompt(prompt, { expandPromptTemplates: false, source: "rpc" });
      } catch (promptError) {
        if (boundaryError) throw boundaryError;
        if (limitError) throw limitError;
        if (rpcError) throw rpcError;
        const detail = promptError instanceof Error ? promptError.message : String(promptError);
        const retryable = /fetch failed|network|econn|etimedout|eai_again|socket|stream ended|connection reset|connection closed|request aborted/iu.test(detail);
        if (retryable && usage.tool_calls === 0 && usage.model_calls < request.limits.max_turns && networkRetries < 1) {
          networkRetries += 1;
          continue;
        }
        throw promptError;
      }
      if (boundaryError) throw boundaryError;
      if (rpcError) throw rpcError;
      if (limitError) throw limitError;
      const runAssistant = lastRunAssistant as AssistantMessage | undefined;
      if (runAssistant?.stopReason === "length") throw new Error("OUTPUT_TOKEN_LIMIT");
      if (!runAssistant || runAssistant.stopReason !== "stop") {
        if (usage.tool_calls === 0 && usage.model_calls < request.limits.max_turns && networkRetries < 1
          && isRetryableTransportFailure(runAssistant, streamTrace)) {
          networkRetries += 1;
          continue;
        }
        if (!streamTrace.hasFinishReason && isRetryableTransportFailure(runAssistant, streamTrace)) {
          throw new RuntimeBoundaryError("STREAM_INTERRUPTED", "The model stream ended before a complete response.", {
            stop_reason: runAssistant?.stopReason,
            raw_stop_reason: runAssistant?.rawStopReason,
          });
        }
        throw new Error("MODEL_ERROR");
      }
      content = session.getLastAssistantText()?.trim() ?? "";
      if (Buffer.byteLength(content, "utf8") > MAX_OUTPUT_BYTES || content.includes(request.config.api_key)) {
        throw new Error("MODEL_ERROR");
      }
      const contentParserError = streamTrace.compareFinalContent(content);
      if (contentParserError) {
        streamTrace.recordBoundary(contentParserError.code, contentParserError.details);
        throw contentParserError;
      }
      try {
        validateFinalReport(content);
      } catch (error) {
        if (!(error instanceof RuntimeBoundaryError)) throw error;
        // Keep the original draft available to the host for its one strict
        // correction pass; the trace records the format failure and position.
        await streamTrace.write(error.code, {
          ...error.details,
          stop_reason: runAssistant.stopReason,
          raw_stop_reason: runAssistant.rawStopReason,
        }, request.config.api_key);
      }
      if (streamTrace.hasBoundaryEvents) {
        await streamTrace.write(streamTrace.latestBoundaryCode ?? "TOOL_ARGUMENT_JSON", {
          outcome: "tool_repaired_before_execution",
          stop_reason: runAssistant.stopReason,
          raw_stop_reason: runAssistant.rawStopReason,
        }, request.config.api_key);
      }
      break;
    }
    emit(progress("completed", "完成分析"));
    return { type: "result", engine: "pi_sdk", task_kind: request.task_kind,
      content, finish_reason: "stop", usage };
  } catch (error) {
    // Preserve billed usage even when no acceptable final report was emitted.
    // Successful requests already return these counters in their result.
    if (usage.model_calls > 0) emit({ type: "usage", usage: { ...usage } });
    const failedAssistant = lastRunAssistant as AssistantMessage | undefined;
    if (error instanceof RuntimeBoundaryError) {
      await streamTrace.write(error.code, {
        ...error.details,
        ...traceFailureDetails(error, failedAssistant),
      }, request.config.api_key);
      throw error;
    }
    // Every post-session failure gets its own original runtime classification.
    // Repaired tool boundaries remain in boundary_events; they must not turn a
    // later provider error, limit, or RPC failure into a misleading parser code.
    await streamTrace.write(traceCodeForFailure(error, limitError, rpcError), {
      outcome: "runtime_failure",
      ...traceFailureDetails(error, failedAssistant),
    }, request.config.api_key);
    if (limitError) throw limitError;
    if (rpcError) throw rpcError;
    if (error instanceof RpcError) throw error;
    if (error instanceof Error && error.message === "OUTPUT_TOKEN_LIMIT") throw error;
    throw new Error(error instanceof Error ? "MODEL_ERROR" : "MODEL_ERROR");
  } finally {
    if (timer) clearTimeout(timer);
    unsubscribe();
    session.dispose();
  }
}
