#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const documents = [
  ".mintlify/skills/hydradb-ingest-context/SKILL.md",
  ".mintlify/skills/hydradb-query-context/SKILL.md",
  "get-started/v2/quickstart.mdx",
  "api-reference/v2/endpoint/ingest-context.mdx",
  "api-reference/v2/endpoint/tenant-status.mdx",
  "api-reference/v2/endpoint/update-source-metadata.mdx",
  "api-reference/v2/error-responses.mdx",
  "api-reference/v2/index.mdx",
  "api-reference/v2/sdks.mdx",
  "essentials/v2/metadata.mdx",
  "AGENTS.mdx",
];

const options = {
  module: ts.ModuleKind.Node16,
  moduleResolution: ts.ModuleResolutionKind.Node16,
  noEmit: true,
  skipLibCheck: true,
  strict: true,
  target: ts.ScriptTarget.ES2022,
  types: ["node"],
};
const expectedSnippetCount = 41;

let failures = 0;
let snippetNumber = 0;

for (const document of documents) {
  const markdown = fs.readFileSync(path.join(root, document), "utf8");
  const blocks = [...markdown.matchAll(/```(?:typescript|ts)(?:[^\n]*)\n(.*?)```/gis)];
  if (blocks.length === 0) {
    failures += 1;
    console.error(`${document}: no TypeScript snippets found; SDK type coverage was lost.`);
  }

  for (const [index, match] of blocks.entries()) {
    snippetNumber += 1;
    const block = match[1];
    const imports = block.match(/^import[^\n]+;\s*$/gm) ?? [];
    let body = block.replace(/^import[^\n]+;\s*$/gm, "").trim();

    if (!imports.join("\n").includes("HydraDBClient")) {
      imports.push('import { HydraDBClient } from "@hydradb/sdk";');
    }
    if (!/\bconst\s+client\s*=/.test(body)) {
      body = 'const client = new HydraDBClient({ token: "typecheck-only" });\n' + body;
    }

    const source = `${imports.join("\n")}\n\nasync function snippet() {\n${body}\n}\nvoid snippet;\n`;
    const virtualFile = path.join(root, "tests", `__agent_snippet_${snippetNumber}.ts`);
    const isVirtualFile = (fileName) => path.resolve(fileName) === path.resolve(virtualFile);
    const host = ts.createCompilerHost(options);
    const originalFileExists = host.fileExists.bind(host);
    const originalGetSourceFile = host.getSourceFile.bind(host);
    const originalReadFile = host.readFile.bind(host);

    host.fileExists = (fileName) => isVirtualFile(fileName) || originalFileExists(fileName);
    host.readFile = (fileName) => isVirtualFile(fileName) ? source : originalReadFile(fileName);
    host.getSourceFile = (fileName, languageVersion, onError, shouldCreateNewSourceFile) => {
      if (isVirtualFile(fileName)) {
        return ts.createSourceFile(fileName, source, languageVersion, true);
      }
      return originalGetSourceFile(fileName, languageVersion, onError, shouldCreateNewSourceFile);
    };

    const program = ts.createProgram([virtualFile], options, host);
    const diagnostics = ts.getPreEmitDiagnostics(program);
    if (diagnostics.length > 0) {
      failures += 1;
      const formatted = ts.formatDiagnosticsWithColorAndContext(diagnostics, {
        getCanonicalFileName: (fileName) => fileName,
        getCurrentDirectory: () => root,
        getNewLine: () => "\n",
      });
      console.error(`${document}: TypeScript block ${index + 1} failed:\n${formatted}`);
    }
  }
}

if (snippetNumber === 0) {
  console.error("No TypeScript agent snippets found.");
  process.exit(1);
}
if (snippetNumber !== expectedSnippetCount) {
  console.error(
    `Expected ${expectedSnippetCount} changed-page TypeScript snippets, found ${snippetNumber}; ` +
    "review the coverage list and update the audited count intentionally."
  );
  process.exit(1);
}

if (failures > 0) process.exit(1);
console.log(`Strict SDK type-check passed for ${snippetNumber} TypeScript agent snippets.`);
