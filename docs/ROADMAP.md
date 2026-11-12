# Development roadmap

Everything below is planned unless marked complete. No provider, Wi-Fi manager
or dashboard is implemented in this foundation release.

## 0. Repository foundation — complete

Preserve Koseng's Git history, GPL-3.0 license and original README; review existing
USB/cloud behavior; remove automatic deletion; quarantine entry points; add a
Python package, non-secret config validation, tests and CI.

## 1. Storage ownership and durable staging

Implement the handoff in [SAFETY.md](SAFETY.md), complete inventory, immutable
batches, streaming hashes, durable jobs, recovery and disk-space backpressure.
Fault-injection tests must prove unknown ownership and failed unmount prevent
further access. Only then plan an authorized disposable-data hardware trial.

## 2. Koofr WebDAV

Plan uploads/content checks against `https://app.koofr.net/dav/Koofr/`, using the
account email and a separate app password, following
[Koofr's WebDAV guide](https://koofr.eu/help/koofr_with_webdav/how-do-i-connect-a-service-to-koofr-through-webdav/).
Keep credentials outside Git in restricted service-owned storage, never in the
dashboard client or public configuration.

Evaluate streaming HTTP versus rclone's [WebDAV backend](https://rclone.org/webdav/)
on the chosen Pi OS. Require TLS checks, correct path encoding, bounded memory,
rate-limit handling, retries and version-preserving destinations. Test Unicode
paths, empty/large files, changed versions, interrupted uploads, wrong credentials
and read-back hashing where server checksums cannot prove content equality.
Keep originals and staging.

## 3. Google Drive through rclone

Use the [Drive backend](https://rclone.org/drive/) with a dedicated configured
remote, appropriate OAuth client/scope and protected rclone configuration outside
Git. Follow [headless setup](https://rclone.org/remote_setup/) for the Pi. Check
current OAuth requirements during implementation; Drive documentation now
recommends creating an own client ID.

Invoke rclone with argument arrays, an explicit config path, bounded transfers,
cancellation, timeouts, backoff and structured results. Copy into versioned backup
directories without deletion propagation. Process exit is an upload result;
independent remote-content checking must precede verified. Hide tokens and raw
provider output. Test expired authorization, rate/quota errors, large files,
retries and persistence using fake executables before test-account integration.

## 4. Improved Wi-Fi configuration

Inspect the supported Pi OS network stack and use its manager through a narrow
service interface. Add guided scans, saved networks/priorities, reconnect/fallback,
phone-hotspot support and clear diagnostics. Test a new connection and roll back
failed changes to avoid stranding the device. Keep credentials out of logs/URLs.
Define recovery over a separately configured management connection; do not assume
simultaneous AP/client support. Simulate failures before changing real networks.

## 5. Modern local dashboard

Use independently implemented Overview, Wi-Fi, Cloud and History views. Show
storage ownership/activity, saved networks, destination setup/checks, and per-file
uploaded/verified/paused/failed results. Build a responsive accessible interface
with first-run authentication, protected sessions, CSRF protection, redacted
diagnostics and explicit recovery. Bind locally until secure LAN access is set up.
Do not expose rclone remote control or privileged shell access. Keep the web
process unprivileged and validate narrow storage/network commands.

## Reference boundary and acceptance

[Cloud Stick Pro](https://github.com/rtariq2015/cloud-stick-pro) supplies feature
inspiration only; no source/assets/history are imported or changed. Before a
deployment release, record automated, test-account and actual Zero 2 W evidence
separately. Unit tests or UI previews cannot establish real USB/power-loss safety.
No automatic deletion is planned for the initial runtime.
