# `_ci-proposed/` — docs → HydraDB sync (manual install)

This folder is a **staging area**, not active CI. It holds a GitHub Action + script
that ingest this repo's Mintlify pages into a HydraDB collection so the Ask-AI
widget (`/askai.js`) can retrieve and cite them. It lives here because the agent
that generated it may not have permission to push into `.github/`. **You** move
the two files into place.

## 1. Copy these files

| From (this folder) | To (ideal location) |
| --- | --- |
| `_ci-proposed/workflows/sync-docs.yml` | `.github/workflows/sync-docs.yml` |
| `_ci-proposed/scripts/sync-docs.mjs`   | `scripts/sync-docs.mjs` |

Then delete `_ci-proposed/`. No dependency changes are needed — `@hydradb/sdk`
is already a dependency of this repo.

> ⚠️ **`.gitignore` gotcha:** this repo's `.gitignore` has a bare `scripts` rule,
> so `scripts/sync-docs.mjs` is ignored by default. Force-add it:
> `git add -f scripts/sync-docs.mjs` (the existing `scripts/` files are tracked
> the same way).

## 2. Set the secret and variables (GitHub → repo Settings)

All of these belong to **this repo** (`hydra-db/mintlify-docs`), because that is
where the Action runs.

| Kind | Name | Value | Required |
| --- | --- | --- | --- |
| **Secret** (Settings → Secrets and variables → Actions → *Secrets*) | `HYDRA_DB_API_KEY` | A HydraDB API key with **write/ingest** access to the docs database (see §4) | ✅ Yes |
| Variable (same page → *Variables*) | `HYDRA_DB_DATABASE` | The database that holds the public docs, e.g. `hydra_docs` | Optional (defaults to `hydra_docs`) |
| Variable | `HYDRA_DB_DOCS_COLLECTION` | The collection the widget queries, e.g. `docs` | Optional (defaults to `docs`) |

## 3. Run it

- **Automatic:** every push to `main` that touches a `.mdx`/`.md` file syncs only
  the changed pages (`--since-ref`), and prunes pages you deleted.
- **Manual / full re-ingest:** Actions tab → *sync-docs* → *Run workflow* →
  tick **full** to re-ingest everything.
- **Locally:** `HYDRA_DB_API_KEY=… node scripts/sync-docs.mjs --dry-run`

## 4. Which HydraDB API key, and where each key lives

There are **two different keys**, used in two different places — don't mix them:

| Purpose | Which key | Where it is set |
| --- | --- | --- |
| **Ingest** docs into the collection (this Action) | A HydraDB key with **write** scope on the docs database | GitHub **Actions secret** `HYDRA_DB_API_KEY` in `hydra-db/mintlify-docs`. Server-side (CI only) — never shipped to the browser. |
| **Answer** questions in the browser (the widget) | A **public, read-only, docs-scoped** HydraDB key (or none, if your ask endpoint injects it) | `window.HydraAskAI.apiKey` / `window.HYDRA_ASKAI_KEY` in the docs site. This one is intentionally browser-visible and must be rate-limited + domain-allowlisted server-side (Kapa/Inkeep model). |

> The ingest key is powerful (it writes content) and must **only** live as a CI
> secret. The widget key is a scoped read key and is safe to expose, the same way
> Kapa's `data-website-id` and Inkeep's `NEXT_PUBLIC_INKEEP_API_KEY` are public.

## 5. What gets ingested

Every `.mdx`/`.md` page under the content dirs (`api-reference/`, `essentials/`,
`cookbooks/`, `get-started/`, `plugins/`, and root-level pages). Excluded:
`archive/`, `snippets/`, `footer*`, `askai-harness/`, tooling dirs. YAML
frontmatter `title`/`description` is used for the title; the page URL is its
Mintlify path (`essentials/settings.mdx` → `/essentials/settings`).
