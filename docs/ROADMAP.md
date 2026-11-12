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

## Phase 2 — integrate the storage controller before hardware use

Implement a narrow privileged gadget controller that proves release, drains I/O,
checks dirty filesystems without repair, mounts/detaches safely, coordinates one
device owner across processes, and recovers ownership conservatively after crashes.
Test fault injection for every transition. Determine host flush/eject observability
and power continuity before a separately authorized disposable-data Pi trial.
Keep setup/services quarantined until this work is reviewed.

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
