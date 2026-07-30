# Contributing to mintlify-docs

Welcome, and thank you for your interest in contributing to mintlify-docs. This project contains the public-facing documentation for HydraDB at [docs.hydradb.com](https://docs.hydradb.com), and we appreciate contributions of all kinds -- typo fixes, content improvements, new guides, and structural enhancements.

All participants in this project are expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md). Please read it before contributing.

---

## Developer Certificate of Origin (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) instead of a Contributor License Agreement (CLA). The DCO is a lightweight mechanism that certifies you have the right to submit the content you are contributing. Every commit you submit **must** include a `Signed-off-by` line, and this requirement is enforced by CI.

### How to sign off your commits

Add the `-s` flag when committing:

```bash
git commit -s -m "docs: fix typo in quickstart guide"
```

This appends a line like the following to your commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match your Git configuration. You can verify your settings with:

```bash
git config user.name
git config user.email
```

If you have already made commits without signing off, you can amend the most recent commit:

```bash
git commit --amend -s --no-edit
```

Or rebase to sign off multiple commits:

```bash
git rebase --signoff HEAD~N
```

where `N` is the number of commits to update.

**Commits without a valid `Signed-off-by` line will be rejected by CI and cannot be merged.**

For the full text of the DCO, see: [https://developercertificate.org/](https://developercertificate.org/)

---

## Getting Started

### Fork and clone

1. Fork the repository on GitHub.
2. Clone your fork locally:

```bash
git clone https://github.com/<your-username>/mintlify-docs.git
cd mintlify-docs
```

1. Add the upstream remote:

```bash
git remote add upstream https://github.com/usecortex/mintlify-docs.git
```

### Set up the development environment

Install the Mintlify CLI for local preview:

```bash
npm install -g mintlify
```

### Run a local preview

Start the local development server:

```bash
mintlify dev
```

This will open a local preview of the documentation site. Changes to MDX files will hot-reload automatically.

---

## Branch Naming Convention

Create a new branch from `main` for every change. Use the following prefixes:

- `docs/` -- content changes (e.g., `docs/update-quickstart`)
- `fix/` -- fixes to broken links, formatting, etc. (e.g., `fix/broken-api-link`)
- `feat/` -- new documentation pages or sections (e.g., `feat/sdk-guide`)
- `chore/` -- maintenance, CI, and tooling (e.g., `chore/update-nav`)

---

## Commit Message Format

This project follows the [Conventional Commits](https://www.conventionalcommits.org/) specification.

### Format

```
type(scope): description
```

### Types


| Type       | Purpose                               |
| ---------- | ------------------------------------- |
| `docs`     | Documentation content changes         |
| `fix`      | Fixes to formatting, links, or errors |
| `feat`     | New documentation pages or sections   |
| `chore`    | Maintenance, CI, or tooling changes   |
| `refactor` | Restructuring without content change  |


### Examples

```
docs(api): add search endpoint examples
fix(quickstart): correct installation command
feat(guides): add SDK integration guide
chore(nav): reorganize sidebar navigation
```

### Signing off

Every commit must include the DCO sign-off.

---

## Pull Request Guidelines

- **Reference an issue.** Every PR must reference an existing GitHub issue. If no issue exists for your change, create one first and wait for acknowledgment from a maintainer before starting work.
- **Fill out the PR template completely.** Do not delete sections from the template.
- **Keep PRs focused.** Each PR should contain one logical change. Avoid bundling unrelated fixes.
- **All CI checks must pass.**
- **At least one maintainer review is required** before any PR can be merged.
- **Rebase on `main` before requesting review.**

---

## Content Guidelines

- **Write clearly and concisely.** Use simple language and short sentences.
- **Use MDX components** provided by Mintlify (callouts, code blocks, tabs, etc.) where appropriate.
- **Include code examples** for all API endpoints and SDK methods.
- **No internal details.** Do not include internal architecture, infrastructure details, or security configurations in public docs.
- **No hardcoded credentials.** Use placeholder values like `sk_live_your_api_key` in examples.

### Code snippet convention

Readers copy-paste individual blocks, so every fenced **executable** code block (code a
reader would run: Python/TypeScript/etc. making SDK or API calls) must be one of:

1. **Standalone** — includes its imports and client construction, and runs as-is when
   pasted into a fresh file. The **first** executable block on a page (and the first
   block of each language in a `CodeGroup`) must be standalone.
2. **Continuation** — depends on state (usually `client`) defined in an **earlier block
   on the page** — not necessarily the immediately preceding one. It must say so on its
   **first line** with a language-appropriate marker comment naming what it assumes:

   ```ts
   // Continues from an earlier block (assumes `client`)
   ```

   ```python
   # Continues from an earlier block (assumes `client`)
   ```

**Non-executable blocks are exempt**: Mermaid diagrams, JSON/JSONC payloads, YAML/config
fragments, shell commands, and `requirements.txt`-style listings need no marker — the
rule applies to code a reader is expected to paste into a program and run.

Never publish a block that silently depends on earlier state — the DX audit found 217
such fragments, and they are the main reason documented snippets fail when readers run
them in isolation. Declaration-only blocks (code that defines things but never calls
the API) should be folded into a runnable block or marked as continuations too.

---

## What We Will NOT Accept

- PRs without a linked issue.
- Content that exposes internal system details or security configurations.
- Changes that break the documentation build.
- PRs that do not pass CI checks.
- Cosmetic-only changes unless they meaningfully improve readability.

---

## Reporting Issues

### Content errors

Use the **Bug Report** issue template for:

- Incorrect information in the docs.
- Broken links or missing pages.
- Formatting issues.

### New content requests

Use the **Feature Request** issue template for:

- Requests for new guides or tutorials.
- Suggestions for improving existing content.
- API documentation gaps.

**Before opening a new issue, search existing issues to avoid duplicates.**

---

## Thank You

Every contribution -- whether it is a typo fix, a new guide, or a structural improvement -- makes the HydraDB documentation better. We appreciate your time and effort.