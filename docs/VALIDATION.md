# Phase 1 validation record

Date: 2026-11-12. Environment: Linux x86-64, Python 3.12.3.
Development branch: `development`. Package version: `0.1.0.dev1`.

| Check | Result and evidence boundary |
| --- | --- |
| Automated tests | 62 tests passed locally with the optional real-rclone test enabled; no cloud account or Pi required |
| Ownership/staging | Unsafe transitions, interruption, host-owned read refusal, complete/deep inventory, old timestamps, same-size changed content, symlinks, manifest tampering, low space and atomic publication |
| Queue/history | Version/destination deduplication, separate uploaded/verified events, completion timestamps, transient/permanent failures, capped backoff, exhaustion, interrupted-job recovery, worker contention and database-write failures |
| Provider adapter | Disabled default, protected config, argument arrays, bounded execution, sanitized errors, no deletion, ambient rclone override removal |
| Real rclone | Version 1.76.0, official Linux amd64 archive checksum verified; temporary local alias remote only; copies, SHA-256 read-back, empty/Unicode/shell-like names, idempotency and corruption rejection |
| Wi-Fi | Personal/hotspot and PEAP/EAP-TLS profile validation; mandatory server trust fields; unknown/portal/limited/offline and metered upload gating |
| Legacy safeguards | Import-safe Python, disabled mounts/loops/services/SCP and retained files; setup guards inspected without execution |
| Packaging | Wheel build/install and installed-package tests; new package and GPL license only, no legacy runtime bundled |
| CLI | Static Phase 1 capabilities, safe example config and bounded read-only history/errors |
| Syntax | Python compilation; both setup scripts parsed with `sh -n`; three service units verified without activation |
| Provenance | Original README/license bytes match `1bc5702`; upstream ancestry and original source locations retained |
| Documentation/Git | Current local links resolve; ignored credentials/state/images/batches/builds; staged whitespace checked |

Run the checks from [CONTRIBUTING.md](../CONTRIBUTING.md). GitHub Actions is
configured for Python 3.9, 3.12 and 3.13, including pinned rclone on 3.12. Local
validation used Python 3.12 only; remote CI results belong to the associated
development-branch commit and should be checked separately.

No actual cloud account, USB host/gadget, Pi, NetworkManager changes, enterprise
authentication or captive-portal sign-in was tested. No formatting, installation,
flashing or service activation was performed. The inherited setup-guard test
initially launched the quarantined scripts against a fake sudo; it has been
replaced by inspection to avoid executing installation scripts at all. The
guards exited before any installation/formatting commands.

The earlier foundation checkpoint had 19 passing tests and is preserved in
commit `de48881`. Its staged-only publication notes described that earlier
checkpoint; the new repository now exists and Phase 1 targets its development
branch. The Cloud Stick Pro repository remains unchanged.

See [remaining hardware acceptance](PHASE1.md#hardware-testing-still-required).
Passing software checks is not evidence of safe concurrent USB filesystem access.
