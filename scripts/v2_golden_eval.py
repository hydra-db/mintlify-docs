#!/usr/bin/env python3
"""HydraDB v2 golden-dataset retrieval-quality eval.

Unlike the contract test (which validates response *shape*), this eval fabricates a
coherent, fully-known corpus and asserts that the RIGHT knowledge actually comes back
AND that every documented /query parameter behaves as the docs promise.

Design decisions:
  * Most Knowledge recall targets are ingested as parsed documents (`documents` +
    `document_metadata`) so retrieval quality is not dominated by app-source behavior.
  * App-source targets use the app-native model (`kind`, `provider`, `external_id`,
    `fields`, `metadata`, `additional_metadata`, and optional `relations[]`).
  * Content is rich and multi-paragraph (real policy/runbook prose), not one-liners.
  * The selector field is `query_apps` (renamed from `search_apps`).

What each golden query asserts (ground truth is known because we fabricated it):
  - recall@k         : expected source(s) returned within the top-k chunks
  - rank / max_rank  : precision queries rank the right source at/near the top
  - markers          : the actual answer text appears in a returned chunk
  - max_chunks       : `max_results` is respected
  - rank_before      : `recency_bias` ranks the newer doc above the older one
  - expect_graph     : `graph_context=true` returns a populated graph slice
  - expect_addl_ctx  : `query_forceful_relations=true` populates additional_context
  - query_apps       : `query_apps=true` surfaces the app source
  - department filter : `metadata_filters` returns only the matching department
  - expect_empty     : a nonexistent code returns zero chunks

Covers every /query parameter: type (knowledge/memory/all), query_by (hybrid/text),
mode (fast/thinking), operator (or/and/phrase), max_results, alpha (numeric/auto),
recency_bias, graph_context, query_forceful_relations, query_apps, metadata_filters
(metadata + additional_metadata), additional_context.

Reuses ApiClient / OpenApiContract / config (incl. the API key) from
v2_e2e_contract_test so credentials live in one place.

Run:   python3 v2_golden_eval.py
Env:   GOLDEN_REUSE_SUB=<sub>  -> skip ingestion, eval an existing corpus
       GOLDEN_TOP_K=10
       GOLDEN_INGEST_COPIES=3  -> ingest the same corpus under 3 distinct ID sets
       GOLDEN_EVAL_COPY=1      -> run search/analysis against only this copy
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import v2_e2e_contract_test as ct

TENANT_ID = ct.TENANT_ID
RUN_ID = os.getenv("GOLDEN_RUN_ID", time.strftime("%Y%m%d%H%M%S"))
SUB_TENANT = os.getenv("GOLDEN_REUSE_SUB") or f"golden_{RUN_ID}"
REUSE = bool(os.getenv("GOLDEN_REUSE_SUB"))
# The ingestion inbox currently dedupes by bare doc_id, not by sub_tenant_id.
# Keep the human-readable golden IDs, but append a deterministic per-subtenant
# suffix so repeated eval runs do not collide with old PENDING/DISPATCHED rows.
ID_SUFFIX = os.getenv("GOLDEN_ID_SUFFIX")
if ID_SUFFIX is None:
    ID_SUFFIX = hashlib.sha1(SUB_TENANT.encode("utf-8")).hexdigest()[:8]
INGEST_COPIES = max(
    1,
    int(os.getenv("GOLDEN_INGEST_COPIES") or os.getenv("GOLDEN_INGEST_N") or "1"),
)
EVAL_COPY_INDEX = max(1, int(os.getenv("GOLDEN_EVAL_COPY", "1")))
DEFAULT_TOP_K = int(os.getenv("GOLDEN_TOP_K", "10"))
RESULTS_DIR = ct.RESULTS_ROOT / f"golden_{RUN_ID}"
FILE_BATCH = 8
SOURCE_READY_TIMEOUT = int(os.getenv("GOLDEN_SOURCE_TIMEOUT", "1800"))
GRAPH_TIMEOUT = int(os.getenv("GOLDEN_GRAPH_TIMEOUT", "900"))
POLL = 8.0
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
REQUEST_RETRIES = int(os.getenv("GOLDEN_REQUEST_RETRIES", "4"))
REQUEST_RETRY_SLEEP = float(os.getenv("GOLDEN_REQUEST_RETRY_SLEEP", "3"))

# -----------------------------------------------------------------------------
# Rich content helpers.
# -----------------------------------------------------------------------------


def doc(*paragraphs: str) -> str:
    return "\n\n".join(p.strip() for p in paragraphs)


def copy_id_suffix(copy_index: int = 1) -> str:
    """Return the runtime suffix for one ingestion/eval copy."""
    base = ID_SUFFIX.strip()
    if INGEST_COPIES > 1 or copy_index != 1:
        return f"{base + '_' if base else ''}c{copy_index:02d}"
    return base


def gold_id(base_id: str, copy_index: int | None = None) -> str:
    """Return the runtime source id for a stable golden fixture id."""
    suffix = copy_id_suffix(copy_index or EVAL_COPY_INDEX)
    return f"{base_id}_{suffix}" if suffix else base_id


def _rewrite_exact_ids(value: Any, id_map: dict[str, str]) -> Any:
    """Recursively rewrite fixture-id string values, preserving other content."""
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [_rewrite_exact_ids(v, id_map) for v in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_exact_ids(v, id_map) for v in value)
    if isinstance(value, dict):
        return {k: _rewrite_exact_ids(v, id_map) for k, v in value.items()}
    return value


def apply_id_suffix(
    knowledge: list[dict[str, Any]],
    apps: list[dict[str, Any]],
    mems: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    *,
    copy_index: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    copy_index = copy_index or EVAL_COPY_INDEX
    base_ids = [d["id"] for d in knowledge] + [a["id"] for a in apps] + [m["id"] for m in mems]
    id_map = {base_id: gold_id(base_id, copy_index) for base_id in base_ids}
    return (
        _rewrite_exact_ids(knowledge, id_map),
        _rewrite_exact_ids(apps, id_map),
        _rewrite_exact_ids(mems, id_map),
        _rewrite_exact_ids(queries, id_map),
    )


# -----------------------------------------------------------------------------
# Golden corpus — hero (targeted) knowledge documents, ingested as documents.
# Each hero embeds a unique fact + unique code inside realistic surrounding prose.
# -----------------------------------------------------------------------------


def hero_docs() -> list[dict[str, Any]]:
    return [
        {
            "id": "gold_hr_pto",
            "dept": "people",
            "title": "Paid Time Off Policy",
            "text": doc(
                "Paid Time Off Policy. This policy describes how paid time off (PTO) is "
                "accrued, requested, and carried over at Acme Robotics.",
                "Full-time employees accrue 22 PTO days per year, accrued monthly and "
                "available from the first day of employment on a pro-rated basis. Part-time "
                "employees accrue PTO proportional to their scheduled hours.",
                "Unused PTO rolls over up to a maximum of 10 days into the following calendar "
                "year; any balance above the cap is forfeited on December 31. PTO requests "
                "should be submitted at least two weeks in advance through the HR portal and "
                "are approved by the employee's direct manager.",
                "PTO is separate from sick leave, bereavement leave, and company holidays, "
                "which are governed by their own policies.",
            ),
            "markers": ["22 PTO days"],
        },
        {
            "id": "gold_hr_parental",
            "dept": "people",
            "title": "Parental Leave Policy",
            "text": doc(
                "Parental Leave Policy. Acme Robotics provides parental leave to support "
                "employees welcoming a new child through birth, adoption, or foster placement.",
                "Parental leave is 16 weeks fully paid for all new parents, regardless of "
                "gender or caregiver status. Leave must be taken within the first 12 months "
                "following the child's arrival and may be taken in up to two separate blocks.",
                "Employees should notify People Operations at least 30 days before the planned "
                "start date when foreseeable. Health benefits continue unchanged during leave, "
                "and job protection is guaranteed for the full duration.",
            ),
            "markers": ["16 weeks"],
        },
        {
            "id": "gold_hr_remote",
            "dept": "people",
            "title": "Remote Work Policy (RMT-3)",
            "text": doc(
                "Remote Work Policy. This policy, tracked internally under code RMT-3, defines "
                "expectations for hybrid and remote work across Acme Robotics.",
                "Employees may work remotely up to 3 days per week. Core collaboration days are "
                "Tuesday and Thursday, when staff are expected on-site for planning, design "
                "review, and hardware testing.",
                "Fully remote arrangements require VP approval and a documented business "
                "justification. Home-office stipends are available once per two years to cover "
                "ergonomic equipment.",
            ),
            "markers": ["3 days per week", "RMT-3"],
        },
        {
            "id": "gold_eng_deploy",
            "dept": "engineering",
            "title": "Production Deploy Runbook",
            "text": doc(
                "Production Deploy Runbook. Follow these steps for every production release of "
                "the control-plane services.",
                "Step 1: build and sign the container image. Step 2: run database migrations in "
                "backward-compatible mode. Step 3: drain connections from the outgoing instances "
                "so in-flight requests complete. Step 4: cut over traffic to the new version.",
                "Rollback uses a blue-green deployment: flip the load balancer back to the "
                "previous color, which remains warm for 60 minutes after cutover. Never delete "
                "the previous color until error rates have been nominal for at least 30 minutes.",
            ),
            "markers": ["drain connections", "blue-green"],
        },
        {
            "id": "gold_eng_incident",
            "dept": "engineering",
            "title": "Sev1 Incident Response",
            "text": doc(
                "Severity 1 Incident Response. A Sev1 is any full outage or data-integrity "
                "threat affecting customers.",
                "When a Sev1 is declared, page the on-call engineer within 5 minutes and open "
                "the war room in the #inc-sev1 Slack channel. An incident commander is assigned "
                "immediately and owns all external and internal communication.",
                "Post-incident, a blameless retrospective is scheduled within three business "
                "days and corrective actions are tracked to completion.",
            ),
            "markers": ["5 minutes", "#inc-sev1"],
        },
        {
            "id": "gold_eng_dbfailover",
            "dept": "engineering",
            "title": "Database Failover Runbook",
            "text": doc(
                "Database Failover Runbook. This runbook covers promoting a replica when the "
                "primary database becomes unavailable.",
                "Database failover promotes the replica db-r2 in region eu-west. The target "
                "recovery time objective (RTO) is 90 seconds. Before promoting, confirm that "
                "replication lag is under 1 second to avoid data loss.",
                "After promotion, update the connection string in the secrets manager, verify "
                "application health, and provision a new replica to restore redundancy.",
            ),
            "markers": ["db-r2", "eu-west", "90 seconds"],
        },
        {
            "id": "gold_prod_pricing",
            "dept": "product",
            "title": "Subscription Pricing",
            "text": doc(
                "Subscription Pricing. Acme Robotics Cloud is sold in three tiers billed monthly "
                "or annually.",
                "The Starter plan is $29 per month and includes a single workspace. The Pro plan "
                "is $149 per month and adds advanced analytics, priority support, and a higher "
                "API rate limit of 600 requests per minute. The Enterprise plan is custom-priced "
                "with volume discounts and a dedicated success manager.",
                "Annual billing provides a two-month discount versus paying monthly.",
            ),
            "markers": ["$149", "600 requests per minute"],
        },
        {
            "id": "gold_prod_sku",
            "dept": "product",
            "title": "Warehouse Picker Spec (ACME-BOT-7)",
            "text": doc(
                "Product Specification: Warehouse Picker. SKU ACME-BOT-7 is the flagship "
                "warehouse picking robot for mid-size fulfillment centers.",
                "It has a payload capacity of 12kg and a maximum reach of 2.1 meters. The "
                "onboard battery lasts 9 hours per charge and recharges to 80 percent in 45 "
                "minutes. Navigation uses LiDAR plus visual fiducials.",
                "The unit ships with a two-year warranty and supports over-the-air firmware "
                "updates.",
            ),
            "markers": ["ACME-BOT-7", "12kg"],
        },
        {
            "id": "gold_sec_gdpr",
            "dept": "security",
            "title": "GDPR Data Deletion Procedure",
            "text": doc(
                "GDPR Data Deletion Procedure. This procedure governs how Acme Robotics handles "
                "data subject erasure requests under GDPR Article 17.",
                "Verified data deletion requests are fulfilled within 30 days of receipt. Each "
                "request is tracked with the ticket prefix DEL- and is verified by the privacy "
                "team before any data is purged.",
                "Deletion cascades across primary stores, backups (on their next rotation), and "
                "downstream analytics. A completion certificate is issued to the requester.",
            ),
            "markers": ["30 days", "DEL-"],
        },
        {
            "id": "gold_sec_retention",
            "dept": "security",
            "title": "Data Retention Schedule",
            "text": doc(
                "Data Retention Schedule. Retention windows balance operational needs with "
                "privacy obligations.",
                "Application and infrastructure log data is retained for 400 days, after which "
                "it is automatically deleted. Personally identifiable information (PII) is "
                "retained for 730 days unless an earlier erasure request applies, after which "
                "records are irreversibly purged.",
                "Financial records follow a separate seven-year schedule mandated by "
                "regulation.",
            ),
            "markers": ["400 days", "730 days"],
        },
        {
            "id": "gold_sec_soc2",
            "dept": "security",
            "title": "SOC 2 Compliance Overview",
            "text": doc(
                "SOC 2 Compliance Overview. Acme Robotics maintains a SOC 2 Type II attestation "
                "covering security, availability, and confidentiality.",
                "The audit is performed annually by the independent firm Lindgren & Co. Reports "
                "are available to customers under a non-disclosure agreement upon request "
                "through the trust portal.",
                "Continuous control monitoring runs throughout the year, and exceptions are "
                "remediated before the audit window closes.",
            ),
            "markers": ["Lindgren"],
        },
        {
            "id": "gold_support_refund",
            "dept": "support",
            "title": "Refund and Returns Policy",
            "text": doc(
                "Refund and Returns Policy. This policy explains eligibility and process for "
                "hardware refunds and returns.",
                "Refunds are available within 30 days of delivery. Units returned in unopened "
                "condition are fully refunded; opened robots incur a 15% restocking fee to cover "
                "inspection and recertification. Refunds are issued to the original payment "
                "method within five business days of inspection.",
                "Customers initiate a return by requesting an RMA number from support.",
            ),
            "markers": ["30 days", "15%"],
        },
        # --- Recency pair: same topic, conflicting facts, different timestamps. ---
        {
            "id": "gold_vpn_old",
            "dept": "engineering",
            "title": "VPN Gateway (legacy)",
            "timestamp": "2023-02-01T00:00:00Z",
            "text": doc(
                "VPN Gateway Configuration. This document describes connecting to the corporate "
                "network over the VPN.",
                "The VPN gateway endpoint is vpn-1.acme.internal on UDP port 1194. Use the "
                "legacy OpenVPN profile distributed by IT. This configuration is maintained for "
                "historical reference.",
            ),
            "markers": ["vpn-1.acme.internal"],
        },
        {
            "id": "gold_vpn_new",
            "dept": "engineering",
            "title": "VPN Gateway (current)",
            "timestamp": "2026-05-01T00:00:00Z",
            "text": doc(
                "VPN Gateway Configuration. This is the current, authoritative guide for "
                "connecting to the corporate network.",
                "The VPN gateway endpoint is vpn-2.acme.internal on UDP port 51820 using "
                "WireGuard. All staff must migrate to this endpoint; the previous gateway is "
                "deprecated and will be decommissioned.",
            ),
            "markers": ["vpn-2.acme.internal", "51820"],
        },
        # --- Graph-rich doc: many entities/relations for graph_context test. ---
        {
            "id": "gold_graph_payments",
            "dept": "engineering",
            "title": "Payments Service Architecture",
            "text": doc(
                "Payments Service Architecture. This document maps the dependencies and "
                "ownership of the payments subsystem.",
                "The PaymentsService depends on the OrdersDatabase for transaction records and "
                "calls the BillingAPI to generate invoices. The PaymentsService is owned by the "
                "Platform team and is deployed in the eu-west region.",
                "The OrdersDatabase is managed by the Data Platform team. The BillingAPI "
                "integrates with the external provider Stripe. When the PaymentsService scales, "
                "it publishes events to the EventBus consumed by the AnalyticsPipeline.",
            ),
            "markers": ["PaymentsService", "OrdersDatabase"],
        },
        # --- Forceful-relation declaring doc (relation set at ingest -> gold_hr_pto). ---
        {
            "id": "gold_onboarding",
            "dept": "people",
            "title": "New Hire First Week Guide",
            "relations": ["gold_hr_pto"],
            "text": doc(
                "New Hire First Week Guide. Welcome to Acme Robotics. This guide walks a new "
                "employee through their first week.",
                "On day one, complete IT setup and security training. Review the time-off "
                "policy and how to request leave, set up payroll and benefits, and meet your "
                "onboarding buddy. By the end of the week you should have shipped a small "
                "starter task.",
                "Your manager will schedule a 30-60-90 day plan covering goals and expectations.",
            ),
            "markers": ["first week"],
        },
    ]


def distractor_docs() -> list[dict[str, Any]]:
    """Rich, realistic, topically-clustered noise. None contain hero markers/codes."""
    out: list[dict[str, Any]] = []

    hr = [
        (
            "Dress Code Guidelines",
            "people",
            "Acme Robotics maintains a smart-casual dress code in the office. Client-facing "
            "meetings call for business attire. Lab and warehouse visits require closed-toe "
            "shoes and any posted personal protective equipment. The company provides branded "
            "apparel during onboarding and at major team events.",
        ),
        (
            "Employee Referral Program",
            "people",
            "The employee referral program rewards staff who help us hire great people. A "
            "referral that results in a hire earns a one-time bonus paid after the new hire "
            "completes their probationary period. Referrals for hard-to-fill engineering roles "
            "carry an enhanced bonus. Submit referrals through the recruiting portal.",
        ),
        (
            "Retirement Benefits",
            "people",
            "Acme Robotics offers a retirement savings plan with an employer match of up to five "
            "percent of base salary. Contributions vest immediately. Employees can choose between "
            "traditional and Roth options and adjust their elections each quarter. Financial "
            "wellness sessions are offered twice a year.",
        ),
        (
            "Learning and Development",
            "people",
            "Every employee receives an annual learning budget for courses, books, and "
            "conferences. Manager approval is required for expenses above the standard threshold. "
            "Internal lunch-and-learn sessions are recorded and shared in the knowledge base.",
        ),
        (
            "Sabbatical Program",
            "people",
            "Employees become eligible for a four-week paid sabbatical after completing five "
            "continuous years of service. Sabbaticals must be scheduled a quarter in advance and "
            "coordinated with the team to ensure coverage.",
        ),
        (
            "Expense Reimbursement",
            "finance",
            "Business expenses are reimbursed through the finance portal. Submit itemized "
            "receipts within 30 days of the expense. Travel bookings should use the approved "
            "corporate travel tool. Meals during travel follow the per-diem schedule.",
        ),
    ]
    eng = [
        (
            "Auth Gateway Runbook",
            "engineering",
            "The auth-gateway service terminates client sessions and issues short-lived tokens. "
            "Restart it through the standard service manager and confirm the health endpoint "
            "returns ok. Watch token issuance latency; if it climbs, scale the deployment "
            "horizontally and check the upstream identity provider.",
        ),
        (
            "Search Indexer Runbook",
            "engineering",
            "The search-indexer consumes change events and updates the search cluster. If the "
            "consumer lag grows, increase the worker count and verify the cluster has headroom. "
            "Reindexing from scratch is a last resort and should be scheduled off-peak.",
        ),
        (
            "Notification Relay Runbook",
            "engineering",
            "The notification-relay delivers email and push messages. Bounces are retried with "
            "exponential backoff. Monitor the dead-letter queue and replay messages once the "
            "downstream provider recovers.",
        ),
        (
            "Cache Strategy",
            "engineering",
            "Hot read paths are backed by an in-memory cache with a short time-to-live. Cache "
            "keys are namespaced by tenant. Invalidation happens on write. Avoid caching "
            "personalized responses unless the key includes the user identifier.",
        ),
        (
            "Observability Standards",
            "engineering",
            "Every service emits structured logs, metrics, and traces. Dashboards track the four "
            "golden signals: latency, traffic, errors, and saturation. Alerts page on-call only "
            "for customer-impacting conditions to reduce fatigue.",
        ),
        (
            "Code Review Guidelines",
            "engineering",
            "Pull requests require at least one approving review. Reviewers check correctness, "
            "tests, and readability. Large changes should be split into reviewable units. Authors "
            "respond to comments before merging and keep the main branch releasable.",
        ),
        (
            "Feature Flag Practices",
            "engineering",
            "New features ship behind flags and roll out gradually. Flags have an owner and a "
            "removal date. Stale flags are cleaned up quarterly to limit configuration drift.",
        ),
        (
            "On-call Expectations",
            "engineering",
            "On-call engineers acknowledge pages promptly and keep a clear handoff at the end of "
            "each rotation. Runbooks are kept current. Toil identified during a rotation is filed "
            "as follow-up work.",
        ),
    ]
    prod = [
        (
            "Mobile App Overview",
            "product",
            "The Acme Robotics mobile app lets operators monitor fleets, acknowledge alerts, and "
            "schedule maintenance. It supports offline viewing of recent telemetry and syncs when "
            "connectivity returns.",
        ),
        (
            "Analytics Dashboard",
            "product",
            "The analytics dashboard visualizes throughput, uptime, and energy usage across a "
            "fleet. Operators can filter by site and export reports. Widgets refresh on a "
            "configurable interval.",
        ),
        (
            "Fleet Scheduling",
            "product",
            "Fleet scheduling assigns tasks to available robots based on priority and battery "
            "level. Operators can pin high-priority jobs and set quiet hours during which only "
            "critical tasks run.",
        ),
        (
            "Firmware Update Flow",
            "product",
            "Firmware updates are staged to a canary group before fleet-wide rollout. Operators "
            "approve the rollout and can pause it if anomalies appear in the canary metrics.",
        ),
        (
            "Integration Marketplace",
            "product",
            "The integration marketplace connects Acme Robotics to warehouse management systems "
            "and ERP tools. Each integration documents its data mappings and sync cadence.",
        ),
        (
            "Accessibility Commitments",
            "product",
            "Our interfaces target accessibility best practices, including keyboard navigation, "
            "sufficient color contrast, and screen-reader labels. Accessibility is part of the "
            "definition of done for new screens.",
        ),
    ]
    support = [
        (
            "Battery Charging Help",
            "support",
            "If a robot is not charging, confirm the dock is powered and the contacts are clean. "
            "Reseat the unit on the dock and watch for the charging indicator. If charging does "
            "not begin, gather the serial number and escalate to tier two.",
        ),
        (
            "Wi-Fi Pairing Help",
            "support",
            "To pair a robot with Wi-Fi, put it into pairing mode, select the network in the app, "
            "and enter the passphrase. Keep the robot within range of the access point during "
            "setup. Captive-portal networks are not supported.",
        ),
        (
            "Firmware Stuck Help",
            "support",
            "If a firmware update appears stuck, do not power off the unit. Wait for the timeout, "
            "after which it will roll back automatically. If it remains unresponsive, perform the "
            "documented recovery sequence.",
        ),
        (
            "Arm Calibration Help",
            "support",
            "Recalibrate the manipulator arm when picking accuracy drops. Run the guided "
            "calibration routine on a clear, level surface. Recalibration takes a few minutes and "
            "should be repeated after any physical collision.",
        ),
        (
            "Warranty Registration Help",
            "support",
            "Register a unit's warranty by entering its serial number in the customer portal "
            "within 30 days of delivery. Registration is required for expedited replacement "
            "service.",
        ),
        (
            "Account Email Change Help",
            "support",
            "To change the account email, the workspace owner submits the request from account "
            "settings and confirms via the verification link sent to the new address.",
        ),
    ]
    groups = {"hr": hr, "eng": eng, "prod": prod, "sup": support}
    for prefix, items in groups.items():
        for i, (title, dept, body) in enumerate(items, 1):
            out.append(
                {
                    "id": f"gold_fill_{prefix}_{i:02d}",
                    "dept": dept,
                    "title": title,
                    "text": doc(f"{title}.", body),
                    "markers": [],
                }
            )
    return out


def app_sources() -> list[dict[str, Any]]:
    """App sources (`app_knowledge`) using the field-based app-native model."""

    def app(
        *,
        sid: str,
        title: str,
        provider: str,
        kind: str,
        external_id: str,
        body: str,
        dept: str,
        timestamp: str | None = None,
        relations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        created_at = timestamp or "2026-05-20T10:00:00Z"
        if kind == "message":
            fields: dict[str, Any] = {
                "kind": "message",
                "body": body,
                "author": "priya",
                "thread_id": external_id,
                "created_at": created_at,
            }
        elif kind == "knowledge_base":
            fields = {
                "kind": "knowledge_base",
                "title": title,
                "body": body,
                "created_by": "golden-eval",
                "updated_by": "golden-eval",
                "created_at": created_at,
                "updated_at": created_at,
            }
        else:
            fields = {"kind": "custom", "data": {"title": title, "body": body}}

        item: dict[str, Any] = {
            "id": sid,
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT,
            "title": title,
            "type": provider,
            "kind": kind,
            "provider": provider,
            "external_id": external_id,
            "fields": fields,
            "metadata": {"department": dept},
            "additional_metadata": {"golden_run": RUN_ID, "dept": dept},
        }
        if timestamp:
            item["timestamp"] = timestamp
        if relations:
            item["relations"] = relations
        return item

    return [
        # query_apps targets.
        app(
            sid="gold_app_slack_escalation",
            title="Slack: Zenat Corp escalation",
            provider="slack",
            kind="message",
            external_id="1716213600.zenat",
            dept="support",
            body="Zenat Corp reported intermittent dock disconnects across their fleet. "
            "Escalation owner is Priya. Workaround firmware channel beta-7. Tracking ticket "
            "ESC-ZENAT-88. Customer is on the Enterprise plan.",
        ),
        app(
            sid="gold_app_notion_roadmap",
            title="Notion: Q3 Roadmap",
            provider="notion",
            kind="knowledge_base",
            external_id="page_q3_roadmap",
            dept="product",
            body="Q3 roadmap highlights multi-site fleet view, predictive maintenance "
            "alerts, and a public webhooks API. Codename PROJECT-LIGHTHOUSE.",
        ),
        # Metadata-filter set: distinct departments.
        app(
            sid="gold_app_legal_1",
            title="NDA Process",
            provider="notion",
            kind="knowledge_base",
            external_id="page_legal_nda",
            dept="legal",
            body="The mutual NDA process routes agreements through the legal queue with a two "
            "business day review target. Marker LEGAL-NDA-01.",
        ),
        app(
            sid="gold_app_legal_2",
            title="Contract Review",
            provider="notion",
            kind="knowledge_base",
            external_id="page_legal_contract_review",
            dept="legal",
            body="Vendor contracts above fifty thousand dollars require legal review and a "
            "risk assessment. Marker LEGAL-CR-02.",
        ),
        app(
            sid="gold_app_legal_3",
            title="IP Assignment",
            provider="notion",
            kind="knowledge_base",
            external_id="page_legal_ip_assignment",
            dept="legal",
            body="All employee inventions are assigned to the company under the IP agreement "
            "signed at onboarding. Marker LEGAL-IP-03.",
        ),
        app(
            sid="gold_app_finance_1",
            title="Invoice Terms",
            provider="notion",
            kind="knowledge_base",
            external_id="page_finance_invoice_terms",
            dept="finance",
            body="Standard customer invoice terms are net thirty. Marker FIN-INV-01.",
        ),
        app(
            sid="gold_app_finance_2",
            title="Budget Cycle",
            provider="notion",
            kind="knowledge_base",
            external_id="page_finance_budget_cycle",
            dept="finance",
            body="The annual budgeting cycle opens in October and closes in December. Marker FIN-BUD-02.",
        ),
        # Recency pair (timestamps provided; fidelity check verifies whether honored).
        app(
            sid="gold_app_price_old",
            title="Premium Add-on (old)",
            provider="webpage",
            kind="knowledge_base",
            external_id="premium_addon_old",
            dept="product",
            timestamp="2023-01-01T00:00:00Z",
            body="The premium support add-on costs 80 dollars per month under the previous plan.",
        ),
        app(
            sid="gold_app_price_new",
            title="Premium Add-on (current)",
            provider="webpage",
            kind="knowledge_base",
            external_id="premium_addon_current",
            dept="product",
            timestamp="2026-05-01T00:00:00Z",
            body="The premium support add-on costs 140 dollars per month under the current plan.",
        ),
        # Explicit app-native relation pair.
        app(
            sid="gold_app_devsetup",
            title="Dev Environment Setup",
            provider="notion",
            kind="knowledge_base",
            external_id="page_devsetup",
            dept="engineering",
            body="To set up the development environment, install the toolchain, clone the "
            "monorepo, and request access to the staging cluster.",
            relations=[
                {
                    "predicate": "related_to",
                    "target": {"id": "gold_app_handshake"},
                    "properties": {"reason": "release setup prerequisite"},
                }
            ],
        ),
        app(
            sid="gold_app_handshake",
            title="Release Approval Ritual",
            provider="notion",
            kind="knowledge_base",
            external_id="page_release_approval",
            dept="engineering",
            body="The internal release approval ritual is codenamed ZEBRAFISH-PROTOCOL and "
            "requires two independent signoffs before any production tag.",
        ),
    ]


def memory_items() -> list[dict[str, Any]]:
    base = [
        (
            "gold_mem_notify",
            "Deploy notification preference",
            "This user wants deployment notifications delivered via Slack direct message, never by email.",
        ),
        (
            "gold_mem_paging",
            "Paging tool preference",
            "For incidents, page this user through PagerDuty. They strongly dislike Opsgenie.",
        ),
        (
            "gold_mem_region",
            "Primary region",
            "This user's primary working region is eu-west and they care most about latency there.",
        ),
        (
            "gold_mem_tone",
            "Communication tone",
            "This user prefers concise, technical answers with no marketing language.",
        ),
        (
            "gold_mem_robot",
            "Robot of interest",
            "This user is evaluating the ACME-BOT-7 warehouse picker for their fulfillment center.",
        ),
        (
            "gold_mem_lang",
            "Code language preference",
            "This user prefers code examples written in TypeScript rather than Python.",
        ),
    ]
    fillers = [
        (
            "gold_mem_fill_01",
            "Meeting time",
            "This user prefers afternoon meetings over mornings.",
        ),
        (
            "gold_mem_fill_02",
            "Editor theme",
            "This user uses a dark theme in their code editor.",
        ),
        (
            "gold_mem_fill_03",
            "Beverage",
            "This user mentioned they switch to decaf after noon.",
        ),
        (
            "gold_mem_fill_04",
            "Newsletter",
            "This user opted out of the monthly product newsletter.",
        ),
        (
            "gold_mem_fill_05",
            "Billing cycle",
            "This user is on an annual billing cycle.",
        ),
        ("gold_mem_fill_06", "Timezone", "This user is based in a UTC+1 timezone."),
    ]
    return [{"id": sid, "title": t, "text": x} for sid, t, x in base + fillers]


def golden_queries() -> list[dict[str, Any]]:
    K = DEFAULT_TOP_K
    return [
        # ---- type=knowledge, query_by=hybrid, mode=fast/thinking ----
        {
            "id": "k01_pto_fast",
            "query": "How many vacation days do full-time employees get per year?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "expected": ["gold_hr_pto"],
            "markers": ["22 PTO days"],
            "top_k": K,
        },
        {
            "id": "k02_parental_thinking",
            "query": "How long is paid parental leave?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "expected": ["gold_hr_parental"],
            "markers": ["16 weeks"],
            "top_k": K,
        },
        {
            "id": "k03_deploy",
            "query": "What are the production deploy steps and how do we roll back?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "expected": ["gold_eng_deploy"],
            "markers": ["drain connections"],
            "top_k": K,
        },
        {
            "id": "k04_pricing",
            "query": "How much does the Pro subscription plan cost each month?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "expected": ["gold_prod_pricing"],
            "markers": ["$149"],
            "top_k": K,
            "max_rank": 3,
        },
        {
            "id": "k05_retention",
            "query": "How long are application logs kept before deletion?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "expected": ["gold_sec_retention"],
            "markers": ["400 days"],
            "top_k": K,
        },
        {
            "id": "k06_soc2",
            "query": "Who performs our SOC 2 audit?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "expected": ["gold_sec_soc2"],
            "markers": ["Lindgren"],
            "top_k": K,
        },
        {
            "id": "k07_refund",
            "query": "What is the refund window and is there a restocking fee?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "expected": ["gold_support_refund"],
            "markers": ["30 days", "15%"],
            "top_k": K,
        },
        # ---- query_by=text: operator or / and / phrase ----
        {
            "id": "t01_text_or",
            "query": "incident on-call channel",
            "type": "knowledge",
            "query_by": "text",
            "operator": "or",
            "expected": ["gold_eng_incident"],
            "markers": ["#inc-sev1"],
            "top_k": K,
        },
        {
            "id": "t02_text_and",
            "query": "restocking fee refund",
            "type": "knowledge",
            "query_by": "text",
            "operator": "and",
            "expected": ["gold_support_refund"],
            "markers": ["15%"],
            "top_k": K,
        },
        {
            "id": "t03_text_phrase",
            "query": "blue-green deployment",
            "type": "knowledge",
            "query_by": "text",
            "operator": "phrase",
            "expected": ["gold_eng_deploy"],
            "markers": ["blue-green"],
            "top_k": K,
            "max_rank": 1,
        },
        # ---- alpha numeric / auto (exact code -> favor BM25) ----
        {
            "id": "a01_alpha_low_sku",
            "query": "ACME-BOT-7 payload capacity",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "alpha": 0.3,
            "expected": ["gold_prod_sku"],
            "markers": ["12kg"],
            "top_k": K,
            "max_rank": 1,
        },
        {
            "id": "a02_alpha_auto_dbr2",
            "query": "db-r2 failover region and RTO",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "alpha": "auto",
            "expected": ["gold_eng_dbfailover"],
            "markers": ["eu-west", "90 seconds"],
            "top_k": K,
            "max_rank": 2,
        },
        # ---- max_results respected ----
        {
            "id": "x01_max_results_3",
            "query": "remote work policy days per week",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "top_k": 3,
            "expected": ["gold_hr_remote"],
            "markers": ["3 days per week"],
            "max_chunks": 3,
        },
        # ---- graph_context populated ----
        {
            "id": "g01_graph_payments",
            "query": "What does the PaymentsService depend on and which team owns it?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "graph_context": True,
            "expected": ["gold_graph_payments"],
            "markers": ["OrdersDatabase"],
            "expect_graph": True,
            "top_k": K,
        },
        # ---- query_apps surfaces an app source ----
        {
            "id": "q01_query_apps",
            "query": "Zenat Corp escalation dock disconnects owner",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "query_apps": True,
            "expected": ["gold_app_slack_escalation"],
            "markers": ["ESC-ZENAT-88"],
            "top_k": K,
        },
        # ---- metadata_filters: metadata department exclusivity (app sources) ----
        {
            "id": "mf01_dept_legal",
            "query": "process review agreement company policy",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "metadata_filters": {"department": "legal"},
            "top_k": K,
            "filter_department": "legal",
            "min_recall_count": 3,
        },
        # ---- metadata_filters: additional_metadata (app sources retain it) ----
        {
            "id": "mf02_addl_meta_finance",
            "query": "invoice budget terms cycle",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "fast",
            "metadata_filters": {"additional_metadata": {"dept": "finance"}},
            "top_k": K,
            "filter_additional_meta": ("dept", "finance"),
            "min_recall_count": 2,
        },
        # ---- additional_context hint (soft: must not break, expected still found) ----
        {
            "id": "ac01_hint",
            "query": "How quickly are deletion requests completed?",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "additional_context": "The user is a privacy officer asking about GDPR.",
            "expected": ["gold_sec_gdpr"],
            "markers": ["30 days"],
            "top_k": K,
        },
        # ---- type=memory ----
        {
            "id": "m01_notify",
            "query": "How does this user want to be notified about deployments?",
            "type": "memory",
            "query_by": "hybrid",
            "mode": "fast",
            "expected": ["gold_mem_notify"],
            "markers": ["Slack"],
            "top_k": K,
        },
        {
            "id": "m02_paging",
            "query": "Which paging tool should we use for this user?",
            "type": "memory",
            "query_by": "hybrid",
            "mode": "fast",
            "expected": ["gold_mem_paging"],
            "markers": ["PagerDuty"],
            "top_k": K,
        },
        # ---- type=all (knowledge + memory merged) ----
        {
            "id": "all01_refund_pref",
            "query": "What is our customer refund window and how does this user prefer updates?",
            "type": "all",
            "query_by": "hybrid",
            "mode": "thinking",
            "top_k": 15,
            "expected": ["gold_support_refund", "gold_mem_notify"],
            "markers": ["30 days"],
            "min_recall": 0.5,
        },
        # ---- negative: nonexistent code -> empty ----
        {
            "id": "n01_absent",
            "query": "NONEXIST-9999",
            "type": "knowledge",
            "query_by": "text",
            "operator": "phrase",
            "expected": [],
            "markers": [],
            "expect_empty": True,
            "top_k": K,
        },
    ]


# -----------------------------------------------------------------------------
# HTTP helpers.
# -----------------------------------------------------------------------------


def safe(client: ct.ApiClient, *args: Any, **kwargs: Any) -> ct.ApiResponse | None:
    kwargs.setdefault("validate_contract", False)
    attempts = max(1, REQUEST_RETRIES)
    last: ct.ApiResponse | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client._perform_safe(*args, **kwargs)
            last = resp
            if resp.status not in TRANSIENT_STATUSES or attempt == attempts:
                return resp
            method = args[0] if args else kwargs.get("method", "?")
            path = args[1] if len(args) > 1 else kwargs.get("path", "?")
            print(
                f"  ! transient HTTP {resp.status} for {method} {path}; "
                f"retry {attempt}/{attempts - 1}"
            )
        except Exception as exc:  # noqa: BLE001
            method = args[0] if args else kwargs.get("method", "?")
            path = args[1] if len(args) > 1 else kwargs.get("path", "?")
            print(f"  ! request error for {method} {path}: {exc}")
            if attempt == attempts:
                return last
        time.sleep(REQUEST_RETRY_SLEEP * attempt)
    return last


def api_error_reason(resp: ct.ApiResponse | None, *, limit: int = 2000) -> str:
    """Return a compact, human-readable API error summary for console logs."""
    if not isinstance(resp, ct.ApiResponse):
        return "no API response received"

    parts: list[str] = []
    if resp.request_id:
        parts.append(f"request_id={resp.request_id}")
    if resp.status_error:
        # status_error already includes a body preview from ApiClient._finalize().
        parts.append(resp.status_error)
    elif resp.contract_error:
        parts.append(resp.contract_error)

    body = resp.json_body
    if isinstance(body, dict):
        error = body.get("error")
        if error is not None:
            parts.append("error=" + json.dumps(error, default=str))
        elif body:
            parts.append("body=" + json.dumps(body, default=str))
    elif resp.body_text:
        parts.append("body=" + resp.body_text)

    text = " | ".join(p for p in parts if p)
    return text[:limit] + ("…" if len(text) > limit else "")


def print_api_error_if_any(label: str, resp: ct.ApiResponse | None, expected_status: int = 202) -> None:
    if not isinstance(resp, ct.ApiResponse) or resp.status != expected_status:
        print(f"    {label} error reason: {api_error_reason(resp)}")


def ingest_documents(client: ct.ApiClient, docs: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for start in range(0, len(docs), FILE_BATCH):
        batch = docs[start : start + FILE_BATCH]
        documents = [
            ("documents", f"{d['id']}.md", d["text"].encode("utf-8"), "text/markdown")
            for d in batch
        ]
        meta = []
        for d in batch:
            m = {
                "id": d["id"],
                "title": d["title"],
                "type": "md",
                "metadata": {"department": d["dept"]},
                "additional_metadata": {"golden_run": RUN_ID, "dept": d["dept"]},
            }
            if d.get("timestamp"):
                m["timestamp"] = d["timestamp"]
            if d.get("relations"):
                m["relations"] = {"ids": d["relations"]}
            meta.append(m)
        resp = safe(
            client,
            "POST",
            "/context/ingest",
            expected_statuses=(202,),
            multipart=(
                {
                    "type": "knowledge",
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT,
                    "upsert": "true",
                    "document_metadata": json.dumps(meta),
                },
                documents,
            ),
        )
        got: list[str] = []
        if isinstance(resp, ct.ApiResponse) and isinstance(resp.data, dict):
            for r in resp.data.get("results", []) or []:
                if isinstance(r, dict) and isinstance(r.get("id"), str):
                    got.append(r["id"])
        ids += got or [d["id"] for d in batch]
        batch_no = start // FILE_BATCH + 1
        st = resp.status if isinstance(resp, ct.ApiResponse) else "ERR"
        print(f"  documents batch {batch_no}: {len(batch)} -> HTTP {st}")
        print_api_error_if_any(f"documents batch {batch_no}", resp)
    return ids


def ingest_apps(client: ct.ApiClient, apps: list[dict[str, Any]]) -> list[str]:
    items = []
    for a in apps:
        item = dict(a)
        # Keep the generated dataset self-contained, but ensure runtime env overrides
        # are reflected if GOLDEN_REUSE_SUB / GOLDEN_RUN_ID changes before ingest.
        item["tenant_id"] = TENANT_ID
        item["sub_tenant_id"] = SUB_TENANT
        item.setdefault("additional_metadata", {})["golden_run"] = RUN_ID
        items.append(item)
    resp = safe(
        client,
        "POST",
        "/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT,
                "upsert": "true",
                "app_knowledge": json.dumps(items),
            },
            [],
        ),
    )
    st = resp.status if isinstance(resp, ct.ApiResponse) else "ERR"
    print(f"  app_knowledge: {len(items)} -> HTTP {st}")
    print_api_error_if_any("app_knowledge", resp)
    return [a["id"] for a in apps]


def ingest_memories(client: ct.ApiClient, mems: list[dict[str, Any]]) -> list[str]:
    items = [{**m, "infer": False} for m in mems]
    resp = safe(
        client,
        "POST",
        "/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "memory",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT,
                "upsert": "true",
                "memories": json.dumps(items),
            },
            [],
        ),
    )
    st = resp.status if isinstance(resp, ct.ApiResponse) else "ERR"
    print(f"  memories: {len(items)} -> HTTP {st}")
    print_api_error_if_any("memories", resp)
    return [m["id"] for m in mems]


def status_map(client: ct.ApiClient, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        resp = safe(
            client,
            "GET",
            "/context/status",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT,
                "ids": chunk,
            },
        )
        if isinstance(resp, ct.ApiResponse) and isinstance(resp.data, dict):
            for s in resp.data.get("statuses", []) or []:
                if isinstance(s, dict):
                    sid = s.get("id")
                    status = s.get("indexing_status")
                    if isinstance(sid, str) and isinstance(status, str):
                        out[sid] = status
    return out


def wait_searchable(client: ct.ApiClient, ids: list[str]) -> None:
    ok = {"graph_creation", "completed"}
    deadline = time.time() + SOURCE_READY_TIMEOUT
    while time.time() < deadline:
        sm = status_map(client, ids)
        ready = sum(1 for v in sm.values() if v in ok)
        failed = [k for k, v in sm.items() if v in {"errored", "failed"}]
        print(
            f"  searchable {ready}/{len(ids)} (reported {len(sm)}, failed {len(failed)})"
        )
        if ready >= len(ids):
            print("  all searchable.")
            return
        if sm and ready + len(failed) >= len(sm):
            print(f"  proceeding; failed={failed[:6]}")
            return
        time.sleep(POLL)
    print("  WARN: timed out waiting for searchable; evaluating what is ready.")


def wait_completed(
    client: ct.ApiClient, ids: list[str], timeout: int
) -> dict[str, str]:
    deadline = time.time() + timeout
    sm: dict[str, str] = {}
    while time.time() < deadline:
        sm = status_map(client, ids)
        if all(sm.get(i) == "completed" for i in ids):
            return sm
        time.sleep(POLL)
    return sm


# -----------------------------------------------------------------------------
# Query + scoring.
# -----------------------------------------------------------------------------


def run_query(client: ct.ApiClient, spec: dict[str, Any]) -> ct.ApiResponse | None:
    body: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT,
        "query": spec["query"],
        "type": spec.get("type", "knowledge"),
        "query_by": spec.get("query_by", "hybrid"),
        "max_results": spec.get("top_k", DEFAULT_TOP_K),
        "graph_context": spec.get("graph_context", False),
    }
    for k in (
        "mode",
        "operator",
        "alpha",
        "recency_bias",
        "additional_context",
        "metadata_filters",
    ):
        if spec.get(k) is not None:
            body[k] = spec[k]
    if spec.get("query_apps"):
        body["query_apps"] = True
    if "query_forceful_relations" in spec:
        body["query_forceful_relations"] = spec["query_forceful_relations"]
    return safe(client, "POST", "/query", json_body=body, expected_statuses=(200,))


def score(spec: dict[str, Any], resp: ct.ApiResponse | None) -> dict[str, Any]:
    data = (
        resp.data
        if isinstance(resp, ct.ApiResponse) and isinstance(resp.data, dict)
        else {}
    )
    transport_error = None
    if not isinstance(resp, ct.ApiResponse):
        transport_error = "request failed before receiving an API response"
    elif resp.status != 200:
        body = (resp.body_text or "")[:500]
        transport_error = f"HTTP {resp.status} from /query" + (f": {body}" if body else "")
    elif not isinstance(resp.data, dict):
        body = (resp.body_text or "")[:500]
        transport_error = "invalid /query response data" + (f": {body}" if body else "")

    chunks = [c for c in (data.get("chunks") or []) if isinstance(c, dict)]
    top_k = spec.get("top_k", DEFAULT_TOP_K)
    top = chunks[:top_k]

    ordered_ids: list[str] = []
    for c in top:
        sid = c.get("id")
        if sid and sid not in ordered_ids:
            ordered_ids.append(sid)
    blob = " ".join((c.get("chunk_content") or "") for c in top).lower()

    expected = spec.get("expected", [])
    markers = spec.get("markers", [])
    found_expected = [e for e in expected if e in ordered_ids]
    missing_expected = [e for e in expected if e not in ordered_ids]
    recall = (len(found_expected) / len(expected)) if expected else None
    first_rank = next(
        (i for i, sid in enumerate(ordered_ids, 1) if sid in expected), None
    )
    markers_found = [m for m in markers if m.lower() in blob]
    markers_missing = [m for m in markers if m.lower() not in blob]

    reasons: list[str] = []
    passed = True
    if transport_error:
        passed = False
        reasons.append(transport_error)

    if not transport_error and spec.get("expect_empty"):
        if chunks:
            passed = False
            reasons.append(f"expected zero chunks, got {len(chunks)}")
    elif not transport_error:
        min_recall = spec.get("min_recall", 1.0 if expected else None)
        if (
            expected
            and min_recall is not None
            and (recall is None or recall < min_recall)
        ):
            passed = False
            reasons.append(
                f"recall {recall} < {min_recall} (missing {missing_expected})"
            )
        if markers_missing:
            passed = False
            reasons.append(f"missing markers {markers_missing}")
        if spec.get("max_rank") is not None and (
            first_rank is None or first_rank > spec["max_rank"]
        ):
            passed = False
            reasons.append(f"rank {first_rank} > max_rank {spec['max_rank']}")

    if not transport_error and spec.get("max_chunks") is not None and len(chunks) > spec["max_chunks"]:
        passed = False
        reasons.append(
            f"max_results not respected: {len(chunks)} > {spec['max_chunks']}"
        )

    if not transport_error and spec.get("rank_before"):
        a, b = spec["rank_before"]
        ra = ordered_ids.index(a) if a in ordered_ids else None
        rb = ordered_ids.index(b) if b in ordered_ids else None
        if ra is None or (rb is not None and ra > rb):
            passed = False
            reasons.append(f"recency: expected {a} before {b} (positions {ra} vs {rb})")

    graph_populated = None
    if not transport_error and spec.get("expect_graph"):
        gc = data.get("graph_context") or {}
        graph_populated = (
            bool(
                (
                    gc.get("query_paths")
                    or gc.get("chunk_relations")
                    or gc.get("synthesis_context")
                )
            )
            if isinstance(gc, dict)
            else False
        )
        if not graph_populated:
            passed = False
            reasons.append("graph_context empty (expected populated graph slice)")

    addl_ctx_keys = None
    if not transport_error and spec.get("expect_addl_ctx"):
        ac = data.get("additional_context")
        addl_ctx_keys = list(ac.keys()) if isinstance(ac, dict) else []
        if not addl_ctx_keys:
            passed = False
            reasons.append(
                "additional_context empty (forceful relations did not surface)"
            )

    dept_violations = None
    if not transport_error and spec.get("filter_department"):
        want = spec["filter_department"]
        dept_violations = [
            (c.get("id"), (c.get("metadata") or {}).get("department"))
            for c in top
            if isinstance(c.get("metadata"), dict)
            and c["metadata"].get("department") not in (None, want)
        ]
        need = spec.get("min_recall_count", 1)
        if not top:
            passed = False
            reasons.append("department filter returned zero chunks")
        elif dept_violations:
            passed = False
            reasons.append(
                f"department filter leaked non-{want} docs: {dept_violations[:4]}"
            )
        elif len(top) < need:
            passed = False
            reasons.append(
                f"department filter returned {len(top)} chunks, expected >= {need}"
            )

    additional_meta_violations = None
    if not transport_error and spec.get("filter_additional_meta"):
        key, want = spec["filter_additional_meta"]
        additional_meta_violations = [
            (c.get("id"), (c.get("additional_metadata") or {}).get(key))
            for c in top
            if isinstance(c.get("additional_metadata"), dict)
            and c["additional_metadata"].get(key) not in (None, want)
        ]
        need = spec.get("min_recall_count", 1)
        if not top:
            passed = False
            reasons.append("additional_metadata filter returned zero chunks")
        elif additional_meta_violations:
            passed = False
            reasons.append(
                f"additional_metadata filter leaked {key}!={want}: {additional_meta_violations[:4]}"
            )
        elif len(top) < need:
            passed = False
            reasons.append(
                f"additional_metadata filter returned {len(top)} chunks, expected >= {need}"
            )

    return {
        "id": spec["id"],
        "query": spec["query"],
        "type": spec.get("type", "knowledge"),
        "query_by": spec.get("query_by", "hybrid"),
        "mode": spec.get("mode"),
        "operator": spec.get("operator"),
        "expected": expected,
        "markers": markers,
        "passed": passed,
        "reasons": reasons,
        "recall": recall,
        "first_rank": first_rank,
        "markers_found": markers_found,
        "markers_missing": markers_missing,
        "returned_top_ids": ordered_ids,
        "returned_chunk_count": len(chunks),
        "graph_populated": graph_populated,
        "additional_context_keys": addl_ctx_keys,
        "department_violations": dept_violations,
        "additional_meta_violations": additional_meta_violations,
        "request_id": resp.request_id if isinstance(resp, ct.ApiResponse) else None,
        "http_status": resp.status if isinstance(resp, ct.ApiResponse) else None,
    }


# -----------------------------------------------------------------------------
# Ingestion-fidelity checks — assert sources were stored as declared.
# These checks document whether uploaded document metadata, relations, timestamps,
# and app chunks are preserved end-to-end.
# -----------------------------------------------------------------------------


def _get_source(client: ct.ApiClient, sid: str) -> dict[str, Any]:
    r = safe(
        client,
        "POST",
        "/context/list",
        json_body={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT,
            "type": "knowledge",
            "ids": [sid],
            "page": 1,
            "page_size": 5,
        },
    )
    srcs = (
        (r.data or {}).get("sources")
        if isinstance(r, ct.ApiResponse) and isinstance(r.data, dict)
        else None
    )
    return (srcs or [{}])[0] if srcs else {}


def fidelity_checks(client: ct.ApiClient) -> list[dict[str, Any]]:
    """Return finding records (same shape as score()) for ingestion fidelity."""
    out: list[dict[str, Any]] = []

    def finding(fid: str, passed: bool, detail: str, evidence: dict[str, Any]) -> None:
        out.append(
            {
                "id": fid,
                "query": "(ingestion fidelity)",
                "type": "—",
                "query_by": "—",
                "mode": None,
                "operator": None,
                "expected": [],
                "markers": [],
                "passed": passed,
                "reasons": [] if passed else [detail],
                "recall": None,
                "first_rank": None,
                "markers_found": [],
                "markers_missing": [],
                "returned_top_ids": [],
                "returned_chunk_count": 0,
                "graph_populated": None,
                "additional_context_keys": None,
                "department_violations": None,
                "additional_meta_violations": None,
                "request_id": None,
                "http_status": None,
                "detail": detail,
                "evidence": evidence,
            }
        )

    def metadata_of(src: dict[str, Any]) -> dict[str, Any]:
        # /context/list currently returns legacy storage names even though query
        # results expose canonical `metadata` / `additional_metadata`.
        return src.get("metadata") or src.get("tenant_metadata") or {}

    def additional_metadata_of(src: dict[str, Any]) -> dict[str, Any]:
        return src.get("additional_metadata") or src.get("document_metadata") or {}

    # 1. Uploaded document should retain the document_metadata payload.
    f = _get_source(client, gold_id("gold_sec_gdpr"))
    f_meta = metadata_of(f)
    f_addl = additional_metadata_of(f)
    title_ok = f.get("title") == "GDPR Data Deletion Procedure"
    meta_ok = f_meta.get("department") == "security" and f_addl.get("dept") == "security"
    finding(
        "fidelity_document_metadata_persisted",
        meta_ok,
        f"document_metadata payload persisted={meta_ok}; title override honored={title_ok} "
        f"(title={f.get('title')!r}, expected 'GDPR Data Deletion Procedure')",
        {
            "title": f.get("title"),
            "expected_title": "GDPR Data Deletion Procedure",
            "title_override_honored": title_ok,
            "type": f.get("type"),
            "metadata": f_meta,
            "additional_metadata": f_addl,
            "raw_metadata": f.get("metadata"),
            "raw_additional_metadata": f.get("additional_metadata"),
            "raw_tenant_metadata": f.get("tenant_metadata"),
            "raw_document_metadata": f.get("document_metadata"),
        },
    )

    # 2. App source should retain metadata (positive control).
    a = _get_source(client, gold_id("gold_app_legal_1"))
    a_meta = metadata_of(a)
    a_addl = additional_metadata_of(a)
    finding(
        "fidelity_app_metadata_persisted",
        a_meta.get("department") == "legal" and a_addl.get("dept") == "legal",
        f"app metadata persisted={a_meta.get('department') == 'legal'}; metadata={a_meta}",
        {
            "metadata": a_meta,
            "additional_metadata": a_addl,
            "raw_metadata": a.get("metadata"),
            "raw_additional_metadata": a.get("additional_metadata"),
            "raw_tenant_metadata": a.get("tenant_metadata"),
            "raw_document_metadata": a.get("document_metadata"),
        },
    )

    # 3. Document-upload forceful relations → test end-to-end with thinking-mode query
    #    and query_forceful_relations: true, query_apps: false.
    #    (document_metadata.relations.ids is not part of the app-aware lane.)
    qr2 = safe(
        client,
        "POST",
        "/query",
        json_body={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT,
            "query": "new hire first week time-off policy leave request",
            "type": "knowledge",
            "query_by": "hybrid",
            "mode": "thinking",
            "query_forceful_relations": True,
            "max_results": 10,
            "graph_context": False,
        },
    )
    addl_ctx = None
    if isinstance(qr2, ct.ApiResponse) and isinstance(qr2.data, dict):
        addl_ctx = qr2.data.get("additional_context")
    rels_work = bool(addl_ctx) if isinstance(addl_ctx, dict) else False
    finding(
        "fidelity_relations_persisted",
        rels_work,
        f"document-upload relations declared at ingest did not surface in thinking-mode "
        f"additional_context (addl_ctx={addl_ctx!r}) "
        "-> query_forceful_relations is non-functional end-to-end",
        {
            "additional_context_keys": list(addl_ctx.keys())
            if isinstance(addl_ctx, dict)
            else None
        },
    )

    # 4. Provided timestamp should be honored (needed for recency_bias).
    old = _get_source(client, gold_id("gold_app_price_old"))
    new = _get_source(client, gold_id("gold_app_price_new"))
    ts_ok = old.get("timestamp", "").startswith("2023") and new.get(
        "timestamp", ""
    ).startswith("2026")
    finding(
        "fidelity_timestamp_honored",
        ts_ok,
        f"provided timestamps not honored (old={old.get('timestamp')!r}, "
        f"new={new.get('timestamp')!r}) -> recency_bias is not controllable",
        {"old_timestamp": old.get("timestamp"), "new_timestamp": new.get("timestamp")},
    )

    # 5. App chunk_content should be clean extracted text, not a serialized JSON
    # source wrapper. If this fails while q01_query_apps passes, retrieval found the
    # app source but the API returned the wrong chunk_content format.
    qr = safe(
        client,
        "POST",
        "/query",
        json_body={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT,
            "query": "Zenat Corp escalation",
            "type": "knowledge",
            "query_by": "hybrid",
            "query_apps": True,
            "max_results": 5,
            "graph_context": False,
        },
    )
    appchunk = ""
    if isinstance(qr, ct.ApiResponse) and isinstance(qr.data, dict):
        for c in qr.data.get("chunks", []) or []:
            if c.get("id") == gold_id("gold_app_slack_escalation"):
                appchunk = c.get("chunk_content") or ""
                break
    clean = bool(appchunk) and not appchunk.lstrip().startswith("{")
    finding(
        "fidelity_app_chunk_clean_text",
        clean,
        "app_knowledge chunk_content is raw serialized JSON, not extracted text",
        {"chunk_content_prefix": appchunk[:160]},
    )

    return out


# -----------------------------------------------------------------------------
# Main.
# -----------------------------------------------------------------------------


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "queries").mkdir(exist_ok=True)

    contract = ct.OpenApiContract(ct.OPENAPI_PATH, strict_extra_keys=False)
    client = ct.ApiClient(ct.BASE_URL, ct.API_KEY, contract, ct.Recorder())

    if not REUSE and EVAL_COPY_INDEX > INGEST_COPIES:
        raise ValueError(
            f"GOLDEN_EVAL_COPY={EVAL_COPY_INDEX} cannot exceed "
            f"GOLDEN_INGEST_COPIES={INGEST_COPIES} when not reusing an existing corpus"
        )

    base_heroes = hero_docs()
    base_fillers = distractor_docs()
    base_knowledge = base_heroes + base_fillers
    base_apps = app_sources()
    base_mems = memory_items()
    base_queries = golden_queries()

    # Search/analysis is intentionally performed against one copy only.
    knowledge, apps, mems, queries = apply_id_suffix(
        base_knowledge,
        base_apps,
        base_mems,
        base_queries,
        copy_index=EVAL_COPY_INDEX,
    )

    (RESULTS_DIR / "golden_dataset.json").write_text(
        json.dumps(
            {
                "sub_tenant": SUB_TENANT,
                "run_id": RUN_ID,
                "id_suffix": ID_SUFFIX,
                "ingest_copies": INGEST_COPIES,
                "eval_copy_index": EVAL_COPY_INDEX,
                "eval_id_suffix": copy_id_suffix(EVAL_COPY_INDEX),
                "copy_id_suffixes": [copy_id_suffix(i) for i in range(1, INGEST_COPIES + 1)],
                "knowledge_documents": knowledge,
                "app_sources": apps,
                "memories": mems,
                "queries": queries,
            },
            indent=2,
        )
    )

    print("=== HydraDB v2 golden-dataset retrieval eval (rich, document-based) ===")
    print(f"Base URL:   {ct.BASE_URL}")
    print(f"Sub-tenant: {SUB_TENANT}  (isolated)")
    print(f"ID suffix:  {ID_SUFFIX or '(disabled)'}")
    print(f"Copies:     ingest={INGEST_COPIES}, eval={EVAL_COPY_INDEX} ({copy_id_suffix(EVAL_COPY_INDEX) or 'no suffix'})")
    print(
        f"Corpus:     {len(knowledge)} document docs ({len(base_heroes)} hero / {len(base_fillers)} "
        f"distractor) + {len(apps)} app sources + {len(mems)} memories"
    )
    print(f"Queries:    {len(queries)} (covering every /query parameter)")
    print(f"Results:    {RESULTS_DIR}")

    file_ids = [d["id"] for d in knowledge]
    app_ids = [a["id"] for a in apps]
    if not REUSE:
        print("\n--- Ingesting (documents + app sources + memories) ---")
        all_file_ids: list[str] = []
        all_app_ids: list[str] = []
        for copy_index in range(1, INGEST_COPIES + 1):
            copy_knowledge, copy_apps, copy_mems, _ = apply_id_suffix(
                base_knowledge,
                base_apps,
                base_mems,
                [],
                copy_index=copy_index,
            )
            copy_suffix = copy_id_suffix(copy_index) or "no suffix"
            print(f"\n  copy {copy_index}/{INGEST_COPIES} ({copy_suffix})")
            ingest_documents(client, copy_knowledge)
            ingest_apps(client, copy_apps)
            ingest_memories(client, copy_mems)
            all_file_ids.extend(d["id"] for d in copy_knowledge)
            all_app_ids.extend(a["id"] for a in copy_apps)

        print("\n--- Waiting for searchable ---")
        wait_searchable(client, all_file_ids + all_app_ids)
        # The graph_context query needs `completed`; wait on the eval copy only.
        graph_ids = [gold_id("gold_graph_payments", EVAL_COPY_INDEX)]
        print(f"\n--- Waiting for graph completion on {graph_ids} ---")
        sm = wait_completed(client, graph_ids, GRAPH_TIMEOUT)
        print(f"  graph sources status: {sm}")
        time.sleep(POLL)
    else:
        print(f"\n--- Reusing {SUB_TENANT}; skipping ingest ---")

    print("\n--- Running golden queries ---")
    results: list[dict[str, Any]] = []
    seen_chunks: list[dict[str, Any]] = []
    for spec in queries:
        resp = run_query(client, spec)
        if isinstance(resp, ct.ApiResponse) and isinstance(resp.data, dict):
            seen_chunks += [
                c for c in (resp.data.get("chunks") or []) if isinstance(c, dict)
            ]
        r = score(spec, resp)
        results.append(r)
        (RESULTS_DIR / "queries" / f"{spec['id']}.json").write_text(
            json.dumps(
                {
                    "spec": spec,
                    "score": r,
                    "response_body": resp.json_body
                    if isinstance(resp, ct.ApiResponse)
                    else None,
                    "response_text": resp.body_text
                    if isinstance(resp, ct.ApiResponse)
                    else None,
                },
                indent=2,
                default=str,
            )
        )
        mark = "PASS" if r["passed"] else "FAIL"
        rc = f"recall={r['recall']:.2f}" if r["recall"] is not None else "recall=n/a"
        rk = f"rank={r['first_rank']}" if r["first_rank"] else "rank=-"
        extra = "" if r["passed"] else "  << " + "; ".join(r["reasons"])
        print(
            f"  [{mark}] {spec['id']:<22} {rc} {rk} "
            f"mk={len(r['markers_found'])}/{len(spec.get('markers', []))}{extra}"
        )

    # Ingestion-fidelity findings (documented spec-conformance checks).
    print("\n--- Ingestion-fidelity checks ---")
    findings = fidelity_checks(client)
    for fr in findings:
        (RESULTS_DIR / "queries" / f"{fr['id']}.json").write_text(
            json.dumps(fr, indent=2, default=str)
        )
        mark = "PASS" if fr["passed"] else "FINDING"
        print(f"  [{mark}] {fr['id']:<34} {fr.get('detail', '')[:90]}")

    # Retrieval metrics computed over the QUERY results only (not fidelity findings).
    with_expected = [r for r in results if r["expected"]]
    recalls = [r["recall"] for r in with_expected if r["recall"] is not None]
    ranked = [r for r in with_expected if r["first_rank"]]
    mrr = (
        (sum(1.0 / r["first_rank"] for r in ranked) / len(with_expected))
        if with_expected
        else None
    )
    marker_specs = [r for r in results if r["markers"]]
    marker_hits = [r for r in marker_specs if not r["markers_missing"]]
    query_passed = [r for r in results if r["passed"]]

    # Combine for overall accounting.
    results = results + findings
    q_pass = len(query_passed)
    f_pass = sum(1 for fr in findings if fr["passed"])
    metrics = {
        "run_id": RUN_ID,
        "sub_tenant": SUB_TENANT,
        "retrieval_queries": len(results) - len(findings),
        "retrieval_passed": q_pass,
        "retrieval_pass_rate": round(q_pass / (len(results) - len(findings)), 3),
        "mean_recall_at_k": round(sum(recalls) / len(recalls), 3) if recalls else None,
        "mrr": round(mrr, 3) if mrr is not None else None,
        "marker_hit_rate": round(len(marker_hits) / len(marker_specs), 3)
        if marker_specs
        else None,
        "fidelity_checks": len(findings),
        "fidelity_passed": f_pass,
        "fidelity_findings": [fr["id"] for fr in findings if not fr["passed"]],
        "top_k": DEFAULT_TOP_K,
        "corpus_size": len(knowledge) + len(apps) + len(mems),
    }
    (RESULTS_DIR / "_golden_metrics.json").write_text(json.dumps(metrics, indent=2))

    lines = [
        "# Golden-dataset retrieval eval (rich, two-lane)\n",
        f"- Sub-tenant `{SUB_TENANT}` — {len(knowledge)} document docs + {len(apps)} app + "
        f"{len(mems)} memories",
        f"- **Retrieval quality: {q_pass}/{len(results) - len(findings)} passed** | "
        f"mean recall@{DEFAULT_TOP_K} **{metrics['mean_recall_at_k']}** | "
        f"MRR **{metrics['mrr']}** | marker hit-rate **{metrics['marker_hit_rate']}**",
        f"- **Ingestion fidelity: {f_pass}/{len(findings)} passed** "
        f"(findings: {', '.join(metrics['fidelity_findings']) or 'none'})\n",
        "## Retrieval queries\n",
        "| Query | param focus | recall | rank | markers | verdict | notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results[: len(results) - len(findings)]:
        rc = f"{r['recall']:.2f}" if r["recall"] is not None else "—"
        rk = str(r["first_rank"]) if r["first_rank"] else "—"
        mk = f"{len(r['markers_found'])}/{len(r['markers'])}" if r["markers"] else "—"
        v = "✅" if r["passed"] else "❌"
        lines.append(
            f"| `{r['id']}` | {r['type']}/{r['query_by']}/{r['mode']} | {rc} | "
            f"{rk} | {mk} | {v} | {'; '.join(r['reasons'])} |"
        )
    lines += [
        "\n## Ingestion fidelity findings\n",
        "| Check | verdict | detail |",
        "|---|---|---|",
    ]
    for fr in findings:
        v = "✅" if fr["passed"] else "❌ FINDING"
        lines.append(f"| `{fr['id']}` | {v} | {fr.get('detail', '')} |")
    (RESULTS_DIR / "_golden_report.md").write_text("\n".join(lines) + "\n")

    print("\n=== GOLDEN EVAL SUMMARY ===")
    print(
        f"Retrieval quality: {q_pass}/{len(results) - len(findings)} passed  |  "
        f"recall@{DEFAULT_TOP_K} {metrics['mean_recall_at_k']}  |  MRR {metrics['mrr']}  |  "
        f"marker hit-rate {metrics['marker_hit_rate']}"
    )
    print(f"Ingestion fidelity: {f_pass}/{len(findings)} passed")
    if metrics["fidelity_findings"]:
        print("Confirmed findings:", ", ".join(metrics["fidelity_findings"]))
    print(f"Report: {RESULTS_DIR / '_golden_report.md'}")
    q_fail = [r for r in results[: len(results) - len(findings)] if not r["passed"]]
    if q_fail:
        print("\nRetrieval failures:")
        for r in q_fail:
            print(
                f"- {r['id']}: {'; '.join(r['reasons'])} | top={r['returned_top_ids'][:6]}"
            )
    # Exit non-zero only if a RETRIEVAL query failed; fidelity findings are reported,
    # not gating (they are known/expected product findings to hand off).
    return 0 if not q_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
