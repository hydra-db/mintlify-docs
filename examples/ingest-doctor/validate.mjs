#!/usr/bin/env node

import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export const LIMITS = Object.freeze({
  entitiesPerGraph: 5_000,
  relationsPerGraph: 10_000,
  relationsPerEntity: 500,
  entityNameCharacters: 256,
  predicateCharacters: 256,
  relationContextCharacters: 2_000,
});

export const LOCAL_SAFETY_LIMITS = Object.freeze({
  manifestBytes: 128 * 1024 * 1024,
  visibleDiagnostics: 200,
  structureDepth: 128,
  scannedNodes: 200_000,
});

const MAX_VISIBLE_DIAGNOSTICS = LOCAL_SAFETY_LIMITS.visibleDiagnostics;
const MAX_STRUCTURE_DEPTH = LOCAL_SAFETY_LIMITS.structureDepth;
const MAX_SCANNED_NODES = LOCAL_SAFETY_LIMITS.scannedNodes;
const MAX_MANIFEST_BYTES = LOCAL_SAFETY_LIMITS.manifestBytes;
const UNSAFE_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const APP_KINDS = new Set([
  "email",
  "message",
  "ticket",
  "knowledge_base",
  "comment",
  "custom",
]);
const SEVERITY_ORDER = Object.freeze({ error: 0, warning: 1 });

function isRecord(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}

function sortedEntries(value) {
  return Object.keys(value)
    .sort((left, right) => left.localeCompare(right))
    .map((key) => [key, value[key]]);
}

function characterLength(value) {
  return Array.from(value).length;
}

function createCollector() {
  const visible = [];
  let errorCount = 0;
  let warningCount = 0;

  return {
    add(severity, code, path, message) {
      if (severity === "error") {
        errorCount += 1;
      } else {
        warningCount += 1;
      }

      if (visible.length < MAX_VISIBLE_DIAGNOSTICS) {
        visible.push({ severity, code, path, message });
      }
    },
    finish() {
      visible.sort((left, right) => {
        return (
          SEVERITY_ORDER[left.severity] - SEVERITY_ORDER[right.severity] ||
          left.code.localeCompare(right.code) ||
          left.path.localeCompare(right.path) ||
          left.message.localeCompare(right.message)
        );
      });

      const total = errorCount + warningCount;
      return {
        diagnostics: visible,
        errors: errorCount,
        warnings: warningCount,
        diagnosticsShown: visible.length,
        diagnosticsTruncated: total > visible.length,
      };
    },
  };
}

function scanUnsafeKeys(value, path, collector) {
  const pending = [{ value, path, depth: 0, exiting: false }];
  // Active ancestors detect back-edges while shared DAG nodes are charged
  // once per path against the depth and expanded-node safety limits.
  const active = new WeakSet();
  let queuedNodes = 1;
  let scannedNodes = 0;
  let safe = true;

  while (pending.length > 0) {
    const current = pending.pop();
    if (current.exiting) {
      active.delete(current.value);
      continue;
    }

    queuedNodes -= 1;
    scannedNodes += 1;
    if (scannedNodes > MAX_SCANNED_NODES) {
      collector.add(
        "error",
        "E_STRUCTURE_NODE_LIMIT",
        current.path,
        "The local manifest is too structurally large to inspect safely.",
      );
      return false;
    }
    if (current.value === null || typeof current.value !== "object") {
      continue;
    }
    if (current.depth > MAX_STRUCTURE_DEPTH) {
      collector.add(
        "error",
        "E_STRUCTURE_DEPTH_LIMIT",
        current.path,
        "The local manifest is nested too deeply to inspect safely.",
      );
      safe = false;
      continue;
    }

    if (active.has(current.value)) {
      collector.add(
        "error",
        "E_CYCLIC_VALUE",
        current.path,
        "The local manifest contains a cyclic value that cannot be encoded as JSON.",
      );
      safe = false;
      continue;
    }
    active.add(current.value);
    pending.push({ value: current.value, exiting: true });

    if (Array.isArray(current.value)) {
      if (
        scannedNodes + queuedNodes + current.value.length >
        MAX_SCANNED_NODES
      ) {
        collector.add(
          "error",
          "E_STRUCTURE_NODE_LIMIT",
          current.path,
          "The local manifest is too structurally large to inspect safely.",
        );
        return false;
      }
      for (let index = current.value.length - 1; index >= 0; index -= 1) {
        pending.push({
          value: current.value[index],
          path: `${current.path}[${index}]`,
          depth: current.depth + 1,
          exiting: false,
        });
      }
      queuedNodes += current.value.length;
      continue;
    }

    const entries = sortedEntries(current.value);
    if (
      scannedNodes + queuedNodes + entries.length >
      MAX_SCANNED_NODES
    ) {
      collector.add(
        "error",
        "E_STRUCTURE_NODE_LIMIT",
        current.path,
        "The local manifest is too structurally large to inspect safely.",
      );
      return false;
    }
    let queuedChildren = 0;
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const [key, child] = entries[index];
      const childPath = `${current.path}.*`;
      if (UNSAFE_KEYS.has(key)) {
        collector.add(
          "error",
          "E_UNSAFE_OBJECT_KEY",
          childPath,
          "An object contains a prototype-affecting key.",
        );
        safe = false;
      } else {
        pending.push({
          value: child,
          path: childPath,
          depth: current.depth + 1,
          exiting: false,
        });
        queuedChildren += 1;
      }
    }
    queuedNodes += queuedChildren;
  }

  return safe;
}

function parseOpaqueJson(manifest, field, collector) {
  if (!Object.hasOwn(manifest, field)) {
    return { present: false, value: undefined };
  }

  const encoded = manifest[field];
  if (typeof encoded !== "string") {
    collector.add(
      "error",
      "E_JSON_STRING_REQUIRED",
      field,
      "Multipart JSON fields must be JSON-encoded strings.",
    );
    return { present: true, value: undefined };
  }

  let value;
  try {
    value = JSON.parse(encoded);
  } catch {
    collector.add(
      "error",
      "E_JSON_INVALID",
      field,
      "The multipart field does not contain valid JSON.",
    );
    return { present: true, value: undefined };
  }

  const safe = scanUnsafeKeys(value, field, collector);
  return { present: true, value: safe ? value : undefined };
}

function expectRecord(value, path, code, collector, message) {
  if (!isRecord(value)) {
    collector.add("error", code, path, message);
    return false;
  }
  return true;
}

function validateOptionalString(value, path, collector) {
  if (value !== undefined && typeof value !== "string") {
    collector.add(
      "error",
      "E_STRING_REQUIRED",
      path,
      "The field must be a string when provided.",
    );
  }
}

function validateOptionalRecord(value, path, collector) {
  if (value !== undefined && !isRecord(value)) {
    collector.add(
      "error",
      "E_OBJECT_REQUIRED",
      path,
      "The field must be a JSON object when provided.",
    );
  }
}

function validateRelationsField(value, path, collector) {
  if (value === undefined) {
    return;
  }
  if (!expectRecord(
    value,
    path,
    "E_SOURCE_RELATIONS_SHAPE",
    collector,
    "Forceful relations must be an object.",
  )) {
    return;
  }
  if (!Array.isArray(value.ids)) {
    collector.add(
      "error",
      "E_SOURCE_RELATION_IDS_SHAPE",
      `${path}.ids`,
      "Forceful relation ids must be an array.",
    );
    return;
  }
  value.ids.forEach((id, index) => {
    if (typeof id !== "string") {
      collector.add(
        "error",
        "E_SOURCE_RELATION_ID_TYPE",
        `${path}.ids[${index}]`,
        "Each forceful relation id must be a string.",
      );
    }
  });
}

function validateAppRelations(value, path, collector) {
  if (value === undefined) {
    return;
  }

  if (isRecord(value)) {
    collector.add(
      "warning",
      "W_APP_RELATIONS_LEGACY",
      path,
      "The app source uses the legacy forceful-relation shortcut.",
    );
    validateRelationsField(value, path, collector);
    return;
  }

  if (!Array.isArray(value)) {
    collector.add(
      "error",
      "E_APP_RELATIONS_SHAPE",
      path,
      "App-native relations must be an array.",
    );
    return;
  }

  value.forEach((relation, index) => {
    const relationPath = `${path}[${index}]`;
    if (!isRecord(relation)) {
      collector.add(
        "error",
        "E_APP_RELATION_SHAPE",
        relationPath,
        "Each app-native relation must be an object.",
      );
      return;
    }

    if (
      typeof relation.predicate !== "string" ||
      relation.predicate.length === 0
    ) {
      collector.add(
        "error",
        "E_APP_RELATION_PREDICATE_REQUIRED",
        `${relationPath}.predicate`,
        "Each app-native relation requires a non-empty predicate string.",
      );
    }

    if (!isRecord(relation.target)) {
      collector.add(
        "error",
        "E_APP_RELATION_TARGET_REQUIRED",
        `${relationPath}.target`,
        "Each app-native relation requires a target object.",
      );
      return;
    }

    const { target } = relation;
    const hasId = typeof target.id === "string" && target.id.length > 0;
    const hasExternalId =
      typeof target.external_id === "string" &&
      target.external_id.length > 0;
    const hasProvider =
      typeof target.provider === "string" && target.provider.length > 0;

    if (target.id !== undefined && typeof target.id !== "string") {
      collector.add(
        "error",
        "E_APP_RELATION_TARGET_ID_TYPE",
        `${relationPath}.target.id`,
        "An app relation target id must be a string when provided.",
      );
    }
    if (
      target.external_id !== undefined &&
      typeof target.external_id !== "string"
    ) {
      collector.add(
        "error",
        "E_APP_RELATION_EXTERNAL_ID_TYPE",
        `${relationPath}.target.external_id`,
        "An app relation target external_id must be a string when provided.",
      );
    }
    if (target.provider !== undefined && typeof target.provider !== "string") {
      collector.add(
        "error",
        "E_APP_RELATION_PROVIDER_TYPE",
        `${relationPath}.target.provider`,
        "An app relation target provider must be a string when provided.",
      );
    }
    if (!hasId && !hasExternalId) {
      collector.add(
        "error",
        "E_APP_RELATION_TARGET_ID_REQUIRED",
        `${relationPath}.target`,
        "An app relation target requires an id or external_id.",
      );
    }
    if (hasExternalId && !hasProvider) {
      collector.add(
        "error",
        "E_APP_RELATION_PROVIDER_REQUIRED",
        `${relationPath}.target.provider`,
        "An external_id relation target also requires a provider.",
      );
    }

    validateOptionalRecord(
      relation.properties,
      `${relationPath}.properties`,
      collector,
    );
  });
}

function validateId(value, path, collector, { required = false } = {}) {
  if (value === undefined) {
    if (required) {
      collector.add(
        "error",
        "E_ID_REQUIRED",
        path,
        "This source item requires an id field.",
      );
    }
    return undefined;
  }

  if (typeof value !== "string") {
    collector.add(
      "error",
      "E_ID_TYPE",
      path,
      "A source id must be a string.",
    );
    return undefined;
  }

  if (value.includes(",")) {
    collector.add(
      "error",
      "E_ID_COMMA",
      path,
      "A source id cannot contain a comma.",
    );
  }

  return value;
}

function validateDocumentMetadata(value, collector, sourceRecords) {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    collector.add(
      "error",
      "E_DOCUMENT_METADATA_SHAPE",
      "document_metadata",
      "document_metadata must encode a JSON array.",
    );
    return [];
  }

  value.forEach((item, index) => {
    const path = `document_metadata[${index}]`;
    if (!expectRecord(
      item,
      path,
      "E_DOCUMENT_METADATA_ITEM_SHAPE",
      collector,
      "Each document_metadata item must be an object.",
    )) {
      return;
    }

    const id = validateId(item.id, `${path}.id`, collector);
    if (typeof id === "string" && id.length > 0) {
      sourceRecords.push({ id, path: `${path}.id` });
    }

    validateOptionalRecord(item.metadata, `${path}.metadata`, collector);
    validateOptionalRecord(
      item.additional_metadata,
      `${path}.additional_metadata`,
      collector,
    );
    validateOptionalString(item.title, `${path}.title`, collector);
    validateOptionalString(item.type, `${path}.type`, collector);
    validateOptionalString(item.url, `${path}.url`, collector);
    validateOptionalString(item.timestamp, `${path}.timestamp`, collector);
    validateRelationsField(item.relations, `${path}.relations`, collector);
  });

  return value;
}

function validateAppKnowledge(
  value,
  collector,
  sourceRecords,
  database,
  collection,
) {
  if (value === undefined) {
    return [];
  }

  const items = Array.isArray(value) ? value : isRecord(value) ? [value] : null;
  if (items === null) {
    collector.add(
      "error",
      "E_APP_KNOWLEDGE_SHAPE",
      "app_knowledge",
      "app_knowledge must encode an object or an array of objects.",
    );
    return [];
  }

  items.forEach((item, index) => {
    const path = `app_knowledge[${index}]`;
    if (!expectRecord(
      item,
      path,
      "E_APP_KNOWLEDGE_ITEM_SHAPE",
      collector,
      "Each app_knowledge item must be an object.",
    )) {
      return;
    }

    const id = validateId(item.id, `${path}.id`, collector, { required: true });
    if (typeof id === "string" && id.length > 0) {
      sourceRecords.push({ id, path: `${path}.id` });
    } else if (id === "") {
      collector.add(
        "warning",
        "W_APP_ID_GENERATED",
        `${path}.id`,
        "An empty app source id cannot be used as a same-request graph key.",
      );
    }

    if (item.database === undefined) {
      collector.add(
        "warning",
        "W_APP_DATABASE_OMITTED",
        `${path}.database`,
        "The app source omits its item-level database scope.",
      );
    } else if (typeof item.database !== "string") {
      collector.add(
        "error",
        "E_APP_DATABASE_TYPE",
        `${path}.database`,
        "The app source database must be a string when provided.",
      );
    } else if (typeof database === "string" && item.database !== database) {
      collector.add(
        "error",
        "E_APP_DATABASE_MISMATCH",
        `${path}.database`,
        "The app source database must match the form-level database.",
      );
    }

    if (item.collection === undefined) {
      collector.add(
        "warning",
        "W_APP_COLLECTION_OMITTED",
        `${path}.collection`,
        "The app source omits its item-level collection scope.",
      );
    } else if (typeof item.collection !== "string") {
      collector.add(
        "error",
        "E_APP_COLLECTION_TYPE",
        `${path}.collection`,
        "The app source collection must be a string when provided.",
      );
    } else if (typeof collection === "string" && item.collection !== collection) {
      collector.add(
        "error",
        "E_APP_COLLECTION_MISMATCH",
        `${path}.collection`,
        "The app source collection must match the form-level collection.",
      );
    }

    const hasLegacyContent = isRecord(item.content);
    const hasModernFields = isRecord(item.fields);
    if (!hasLegacyContent && !hasModernFields) {
      collector.add(
        "error",
        "E_APP_PAYLOAD_REQUIRED",
        path,
        "Each app source requires a fields object or a legacy content object.",
      );
    }
    if (item.content !== undefined && !hasLegacyContent) {
      collector.add(
        "error",
        "E_APP_CONTENT_SHAPE",
        `${path}.content`,
        "Legacy app source content must be an object when provided.",
      );
    }
    if (item.fields !== undefined && !hasModernFields) {
      collector.add(
        "error",
        "E_APP_FIELDS_SHAPE",
        `${path}.fields`,
        "Modern app source fields must be an object when provided.",
      );
    }
    if (hasLegacyContent) {
      collector.add(
        "warning",
        "W_APP_LEGACY_CONTENT",
        `${path}.content`,
        "The app source uses the legacy generic content model.",
      );
    }

    if (hasModernFields) {
      if (item.kind === undefined) {
        collector.add(
          "warning",
          "W_APP_KIND_OMITTED",
          `${path}.kind`,
          "The app source omits its recommended top-level kind.",
        );
      } else if (typeof item.kind !== "string" || item.kind.length === 0) {
        collector.add(
          "error",
          "E_APP_KIND_TYPE",
          `${path}.kind`,
          "The app source kind must be a non-empty string when provided.",
        );
      }

      if (item.provider === undefined) {
        collector.add(
          "warning",
          "W_APP_PROVIDER_OMITTED",
          `${path}.provider`,
          "The app source omits its recommended provider namespace.",
        );
      } else if (
        typeof item.provider !== "string" ||
        item.provider.length === 0
      ) {
        collector.add(
          "error",
          "E_APP_PROVIDER_TYPE",
          `${path}.provider`,
          "The app source provider must be a non-empty string when provided.",
        );
      }

      if (item.external_id === undefined) {
        collector.add(
          "warning",
          "W_APP_EXTERNAL_ID_OMITTED",
          `${path}.external_id`,
          "The app source omits its recommended provider-scoped external_id.",
        );
      } else if (
        typeof item.external_id !== "string" ||
        item.external_id.length === 0
      ) {
        collector.add(
          "error",
          "E_APP_EXTERNAL_ID_TYPE",
          `${path}.external_id`,
          "The app source external_id must be a non-empty string when provided.",
        );
      }

      const topLevelKindIsValid =
        typeof item.kind === "string" && item.kind.length > 0;
      const fieldsKindIsValid =
        typeof item.fields.kind === "string" &&
        item.fields.kind.length > 0;

      if (item.fields.kind === undefined) {
        collector.add(
          "warning",
          "W_APP_FIELDS_KIND_OMITTED",
          `${path}.fields.kind`,
          "The app source fields object omits its kind discriminator.",
        );
      } else if (!fieldsKindIsValid) {
        collector.add(
          "error",
          "E_APP_FIELDS_KIND_TYPE",
          `${path}.fields.kind`,
          "The app source fields kind must be a non-empty string when provided.",
        );
      } else if (topLevelKindIsValid && item.fields.kind !== item.kind) {
        collector.add(
          "error",
          "E_APP_KIND_CONFLICT",
          `${path}.fields.kind`,
          "The fields kind must match the top-level app source kind.",
        );
      }

      const effectiveKind = fieldsKindIsValid
        ? item.fields.kind
        : topLevelKindIsValid
          ? item.kind
          : undefined;
      if (effectiveKind !== undefined && !APP_KINDS.has(effectiveKind)) {
        collector.add(
          "error",
          "E_APP_KIND_UNSUPPORTED",
          fieldsKindIsValid ? `${path}.fields.kind` : `${path}.kind`,
          "The app source kind is unsupported; use a documented kind or custom.",
        );
      }
    }

    validateOptionalRecord(item.metadata, `${path}.metadata`, collector);
    validateOptionalRecord(
      item.additional_metadata,
      `${path}.additional_metadata`,
      collector,
    );
    validateOptionalString(item.title, `${path}.title`, collector);
    validateOptionalString(item.type, `${path}.type`, collector);
    validateOptionalString(item.description, `${path}.description`, collector);
    validateOptionalString(item.url, `${path}.url`, collector);
    validateOptionalString(item.timestamp, `${path}.timestamp`, collector);
    if (item.attachments !== undefined && !Array.isArray(item.attachments)) {
      collector.add(
        "error",
        "E_APP_ATTACHMENTS_SHAPE",
        `${path}.attachments`,
        "App source attachments must be an array when provided.",
      );
    }
    validateAppRelations(item.relations, `${path}.relations`, collector);
  });

  return items;
}

function validateMemoryMetadata(value, path, collector) {
  if (value === undefined || value === "") {
    return;
  }
  if (typeof value !== "string") {
    collector.add(
      "error",
      "E_MEMORY_METADATA_STRING_REQUIRED",
      path,
      "Memory metadata must be a JSON-encoded object string.",
    );
    return;
  }

  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    collector.add(
      "error",
      "E_MEMORY_METADATA_JSON_INVALID",
      path,
      "Memory metadata does not contain valid JSON.",
    );
    return;
  }

  scanUnsafeKeys(parsed, path, collector);
  if (!isRecord(parsed)) {
    collector.add(
      "error",
      "E_MEMORY_METADATA_SHAPE",
      path,
      "Memory metadata must encode a JSON object.",
    );
  }
}

function validateMemoryAdditionalMetadata(value, path, collector) {
  if (value === undefined || value === "") {
    return;
  }
  if (isRecord(value)) {
    scanUnsafeKeys(value, path, collector);
    return;
  }
  if (typeof value !== "string") {
    collector.add(
      "error",
      "E_MEMORY_ADDITIONAL_METADATA_SHAPE",
      path,
      "Memory additional_metadata must be an object or a JSON-encoded object string.",
    );
    return;
  }

  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    collector.add(
      "error",
      "E_MEMORY_ADDITIONAL_METADATA_JSON_INVALID",
      path,
      "Memory additional_metadata does not contain valid JSON.",
    );
    return;
  }
  scanUnsafeKeys(parsed, path, collector);
  if (!isRecord(parsed)) {
    collector.add(
      "error",
      "E_MEMORY_ADDITIONAL_METADATA_SHAPE",
      path,
      "Memory additional_metadata must encode a JSON object.",
    );
  } else {
    collector.add(
      "warning",
      "W_MEMORY_ADDITIONAL_METADATA_ENCODED",
      path,
      "The encoded additional_metadata compatibility form is accepted; an object is canonical.",
    );
  }
}

function validateMemories(value, collector, sourceRecords) {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    collector.add(
      "error",
      "E_MEMORIES_SHAPE",
      "memories",
      "memories must encode a JSON array.",
    );
    return [];
  }

  value.forEach((item, index) => {
    const path = `memories[${index}]`;
    if (!expectRecord(
      item,
      path,
      "E_MEMORY_ITEM_SHAPE",
      collector,
      "Each memory item must be an object.",
    )) {
      return;
    }

    const id = validateId(item.id, `${path}.id`, collector);
    if (typeof id === "string" && id.length > 0) {
      sourceRecords.push({ id, path: `${path}.id` });
    }

    const hasText = typeof item.text === "string" && item.text.length > 0;
    const hasPairs =
      Array.isArray(item.user_assistant_pairs) &&
      item.user_assistant_pairs.length > 0;
    if (!hasText && !hasPairs) {
      collector.add(
        "error",
        "E_MEMORY_CONTENT_REQUIRED",
        path,
        "A memory requires non-empty text or user_assistant_pairs.",
      );
    }
    if (hasText && hasPairs) {
      collector.add(
        "warning",
        "W_MEMORY_MULTIPLE_CONTENT",
        path,
        "The memory supplies both supported content representations.",
      );
    }

    if (item.text !== undefined && typeof item.text !== "string") {
      collector.add(
        "error",
        "E_MEMORY_TEXT_TYPE",
        `${path}.text`,
        "Memory text must be a string when provided.",
      );
    }

    if (
      item.user_assistant_pairs !== undefined &&
      !Array.isArray(item.user_assistant_pairs)
    ) {
      collector.add(
        "error",
        "E_MEMORY_PAIRS_SHAPE",
        `${path}.user_assistant_pairs`,
        "user_assistant_pairs must be an array.",
      );
    } else if (Array.isArray(item.user_assistant_pairs)) {
      item.user_assistant_pairs.forEach((pair, pairIndex) => {
        const pairPath = `${path}.user_assistant_pairs[${pairIndex}]`;
        if (!isRecord(pair)) {
          collector.add(
            "error",
            "E_MEMORY_PAIR_SHAPE",
            pairPath,
            "Each conversation pair must be an object.",
          );
          return;
        }
        if (
          typeof pair.user !== "string" ||
          typeof pair.assistant !== "string"
        ) {
          collector.add(
            "error",
            "E_MEMORY_PAIR_CONTENT",
            pairPath,
            "Each conversation pair requires string user and assistant fields.",
          );
        }
      });
    }

    if (item.infer !== undefined && typeof item.infer !== "boolean") {
      collector.add(
        "error",
        "E_MEMORY_INFER_TYPE",
        `${path}.infer`,
        "Memory infer must be a boolean when provided.",
      );
    }
    if (
      item.is_markdown !== undefined &&
      typeof item.is_markdown !== "boolean"
    ) {
      collector.add(
        "error",
        "E_MEMORY_MARKDOWN_TYPE",
        `${path}.is_markdown`,
        "Memory is_markdown must be a boolean when provided.",
      );
    } else if (item.is_markdown === true && !hasText) {
      collector.add(
        "warning",
        "W_MEMORY_MARKDOWN_WITHOUT_TEXT",
        `${path}.is_markdown`,
        "is_markdown has no text field to describe.",
      );
    }
    if (
      item.expiry_time !== undefined &&
      !Number.isInteger(item.expiry_time)
    ) {
      collector.add(
        "error",
        "E_MEMORY_EXPIRY_TYPE",
        `${path}.expiry_time`,
        "Memory expiry_time must be an integer when provided.",
      );
    }

    validateOptionalString(item.title, `${path}.title`, collector);
    validateOptionalString(
      item.custom_instructions,
      `${path}.custom_instructions`,
      collector,
    );
    if (
      typeof item.custom_instructions === "string" &&
      item.custom_instructions.length > 0 &&
      (item.infer === undefined || item.infer === false)
    ) {
      collector.add(
        "warning",
        "W_MEMORY_INSTRUCTIONS_IGNORED",
        `${path}.custom_instructions`,
        "custom_instructions is ignored when infer is false.",
      );
    }
    validateOptionalString(item.user_name, `${path}.user_name`, collector);
    validateMemoryMetadata(item.metadata, `${path}.metadata`, collector);
    validateMemoryAdditionalMetadata(
      item.additional_metadata,
      `${path}.additional_metadata`,
      collector,
    );
    validateRelationsField(item.relations, `${path}.relations`, collector);
  });

  return value;
}

function checkDuplicateSourceIds(sourceRecords, collector) {
  const firstPathById = new Map();
  for (const source of sourceRecords) {
    if (firstPathById.has(source.id)) {
      collector.add(
        "error",
        "E_ID_DUPLICATE",
        source.path,
        "An explicit source id appears more than once in this request.",
      );
    } else {
      firstPathById.set(source.id, source.path);
    }
  }
}

function validateGraphPayload(
  value,
  collector,
  sourceRecords,
  graphSummary,
) {
  if (value === undefined) {
    return;
  }
  if (!expectRecord(
    value,
    "graph_payload",
    "E_GRAPH_PAYLOAD_SHAPE",
    collector,
    "graph_payload must encode a map keyed by source id.",
  )) {
    return;
  }

  const sourceIds = new Set(sourceRecords.map(({ id }) => id));
  const graphs = sortedEntries(value);
  graphSummary.graphs = graphs.length;

  graphs.forEach(([sourceId, graph], graphIndex) => {
    const path = "graph_payload.*";
    if (!sourceIds.has(sourceId)) {
      collector.add(
        "error",
        "E_GRAPH_UNKNOWN_SOURCE",
        path,
        "A graph key does not match an explicit source id in this request.",
      );
    }

    if (!expectRecord(
      graph,
      path,
      "E_GRAPH_SHAPE",
      collector,
      "Each graph_payload entry must be an object.",
    )) {
      return;
    }

    const entities = graph.entities;
    const relations = graph.relations;
    const validEntities = isRecord(entities);
    const validRelations = Array.isArray(relations);

    if (!validEntities) {
      collector.add(
        "error",
        "E_GRAPH_ENTITIES_SHAPE",
        `${path}.entities`,
        "Graph entities must be an object map.",
      );
    }
    if (!validRelations) {
      collector.add(
        "error",
        "E_GRAPH_RELATIONS_SHAPE",
        `${path}.relations`,
        "Graph relations must be an array.",
      );
    }
    if (!validEntities || !validRelations) {
      return;
    }

    const entityEntries = sortedEntries(entities);
    graphSummary.entities += entityEntries.length;
    graphSummary.relations += relations.length;

    if (entityEntries.length > LIMITS.entitiesPerGraph) {
      collector.add(
        "error",
        "E_GRAPH_ENTITY_LIMIT",
        `${path}.entities`,
        "A graph contains more than 5,000 entities.",
      );
    }
    if (relations.length > LIMITS.relationsPerGraph) {
      collector.add(
        "error",
        "E_GRAPH_RELATION_LIMIT",
        `${path}.relations`,
        "A graph contains more than 10,000 relations.",
      );
    }

    const entityKeys = new Set(entityEntries.map(([key]) => key));
    const entityIndexByKey = new Map(
      entityEntries.map(([key], index) => [key, index]),
    );
    const normalizedNames = new Map();
    const referencedEntities = new Set();
    const degree = new Map(entityEntries.map(([key]) => [key, 0]));

    entityEntries.forEach(([entityKey, entity], entityIndex) => {
      const entityPath = `${path}.entities.*`;
      if (entityKey.length === 0) {
        collector.add(
          "error",
          "E_GRAPH_ENTITY_KEY_EMPTY",
          entityPath,
          "Entity map keys must not be empty.",
        );
      }
      if (!isRecord(entity)) {
        collector.add(
          "error",
          "E_GRAPH_ENTITY_SHAPE",
          entityPath,
          "Each graph entity must be an object.",
        );
        return;
      }

      if (typeof entity.name !== "string" || entity.name.length === 0) {
        collector.add(
          "error",
          "E_GRAPH_ENTITY_NAME_REQUIRED",
          `${entityPath}.name`,
          "Each graph entity requires a non-empty name string.",
        );
      } else {
        if (characterLength(entity.name) > LIMITS.entityNameCharacters) {
          collector.add(
            "error",
            "E_GRAPH_ENTITY_NAME_LIMIT",
            `${entityPath}.name`,
            "An entity name exceeds 256 characters.",
          );
        }

        const normalizedName = entity.name.toLowerCase();
        if (normalizedNames.has(normalizedName)) {
          collector.add(
            "warning",
            "W_GRAPH_NORMALIZED_NAME_COLLISION",
            `${entityPath}.name`,
            "Multiple entity names become identical after lowercase normalization.",
          );
        } else {
          normalizedNames.set(normalizedName, entityKey);
        }
      }

      validateOptionalString(entity.type, `${entityPath}.type`, collector);
      validateOptionalString(
        entity.namespace,
        `${entityPath}.namespace`,
        collector,
      );
      validateOptionalString(
        entity.identifier,
        `${entityPath}.identifier`,
        collector,
      );
    });

    const relationFingerprints = new Set();
    relations.forEach((relation, relationIndex) => {
      const relationPath = `${path}.relations[${relationIndex}]`;
      if (!isRecord(relation)) {
        collector.add(
          "error",
          "E_GRAPH_RELATION_SHAPE",
          relationPath,
          "Each graph relation must be an object.",
        );
        return;
      }

      const sourceIsString =
        typeof relation.source === "string" && relation.source.length > 0;
      const targetIsString =
        typeof relation.target === "string" && relation.target.length > 0;
      const predicateIsString =
        typeof relation.predicate === "string" &&
        relation.predicate.length > 0;

      if (!sourceIsString) {
        collector.add(
          "error",
          "E_GRAPH_RELATION_SOURCE_REQUIRED",
          `${relationPath}.source`,
          "Each graph relation requires a non-empty source string.",
        );
      } else if (!entityKeys.has(relation.source)) {
        collector.add(
          "error",
          "E_GRAPH_SOURCE_UNKNOWN_ENTITY",
          `${relationPath}.source`,
          "The relation source does not reference an entity-map key.",
        );
      } else {
        referencedEntities.add(relation.source);
      }

      if (!targetIsString) {
        collector.add(
          "error",
          "E_GRAPH_RELATION_TARGET_REQUIRED",
          `${relationPath}.target`,
          "Each graph relation requires a non-empty target string.",
        );
      } else if (!entityKeys.has(relation.target)) {
        collector.add(
          "error",
          "E_GRAPH_TARGET_UNKNOWN_ENTITY",
          `${relationPath}.target`,
          "The relation target does not reference an entity-map key.",
        );
      } else {
        referencedEntities.add(relation.target);
      }

      if (!predicateIsString) {
        collector.add(
          "error",
          "E_GRAPH_RELATION_PREDICATE_REQUIRED",
          `${relationPath}.predicate`,
          "Each graph relation requires a non-empty predicate string.",
        );
      } else if (
        characterLength(relation.predicate) > LIMITS.predicateCharacters
      ) {
        collector.add(
          "error",
          "E_GRAPH_PREDICATE_LIMIT",
          `${relationPath}.predicate`,
          "A relation predicate exceeds 256 characters.",
        );
      }

      if (
        relation.context !== undefined &&
        typeof relation.context !== "string"
      ) {
        collector.add(
          "error",
          "E_GRAPH_CONTEXT_TYPE",
          `${relationPath}.context`,
          "Relation context must be a string when provided.",
        );
      } else if (
        typeof relation.context === "string" &&
        characterLength(relation.context) >
          LIMITS.relationContextCharacters
      ) {
        collector.add(
          "error",
          "E_GRAPH_CONTEXT_LIMIT",
          `${relationPath}.context`,
          "Relation context exceeds 2,000 characters.",
        );
      }

      validateOptionalString(
        relation.temporal_details,
        `${relationPath}.temporal_details`,
        collector,
      );

      if (
        sourceIsString &&
        targetIsString &&
        entityKeys.has(relation.source) &&
        entityKeys.has(relation.target)
      ) {
        degree.set(relation.source, degree.get(relation.source) + 1);
        if (relation.target !== relation.source) {
          degree.set(relation.target, degree.get(relation.target) + 1);
        }
      }

      if (sourceIsString && targetIsString && predicateIsString) {
        const fingerprint = JSON.stringify([
          relation.source,
          relation.target,
          relation.predicate,
        ]);
        if (relationFingerprints.has(fingerprint)) {
          collector.add(
            "warning",
            "W_GRAPH_DUPLICATE_RELATION",
            relationPath,
            "The graph contains a repeated source-target-predicate relation.",
          );
        } else {
          relationFingerprints.add(fingerprint);
        }
      }
    });

    entityEntries.forEach(([key]) => {
      if (!referencedEntities.has(key)) {
        const index = entityIndexByKey.get(key);
        collector.add(
          "warning",
          "W_GRAPH_ORPHAN_ENTITY",
          `${path}.entities.*`,
          "An entity is not referenced by any relation and may be dropped.",
        );
      }
      if ((degree.get(key) ?? 0) > LIMITS.relationsPerEntity) {
        const index = entityIndexByKey.get(key);
        collector.add(
          "error",
          "E_GRAPH_DEGREE_LIMIT",
          `${path}.entities.*`,
          "An entity participates in more than 500 relations.",
        );
      }
    });
  });
}

function validateDocuments(value, collector) {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    collector.add(
      "error",
      "E_DOCUMENTS_SHAPE",
      "documents",
      "The local documents descriptor must be an array.",
    );
    return [];
  }

  value.forEach((document, index) => {
    if (
      !(typeof document === "string" && document.length > 0) &&
      !(
        isRecord(document) &&
        typeof document.filename === "string" &&
        document.filename.length > 0
      )
    ) {
      collector.add(
        "error",
        "E_DOCUMENT_DESCRIPTOR",
        `documents[${index}]`,
        "Each local document descriptor must be a non-empty filename string or an object with filename.",
      );
    }
  });
  return value;
}

/**
 * Validate a local JSON representation of a HydraDB multipart ingest request.
 *
 * The opaque multipart fields (document_metadata, app_knowledge, memories,
 * and graph_payload) must remain JSON strings in the manifest. `documents` is
 * represented locally as an array of filenames or `{ filename }` descriptors.
 */
export function validateIngestManifest(manifest) {
  const collector = createCollector();
  const graphSummary = { graphs: 0, entities: 0, relations: 0 };

  if (!isRecord(manifest)) {
    collector.add(
      "error",
      "E_MANIFEST_SHAPE",
      "$",
      "The ingest manifest must be a JSON object.",
    );
    const finished = collector.finish();
    return {
      status: "fail",
      summary: {
        sources: 0,
        graphs: 0,
        entities: 0,
        relations: 0,
        errors: finished.errors,
        warnings: finished.warnings,
        diagnosticsShown: finished.diagnosticsShown,
        diagnosticsTruncated: finished.diagnosticsTruncated,
      },
      diagnostics: finished.diagnostics,
    };
  }

  const manifestStructureSafe = scanUnsafeKeys(manifest, "$", collector);
  if (!manifestStructureSafe) {
    const finished = collector.finish();
    return {
      status: "fail",
      summary: {
        sources: 0,
        graphs: 0,
        entities: 0,
        relations: 0,
        errors: finished.errors,
        warnings: finished.warnings,
        diagnosticsShown: finished.diagnosticsShown,
        diagnosticsTruncated: finished.diagnosticsTruncated,
      },
      diagnostics: finished.diagnostics,
    };
  }

  const type = manifest.type ?? "knowledge";
  if (type !== "knowledge" && type !== "memory") {
    collector.add(
      "error",
      "E_TYPE",
      "type",
      'The type must be "knowledge" or "memory".',
    );
  }

  const database = manifest.database ?? manifest.tenant_id;
  if (typeof database !== "string" || database.length === 0) {
    collector.add(
      "error",
      "E_DATABASE_REQUIRED",
      "database",
      "A non-empty database string is required.",
    );
  }
  if (manifest.database === undefined && manifest.tenant_id !== undefined) {
    collector.add(
      "warning",
      "W_DATABASE_ALIAS",
      "tenant_id",
      "tenant_id is accepted as a deprecated alias for database.",
    );
  }
  if (
    typeof manifest.database === "string" &&
    typeof manifest.tenant_id === "string" &&
    manifest.database !== manifest.tenant_id
  ) {
    collector.add(
      "error",
      "E_DATABASE_ALIAS_CONFLICT",
      "tenant_id",
      "database and tenant_id must match when both are provided.",
    );
  }

  const collection = manifest.collection ?? manifest.sub_tenant_id ?? "";
  if (typeof collection !== "string") {
    collector.add(
      "error",
      "E_COLLECTION_TYPE",
      "collection",
      "The collection must be a string when provided.",
    );
  }
  if (
    manifest.collection === undefined &&
    manifest.sub_tenant_id !== undefined
  ) {
    collector.add(
      "warning",
      "W_COLLECTION_ALIAS",
      "sub_tenant_id",
      "sub_tenant_id is accepted as a deprecated alias for collection.",
    );
  }
  if (
    typeof manifest.collection === "string" &&
    typeof manifest.sub_tenant_id === "string" &&
    manifest.collection !== manifest.sub_tenant_id
  ) {
    collector.add(
      "error",
      "E_COLLECTION_ALIAS_CONFLICT",
      "sub_tenant_id",
      "collection and sub_tenant_id must match when both are provided.",
    );
  }

  if (
    manifest.upsert !== undefined &&
    !(
      typeof manifest.upsert === "boolean" ||
      (typeof manifest.upsert === "string" &&
        ["true", "false", "1", "0"].includes(manifest.upsert))
    )
  ) {
    collector.add(
      "error",
      "E_UPSERT_TYPE",
      "upsert",
      "The upsert field must be a boolean or true/false/1/0 string.",
    );
  }

  const documents = validateDocuments(manifest.documents, collector);
  const documentMetadataJson = parseOpaqueJson(
    manifest,
    "document_metadata",
    collector,
  );
  const appKnowledgeJson = parseOpaqueJson(
    manifest,
    "app_knowledge",
    collector,
  );
  const memoriesJson = parseOpaqueJson(manifest, "memories", collector);
  const graphPayloadJson = parseOpaqueJson(
    manifest,
    "graph_payload",
    collector,
  );

  const sourceRecords = [];
  const documentMetadata = validateDocumentMetadata(
    documentMetadataJson.value,
    collector,
    sourceRecords,
  );
  const appKnowledge = validateAppKnowledge(
    appKnowledgeJson.value,
    collector,
    sourceRecords,
    database,
    collection,
  );
  const memories = validateMemories(
    memoriesJson.value,
    collector,
    sourceRecords,
  );

  if (
    documentMetadataJson.value !== undefined &&
    documents.length !== documentMetadata.length
  ) {
    collector.add(
      "error",
      "E_DOCUMENT_METADATA_COUNT",
      "document_metadata",
      "document_metadata must contain one item for each local document descriptor.",
    );
  }

  if (type === "knowledge") {
    if (memoriesJson.present) {
      collector.add(
        "error",
        "E_KNOWLEDGE_MEMORIES_CONFLICT",
        "memories",
        "A knowledge request cannot include memories.",
      );
    }
    if (documents.length === 0 && appKnowledge.length === 0) {
      collector.add(
        "error",
        "E_KNOWLEDGE_SOURCE_REQUIRED",
        "$",
        "A knowledge request requires documents or app_knowledge.",
      );
    }
  }

  if (type === "memory") {
    if (manifest.documents !== undefined) {
      collector.add(
        "error",
        "E_MEMORY_DOCUMENTS_CONFLICT",
        "documents",
        "A memory request cannot include documents.",
      );
    }
    if (documentMetadataJson.present) {
      collector.add(
        "error",
        "E_MEMORY_DOCUMENT_METADATA_CONFLICT",
        "document_metadata",
        "A memory request cannot include document_metadata.",
      );
    }
    if (appKnowledgeJson.present) {
      collector.add(
        "error",
        "E_MEMORY_APP_KNOWLEDGE_CONFLICT",
        "app_knowledge",
        "A memory request cannot include app_knowledge.",
      );
    }
    if (!memoriesJson.present || memories.length === 0) {
      collector.add(
        "error",
        "E_MEMORIES_REQUIRED",
        "memories",
        "A memory request requires a non-empty memories array.",
      );
    }
  }

  checkDuplicateSourceIds(sourceRecords, collector);
  validateGraphPayload(
    graphPayloadJson.value,
    collector,
    sourceRecords,
    graphSummary,
  );

  const sourceCount =
    type === "memory"
      ? memories.length
      : documents.length + appKnowledge.length;
  const finished = collector.finish();
  return {
    status: finished.errors === 0 ? "pass" : "fail",
    summary: {
      sources: sourceCount,
      graphs: graphSummary.graphs,
      entities: graphSummary.entities,
      relations: graphSummary.relations,
      errors: finished.errors,
      warnings: finished.warnings,
      diagnosticsShown: finished.diagnosticsShown,
      diagnosticsTruncated: finished.diagnosticsTruncated,
    },
    diagnostics: finished.diagnostics,
  };
}

export function formatHuman(result) {
  const { summary } = result;
  const countLabel = (count, singular, plural = `${singular}s`) =>
    `${count} ${count === 1 ? singular : plural}`;
  const lines = [
    result.status === "pass"
      ? "PASS — ingest manifest is internally consistent"
      : "FAIL — ingest manifest has validation errors",
    [
      countLabel(summary.sources, "source"),
      countLabel(summary.graphs, "graph"),
      countLabel(summary.entities, "entity", "entities"),
      countLabel(summary.relations, "relation"),
      countLabel(summary.errors, "error"),
      countLabel(summary.warnings, "warning"),
    ].join(" · "),
  ];

  for (const diagnostic of result.diagnostics) {
    const label = diagnostic.severity === "error" ? "ERROR" : "WARN";
    lines.push(
      `${label} ${diagnostic.code} ${diagnostic.path}`,
      `  ${diagnostic.message}`,
    );
  }

  if (summary.diagnosticsTruncated) {
    lines.push(
      `… ${summary.errors + summary.warnings - summary.diagnosticsShown} additional diagnostics omitted`,
    );
  }

  return lines.join("\n");
}

function usage() {
  return [
    "Usage: node examples/ingest-doctor/validate.mjs [--json] <manifest.json>",
    "",
    "Exit codes: 0 = valid, 1 = validation errors, 2 = CLI or file error",
  ].join("\n");
}

export async function runCli(
  args,
  {
    stdout = (text) => process.stdout.write(text),
    stderr = (text) => process.stderr.write(text),
  } = {},
) {
  let jsonOutput = false;
  let filePath;

  for (const argument of args) {
    if (argument === "--json") {
      jsonOutput = true;
    } else if (argument === "--help" || argument === "-h") {
      stdout(`${usage()}\n`);
      return 0;
    } else if (argument.startsWith("-")) {
      stderr(`Unknown option.\n${usage()}\n`);
      return 2;
    } else if (filePath === undefined) {
      filePath = argument;
    } else {
      stderr(`Expected one manifest file.\n${usage()}\n`);
      return 2;
    }
  }

  if (filePath === undefined) {
    stderr(`${usage()}\n`);
    return 2;
  }

  let encoded;
  try {
    const resolvedPath = resolve(filePath);
    const fileInfo = await stat(resolvedPath);
    if (fileInfo.size > MAX_MANIFEST_BYTES) {
      stderr(
        "The ingest manifest file exceeds Ingest Doctor's 128 MiB local safety limit.\n",
      );
      return 2;
    }
    encoded = await readFile(resolvedPath, "utf8");
  } catch {
    stderr("Could not read the ingest manifest file.\n");
    return 2;
  }

  let manifest;
  try {
    manifest = JSON.parse(encoded);
  } catch {
    stderr("The ingest manifest file is not valid JSON.\n");
    return 2;
  }

  const result = validateIngestManifest(manifest);
  stdout(
    jsonOutput
      ? `${JSON.stringify(result, null, 2)}\n`
      : `${formatHuman(result)}\n`,
  );
  return result.status === "pass" ? 0 : 1;
}

const isMain =
  process.argv[1] !== undefined &&
  resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (isMain) {
  process.exitCode = await runCli(process.argv.slice(2));
}
