# History and development branch workflow

The new project repository is
<https://github.com/rtariq2015/raspi-cloud-sync-pro>.
`origin` points there; `upstream` points to Koseng's original project. Upstream
commit `1bc57020851edf2860f0b44ea4a0ecd7e2904615` remains in the ancestry.
Do not initialize an orphan history, squash the upstream commits, force-push,
or use Cloud Stick Pro as a destination.

Foundation work is committed separately from the Phase 1 implementation on
`development`. Future work should extend this branch or a topic branch based on it.
Run the checks in [CONTRIBUTING.md](../CONTRIBUTING.md), inspect the staged diff,
and verify the push URL before committing/pushing:

```sh
git branch --show-current
git remote -v
git diff --cached --check
git diff --cached --stat
```

Only `origin/development` is a push destination for this phase. GitHub uses
`development` as the default view so the project homepage shows current progress.
This repository setting does not merge into or change `main`; main and the
upstream repository remain untouched. Main-branch merges and releases require
the owner's approval. The `LICENSE` and `README.upstream.md` must remain exact
upstream copies; `.gitattributes` preserves their bytes.
