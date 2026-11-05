import ast
import contextlib
import copy
from concurrent.futures import ThreadPoolExecutor
import email
import email.policy
import io
import json
from pathlib import Path
import re
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid

import httpx
from hydra_db import HydraDB
from notion_client import Client as NotionClient
from notion_client.helpers import iterate_paginated_api


ROOT = Path(__file__).resolve().parents[1]
CURSOR = "cookbooks/v2/cookbook-01-build-cursor-for-docs.mdx"
NOTION = "cookbooks/v2/cookbook-04-build-notion-ai.mdx"
ROUTING = "cookbooks/v2/hydradb-cookbook-06.mdx"


def blocks(relative, language="python"):
    text = (ROOT / relative).read_text()
    return re.findall(rf"^```{language}[^\n]*\n(.*?)^```", text, re.M | re.S)


def block(relative, contains, language="python"):
    return next(code for code in blocks(relative, language) if contains in code)


def definitions(relative, contains, **namespace):
    tree = ast.parse(block(relative, contains))
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    ast.fix_missing_locations(tree)
    exec(compile(tree, relative, "exec"), namespace)
    return namespace


class CookbookSDKTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.outcomes = [{"id": "accepted", "status": "queued"}]
        self.http = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.client = HydraDB(token="synthetic-test-key", base_url="https://fixture.invalid", httpx_client=self.http)

    def tearDown(self):
        self.http.close()

    def respond(self, request):
        self.assertEqual(request.url.host, "fixture.invalid")
        self.assertEqual(request.headers["API-Version"], "2")
        self.requests.append(request)
        data = {"results": self.outcomes, "success_count": sum(r["status"] != "failed" for r in self.outcomes), "failed_count": sum(r["status"] == "failed" for r in self.outcomes)}
        return httpx.Response(202, json={"success": True, "data": data, "error": None, "meta": {"request_id": "synthetic-request"}})

    def namespace(self, relative, contains):
        return definitions(relative, contains, client=self.client, json=json, DATABASE_ID="fixture", TENANT_ID="fixture")

    def test_cookbook06_unwraps_real_sdk_envelope(self):
        ns = self.namespace(ROUTING, "def upload_functions")
        self.assertEqual(["accepted"], ns["_upload_batch"]([{"id": "accepted", "content": {"text": "synthetic"}}], "functions"))

    def test_named_finance_schema_is_valid_for_its_loader(self):
        schema = block(ROUTING, '"cost_threshold":', "json")
        ns = self.namespace(ROUTING, "def load_schema")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approve_expense.json"
            path.write_text(schema)
            parsed = ns["load_schema"](path)
        self.assertEqual("approve_expense", parsed["id"])

    def test_cookbook04_memory_uses_multipart_json_string(self):
        ns = self.namespace(NOTION, "def add_user_memory")
        ns["add_user_memory"]("synthetic-user", "Synthetic preference", collection="user-test")
        request = self.requests[-1]
        body = request.read()
        if "multipart/form-data" in request.headers["content-type"]:
            message = email.message_from_bytes(("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode() + body, policy=email.policy.default)
            fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode() for part in message.iter_parts()}
        else:
            fields = dict(httpx.QueryParams(body.decode()))
        self.assertEqual("memory", fields["type"])
        self.assertEqual("Synthetic preference", json.loads(fields["memories"])[0]["text"])

    def test_partial_batch_reports_only_accepted_ids_to_verification(self):
        self.outcomes = [{"id": "accepted", "status": "queued"}, {"id": "rejected", "status": "failed", "error": "synthetic rejection"}]
        ns = self.namespace(CURSOR, "def upload_batch")
        verified = []
        ns["verify_item"] = verified.append
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ids = ns["upload_batch"]([{"id": "accepted"}, {"id": "rejected"}])
            ns["verify_batch"](ids)
        self.assertEqual(["accepted"], ids)
        self.assertEqual(["accepted"], verified)
        self.assertIn("rejected", output.getvalue().lower())
        self.assertNotIn("Uploaded 2", output.getvalue())

    def test_all_failed_batch_does_not_report_success(self):
        self.outcomes = [{"id": "rejected", "status": "failed", "error": "synthetic rejection"}]
        ns = self.namespace(CURSOR, "def upload_batch")
        with self.assertRaises(RuntimeError):
            ns["upload_batch"]([{"id": "rejected"}])

    def test_empty_batch_response_does_not_report_success(self):
        self.outcomes = []
        ns = self.namespace(CURSOR, "def upload_batch")
        with self.assertRaises(RuntimeError):
            ns["upload_batch"]([{"id": "missing"}])

    def test_other_batch_consumers_handle_partial_and_all_failed(self):
        for relative, marker, name, args in [(NOTION, "def extract_text", "upload_batch", ()), (ROUTING, "def upload_functions", "_upload_batch", ("functions",))]:
            for partial in (True, False):
                with self.subTest(relative=relative, partial=partial):
                    self.outcomes = ([{"id": "accepted", "status": "queued"}] if partial else []) + [{"id": "rejected", "status": "failed", "error": "synthetic rejection"}]
                    ns = self.namespace(relative, marker)
                    if partial:
                        self.assertEqual(["accepted"], ns[name]([{"id": "accepted"}, {"id": "rejected"}], *args))
                    else:
                        with self.assertRaises(RuntimeError):
                            ns[name]([{"id": "rejected"}], *args)


class NotionWorkflowTest(unittest.TestCase):
    def test_exact_helper_uses_released_data_source_api_and_pagination(self):
        requests = []
        uploaded = []

        def respond(request):
            requests.append((request.method, request.url.path))
            path = request.url.path
            if path == "/v1/databases/database-test":
                data = {"object": "database", "id": "database-test", "data_sources": [{"id": "source-a"}, {"id": "source-b"}]}
            elif path.endswith("/query"):
                body = json.loads(request.read())
                page_id = "page-c" if "source-b" in path else "page-b" if body.get("start_cursor") else "page-a"
                data = {"results": [{"id": page_id, "properties": {"Name": {"title": [{"plain_text": page_id}]}}, "created_by": {"id": "synthetic-author"}, "last_edited_time": "2026-01-01T00:00:00Z"}], "has_more": page_id == "page-a", "next_cursor": "next-page" if page_id == "page-a" else None}
            else:
                self.assertIn("/children", path)
                more = "page-a" in path and not request.url.params.get("start_cursor")
                text = "continued block" if request.url.params.get("start_cursor") else "synthetic page text"
                data = {"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}], "has_more": more, "next_cursor": "block-next" if more else None}
            return httpx.Response(200, json=data)

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            notion = NotionClient(auth="synthetic-notion-token", client=http)
            ns = definitions(NOTION, "def extract_text", json=json, time=SimpleNamespace(sleep=lambda _: None), iterate_paginated_api=iterate_paginated_api)
            ns.update(notion=notion, upload_batch=lambda batch, collection: uploaded.extend(batch) or [row["id"] for row in batch])
            ids = ns["ingest_notion_database"]("database-test", collection="workspace")
        self.assertEqual(["page-a", "page-b", "page-c"], ids)
        self.assertEqual(3, len(uploaded))
        self.assertEqual("synthetic page text\n\ncontinued block", uploaded[0]["content"]["text"])
        self.assertIn(("POST", "/v1/data_sources/source-a/query"), requests)
        self.assertIn(("POST", "/v1/data_sources/source-b/query"), requests)
        self.assertEqual(3, sum(path.endswith("/query") for _, path in requests))


class ApprovalInvariantTest(unittest.TestCase):
    def make_orchestrator(self, requires_approval=True):
        self.executed = []
        self.schema = {"id": "synthetic_action", "parameters": {}, "meta": {"requires_approval": requires_approval, "cost_threshold": 5000}}
        policy = definitions(ROUTING, "class PolicyEngine", datetime=__import__("datetime").datetime, timezone=__import__("datetime").timezone)
        policy["USER_ROLES"] = {"synthetic-user": {"level": "manager", "departments": ["all"]}}
        policy["PERMISSION_HIERARCHY"] = ["viewer", "contributor", "manager", "admin"]
        self.policy_namespace = policy
        llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"amount":10000}'))]))))
        query = SimpleNamespace(data=SimpleNamespace(chunks=[SimpleNamespace(chunk_content=json.dumps(self.schema))]))
        client = SimpleNamespace(query=lambda **_: query, context=SimpleNamespace(ingest=lambda **_: None))
        self.query_response = query
        ns = definitions(ROUTING, "class ChiefOfStaffOrchestrator", uuid=uuid, json=json, client=client, TENANT_ID="fixture", OpenAI=lambda: llm)
        orchestrator = ns["ChiefOfStaffOrchestrator"]({"synthetic_action": lambda params: self.executed.append(dict(params)) or {"success": True}}, policy["PolicyEngine"](), SimpleNamespace(get_token=lambda *_: "synthetic-token"))
        orchestrator.schemas = {"synthetic_action": self.schema}
        orchestrator.approvals = None
        return orchestrator

    def add_approval_store(self, orchestrator):
        ns = definitions(ROUTING, "class ApprovalStore", copy=copy, json=json, threading=threading, uuid=uuid)
        orchestrator.approvals = ns["ApprovalStore"]("synthetic-approver")

    def dispatch(self, path, orchestrator, previous=None, approval_id=None):
        if path == "direct":
            return orchestrator.handle_task("Synthetic action", "synthetic-user") if previous is None else orchestrator.resume_approval(approval_id, "synthetic-user")
        if path == "plan":
            ns = definitions(ROUTING, "def execute_plan")
            plan = [{"step": 1, "function_id": "synthetic_action", "params": {"amount": 10000}, "depends_on": []}]
            return ns["execute_plan"](plan, "synthetic-user", orchestrator, results=previous["results"] if previous else None, approval_ids={1: approval_id} if approval_id else None)
        ns = definitions(ROUTING, "def rollback_plan", COMPENSATION_REGISTRY={"forward_action": "synthetic_action"})
        results = {1: {"status": "done", "function_id": "forward_action", "result": {"success": True, "amount": 10000}}}
        return ns["rollback_plan"](results, 2, "synthetic-user", orchestrator, approval_ids={1: approval_id} if approval_id else None, compensated=previous["compensated"] if previous else None)

    def test_approval_states_across_all_execution_paths(self):
        for path in ("direct", "plan", "compensation"):
            for requirement in ("flag", "threshold", "none"):
                for state in ("absent", "pending", "denied", "approved"):
                    with self.subTest(path=path, requirement=requirement, state=state):
                        orchestrator = self.make_orchestrator(requirement == "flag")
                        if requirement != "threshold":
                            self.schema["meta"].pop("cost_threshold")
                        if state != "absent":
                            self.add_approval_store(orchestrator)
                        first = self.dispatch(path, orchestrator)
                        if requirement == "none":
                            self.assertEqual(1, len(self.executed))
                            self.assertIn(first["status"], ("done", "complete"))
                            continue
                        self.assertEqual("pending", first["status"])
                        self.assertEqual([], self.executed)
                        if state == "absent":
                            continue
                        approval_id = first["approval_id"]
                        if state != "pending":
                            orchestrator.approvals.decide(approval_id, "synthetic-approver", state == "approved")
                        second = self.dispatch(path, orchestrator, first, approval_id)
                        self.assertEqual(1 if state == "approved" else 0, len(self.executed))
                        self.assertIn(second["status"], ("done", "complete") if state == "approved" else ("pending", "blocked"))
                        self.dispatch(path, orchestrator, second, approval_id)
                        self.assertEqual(1 if state == "approved" else 0, len(self.executed))

    def test_approval_does_not_override_a_current_policy_denial(self):
        orchestrator = self.make_orchestrator()
        self.add_approval_store(orchestrator)
        first = orchestrator.handle_task("Synthetic action", "synthetic-user")
        orchestrator.approvals.decide(first["approval_id"], "synthetic-approver", True)
        self.policy_namespace["USER_ROLES"] = {}
        self.assertEqual("blocked", orchestrator.resume_approval(first["approval_id"], "synthetic-user")["status"])
        self.assertEqual([], self.executed)

    def test_retrieved_metadata_cannot_disable_local_approval_policy(self):
        orchestrator = self.make_orchestrator()
        self.query_response.data.chunks[0].chunk_content = json.dumps({"id": "synthetic_action", "parameters": {}, "meta": {"requires_approval": False}})
        self.assertEqual("pending", orchestrator.handle_task("Synthetic action", "synthetic-user")["status"])
        self.assertEqual([], self.executed)

    def test_unknown_local_schema_is_blocked(self):
        orchestrator = self.make_orchestrator()
        orchestrator.schemas = {}
        self.assertEqual("blocked", orchestrator.handle_task("Synthetic action", "synthetic-user")["status"])
        self.assertEqual([], self.executed)

    def test_approval_is_bound_to_user_function_and_parameters(self):
        orchestrator = self.make_orchestrator()
        self.add_approval_store(orchestrator)
        first = orchestrator.handle_task("Synthetic action", "synthetic-user")
        approval_id = first["approval_id"]
        with self.assertRaises(PermissionError):
            orchestrator.approvals.decide(approval_id, "untrusted-user", True)
        with self.assertRaises(ValueError):
            orchestrator.approvals.decide(approval_id, "synthetic-approver", "false")
        orchestrator.approvals.decide(approval_id, "synthetic-approver", True)
        for user, function, params in [("wrong-user", "synthetic_action", {"amount": 10000}), ("synthetic-user", "wrong-function", {"amount": 10000}), ("synthetic-user", "synthetic_action", {"amount": 20000})]:
            self.assertEqual("invalid", orchestrator.approvals.consume(approval_id, user, function, params))
        self.assertEqual("blocked", orchestrator.execute_function("synthetic_action", "synthetic-user", {"amount": 20000}, approval_id=approval_id)["status"])
        self.assertEqual([], self.executed)

    def test_tokens_are_injected_only_after_approval_and_not_stored(self):
        orchestrator = self.make_orchestrator()
        self.add_approval_store(orchestrator)
        self.schema["auth"] = {"oauth_provider": "synthetic-provider"}
        reads = []
        orchestrator.vault = SimpleNamespace(get_token=lambda *_: reads.append(True) or "synthetic-vault-token")
        first = orchestrator.execute_function("synthetic_action", "synthetic-user", {"amount": 10000, "_token": "untrusted-token"})
        self.assertEqual([], reads)
        self.assertNotIn("_token", orchestrator.approvals.get(first["approval_id"])["params"])
        orchestrator.approvals.decide(first["approval_id"], "synthetic-approver", True)
        orchestrator.resume_approval(first["approval_id"], "synthetic-user")
        self.assertEqual([True], reads)
        self.assertEqual("synthetic-vault-token", self.executed[0]["_token"])

    def test_concurrent_resume_consumes_approval_once(self):
        orchestrator = self.make_orchestrator()
        self.add_approval_store(orchestrator)
        first = orchestrator.handle_task("Synthetic action", "synthetic-user")
        orchestrator.approvals.decide(first["approval_id"], "synthetic-approver", True)
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: orchestrator.resume_approval(first["approval_id"], "synthetic-user")["status"], range(2)))
        self.assertCountEqual(["done", "blocked"], statuses)
        self.assertEqual(1, len(self.executed))

    def test_plan_resume_does_not_repeat_completed_steps(self):
        orchestrator = self.make_orchestrator()
        self.add_approval_store(orchestrator)
        self.schema["meta"].pop("cost_threshold")
        orchestrator.schemas["first"] = {"id": "first", "meta": {}}
        orchestrator.registry["first"] = lambda _: self.executed.append("first") or {"success": True}
        plan = [{"step": 1, "function_id": "first", "params": {}, "depends_on": []}, {"step": 2, "function_id": "synthetic_action", "params": {"amount": 10000}, "depends_on": [1]}]
        ns = definitions(ROUTING, "def execute_plan")
        first = ns["execute_plan"](plan, "synthetic-user", orchestrator)
        self.assertEqual(["first"], self.executed)
        orchestrator.approvals.decide(first["approval_id"], "synthetic-approver", True)
        second = ns["execute_plan"](plan, "synthetic-user", orchestrator, results=json.loads(json.dumps(first["results"])), approval_ids={2: first["approval_id"]})
        self.assertEqual("complete", second["status"])
        self.assertEqual(1, self.executed.count("first"))
        self.assertEqual(2, len(self.executed))

    def test_missing_approval_service_never_executes_required_action(self):
        orchestrator = self.make_orchestrator()
        result = orchestrator.handle_task("Synthetic action", "synthetic-user")
        self.assertEqual([], self.executed)
        self.assertEqual("pending", result["status"])

    def test_plan_cannot_bypass_the_approval_gate(self):
        orchestrator = self.make_orchestrator()
        ns = definitions(ROUTING, "def execute_plan")
        result = ns["execute_plan"]([{"step": 1, "function_id": "synthetic_action", "params": {"amount": 10000}, "depends_on": []}], "synthetic-user", orchestrator)
        self.assertEqual([], self.executed)
        self.assertEqual("pending", result["status"])


if __name__ == "__main__":
    unittest.main()
