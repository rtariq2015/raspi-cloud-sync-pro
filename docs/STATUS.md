# Project progress

**Updated: 13 November 2026. Development build; no release published.**

This page records evidence, not a percentage estimate. Follow the
[development branch](https://github.com/rtariq2015/raspi-cloud-sync-pro/tree/development)
for the current source and [Actions](https://github.com/rtariq2015/raspi-cloud-sync-pro/actions)
for remote automated checks.

**Latest access update:** a Pi reboot removed the transient preview services and
the old Quick Tunnel returned error 1033. The protected dev10 dashboard has been
restored behind a fixed free ngrok address. Local/public login HTTP checks and
the actual Chrome login form pass; stored credentials and uploads were preserved.
The dashboard and connector now run as persistent, enabled system services;
unit syntax, runtime health and disk/manager enablement checks pass. A physical
reboot has not been performed or accepted.
The changed Google callback and Pi client origin are now reconciled, preserving
credentials and retaining private backups/rollback. Setup requests remain
disabled pending broker restoration and fresh owner preparation. Previous
preparation remains historical. [Origin migration](acceptance/pi-google-origin-migration-2026-11-13.json),
[recovery evidence](acceptance/pi-fixed-https-recovery-2026-11-13.json)
and [remote access guide](REMOTE_ACCESS.md).

## Working on the Pi

- Home Wi-Fi and authenticated SSH access. USB Ethernet carrier is present;
  stable USB addressing remains an outstanding acceptance item.
- Dashboard login with owner/viewer accounts, phone-friendly screens, QA-file
  upload, bounded text preview and downloads with byte/SHA-256 checks.
- Restricted dev10 dashboard service candidate with NoNewPrivileges and home/system
  filesystem restrictions. The upgraded service passed process/HTTP health checks
  and public HTTPS access checks; dev8 is retained for rollback. Login and authenticated file access also passed through
  a temporary public HTTPS tunnel; no router ports or VPN app were needed.
- Rust watcher fixture trial: 398,048-byte ARM64 binary, 1,796 KiB parent idle RSS,
  incomplete batches ignored and completed unchanged work not retriggered.
- Rust paused-worker comparison on the Pi: three child launches reduced to one
  in separate twelve-second fixture windows; new files/configuration still wake
  at the next poll. This measures launches, not electrical power consumption.
- End-to-end Rust → Python → ARM64 rclone trial: three files, two temporary local
  destinations, six read-back verified jobs, blocked-policy queueing, deduplication
  and retained data after controlled process interruption. No cloud account used.
- First controlled real Google Drive backup on dev8: browser owner/session/PKCE
  authorization passed, then one known QA file uploaded to a unique app-owned
  folder and passed independent downloaded-content SHA-256 verification. The
  original and private QA configuration are retained; background uploads remain
  disabled. [Sanitized evidence](acceptance/pi-google-drive-2026-11-13.json).
- Dev9 private Google preparation passed a synthetic CLI handoff between two
  restricted QA identities on the Pi. Actual kernel permissions prevented each
  from reading the other's private state; unsafe permissions and overwrite
  attempts were rejected. No real credentials or live service settings changed.
  [Handoff evidence](acceptance/pi-google-handoff-dev9-2026-11-13.json).
- Real encrypted Samba protocol fixture in an isolated Linux container: active
  writers defer capture; snapshot handles block new writes/deletes. This is
  separate from actual Pi/Windows/USB acceptance.
- The same locking contract now passes with actual Samba 4.22.11 on the Pi,
  using an isolated encrypted loopback share. Active writes defer capture,
  snapshots block writes/deletes, interrupted reads retain incomplete data,
  and old versions remain intact. The temporary server stopped after the test;
  Windows/USB transfers remain unaccepted.
- A credential-free native Wi-Fi restriction trial passes on the Pi: actual
  kernel ingress/egress allowlists, denied loopback TCP/UDP with successful
  surrounding controls, and direct Wi-Fi DNS/HTTPS. The four temporary services
  stopped and working access was preserved. This is partial platform proof;
  USB-path exclusion and each credentialed executor still require acceptance.
  [Native evidence](acceptance/pi-wifi-rni-partial-2026-11-13.json).

![Actual dashboard on the Pi](images/dashboard-desktop.png)

## Implemented and tested locally

- **571 Python tests and 10 Rust tests pass locally.** Python includes real rclone
  against temporary local storage; this is not a real cloud-account test.
- Completed-batch backup worker with per-destination jobs, retries, network policy,
  restart reconciliation and source retention. Disabled by default.
- Rust staged-file scheduler, upload wrapper, ARM64 cross-build and inactive unit.
- Non-secret enterprise Wi-Fi plans including PEAP/MSCHAPv2, anonymous identity,
  CA/server validation and EAP-TLS references. Planning does not activate Wi-Fi.
- USB/Samba ownership, staging, controller and recovery simulations from Phase 2.
- Local-only cloud backend checks: missing/mismatched/alias configurations are
  distinguished without claiming account access or making a cloud request.
- Dev7 Google Drive/Dropbox Connect foundation: owner-only consent initiation,
  PKCE, one-use session-bound returns and private token storage. Missing registered
  clients show setup-required; authorization never enables uploads. Eighteen new
  tests use fake OAuth responses, not real accounts. These controls are now in the live dev10 preview.
- Dev8 Dropbox rclone adapter with synchronous commits, smaller upload chunks and
  independent read-back verification. Local-alias integration passes; real
  Dropbox authorization/backup and the guided runtime integration remain pending.
- Dev9 source adds a private Google authorization handoff into a disabled
  backup-owned configuration. Dedicated inherited channels, credential/client/scope
  bindings, private permissions, atomic no-replace publication and retained
  incomplete transactions have focused tests. No cloud request or upload
  activation occurs during preparation. [Operator guide](GOOGLE_HANDOFF.md).
- Dev10 adds owner-only preparation requests, a separately gated local broker,
  and historical preparation status in the dashboard. Source and configuration
  bindings, owner revocation, expiration, private channels, retained interrupted
  work, and bounded browser status retries have automated tests. The protected
  dev10 dashboard and request watcher ran on the Pi. A real owner HTTPS
  request prepared one private backup configuration successfully. Neither
  preparation nor its status enables automatic backups.
  [Live acceptance](acceptance/pi-google-setup-live-dev10-2026-11-13.json).
  The watcher disappeared with the reboot; current setup requests remain disabled
  pending broker restoration and fresh owner preparation, as recorded above.
- The dev10 synthetic Pi trial now passes owner HTTP-handler requests, the actual
  root broker, separate ordinary service identities, historical status and
  recovery from a prepared destination whose receipt was not recorded. Existing
  configuration inodes/bytes and source files were retained; actual kernel reads
  across private identity boundaries were denied. A separate backed-up setup step
  created the locked backup identity with its own group, preserving every old
  account entry/password. The trial changed no live service or network settings.
  [Dev10 evidence](acceptance/pi-google-setup-dev10-2026-11-13.json).

- Actual Pi systemd event/recovery tests now pass with synthetic credentials:
  a blocked owner lock recovers on retry, persistent failures stop after three
  starts, and deliberate recovery preserves earlier configurations. The separate
  slow-failure trial observed three 25-second executions over 78.5 seconds and
  refused a fourth start. Kernel sandbox/identity checks passed. Temporary QA
  units are stopped. These synthetic checks have separate evidence from the
  accepted live preparation. [Event/recovery evidence](acceptance/pi-google-setup-systemd-2026-11-13.json),
  [slow-failure evidence](acceptance/pi-google-setup-slow-retries-2026-11-13.json).

- The next operational-config foundation passes 24 local filesystem/history
  tests. It preserves preparation proof, separates future mutable token writes,
  retains failures and prevents a new destination generation from reusing old
  verified jobs. It is disabled, untested against a provider and not connected
  to the live worker. Test/Enable and automatic file backup remain unfinished.

- New owner-only Test/Enable/Disable dashboard controls and the private Google
  Test worker pass local tests. The worker creates a separate generation, checks
  the app-owned folder, forces operational-token refresh and verifies downloaded
  fixture content. It refuses credentials until a root-authorized per-run gate;
  fake cloud transport tests do not establish live refresh or Wi-Fi confinement.
  These changes are not deployed and both request flags remain off on the Pi.
- Dev11 now adds a default-disabled root Test coordinator, ordinary-identity
  bootstrap and held kernel-policy inspector in source. They check fresh owner,
  Wi-Fi, worker and canary evidence, keep the completed worker alive for final
  verification and report failures conservatively. Local fault tests pass;
  source-worker/DNS-mount and real Google acceptance remain pending. The native
  four-channel handoff and post-exit cgroup lifecycle were tested separately.
  [Native evidence](acceptance/pi-openfile-handoff-2026-11-13.json),
  [source contracts](GOOGLE_CONTROLS.md).

- Dev5/dev6 password change/recovery: actual Pi loopback HTTP, private interactive
  recovery prompt, own-session revocation, preserved viewer session and private
  hash backups. Only disposable test accounts changed.
- Five-minute Pi retention trial: ten more files, twenty more verified local
  copies, 20/20 Wi-Fi gateway probes and no reported power throttling.

The root-terminal authentication was restored and the protected-service upgrade
has completed. No account passwords, USB/Wi-Fi settings or boot enablement changed.

## Being worked on

1. Accept persistent startup through a controlled reboot and an off-LAN phone
   connection using the installed fixed free HTTPS address.
2. Extend the accepted Pi loopback Samba contract to actual Windows/USB host
   transfers after stable USB addressing is established.
3. Complete the guided Google Drive folder/test/enable workflow and persistent
   worker after the first controlled real-account proof. Extend account trials
   to refresh, outages, large/changed files and the other providers.
4. Resolve the observed existing USB profile auto-switcher/DHCP loop without
   interrupting Wi-Fi management, then accept actual Pi Samba and host transfers.

## Still requires acceptance

- Google Drive refresh/outage/large-file acceptance and recurring worker setup;
  Koofr and Dropbox real-account authorization and backup tests.
- A phone on an outside network and physical Android PWA installation.
- Enterprise Wi-Fi on the intended network with its approved CA/server identity.
- Captive-portal sign-in for the Pi's own session; no universal automatic acceptance.
- USB power/endurance/recovery tests and installation on another physical Pi.
- A ready-to-flash appliance image and signed in-device updates with rollback.

Bluetooth setup/status is an optional future addition. It is not enabled by this
work. The chosen architecture stays lightweight: Rust watcher, Python control/UI,
and rclone/Samba for their protocols.

The Google trial used approved Wi-Fi before and after, with rclone bound to the
Wi-Fi IPv4 address; it does not establish an egress-firewall guarantee. Firmware
reported no throttling afterward. The OAuth application stays External/Testing,
where Drive refresh tokens expire after seven days. This one-file proof does not
complete automatic consumer setup or indefinite unattended backup.

## Guides

- [What the product will do](PRODUCT_PLAN.md)
- [Dashboard and Android web app](DASHBOARD.md)
- [Backup worker](STAGED_WORKER.md)
- [Private cloud setup and acceptance](CLOUD_SETUP.md)
- [Rust decision and measurements](RUST_EVALUATION.md)
- [Another Pi](REPLICATION.md)
- [Custom appliance image and updates](APPLIANCE_IMAGE.md)
- [Detailed test record](VALIDATION.md)

Original files remain retained. The current SD card has not been reflashed.
Main-branch merge and release publication still require owner approval.

The current USB carrier stays present, but a follow-up observed its existing
auto-switcher cycling between DHCP attempts and local addressing. This trial
changed no USB profile; stable USB addressing is an outstanding acceptance item.

Dev6 also refuses CLI password entry when no secure terminal is available.
The private PTY path and no-terminal refusal both passed on the Pi. GitHub
checks now include ARM64 cross-builds and an isolated real Samba protocol job.
