import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");
const initializers = [];
for (const filename of fs.readdirSync(path.join(root, "cookbooks/v2")).filter(name => name.endsWith(".mdx"))) {
  const text = fs.readFileSync(path.join(root, "cookbooks/v2", filename), "utf8");
  const fences = [...text.matchAll(/^```(?:typescript|ts|javascript|js)[^\n]*\n([\s\S]*?)^```/gm)];
  for (const [index, match] of fences.entries()) {
    const source = ts.createSourceFile(filename, match[1], ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    function visit(node) {
      if (ts.isCallExpression(node) && /\.context\.ingest$/.test(node.expression.getText(source))) {
        const body = node.arguments[0];
        if (body && ts.isObjectLiteralExpression(body)) {
          const upsert = body.properties.find(property => ts.isPropertyAssignment(property) && property.name.getText(source) === "upsert");
          if (upsert) initializers.push({ location: `${filename} block ${index + 1}`, text: upsert.initializer.getText(source) });
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
}
assert(initializers.length > 0, "No cookbook ingestion upsert fields were found");
const directory = fs.mkdtempSync(path.join(root, ".cookbook-types-"));
try {
  const filename = path.join(directory, "upsert.ts");
  fs.writeFileSync(filename, [
    'import { HydraDBClient } from "@hydradb/sdk";',
    'type Upsert = NonNullable<Parameters<HydraDBClient["context"]["ingest"]>[0]["upsert"]>;',
    ...initializers.map((item, index) => `const value${index}: Upsert = ${item.text};`),
  ].join("\n"));
  const program = ts.createProgram([filename], { strict: true, noEmit: true, skipLibCheck: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext, types: ["node"] });
  const errors = ts.getPreEmitDiagnostics(program);
  for (const error of errors) {
    const line = error.file && error.start !== undefined ? error.file.getLineAndCharacterOfPosition(error.start).line : -1;
    const item = initializers[line - 2];
    console.error(`${item?.location ?? "SDK types"}: ${ts.flattenDiagnosticMessageText(error.messageText, "\n")}`);
  }
  assert.equal(errors.length, 0, "Cookbook upsert fields must satisfy the released SDK's string field type");
  console.log(`${initializers.length} exact cookbook upsert expressions compile against @hydradb/sdk`);
} finally {
  fs.rmSync(directory, { recursive: true, force: true });
}
