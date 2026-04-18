# Security Policy

## Scope

The Weekly Issue Arena is a static site backed by automated GitHub Actions workflows. Security-relevant areas include:

- **CI/CD workflows** — GitHub Actions that fetch data, update state files, and push commits using `GITHUB_TOKEN`
- **Rendered external data** — issue titles, usernames, and repo names sourced from the GitHub API are rendered into the README and the site
- **GitHub API tokens** — used in workflows for API access and authenticated pushes

## Supported Versions

Only the latest version on the `main` branch is supported. There are no versioned releases.

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, report vulnerabilities privately via **GitHub private advisory (preferred):** use the "Report a vulnerability" button on the [Security tab](https://github.com/claudiotancredi/weekly-issue-arena/security/advisories).

Include as much detail as possible:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You can expect an initial response within 72 hours.

## Security Considerations

This project does **not** handle user authentication, passwords, or personal data beyond public GitHub usernames. The main risks are:

- **Injection via crafted content** — issue titles or PR bodies rendered into Markdown or HTML without proper escaping
- **Workflow token misuse** — `GITHUB_TOKEN` permissions are scoped to `contents: write` and `discussions: write`
- **Dependency vulnerabilities** — Python and npm dependencies used in scripts and the site build
