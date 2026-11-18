# Cloud Backup monitoring and optional administration

**Monitoring deployed and validated — 15 November 2026.** The owner confirmed
the real dashboard's 20 verified / 0 pending / 0 failed panel. See the
[actual deployment and security results](CLOUD_BACKUP_UI_DEPLOYMENT.md).
The accepted 8 GiB USB controller, Google credentials/destination and backup
worker are unchanged. Safe Eject is still required. Optional rclone administration
remains disabled; monitoring does not start another backup engine.

## Existing installation inspected

Before deployment, a read-only SSH inspection verified the Raspberry Pi model and observed the
existing dashboard and USB backup service running under different accounts:
`raspi-dashboard` and `raspi-backup`. The previous dashboard had no `--history`,
`--worker-state` or status-bridge argument, explaining why it cannot currently
display the working USB backup history. The approved deployment now connects
only the sanitized bridge, retaining private worker-file isolation.

The backup service's explicit PATH selects
`/opt/raspi-cloud-sync-pro/tools/mass-storage-bin/rclone`, a symlink to the
root-owned, non-group/world-writable ELF executable
`/opt/raspi-cloud-sync-pro/tools/rclone-1.76.0`. The existing binary reports
v1.76.0. Only read-only version/help inspection was performed; no second rclone
was installed and no configuration or cloud account was opened or changed.

The existing implementation already has job history, last-completed-tick worker
reports, authenticated owner/viewer sessions and separate web-upload Google
setup/Test/Enable controls. Those components are reused.

## Monitoring implemented

The Backups panel and History page now use authenticated `GET /api/backups`:

- Verified, pending, retrying and failed totals across the complete connected
  history, rather than counting only a limited recent list.
- The latest 100 jobs, timestamps, attempt counts, retry times and fixed,
  sanitized error explanations.
- Actual persisted upload/verification stages. No byte counters or percentages
  are invented: the existing worker does not export that telemetry.
- Provider labels and historical Google verification, including its last
  successful timestamp. Private remote names, folder IDs, paths and filenames
  are omitted; rows use labels such as **Backup #12**.
- Separately observed service state, bridge freshness and the age of the last
  worker tick. A stale bridge makes current service health unknown. Long
  transfers/idle schedulers can make a tick old without proving service failure.
- Visible-page refresh every 15 seconds, single-flight requests and a 10-second
  fetch timeout. Failed refreshes clear old healthy displays.

`/api/status` no longer hardcodes `cloud="not_configured"`. It reports historical
Google evidence or unknown status, separately from dashboard OAuth setup.
Opening the dashboard does not verify current Google authorization. A running
service and previous success are not proof of current cloud connectivity.

The legacy authenticated `/api/history` route uses the same sanitized projection
so it cannot bypass filename, destination and error redaction. Owner/viewer
monitoring permissions are preserved. The separate existing web-upload file
browser is unchanged.

## Read-only account bridge

The optional new reporter runs as **raspi-backup**, with **raspi-dashboard** as
the reporter's primary group. It reads only the existing worker-status JSON,
history database and a fixed `systemctl show` query for the existing USB backup
unit. It never reads rclone configuration, starts services, uploads, authenticates
to Google or invokes rclone. The worker's own identity/group remains unchanged.

SQLite aggregates and recent rows share one `mode=ro`, `query_only` transaction
with bounded query/lock time. Normal WAL visibility is retained; immutable mode
is deliberately not used. Missing, locked or invalid history produces unknown
counts, rather than invented zeroes.

The reporter publishes only `backup-status.json` using a temporary file,
explicit mode **0640**, file fsync, atomic replacement and directory fsync in a
**0750** runtime directory. The dashboard group can read this report but cannot
write it or traverse the private worker directory. No broad/default ACL or
credential-directory permission change is required. Failed publication retains
the prior report, which becomes stale.

The web reader enforces path/type/ownership/mode/link-count/size checks and a
strict schema, finite timestamps, fixed provider labels and error allowlists.
It recomputes both bridge and worker ages when reading. Unknown extra fields,
private error strings and malformed reports cannot pass into the response.

Source examples, deliberately without automatic enablement:

- [Reporter service](../deploy/systemd/raspi-cloud-backup-status.service.example)
- [15-second timer](../deploy/systemd/raspi-cloud-backup-status.timer.example)

The approved dashboard configuration adds only:

```text
--backup-status /run/raspi-cloud-backup-status
```

Existing direct `--history`/`--worker-state` options still support readable local
fixtures, but are not a reason to expose the production worker directory.
When the bridge is explicitly selected, failure does not fall back to private
source files.

## Cloud tests and advanced tools

The existing Google web **Test** uploads/verifies a synthetic object and can
pause web-backup permission. It remains clearly separate from the working USB
backup monitor. This milestone has initiated no real Google test or upload and
adds no automatic connection-test button for the production USB account.

The deployed owner-only **Advanced Tools** entry remains disabled. Opening the
actual official rclone GUI requires a separately approved installation. See
[rclone administration](RCLONE_ADMIN.md). Viewer accounts receive neither its
navigation entry nor access to the readiness endpoint.

Without `--rclone-admin-access`, the new owner endpoint performs no probes.
An approved opt-in only observes fixed loopback GUI/API login endpoints; it does
not prove a browser tunnel or successful admin login. The owner must confirm
both SSH forwards are running on the browsing computer before opening a new
tab. A remote phone's localhost is that phone, not the Pi. No credentials,
login-token URL or RC API URL are supplied to the frontend; no public proxy is
implemented.

The optional GUI uses a separate empty configuration and independent login;
it does not gain access to the live worker's remotes, files or Google tokens.
Both the GUI and RC API remain loopback-only. Future public one-click remote
administration would require a separate authentication/proxy security review.

## Validation and deployment boundary

Synthetic tests cover complete versus recent totals, all job states, stopped
services, disconnected networks, missing/stale/invalid reports, safe error
mapping, WAL visibility, locked databases, failed atomic publication, symlinks,
hard links, file permissions and anonymous/owner/viewer access. Node fixtures
exercise polling, timeouts, stale-display clearing, stage-only progress and
explicit tunnel confirmation. No real backup files or cloud accounts are used.

Actual local result: **1,046 tests run, 1,043 passed, three optional local-rclone
integration tests skipped**. The suite includes 41 new monitoring/admin/UI
tests. JavaScript syntax and whitespace checks passed. The three new systemd
templates parsed using temporary copies on Zorin; verification reported only
the two expected missing Pi executable paths. This is not Pi runtime acceptance.
No headless browser was available locally, so mobile validation used the existing
responsive layout and synthetic DOM tests, not a physical Android browser.

Local commands:

```sh
PYTHONPATH=src:tests .venv/bin/python -m unittest test_backup_status test_dashboard_backup_ui test_dashboard_advanced_ui test_rclone_admin_configuration -v
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -v
node --check src/raspi_cloud_sync_pro/static/dashboard.js
git diff --check
```

For any future deployment, the owner must approve installation of the tested
dashboard/reporter source and reviewed units, resolve the actual protected
worker-state paths in the example, verify the reporter's group/output
permissions, start its timer and restart **only the dashboard** with the bridge
argument. No backup-worker or USB-controller restart is required by this design.
The live acceptance must compare sanitized counts with protected history and
prove the dashboard identity cannot read worker credentials. Keep the previous
dashboard package/unit for rollback; remove the new argument/stop only the new
reporter if it fails. Never rewind backup history or alter the USB image.

Optional GUI account/credential provisioning, startup, both-port authentication
and tunnel acceptance require separate approval. The GUI flag stays off until
that acceptance. No source preparation here grants deployment permission.
