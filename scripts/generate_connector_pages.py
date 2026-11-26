"""Generate one page per connector enabled in production, plus the providers overview.

scripts/production_providers.json is refreshed from GET /connectors/providers on production.
Every provider's page body is rendered from its copy file in scripts/connector_pages/. A provider without one stops the run.
"""
import argparse
from collections import namedtuple
import html
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "scripts/connector_capabilities.json"
PRODUCTION = ROOT / "scripts/production_providers.json"
NAMES = ROOT / "scripts/dashboard_provider_names.json"
TITLE_LOGOS = ROOT / "connector-logos.css"
LOGOS = ROOT / "images/connectors"
DIRECTORY = ROOT / "essentials/v2/connectors"
INDEX = ROOT / "essentials/v2/connector-providers.mdx"
COPY = ROOT / "scripts/connector_pages"
GUIDE = "/essentials/v2/connectors"
SCOPED_PROVIDER_EXAMPLES = {
    "slack": ("slack-account-a", "slack-account-b"),
    "github": ("github-account-a", "github-account-b"),
    "linear": ("linear-account-a", "linear-account-b"),
    "notion": ("notion-account-a", "notion-account-b"),
}
NAVIGATION = ROOT / "docs.json"
NAVIGATION_GROUP = "Connector catalog"
# Sidebar and overview order: what customers connect most comes first.
GROUPS = {
    "Messaging & email": {"messaging", "email", "social"},
    "Tickets & project management": {"project_management"},
    "Docs & cloud storage": {"docs", "knowledge", "storage", "cms", "design"},
    "Meetings & calendar": {"communications", "calendar", "scheduling"},
    "Engineering": {"dev_tools", "monitoring", "security"},
    "Marketing & analytics": {"marketing", "analytics", "forms"},
    "HR & finance": {"hr", "finance"},
    "CRM & support": {"crm", "sales", "support"},
}
PROVIDER_GROUPS = {
    "airtable": "Docs & cloud storage",
    "microsoft_teams": "Messaging & email",
    "outlook": "Messaging & email",
    "twilio": "Messaging & email",
}
# Order inside each group: the tools a company is most likely to run come first.
# A provider missing from its list follows the listed ones, by name.
PROVIDER_ORDER = {
    "Messaging & email": ["slack", "gmail", "outlook", "microsoft_teams", "discord", "sendgrid", "twilio", "twitter"],
    "Tickets & project management": ["jira", "linear", "asana", "clickup", "monday", "smartsheet", "wrike", "shortcut"],
    "Docs & cloud storage": ["confluence", "notion", "google_drive", "dropbox", "sharepoint", "bookstack", "airtable",
                             "guru", "coda", "contentful", "figma"],
    "Meetings & calendar": ["google_calendar", "zoom", "calendly", "fireflies", "granola", "loom", "avoma"],
    "Engineering": ["github", "gitlab", "sentry", "datadog", "pagerduty", "supabase", "launchdarkly", "okta"],
    "Marketing & analytics": ["posthog", "mixpanel", "bigquery", "google_search_console", "googleads", "mailchimp",
                              "klaviyo", "pardot", "typeform", "surveymonkey"],
    "HR & finance": ["stripe", "quickbooks", "xero", "recurly", "bamboohr", "greenhouse", "lever"],
    "CRM & support": ["hubspot", "zendesk", "intercom", "gong", "pipedrive", "freshdesk", "servicenow", "attio",
                      "zoho_crm", "helpscout", "copper"],
}
Logo = namedtuple("Logo", "light dark")


def ordered(title, members):
    """Members of one group in PROVIDER_ORDER, then any unlisted provider in the name order they arrived in."""
    rank = {provider: position for position, provider in enumerate(PROVIDER_ORDER[title])}
    return sorted(members, key=lambda item: rank.get(item["production_id"], len(rank)))


def slug(identifier):
    return identifier.replace("_", "-")


def group(row):
    if row["provider"] in PROVIDER_GROUPS:
        return PROVIDER_GROUPS[row["provider"]]
    return next(title for title, categories in GROUPS.items() if row["category"] in categories)


PLACEHOLDER_MARK = 'data-placeholder="monogram"'
INKS = {"light": "#363636", "dark": "#cccccc"}


def url(path):
    return f"/{path.relative_to(ROOT)}"


def is_placeholder(path):
    return PLACEHOLDER_MARK in path.read_text()


def artwork(identifier):
    """The dashboard's own mark. Monochrome marks ship a light-mode and a dark-mode file; multi-colour marks ship one file for both."""
    single, light, dark = (LOGOS / f"{slug(identifier)}{suffix}.svg" for suffix in ("", "-light", "-dark"))
    if light.is_file() and dark.is_file() and not is_placeholder(light):
        return Logo(url(light), url(dark))
    return Logo(url(single), None) if single.is_file() else None


def logo(identifier):
    return artwork(identifier) or Logo(*(url(LOGOS / f"{slug(identifier)}-{theme}.svg") for theme in INKS))


def monogram(name, ink):
    return "\n".join([
        f'<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" {PLACEHOLDER_MARK}>',
        f'<text x="12" y="12.5" text-anchor="middle" dominant-baseline="central" font-family="Helvetica, Arial, sans-serif" font-size="21" font-weight="600" fill="{ink}">{html.escape(name[0].upper())}</text>',
        "</svg>", "",
    ])


def placeholder_logos(providers):
    """The one place that decides which providers show a placeholder: those with no dashboard artwork. Adding real artwork drops the placeholder on the next run."""
    return {
        LOGOS / f"{slug(provider['provider'])}-{theme}.svg": monogram(provider["display_name"], ink)
        for provider in providers if artwork(provider["provider"]) is None
        for theme, ink in INKS.items()
    }


def images(found, alt, class_name):
    if found.dark is None:
        return [f'<img src="{found.light}" alt="{alt}" className="{class_name}" noZoom />']
    return [
        f'<img src="{found.light}" alt="{alt}" className="{class_name} block dark:hidden" noZoom />',
        f'<img src="{found.dark}" alt="{alt}" className="{class_name} hidden dark:block" noZoom />',
    ]


def title_logos(providers):
    """Mintlify has no title icon slot, so the logo is drawn before #page-title on the page whose path names the provider."""
    page = lambda identifier, theme="html": f'{theme}[data-current-path$="/essentials/v2/connectors/{slug(identifier)}"] #page-title::before'
    lines = [
        "/* Generated by scripts/generate_connector_pages.py. Each connector page shows its logo before the title. */",
        'html[data-current-path*="/essentials/v2/connectors/"] #page-title::before {',
        "    display: inline-block;", "    width: 1.1em;", "    height: 1.1em;", "    margin-right: 0.35em;", "    vertical-align: -0.19em;",
        "    background-position: center;", "    background-repeat: no-repeat;", "    background-size: 70%;",
        "    background-color: rgba(0, 0, 0, 0.04);", "    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.09);", "    border-radius: 0.22em;", "}",
        'html.dark[data-current-path*="/essentials/v2/connectors/"] #page-title::before {',
        "    background-color: rgba(255, 255, 255, 0.05);", "    box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.09);", "}",
    ]
    for provider in providers:
        found = logo(provider["provider"])
        lines.append(f'{page(provider["provider"])} {{ content: ""; background-image: url("{found.light}"); }}')
        if found.dark:
            lines.append(f'{page(provider["provider"], "html.dark")} {{ background-image: url("{found.dark}"); }}')
    return "\n".join(lines) + "\n"


def card(provider, found):
    href = f"/essentials/v2/connectors/{slug(provider['provider'])}"
    if found.dark is None:
        icon = f' icon="{found.light}"'
    else:
        icon = f" icon={{<>{''.join(images(found, '', 'connector-card-logo'))}</>}}"
    return f'  <Card title="{provider["display_name"]}"{icon} href="{href}" horizontal />'


def navigation(by_group):
    """Rewrites only the connector catalog group of docs.json, so hand-kept groups keep their layout."""
    text = NAVIGATION.read_text()
    marker = text.index(f'"group": "{NAVIGATION_GROUP}"')
    start = text.rindex("{", 0, marker)
    depth = 0
    for end in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            break
    indent = text[text.rindex("\n", 0, start) + 1:start]
    entry = {
        "group": NAVIGATION_GROUP, "public": True,
        "pages": [str(INDEX.relative_to(ROOT).with_suffix(""))] + [
            {"group": title, "pages": [f"essentials/v2/connectors/{slug(provider['provider'])}" for provider in members]}
            for title, members in by_group.items()
        ],
    }
    block = json.dumps(entry, indent=2).replace("\n", "\n" + indent)
    return text[:start] + block + text[end + 1:]


# Badges a customer sees on each attribute. The legend is printed once, on the providers overview.
USES = {
    "Searchable text": "part of the text a query matches.",
    "Query filter": "a key you can filter a query on, typed exactly as shown.",
    "Result date": "the date shown for the result. HydraDB uses it, among other signals, when it ranks results by date.",
    "Graph connection": "links the item to another item in the knowledge graph.",
    "Graph identity": "names a person or group, and the graph attaches the item to them.",
    "Access rule": "decides who can read the item.",
    "Stored only": "kept on the item and shown when you inspect it. A query does not match it and cannot filter on it.",
}
STORED_ONLY = "Stored only"
SEARCHABLE = "Searchable text"
CLASSES = {
    "email": ("Email", "an email message, with its sender, recipients, subject and thread."),
    "message": ("Message", "a chat or conversation message, with its author and thread."),
    "ticket": ("Ticket", "an issue, task or support ticket, with its workflow and ownership details."),
    "knowledge_base": ("Page", "a page, article or other document-style content."),
    "contact": ("Contact", "a person or organization record."),
    "comment": ("Comment", "a comment that belongs to another item or thread."),
    "meeting": ("Meeting", "a meeting or calendar event."),
    "custom": ("Record", "a record that fits none of the other classes."),
    "container": ("Container", "a project, channel, folder or space that holds other items."),
    "iteration": ("Iteration", "a sprint, cycle or release with dates, a state and a goal."),
}
COPY_KEYS = {"description", "opening", "permissions", "connect", "sync", "documents", "graph", "not_synced"}
OPTIONAL_COPY_KEYS = {"callouts", "shared_attributes", "example_searches"}
CONNECT_KEYS = {"auth", "credentials", "steps", "account"}
FIELD_TYPES = {"secret string", "string", "string list", "boolean", "number"}
SYNC_KEYS = {"first_sync", "changes", "edits", "deletes"}
OPTIONAL_SYNC_KEYS = {"notes", "scheduled"}
OPTIONAL_CONNECT_KEYS = {"alternatives", "credentials_note", "settings", "advanced_settings", "permissions_label", "permissions", "permissions_note", "examples", "scope_unit"}
CALLOUT_SECTIONS = {"opening", "permissions", "connect", "sync", "synced", "graph"}
MAX_EXAMPLE_SEARCHES = 3


def attribute_problems(item):
    uses = item.get("uses") or []
    name = item.get("name")
    if not uses or any(use not in USES for use in uses):
        return [f"{name}: uses {uses}"]
    if STORED_ONLY in uses and len(uses) > 1:
        return [f"{name}: {STORED_ONLY} cannot be combined with another use"]
    if SEARCHABLE not in uses and STORED_ONLY not in uses and "not searchable" not in item.get("text", "").lower():
        return [f"{name}: an attribute with no search use must say it is not searchable"]
    return []


def connect_fields(connect):
    return [*connect.get("credentials", []), *connect.get("settings", []), *connect.get("advanced_settings", []),
            *(item for option in connect.get("alternatives", []) for item in option["credentials"])]


def load_copy(identifier):
    """Returns the provider's checked-in page copy. Raises ValueError on a copy file that is missing or cannot render."""
    path = COPY / f"{identifier}.json"
    if not path.is_file():
        raise ValueError(f"{path.relative_to(ROOT)}: every production provider needs a copy file")
    copy = json.loads(path.read_text())
    problems = [f"missing {key}" for key in sorted(COPY_KEYS - copy.keys())]
    problems += [f"unknown {key}" for key in sorted(copy.keys() - COPY_KEYS - OPTIONAL_COPY_KEYS)]
    connect = copy.get("connect", {})
    problems += [f"connect missing {key}" for key in sorted(CONNECT_KEYS - connect.keys())]
    problems += [f"connect unknown {key}" for key in sorted(connect.keys() - CONNECT_KEYS - OPTIONAL_CONNECT_KEYS)]
    problems += [f"{item.get('name')}: type {item.get('type')}" for item in connect_fields(connect) if item.get("type") not in FIELD_TYPES]
    sync = copy.get("sync", {})
    problems += [f"sync missing {key}" for key in sorted(SYNC_KEYS - sync.keys())]
    problems += [f"sync unknown {key}" for key in sorted(sync.keys() - SYNC_KEYS - OPTIONAL_SYNC_KEYS)]
    if not isinstance(sync.get("scheduled", True), bool):
        problems.append("sync.scheduled must be true or false")
    if not isinstance(copy.get("permissions", {}).get("cloud_outcome", True), bool):
        problems.append("permissions.cloud_outcome must be true or false")
    if identifier in SCOPED_PROVIDER_EXAMPLES and not connect.get("scope_unit"):
        problems.append("connect.scope_unit names what one provider_account_scope covers")
    for section in copy.get("callouts", {}):
        if section not in CALLOUT_SECTIONS:
            problems.append(f"callout for unknown section {section}")
    for document in copy.get("documents", []):
        if document.get("kind") not in CLASSES:
            problems.append(f"{document.get('name')}: unknown kind {document.get('kind')}")
        if not document.get("attributes"):
            problems.append(f"{document.get('name')}: no attributes")
    for item in all_attributes(copy):
        problems += attribute_problems(item)
    if len(copy.get("example_searches", [])) > MAX_EXAMPLE_SEARCHES:
        problems.append(f"example_searches has more than {MAX_EXAMPLE_SEARCHES} entries")
    if not isinstance(copy.get("permissions", {}).get("carried"), bool):
        problems.append("permissions.carried must be true or false")
    if problems:
        raise ValueError(f"{path.relative_to(ROOT)}: {'; '.join(problems)}")
    return copy


def all_attributes(copy):
    return [*copy.get("shared_attributes", []), *(attribute for document in copy.get("documents", []) for attribute in document.get("attributes", []))]


def attribute(value):
    return html.escape(value, quote=False).replace('"', "&quot;")


def mdx(text):
    """Escapes JSX-significant characters outside inline code so copy reads as plain Markdown."""
    parts = re.split(r"(`[^`]*`)", text)
    return "".join(part if part.startswith("`") else part.replace("{", "&#123;").replace("}", "&#125;").replace("<", "&lt;").replace(">", "&gt;") for part in parts)


def field(name, kind, text, required=False):
    return [f'<ResponseField name="{attribute(name)}" type="{kind}"{" required" if required else ""}>', mdx(text), "</ResponseField>", ""]


def fields(items):
    return [line for item in items for line in field(item["name"], item["type"], item["text"], item.get("required", False))]


def accordion(title, lines):
    return [f'<Accordion title="{attribute(title)}">', "", *lines, "</Accordion>", ""]


def callout(copy, section):
    found = copy.get("callouts", {}).get(section)
    if not found:
        return []
    tag = found["kind"].capitalize()
    return [f"<{tag}>", mdx(found["text"]), f"</{tag}>", ""]


def labelled(label, value):
    return f"- **{label}:** {mdx(value)}"


def bullets(value):
    return [f"- {mdx(item)}" for item in value] if isinstance(value, list) else [mdx(value)]


def attributes(items):
    """Every use an attribute has shows as its own badge. Stored-only fields fold into one accordion per record type."""
    shown = [item for item in items if STORED_ONLY not in item["uses"]]
    stored = [item for item in items if STORED_ONLY in item["uses"]]
    lines = [line for item in shown for line in field(item["name"], ", ".join(use for use in USES if use in item["uses"]), item["text"])]
    if stored:
        lines += accordion(f"Stored only, not searchable ({len(stored)})", [line for item in stored for line in field(item["name"], STORED_ONLY, item["text"])])
    return lines


def opening_section(provider, copy, beta):
    lines = [line for paragraph in copy["opening"] for line in (mdx(paragraph), "")]
    lines += callout(copy, "opening")
    if beta:
        lines += ["<Note>", f"{mdx(provider['display_name'])} is in beta.", "</Note>", ""]
    return lines


def permissions_section(provider, copy, acl_supported):
    name = mdx(provider["display_name"])
    permissions = copy["permissions"]
    if permissions["carried"] != acl_supported:
        raise ValueError(f"{provider['production_id']}: permissions.carried is {permissions['carried']} but production reports acl_supported {acl_supported}")
    lines = ["## Permissions", ""]
    if permissions["carried"]:
        cloud = " On HydraDB Cloud, with Enforce ACL on, such an item follows the access rule you set on its resource. With no rule, it is hidden from search. Self-hosted deployments can be configured differently. Permission warnings appear in [Get Connector Status](/api-reference/v2/endpoint/get-connector-status)."
        lines += [
            f"Turn on **Enforce ACL** for this connector to make search follow {name} permissions. With Enforce ACL off, HydraDB does not enforce them. Only a workspace owner or writer signed in to HydraDB can turn it on. API-key requests must omit `enforce_acl`, including `false`. See [Enforce ACL]({GUIDE}#enforce-acl).",
            "", *callout(copy, "permissions"),
            labelled("Who can read an item", permissions["who_can_read"]),
            labelled("How a person is identified", permissions["identity"]),
            labelled("When permissions cannot be read", permissions["when_unreadable"]) + (cloud if permissions.get("cloud_outcome", True) else ""),
            *bullets(permissions.get("notes", [])), "",
            f"You can also [set your own rule on a resource]({GUIDE}#set-your-own-rule-on-a-resource).", "",
        ]
    else:
        lines += [
            "<Warning>",
            f"{name} permissions are not imported, so **Enforce ACL** is unavailable for this connector. Every synced item is visible to everyone who can query the collection, unless you [set an access rule on the resource]({GUIDE}#set-your-own-rule-on-a-resource).",
            "</Warning>", "", *callout(copy, "permissions"),
        ]
        if permissions.get("notes"):
            lines += [*bullets(permissions["notes"]), ""]
        lines += [f"API-key requests must omit `enforce_acl`, including `false`. See [ACL on synced content]({GUIDE}#5-acl-native-rbac-awareness).", ""]
    return lines


def connect_section(provider, copy):
    name = mdx(provider["display_name"])
    connect = copy["connect"]
    lines = ["## Before you connect", "", *callout(copy, "connect"), "### Credentials", "", mdx(connect["auth"]), "", *fields(connect["credentials"])]
    if connect.get("alternatives"):
        lines += accordion("Other credential options", [line for option in connect["alternatives"] for line in (f"**{mdx(option['title'])}**", "", *fields(option["credentials"]))])
    if connect.get("credentials_note"):
        lines += [mdx(connect["credentials_note"]), ""]
    if connect.get("settings"):
        lines += ["**Connector settings**", "", *fields(connect["settings"])]
    if connect.get("advanced_settings"):
        lines += accordion(f"Connector-wide defaults ({len(connect['advanced_settings'])})", fields(connect["advanced_settings"]))
    if connect.get("permissions"):
        lines += [f"**{connect.get('permissions_label', 'Permissions to grant')}**", ""]
        lines += [f"- `{item['name']}`" + (f": {mdx(item['text'])}" if item.get("text") else "") for item in connect["permissions"]]
        lines.append("")
        if connect.get("permissions_note"):
            lines += [mdx(connect["permissions_note"]), ""]
    lines += ["### Set it up", "", "<Steps>"]
    for step in connect["steps"]:
        lines += [f'  <Step title="{attribute(step["title"])}">', f"    {mdx(step['text'])}", "  </Step>"]
    lines += ["</Steps>", ""]
    for example in connect.get("examples", []):
        lines += [mdx(example["intro"]), "", "<CodeGroup>"]
        for tab in example["tabs"]:
            lines += [f"```json {tab['title']}", json.dumps(tab["json"], indent=2), "```"]
        lines += ["</CodeGroup>", ""]
    lines += [mdx(connect["account"]), ""]
    if provider["provider"] in SCOPED_PROVIDER_EXAMPLES:
        first, second = SCOPED_PROVIDER_EXAMPLES[provider["provider"]]
        lines += [
            f"To connect more than one {mdx(connect['scope_unit'])}, give each connector its own `provider_account_scope`, such as `{first}` and `{second}`. These are labels you choose, not {name}'s own account ids, and HydraDB does not check them. Two connectors that share a scope and write to the same collection can overwrite each other's items. See [several accounts from one provider]({GUIDE}#6-connect-several-accounts-from-one-provider).",
            "",
        ]
    lines += accordion("Connect through the API", [
        f"Use provider `{provider['production_id']}` with [Create Connector](/api-reference/v2/endpoint/create-connector). Then call [Discover Connector Resources](/api-reference/v2/endpoint/discover-connector-resources) to list the resources the connector can sync, and [Configure Connector](/api-reference/v2/endpoint/configure-connector) to choose what to sync.",
        "",
    ])
    return lines


def sync_section(copy):
    sync = copy["sync"]
    lines = [
        "## Keeping in sync", "", *callout(copy, "sync"),
        labelled("First sync", sync["first_sync"]), labelled("New and changed items", sync["changes"]),
        labelled("Edits", sync["edits"]), labelled("Deletions", sync["deletes"]),
        *bullets(sync.get("notes", [])), "",
    ]
    if sync.get("scheduled", True):
        lines += ["After the first sync, HydraDB syncs every hour by default. Set `sync_interval_seconds` on the connector to change it.", ""]
    return lines


def synced_section(provider, copy):
    lines = ["## What gets synced", "", *callout(copy, "synced")]
    if copy.get("shared_attributes"):
        lines += [f"### On every {mdx(provider['display_name'])} item", "", *attributes(copy["shared_attributes"])]
    for document in copy["documents"]:
        lines += [f"### {document['name']}", ""]
        lines += [labelled(label, document[key]) for label, key in (("One document", "one_document"), ("Title", "title"), ("Text", "text"), ("Date", "date"), ("Link back", "link_back"), ("Files", "files")) if document.get(key)]
        lines += ["", *attributes(document["attributes"])]
    if copy.get("example_searches"):
        lines += ["**Example searches**", "", *bullets(copy["example_searches"]), ""]
    return lines


def graph_section(provider, copy):
    name = mdx(provider["display_name"])
    graph = copy["graph"]
    return [
        "## How it fits into the graph", "", *callout(copy, "graph"),
        *(f"- {mdx(document['name'])} use the {CLASSES[document['kind']][0]} class." for document in copy["documents"]),
        "", f"[Graph classes](/essentials/v2/connector-providers#graph-classes) explains each class.", "",
        f"**Connections {name} creates**", "", *bullets(graph["connections"]),
        "", "**People and groups**", "", *bullets(graph["people"]),
        "", "**Found in the text**", "",
        f"{mdx(graph['from_text'])} HydraDB identifies people, organizations, projects and other named things in this text. To steer what it looks for, use [custom instructions]({GUIDE}#4-custom-ingestion-instructions).",
        "",
    ]


def not_synced_section(copy):
    return ["## Not synced", "", *bullets(copy["not_synced"]), ""]


def copy_body(provider, copy, beta, acl_supported):
    return [
        *opening_section(provider, copy, beta),
        *permissions_section(provider, copy, acl_supported),
        *connect_section(provider, copy),
        *sync_section(copy),
        *synced_section(provider, copy),
        *graph_section(provider, copy),
        *not_synced_section(copy),
    ]


def pages():
    production = {slug(row["provider"]): row for row in json.loads(PRODUCTION.read_text())["providers"]}
    enabled = {key: group(row) for key, row in production.items()}
    names = json.loads(NAMES.read_text())
    snapshot = json.loads(SNAPSHOT.read_text())
    providers = []
    for item in snapshot["providers"]:
        if slug(item["provider"]) in production:
            production_id = production[slug(item["provider"])]["provider"]
            providers.append({**item, "production_id": production_id, "display_name": names["names"][production_id]})
    providers.sort(key=lambda item: item["display_name"].casefold())
    output = {}
    for provider in providers:
        identifier = provider["provider"]
        name = provider["display_name"]
        row = production[slug(identifier)]
        found = logo(identifier)
        copy = load_copy(provider["production_id"])
        lines = [
            "---", f"title: {json.dumps(name)}",
            f"description: {json.dumps(copy['description'])}",
            f"icon: {json.dumps(found.light)}",
            "---", "",
            *copy_body(provider, copy, row["is_beta"], row["acl_supported"]),
        ]
        output[DIRECTORY / f"{slug(identifier)}.mdx"] = "\n".join(lines)
    by_group = {title: ordered(title, [provider for provider in providers if enabled[slug(provider["provider"])] == title])
                for title in GROUPS}
    lines = [
        "---", 'title: "Connector catalog"', 'sidebarTitle: "Catalog"',
        'description: "Every app HydraDB can sync, grouped by what it is for."', "---", "",
        f"Pick an app to see what it syncs and what it needs. The setup steps every connector shares are in the [connectors guide]({GUIDE}).", "",
    ]
    for title, members in by_group.items():
        lines.extend([f"## {title}", "", "<Columns cols={3}>", *(card(provider, logo(provider["provider"])) for provider in members), "</Columns>", ""])
    lines.extend([
        "## How to read a connector page", "",
        "Every page has the same sections, in this order:", "",
        "- **Permissions:** whether the app's own permissions decide who can read an item.",
        "- **Before you connect:** the credentials and steps the app needs.",
        "- **Keeping in sync:** how history, edits and deletions arrive.",
        "- **What gets synced:** what one document is, and each attribute HydraDB keeps.",
        "- **How it fits into the graph:** the class of each item and the connections it gets.",
        "- **Not synced:** what does not come across.", "",
        "### Attribute labels", "",
        '<Accordion title="What the label on an attribute means">', "",
        *(f"- **{label}:** {meaning}" for label, meaning in USES.items()), "",
        "An attribute without the Searchable text label says so in its description.", "",
        "</Accordion>", "",
        "### Graph classes", "",
        '<Accordion title="The class every synced item belongs to">', "",
        "Similar items from different apps share a class. Each connector page says which details its items carry.", "",
        *(f"- **{title}:** {meaning}" for title, meaning in CLASSES.values()), "",
        f"HydraDB also finds people, organizations, projects and other named things in the text of every item. To steer what it looks for, use [custom instructions]({GUIDE}#4-custom-ingestion-instructions).", "",
        "</Accordion>", "",
        "## Related", "",
        f"- [Connectors]({GUIDE}): shared setup and sync workflow",
        f"- [Several accounts from one provider]({GUIDE}#6-connect-several-accounts-from-one-provider): keep two accounts of the same app apart",
        "- [List connector providers](/api-reference/v2/endpoint/list-connector-providers): availability and credential fields for your deployment", "",
    ])
    output[INDEX] = "\n".join(lines)
    output[NAVIGATION] = navigation(by_group)
    output[TITLE_LOGOS] = title_logos(providers)
    output.update(placeholder_logos(providers))
    return output


def stale_placeholders(generated):
    return {path for path in LOGOS.glob("*.svg") if is_placeholder(path)} - generated.keys()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = pages()
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, content in generated.items()
                 if not path.exists() or path.read_text() != content]
        extra = set(DIRECTORY.glob("*.mdx")) - generated.keys() | stale_placeholders(generated)
        if stale or extra:
            raise SystemExit(f"Connector pages are stale: {stale + sorted(str(path.relative_to(ROOT)) for path in extra)}")
        placeholders = sum(1 for path in generated if path.parent == LOGOS) // 2
        print(f"{len(generated) - 3 - 2 * placeholders} connector pages, the overview, the navigation group, the title logos and {placeholders} placeholder logos match the snapshot")
    else:
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        for path in set(DIRECTORY.glob("*.mdx")) - generated.keys() | stale_placeholders(generated):
            path.unlink()
        for path, content in generated.items():
            if not path.exists() or path.read_text() != content:
                path.write_text(content)
