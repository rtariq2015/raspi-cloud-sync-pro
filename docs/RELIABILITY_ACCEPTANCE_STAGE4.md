# Stage 4 — Final Reliability Acceptance

**PASS for the approved clean-reboot acceptance — 15 November 2026.**
The existing 8 GiB CLOUDSTICK returned automatically after one normal Pi reboot.
Windows file contents, backup records and the protected configuration were
preserved. This completes this bounded acceptance test; it is not a production
release or a guarantee against every hardware or network failure.

## Baseline and evidence

- Repository baseline: `22807f7be88d9b6a40bd14c72e728954eee8ba7c`, `development`.
- Installed controller source: `8e3afad74acbb823d5180aeed3c5ab3f6ec18b2e`;
  all 48 protected module hashes matched the accepted installation.
- Existing worker: the accepted 43-module package with the backup-failure
  reporting fix; no source, launcher, unit or worker configuration changes.
- Both installed packages declare `0.1.0.dev22`; exact source hashes, rather
  than that shared version label, establish the installed baseline.
- Raspberry Pi Zero 2 W, 64-bit, kernel `6.18.60+rpt-rpi-v8` before and after.
- Fresh final preflight: 20:26:46–47 UTC (15:26 EST).
- Protected postboot verification: 20:39:47–48 UTC (15:39 EST).

Private read-only receipts preserve source/configuration fingerprints, exact
image identities and per-row history hashes. Public reporting deliberately
omits credentials, account/folder IDs, network addresses and private filenames.
An independent local evidence review checked the before/after observations.

## Approved physical procedure performed

1. Rechecked healthy management access and power, idle services, unchanged files
   and history, sufficient space and no conflicting image mounts/loops/capture.
2. Stopped only the idle backup worker and USB controller, leaving both enabled.
   Stopping the controller prevented its normal automatic drive reattachment.
3. Performed genuine Windows Safe Eject after verifying the volume identity,
   exclusive lock and cache flush. No force-eject or power removal was used.
4. Independently verified an empty gadget LUN and no image-owned mounts/loops;
   then sent exactly one normal Pi-only reboot request. Windows stayed running
   and the USB cable remained connected.
5. Confirmed a new Pi boot ID through pinned SSH and checked Windows and protected
   Pi state. No manual postboot service start or reboot retry was needed.

The first 50-second SSH observation window did not reach the Pi. A subsequent
targeted check succeeded at approximately 101 seconds of Pi uptime. This proves
recovery in this test, not a measured maximum startup time.

## Actual results

| Check | Result |
| --- | --- |
| Automatic startup | Controller and backup worker both active/running/enabled, successful service status; no manual start |
| Wi-Fi and SSH | Returned through Wi-Fi with the existing pinned SSH identity; Wi-Fi default route present |
| Wi-Fi recovery timer | Automatically active/waiting/enabled; latest guard observation healthy with zero failures |
| Windows USB | Healthy removable exFAT CLOUDSTICK returned on D:; raw disk 8,589,934,592 bytes, filesystem 8,586,788,864 bytes |
| Windows file integrity | All 19 files, totaling 216,012,177 bytes, matched preboot SHA-256 hashes; zero missing, changed or extra files; read handles closed |
| Image and ownership | Same exact 8 GiB image identity and approval; expected host/exported state; no image-owned loop/mount or active capture/upload at inspection |
| Enrollment and rollback archive | Export marker and released maintenance state retained; intent/image bindings and original 1 GiB archive identity/mtime verified |
| Backup history | Same 20 verified jobs and 100 events; every existing job/event row hash unchanged; SQLite quick check `ok`; zero pending/failed jobs |
| Google configuration | Same account/destination bindings, static configuration and refresh grant; no reauthorization, new upload or deletion initiated |
| Installed code and units | Controller/worker module hashes, launchers, unit files and configuration bindings unchanged |
| Storage and power | Root writable, 42,132,090,880 available bytes (about 39.24 GiB); power status `0x0`; no recent critical storage errors detected |
| OS zram | Fresh authoritative kernel proof accepted the unrelated active backing loop; swap was unchanged |

All 24 protected before/after comparison gates passed. No repair, rollback,
architecture change, formatting, credential change or network change was needed.
The retained original archive was checked by identity, metadata and modification
time; its full contents were not rehashed in Stage 4. Staged-receipt counts were
preserved; the complete per-row digest comparison applies to jobs and events.

## Reused evidence and limits

No application source changed, so the already accepted source regression was
not rerun locally: 1,002 main-suite passes with three optional rclone skips,
36 USB operator passes and 35 storage simulation passes (1,073 passes total).
Those are earlier source-test results, not new reboot tests.

The [Stage 3 physical acceptance](IMAGE_ENROLLMENT.md#physical-acceptance)
already verified one synthetic Google Production upload by an independent
download/SHA-256 comparison. Stage 4 checked preservation of its configuration
and history; it did not repeat a cloud transfer or remote-object download.

The [existing Wi-Fi recovery evidence](acceptance/pi-wifi-recovery-guard-dev22-2026-11-14.json)
includes an approved disconnect, offline queuing, reconnection and 443 seconds
of healthy follow-up. Stage 4 adds automatic timer startup after this reboot;
it does not repeat that disruption or establish the original fault's cause.

Abrupt power loss, a full drive, prolonged workplace load, additional Windows
versions and clinical scanner compatibility remain unaccepted hardware cases.
The dashboard/tunnel and phone workflow were outside this core acceptance.
**Safe Eject remains mandatory after each copied batch; no-eject capture is not
implemented.** Keep the power/data cable connected during capture and backup.

The original image, retained staging, backup history and protected rollback
records remain preserved. After Windows may have written to the new image,
never blindly restore the old image or rewind history: preserve both images and
reconcile newer files before a separately approved recovery.

No blocker was found for this clean-reboot acceptance. Stop for owner review;
continuous no-eject work requires a separately approved task.
