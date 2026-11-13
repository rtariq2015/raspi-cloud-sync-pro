# Development roadmap

## Foundation — complete

Preserve Koseng's history, original README and GPL-3.0; document upstream behavior;
remove automatic deletion; quarantine unsafe setup/services; establish packaging,
safe config, tests and CI.

## Phase 1 — core implemented, integration deliberately blocked

- Ownership state model and exclusive snapshot stager with full enumeration,
  streamed hashes, atomic ready batches and low-space handling.
- SQLite jobs/history, destination/version deduplication, a worker lock,
  independent verification events, bounded retries and interrupted-job recovery.
- Opt-in rclone adapter for Koofr WebDAV/Google Drive remote configurations;
  immutable version paths and remote-content read-back hashes.
- Wi-Fi profile validation for personal/home/hotspot, PEAP/MSCHAPv2 and EAP-TLS;
  upload policy pauses on unknown/portal/limited/offline or disallowed metered links.
- Hardware-free regression tests and an optional real-rclone local-remote test.

See [Phase 1 details](PHASE1.md) for implemented scope and remaining limits.

## Phase 2 — device and network-sharing prototypes implemented; hardware blocked

The Linux configfs mass-storage backend integrates the ownership state machine,
read-only loops/mounts, non-repairing exFAT checks, an intent journal, cross-process
locking and conservative resource recovery. Host release detection is session-bound;
actual kernel/Windows behavior remains to be validated.

USB Ethernet is the preferred mode, using the official Raspberry Pi external
RNDIS/CDC-ECM provider with a stable USB-local network policy and authenticated
SMB3 sharing. SMB deny-write/delete snapshots feed the existing durable queue and
verification/retry engine. Wi-Fi is unnecessary for local file capture. Device
mode plans and future switch guards reject active/unproven transfers; live and
composite switching are unsupported.

Read-only readiness/power diagnostics, gated runtimes, static service/deployment
templates and fault simulations are present. All hardware/network mutations remain
disabled. Follow [the exact Pi trial sequence](PI_TEST_GUIDE.md) only after separate
approval. Real Samba share-mode/lease, Windows driver, USB power and filesystem
recovery tests are still required before deployment.

The [2026-11-13 connectivity baseline](PI_CONNECTIVITY.md) now establishes working
USB management and tested home Wi-Fi on the existing Pi. `network-status` exposes
the current addressing/route/auto-switcher differences from the share runtime
without changing them. Next is a reviewed disposable-file Samba test, preserving
the working management path; cloud-account acceptance follows local capture.

## Phase 3 — cloud account acceptance and operational scheduler

Configure a Koofr WebDAV remote with TLS and a separate app password; configure
Google Drive with a dedicated OAuth client/scope using rclone's headless workflow.
Keep config and tokens outside Git, mode 0600, owned by the service user. Confirm
each configured backend and backup directory before enabling the adapter.

Use test accounts for path encoding, empty/large/changed files, interrupted uploads,
auth expiry, rate/quota failures and downloaded-content checks. Measure real
throughput/memory on the Zero 2 W. Add cancellation, explicit failed-job recovery,
periodic verification/audit and a scheduler with observed network state. Do not
mirror deletions. Large-file resumability remains separate from whole-file retry.

## Phase 4 — OS Wi-Fi management

Implement the [Wi-Fi design](NETWORKING.md) against the selected OS NetworkManager
API. Add scanning, saved-network priorities, guarded activation/checkpoint rollback,
metered policy, enterprise certificate/secret handling and portal guidance.
Do not advertise generic captive-portal support or simultaneous AP/client mode.
Record home/router, Android/iPhone hotspot and managed enterprise test results.

## Phase 5 — local dashboard

Build independent Overview, Wi-Fi, Cloud and History views, informed by Cloud
Stick Pro's feature concepts. Include first-run authentication, protected sessions,
CSRF protection, safe LAN access, redacted diagnostics and accessible mobile UI.
Display live ownership, observed connectivity, per-file version, upload result,
verification method/time, retry time and failure code. Never show verified merely
because upload completed or turn a past result into a claim about current contents.
The web process must remain unprivileged and expose only validated narrow actions.

## Deployment acceptance

Keep automated, local-rclone, test-account and actual Pi evidence separate.
[Hardware matrix and remaining work](PHASE1.md#hardware-testing-still-required).
No initial release will automatically delete USB originals or staging data.
Merging to the default branch and publishing a release require owner approval.
