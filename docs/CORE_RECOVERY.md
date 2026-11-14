# Core Cloud Stick recovery

Updated 14 November 2026. Development only; no main merge or release.

**USB storage → safe Eject → local capture → automatic verified Google backup
now works on the real Pi with the Zorin laptop.** Actual Wi-Fi disconnection and
Pi reboot recovery pass. Twelve file versions are verified; local copies and
older versions remain. Windows-specific acceptance is still outstanding.

| Requirement | Result and evidence |
| --- | --- |
| Genuine writable USB storage | PASS on Linux; Windows NOT TESTED independently |
| Small, 64 MiB, nested and Unicode copies | PASS; host read-back and captured SHA-256 agree |
| Conservative safe capture | PASS; observed loaded→empty LUN, ordinary unbind, RO loop, nonrepairing exFAT check, clean flags and RO mount |
| Automatic capture and reattachment | PASS; uninterrupted controller hands completed batches to the restricted worker |
| Changed/new files only | PASS; unchanged eject adds no batch/job/event; a changed file and a new file made a two-file batch |
| Automatic Google upload | PASS; 12 verified versions, one cloud attempt each |
| Remote integrity | PASS; actual uploaded contents downloaded and checked against SHA-256 |
| Original/version retention | PASS; image, staged copies and older verified versions retained; no automatic deletion |
| Actual Wi-Fi outage/reconnection | PASS; wlan0 disconnected, USB/services stayed active, new file queued with zero attempts, reconnect and Google verification passed |
| Actual Pi restart | PASS; new boot ID, both services and USB returned; a queued 99-byte file uploaded and verified after boot |
| Wi-Fi/SSH preserved | PASS after controlled reconnect/reboot; same approved profile; no laptop network changes |
| Wi-Fi path/stability | PASS; bound DNS packet, gateway ICMP and validated TLS; more than 12 minutes with no unexpected NM disconnect |
| Windows physical acceptance | NOT TESTED independently; [prepared steps](WINDOWS_ACCEPTANCE.md) |
| Permanent Google authorization | NOT READY; existing External/Testing setup has a seven-day refresh-grant lifetime |

The dedicated 1 GiB MBR/exFAT image is a regular file on the existing Pi SD
filesystem. These tests did not reflash, reformat or repartition the card. The
live Linux root filesystem is not exported. Built-in Wi-Fi carries cloud traffic;
USB Ethernet is an optional alternative, not this product's drive interface.
A clean host Eject is required for each completed batch; keep the cable connected
for power. A quiet period, unchanged size or concurrent RO mount is not release
proof. Fully automatic no-eject mass storage remains unproven.

The approved private dev22 build is installed with both core services enabled.
Public hardware gates remain false. The root controller has no cloud/network
API; only completed private batches reach the unprivileged Google worker. The
original prepared Google configuration is preserved; a separate private writable
copy supports token refresh. Cached uplink policy is not a kernel egress guarantee.
The actual runtime has only Wi-Fi as its default Internet route.

Startup preparation backed up and commented the sole legacy `g_ether` loader,
added `libcomposite`, and disabled/masked the previously inactive Pi ICS watcher.
Firmware, command line, initramfs, Wi-Fi policy, SSH and accounts were preserved.
The active initramfs contained no legacy loader; no rebuild was needed. Actual
profile reconstruction and a clean Pi reboot then passed. Ambiguous journals,
partial profiles, pending Root captures, dirty filesystems, power uncertainty or
conflicting drivers still refuse startup and retain data for review.
See [startup and rollback](MSC_BOOT.md).

Earlier acceptance needs two qualifications. The first user-confirmed copy/eject
was attributed to Windows from conversation context; its host OS was not
independently recorded. Its safe capture and Google verification remain valid.
A timed observer expired, so that first handoff was explicitly operator-confirmed
rather than claimed as automatic. Later Linux-host automation is recorded separately.

Dev20's new cross-pass inode comparison stopped safely on actual exFAT. A fresh
exclusive-RO diagnostic reproduced inode-only changes in three files while
contents and other metadata matched. The complete copies and failed journal were
retained and reviewed; no forced repair/unmount or source deletion occurred.
Dev21 dropped only cross-pass inode equality, retaining per-open identity, path,
size, timestamps and payload-hash checks. Its native unchanged/delta/retention
tests passed. [Upstream exFAT inode assignment](https://github.com/torvalds/linux/blob/v6.18/fs/exfat/inode.c#L629-L669).

The first short radio-outage window restored Wi-Fi and kept USB working but ended
before capture reached the queue; it is not counted as an offline-queue pass.
The longer timed test directly observed a disconnected radio and a retained,
zero-attempt queued file, followed by automatic reconnection and verification.
A separate HTTPS connectivity probe received HTTP 403 with a valid TLS session;
that proves transport, not website-content access. Actual Google transfers passed.

Before permanent workplace use, resolve the Google development/testing limit
with the owner. External/Testing Drive refresh grants expire after seven days;
app publication/verification was not performed automatically. See [cloud setup](CLOUD_SETUP.md)
and [Google's expiration rules](https://developers.google.com/identity/protocols/oauth2).
Windows host eject, remount, policy and power/suspend behavior still require
physical acceptance. Dashboards and additional providers remain deferred.

Sanitized evidence:

- [Initial file](acceptance/pi-mass-storage-google-first-file-2026-11-14.json)
- [Automatic Linux USB](acceptance/pi-mass-storage-auto-linux-2026-11-14.json)
- [Isolated offline worker](acceptance/pi-core-offline-worker-2026-11-14.json)
- [Dev21 changed versions](acceptance/pi-mass-storage-delta-dev21-2026-11-14.json)
- [Real dev22 reboot](acceptance/pi-mass-storage-reboot-dev22-2026-11-14.json)
- [Actual dev22 radio outage](acceptance/pi-actual-wifi-outage-dev22-2026-11-14.json)

[Plain-language updates and owner questions](https://github.com/rtariq2015/raspi-cloud-sync-pro/issues/2).
