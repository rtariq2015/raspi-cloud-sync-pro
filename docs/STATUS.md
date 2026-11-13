# Project progress

**Updated: 13 November 2026. Development build; no release published.**

This page records evidence, not a percentage estimate. Follow the
[development branch](https://github.com/rtariq2015/raspi-cloud-sync-pro/tree/development)
for the current source and [Actions](https://github.com/rtariq2015/raspi-cloud-sync-pro/actions)
for remote automated checks.

## Working on the Pi

- USB Ethernet management, home Wi-Fi and authenticated SSH access.
- Dashboard login with owner/viewer accounts, phone-friendly screens, QA-file
  upload, bounded text preview and downloads with byte/SHA-256 checks.
- Restricted dev6 dashboard service candidate with NoNewPrivileges and home/system
  filesystem restrictions. The upgraded service passed process/HTTP health checks
  and public HTTPS access checks; the dev3 version is retained for rollback. Login and authenticated file access also passed through
  a temporary public HTTPS tunnel; no router ports or VPN app were needed.
- Rust watcher fixture trial: 398,048-byte ARM64 binary, 1,796 KiB parent idle RSS,
  incomplete batches ignored and completed unchanged work not retriggered.
- End-to-end Rust → Python → ARM64 rclone trial: three files, two temporary local
  destinations, six read-back verified jobs, blocked-policy queueing, deduplication
  and retained data after controlled process interruption. No cloud account used.
- Real encrypted Samba protocol fixture in an isolated Linux container: active
  writers defer capture; snapshot handles block new writes/deletes. This is
  separate from actual Pi/Windows/USB acceptance.

![Actual dashboard on the Pi](images/dashboard-desktop.png)

## Implemented and tested locally

- **177 Python tests and 6 Rust tests pass locally.** Python includes real rclone
  against temporary local storage; this is not a real cloud-account test.
- Completed-batch backup worker with per-destination jobs, retries, network policy,
  restart reconciliation and source retention. Disabled by default.
- Rust staged-file scheduler, upload wrapper, ARM64 cross-build and inactive unit.
- Non-secret enterprise Wi-Fi plans including PEAP/MSCHAPv2, anonymous identity,
  CA/server validation and EAP-TLS references. Planning does not activate Wi-Fi.
- USB/Samba ownership, staging, controller and recovery simulations from Phase 2.
- Local-only cloud backend checks: missing/mismatched/alias configurations are
  distinguished without claiming account access or making a cloud request.

- Dev5/dev6 password change/recovery: actual Pi loopback HTTP, private interactive
  recovery prompt, own-session revocation, preserved viewer session and private
  hash backups. Only disposable test accounts changed.
- Five-minute Pi retention trial: ten more files, twenty more verified local
  copies, 20/20 Wi-Fi gateway probes and no reported power throttling.

The root-terminal authentication was restored and the protected-service upgrade
has completed. No account passwords, USB/Wi-Fi settings or boot enablement changed.

## Being worked on

1. Replace the tested temporary HTTPS preview with a stable hostname/access policy,
   then accept an off-LAN phone connection.
2. Accept the actual Pi Samba server and file-locking contract on disposable
   data, using private extracted packages without global service activation.
3. Finish private cloud account authorization and actual cloud acceptance;
   account password change/recovery has now passed an isolated Pi HTTP trial.
4. Resolve the observed existing USB profile auto-switcher/DHCP loop without
   interrupting Wi-Fi management, then accept actual Pi Samba and host transfers.

## Still requires acceptance

- Google Drive authorization and Koofr app-password setup with real test accounts.
- A phone on an outside network and physical Android PWA installation.
- Enterprise Wi-Fi on the intended network with its approved CA/server identity.
- Captive-portal sign-in for the Pi's own session; no universal automatic acceptance.
- USB power/endurance/recovery tests and installation on another physical Pi.
- A ready-to-flash appliance image and signed in-device updates with rollback.

Bluetooth setup/status is an optional future addition. It is not enabled by this
work. The chosen architecture stays lightweight: Rust watcher, Python control/UI,
and rclone/Samba for their protocols.

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
