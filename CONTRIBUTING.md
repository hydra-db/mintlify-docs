# Contributing to HydraDB Documentation

Welcome, and thank you for your interest in contributing to the HydraDB Documentation. This project is the public-facing documentation site for HydraDB, built with [Mintlify](https://mintlify.com). We appreciate contributions of all kinds -- typo fixes, content improvements, new guides, and API reference updates.

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

For the full text of the DCO, see: https://developercertificate.org/

---

## Getting Started

### Fork and clone

1. Fork the repository on GitHub.
2. Clone your fork locally:

```bash
git clone https://github.com/<your-username>/mintlify-docs.git
cd mintlify-docs
```

3. Add the upstream remote:

```bash
git remote add upstream https://github.com/usecortex/mintlify-docs.git
```

### Set up the development environment

The fastest way to get a working environment is with `make`:

```bash
make bootstrap
```

This installs all dependencies and prints next steps. If you prefer to do it manually:

```bash
pnpm install        # or: npm install
```

If you do not have `pnpm` installed:

```bash
npm install -g pnpm
```

### Start the local preview server

```bash
make dev            # or: pnpm mintlify dev
```

This starts a local development server at `http://localhost:3000` where you can preview your documentation changes in real time.

### Verify your setup

Navigate to `http://localhost:3000` in your browser. You should see the HydraDB documentation site. Any changes you make to `.mdx` files will be reflected automatically.

### Validate the build

Before submitting a PR, verify the documentation builds without errors:

```bash
make build          # or: pnpm mintlify validate
```

---

## Branch Naming Convention

Create a new branch from `main` for every change. Use the following prefixes:

- `docs/` -- content changes (e.g., `docs/update-quickstart`)
- `fix/` -- bug fixes in docs content or configuration (e.g., `fix/broken-api-link`)
- `feat/` -- new documentation pages or sections (e.g., `feat/add-migration-guide`)
- `chore/` -- maintenance, CI, and tooling (e.g., `chore/update-mintlify-config`)

---

## Commit Message Format

This project follows the [Conventional Commits](https://www.conventionalcommits.org/) specification.

### Format

```
type(scope): description
```

### Types

| Type       | Purpose                                  |
|------------|------------------------------------------|
| `docs`     | Documentation content changes            |
| `fix`      | Fixes to content, links, or configuration |
| `feat`     | New documentation pages or sections      |
| `chore`    | Maintenance, CI, or tooling changes      |
| `refactor` | Restructuring content without changing meaning |

### Examples

```
docs(quickstart): clarify API key setup steps
fix(api-reference): correct endpoint URL for delete-memory
feat(guides): add migration guide from v1 to v2
chore(ci): update Mintlify GitHub Action version
```

### Signing off

Every commit must include the DCO sign-off. A complete commit message looks like:

```
docs(quickstart): clarify API key setup steps

Rewrite the authentication section to include screenshots and
step-by-step instructions for obtaining an API key.

Signed-off-by: Jane Developer <jane@example.com>
```

---

## Pull Request Guidelines

- **Reference an issue.** Every PR must reference an existing GitHub issue. If no issue exists for your change, create one first and wait for acknowledgment from a maintainer before starting work.
- **Fill out the PR template completely.** Do not delete sections from the template.
- **Keep PRs focused.** Each PR should contain one logical change. Avoid bundling unrelated fixes.
- **All CI checks must pass.** This includes linting, build verification, and DCO verification.
- **At least one maintainer review is required** before any PR can be merged.
- **Rebase on `main` before requesting review.** Ensure your branch is up to date and has no merge conflicts:

```bash
git fetch upstream
git rebase upstream/main
```

---

## Content Guidelines

- **Write clearly and concisely.** Use short sentences and active voice.
- **Use proper MDX formatting.** Follow the existing patterns in the repository for headings, code blocks, callouts, and tabs.
- **Use placeholder values in code examples.** Never include real API keys, tokens, or credentials. Use `YOUR_API_KEY` or similar placeholders.
- **Verify links.** Ensure all internal and external links are valid before submitting.
- **Test locally.** Run `make dev` and verify your changes render correctly in the browser.

---

## What We Will NOT Accept

To maintain documentation quality and protect contributors, the following will not be merged:

- PRs without a linked issue.
- Marketing or promotional content not aligned with technical documentation.
- Unverified technical claims or inaccurate API usage examples.
- Content that includes hardcoded secrets or credentials.
- PRs that do not pass CI checks.
- Cosmetic-only changes (whitespace, formatting) unless they are part of a larger, substantive fix.

---

## First-Time Contributors

If this is your first contribution, here is how to get started:

1. **Find a good first issue.** Look for issues labeled [`good first issue`](https://github.com/usecortex/mintlify-docs/labels/good%20first%20issue) -- these are scoped, well-defined tasks suitable for newcomers.
2. **Read the existing docs.** Browse the live site at [docs.hydradb.com](https://docs.hydradb.com) to understand the documentation structure and tone.
3. **Ask questions.** If anything is unclear, open a thread in [GitHub Discussions](https://github.com/usecortex/mintlify-docs/discussions). There are no bad questions.

---

## Review Process

All pull requests go through content review before merging:

1. **At least one maintainer** will review every PR.
2. Reviews focus on **technical accuracy**, **clarity**, and **alignment with documentation conventions**.
3. Maintainers may request changes. Address all review comments before re-requesting review.
4. Once a PR is approved and all CI checks pass, a maintainer will merge it.

Please be patient -- maintainers review on a best-effort basis. If your PR has not received a review within a reasonable time, a polite comment on the PR is welcome.

---

## Reporting Issues and Requesting New Content

### Documentation issues

Use the **Bug Report** issue template. Include:

- The page URL where the issue exists.
- A clear description of what is incorrect or confusing.
- What the content should say instead.

### New content requests

Use the **Feature Request** issue template. Include:

- The topic or feature that needs documentation.
- Why this documentation would be valuable.
- Any reference material or examples.

**Before opening a new issue, search existing issues to avoid duplicates.**

---

## Thank You

Every contribution -- whether it is a typo fix, a new guide, or a content restructure -- makes the HydraDB Documentation better. We appreciate your time and effort.
