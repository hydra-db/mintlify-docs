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
git clone https://github.com/hydra-db/mintlify-docs.git
cd mintlify-docs
make bootstrap   # installs dependencies
make dev         # starts local dev server at http://localhost:3000
```

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
├── index.mdx                  # Home / landing page
├── concepts.mdx               # Core concepts overview
├── quickstart.mdx             # Getting started guide
├── essentials/                 # Essential guides
│   ├── recall.mdx             #   Recall functionality
│   ├── memories.mdx           #   Working with memories
│   ├── context-graph.mdx      #   Context graph
│   ├── multi-tenant.mdx       #   Multi-tenancy
│   ├── metadata.mdx           #   Metadata handling
│   └── use-api-results.mdx    #   Using API results
├── plugins/                   # Plugin integrations
│   ├── claude-code.mdx        #   Claude Code plugin
│   ├── open-claw.mdx          #   OpenClaw plugin
│   └── mcp.mdx                #   MCP integration
├── use-cases/                 # Example use cases
│   ├── glean-clone.mdx        #   Glean clone
│   ├── ai-chief-of-staff.mdx  #   AI Chief of Staff
│   ├── ai-linkedin-recruiter.mdx  # AI LinkedIn Recruiter
│   └── ai-travel-planner.mdx  #   AI Travel Planner
├── api-reference/             # API reference docs
│   ├── openapi.json           #   OpenAPI spec
│   ├── sdks.mdx               #   SDK overview
│   ├── error-responses.mdx    #   Error responses
│   └── endpoint/              #   Individual endpoint docs
├── snippets/                  # Reusable MDX snippets
├── images/                    # Image assets
├── logo/                      # Brand logos (light/dark)
├── scripts/                   # Developer scripts
│   └── bootstrap.sh           #   Project bootstrap
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
