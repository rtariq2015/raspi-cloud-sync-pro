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
approval. Samba share-mode behavior passed an isolated container and an encrypted
loopback share on the actual Pi. Windows/USB transfers, leases/reopens, USB power
and filesystem recovery tests are still required before deployment.

The [2026-11-13 connectivity baseline](PI_CONNECTIVITY.md) established home Wi-Fi
and initial USB management; later observation found unstable USB addressing.
`network-status` exposes current addressing/route/auto-switcher differences from
the share runtime without changing them. The Pi loopback Samba test is accepted;
stable USB addressing and actual Windows-host transfers remain, preserving the
working Wi-Fi management path. Cloud-account acceptance follows local capture.

## Phase 3 — scheduler implemented; first Google Drive QA backup accepted

The opt-in completed-batch worker and small Rust scheduler are implemented and
tested locally; the Rust ready-batch fixture also ran on the Pi. Real browser
owner/session/PKCE authorization and one controlled Google Drive QA upload with
independent downloaded-content SHA-256 verification now pass on the Pi. The
original stays retained. This does not enable recurring background uploads. See
[the worker guide](STAGED_WORKER.md) and [Rust evidence](RUST_EVALUATION.md).

Complete the Google Drive folder/test/enable wizard and persistent worker, then
accept token refresh, large/changed files, outages and interrupted real-account
uploads. The controlled proof used a dedicated OAuth client with `drive.file`;
External/Testing retains its seven-day refresh-token limit. Rclone's documented
headless setup remains an alternative to the tested browser path. Keep config
and tokens outside Git, mode 0600, owned by the service user. Confirm each backend
and destination before enabling it. Koofr app-password and Dropbox real-account
authorization/backup trials remain pending.

Dev9 adds the private CLI preparation boundary: a bounded inherited channel
passes validated authorization into a disabled backup-owned configuration.
Synthetic separate-identity transfer and kernel access denials passed on the Pi.
The live dashboard remains dev8; its integration with a production backup identity,
current-state reporting and explicit Test/Enable controls still need completion.
See [the handoff contract](GOOGLE_HANDOFF.md).

Dev10 adds durable owner requests, private source/configuration bindings, a
gated root coordinator, and historical dashboard receipts. Its native synthetic
Pi trial passed separate identities, interrupted-receipt recovery and retained
configuration checks. A dedicated locked backup identity is prepared. Accept
the systemd event/sandbox path and live dashboard integration next; preparation
still performs no cloud test or automatic upload.

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

The `0.1.0.dev3` preview now implements Overview, Files, Backups, Wi-Fi/access and
History views with owner/viewer accounts, protected sessions, CSRF and private
web-upload staging. It has passed local and real Pi QA-file checks; see
[DASHBOARD.md](DASHBOARD.md). The phone layout and PWA foundation share this app.

Dev5 adds tested browser password changes and private CLI account recovery.
Continue with operational setup controls and invitations,
a stable hostname for the tested browser-only HTTPS preview (no Tailscale requirement), physical Android
installation, Samba file browsing and cloud-worker integration. Include
CSRF protection, safe LAN access, redacted diagnostics and accessible mobile UI.
Display live ownership, observed connectivity, per-file version, upload result,
verification method/time, retry time and failure code. Never show verified merely
because upload completed or turn a past result into a claim about current contents.
The web process must remain unprivileged and expose only validated narrow actions.

The expanded [product plan](PRODUCT_PLAN.md) records research, supported network
types from the user's screenshots, replication requirements and acceptance order.

## Deployment acceptance

Keep automated, local-rclone, test-account and actual Pi evidence separate.
[Hardware matrix and remaining work](PHASE1.md#hardware-testing-still-required).
No initial release will automatically delete USB originals or staging data.
Merging to main and publishing a release require owner approval.


## Appliance image and device updates — planned

[Image/update design](APPLIANCE_IMAGE.md) covers an official Trixie ARM64 base,
unique first-boot identity, private onboarding, signed versioned application
updates and rollback. No appliance image, signing service or update release is
published yet. Build/test on a spare card and second Pi before distribution;
do not replace the current working card to trial an image recipe.
