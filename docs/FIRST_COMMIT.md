# First Raspi Cloud Sync Pro commit

This repository already has upstream commits. Extend that history; do not run
`git init`, create an orphan branch or squash the upstream ancestry. Keep the
`upstream` remote pointing at Koseng. Never publish to Cloud Stick Pro.

After the [contributor checks](../CONTRIBUTING.md), review and stage changes:

```sh
git status --short
git diff --stat
git diff --check
git add README.md README.upstream.md LICENSE .gitignore .gitattributes pyproject.toml CONTRIBUTING.md config docs tests .github src
git diff --cached --check
git diff --cached --stat
```

Suggested first project commit:

```sh
git commit -m "Initialize Raspi Cloud Sync Pro foundation and quarantine unsafe legacy runtime"
```

For publication, create a new empty GitHub repository named
`raspi-cloud-sync-pro` in the intended account, without initializing a README or
license. Add its actual URL as `origin`, inspect the push destination, then push
the current branch. No account URL is assumed here. Do not force-push or push to
`upstream`.

These instructions do not create a commit/repository or push. No hardware needs
to be connected, formatted or flashed.
