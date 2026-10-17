import { createHash, randomUUID } from "node:crypto";
import { mkdir, readdir, stat, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { AssistantMessage } from "@earendil-works/pi-ai";

export type BoundaryCode =
  | "MODEL_FORMAT_ERROR"
  | "MODEL_ERROR"
  | "STREAM_INTERRUPTED"
  | "OUTPUT_TOKEN_LIMIT"
  | "TURN_LIMIT"
  | "TOOL_LIMIT"
  | "TIMEOUT"
  | "RPC_ERROR"
  | "TOOL_ARGUMENT_JSON"
  | "TOOL_SCHEMA"
  | "TOOL_PARSER_ERROR";

export class RuntimeBoundaryError extends Error {
  readonly code: BoundaryCode;
  readonly details: Record<string, unknown>;

  constructor(code: BoundaryCode, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "RuntimeBoundaryError";
    this.code = code;
    this.details = details;
  }
}

const MAX_PROVIDER_HEAD_EVENTS = 64;
const MAX_PROVIDER_TAIL_EVENTS = 256;
const MAX_TRACE_TEXT = 64 * 1024;
const MAX_COMPARE_CONTENT = 256 * 1024;
const MAX_TRACE_TOOL_ARGUMENTS = 256 * 1024;
const MAX_BOUNDARY_SNAPSHOT_TEXT = 32 * 1024;
const MAX_TRACE_FILE_BYTES = 1024 * 1024;
const MAX_TRACE_FILES = 100;

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function appendBounded(current: string, addition: string, limit: number): { value: string; truncated: boolean } {
  if (current.length >= limit) return { value: current, truncated: true };
  const available = limit - current.length;
  return { value: current + addition.slice(0, available), truncated: addition.length > available };
}

function jsonPosition(error: unknown): number | undefined {
  const message = error instanceof Error ? error.message : String(error);
  const match = /position\s+(\d+)/iu.exec(message);
  return match ? Number(match[1]) : undefined;
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "undefined";
  if (Array.isArray(value)) return `[${value.map((item) => stableJson(item)).join(",")}]`;
  if (!isRecord(value)) return JSON.stringify(value) ?? "undefined";
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
}

function sameJson(left: unknown, right: unknown): boolean {
  return stableJson(left) === stableJson(right);
}

export function parseStrictToolArguments(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch (error) {
    throw new RuntimeBoundaryError("TOOL_ARGUMENT_JSON", "Tool arguments were not valid JSON.", {
      parse_error: error instanceof Error ? error.message.slice(0, 200) : "parse failed",
      position: jsonPosition(error),
      raw_length: raw.length,
    });
  }
}

// DeepSeek occasionally emits object keys without the opening quote
// (e.g. {"record_id":90,document_id":"doc-..."}).  An identifier directly
// followed by '"' after '{' or ',' is never valid JSON, so quoting it back
// is a lossless, zero-false-positive repair for that known defect.
function repairMissingKeyQuotes(content: string): string {
  return content.replace(/([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)"(?=\s*:)/gu, "$1\"$2\"");
}

export function validateFinalReport(content: string): JsonRecord {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content) as unknown;
  } catch (error) {
    const repaired = repairMissingKeyQuotes(content);
    if (repaired !== content) {
      try {
        parsed = JSON.parse(repaired) as unknown;
      } catch {
        throw new RuntimeBoundaryError("MODEL_FORMAT_ERROR", "The final model content was not valid JSON.", {
          parse_error: error instanceof Error ? error.message.slice(0, 200) : "parse failed",
          position: jsonPosition(error),
          content_length: content.length,
        });
      }
    } else {
      throw new RuntimeBoundaryError("MODEL_FORMAT_ERROR", "The final model content was not valid JSON.", {
        parse_error: error instanceof Error ? error.message.slice(0, 200) : "parse failed",
        position: jsonPosition(error),
        content_length: content.length,
      });
    }
  }
  if (!isRecord(parsed)) {
    throw new RuntimeBoundaryError("MODEL_FORMAT_ERROR", "The final model content must be a JSON object.", {
      json_type: Array.isArray(parsed) ? "array" : typeof parsed,
      content_length: content.length,
    });
  }
  // Keep the report contract extensible, but reject known fields with the wrong
  // shape before the host/backend gets an opaque string.
  const stringFields = ["decision", "summary", "demand_summary"];
  for (const field of stringFields) {
    if (parsed[field] !== undefined && typeof parsed[field] !== "string") {
      throw new RuntimeBoundaryError("MODEL_FORMAT_ERROR", "A report field has the wrong JSON type.", {
        field,
        expected: "string",
      });
    }
  }
  const arrayFields = ["reasons", "unknowns", "next_actions", "options"];
  for (const field of arrayFields) {
    if (parsed[field] !== undefined && !Array.isArray(parsed[field])) {
      throw new RuntimeBoundaryError("MODEL_FORMAT_ERROR", "A report field has the wrong JSON type.", {
        field,
        expected: "array",
      });
    }
  }
  return parsed;
}

interface RawToolCall {
  key: string;
  index?: number;
  id?: string;
  name?: string;
  arguments: string;
  truncated: boolean;
  identityMissing: boolean;
}

function traceValue(value: unknown, apiKey: string, key = "", depth = 0): unknown {
  // Provider chunks nest tool arguments under choices/delta/tool_calls/function;
  // keep enough depth to retain those arguments while still bounding objects.
  if (depth > 12) return "<depth-limited>";
  const lowerKey = key.toLocaleLowerCase();
  if (/authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|cookie/iu.test(lowerKey)) {
    return "<REDACTED>";
  }
  if (/^reasoning(?:_content|_details)?$|^thinking(?:_content|signature)?$/iu.test(lowerKey)) {
    return typeof value === "string" ? { omitted: "thinking", chars: value.length } : { omitted: "thinking" };
  }
  if (typeof value === "string") {
    let text = apiKey ? value.split(apiKey).join("<REDACTED>") : value;
    text = text.replace(/Bearer\s+[A-Za-z0-9._~+\-/]+=*/giu, "Bearer <REDACTED>");
    text = text.replace(/\bsk-[A-Za-z0-9_-]{8,}\b/gu, "<REDACTED>");
    return text.length > MAX_TRACE_TEXT ? `${text.slice(0, MAX_TRACE_TEXT)}...` : text;
  }
  if (Array.isArray(value)) {
    // The provider trace is already bounded to a 64-event head plus a
    // 256-event tail. Preserve both halves through redaction so the final
    // finish chunk cannot be discarded by this generic array cap.
    const maxItems = key === "provider_events" ? MAX_PROVIDER_HEAD_EVENTS + MAX_PROVIDER_TAIL_EVENTS : 128;
    return value.slice(0, maxItems).map((item) => traceValue(item, apiKey, key, depth + 1));
  }
  if (isRecord(value)) {
    const output: JsonRecord = {};
    for (const [childKey, childValue] of Object.entries(value).slice(0, 128)) {
      output[childKey] = traceValue(childValue, apiKey, childKey, depth + 1);
    }
    return output;
  }
  return value;
}

function traceAssistant(message: AssistantMessage | undefined, apiKey: string): unknown {
  if (!message) return undefined;
  const content = message.content
    .filter((block) => block.type !== "thinking")
    .map((block) => traceValue(block, apiKey));
  return traceValue({
    role: message.role,
    api: message.api,
    provider: message.provider,
    model: message.model,
    stopReason: message.stopReason,
    rawStopReason: message.rawStopReason,
    errorMessage: message.errorMessage,
    usage: message.usage,
    content,
  }, apiKey);
}

export class StreamBoundaryTrace {
  private readonly runId = randomUUID();
  private providerHeadEvents: unknown[] = [];
  private providerTailEvents: unknown[] = [];
  private providerEventBytes = 0;
  private providerEventHash = createHash("sha256");
  private providerEventDropped = 0;
  private readonly toolCalls = new Map<string, RawToolCall>();
  private content = "";
  private anonymous = 0;
  private finishReasons: string[] = [];
  private providerChunkCount = 0;
  private turnNumber = 0;
  private responseId: string | undefined;
  private traceTruncated = false;
  private identityError = false;
  private contentTruncated = false;
  private assistant: AssistantMessage | undefined;
  private requestMetadata: JsonRecord | undefined;
  private boundaryEvents: Array<Record<string, unknown>> = [];
  private toolComparisons: Array<Record<string, unknown>> = [];
  private traceWritten = false;

  resetTurn(): void {
    this.providerHeadEvents = [];
    this.providerTailEvents = [];
    this.providerEventBytes = 0;
    this.providerEventHash = createHash("sha256");
    this.providerEventDropped = 0;
    this.toolCalls.clear();
    this.content = "";
    this.anonymous = 0;
    this.finishReasons = [];
    this.providerChunkCount = 0;
    this.turnNumber += 1;
    this.responseId = undefined;
    this.traceTruncated = false;
    this.identityError = false;
    this.contentTruncated = false;
    this.assistant = undefined;
    this.requestMetadata = undefined;
  }

  captureRequestMetadata(payload: unknown): void {
    if (!isRecord(payload)) return;
    const metadata: JsonRecord = {};
    if (typeof payload.model === "string") metadata.model = payload.model;
    if (typeof payload.max_tokens === "number") metadata.max_tokens = payload.max_tokens;
    const reasoningEfforts = new Set(["low", "medium", "high"]);
    if (typeof payload.reasoning_effort === "string" && reasoningEfforts.has(payload.reasoning_effort)) {
      metadata.reasoning_mode = payload.reasoning_effort;
    }
    if (isRecord(payload.thinking) && (payload.thinking.type === "enabled" || payload.thinking.type === "disabled")) {
      metadata.thinking_mode = payload.thinking.type;
    } else if (payload.thinking === "enabled" || payload.thinking === "disabled") {
      metadata.thinking_mode = payload.thinking;
    }
    if (isRecord(payload.response_format) && typeof payload.response_format.type === "string") {
      metadata.response_format = { type: payload.response_format.type };
    }
    if (typeof payload.tool_choice === "string") {
      metadata.tool_choice = payload.tool_choice;
    } else if (isRecord(payload.tool_choice)) {
      metadata.tool_choice = {
        type: typeof payload.tool_choice.type === "string" ? payload.tool_choice.type : undefined,
        function: isRecord(payload.tool_choice.function) && typeof payload.tool_choice.function.name === "string"
          ? { name: payload.tool_choice.function.name }
          : undefined,
      };
    }
    if (Array.isArray(payload.tools)) {
      metadata.tool_names = payload.tools.flatMap((tool) => {
        if (!isRecord(tool)) return [];
        const functionValue = isRecord(tool.function) ? tool.function : undefined;
        const name = functionValue && typeof functionValue.name === "string"
          ? functionValue.name
          : typeof tool.name === "string" ? tool.name : undefined;
        return name ? [name] : [];
      }).slice(0, 64);
    }
    if (Array.isArray(payload.messages)) {
      const messages = payload.messages.filter(isRecord);
      metadata.message_roles = messages
        .map((message) => typeof message.role === "string" ? message.role : "unknown")
        .slice(0, 128);
      metadata.assistant_tool_calls = messages.some((message) => {
        if (message.role !== "assistant") return false;
        if (Array.isArray(message.tool_calls) && message.tool_calls.length > 0) return true;
        return Array.isArray(message.content) && message.content.some((block) =>
          isRecord(block) && (block.type === "toolCall" || block.type === "tool_use"));
      });
      metadata.reasoning_present = messages.some((message) => {
        if (Object.hasOwn(message, "reasoning_content") || Object.hasOwn(message, "reasoning")) return true;
        return Array.isArray(message.content) && message.content.some((block) =>
          isRecord(block) && (block.type === "thinking" || block.type === "reasoning"));
      });
    }
    this.requestMetadata = metadata;
  }

  captureProviderEvent(data: unknown): void {
    this.providerChunkCount += 1;
    const serialized = JSON.stringify(data);
    if (serialized !== undefined) {
      const bytes = Buffer.byteLength(serialized, "utf8");
      this.providerEventBytes += bytes;
      this.providerEventHash.update(serialized, "utf8");
      if (this.providerEventBytes > MAX_TRACE_FILE_BYTES) this.traceTruncated = true;
    }
    if (this.providerHeadEvents.length < MAX_PROVIDER_HEAD_EVENTS) {
      this.providerHeadEvents.push(data);
    } else {
      if (this.providerTailEvents.length >= MAX_PROVIDER_TAIL_EVENTS) {
        this.providerTailEvents.shift();
        this.providerEventDropped += 1;
      }
      this.providerTailEvents.push(data);
      this.traceTruncated ||= this.providerEventDropped > 0;
    }
    if (!isRecord(data)) return;
    if (typeof data.id === "string") this.responseId = data.id;
    const choices = Array.isArray(data.choices) ? data.choices : [];
    const choice = isRecord(choices[0]) ? choices[0] : undefined;
    if (!choice) return;
    if (typeof choice.finish_reason === "string" && choice.finish_reason.length > 0) {
      if (this.finishReasons.length < 16) this.finishReasons.push(choice.finish_reason);
      else this.traceTruncated = true;
    }
    const delta = isRecord(choice.delta) ? choice.delta : undefined;
    if (!delta) return;
    if (typeof delta.content === "string") {
      const result = appendBounded(this.content, delta.content, MAX_COMPARE_CONTENT);
      this.content = result.value;
      this.contentTruncated ||= result.truncated;
      this.traceTruncated ||= result.truncated;
    }
    const toolCalls = Array.isArray(delta.tool_calls) ? delta.tool_calls : [];
    for (const rawToolCall of toolCalls) {
      if (!isRecord(rawToolCall)) continue;
      const index = typeof rawToolCall.index === "number" && Number.isInteger(rawToolCall.index)
        ? rawToolCall.index
        : undefined;
      const id = typeof rawToolCall.id === "string" && rawToolCall.id.length > 0 ? rawToolCall.id : undefined;
      const name = isRecord(rawToolCall.function) && typeof rawToolCall.function.name === "string"
        ? rawToolCall.function.name
        : undefined;
      const key = index !== undefined ? `index:${index}` : id ? `id:${id}` : `anonymous:${this.anonymous++}`;
      if (index === undefined && id === undefined) this.identityError = true;
      const byIndex = index === undefined ? undefined : [...this.toolCalls.values()].find((call) => call.index === index);
      const byId = id === undefined ? undefined : [...this.toolCalls.values()].find((call) => call.id === id);
      if (byIndex && byId && byIndex !== byId) this.identityError = true;
      const call = this.toolCalls.get(key) ?? byIndex ?? byId ?? {
        key,
        index,
        id,
        name,
        arguments: "",
        truncated: false,
        identityMissing: index === undefined && id === undefined,
      };
      if (index !== undefined) call.index = index;
      if (id !== undefined) call.id = id;
      if (name !== undefined) call.name = call.name ?? name;
      if (isRecord(rawToolCall.function) && typeof rawToolCall.function.arguments === "string") {
        const result = appendBounded(call.arguments, rawToolCall.function.arguments, MAX_TRACE_TOOL_ARGUMENTS);
        call.arguments = result.value;
        call.truncated ||= result.truncated;
        this.traceTruncated ||= result.truncated;
      }
      this.toolCalls.set(call.key, call);
    }
  }

  private sdkToolArguments(toolCallId: string | undefined, toolName: string | undefined): unknown {
    const blocks = this.assistant?.content.filter((block) => block.type === "toolCall") ?? [];
    const byId = toolCallId ? blocks.find((block) => block.type === "toolCall" && block.id === toolCallId) : undefined;
    if (byId?.type === "toolCall") return byId.arguments;
    const byName = blocks.filter((block) => block.type === "toolCall" && block.name === toolName);
    return byName.length === 1 && byName[0].type === "toolCall" ? byName[0].arguments : undefined;
  }

  setAssistantMessage(message: AssistantMessage): void {
    this.assistant = message;
  }

  compareFinalContent(content: string): RuntimeBoundaryError | undefined {
    const rawContent = this.content.trim();
    const sdkContent = this.assistant?.content
      .filter((block) => block.type === "text")
      .map((block) => block.type === "text" ? block.text : "")
      .join("")
      .trim();
    if (this.contentTruncated || sdkContent === undefined || rawContent !== content.trim() || sdkContent !== content.trim()) {
      return new RuntimeBoundaryError("TOOL_PARSER_ERROR", "The assembled provider content differed from the SDK assistant text.", {
        raw_content_sha256: sha256(rawContent),
        sdk_content_sha256: sha256(sdkContent ?? ""),
        raw_content_length: rawContent.length,
        sdk_content_length: sdkContent?.length ?? 0,
        content_truncated: this.contentTruncated,
      });
    }
    return undefined;
  }

  get hasBoundaryEvents(): boolean {
    return this.boundaryEvents.length > 0;
  }

  get latestBoundaryCode(): BoundaryCode | undefined {
    const code = this.boundaryEvents.at(-1)?.code;
    return typeof code === "string" ? code as BoundaryCode : undefined;
  }

  recordBoundary(code: BoundaryCode, details: Record<string, unknown>): void {
    if (this.boundaryEvents.length >= 32) {
      this.traceTruncated = true;
      return;
    }
    this.boundaryEvents.push({
      turn: this.turnNumber,
      code,
      response_id: this.responseId,
      provider_chunk_count: this.providerChunkCount,
      finish_reasons: [...this.finishReasons],
      assembled_content: this.content.slice(0, MAX_BOUNDARY_SNAPSHOT_TEXT),
      assembled_tools: [...this.toolCalls.values()].map((call) => ({
        index: call.index,
        id: call.id,
        name: call.name,
        arguments: call.arguments.slice(0, MAX_BOUNDARY_SNAPSHOT_TEXT),
        truncated: call.truncated,
      })),
      details,
    });
  }

  get finishReason(): string | undefined {
    return this.finishReasons.at(-1);
  }

  get hasFinishReason(): boolean {
    return this.finishReasons.length > 0;
  }

  get toolCallCount(): number {
    return this.toolCalls.size;
  }

  findToolCall(toolCallId: string | undefined, toolName: string | undefined): RawToolCall | undefined {
    if (toolCallId) {
      const byId = [...this.toolCalls.values()].find((call) => call.id === toolCallId);
      if (byId) return byId;
    }
    const byName = [...this.toolCalls.values()].filter((call) => call.name === toolName);
    return byName.length === 1 ? byName[0] : undefined;
  }

  readToolArguments(
    toolCallId: string | undefined,
    toolName: string | undefined,
    sdkArguments?: unknown,
  ): { raw: string; value: unknown } | RuntimeBoundaryError {
    const call = this.findToolCall(toolCallId, toolName);
    if (!call) {
      return new RuntimeBoundaryError("TOOL_PARSER_ERROR", "The raw tool-call identity could not be matched.", {
        tool_name: toolName,
      });
    }
    if (call.identityMissing || call.truncated || this.identityError) {
      return new RuntimeBoundaryError("TOOL_PARSER_ERROR", "The raw tool-call stream could not be assembled safely.", {
        tool_name: call.name,
        index: call.index,
        raw_length: call.arguments.length,
      });
    }
    try {
      const value = parseStrictToolArguments(call.arguments);
      const sdkValue = sdkArguments === undefined ? this.sdkToolArguments(toolCallId, toolName) : sdkArguments;
      if (sdkValue !== undefined) {
        const rawValueHash = sha256(stableJson(value));
        const sdkValueHash = sha256(stableJson(sdkValue));
        const equal = sameJson(value, sdkValue);
        if (this.toolComparisons.length < 128) {
          this.toolComparisons.push({
            turn: this.turnNumber,
            code: equal ? "TOOL_ARGUMENT_MATCH" : "TOOL_PARSER_ERROR",
            tool_name: call.name,
            tool_call_id: call.id,
            raw_value_sha256: rawValueHash,
            sdk_value_sha256: sdkValueHash,
          });
        }
        if (!equal) {
          return new RuntimeBoundaryError("TOOL_PARSER_ERROR", "The SDK tool arguments differed from the raw provider JSON.", {
            tool_name: call.name,
            index: call.index,
            raw_value_sha256: rawValueHash,
            sdk_value_sha256: sdkValueHash,
          });
        }
      }
      return { raw: call.arguments, value };
    } catch (error) {
      return error instanceof RuntimeBoundaryError
        ? error
        : new RuntimeBoundaryError("TOOL_ARGUMENT_JSON", "Tool arguments were not valid JSON.");
    }
  }

  validateToolCall(toolCallId: string | undefined, toolName: string | undefined): RuntimeBoundaryError | undefined {
    const result = this.readToolArguments(toolCallId, toolName);
    return result instanceof RuntimeBoundaryError ? result : undefined;
  }

  async write(code: BoundaryCode, details: Record<string, unknown>, apiKey: string): Promise<void> {
    if (this.traceWritten) return;
    this.traceWritten = true;
    const directory = process.env.SCREENING_AI_TRACE_DIR?.trim() || resolve(process.cwd(), "data", "ai-traces");
    const providerEvents = [...this.providerHeadEvents, ...this.providerTailEvents];
    const sdkText = this.assistant?.content
      .filter((block) => block.type === "text")
      .map((block) => block.type === "text" ? block.text : "")
      .join("") ?? "";
    const payload = {
      version: 1,
      run_id: this.runId,
      turn: this.turnNumber,
      created_at: new Date().toISOString(),
      code,
      response_id: this.responseId,
      provider_chunk_count: this.providerChunkCount,
      provider_event_bytes: this.providerEventBytes,
      provider_event_sha256: this.providerEventHash.digest("hex"),
      provider_event_dropped: this.providerEventDropped,
      provider_event_source: "pi-ai onProviderStreamEvent parsed provider chunks; raw HTTP bytes are unavailable here",
      request_metadata: this.requestMetadata,
      finish_reasons: this.finishReasons,
      assembled: {
        content: this.content,
        content_sha256: sha256(this.content),
        tools: [...this.toolCalls.values()].map((call) => ({
          index: call.index,
          id: call.id,
          name: call.name,
          arguments: call.arguments,
          arguments_sha256: sha256(call.arguments),
          truncated: call.truncated,
        })),
      },
      provider_events: providerEvents,
      boundary_events: this.boundaryEvents,
      tool_argument_comparisons: this.toolComparisons,
      sdk_message: traceAssistant(this.assistant, apiKey),
      sdk_text_sha256: sha256(sdkText),
      details,
      trace_truncated: this.traceTruncated,
    };
    try {
      await mkdir(directory, { recursive: true });
      const file = join(directory, `screening-${Date.now()}-${randomUUID()}.json`);
      const tracedPayload = traceValue(payload, apiKey) as JsonRecord;
      let serialized = JSON.stringify(tracedPayload, null, 2);
      if (Buffer.byteLength(serialized, "utf8") > MAX_TRACE_FILE_BYTES) {
        tracedPayload.provider_events = [
          ...providerEvents.slice(0, 8),
          ...providerEvents.slice(-32),
        ];
        tracedPayload.provider_events_dropped = this.providerEventDropped + Math.max(0, providerEvents.length - 40);
        tracedPayload.trace_truncated = true;
        serialized = JSON.stringify(tracedPayload, null, 2);
      }
      if (Buffer.byteLength(serialized, "utf8") > MAX_TRACE_FILE_BYTES) {
        const assembled = isRecord(tracedPayload.assembled) ? tracedPayload.assembled : {};
        tracedPayload.provider_events = [];
        tracedPayload.provider_events_dropped = this.providerChunkCount;
        tracedPayload.assembled = {
          ...assembled,
          content: typeof assembled.content === "string" ? assembled.content.slice(0, 8192) : assembled.content,
          tools: Array.isArray(assembled.tools)
            ? assembled.tools.map((tool) => isRecord(tool)
              ? { ...tool, arguments: typeof tool.arguments === "string" ? tool.arguments.slice(0, 16384) : tool.arguments }
              : tool)
            : assembled.tools,
        };
        serialized = JSON.stringify(tracedPayload, null, 2);
      }
      await writeFile(file, serialized, { encoding: "utf8", mode: 0o600 });
      const entries = await readdir(directory, { withFileTypes: true });
      const files = [] as { path: string; mtime: number }[];
      for (const entry of entries) {
        if (!entry.isFile() || !entry.name.startsWith("screening-") || !entry.name.endsWith(".json")) continue;
        const path = join(directory, entry.name);
        const info = await stat(path);
        files.push({ path, mtime: info.mtimeMs });
      }
      files.sort((left, right) => right.mtime - left.mtime);
      await Promise.all(files.slice(MAX_TRACE_FILES).map((entry) => unlink(entry.path).catch(() => undefined)));
    } catch {
      // Diagnostics must never turn a classified model/tool error into an
      // unrelated runtime failure.
    }
  }
}
