import { TOOL_NAMES } from "./constants.js";

export type JsonObject = Record<string, unknown>;

export type ToolName = (typeof TOOL_NAMES)[number];

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
  analysis_goal?: string;
  analysis_semantic_version?: string;
  customer_preferences?: JsonObject;
  company_profile_present?: boolean;
  research?: ResearchContext;
}

export interface ResearchContext {
  policy_version: string;
  sources: JsonObject[];
}

export interface RuntimeLimits {
  max_turns: number;
  max_tool_calls: number;
  timeout_ms: number;
  max_output_tokens: number;
}

export interface RuntimeRequest {
  protocol: 1 | 2;
  task_kind: "market" | "reading";
  config: RuntimeConfig;
  messages: RuntimeMessage[];
  context: RuntimeContext;
  limits: RuntimeLimits;
}

export interface RuntimeUsage {
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ProgressEvent {
  type: "progress";
  stage: "started" | "model" | "tool_start" | "tool_end" | "completed";
  label: string;
  tool?: ToolName;
  turn?: number;
  is_error?: true;
  at: string;
}

export interface ResultEvent {
  type: "result";
  engine: "pi_sdk";
  task_kind: "market" | "reading";
  content: string;
  finish_reason: "stop";
  usage: RuntimeUsage;
}

export interface ErrorEvent {
  type: "error";
  code:
    | "INVALID_INPUT"
    | "MODEL_CONFIG"
    | "MODEL_ERROR"
    | "MODEL_FORMAT_ERROR"
    | "STREAM_INTERRUPTED"
    | "TOOL_ARGUMENT_JSON"
    | "TOOL_SCHEMA"
    | "TOOL_PARSER_ERROR"
    | "OUTPUT_TOKEN_LIMIT"
    | "TIMEOUT"
    | "TURN_LIMIT"
    | "TOOL_LIMIT"
    | "RPC_ERROR"
    | "NO_FINAL"
    | "INTERNAL";
  message: string;
}

export interface UsageEvent {
  type: "usage";
  usage: RuntimeUsage;
}

export type RuntimeEvent = ProgressEvent | ResultEvent | ErrorEvent | UsageEvent;

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProtocolError";
  }
}

export class LimitError extends Error {
  constructor(public readonly kind: "turn" | "tool" | "timeout" | "output") {
    super(kind);
    this.name = "LimitError";
  }
}

export class RpcError extends Error {
  constructor(message = "RPC bridge failed") {
    super(message);
    this.name = "RpcError";
  }
}

export function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export interface SearchProjectSourcesArguments {
  purpose: string;
  query: string;
  source_ids?: string[];
  limit?: number;
  offset?: number;
  published_after?: string | null;
  published_before?: string | null;
}

export interface ReadResearchSourceArguments {
  source_ref: string;
  purpose: string;
  page?: number;
  offset?: number;
  limit?: number;
  refresh?: boolean;
}

export type SourceRelation =
  | "unconfirmed"
  | "same_procurement"
  | "related_project"
  | "buyer_background"
  | "unrelated"
  | "conflict";

export interface AssessSourceRelationArguments {
  source_ref: string;
  source_version: string;
  relation: SourceRelation;
  quote: string;
  reason: string;
}

export interface SearchResearchContentArguments {
  source_ref: string;
  source_version: string;
  query: string;
  purpose: string;
  limit?: number;
}

export interface FetchProjectDocumentsArguments {
  record_id?: number;
  document_id?: string;
  page?: number;
  query?: string;
  offset?: number;
  limit?: number;
}

export type HostToolArguments = {
  read_project_records: { record_id?: number | string; query?: string };
  read_company_capability: { topic?: string };
  read_related_records: { project_id?: number | string };
  fetch_project_documents: FetchProjectDocumentsArguments;
  search_project_sources: SearchProjectSourcesArguments;
  read_research_source: ReadResearchSourceArguments;
  assess_source_relation: AssessSourceRelationArguments;
  search_research_content: SearchResearchContentArguments;
};

export type HostToolName = keyof HostToolArguments;

export type HostRpcToolName =
  | "fetch_project_documents"
  | "search_project_sources"
  | "read_research_source"
  | "assess_source_relation"
  | "search_research_content";

export type HostRpcArguments = Pick<HostToolArguments, HostRpcToolName>;

export interface HostToolBridge {
  request<Tool extends HostRpcToolName>(tool: Tool, argumentsValue: HostRpcArguments[Tool]): Promise<JsonObject>;
  setAbort?(onFailure: (error: RpcError) => void): void;
  close(): void;
}
