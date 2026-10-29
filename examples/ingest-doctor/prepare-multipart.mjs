import { posix } from "node:path";
import { validateIngestManifest } from "./validate.mjs";

export const MULTIPART_FIELD_ORDER = Object.freeze([
  "type",
  "database",
  "collection",
  "upsert",
  "documents",
  "document_metadata",
  "app_knowledge",
  "memories",
  "graph_payload",
]);

const SUPPORTED_MANIFEST_FIELDS = new Set([
  ...MULTIPART_FIELD_ORDER,
  "tenant_id",
  "sub_tenant_id",
]);

function preparationDiagnostic(code, path, message) {
  return { severity: "error", code, path, message };
}

function blocked(validation, diagnostic) {
  return {
    status: "blocked",
    validation,
    preparationDiagnostics: diagnostic === undefined ? [] : [diagnostic],
    formData: null,
    partSummary: {
      fieldNames: [],
      documentCount: 0,
      partCount: 0,
    },
  };
}

function snapshotSupportedFields(manifest) {
  const snapshot = {
    type: manifest.type ?? "knowledge",
    database: manifest.database ?? manifest.tenant_id,
  };

  if (
    Object.hasOwn(manifest, "collection") ||
    Object.hasOwn(manifest, "sub_tenant_id")
  ) {
    snapshot.collection = manifest.collection ?? manifest.sub_tenant_id;
  }
  if (Object.hasOwn(manifest, "upsert")) {
    snapshot.upsert =
      typeof manifest.upsert === "boolean"
        ? String(manifest.upsert)
        : manifest.upsert;
  }
  if (Object.hasOwn(manifest, "documents")) {
    snapshot.documents = Object.freeze(
      manifest.documents.map((document) =>
        typeof document === "string" ? document : document.filename,
      ),
    );
  }
  for (const field of [
    "document_metadata",
    "app_knowledge",
    "memories",
    "graph_payload",
  ]) {
    if (Object.hasOwn(manifest, field)) {
      snapshot[field] = manifest[field];
    }
  }

  return Object.freeze(snapshot);
}

function isWellFormedUnicode(value) {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        return false;
      }
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function uploadFilename(filename) {
  if (!isWellFormedUnicode(filename) || /[\0\r\n]/u.test(filename)) {
    return undefined;
  }

  const name = posix.basename(filename.replaceAll("\\", "/"));
  return name === "" || name === "." || name === ".." ? undefined : name;
}

/**
 * Convert a validated local ingest manifest into in-memory multipart FormData.
 *
 * This module contains no file-loading, credential, or transport
 * implementation. Applications provide document bytes through
 * `loadDocument`, which is application code and may perform its own I/O.
 */
export async function prepareValidatedMultipart(
  manifest,
  { loadDocument } = {},
) {
  const validation = validateIngestManifest(manifest);
  if (validation.status !== "pass") {
    return blocked(validation);
  }

  const unknownFieldPresent = Object.keys(manifest).some(
    (field) => !SUPPORTED_MANIFEST_FIELDS.has(field),
  );
  if (unknownFieldPresent) {
    return blocked(
      validation,
      preparationDiagnostic(
        "E_MULTIPART_FIELD_UNSUPPORTED",
        "$.*",
        "The manifest contains a top-level field that this multipart helper does not support.",
      ),
    );
  }

  if (
    typeof globalThis.FormData !== "function" ||
    typeof globalThis.Blob !== "function"
  ) {
    return blocked(
      validation,
      preparationDiagnostic(
        "E_MULTIPART_RUNTIME",
        "$",
        "Multipart preparation requires built-in FormData and Blob support.",
      ),
    );
  }

  const snapshot = snapshotSupportedFields(manifest);
  for (const field of MULTIPART_FIELD_ORDER) {
    if (
      field !== "documents" &&
      Object.hasOwn(snapshot, field) &&
      !isWellFormedUnicode(snapshot[field])
    ) {
      return blocked(
        validation,
        preparationDiagnostic(
          "E_MULTIPART_STRING_ENCODING",
          field,
          "A multipart string contains an unpaired UTF-16 surrogate and cannot be preserved safely.",
        ),
      );
    }
  }

  const documents = snapshot.documents ?? [];
  if (documents.length > 0 && typeof loadDocument !== "function") {
    return blocked(
      validation,
      preparationDiagnostic(
        "E_DOCUMENT_LOADER_REQUIRED",
        "documents",
        "A document loader is required before multipart preparation can continue.",
      ),
    );
  }

  const descriptors = [];
  for (let index = 0; index < documents.length; index += 1) {
    const filename = documents[index];
    const uploadName = uploadFilename(filename);
    if (uploadName === undefined) {
      return blocked(
        validation,
        preparationDiagnostic(
          "E_DOCUMENT_UPLOAD_NAME",
          `documents[${index}]`,
          "The document descriptor cannot produce a safe multipart upload filename.",
        ),
      );
    }
    descriptors.push(
      Object.freeze({
        filename,
        uploadName,
      }),
    );
  }

  const loadedDocuments = [];
  for (let index = 0; index < descriptors.length; index += 1) {
    const descriptor = descriptors[index];
    let blob;
    try {
      blob = await loadDocument(
        Object.freeze({ filename: descriptor.filename }),
        index,
      );
    } catch {
      return blocked(
        validation,
        preparationDiagnostic(
          "E_DOCUMENT_LOAD",
          `documents[${index}]`,
          "The application document loader could not provide this document.",
        ),
      );
    }
    if (!(blob instanceof globalThis.Blob)) {
      return blocked(
        validation,
        preparationDiagnostic(
          "E_DOCUMENT_BLOB_REQUIRED",
          `documents[${index}]`,
          "The application document loader must return a Blob.",
        ),
      );
    }
    loadedDocuments.push({ blob, uploadName: descriptor.uploadName });
  }

  const formData = new globalThis.FormData();
  const fieldNames = [];
  for (const field of MULTIPART_FIELD_ORDER) {
    if (field === "documents") {
      for (const document of loadedDocuments) {
        formData.append(field, document.blob, document.uploadName);
        fieldNames.push(field);
      }
    } else if (Object.hasOwn(snapshot, field)) {
      formData.append(field, snapshot[field]);
      fieldNames.push(field);
    }
  }

  return {
    status: "ready",
    validation,
    preparationDiagnostics: [],
    formData,
    partSummary: {
      fieldNames,
      documentCount: loadedDocuments.length,
      partCount: fieldNames.length,
    },
  };
}
