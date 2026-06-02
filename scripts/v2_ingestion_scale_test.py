#!/usr/bin/env python3
"""HydraDB v2 ingestion scalability smoke/load helper.

Ingests N synthetic sources through POST /context/ingest, grouped into batches of
K sources per request. This script is intentionally ingestion-only: it does not
run search or retrieval-quality checks.

Examples:
  python scripts/v2_ingestion_scale_test.py --n 100 --k 10
  python scripts/v2_ingestion_scale_test.py --n 500 --k 25 --source-kind app

Environment overrides:
  HYDRADB_BASE_URL, HYDRADB_API_KEY, HYDRADB_TENANT_ID, HYDRADB_SUB_TENANT_ID
  INGEST_SCALE_N, INGEST_SCALE_K, INGEST_SCALE_SOURCE_KIND, INGEST_SCALE_RUN_ID
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import time
import uuid
from pathlib import Path
from typing import Any

import v2_e2e_contract_test as ct

RESULTS_ROOT = Path(__file__).resolve().parent / "v2_e2e_results"
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest N synthetic HydraDB v2 sources in batches of K per request."
    )
    parser.add_argument("--n", type=_positive_int, default=int(os.getenv("INGEST_SCALE_N", "100")))
    parser.add_argument(
        "--k",
        "--batch-size",
        dest="k",
        type=_positive_int,
        default=int(os.getenv("INGEST_SCALE_K", "10")),
        help="number of sources to include in each /context/ingest request",
    )
    parser.add_argument(
        "--source-kind",
        choices=("documents", "app"),
        default=os.getenv("INGEST_SCALE_SOURCE_KIND", "documents"),
        help="documents = multipart markdown files; app = app_knowledge JSON sources",
    )
    parser.add_argument("--base-url", default=os.getenv("HYDRADB_BASE_URL", ct.BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("HYDRADB_API_KEY", ct.API_KEY))
    parser.add_argument("--tenant-id", default=os.getenv("HYDRADB_TENANT_ID", ct.TENANT_ID))
    parser.add_argument(
        "--sub-tenant-id",
        default=os.getenv(
            "HYDRADB_SUB_TENANT_ID",
            f"ingest_scale_{os.getenv('INGEST_SCALE_RUN_ID', time.strftime('%Y%m%d%H%M%S'))}",
        ),
    )
    parser.add_argument("--run-id", default=os.getenv("INGEST_SCALE_RUN_ID", time.strftime("%Y%m%d%H%M%S")))
    parser.add_argument("--upsert", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sleep", type=float, default=float(os.getenv("INGEST_SCALE_SLEEP", "0")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("INGEST_SCALE_RETRIES", "3")))
    parser.add_argument("--retry-sleep", type=float, default=float(os.getenv("INGEST_SCALE_RETRY_SLEEP", "2")))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="defaults to scripts/v2_e2e_results/ingestion_scale_<run_id>",
    )
    return parser.parse_args()


def rand_token(rng: random.Random, length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(length))


def make_doc_source(run_id: str, index: int, rng: random.Random) -> dict[str, Any]:
    sid = f"scale_doc_{run_id}_{index:06d}_{rand_token(rng, 6)}"
    topic = rng.choice(["robots", "billing", "security", "support", "deploy", "inventory"])
    marker = f"MARKER-{run_id}-{index:06d}"
    text = (
        f"# Synthetic ingestion document {index}\n\n"
        f"This is synthetic markdown content for ingestion scalability testing. "
        f"The topic is {topic}. The unique marker is {marker}.\n\n"
        f"Random detail {rand_token(rng, 14)} describes a harmless operational note. "
        f"This document is generated only to exercise the ingestion path.\n"
    )
    return {
        "id": sid,
        "title": f"Synthetic Scale Document {index}",
        "type": "md",
        "dept": topic,
        "text": text,
        "marker": marker,
    }


def make_app_source(run_id: str, index: int, tenant_id: str, sub_tenant_id: str, rng: random.Random) -> dict[str, Any]:
    sid = f"scale_app_{run_id}_{index:06d}_{rand_token(rng, 6)}"
    dept = rng.choice(["engineering", "support", "finance", "legal", "product"])
    marker = f"APP-MARKER-{run_id}-{index:06d}"
    created_at = "2026-06-02T00:00:00Z"
    body = (
        f"Synthetic app source {index} for ingestion scalability testing. "
        f"Department {dept}. Unique marker {marker}. Random note {rand_token(rng, 18)}."
    )
    return {
        "id": sid,
        "tenant_id": tenant_id,
        "sub_tenant_id": sub_tenant_id,
        "title": f"Synthetic Scale App Source {index}",
        "type": "notion",
        "kind": "knowledge_base",
        "provider": "notion",
        "external_id": f"scale-page-{run_id}-{index:06d}",
        "fields": {
            "kind": "knowledge_base",
            "title": f"Synthetic Scale App Source {index}",
            "body": body,
            "created_by": "ingestion-scale-test",
            "updated_by": "ingestion-scale-test",
            "created_at": created_at,
            "updated_at": created_at,
        },
        "metadata": {"department": dept},
        "additional_metadata": {"scale_run": run_id, "source_index": index, "dept": dept},
    }


def make_document_multipart(batch: list[dict[str, Any]], tenant_id: str, sub_tenant_id: str, upsert: bool) -> tuple[dict[str, str], list[tuple[str, str, bytes, str]]]:
    files = [
        ("documents", f"{item['id']}.md", item["text"].encode("utf-8"), "text/markdown")
        for item in batch
    ]
    metadata = [
        {
            "id": item["id"],
            "title": item["title"],
            "type": item["type"],
            "metadata": {"department": item["dept"]},
            "additional_metadata": {
                "scale_marker": item["marker"],
                "source_index": item["id"],
                "dept": item["dept"],
            },
        }
        for item in batch
    ]
    fields = {
        "type": "knowledge",
        "tenant_id": tenant_id,
        "sub_tenant_id": sub_tenant_id,
        "upsert": str(upsert).lower(),
        "document_metadata": json.dumps(metadata),
    }
    return fields, files


def make_app_multipart(batch: list[dict[str, Any]], tenant_id: str, sub_tenant_id: str, upsert: bool) -> tuple[dict[str, str], list[tuple[str, str, bytes, str]]]:
    fields = {
        "type": "knowledge",
        "tenant_id": tenant_id,
        "sub_tenant_id": sub_tenant_id,
        "upsert": str(upsert).lower(),
        "app_knowledge": json.dumps(batch),
    }
    return fields, []


def request_with_retries(
    client: ct.ApiClient,
    multipart: tuple[dict[str, str], list[tuple[str, str, bytes, str]]],
    retries: int,
    retry_sleep: float,
) -> ct.ApiResponse:
    attempts = max(1, retries)
    last: ct.ApiResponse | None = None
    for attempt in range(1, attempts + 1):
        resp = client._perform_safe(
            "POST",
            "/context/ingest",
            expected_statuses=(202,),
            multipart=multipart,
            validate_contract=False,
        )
        last = resp
        if resp.status not in TRANSIENT_STATUSES or attempt == attempts:
            return resp
        print(f"    transient HTTP {resp.status}; retry {attempt}/{attempts - 1}")
        time.sleep(retry_sleep * attempt)
    assert last is not None
    return last


def response_result_ids(resp: ct.ApiResponse) -> list[str]:
    if not isinstance(resp.data, dict):
        return []
    out: list[str] = []
    for item in resp.data.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        sid = item.get("id") or item.get("source_id")
        if isinstance(sid, str):
            out.append(sid)
    return out


def main() -> int:
    args = parse_args()
    if args.k > args.n:
        args.k = args.n

    out_dir = args.output_dir or RESULTS_ROOT / f"ingestion_scale_{args.run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    contract = ct.OpenApiContract(ct.OPENAPI_PATH, strict_extra_keys=False)
    client = ct.ApiClient(args.base_url, args.api_key, contract, ct.Recorder())
    rng = random.Random(args.run_id)

    if args.source_kind == "documents":
        sources = [make_doc_source(args.run_id, i, rng) for i in range(1, args.n + 1)]
    else:
        sources = [
            make_app_source(args.run_id, i, args.tenant_id, args.sub_tenant_id, rng)
            for i in range(1, args.n + 1)
        ]

    summary: dict[str, Any] = {
        "run_id": args.run_id,
        "base_url": args.base_url,
        "tenant_id": args.tenant_id,
        "sub_tenant_id": args.sub_tenant_id,
        "source_kind": args.source_kind,
        "n": args.n,
        "k": args.k,
        "upsert": args.upsert,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "batches": [],
    }

    print("=== HydraDB v2 ingestion scale test ===")
    print(f"Base URL:    {args.base_url}")
    print(f"Tenant:      {args.tenant_id}")
    print(f"Sub-tenant:  {args.sub_tenant_id}")
    print(f"Source kind: {args.source_kind}")
    print(f"Total n:     {args.n}")
    print(f"Batch k:     {args.k}")
    print(f"Results:     {out_dir}")
    print()

    accepted = 0
    returned_ids: list[str] = []
    t0 = time.perf_counter()

    for batch_no, start in enumerate(range(0, len(sources), args.k), 1):
        batch = sources[start : start + args.k]
        multipart = (
            make_document_multipart(batch, args.tenant_id, args.sub_tenant_id, args.upsert)
            if args.source_kind == "documents"
            else make_app_multipart(batch, args.tenant_id, args.sub_tenant_id, args.upsert)
        )

        bt0 = time.perf_counter()
        resp = request_with_retries(client, multipart, args.retries, args.retry_sleep)
        elapsed = time.perf_counter() - bt0
        ids = response_result_ids(resp)
        if resp.status == 202:
            accepted += len(batch)
        returned_ids.extend(ids)
        req_id = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
        batch_record = {
            "batch": batch_no,
            "start_index": start + 1,
            "count": len(batch),
            "http_status": resp.status,
            "elapsed_seconds": round(elapsed, 3),
            "request_id": req_id,
            "returned_ids_count": len(ids),
            "returned_ids_sample": ids[:5],
            "status_error": resp.status_error,
            "body": resp.data if isinstance(resp.data, dict) else resp.body_text[:1000],
        }
        summary["batches"].append(batch_record)
        print(
            f"batch {batch_no:04d}: {len(batch):4d} sources -> HTTP {resp.status} "
            f"in {elapsed:.2f}s returned={len(ids)} request_id={req_id or '-'}"
        )
        if resp.status != 202:
            print(f"  body: {resp.body_text[:500]}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    total_elapsed = time.perf_counter() - t0
    summary.update(
        {
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_elapsed_seconds": round(total_elapsed, 3),
            "accepted_sources_by_status": accepted,
            "returned_ids_count": len(returned_ids),
            "returned_ids_sample": returned_ids[:25],
            "throughput_sources_per_second": round(args.n / total_elapsed, 3) if total_elapsed > 0 else None,
        }
    )

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "source_ids.json").write_text(json.dumps([s["id"] for s in sources], indent=2))

    print("\n=== Summary ===")
    print(f"Accepted batches/sources by HTTP 202: {accepted}/{args.n}")
    print(f"Elapsed: {total_elapsed:.2f}s")
    print(f"Throughput: {summary['throughput_sources_per_second']} sources/s")
    print(f"Wrote: {out_dir / 'summary.json'}")

    failed_batches = [b for b in summary["batches"] if b["http_status"] != 202]
    return 1 if failed_batches else 0


if __name__ == "__main__":
    raise SystemExit(main())
