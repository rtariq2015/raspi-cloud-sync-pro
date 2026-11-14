# Core Cloud Stick recovery

Updated 14 November 2026. Development only; no merge or release.

**Automatic USB capture and Google backup now pass on the Zorin laptop.** Three
synthetic files (66 bytes, 33 bytes with a Unicode name, and 64 MiB) were written
and read back through the real gadget. Normal media eject triggered the running
controller, without an operator capture command. It captured matching bytes,
reattached the drive, and handed the batch to the separate worker. All three
Google uploads passed downloaded SHA-256 verification on their first attempt.
The earlier 10,490,314-byte file remained verified without another upload.

**Host attribution correction:** the earlier user-confirmed copy/eject was
described as Windows based on conversation context. That host OS was not
independently recorded. The new automated host test is explicitly Zorin Linux;
Windows-specific physical acceptance remains outstanding. The earlier file's
capture, Google upload and verification evidence remains valid.

| Requirement | Result | Evidence and limit |
| --- | --- | --- |
| Genuine writable USB drive | PASS on Linux; Windows NOT VERIFIED | Actual 1 GiB removable USB gadget enumerated and mounted on Zorin; Windows still requires separately recorded host acceptance |
| Small/large/nested/Unicode copies | PASS on Linux | Three synthetic files, including 64 MiB; host read-back and captured SHA-256 match |
| Separate Windows copy/hash test | NOT TESTED | Earlier host OS attribution was contextual rather than independently recorded |
| Safe local capture | PASS | User safe eject plus fresh empty LUN; ordinary unbind, read-only loop, nonrepairing exFAT check, clear dirty/media-failure flags and read-only backup-UID staging |
| Unattended eject detection | PASS on Linux | Normal unmount and UDisks Drive.Eject; same continuous controller process observed release, staged, reattached and handed off |
| Automatic Google upload | PASS | Already-running worker discovered the completed batch, uploaded and verified it on attempt 1 |
| Remote integrity | PASS | Actual job events: queued → uploading → uploaded → verifying → verified; provider downloads remote contents and compares SHA-256 |
| Original retention | PASS | Image retained; completed local staged copy retained; no cleanup/deletion enabled |
| Isolated worker offline/restart/recovery | PASS | Real USB file queued with zero attempts through two fresh network-isolated workers; restored normal worker uploaded and downloaded-hash verified it on attempt 1 |
| Physical Wi-Fi outage recovery | NOT TESTED | Working Wi-Fi/SSH was preserved; automated offline/retry tests do not establish physical outage acceptance |
| Physical reboot recovery | NOT TESTED | No Pi reboot performed |
| Worker restart and upload deduplication | PASS | Worker PID changed; verified job, attempt count and event counts stayed unchanged over two more ticks |
| Safe USB reattachment | PASS on Linux | Gadget configured again and automatically remounted by Zorin; no Pi image loop or capture mount remained |
| Existing Wi-Fi/SSH | PASS | Separate authenticated SSH connections remain on built-in wlan0 |
| USB Ethernet recovery | PASS | Normally unloaded/reloaded g_ether, restored ICS state, observed configured Ethernet gadget; Windows IP/SSH was not separately tested |

The dedicated 1 GiB MBR/exFAT image is a regular file on the existing Pi SD
filesystem. The SD card was not reformatted, repartitioned or reflashed, and the
live Linux root filesystem was never exported. USB mode changes used the existing
peripheral configuration; unrelated CM4/CM5/Pi 5 settings were untouched.

Image preparation stopped conservatively before its completion receipt. A later
read-only whole-image/partition check found a clean filesystem and no owned loop
remaining. The kernel demonstrably acknowledges lazy loop detach before its
association disappears. The backend and preparation helper now wait for actual
disappearance; they never force detach. The partial preparation record is retained.

The manual handoff requires separate plan-bound Root approval, the user's actual
safe-eject confirmation, retained loaded/empty observations, the original image
identity and fresh strict device checks. It does not forge the expired observer's
in-process evidence. Source: [USB operator tools](../tools/acceptance/mass_storage/README.md).

The Google worker reuses the existing protected dev17 implementation. It runs as
the restricted backup identity, reads only completed private staging and uses a
separate private writable copy of the existing prepared Google configuration.
The original configuration is unchanged. Its destination is the previously
selected `RaspiCloudSyncPro` folder. This is a local operator-authorized path;
the web Test/Enable coordinator is separate. Cached Wi-Fi policy is not a kernel
egress guarantee. Service/config examples are disabled references, not an installer.
The actual backup worker is running and boot-enabled after its native backup and
process-restart checks. Its prior unit and rollback instructions are retained;
physical reboot acceptance still remains.

Next: duplicate capture prevention, queued-upload restart/outage tests, persistent
USB startup, Windows-specific acceptance, then controlled reboot
acceptance. A no-eject mass-storage workflow remains unproven. Dashboards and
other providers stay deferred until the core workflow is accepted.

The source-disabled `mass_storage_appliance` prototype now reuses the continuous
controller session and transfers completed Root-private snapshots into the
backup identity's private queue. It adopts only an already loaded, configured
profile and still requires a fresh loaded observation before accepting eject.
Its public `APPROVED_TEST_BUILD` flag and the original global hardware gate remain
false. The separately recorded protected dev19 test build is running and passed
the automatic Linux-host handoff. Its root service required `CAP_SYS_PTRACE` to
read PID 1's mount namespace identity; without it startup refused before mutations.
The corrected service was probed on the Pi before starting the controller.
It does not provision the USB profile after reboot; interrupted handoffs retain
data for manual recovery. The example service is therefore static and disabled.

Full sanitized evidence: [automatic USB acceptance](acceptance/pi-mass-storage-auto-linux-2026-11-14.json).
The [offline worker trial](acceptance/pi-core-offline-worker-2026-11-14.json)
isolated only the backup worker's network namespace, preserving the Pi radio and
SSH. Its real captured 72-byte file stayed queued through two fresh processes,
then reached Google and passed verification when the normal worker resumed.
Five jobs are now verified; the four older jobs were not uploaded again. This
does not establish physical radio-outage or Pi-reboot acceptance.

The first dev20 duplicate-capture trial **failed closed**, after publishing a
complete local copy and before unmount/reattachment. A fresh exclusive-read-only
diagnostic reproduced three inode-number-only differences between passes;
device, size, timestamps and copied hashes matched. Linux exFAT assigns an
`iunique()` number when rebuilding an evicted inode, so inode numbers cannot
identify a file across separate opens. See the
[upstream exFAT implementation](https://github.com/torvalds/linux/blob/v6.18/fs/exfat/inode.c#L629-L669).
Dev21 removes only that cross-pass comparison; per-open identity, content hashes,
path sets, device and timestamps remain checked. Its two regression tests pass.
The failed journal and valid copied data were retained and reviewed; no source
data was deleted or filesystem repair forced. Native dev21 acceptance remains
required before marking duplicate suppression as passed.
Plain-language updates and owner questions: [GitHub progress thread](https://github.com/rtariq2015/raspi-cloud-sync-pro/issues/2).
