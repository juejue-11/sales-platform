import {
  CAPABILITY_CARD_FIELDS,
  COMPANY_MATERIAL_FIELDS,
  CUSTOMER_PREFERENCE_FIELDS,
  CUSTOMER_PREFERENCE_STRING_FIELDS,
} from "./constants.js";
import { ProtocolError, isObject, type JsonObject, type RuntimeContext } from "./types.js";

export function hasSubstantiveValue(value: unknown): boolean {
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.some((item) => hasSubstantiveValue(item));
  if (isObject(value)) return Object.values(value).some((item) => hasSubstantiveValue(item));
  return value !== undefined && value !== null;
}

export function normalizeCapabilityCard(value: JsonObject): { card: JsonObject; legacyPreferences: JsonObject } {
  const card: JsonObject = {};
  for (const field of CAPABILITY_CARD_FIELDS) {
    if (Object.hasOwn(value, field) && value[field] !== undefined && value[field] !== null) {
      card[field] = value[field];
    }
  }
  const aliases: Record<string, (typeof CAPABILITY_CARD_FIELDS)[number]> = {
    business_scope: "scope_description",
    products: "supported_products",
    case_examples: "solution_examples",
  };
  for (const [legacy, current] of Object.entries(aliases)) {
    if (!Object.hasOwn(card, current) && Object.hasOwn(value, legacy) && value[legacy] !== undefined && value[legacy] !== null) {
      card[current] = value[legacy];
    }
  }
  const legacyPreferences: JsonObject = {};
  if (Object.hasOwn(value, "target_keywords")) legacyPreferences.target_keywords = value.target_keywords;
  else if (Object.hasOwn(value, "keywords")) legacyPreferences.target_keywords = value.keywords;
  if (Object.hasOwn(value, "region_preferences")) legacyPreferences.region_preferences = value.region_preferences;
  else if (Object.hasOwn(value, "region")) legacyPreferences.region_preferences = value.region;
  for (const field of CUSTOMER_PREFERENCE_STRING_FIELDS) {
    if (Object.hasOwn(value, field)) legacyPreferences[field] = value[field];
  }
  return { card, legacyPreferences };
}

function asString(value: unknown, field: string, maxLength = 1_000_000): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) {
    throw new ProtocolError(`invalid ${field}`);
  }
  return value;
}

function normalizePreferenceValue(value: unknown, field: string): unknown {
  if (value === null || value === undefined) return undefined;
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value.map((item, index) => asString(item, `${field}[${index}]`).trim()).filter(Boolean);
  }
  throw new ProtocolError(`invalid ${field}`);
}

function normalizePreferenceString(value: unknown, field: string): string | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value !== "string") throw new ProtocolError(`invalid ${field}`);
  return value.trim();
}

export function normalizeCustomerPreferences(value: unknown, legacy: JsonObject): JsonObject {
  const supplied = value === undefined ? {} : asObject(value, "context.customer_preferences");
  const preferences: JsonObject = {};
  for (const field of CUSTOMER_PREFERENCE_FIELDS) {
    const candidate = Object.hasOwn(supplied, field) ? supplied[field] : legacy[field];
    const normalized = CUSTOMER_PREFERENCE_STRING_FIELDS.has(field)
      ? normalizePreferenceString(candidate, `context.customer_preferences.${field}`)
      : normalizePreferenceValue(candidate, `context.customer_preferences.${field}`);
    if (normalized !== undefined) preferences[field] = normalized;
  }
  return preferences;
}

function asObject(value: unknown, field: string): JsonObject {
  if (!isObject(value)) throw new ProtocolError(`invalid ${field}`);
  return value;
}

export function hasCompanyMaterial(card: JsonObject): boolean {
  return COMPANY_MATERIAL_FIELDS.some((field) => hasSubstantiveValue(card[field]));
}

export function normalizeAnalysisString(value: unknown, field: string, fallback: string): string {
  if (value === undefined) return fallback;
  const normalized = asString(value, field).trim();
  if (!normalized) throw new ProtocolError(`invalid ${field}`);
  return normalized;
}

export function normalizeRecords(project: JsonObject): JsonObject[] {
  const records = project.records;
  return Array.isArray(records) ? records.filter(isObject) : [];
}

export function modelRecordView(record: JsonObject): JsonObject {
  if (!Object.prototype.hasOwnProperty.call(record, "evidence_fragments")) return record;
  const view = { ...record };
  delete view.body_text;
  return view;
}

export function valueId(value: unknown): string | undefined {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return undefined;
}

export function ownContextValue(context: RuntimeContext, field: string): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(context, field);
  return descriptor && !descriptor.get ? descriptor.value : undefined;
}
