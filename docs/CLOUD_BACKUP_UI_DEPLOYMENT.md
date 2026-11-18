# Cloud Backup monitoring deployment acceptance

**PASS — 15 November 2026.** The existing Raspberry Pi dashboard now displays
the working USB backup worker's sanitized status and history. The owner signed
in privately and confirmed the new panel shows **20 verified, 0 pending and
0 failed**, with the worker observed as running.

Deployed application source is exactly
`12e74b440fc5c2b7bda094a76176f8decc6b32ec`. This record documents that deployment;
it does not activate rclone administration or introduce another development stage.

## Approved changes performed

- Retained the existing dashboard release, Python environment, base unit,
  accounts, stored data and earlier rollback artifacts.
- Installed the reviewed source in a separate root-protected prefix, with its
  source manifest, license and fixed launchers. No pip/dependency/rclone install.
- Reused Python 3.13.5, Flask 3.1.3 and Waitress 3.0.2 from the existing environment.
  Its old distribution metadata remains unchanged; the explicit source prefix
  pins the running application to the accepted commit.
- Added only the dashboard's `ExecStart` override, preserving its previous
  arguments and existing port **8081**, plus `--backup-status`.
- Installed the read-only reporter and 15-second timer, including the explicitly
  approved `timers.target` startup binding. Enabled the timer and observed it
  refresh the report.
- Restarted only the dashboard. Existing browser sessions expired as expected;
  the owner had explicitly approved the one-time private re-sign-in.

The USB controller, backup worker and remote-access service retained their
running process IDs. No USB operation, image mount, networking change, Google
authorization change, backup transfer or rclone administration startup occurred.

## Actual acceptance evidence

| Check | Result |
| --- | --- |
| Dashboard | Active/running/enabled, successful status; public HTTPS login returned 200 |
| Installed artifacts | All 62 source/launcher/license manifest entries and all three deployed unit/override files matched the reviewed artifacts |
| Public assets | JavaScript and CSS byte-matched the accepted source; responses were `no-store` |
| Existing owner sign-in | Owner reported successful private sign-in and correct monitoring panel/counts |
| Real backup totals | Reporter matched protected history: 20 verified jobs, 100 events; no pending/failed jobs |
| History preservation | Every existing job/event row digest matched before and after; SQLite quick check `ok` |
| Credentials/accounts | Worker credential and dashboard account/secret hashes unchanged; existing owner/viewer roles retained |
| Reporter identity | `raspi-backup` user with `raspi-dashboard` primary group; working backup service identity unchanged |
| Shared metadata | Runtime directory 0750 and report 0640 with the intended owner/group; dashboard can read but cannot write |
| Private worker data | Dashboard cannot read worker directory, configuration, rclone credentials or history database |
| Anonymous access | Public monitoring and advanced-status endpoints both returned 401 |
| Role permissions | Installed Pi runtime passed isolated owner/viewer login and permission fixtures; viewer administration denied |
| Timer | Enabled and active/waiting; report refresh observed across its interval |
| Reporter service | Successful oneshot exit status 0; inactive between timer runs is normal |
| Existing appliance | Exact 8 GiB image/approval/inventory and core source/unit/configuration bindings retained; healthy configured USB state |
| Optional GUI | Administration unit absent, activation flag absent and owner entry disabled; no RC API exposed |

The read-only bridge publishes provider labels, counters, timestamps, fixed error
explanations and labels such as `Backup #12`. Private filenames, remote names,
folder/account IDs, raw paths, credentials and provider output are excluded.
Recorded Google verification remains separate from current authorization or
instantaneous connectivity. Progress shows persisted job stages, not invented
byte counts or percentages.

## Initial stopped attempt

The first installer attempt used port 8080 for its health check instead of the
existing 8081. It stopped during preflight before creating a deployment work
directory or changing application files/services; no rollback was needed.
The deployment checker was corrected to derive its probe port from the
preserved service arguments. Focused tests and independent review passed, and
the owner separately approved resumption. The successful attempt preserved the
real port and all other configuration. Both attempts' protected evidence remains
retained. This was an operator-checker error, not a dashboard or Wi-Fi fault.

## Rollback readiness

Fresh private root-owned records retain the previous dashboard unit, account
snapshot, before/after fingerprints and acceptance results. The original release
and environment remain intact. Raw records stay private on the Pi and development
computer; the [public receipt](acceptance/pi-cloud-backup-monitoring-2026-11-15.json)
contains only sanitized acceptance facts.

If a later approved rollback is needed:

1. Stop only the new status timer/reporter and disable its startup binding.
2. Verify and remove only the monitoring dashboard override and its new approval
   marker. Preserve unexpected changes for review rather than deleting them.
3. Reload systemd and restart only the original dashboard configuration.
4. Verify dashboard readiness, unchanged core services and private-data isolation.

Never restore stale account/OAuth state, rewind history, discard staging or swap
USB images as part of dashboard rollback. No rollback was required during the
successful deployment.

## Test boundaries

The accepted source already passed 1,043 local tests with three optional rclone
integration skips and five GitHub checks. Application source did not change for
deployment; that suite was not repeated locally. Additional isolated installer
fault checks, configured-port checks, Pi role fixtures, permission checks,
protected comparisons and public HTTPS/asset checks passed. Independent review
found the successful receipt consistent with those checks.

Owner browser acceptance is user-reported. Existing viewer credentials were not
requested or used; viewer enforcement was tested with isolated accounts on the
installed Pi runtime, alongside unchanged real account/role records. Responsive
markup/CSS was checked; a physical Android visual test was not independently
confirmed. Timer boot enablement is configured, but no additional reboot test
was authorized or performed. No fresh Google connectivity/upload or USB file-copy
test was performed: earlier approved USB-to-Google acceptance is reused.

The optional official rclone GUI remains disabled for a separately approved
future task. Monitoring deployment is complete; stop here for owner review.
