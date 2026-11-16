import argparse
import html
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "scripts/connector_capabilities.json"
DIRECTORY = ROOT / "essentials/v2/connectors"
INDEX = ROOT / "essentials/v2/connector-providers.mdx"
SCOPED_PROVIDER_EXAMPLES = {
    "slack": ("slack-account-a", "slack-account-b"),
    "github": ("github-account-a", "github-account-b"),
    "linear": ("linear-account-a", "linear-account-b"),
    "notion": ("notion-account-a", "notion-account-b"),
}


def prose(value):
    value = value.replace("With acl_supported on for the provider, permission-aware search", "With Enforce ACL enabled for a provider reporting acl_supported: true, permission-aware search")
    escaped = html.escape(re.sub(r"\s*\u2014\s*", "; ", value), quote=False).replace("{", "&#123;").replace("}", "&#125;")
    escaped = re.sub(r"https?://[^\s]*&lt;[^\s]*", lambda match: f"`{html.unescape(match.group())}`", escaped)
    return re.sub(r"&lt;([A-Za-z_][\w-]*)&gt;", r"`<\1>`", escaped)


def pages():
    snapshot = json.loads(SNAPSHOT.read_text())
    providers = sorted(snapshot["providers"], key=lambda item: item["name"].casefold())
    output = {}
    for provider in providers:
        identifier = provider["provider"]
        name = provider["name"]
        lines = [
            "---", f"title: {json.dumps(name)}",
            f"description: {json.dumps(f'What HydraDB syncs from {name}, and how to connect it.')}",
            "---", "", f"Use the {prose(name)} connector to bring the following content into HydraDB.",
            "", "## What syncs", "",
        ]
        if provider.get("summary"):
            lines.append(prose(provider["summary"]))
        types = provider["indexed_object_types"]
        if types:
            lines.append("The provider description lists these indexed object types:")
            lines.append("")
            lines.extend(f"- `{kind}`" for kind in types)
        elif not provider.get("summary"):
            lines.append("The provider description does not enumerate fixed indexed object types. Use the setup notes below when available; the resource picker shows what your connection can select.")
        lines.extend(["", "## Connect", ""])
        lines.extend([
            "Create one connector per account, using that account's credentials where required. Use a separate connector for a different account; see [account scopes](/essentials/v2/connectors#6-connect-several-accounts-from-one-provider). Shared destinations are allowed, but collection names do not authorize access.",
            "",
        ])
        if identifier in SCOPED_PROVIDER_EXAMPLES:
            first, second = SCOPED_PROVIDER_EXAMPLES[identifier]
            lines.extend([
                f"Set a stable, distinct `provider_account_scope` for each connected account yourself, for example `{first}` and `{second}`. These are caller-provided example identifiers, not validated native account-ID formats. The API stores the supplied value; it does not derive or validate the provider's account identity. Reusing a scope can make sources with matching external IDs overwrite each other in the same destination.",
                "",
            ])
        if provider.get("setup_guide"):
            lines.extend(f"{number}. {prose(step)}" for number, step in enumerate(provider["setup_guide"], 1))
            lines.append("")
        if identifier == "supabase":
            lines.append("Follow the Supabase connection flow in the dashboard. This is a push connector; the webhook triggers, not a polling credential, deliver changes.")
        else:
            lines.append(f"Use provider `{identifier}` with [Create Connector](/api-reference/v2/endpoint/create-connector), then [discover resources](/api-reference/v2/endpoint/discover-connector-resources) and [configure the resources](/api-reference/v2/endpoint/configure-connector) you want to sync. The [provider description](/api-reference/v2/endpoint/list-connector-providers) supplies the credential fields for your deployment.")
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
        output[DIRECTORY / f"{identifier.replace('_', '-')}.mdx"] = "\n".join(lines)
    lines = [
        "---", 'title: "Connector providers"',
        'description: "See what each connector indexes before you connect an account."', "---", "",
        "Choose a provider to see the indexed object types and any provider-specific setup steps. These pages describe content capabilities; availability and credentials come from your deployment's [provider catalog](/api-reference/v2/endpoint/list-connector-providers).", "",
        "| Provider | Indexed object types (examples) |", "| --- | --- |",
    ]
    for provider in providers:
        slug = provider["provider"].replace("_", "-")
        types = provider["indexed_object_types"]
        summary = ", ".join(f"`{kind}`" for kind in types[:3])
        if len(types) > 3:
            summary += ", and more"
        if not summary:
            summary = "See provider page"
        lines.append(f"| [{provider['name']}](/essentials/v2/connectors/{slug}) | {summary} |")
    lines.extend(["", "## Related", "", "- [Connectors](/essentials/v2/connectors): shared setup and sync workflow", ""])
    output[INDEX] = "\n".join(lines)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = pages()
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, content in generated.items()
                 if not path.exists() or path.read_text() != content]
        extra = set(DIRECTORY.glob("*.mdx")) - generated.keys()
        if stale or extra:
            raise SystemExit(f"Connector pages are stale: {stale + sorted(str(path.relative_to(ROOT)) for path in extra)}")
        print(f"{len(generated) - 1} connector pages match the capability snapshot")
    else:
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        for path, content in generated.items():
            path.write_text(content)
