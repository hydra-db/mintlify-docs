# HydraDB Documentation

Public-facing documentation for **HydraDB**, built with [Mintlify](https://mintlify.com). The site is authored in MDX and configured via `docs.json`.

Live site: [https://docs.hydradb.com](https://docs.hydradb.com)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| [Node.js](https://nodejs.org/) | 18+ | `nvm install 18` or download from nodejs.org |
| [pnpm](https://pnpm.io/) | latest | `npm install -g pnpm` |

## Quick Start

```bash
git clone https://github.com/usecortex/mintlify-docs.git
cd mintlify-docs
make bootstrap   # installs dependencies
make dev         # starts local dev server at http://localhost:3000
```

## Featured developer journey: personalized company assistant

This documentation contribution fixes one complete, commonly confusing path: **building an assistant that uses shared company knowledge while personalizing answers for the current user.**

Follow the journey in order:

1. [Choose Memory or Knowledge](./essentials/v2/memory-or-knowledge.mdx) to decide what belongs in each store and when to use `infer`.
2. [Build a personalized company assistant](./cookbooks/v2/personalized-company-assistant.mdx) to ingest Slack and Notion content as Knowledge, save a per-user Memory, wait for indexing, and retrieve both stores safely with `type: "all"`.

The cookbook includes scope design, app-source ingestion, indexing readiness, authorization guidance, expected output, and troubleshooting. It is designed to be a complete implementation path rather than a collection of disconnected endpoint examples.

## Available Make Targets

| Target | Description |
|--------|-------------|
| `make help` | Show all available targets |
| `make bootstrap` | Zero-to-running setup (install deps, print next steps) |
| `make install` | Install dependencies with pnpm |
| `make dev` | Start Mintlify dev server (http://localhost:3000) |
| `make build` | Validate the documentation build |
| `make clean` | Remove `node_modules/` and `.mintlify/` cache |

## Content Structure

```
mintlify-docs/
├── docs.json                  # Mintlify config (navigation, theme, logos)
│
├── get-started/               # Getting started (v1 & v2)
│   ├── introduction.mdx       #   v1: Welcome
│   ├── core-concepts.mdx      #   v1: Core primitives overview
│   ├── quickstart.mdx         #   v1: 5-minute guide
│   └── v2/                    #   v2: same structure, new API
│       ├── introduction.mdx
│       ├── core-concepts.mdx
│       └── quickstart.mdx
│
├── essentials/                # Essential guides (v1 & v2)
│   ├── memories.mdx           #   v1: User-scoped context
│   ├── knowledge.mdx          #   v1: Shared document context
│   ├── recall.mdx             #   v1: Retrieval
│   ├── app-sources.mdx        #   v1: Slack, Notion, etc.
│   ├── multi-tenant.mdx       #   v1: Sub-tenants & isolation
│   ├── metadata.mdx           #   v1: Schema & filtering
│   ├── context-graphs.mdx     #   v1: Entity relationships
│   ├── architecture.mdx       #   v1: System overview
│   ├── api-results.mdx        #   v1: Using API results
│   ├── semantic-search.mdx    #   v1: Semantic search
│   ├── webhooks.mdx           #   v1: Webhook events
│   └── v2/                    #   v2: same concepts, new API
│       ├── memories.mdx
│       ├── knowledge.mdx
│       ├── query.mdx
│       ├── app-sources.mdx
│       ├── connectors.mdx
│       ├── api-results.mdx
│       ├── metadata.mdx
│       ├── multi-tenant.mdx
│       ├── architecture.mdx
│       ├── context-graphs.mdx
│       ├── bring-your-own-graph.mdx
│       ├── graph-collections-byog.mdx
│       ├── semantic-search.mdx
│       ├── webhooks.mdx
│       └── memory-or-knowledge.mdx  # Decision guide
│
├── api-reference/             # API reference (v1 & v2)
│   ├── openapi.json           #   v1 OpenAPI spec
│   ├── index.mdx              #   v1 API overview
│   ├── sdks.mdx               #   v1 SDK docs
│   ├── error-responses.mdx    #   v1 error codes
│   └── endpoint/              #   v1 endpoint docs
│       ├── add-memory.mdx
│       ├── upload-knowledge.mdx
│       ├── delete-memory.mdx
│       ├── full-recall.mdx
│       ├── recall-preferences.mdx
│       ├── verify-processing.mdx
│       └── ...
│   └── v2/                    #   v2 API reference
│       ├── openapi.json
│       ├── index.mdx
│       ├── sdks.mdx
│       ├── error-responses.mdx
│       └── endpoint/
│           ├── create-tenant.mdx
│           ├── ingest-context.mdx
│           ├── query.mdx
│           └── ...
│
├── cookbooks/                 # End-to-end use cases (v1 & v2)
│   ├── index.mdx
│   ├── glean-clone.mdx
│   ├── ai-chief-of-staff.mdx
│   └── v2/
│       ├── index.mdx
│       └── ...
│
├── plugins/                   # Plugin integrations
│   ├── claude-code.mdx
│   ├── mcp.mdx
│   ├── cli.mdx
│   └── openclaw.mdx
│
├── AGENTS.mdx                 # AI agent integration guide
├── continuity-assurance.mdx   # Deployment & continuity policy
├── archive/                   # Deprecated/old docs
├── snippets/                  # Reusable MDX components
├── images/                    # Image assets
├── logo/                      # Brand logos (light/dark)
├── scripts/                   # Developer scripts
├── style.css                  # Custom styles
└── Makefile                   # Developer workflow targets
```

## Adding & Editing Documentation

1. **Edit an existing page** - Open the corresponding `.mdx` file and modify the content. The dev server hot-reloads changes.

2. **Add a new page** - Create a new `.mdx` file in the appropriate directory, then add its path to the `navigation` section in `docs.json`.

3. **Update navigation** - All page ordering and grouping is controlled in `docs.json` under `navigation.tabs`.

4. **API reference pages** - Endpoint docs live in `api-reference/endpoint/`. The OpenAPI spec is at `api-reference/openapi.json`.

5. **Reusable content** - Shared snippets go in the `snippets/` directory and can be imported into any page.

## Useful Links

- [Mintlify Documentation](https://mintlify.com/docs)
- [MDX Syntax Guide](https://mintlify.com/docs/text)
- [Mintlify Components](https://mintlify.com/docs/components)
- [HydraDB Website](https://hydradb.com)
