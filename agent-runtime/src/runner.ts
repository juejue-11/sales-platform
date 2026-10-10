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
const DUPLEX_PROTOCOL_VERSION = 2;
const PROVIDER_ID = "sales-screening-runtime";
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 256 * 1024;
const MAX_RPC_RESPONSE_BYTES = 4 * 1024 * 1024;
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
  "fetch_project_documents",
] as const;
const TOOL_NAME_SET = new Set<string>(TOOL_NAMES);
const RPC_ERROR_CODES = new Set([
  "invalid_request",
  "record_not_in_project",
  "not_configured",
  "not_found",
  "unsupported",
  "auth_required",
  "failed",
  "network_error",
  "parse_failed",
  "unreadable",
  "timeout",
  "cancelled",
  "internal",
]);
const DUPLEX_SYSTEM_INSTRUCTION =
  "当正文提到附件、招标文件，或采购范围/规格/交付资料不足时，必须先调用 fetch_project_documents 再作判断。" +
  "只能使用当前项目记录的 record_id；不得索要或传入 URL、本地路径、请求头或凭据。" +
  "工具失败或资料未解析时，必须明确列为资料缺口并使用 verify/待核实，不得编造；投标截止时间不是交付时间。";

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
  protocol: 1 | 2;
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
    | "RPC_ERROR"
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
  if (input.protocol !== PROTOCOL_VERSION && input.protocol !== DUPLEX_PROTOCOL_VERSION) {
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
    protocol: input.protocol as 1 | 2,
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
  constructor(public readonly kind: "turn" | "tool" | "timeout" | "output") {
    super(kind);
    this.name = "LimitError";
  }
}

class RpcError extends Error {
  constructor(message = "RPC bridge failed") {
    super(message);
    this.name = "RpcError";
  }
}

interface ByteStream extends AsyncIterable<Buffer | string> {
  pause?: () => void;
  destroy?: () => void;
}

/**
 * Read newline-delimited protocol frames without waiting for EOF. Protocol 1
 * still uses readRemaining(), while protocol 2 keeps this reader alive for
 * host responses to tool_request frames.
 */
class JsonLineReader {
  private readonly iterator: AsyncIterator<Buffer | string>;
  private buffer = Buffer.alloc(0);
  private ended = false;
  private closed = false;

  constructor(private readonly stream: ByteStream) {
    this.iterator = stream[Symbol.asyncIterator]();
  }

  async readLine(maxBytes: number): Promise<string | null> {
    while (true) {
      const newline = this.buffer.indexOf(0x0a);
      if (newline >= 0) {
        const line = this.buffer.subarray(0, newline);
        this.buffer = this.buffer.subarray(newline + 1);
        if (line.length > maxBytes) throw new ProtocolError("input too large");
        return line.toString("utf8").replace(/\r$/u, "");
      }
      if (this.buffer.length > maxBytes) throw new ProtocolError("input too large");
      if (this.ended || this.closed) {
        if (this.buffer.length === 0) return null;
        const line = this.buffer;
        this.buffer = Buffer.alloc(0);
        if (line.length > maxBytes) throw new ProtocolError("input too large");
        return line.toString("utf8").replace(/\r$/u, "");
      }
      const next = await this.iterator.next();
      if (next.done) {
        this.ended = true;
        continue;
      }
      const chunk = Buffer.isBuffer(next.value) ? next.value : Buffer.from(String(next.value));
      if (chunk.length > 0) this.buffer = Buffer.concat([this.buffer, chunk]);
    }
  }

  async readRemaining(maxBytes: number): Promise<string> {
    const chunks: Buffer[] = [];
    let total = this.buffer.length;
    if (this.buffer.length > 0) {
      chunks.push(this.buffer);
      this.buffer = Buffer.alloc(0);
    }
    while (!this.ended && !this.closed) {
      const next = await this.iterator.next();
      if (next.done) {
        this.ended = true;
        break;
      }
      const chunk = Buffer.isBuffer(next.value) ? next.value : Buffer.from(String(next.value));
      total += chunk.length;
      if (total > maxBytes) throw new ProtocolError("input too large");
      if (chunk.length > 0) chunks.push(chunk);
    }
    if (total > maxBytes) throw new ProtocolError("input too large");
    return Buffer.concat(chunks).toString("utf8");
  }

  close(): void {
    this.closed = true;
    this.buffer = Buffer.alloc(0);
    this.stream.pause?.();
    this.stream.destroy?.();
  }
}

interface HostToolBridge {
  request(tool: "fetch_project_documents", argumentsValue: { record_id?: number }): Promise<JsonObject>;
  setAbort?(onFailure: (error: RpcError) => void): void;
  close(): void;
}

interface PendingRpc {
  resolve: (value: JsonObject) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

class DuplexRpc implements HostToolBridge {
  private nextId = 0;
  private closed = false;
  private terminalError: RpcError | undefined;
  private failureNotified = false;
  private onFailure: ((error: RpcError) => void) | undefined;
  private readonly pending = new Map<string, PendingRpc>();
  private readonly readerTask: Promise<void>;

  constructor(
    private readonly reader: JsonLineReader,
    private readonly writeLine: (value: unknown) => void,
    private readonly deadline: number,
  ) {
    this.readerTask = this.consumeResponses();
    void this.readerTask.catch(() => {});
  }

  request(tool: "fetch_project_documents", argumentsValue: { record_id?: number }): Promise<JsonObject> {
    if (this.terminalError) return Promise.reject(this.terminalError);
    if (this.closed) return Promise.reject(new RpcError("RPC bridge is closed"));
    const id = `rpc-${++this.nextId}`;
    const remaining = this.deadline - Date.now();
    if (remaining <= 0) return Promise.reject(new LimitError("timeout"));
    this.writeLine({ type: "tool_request", id, tool, arguments: argumentsValue });
    return new Promise<JsonObject>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new LimitError("timeout"));
      }, remaining);
      this.pending.set(id, { resolve, reject, timer });
    });
  }

  setAbort(onFailure: (error: RpcError) => void): void {
    this.onFailure = onFailure;
    this.notifyFailure();
  }

  private notifyFailure(): void {
    if (this.failureNotified || !this.onFailure) return;
    const failure = this.terminalError ?? (this.closed ? new RpcError("RPC bridge closed") : undefined);
    if (!failure) return;
    this.failureNotified = true;
    this.onFailure(failure);
  }

  private async consumeResponses(): Promise<void> {
    try {
      while (!this.closed) {
        const line = await this.reader.readLine(MAX_RPC_RESPONSE_BYTES);
        if (line === null) throw new RpcError("RPC bridge closed");
        if (!line.trim()) continue;
        let parsed: unknown;
        try {
          parsed = JSON.parse(line);
        } catch {
          throw new RpcError("invalid RPC response");
        }
        if (!isObject(parsed) || parsed.type !== "tool_response" || typeof parsed.id !== "string") {
          throw new RpcError("unexpected RPC response");
        }
        const pending = this.pending.get(parsed.id);
        if (!pending) throw new RpcError("unknown RPC response id");
        if (parsed.ok === true) {
          if (!isObject(parsed.data)) throw new RpcError("invalid RPC response data");
          this.pending.delete(parsed.id);
          clearTimeout(pending.timer);
          pending.resolve(parsed.data);
          continue;
        }
        if (parsed.ok !== false || typeof parsed.error !== "string" || !RPC_ERROR_CODES.has(parsed.error)) {
          throw new RpcError("invalid RPC response error");
        }
        this.pending.delete(parsed.id);
        clearTimeout(pending.timer);
        pending.resolve({ documents: [], procurement: {}, error: parsed.error });
      }
    } catch (error) {
      const failure = error instanceof RpcError ? error : new RpcError();
      this.terminalError ??= failure;
      for (const [id, pending] of this.pending) {
        this.pending.delete(id);
        clearTimeout(pending.timer);
        pending.reject(failure);
      }
      if (!this.closed) {
        this.notifyFailure();
        throw failure;
      }
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.terminalError ??= new RpcError("RPC bridge closed");
    for (const [id, pending] of this.pending) {
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.reject(new RpcError("RPC bridge closed"));
    }
    this.reader.close();
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

function makeTools(
  context: RuntimeContext,
  reserveToolCall: () => boolean,
  rpc: HostToolBridge | undefined,
): ToolDefinition[] {
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

  const fetchProjectDocuments = defineTool({
    name: "fetch_project_documents",
    label: "读取公告公开资料",
    description:
      "当正文提到附件、招标文件，或采购范围、规格、交付资料不足时，读取当前项目公告公开资料。" +
      "只能按当前项目记录的 record_id 请求；不要传 URL、本地路径、请求头或凭据。" +
      "失败或未解析时必须写明资料缺口，不能编造，也不能把投标截止时间当成交付时间。",
    promptSnippet: "按当前项目记录读取公告公开资料",
    parameters: Type.Object({
      record_id: Type.Optional(Type.Integer({ minimum: 1 })),
    }),
    execute: async (_toolCallId, params) => {
      if (!reserveToolCall()) throw new LimitError("tool");
      if (!rpc) {
        return textResult({ documents: [], procurement: {}, error: "unsupported" }, "rpc.fetch_project_documents");
      }
      if (params.record_id !== undefined && (!Number.isSafeInteger(params.record_id) || params.record_id < 1)) {
        return textResult({ documents: [], procurement: {}, error: "invalid_request" }, "rpc.fetch_project_documents");
      }
      const recordId = params.record_id;
      const records = normalizeRecords(context.project);
      if (recordId !== undefined && !records.some((record) => valueId(record.record_id ?? record.id) === String(recordId))) {
        return textResult(
          { documents: [], procurement: {}, error: "record_not_in_project" },
          "context.project.records",
        );
      }
      const data = await rpc.request("fetch_project_documents", recordId === undefined ? {} : { record_id: recordId });
      return textResult(data, "rpc.fetch_project_documents");
    },
  });

  return rpc
    ? [readProjectRecords, readCompanyCapability, readRelatedRecords, fetchProjectDocuments]
    : [readProjectRecords, readCompanyCapability, readRelatedRecords];
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
      // The runtime accepts seeded transcripts whose first message can be a user
      // message. pi-ai can only anchor incremental tool additions at a leading
      // system message; otherwise it intentionally omits the top-level tools
      // field. Keep the complete executable loadout on every request for this
      // generic OpenAI-compatible endpoint.
      supportsMidConvoToolAdditions: false,
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
    if (error.kind === "output") return { type: "error", code: "MODEL_ERROR", message: "The model request failed." };
    return { type: "error", code: "TOOL_LIMIT", message: "The tool call limit was exceeded." };
  }
  if (error instanceof RpcError) {
    return { type: "error", code: "RPC_ERROR", message: "The tool bridge failed." };
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
  const systemPrompt = request.protocol === DUPLEX_PROTOCOL_VERSION
    ? `${request.messages[0].content}\n\n${DUPLEX_SYSTEM_INSTRUCTION}`
    : request.messages[0].content;
  const resourceLoader = emptyResourceLoader(systemPrompt);
  let limitError: LimitError | undefined;
  let rpcError: RpcError | undefined;
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
  const tools = makeTools(request.context, reserveToolCall, request.protocol === DUPLEX_PROTOCOL_VERSION ? rpc : undefined);
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
    tools: tools.map((tool) => tool.name),
    excludeTools: ["read", "bash", "edit", "write", "grep", "find", "ls", "powershell"],
    customTools: tools,
  });
  // Keep the custom loadout explicit after session construction as well as in
  // createAgentSession options. This public SDK API is the authoritative
  // activation path when a session starts from seeded messages.
  session.setActiveToolsByName(tools.map((tool) => tool.name));

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
    if (!lastRunAssistant || lastRunAssistant.stopReason !== "stop") throw new Error("MODEL_ERROR");
    const content = session.getLastAssistantText()?.trim();
    if (!content) throw new Error("NO_FINAL");
    if (Buffer.byteLength(content, "utf8") > MAX_OUTPUT_BYTES || content.includes(request.config.api_key)) {
      throw new Error("MODEL_ERROR");
    }
    emit(progress("completed", "完成分析"));
    return { type: "result", engine: "pi_sdk", content, usage };
  } catch (error) {
    if (limitError) throw limitError;
    if (rpcError) throw rpcError;
    if (error instanceof RpcError) throw error;
    throw new Error(error instanceof Error ? "MODEL_ERROR" : "MODEL_ERROR");
  } finally {
    if (timer) clearTimeout(timer);
    unsubscribe();
    session.dispose();
  }
}

function emitLine(event: unknown): void {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

async function readInitialRequest(): Promise<{ reader: JsonLineReader; parsed: unknown }> {
  const reader = new JsonLineReader(process.stdin);
  const firstLine = await reader.readLine(MAX_INPUT_BYTES);
  if (firstLine === null) throw new ProtocolError("invalid json");

  let firstParsed: unknown;
  try {
    firstParsed = JSON.parse(firstLine);
  } catch {
    firstParsed = undefined;
  }
  // Protocol 2 is explicitly framed: the initial request is one JSON line and
  // stdin remains open for tool_response frames. Protocol 1 falls through to
  // the old EOF-delimited request path, including pretty-printed JSON.
  if (isObject(firstParsed) && firstParsed.protocol === DUPLEX_PROTOCOL_VERSION) {
    return { reader, parsed: firstParsed };
  }
  const firstBytes = Buffer.byteLength(firstLine, "utf8");
  const remaining = await reader.readRemaining(Math.max(0, MAX_INPUT_BYTES - firstBytes - 1));
  const raw = remaining ? `${firstLine}\n${remaining}` : firstLine;
  let parsed = firstParsed;
  if (parsed === undefined) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new ProtocolError("invalid json");
    }
  }
  if (isObject(parsed) && parsed.protocol === DUPLEX_PROTOCOL_VERSION) {
    throw new ProtocolError("protocol 2 request must be one JSON line");
  }
  return { reader, parsed };
}

export async function main(): Promise<void> {
  let reader: JsonLineReader | undefined;
  let rpc: DuplexRpc | undefined;
  try {
    const initial = await readInitialRequest();
    reader = initial.reader;
    const request = parseRequest(initial.parsed);
    if (request.protocol === DUPLEX_PROTOCOL_VERSION) {
      rpc = new DuplexRpc(reader, emitLine, Date.now() + request.limits.timeout_ms);
    }
    const result = await runRequest(request, emitLine, rpc);
    emitLine(result);
  } catch (error) {
    emitLine(normalizeError(error));
  } finally {
    rpc?.close();
    reader?.close();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  void main();
}
