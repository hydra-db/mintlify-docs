#!/usr/bin/env python3
import json
import os
import pathlib
import re
import subprocess
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC_PATH = pathlib.Path(os.environ.get("HYDRADB_V2_SPEC_PATH", ROOT / "api-reference/v2/openapi.json"))
SPEC = json.loads(SPEC_PATH.read_text())
SCHEMAS = SPEC["components"]["schemas"]
ERRORS = []
REGISTRY = Registry().with_resource(
    "urn:hydradb:openapi",
    Resource.from_contents(SPEC, default_specification=DRAFT202012),
)


def require(condition, message):
    if not condition:
        ERRORS.append(message)


def walk(value, path="$ public spec"):
    if isinstance(value, dict):
        title = value.get("title")
        if isinstance(title, str):
            require(not re.search(r"\bobject\b", title, re.I),
                    f"{path}: generic object display title {title!r}; use the schema identity or JSON type")
        if "$ref" in value:
            ref = value["$ref"]
            require(ref.startswith("#/"), f"{path}: external reference {ref}")
            if ref.startswith("#/"):
                target = SPEC
                for part in ref[2:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    if not isinstance(target, dict) or key not in target:
                        ERRORS.append(f"{path}: unresolved reference {ref}")
                        break
                    target = target[key]
        for key, child in value.items():
            if key not in {"example", "examples"}:
                walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk(child, f"{path}[{index}]")


def resolve(schema):
    seen = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen or not ref.startswith("#/components/schemas/"):
            return {}
        seen.add(ref)
        schema = SCHEMAS.get(ref.rsplit("/", 1)[-1], {})
    return schema


def object_shape(schema):
    schema = resolve(schema)
    if schema.get("type") == "object" or "additionalProperties" in schema:
        return schema
    for branch in schema.get("oneOf", []) + schema.get("anyOf", []):
        found = object_shape(branch)
        if found is not None:
            return found
    return None


def check_schema_types(schema, path):
    if not isinstance(schema, dict):
        return
    shape_keys = {"type", "$ref", "properties", "additionalProperties", "allOf", "oneOf", "anyOf", "enum", "const", "not"}
    passthrough = {
        f"{name}.{field}"
        for name in ("list.V2SourceItem", "list.V2MemoryItem")
        for field in ("relations", "app_relations")
    }
    require(bool(shape_keys & schema.keys()) or path in passthrough,
            f"{path}: fixed API values need a schema type or reference")
    types = schema.get("type", [])
    types = {types} if isinstance(types, str) else set(types)
    if "object" in types:
        require(bool(schema.get("properties")) or "additionalProperties" in schema
                or any(key in schema for key in ("allOf", "oneOf", "anyOf")) or path in passthrough,
                f"{path}: fixed objects must expose their properties")
    if "array" in types:
        require("items" in schema, f"{path}: arrays must declare their element type")
    for name, child in schema.get("properties", {}).items():
        check_schema_types(child, f"{path}.{name}")
    if "items" in schema:
        check_schema_types(schema["items"], f"{path}[]")
    for key in ("allOf", "oneOf", "anyOf"):
        for index, child in enumerate(schema.get(key, [])):
            check_schema_types(child, f"{path}.{key}[{index}]")
    values = schema.get("additionalProperties")
    if isinstance(values, dict) and values:
        check_schema_types(values, f"{path}.{{key}}")


def named_response(schema):
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        return component_ref(schema["$ref"])
    branches = schema.get("oneOf") or schema.get("anyOf")
    return bool(branches) and all(
        isinstance(branch, dict) and component_ref(branch.get("$ref")) for branch in branches
    )


def component_ref(ref):
    prefix = "#/components/schemas/"
    return isinstance(ref, str) and ref.startswith(prefix) and ref[len(prefix):] in SCHEMAS


def schema_refs(schema):
    if not isinstance(schema, dict):
        return set()
    refs = {schema["$ref"]} if "$ref" in schema else set()
    for key in ("items", "additionalProperties"):
        refs |= schema_refs(schema.get(key))
    for key in ("allOf", "oneOf", "anyOf"):
        for branch in schema.get(key, []):
            refs |= schema_refs(branch)
    return refs


def exact_ref(schema, target):
    return (
        isinstance(schema, dict)
        and schema.get("$ref") == f"#/components/schemas/{target}"
        and not any(key in schema for key in ("allOf", "oneOf", "anyOf", "items", "additionalProperties", "properties"))
    )


def exact_named_field(schema, target, container):
    if not isinstance(schema, dict) or any(key in schema for key in ("oneOf", "anyOf")):
        return False
    if container == "array":
        return schema.get("type") == "array" and exact_ref(schema.get("items"), target)
    if container == "map":
        return schema.get("type") == "object" and exact_ref(schema.get("additionalProperties"), target)
    if container == "allOf":
        branches = schema.get("allOf", [])
        return len(branches) == 1 and exact_ref(branches[0], target)
    return exact_ref(schema, target)


walk(SPEC)
for schema_name, schema in SCHEMAS.items():
    check_schema_types(schema, schema_name)
    require("title" not in schema,
            f"{schema_name}: component display titles obscure the stable schema identity")
for path, methods in SPEC["paths"].items():
    for method, operation in methods.items():
        if method not in {"get", "post", "put", "patch", "delete"}:
            continue
        for parameter in operation.get("parameters", []):
            if "schema" in parameter:
                check_schema_types(parameter["schema"], f"{method.upper()} {path} parameter {parameter['name']}")
        for media in operation.get("requestBody", {}).get("content", {}).values():
            check_schema_types(media["schema"], f"{method.upper()} {path} request")
        for status, response in operation.get("responses", {}).items():
            content = response.get("content", {})
            if str(status).startswith("2") and str(status) != "204":
                require(content, f"{method.upper()} {path} response {status}: successful body responses need content")
            for media_type, media in content.items():
                require("schema" in media,
                        f"{method.upper()} {path} response {status} {media_type}: response content needs a schema")
                if "schema" not in media:
                    continue
                check_schema_types(media["schema"], f"{method.upper()} {path} response {status}")
                if str(status).startswith("2"):
                    require(named_response(media["schema"]),
                            f"{method.upper()} {path} response {status}: successful responses need named component schemas")
query = SCHEMAS["search.QueryRequest"]["properties"]
for field in ("attributes", "graph_vector_prune", "graph_vector_prune_spacy_entities", "temporal_intent", "temporal_now"):
    require(field not in query, f"QueryRequest exposes internal field {field}")
for field in ("metadata_filters", "graph_context", "response_format", "temporal_reasoning"):
    require(field in query, f"QueryRequest is missing released field {field}")
require(not query["metadata_filters"].get("deprecated"), "metadata_filters must remain supported")
require(query["temporal_reasoning"].get("default") is True, "temporal_reasoning must default to true")
list_filters = SCHEMAS["list.ContentFilter"]["properties"]
require("attributes" not in list_filters, "List Context must not expose unlaunched attributes")
require("metadata" in list_filters, "List Context must preserve filters.metadata")
require(not list_filters.get("metadata", {}).get("deprecated"), "List Context filters.metadata must remain supported")
result = SCHEMAS["search.V2RetrievalResult"]["properties"]
for field in ("source_facts", "source_filter", "sources", "app_search_fusion", "temporal_filter"):
    require(field not in result, f"Query result exposes internal field {field}")
for field in ("chunks", "graph_context", "additional_context", "llm_prompt"):
    require(field in result, f"Query result is missing released field {field}")
for field, (target, container) in {
    "alias_expansions": ("search.AliasExpansionNote", "array"),
    "additional_context": ("search.V2Chunk", "map"),
    "chunks": ("search.V2Chunk", "array"),
    "code_search": ("search.CodeSearchResult", "allOf"),
    "graph_context": ("search.GraphContext", "allOf"),
    "temporal_duration": ("search.TemporalDuration", "ref"),
    "temporal_facts": ("search.TemporalFact", "array"),
}.items():
    schema = result[field]
    require(exact_named_field(schema, target, container),
            f"Query result {field} must use only the named {target} model in its {container} shape")

query_response = (
    SPEC["paths"]["/query"]["post"]["responses"]["200"]
    .get("content", {})
    .get("application/json", {})
    .get("schema", {})
)
require(query_response.get("$ref") == "#/components/schemas/handler.Envelope-search_V2RetrievalResult",
        "POST /query must return the named query envelope")
query_data = SCHEMAS["handler.Envelope-search_V2RetrievalResult"]["properties"]["data"]
query_branches = query_data.get("oneOf", [])
require(len(query_branches) == 2
        and sum(exact_ref(branch, "search.V2RetrievalResult") for branch in query_branches) == 1
        and sum(branch.get("type") == "string" and not any(key in branch for key in ("allOf", "oneOf", "anyOf", "$ref")) for branch in query_branches) == 1,
        "Query envelope data must contain exactly the named search.V2RetrievalResult model and markdown string branch")
chunk = resolve(result["chunks"]["items"])
require({"chunk_content", "id", "relevancy_score"} <= chunk.get("properties", {}).keys(),
        "Query chunks must expose their typed, expandable fields")
triplet = SCHEMAS["search.PathTriplet"]["properties"]
for field in ("source", "target", "relation"):
    child = resolve(triplet[field])
    require(bool(child.get("properties")), f"PathTriplet.{field} must expose its nested fields")
for schema_name, schema in SCHEMAS.items():
    for field in ("metadata", "additional_metadata", "provider_metadata", "credentials"):
        if field in schema.get("properties", {}):
            shape = object_shape(schema["properties"][field])
            require(shape is not None and "additionalProperties" in shape and shape["additionalProperties"] is not False,
                    f"{schema_name}.{field} must preserve arbitrary customer keys")
security = SPEC["components"]["securitySchemes"]["BearerAuth"]
require("prefix.secret" not in security.get("description", ""), "Bearer auth must describe the user's API key")
providers = SPEC["paths"]["/connectors/providers"]["get"]
require(any("BearerAuth" in rule for rule in providers.get("security", [])), "Provider catalog requires Bearer authentication")
for name in ("list.V2ListContentRequest", "sources.V2SourceDeleteRequest"):
    require(SCHEMAS[name]["properties"]["type"].get("enum") == ["knowledge", "memory", "all"], f"{name}: preserve combined knowledge and memory requests")
for path in ("/context/relations", "/context/{id}/subgraph", "/context/subgraph"):
    for parameter in SPEC["paths"][path]["get"].get("parameters", []):
        if parameter.get("name") == "type":
            require(parameter["schema"].get("enum") == ["knowledge", "memory"], f"{path}: publish knowledge and memory selectors")
            require("`all`" not in parameter.get("description", ""), f"{path}: description must match the published selectors")
require("all" in SCHEMAS["search.SourceType"].get("enum", []), "Query must retain combined knowledge and memory retrieval")
filter_text = query["metadata_filters"].get("description", "")
require("500" in filter_text and "64 KiB" in filter_text, "Query filters must document enforced list and byte limits")
graph_source = triplet["source"].get("example", {})
graph_target = triplet["target"].get("example", {})
graph_relation = triplet["relation"].get("example", {})
require(graph_source.get("name") and graph_target.get("name") and graph_source.get("entity_id") != graph_target.get("entity_id"), "Graph examples need distinct named entities")
require(graph_source.get("entity_id") == graph_relation.get("source_entity_id") and graph_target.get("entity_id") == graph_relation.get("target_entity_id"), "Graph example relation must reference its source and target")

if ERRORS:
    print("\n".join(ERRORS), file=sys.stderr)
    sys.exit(1)

internal_fields = {"attributes", "graph_vector_prune", "graph_vector_prune_spacy_entities", "temporal_intent", "temporal_now"}
legacy_fields = {"tenant_id", "sub_tenant_id", "sub_tenant_ids", "tenant_metadata_keys", "milvus_sync_required", "milvus_synced", "milvus_rows_synced"}
pages = sorted((ROOT / "api-reference/v2").rglob("*.mdx")) + [ROOT / "essentials/v2/api-results.mdx"]
operation_count = 0
json_count = 0
response_count = 0
for page in pages:
    source = page.read_text()
    relative = page.relative_to(ROOT)
    require(not re.search(r'<a\s+id="[^"]+"\s*></a>', source), f"{relative}: empty link anchor adds an orange rule")
    require(not re.search(r'<Field name="attributes"', source), f"{relative}: unlaunched attributes parameter")
    binding = re.search(r'^openapi:\s*"api-reference/v2/openapi.json (\w+) ([^"]+)"', source, re.M)
    if binding:
        method, path = binding.groups()
        require(method.lower() in SPEC.get("paths", {}).get(path, {}), f"{relative}: missing {method} {path}")
        operation_count += 1
        operation = SPEC.get("paths", {}).get(path, {}).get(method.lower(), {})
        for block in re.findall(r"<ResponseExample>(.*?)</ResponseExample>", source, re.S):
            for label, body in re.findall(r"```json([^\n]*)\n(.*?)\n```", block, re.S):
                try:
                    sample = json.loads(body)
                except json.JSONDecodeError:
                    continue
                success = sample.get("success") if isinstance(sample, dict) else None
                if success is None and not re.match(r"\s*2\d\d", label):
                    continue
                candidates = []
                for code, response in operation.get("responses", {}).items():
                    positive = str(code).startswith("2")
                    if positive != (success is not False):
                        continue
                    schema = response.get("content", {}).get("application/json", {}).get("schema")
                    if schema:
                        candidates.append({"$ref": f"urn:hydradb:openapi#/paths/{path.replace('~', '~0').replace('/', '~1')}/{method.lower()}/responses/{code}/content/application~1json/schema"})
                if candidates:
                    response_count += 1
                    attempts = [list(Draft202012Validator(candidate, registry=REGISTRY).iter_errors(sample)) for candidate in candidates]
                    findings = min(attempts, key=len)
                    details = "; ".join(f"{error.json_path}: {error.message[:120]}" for error in findings[:4])
                    require(not findings, f"{relative}: response example {label.strip()} does not match the published schema: {details}")
    for line in source.splitlines():
        if line.lstrip().startswith("|"):
            field = re.search(r'<Field name="([^"]+)"', line)
            require(not field or field[1] not in legacy_fields | internal_fields, f"{relative}: deprecated or internal field in handwritten table")
    for label, body in re.findall(r'^\s*```json([^\n]*)\n(.*?)^\s*```', source, re.M | re.S):
        try:
            sample = json.loads(body)
        except json.JSONDecodeError as error:
            ERRORS.append(f"{relative}: invalid JSON example {label.strip()}: {error}")
            continue
        json_count += 1
        if page.name == "query.mdx" and isinstance(sample, dict) and "query" in sample:
            require(not internal_fields & sample.keys(), f"{relative}: internal query request parameters")
        if page.name == "query.mdx" and isinstance(sample, dict) and isinstance(sample.get("data"), dict):
            require(not {"sources", "source_facts", "source_filter", "app_search_fusion", "temporal_filter"} & sample["data"].keys(), f"{relative}: internal query response fields in {label.strip()}")

if ERRORS:
    print("\n".join(ERRORS), file=sys.stderr)
    sys.exit(1)
if os.environ.get("HYDRADB_V2_SKIP_MUTATION_TESTS") != "1":
    mutation = subprocess.run(
        [sys.executable, str(ROOT / ".github/test_verify_v2_public_contract.py")],
        cwd=ROOT,
        env=dict(os.environ, HYDRADB_V2_SKIP_MUTATION_TESTS="1"),
        capture_output=True,
        text=True,
    )
    if mutation.returncode:
        print(mutation.stdout + mutation.stderr, file=sys.stderr)
        sys.exit(mutation.returncode)
print(f"Verified {len(pages)} V2 pages, {operation_count} endpoint bindings, {json_count} JSON examples, {response_count} response shapes, and all schema references.")
