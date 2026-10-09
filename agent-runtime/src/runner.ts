import {
  InMemoryCredentialStore,
  Type,
  type AssistantMessage,
  type Model,
  type Usage,
} from "@earendil-works/pi-ai";
import {
  createAgentSession,
  createExtensionRuntime,
  defineTool,
  ModelRuntime,
  type ResourceLoader,
  SessionManager,
  SettingsManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PROTOCOL_VERSION = 1;
const PROVIDER_ID = "sales-screening-runtime";
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const MAX_MESSAGES = 64;
const MAX_CONTENT_CHARS = 1_000_000;
const DEFAULT_LIMITS = {
  max_turns: 6,
  max_tool_calls: 12,
  timeout_ms: 180_000,
  max_output_tokens: 4096,
} as const;
const TOOL_NAMES = [
  "read_project_records",
  "read_company_capability",
  "read_related_records",
] as const;
const TOOL_NAME_SET = new Set<string>(TOOL_NAMES);

type JsonObject = Record<string, unknown>;
type ToolName = (typeof TOOL_NAMES)[number];

export interface RuntimeConfig {
  base_url: string;
  model: string;
  api_key: string;
}

export interface RuntimeMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
}

export interface RuntimeContext {
  project: JsonObject;
  company_capability_card: JsonObject;
  related_projects: JsonObject[];
}

export interface RuntimeLimits {
  max_turns: number;
  max_tool_calls: number;
  timeout_ms: number;
  max_output_tokens: number;
}

export interface RuntimeRequest {
  protocol: 1;
  config: RuntimeConfig;
  messages: RuntimeMessage[];
  context: RuntimeContext;
  limits: RuntimeLimits;
}

interface RuntimeUsage {
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
}

interface ProgressEvent {
  type: "progress";
  stage: "started" | "model" | "tool_start" | "tool_end" | "completed";
  label: string;
  tool?: ToolName;
  turn?: number;
  is_error?: true;
  at: string;
}

interface ResultEvent {
  type: "result";
  engine: "pi_sdk";
  content: string;
  usage: RuntimeUsage;
}

interface ErrorEvent {
  type: "error";
  code:
    | "INVALID_INPUT"
    | "MODEL_CONFIG"
    | "MODEL_ERROR"
    | "TIMEOUT"
    | "TURN_LIMIT"
    | "TOOL_LIMIT"
    | "NO_FINAL"
    | "INTERNAL";
  message: string;
}

export type RuntimeEvent = ProgressEvent | ResultEvent | ErrorEvent;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, field: string, maxLength = MAX_CONTENT_CHARS): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) {
    throw new ProtocolError(`invalid ${field}`);
  }
  return value;
}

function asObject(value: unknown, field: string): JsonObject {
  if (!isObject(value)) {
    throw new ProtocolError(`invalid ${field}`);
  }
  return value;
}

function clampInteger(value: unknown, fallback: number, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(minimum, Math.min(maximum, Math.trunc(value)));
}

function normalizeLimits(value: unknown): RuntimeLimits {
  const limits = isObject(value) ? value : {};
  return {
    max_turns: clampInteger(limits.max_turns, DEFAULT_LIMITS.max_turns, 1, DEFAULT_LIMITS.max_turns),
    max_tool_calls: clampInteger(limits.max_tool_calls, DEFAULT_LIMITS.max_tool_calls, 0, DEFAULT_LIMITS.max_tool_calls),
    timeout_ms: clampInteger(limits.timeout_ms, DEFAULT_LIMITS.timeout_ms, 1, DEFAULT_LIMITS.timeout_ms),
    max_output_tokens: clampInteger(
      limits.max_output_tokens,
      DEFAULT_LIMITS.max_output_tokens,
      64,
      DEFAULT_LIMITS.max_output_tokens,
    ),
  };
}

function normalizeRecordList(value: unknown, field: string): JsonObject[] {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new ProtocolError(`invalid ${field}`);
  }
  return value.map((item, index) => asObject(item, `${field}[${index}]`));
}

function parseRequest(value: unknown): RuntimeRequest {
  const input = asObject(value, "request");
  if (input.protocol !== PROTOCOL_VERSION) {
    throw new ProtocolError("unsupported protocol");
  }

  const config = asObject(input.config, "config");
  const configuredBaseUrl = asString(config.base_url, "config.base_url", 2048).trim();
  const baseUrl = configuredBaseUrl
    .replace(/\/+$/u, "")
    .replace(/\/chat\/completions$/iu, "");
  const model = asString(config.model, "config.model", 256).trim();
  const apiKey = asString(config.api_key, "config.api_key", 4096);
  if (!baseUrl || !model || !apiKey.trim() || !/^https?:\/\//iu.test(baseUrl) || /\/\/(?:[^/]+)@/u.test(baseUrl)) {
    throw new ProtocolError("invalid model endpoint");
  }
  try {
    const parsed = new URL(baseUrl);
    if (!parsed.hostname || parsed.username || parsed.password) {
      throw new ProtocolError("invalid model endpoint");
    }
  } catch (error) {
    if (error instanceof ProtocolError) throw error;
    throw new ProtocolError("invalid model endpoint");
  }

  if (!Array.isArray(input.messages) || input.messages.length === 0 || input.messages.length > MAX_MESSAGES) {
    throw new ProtocolError("invalid messages");
  }
  const messages = input.messages.map((message, index): RuntimeMessage => {
    const item = asObject(message, `messages[${index}]`);
    const role = item.role;
    if (role !== "system" && role !== "user" && role !== "assistant" && role !== "tool") {
      throw new ProtocolError(`invalid messages[${index}].role`);
    }
    return { role, content: asString(item.content, `messages[${index}].content`) };
  });
  if (messages[0]?.role !== "system" || !messages.some((message) => message.role === "user")) {
    throw new ProtocolError("messages must start with system and include user");
  }
  if (messages[messages.length - 1]?.role !== "user") {
    throw new ProtocolError("messages must end with user");
  }

  const context = asObject(input.context, "context");
  const project = asObject(context.project, "context.project");
  const capability = context.company_capability_card === undefined
    ? {}
    : asObject(context.company_capability_card, "context.company_capability_card");
  const relatedProjects = normalizeRecordList(context.related_projects, "context.related_projects");

  return {
    protocol: PROTOCOL_VERSION,
    config: { base_url: baseUrl, model, api_key: apiKey },
    messages,
    context: {
      project,
      company_capability_card: capability,
      related_projects: relatedProjects,
    },
    limits: normalizeLimits(input.limits),
  };
}

class ProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProtocolError";
  }
}

class LimitError extends Error {
  constructor(public readonly kind: "turn" | "tool" | "timeout") {
    super(kind);
    this.name = "LimitError";
  }
}

function progress(
  stage: ProgressEvent["stage"],
  label: string,
  tool?: ToolName,
  turn?: number,
  isError = false,
): ProgressEvent {
  return {
    type: "progress",
    stage,
    label,
    ...(tool ? { tool } : {}),
    ...(turn === undefined ? {} : { turn }),
    ...(isError ? { is_error: true as const } : {}),
    at: new Date().toISOString(),
  };
}

function zeroUsage(): Usage {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function usageNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.trunc(value) : 0;
}

function addUsage(total: RuntimeUsage, message: AssistantMessage): void {
  total.input_tokens += usageNumber(message.usage?.input);
  total.output_tokens += usageNumber(message.usage?.output);
}

function textResult(value: unknown, source: string) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(value) }],
    details: { source },
  };
}

function valueId(value: unknown): string | undefined {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return undefined;
}

function normalizeRecords(project: JsonObject): JsonObject[] {
  const records = project.records;
  return Array.isArray(records) ? records.filter(isObject) : [];
}

function makeTools(context: RuntimeContext, reserveToolCall: () => boolean): ToolDefinition[] {
  const readProjectRecords = defineTool({
    name: "read_project_records",
    label: "读取项目记录",
    description: "读取当前项目的原始记录。可按 record_id 或 id 过滤；不传参数时返回当前项目全部记录。",
    promptSnippet: "读取当前项目的原始记录",
    parameters: Type.Object({
      record_id: Type.Optional(Type.Union([Type.String(), Type.Number()])),
      query: Type.Optional(Type.String()),
    }),
    execute: async (_toolCallId, params) => {
      if (!reserveToolCall()) throw new LimitError("tool");
      const records = normalizeRecords(context.project);
      const id = valueId(params.record_id);
      const query = typeof params.query === "string" ? params.query.trim().toLocaleLowerCase() : "";
      const selected = records.filter((record) => {
        if (id !== undefined && valueId(record.record_id ?? record.id) !== id) return false;
        if (!query) return true;
        return JSON.stringify(record).toLocaleLowerCase().includes(query);
      });
      return textResult({ records: selected }, "context.project.records");
    },
  });

  const readCompanyCapability = defineTool({
    name: "read_company_capability",
    label: "读取公司能力",
    description: "读取销售平台提供的公司能力卡。没有参数时返回完整能力卡，可按 topic 做文本筛选。",
    promptSnippet: "读取公司的能力卡",
    parameters: Type.Object({
      topic: Type.Optional(Type.String()),
    }),
    execute: async (_toolCallId, params) => {
      if (!reserveToolCall()) throw new LimitError("tool");
      const topic = typeof params.topic === "string" ? params.topic.trim().toLocaleLowerCase() : "";
      if (!topic) return textResult({ company_capability_card: context.company_capability_card }, "context.company_capability_card");
      const matches: JsonObject = {};
      for (const [key, value] of Object.entries(context.company_capability_card)) {
        if (`${key} ${JSON.stringify(value)}`.toLocaleLowerCase().includes(topic)) matches[key] = value;
      }
      return textResult({ company_capability_card: matches, topic }, "context.company_capability_card");
    },
  });

  const readRelatedRecords = defineTool({
    name: "read_related_records",
    label: "读取关联记录",
    description: "读取相关项目及其记录。relation 只表示来源系统或平台给出的关系标签，不能证明它们是同一项目；必须标记为候选关系并核实。",
    promptSnippet: "读取相关项目的候选关联记录",
    parameters: Type.Object({
      project_id: Type.Optional(Type.Union([Type.String(), Type.Number()])),
    }),
    execute: async (_toolCallId, params) => {
      if (!reserveToolCall()) throw new LimitError("tool");
      const id = valueId(params.project_id);
      const projects = context.related_projects
        .filter((project) => id === undefined || valueId(project.id ?? project.project_id) === id)
        .map((project) => ({
          ...project,
          relation: project.relation ?? "candidate",
          relation_status: "candidate_not_proven_same_project",
          relation_note: "关系标签仅供核实，不能证明与当前项目是同一项目。",
          records: normalizeRecords(project),
        }));
      return textResult({ related_projects: projects }, "context.related_projects");
    },
  });

  return [readProjectRecords, readCompanyCapability, readRelatedRecords];
}

function emptyResourceLoader(systemPrompt: string): ResourceLoader {
  const runtime = createExtensionRuntime();
  return {
    getExtensions: () => ({ extensions: [], errors: [], runtime }),
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}

function historyAssistant(content: string, config: RuntimeConfig): AssistantMessage {
  return {
    role: "assistant",
    content: [{ type: "text", text: content }],
    api: "openai-completions",
    provider: PROVIDER_ID,
    model: config.model,
    usage: zeroUsage(),
    stopReason: "stop",
    timestamp: Date.now(),
  };
}

function seedHistory(
  sessionManager: SessionManager,
  messages: RuntimeMessage[],
  config: RuntimeConfig,
): string {
  const lastUserIndex = messages.map((message) => message.role).lastIndexOf("user");
  if (lastUserIndex < 1) throw new ProtocolError("missing prompt");
  for (const message of messages.slice(1, lastUserIndex)) {
    if (message.role === "user") {
      sessionManager.appendMessage({ role: "user", content: message.content, timestamp: Date.now() });
    } else if (message.role === "assistant") {
      sessionManager.appendMessage(historyAssistant(message.content, config));
    } else if (message.role === "tool") {
      sessionManager.appendMessage({
        role: "toolResult",
        toolCallId: `input-tool-${sessionManager.getEntryCount()}`,
        toolName: "external_context",
        content: [{ type: "text", text: message.content }],
        details: {},
        isError: false,
        timestamp: Date.now(),
      });
    }
  }
  return messages[lastUserIndex].content;
}

function modelDefinition(config: RuntimeConfig, maxOutputTokens: number): Model<"openai-completions"> {
  return {
    id: config.model,
    name: config.model,
    api: "openai-completions",
    provider: PROVIDER_ID,
    baseUrl: config.base_url,
    input: ["text"],
    reasoning: false,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: maxOutputTokens,
    compat: {
      supportsDeveloperRole: false,
      supportsUsageInStreaming: true,
      supportsFinishReason: true,
      maxTokensField: "max_tokens",
      supportsMidConvoSystemMessages: true,
      supportsMidConvoToolAdditions: true,
    },
  };
}

function normalizeError(error: unknown): ErrorEvent {
  if (error instanceof ProtocolError) {
    return { type: "error", code: "INVALID_INPUT", message: "Invalid runtime request." };
  }
  if (error instanceof LimitError) {
    if (error.kind === "timeout") return { type: "error", code: "TIMEOUT", message: "The runtime deadline was exceeded." };
    if (error.kind === "turn") return { type: "error", code: "TURN_LIMIT", message: "The model turn limit was exceeded." };
    return { type: "error", code: "TOOL_LIMIT", message: "The tool call limit was exceeded." };
  }
  if (error instanceof Error && error.message === "NO_FINAL") {
    return { type: "error", code: "NO_FINAL", message: "The model did not return a final answer." };
  }
  if (error instanceof Error && error.message === "MODEL_CONFIG") {
    return { type: "error", code: "MODEL_CONFIG", message: "The model configuration could not be initialized." };
  }
  if (error instanceof Error && error.message === "MODEL_ERROR") {
    return { type: "error", code: "MODEL_ERROR", message: "The model request failed." };
  }
  return { type: "error", code: "INTERNAL", message: "The agent runtime failed." };
}

export async function runRequest(request: RuntimeRequest, emit: (event: RuntimeEvent) => void): Promise<ResultEvent> {
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
  const resourceLoader = emptyResourceLoader(request.messages[0].content);
  let limitError: LimitError | undefined;
  let abortSession: (() => void) | undefined;
  const reserveToolCall = (): boolean => {
    if (usage.tool_calls >= request.limits.max_tool_calls) {
      limitError ??= new LimitError("tool");
      abortSession?.();
      return false;
    }
    usage.tool_calls += 1;
    return true;
  };
  const tools = makeTools(request.context, reserveToolCall);
  const { session } = await createAgentSession({
    cwd: "sales-screening-agent",
    agentDir: "sales-screening-agent",
    modelRuntime,
    model: selectedModel,
    thinkingLevel: "off",
    settingsManager,
    sessionManager,
    resourceLoader,
    noTools: "all",
    tools: [...TOOL_NAMES],
    excludeTools: ["read", "bash", "edit", "write", "grep", "find", "ls", "powershell"],
    customTools: tools,
  });

  abortSession = () => {
    void session.abort();
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  const unsubscribe = session.subscribe((event) => {
    if (event.type === "turn_start") {
      // turn_start is emitted before the provider request. Abort here so a
      // turn limit never allows an extra HTTP request to leave the process.
      if (usage.model_calls + 1 > request.limits.max_turns && !limitError) {
        limitError = new LimitError("turn");
        void session.abort();
      }
    } else if (event.type === "message_start" && event.message.role === "assistant") {
      usage.model_calls += 1;
      addUsage(usage, event.message);
      emit(progress("model", "请求模型", undefined, usage.model_calls));
      if (usage.model_calls > request.limits.max_turns && !limitError) {
        limitError = new LimitError("turn");
        void session.abort();
      }
    } else if (event.type === "message_end" && event.message.role === "assistant") {
      addUsage(usage, event.message);
    } else if (event.type === "tool_execution_start" && TOOL_NAME_SET.has(event.toolName)) {
      emit(progress("tool_start", "读取业务资料", event.toolName as ToolName));
    } else if (event.type === "tool_execution_end" && TOOL_NAME_SET.has(event.toolName)) {
      emit(progress("tool_end", "业务资料读取完成", event.toolName as ToolName, undefined, event.isError === true));
    }
  });

  timer = setTimeout(() => {
    if (!limitError) limitError = new LimitError("timeout");
    void session.abort();
  }, request.limits.timeout_ms);
  try {
    await session.prompt(prompt, { expandPromptTemplates: false, source: "rpc" });
    if (limitError) throw limitError;
    const content = session.getLastAssistantText()?.trim();
    if (!content) throw new Error("NO_FINAL");
    emit(progress("completed", "完成分析"));
    return { type: "result", engine: "pi_sdk", content, usage };
  } catch (error) {
    if (limitError) throw limitError;
    throw new Error(error instanceof Error ? "MODEL_ERROR" : "MODEL_ERROR");
  } finally {
    if (timer) clearTimeout(timer);
    unsubscribe();
    session.dispose();
  }
}

function emitLine(event: RuntimeEvent): void {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

async function readStdin(): Promise<string> {
  let total = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
    total += buffer.length;
    if (total > MAX_INPUT_BYTES) throw new ProtocolError("input too large");
    chunks.push(buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

export async function main(): Promise<void> {
  try {
    const raw = await readStdin();
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new ProtocolError("invalid json");
    }
    const request = parseRequest(parsed);
    const result = await runRequest(request, emitLine);
    emitLine(result);
  } catch (error) {
    emitLine(normalizeError(error));
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  void main();
}
