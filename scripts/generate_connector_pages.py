"""Generate one page per connector enabled in production, plus the providers overview.

scripts/production_providers.json is refreshed from GET /connectors/providers on production.
A provider with a copy file in scripts/connector_pages/ gets its page body rendered from that file.
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
NAVIGATION_GROUP = "Connector providers"
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
Logo = namedtuple("Logo", "light dark")
# Production setup steps that explain HydraDB's internals. Each rewrite keeps the effect a customer acts on and drops the reason.
SETUP_STEP_REWRITES = {
    "datadog": {8: "That mirroring takes effect only once HydraDB turns on access-scoped search for this connector. Until then, a private incident is searchable by everyone in your HydraDB tenant. Ask us to turn it on, or leave out the incident_read scope if that is not acceptable: everything else syncs without it."},
    "launchdarkly": {7: "Two things are never indexed. An environment's SDK key and mobile key are dropped before indexing, because they are live credentials for your own environment. So are a member's MFA state and whether they have a password."},
    "sharepoint": {5: "SharePoint item and site permissions are not yet reproduced. Content is readable by everyone in your HydraDB tenant, and every item carries rbac_warning; set a resource access rule for sites that must be restricted."},
}


def prose(value):
    value = value.replace("With acl_supported on for the provider, permission-aware search", "With Enforce ACL enabled for a provider reporting acl_supported: true, permission-aware search")
    escaped = html.escape(re.sub(r"\s*\u2014\s*", "; ", value), quote=False).replace("{", "&#123;").replace("}", "&#125;")
    escaped = re.sub(r"https?://[^\s]*&lt;[^\s]*", lambda match: f"`{html.unescape(match.group())}`", escaped)
    return re.sub(r"&lt;([A-Za-z_][\w-]*)&gt;", r"`<\1>`", escaped)


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
    """Rewrites only the connector providers group of docs.json, so hand-kept groups keep their layout."""
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
    "Result date": "the date HydraDB uses when it ranks results by date.",
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


def load_copy(identifier):
    """Returns the provider's checked-in page copy, or None. Raises ValueError on a copy file that cannot render."""
    path = COPY / f"{identifier}.json"
    if not path.is_file():
        return None
    copy = json.loads(path.read_text())
    problems = [f"missing {key}" for key in sorted(COPY_KEYS - copy.keys())]
    problems += [f"unknown {key}" for key in sorted(copy.keys() - COPY_KEYS - OPTIONAL_COPY_KEYS)]
    connect = copy.get("connect", {})
    problems += [f"connect missing {key}" for key in sorted(CONNECT_KEYS - connect.keys())]
    problems += [f"connect unknown {key}" for key in sorted(connect.keys() - CONNECT_KEYS - OPTIONAL_CONNECT_KEYS)]
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
        lines += [
            f"Turn on **Enforce ACL** for this connector to make search follow {name} permissions. With Enforce ACL off, HydraDB does not enforce them. Only a workspace owner or writer signed in to HydraDB can turn it on. API-key requests must omit `enforce_acl`, including `false`. See [Enforce ACL]({GUIDE}#enforce-acl).",
            "", *callout(copy, "permissions"),
            labelled("Who can read an item", permissions["who_can_read"]),
            labelled("How a person is identified", permissions["identity"]),
            labelled("When permissions cannot be read", permissions["when_unreadable"])
            + " On HydraDB Cloud, with Enforce ACL on, such an item follows the access rule you set on its resource. With no rule, it is hidden from search. Self-hosted deployments can be configured differently. Permission warnings appear in [Get Connector Status](/api-reference/v2/endpoint/get-connector-status).",
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
        f"Use provider `{provider['production_id']}` with [Create Connector](/api-reference/v2/endpoint/create-connector). Then call [Discover Connector Resources](/api-reference/v2/endpoint/discover-connector-resources) to list what the credentials can reach, and [Configure Connector](/api-reference/v2/endpoint/configure-connector) to choose what to sync.",
        "",
    ])
    return lines


def sync_section(copy):
    sync = copy["sync"]
    return [
        "## Keeping in sync", "", *callout(copy, "sync"),
        labelled("First sync", sync["first_sync"]), labelled("New and changed items", sync["changes"]),
        labelled("Edits", sync["edits"]), labelled("Deletions", sync["deletes"]),
        *bullets(sync.get("notes", [])),
        "", "After the first sync, HydraDB syncs every hour by default. Set `sync_interval_seconds` on the connector to change it.", "",
    ]


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


def legacy_body(provider):
    identifier = provider["provider"]
    name = provider["display_name"]
    lines = ["## What syncs", ""]
    if provider.get("summary"):
        lines.extend([prose(provider["summary"]), ""])
    types = provider["indexed_object_types"]
    if types:
        lines.extend(["**Indexed object types**", ""])
        lines.extend(f"- `{kind}`" for kind in types)
    elif not provider.get("summary"):
        lines.append("No fixed object types: what syncs is the set of resources you select when you configure the connector.")
    lines.extend(["", "## Connect", ""])
    lines.extend([
        "Create one connector per account, using that account's credentials where required. Use a separate connector for a different account; see [account scopes](/essentials/v2/connectors#6-connect-several-accounts-from-one-provider). Shared destinations are allowed, but collection names do not authorize access.",
        "",
    ])
    if identifier in SCOPED_PROVIDER_EXAMPLES:
        first, second = SCOPED_PROVIDER_EXAMPLES[identifier]
        lines.extend([
            f"Set a stable, distinct `provider_account_scope` for each connected account yourself, for example `{first}` and `{second}`. These are caller-provided example identifiers. The API stores the supplied value as given; it does not check it against the provider's own account ids. Reusing a scope can make sources with matching external IDs overwrite each other in the same destination.",
            "",
        ])
    if provider.get("setup_guide"):
        rewrites = SETUP_STEP_REWRITES.get(identifier, {})
        lines.extend(f"{number}. {prose(rewrites.get(number, step))}" for number, step in enumerate(provider["setup_guide"], 1))
        lines.append("")
    if identifier == "supabase":
        lines.append("Follow the Supabase connection flow in the dashboard. Changes arrive as they happen, through the database webhooks you set up in Supabase.")
    else:
        lines.append(f"Use provider `{provider['production_id']}` with [Create Connector](/api-reference/v2/endpoint/create-connector), then [discover resources](/api-reference/v2/endpoint/discover-connector-resources) and [configure the resources](/api-reference/v2/endpoint/configure-connector) you want to sync. The [provider description](/api-reference/v2/endpoint/list-connector-providers) supplies the credential fields for your deployment.")
    lines.extend([
        "", "## Common mistakes", "",
        "- A provider page describes indexed content, not the access granted to your credentials. Select resources the connected account can read.",
        "- Synced content is not automatically private to each person. Review [ACL enforcement](/essentials/v2/connectors#enforce-acl) and set resource access rules where needed.",
        "- API-key requests must omit `enforce_acl`, including `false`. Only a signed-in human workspace owner or writer can change **Enforce ACL** in dashboard connector settings, and enabling it requires `acl_supported: true`.",
        "", "## Related", "",
        "- [Connector providers](/essentials/v2/connector-providers)",
        "- [Connectors](/essentials/v2/connectors): setup, sync, and access control",
        "- [Get Connector Status](/api-reference/v2/endpoint/get-connector-status): progress and warnings", "",
    ])
    return lines


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
    rewritten = []
    for provider in providers:
        identifier = provider["provider"]
        name = provider["display_name"]
        row = production[slug(identifier)]
        found = logo(identifier)
        copy = load_copy(provider["production_id"])
        formerly = names["formerly"].get(provider["production_id"])
        called = f"{name} (formerly {formerly})" if formerly else name
        description = copy["description"] if copy else f"What HydraDB syncs from {called}, and how to connect it."
        lines = [
            "---", f"title: {json.dumps(name)}",
            f"description: {json.dumps(description)}",
            f"icon: {json.dumps(found.light)}",
            "---", "",
        ]
        if copy:
            lines += copy_body(provider, copy, row["is_beta"], row["acl_supported"])
            rewritten.append(provider)
        else:
            lines += legacy_body(provider)
        output[DIRECTORY / f"{slug(identifier)}.mdx"] = "\n".join(lines)
    lines = [
        "---", 'title: "Connector providers"', 'sidebarTitle: "All connectors"',
        'description: "See what each connector syncs, what it needs, and who can read what it brings in."', "---", "",
        f"Choose a provider to see what to set up and what it syncs. Availability and credential fields come from your deployment's [provider catalog](/api-reference/v2/endpoint/list-connector-providers). The steps every connector shares are in the [connectors guide]({GUIDE}).", "",
        "## How to read a connector page", "",
        "Each page opens with what the connector brings in and the limits that decide whether it fits. Then:", "",
        "1. **Permissions:** whether the provider's permissions decide who can read an item.",
        "2. **Before you connect:** the credentials, permissions and steps the provider needs.",
        "3. **Keeping in sync:** how history, edits and deletions arrive.",
        "4. **What gets synced:** what one document is, and what HydraDB does with each attribute it keeps.",
        "5. **How it fits into the graph:** the class of each item and the connections the connector creates.",
        "6. **Not synced:** what does not come across.", "",
        "### Attribute labels", "",
        "Each attribute on a connector page carries one label for every use it has.", "",
        *(f"- **{label}:** {meaning}" for label, meaning in USES.items()), "",
        "An attribute without the Searchable text label says so in its description.", "",
        "### Graph classes", "",
        "Every synced item belongs to one class, so similar items from different tools are grouped the same way. A class names the kind of item. It does not promise that every detail below is filled: each connector page says what its items carry and which connections it creates.", "",
        *(f"- **{title}:** {meaning}" for title, meaning in CLASSES.values()), "",
        f"HydraDB also identifies people, organizations, projects and other named things in the searchable text of every item, whichever tool it came from. To steer what it looks for, use [custom instructions]({GUIDE}#4-custom-ingestion-instructions).", "",
    ]
    if len(rewritten) < len(providers):
        links = ", ".join(f"[{provider['display_name']}](/essentials/v2/connectors/{slug(provider['provider'])})" for provider in rewritten)
        lines += [f"These pages follow that layout: {links}. The others list the indexed object types and setup notes while they move to it.", ""]
    by_group = {title: [provider for provider in providers if enabled[slug(provider["provider"])] == title] for title in GROUPS}
    for title, members in by_group.items():
        lines.extend([f"## {title}", "", "<Columns cols={3}>", *(card(provider, logo(provider["provider"])) for provider in members), "</Columns>", ""])
    lines.extend(["## Related", "", "- [Connectors](/essentials/v2/connectors): shared setup and sync workflow", ""])
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
