# Contributing

This GPL-3.0 project derives from Koseng's Raspberry Pi project. Preserve its
license/notices and document legacy modifications. Keep Cloud Stick Pro as a
read-only feature/design reference.

Read [the upstream review](docs/UPSTREAM_REVIEW.md) and
[safety design](docs/SAFETY.md). Do not restore deletion or remove quarantine as
routine cleanup. Ownership, crash recovery and hardware evidence must precede
runtime activation.

## Local checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m raspi_cloud_sync_pro check-config config/config.example.json
PYTHONPATH=src python3 -m raspi_cloud_sync_pro status
python3 -m compileall -q src/raspi_cloud_sync_pro src/home/pi tests
sh -n src/home/pi/execute_setup_bullseye.sh
sh -n src/home/pi/execute_setup_bookworm.sh
git diff --check
```

Tests must use temporary data and mocked process/cloud boundaries, never mount
images, execute Pi installation scripts, format storage, contact real accounts or
require root. Check setup quarantine by inspection and shell syntax parsing only.
Add behavior tests
for transitions, provider failures and persistence as features arrive. Report
simulated and hardware checks separately.

The optional real-rclone test uses a temporary local alias remote only; set
`RASPI_TEST_RCLONE=/absolute/path/to/rclone` to include it. CI enables it on Python
3.12 using the pinned official rclone 1.76.0 archive and SHA-256 checksum. Python
3.9/3.13 jobs may skip that one test. Keep downloads under temporary/tool directories
and do not use cloud credentials in CI. See [Phase 1](docs/PHASE1.md).

Work on `development` or a topic branch; push only to the new project's `origin`.
Do not merge to the default branch or publish a release without owner approval.

Phase 2 device tests use in-memory sysfs/command adapters and temporary files.
Never enable the real hardware code gate to run automated tests. Do not execute
official provider installers or retained Pi setup scripts. `mode-plan`,
`diagnostics` and template parsers are read-only; controller/server units remain
static and disabled. Actual USB, Samba lease/share-mode, electrical and Windows
driver evidence must be recorded separately from simulations.

Use ignored `config/config.local.json` for local settings. Never add credentials
to tracked legacy `config.json` or `scp-copy.exp`: ignore rules do not protect
already tracked files. Inspect staged changes for passwords, tokens, device logs
and user data before committing. Justify new dependencies against Pi constraints.

## Editor setup

Recommended VS Code extensions are Microsoft Remote - SSH (`ms-vscode-remote.remote-ssh`),
Python (`ms-python.python`), Pylance (`ms-python.vscode-pylance`) and Astral Ruff
(`charliermarsh.ruff`). Select the project's virtual environment for Python/tests.
Remote - SSH should use verified host keys and a project-specific key; no password
or key belongs in workspace settings or Git. Ruff is for new project code; avoid
automatically reformatting the preserved upstream tree or generated Windows files.
