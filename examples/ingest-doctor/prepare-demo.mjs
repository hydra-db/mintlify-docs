#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { prepareValidatedMultipart } from "./prepare-multipart.mjs";

export async function runDemo({
  stdout = (text) => process.stdout.write(text),
  stderr = (text) => process.stderr.write(text),
} = {}) {
  const manifest = JSON.parse(
    await readFile(new URL("./repaired.json", import.meta.url), "utf8"),
  );
  const prepared = await prepareValidatedMultipart(manifest, {
    loadDocument: async () =>
      new Blob(["synthetic document bytes"], {
        type: "application/pdf",
      }),
  });

  if (prepared.status !== "ready") {
    stderr(
      `${JSON.stringify(
        {
          status: prepared.status,
          validationStatus: prepared.validation.status,
          preparationDiagnostics: prepared.preparationDiagnostics,
        },
        null,
        2,
      )}\n`,
    );
    return 1;
  }

  stdout(
    `${JSON.stringify(
      {
        status: prepared.status,
        ...prepared.partSummary,
      },
      null,
      2,
    )}\n`,
  );
  return 0;
}

const isMain =
  process.argv[1] !== undefined &&
  resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (isMain) {
  process.exitCode = await runDemo();
}
