export const MAX_RESEARCH_PURPOSE_CHARS = 4_000;
export const MAX_RESEARCH_QUERY_CHARS = 4_000;
export const MAX_RESEARCH_REASON_CHARS = 4_000;
export const MAX_RESEARCH_QUOTE_CHARS = 4_000;
export const MAX_RESEARCH_ID_CHARS = 256;
export const MAX_RESEARCH_SOURCE_IDS = 20;
export const MAX_SEARCH_PROJECT_SOURCES_LIMIT = 50;
export const MAX_SEARCH_PROJECT_SOURCES_OFFSET = 10_000;
export const MAX_READ_RESEARCH_SOURCE_LIMIT = 4_000;
export const MAX_READ_RESEARCH_SOURCE_OFFSET = 240_000;
export const MAX_SEARCH_RESEARCH_CONTENT_LIMIT = 20;
export const MAX_RESEARCH_PAGE = 100_000;
export const DEFAULT_DOCUMENT_LIMIT = 8_000;
export const MAX_DOCUMENT_LIMIT = 16_000;
export const MAX_DOCUMENT_OFFSET = 500_000;
export const MAX_DOCUMENT_PAGE = 100_000;
export const MAX_DOCUMENT_ID_CHARS = 160;
export const MAX_DOCUMENT_QUERY_CHARS = 4_000;
export const RESEARCH_RELATIONS = new Set([
  "unconfirmed",
  "same_procurement",
  "related_project",
  "buyer_background",
  "unrelated",
  "conflict",
] as const);
