# Core Cloud Stick recovery

Updated 14 November 2026. Development only; no merge or release.

The first actual Windows USB file has reached Google Drive and passed downloaded
SHA-256 verification. This is a real hardware/cloud milestone, not a claim that
the appliance is finished.

| Requirement | Result | Evidence and limit |
| --- | --- | --- |
| Genuine writable USB drive | PASS | Pi configfs mass-storage gadget configured; user copied a file into CLOUDSTICK in Windows |
| Larger file copy | PASS | Captured file is 10,490,314 bytes; Windows source hash was not independently supplied |
| Separate small-file Windows test | NOT TESTED | One physical file has been tested so far |
| Safe local capture | PASS | User safe eject plus fresh empty LUN; ordinary unbind, read-only loop, nonrepairing exFAT check, clear dirty/media-failure flags and read-only backup-UID staging |
| Unattended eject detection | NOT TESTED | Initial timed observer expired before the user ejected; explicit operator-confirmed recovery was used and labelled separately |
| Automatic Google upload | PASS | Already-running worker discovered the completed batch, uploaded and verified it on attempt 1 |
| Remote integrity | PASS | Actual job events: queued → uploading → uploaded → verifying → verified; provider downloads remote contents and compares SHA-256 |
| Original retention | PASS | Image retained; completed local staged copy retained; no cleanup/deletion enabled |
| Physical Wi-Fi outage recovery | NOT TESTED | Working Wi-Fi/SSH was preserved; automated offline/retry tests do not establish physical outage acceptance |
| Physical reboot recovery | NOT TESTED | No Pi reboot performed |
| Worker restart and upload deduplication | PASS | Worker PID changed; verified job, attempt count and event counts stayed unchanged over two more ticks |
| Safe USB reattachment | PASS | No image loop or mount remained before rebind; host configured the gadget again; second Explorer appearance not separately confirmed |
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

Next: permanent eject-to-capture operation, safe repeated reattachment, duplicate
capture prevention, queued-upload restart/outage tests, then controlled reboot
acceptance. A no-eject mass-storage workflow remains unproven. Dashboards and
other providers stay deferred until the core workflow is accepted.

The source-disabled `mass_storage_appliance` prototype now reuses the continuous
controller session and transfers completed Root-private snapshots into the
backup identity's private queue. It adopts only an already loaded, configured
profile and still requires a fresh loaded observation before accepting eject.
Its public `APPROVED_TEST_BUILD` flag and the original global hardware gate remain
false. A separately recorded protected test build is required for native testing.
It does not provision the USB profile after reboot; interrupted handoffs retain
data for manual recovery. The example service is therefore static and disabled.
