# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in the HydraDB Documentation, please report it responsibly. **Do not open a public GitHub issue for security vulnerabilities.**

### How to Report

Send an email to **security@hydradb.com** with the following information:

- A description of the vulnerability and its potential impact.
- Steps to reproduce the issue, including any relevant URLs or page references.
- Any suggested fix or mitigation, if you have one.

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within 3 business days.
- **Assessment**: We will investigate and assess the severity of the vulnerability. We may reach out to you for additional details.
- **Resolution**: We aim to provide a fix or mitigation within 30 days of confirming the vulnerability, depending on complexity.
- **Disclosure**: Once a fix is released, we will coordinate with you on public disclosure. We follow a responsible disclosure timeline and will credit you (unless you prefer to remain anonymous).

## Scope

This security policy covers the mintlify-docs repository, including:

- Documentation content accuracy (e.g., incorrect API usage examples that could lead to insecure implementations).
- Exposed secrets, API keys, or credentials accidentally included in documentation or configuration files.
- Client-side scripts or embedded code snippets in the documentation site.
- CI/CD workflows in `.github/workflows/`.

### Out of Scope

- The HydraDB API service itself (report those directly to HydraDB at **security@hydradb.com**).
- The Mintlify platform itself (report those to Mintlify directly).
- Third-party dependencies (report those to the respective maintainers, but let us know if a dependency vulnerability affects this documentation site).
- Broken links or typos (these are not security issues -- please open a regular issue instead).

## Supported Versions

We provide security fixes for the latest content on the `main` branch. Older versions are not actively maintained.

| Version | Supported |
|---------|-----------|
| `main` (latest) | Yes |
| Older releases | No |

## Best Practices for Contributors

When contributing to the HydraDB Documentation, follow these security practices:

- Never commit API keys, tokens, passwords, or other credentials to the repository.
- Use placeholder values (e.g., `YOUR_API_KEY`) in all code examples and configuration snippets.
- Review your changes for accidental inclusion of secrets before submitting a PR.
- Ensure code examples follow secure coding practices and do not demonstrate insecure patterns.
