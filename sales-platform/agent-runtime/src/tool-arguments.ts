import {
  DEFAULT_DOCUMENT_LIMIT,
  MAX_DOCUMENT_ID_CHARS,
  MAX_DOCUMENT_LIMIT,
  MAX_DOCUMENT_OFFSET,
  MAX_DOCUMENT_PAGE,
  MAX_DOCUMENT_QUERY_CHARS,
  MAX_READ_RESEARCH_SOURCE_LIMIT,
  MAX_READ_RESEARCH_SOURCE_OFFSET,
  MAX_RESEARCH_ID_CHARS,
  MAX_RESEARCH_PAGE,
  MAX_RESEARCH_PURPOSE_CHARS,
  MAX_RESEARCH_QUERY_CHARS,
  MAX_RESEARCH_QUOTE_CHARS,
  MAX_RESEARCH_REASON_CHARS,
  MAX_RESEARCH_SOURCE_IDS,
  MAX_SEARCH_PROJECT_SOURCES_LIMIT,
  MAX_SEARCH_PROJECT_SOURCES_OFFSET,
  MAX_SEARCH_RESEARCH_CONTENT_LIMIT,
  RESEARCH_RELATIONS,
} from "./tool-constants.js";
import { RuntimeBoundaryError } from "./json-boundary.js";
import {
  isObject,
  type AssessSourceRelationArguments,
  type FetchProjectDocumentsArguments,
  type ReadResearchSourceArguments,
  type SearchProjectSourcesArguments,
  type SearchResearchContentArguments,
  type SourceRelation,
} from "./types.js";

class ToolArgumentError extends Error {
  constructor() {
    super("invalid tool arguments");
    this.name = "ToolArgumentError";
  }
}

function assertKnownFields(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!isObject(value)) throw new ToolArgumentError();
  const allowed = new Set(fields);
  if (Object.keys(value).some((field) => !allowed.has(field))) throw new ToolArgumentError();
  return value;
}

function rejectUrlOrPath(value: string): void {
  if (/(?:https?|file|data):\/\//iu.test(value) || /^[A-Za-z]:[\\/]/u.test(value) || /^\\{1,2}/u.test(value) || /^\/{1,2}/u.test(value)) {
    throw new ToolArgumentError();
  }
}

function requiredResearchString(value: unknown, maxLength: number, rejectTransport = true): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) throw new ToolArgumentError();
  const normalized = value.trim();
  if (!normalized) throw new ToolArgumentError();
  if (rejectTransport) rejectUrlOrPath(normalized);
  return normalized;
}

function requiredResearchText(value: unknown, maxLength: number): string {
  return requiredResearchString(value, maxLength, false);
}

function optionalResearchString(value: unknown, maxLength: number): string | undefined {
  if (value === undefined) return undefined;
  return requiredResearchString(value, maxLength);
}

function optionalResearchInteger(value: unknown, minimum: number, maximum: number): number | undefined {
  if (value === undefined) return undefined;
  if (!Number.isSafeInteger(value) || typeof value !== "number" || value < minimum || value > maximum) throw new ToolArgumentError();
  return value;
}

function optionalResearchBoolean(value: unknown): boolean | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "boolean") throw new ToolArgumentError();
  return value;
}

function researchIdentifier(value: unknown): string {
  const normalized = requiredResearchString(value, MAX_RESEARCH_ID_CHARS);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:+-]*$/u.test(normalized)) throw new ToolArgumentError();
  return normalized;
}

function documentIdentifier(value: unknown): string {
  const normalized = requiredResearchString(value, MAX_DOCUMENT_ID_CHARS);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:+-]*$/u.test(normalized)) throw new ToolArgumentError();
  return normalized;
}

function optionalDocumentInteger(value: unknown, minimum: number, maximum: number): number | undefined {
  return value === undefined ? undefined : optionalResearchInteger(value, minimum, maximum);
}

function optionalResearchIsoDate(value: unknown): string | undefined {
  const normalized = optionalResearchString(value, 64);
  if (normalized === undefined) return undefined;
  if (!/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/u.test(normalized)) {
    throw new ToolArgumentError();
  }
  if (!Number.isFinite(Date.parse(normalized))) throw new ToolArgumentError();
  const [year, month, day] = normalized.slice(0, 10).split("-").map(Number);
  const calendar = new Date(Date.UTC(year, month - 1, day));
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) throw new ToolArgumentError();
  return normalized;
}

function sourceIds(value: unknown): string[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value) || value.length > MAX_RESEARCH_SOURCE_IDS) throw new ToolArgumentError();
  return value.map(researchIdentifier);
}

export function validateFetchProjectDocuments(value: unknown): FetchProjectDocumentsArguments {
  const args = assertKnownFields(value, ["record_id", "document_id", "page", "query", "offset", "limit"]);
  const recordId = args.record_id;
  if (recordId !== undefined && (typeof recordId !== "number" || !Number.isSafeInteger(recordId) || recordId < 1)) throw new ToolArgumentError();
  const documentId = args.document_id === undefined ? undefined : documentIdentifier(args.document_id);
  const page = optionalDocumentInteger(args.page, 1, MAX_DOCUMENT_PAGE);
  const query = args.query === undefined ? undefined : requiredResearchString(args.query, MAX_DOCUMENT_QUERY_CHARS);
  const offset = optionalDocumentInteger(args.offset, 0, MAX_DOCUMENT_OFFSET);
  const limit = optionalDocumentInteger(args.limit, 1, MAX_DOCUMENT_LIMIT);
  return {
    ...(recordId === undefined ? {} : { record_id: recordId }),
    ...(documentId === undefined ? {} : { document_id: documentId }),
    ...(page === undefined ? {} : { page }),
    ...(query === undefined ? {} : { query }),
    ...(offset === undefined ? {} : { offset }),
    ...(limit === undefined ? { } : { limit }),
  };
}

export function validateSearchProjectSources(value: unknown): SearchProjectSourcesArguments {
  const args = assertKnownFields(value, ["purpose", "query", "source_ids", "limit", "offset", "published_after", "published_before"]);
  const publishedAfter = optionalResearchIsoDate(args.published_after);
  const publishedBefore = optionalResearchIsoDate(args.published_before);
  if (publishedAfter && publishedBefore && Date.parse(publishedAfter) > Date.parse(publishedBefore)) throw new ToolArgumentError();
  const ids = sourceIds(args.source_ids);
  return {
    purpose: requiredResearchString(args.purpose, MAX_RESEARCH_PURPOSE_CHARS),
    query: requiredResearchString(args.query, MAX_RESEARCH_QUERY_CHARS),
    source_ids: ids ?? [],
    limit: optionalResearchInteger(args.limit, 1, MAX_SEARCH_PROJECT_SOURCES_LIMIT) ?? 10,
    offset: optionalResearchInteger(args.offset, 0, MAX_SEARCH_PROJECT_SOURCES_OFFSET) ?? 0,
    published_after: publishedAfter ?? null,
    published_before: publishedBefore ?? null,
  };
}

export function validateReadResearchSource(value: unknown): ReadResearchSourceArguments {
  const args = assertKnownFields(value, ["source_ref", "purpose", "page", "offset", "limit", "refresh"]);
  return {
    source_ref: researchIdentifier(args.source_ref),
    purpose: requiredResearchString(args.purpose, MAX_RESEARCH_PURPOSE_CHARS),
    page: optionalResearchInteger(args.page, 0, MAX_RESEARCH_PAGE) ?? 0,
    offset: optionalResearchInteger(args.offset, 0, MAX_READ_RESEARCH_SOURCE_OFFSET) ?? 0,
    limit: optionalResearchInteger(args.limit, 1, MAX_READ_RESEARCH_SOURCE_LIMIT) ?? MAX_READ_RESEARCH_SOURCE_LIMIT,
    refresh: optionalResearchBoolean(args.refresh) ?? false,
  };
}

export function validateAssessSourceRelation(value: unknown): AssessSourceRelationArguments {
  const args = assertKnownFields(value, ["source_ref", "source_version", "relation", "quote", "reason"]);
  if (typeof args.relation !== "string" || !RESEARCH_RELATIONS.has(args.relation as SourceRelation)) throw new ToolArgumentError();
  return {
    source_ref: researchIdentifier(args.source_ref),
    source_version: researchIdentifier(args.source_version),
    relation: args.relation as SourceRelation,
    quote: requiredResearchText(args.quote, MAX_RESEARCH_QUOTE_CHARS),
    reason: requiredResearchText(args.reason, MAX_RESEARCH_REASON_CHARS),
  };
}

export function validateSearchResearchContent(value: unknown): SearchResearchContentArguments {
  const args = assertKnownFields(value, ["source_ref", "source_version", "query", "purpose", "limit"]);
  return {
    source_ref: researchIdentifier(args.source_ref),
    source_version: researchIdentifier(args.source_version),
    query: requiredResearchString(args.query, MAX_RESEARCH_QUERY_CHARS),
    purpose: requiredResearchString(args.purpose, MAX_RESEARCH_PURPOSE_CHARS),
    limit: optionalResearchInteger(args.limit, 1, MAX_SEARCH_RESEARCH_CONTENT_LIMIT) ?? 10,
  };
}

export function validateRawToolSchema(toolName: string, value: unknown): void {
  try {
    switch (toolName) {
      case "read_project_records": {
        const args = assertKnownFields(value, ["record_id", "query"]);
        if (args.record_id !== undefined && typeof args.record_id !== "string" && typeof args.record_id !== "number") throw new ToolArgumentError();
        if (args.query !== undefined && typeof args.query !== "string") throw new ToolArgumentError();
        return;
      }
      case "read_company_capability": {
        const args = assertKnownFields(value, ["topic"]);
        if (args.topic !== undefined && typeof args.topic !== "string") throw new ToolArgumentError();
        return;
      }
      case "read_related_records": {
        const args = assertKnownFields(value, ["project_id"]);
        if (args.project_id !== undefined && typeof args.project_id !== "string" && typeof args.project_id !== "number") throw new ToolArgumentError();
        return;
      }
      case "fetch_project_documents": validateFetchProjectDocuments(value); return;
      case "search_project_sources": validateSearchProjectSources(value); return;
      case "read_research_source": validateReadResearchSource(value); return;
      case "assess_source_relation": validateAssessSourceRelation(value); return;
      case "search_research_content": validateSearchResearchContent(value); return;
      default: throw new ToolArgumentError();
    }
  } catch {
    throw new RuntimeBoundaryError("TOOL_SCHEMA", "Tool arguments did not satisfy the runtime schema.", { tool_name: toolName });
  }
}
