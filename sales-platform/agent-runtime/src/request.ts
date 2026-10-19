import {
  DEFAULT_ANALYSIS_GOAL,
  DEFAULT_ANALYSIS_SEMANTIC_VERSION,
  DEFAULT_LIMITS,
  DUPLEX_PROTOCOL_VERSION,
  MAX_CONTENT_CHARS,
  MAX_MESSAGES,
  PROTOCOL_VERSION,
} from "./constants.js";
import {
  hasCompanyMaterial,
  normalizeAnalysisString,
  normalizeCapabilityCard,
  normalizeCustomerPreferences,
} from "./context.js";
import { ProtocolError, isObject, type JsonObject, type ResearchContext, type RuntimeContext, type RuntimeLimits, type RuntimeMessage, type RuntimeRequest } from "./types.js";

function asString(value: unknown, field: string, maxLength = MAX_CONTENT_CHARS): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) {
    throw new ProtocolError(`invalid ${field}`);
  }
  return value;
}

function asObject(value: unknown, field: string): JsonObject {
  if (!isObject(value)) throw new ProtocolError(`invalid ${field}`);
  return value;
}

function clampInteger(value: unknown, fallback: number, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.max(minimum, Math.min(maximum, Math.trunc(value)));
}

function normalizeLimits(value: unknown): RuntimeLimits {
  const limits = isObject(value) ? value : {};
  return {
    max_turns: clampInteger(limits.max_turns, DEFAULT_LIMITS.max_turns, 1, DEFAULT_LIMITS.max_turns),
    max_tool_calls: clampInteger(limits.max_tool_calls, DEFAULT_LIMITS.max_tool_calls, 0, DEFAULT_LIMITS.max_tool_calls),
    timeout_ms: clampInteger(limits.timeout_ms, DEFAULT_LIMITS.timeout_ms, 1, DEFAULT_LIMITS.timeout_ms),
    max_output_tokens: clampInteger(limits.max_output_tokens, DEFAULT_LIMITS.max_output_tokens, 64, 65_536),
  };
}

function normalizeRecordList(value: unknown, field: string): JsonObject[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new ProtocolError(`invalid ${field}`);
  return value.map((item, index) => asObject(item, `${field}[${index}]`));
}

function normalizeResearchContext(value: unknown): ResearchContext | undefined {
  if (value === undefined) return undefined;
  const research = asObject(value, "context.research");
  const policyVersion = asString(research.policy_version, "context.research.policy_version", 256).trim();
  if (!policyVersion) throw new ProtocolError("invalid context.research.policy_version");
  const sources = normalizeRecordList(research.sources, "context.research.sources");
  if (sources.length > 500) throw new ProtocolError("invalid context.research.sources");
  return { policy_version: policyVersion, sources };
}

export function parseRequest(value: unknown): RuntimeRequest {
  const input = asObject(value, "request");
  if (input.protocol !== PROTOCOL_VERSION && input.protocol !== DUPLEX_PROTOCOL_VERSION) {
    throw new ProtocolError("unsupported protocol");
  }

  const config = asObject(input.config, "config");
  const configuredBaseUrl = asString(config.base_url, "config.base_url", 2048).trim();
  const baseUrl = configuredBaseUrl.replace(/\/+$/u, "").replace(/\/chat\/completions$/iu, "");
  const model = asString(config.model, "config.model", 256).trim();
  const apiKey = asString(config.api_key, "config.api_key", 4096);
  if (!baseUrl || !model || !apiKey.trim() || !/^https?:\/\//iu.test(baseUrl) || /\/\/(?:[^/]+)@/u.test(baseUrl)) {
    throw new ProtocolError("invalid model endpoint");
  }
  try {
    const parsed = new URL(baseUrl);
    if (!parsed.hostname || parsed.username || parsed.password) throw new ProtocolError("invalid model endpoint");
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
  const rawCapability = context.company_capability_card === undefined
    ? {}
    : asObject(context.company_capability_card, "context.company_capability_card");
  const normalizedCapability = normalizeCapabilityCard(rawCapability);
  const customerPreferences = normalizeCustomerPreferences(context.customer_preferences, normalizedCapability.legacyPreferences);
  const derivedCompanyProfilePresent = hasCompanyMaterial(normalizedCapability.card);
  const explicitCompanyProfilePresent = context.company_profile_present;
  if (explicitCompanyProfilePresent !== undefined && typeof explicitCompanyProfilePresent !== "boolean") {
    throw new ProtocolError("invalid context.company_profile_present");
  }
  const companyProfilePresent = explicitCompanyProfilePresent === undefined
    ? derivedCompanyProfilePresent
    : explicitCompanyProfilePresent && derivedCompanyProfilePresent;
  const relatedProjects = normalizeRecordList(context.related_projects, "context.related_projects");
  const research = normalizeResearchContext(context.research);
  const taskKind = input.task_kind === undefined ? "market" : input.task_kind;
  if (taskKind !== "market" && taskKind !== "reading") throw new ProtocolError("invalid task_kind");

  const normalizedContext: RuntimeContext = {
    project,
    company_capability_card: normalizedCapability.card,
    related_projects: relatedProjects,
    analysis_goal: normalizeAnalysisString(context.analysis_goal, "context.analysis_goal", DEFAULT_ANALYSIS_GOAL),
    analysis_semantic_version: normalizeAnalysisString(
      context.analysis_semantic_version,
      "context.analysis_semantic_version",
      DEFAULT_ANALYSIS_SEMANTIC_VERSION,
    ),
    customer_preferences: customerPreferences,
    company_profile_present: companyProfilePresent,
    ...(research ? { research } : {}),
  };
  return {
    protocol: input.protocol as 1 | 2,
    task_kind: taskKind,
    config: { base_url: baseUrl, model, api_key: apiKey },
    messages,
    context: normalizedContext,
    limits: normalizeLimits(input.limits),
  };
}
