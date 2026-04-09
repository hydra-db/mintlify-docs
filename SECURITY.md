# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in mintlify-docs, please report it responsibly. **Do not open a public GitHub issue for security vulnerabilities.**

### How to Report

Send an email to **security@hydradb.com** with the following information:

- A description of the vulnerability and its potential impact.
- Steps to reproduce the issue, including any relevant configuration or environment details.
- The affected version(s) of mintlify-docs.
- Any suggested fix or mitigation, if you have one.

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within 3 business days.
- **Assessment**: We will investigate and assess the severity of the vulnerability. We may reach out to you for additional details.
- **Resolution**: We aim to provide a fix or mitigation within 30 days of confirming the vulnerability, depending on complexity.
- **Disclosure**: Once a fix is released, we will coordinate with you on public disclosure. We follow a responsible disclosure timeline and will credit you (unless you prefer to remain anonymous).

## Scope

This security policy covers the mintlify-docs repository, including:

- All MDX documentation content and configuration files.
- API reference specifications.
- Build and deployment configurations.
- CI/CD workflows in `.github/workflows/`.

### Out of Scope

- The HydraDB API service itself (report those to HydraDB directly via https://docs.hydradb.com).
- The Mintlify platform itself (report those to Mintlify directly).
- Third-party dependencies (report those to the respective maintainers, but let us know if a dependency vulnerability affects mintlify-docs).

## Supported Versions

We provide fixes for the latest content on the `main` branch.

| Version | Supported |
|---------|-----------|
| `main` (latest) | Yes |
| Older releases | No |

## Best Practices for Contributors

When contributing to mintlify-docs, follow these security practices:

- Never commit API keys, tokens, passwords, or other credentials to the repository.
- Review your changes for accidental inclusion of secrets before submitting a PR.
- Do not include internal system details, architecture specifics, or security configurations in public documentation.
