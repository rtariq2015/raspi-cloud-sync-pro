# Core Cloud Stick acceptance

Updated 14 November 2026. Dev22 development trial; no main merge or release.

**The real Windows USB → safe Eject → local capture → automatic verified Google
Drive workflow passed.** Windows copied a 156-byte file and a 64 MiB file onto
CLOUDSTICK. The Pi captured their contents safely, returned the drive, uploaded
them and checked downloaded Google copies. A later controlled Wi-Fi outage also
passed automatic reconnection and backup. **17 file versions are now verified**,
with local copies retained and one cloud attempt per version.

| Requirement | Result and evidence |
| --- | --- |
| USB drive functionality | PASS on the tested Windows 11 Home host; removable exFAT drive, normal drive letter and signed Microsoft storage driver |
| Safe file capture | PASS; live configured/loaded → configured/empty observation, ordinary unbind, exclusive RO loop/mount, nonrepairing exFAT check and clean flags |
| Google Drive automatic upload | PASS; 17 verified versions, 212,866,521 bytes, one cloud attempt each |
| Verified backup integrity | PASS; Windows read-back hashes match staged bytes; uploaded contents downloaded and SHA-256 checked |
| Offline recovery | PASS; a Windows 1 MiB file remained staged/queued with zero attempts while Wi-Fi was disconnected; the tested guard reconnected and backup verified |
| Existing connectivity preserved | PASS after recovery; approved Wi-Fi/SSH and profile credentials/policy retained; earlier unexpected Wi-Fi fault is documented below |
| Repeat-copy behavior | PASS; repeating the same Windows file retained its contents and created no additional job or upload |
| Original/version retention | PASS; source image, local staged copies and earlier verified versions remain; no automatic deletion |
| Drive return | PASS; Windows drive reappeared and original/copied synthetic files still matched |
| Clean Pi restart | PASS in the earlier Linux-host test; both core services/USB returned and a queued 99-byte file verified after boot |
| Focused reconnection guard | PASS in the real controlled outage; three failed samples, approved-profile activation, unused independent fallback, same boot/core PIDs and healthy follow-up after 443 seconds |
| Guard startup after another reboot | NOT TESTED; tested timer is enabled/active, which is configuration evidence rather than an additional reboot test |
| Permanent Google authorization | NOT READY; existing External/Testing setup has a seven-day refresh-grant lifetime |

The initial Windows pass produced 16 verified versions, including the two copied
files and two small Windows metadata versions. The recovery trial added the
1 MiB version, bringing the total to 17. Existing versions were not uploaded
again. See the [Windows record](WINDOWS_ACCEPTANCE.md) and dated evidence below.

## Everyday behavior and limits

Copy files onto CLOUDSTICK, finish copying, choose Windows **Eject**, and keep
the USB cable connected for power. The Pi stages only after verified host release,
then restores the drive and backs up the completed batch over built-in Wi-Fi.
Windows chooses the drive letter; it was D: on this tested computer.

A quiet period, unchanged size or concurrent RO mount is not release proof.
**Fully automatic capture without Eject is not implemented.** The present trial
uses a dedicated 1 GiB MBR/exFAT backing image on the existing Pi SD filesystem.
These tests did not reflash, reformat or repartition the card, and the live Linux
root filesystem is not exported. USB Ethernet/Samba remains an optional
alternative and is not used as the drive interface here.

The protected, separately approved dev22 build has both core services enabled.
Public hardware gates remain false. The root controller has no cloud/network API;
only completed private batches reach the unprivileged Google worker. The original
prepared Google configuration is preserved; its separate private writable copy
supports token refresh. Cached uplink policy is not a kernel egress guarantee.
Before permanent office use, resolve the Google development/testing limit with
the owner. App publication/verification was not performed automatically. See
[cloud setup](CLOUD_SETUP.md) and [Google's expiration rules](https://developers.google.com/identity/protocols/oauth2).
Longer workloads, other/managed Windows hosts, suspend behavior and arbitrary
power-loss durability remain outside this acceptance. Secondary features stay paused.

## Unexpected Wi-Fi fault and tested mitigation

After the first Windows backup pass, Pi Wi-Fi management stopped responding.
Windows could still read the USB files and their hashes matched. The original
cause remains **UNKNOWN**: the previous boot journal was not retained. No bad
password, driver defect, router incompatibility or power failure is inferred.

The owner explicitly approved one unclean Pi power cycle after Windows-only
quiescence: exact identity, exclusive lock, flush and dismount while held, with
**no media Eject**. That preparation did not establish a clean Linux shutdown.
After the physical cycle, Wi-Fi/SSH, USB and the core services returned. All 16
verified versions remained, and the repeated Windows copy created no extra job.
This single recovery is not a guarantee of arbitrary power-loss safety.

A separate [focused recovery operator](../tools/acceptance/wifi_recovery/README.md)
now passes a controlled Wi-Fi-only failure trial. It retries only the explicitly
approved saved profile after three failed samples, with a durable five-minute
cooldown. It never changes radios, restarts all networking, reloads drivers,
alters credentials or changes USB. Unknown/connecting/radio-off states and another
active profile refuse activation; ISP/DNS failure alone is insufficient.
The real timer reconnected without its independent failsafe having to activate.
A Windows file queued offline then uploaded and passed downloaded verification.
Follow-up checks remained healthy for 443 seconds, with the same boot/core PIDs.
The tested timer is enabled; the public code/configuration examples stay disabled.
This is a tested mitigation, not an established explanation or universal fix for
the original fault. Stop the timer and active oneshot before intentional maintenance.

## Earlier evidence and recovery boundaries

Startup preparation backed up/commented the sole legacy `g_ether` loader, added
`libcomposite`, and disabled/masked the previously inactive Pi ICS watcher.
Firmware, command line, initramfs, Wi-Fi policy, SSH and accounts were preserved.
The initramfs needed no rebuild. Profile reconstruction and the earlier clean Pi
reboot passed. Ambiguous journals, partial profiles, pending root captures, dirty
filesystems, power uncertainty or competing drivers refuse startup and retain
records. See [startup and rollback](MSC_BOOT.md).

The [USB Ethernet recovery check](acceptance/pi-usb-ethernet-module-recovery-2026-11-14.json)
proved saved `g_ether` module reload, configured UDC and restored ICS with Wi-Fi
and boot files unchanged. USB-host IP addressing/SSH after that recovery were
NOT TESTED. Saved recovery parameters remain private; never switch drivers while
a host owns the storage image.

The first user-confirmed copy/eject was attributed to Windows from conversation
context; its host OS was not independently recorded and its timed observer expired.
It remains an operator-confirmed historical capture, separate from the later
independently recorded Windows acceptance. Dev20 stopped safely on exFAT inode-only
changes between opens. Copies and journal were retained; dev21 corrected only
cross-pass inode equality while keeping per-open identity/content checks. Its
unchanged/delta tests passed. [Upstream exFAT inode assignment](https://github.com/torvalds/linux/blob/v6.18/fs/exfat/inode.c#L629-L669).

The first short Linux radio-outage window ended before queue observation and is
not counted as an offline-queue pass. The longer test observed the actual offline
queue and recovery. An earlier HTTPS probe returned HTTP 403 with validated TLS;
that proves transport only. Actual Google upload/download checks passed.

Sanitized evidence:

- [Windows copy/eject/capture/Google, with the later fault recorded](acceptance/pi-windows-mass-storage-google-dev22-2026-11-14.json)
- [Explicitly approved unclean power-cycle recovery and repeat check](acceptance/pi-windows-recovery-powercycle-dev22-2026-11-14.json)
- [Controlled automatic Wi-Fi recovery and queued Windows backup](acceptance/pi-wifi-recovery-guard-dev22-2026-11-14.json)
- [Limited USB Ethernet module recovery](acceptance/pi-usb-ethernet-module-recovery-2026-11-14.json)
- [Initial file](acceptance/pi-mass-storage-google-first-file-2026-11-14.json)
- [Automatic Linux USB](acceptance/pi-mass-storage-auto-linux-2026-11-14.json)
- [Isolated offline worker](acceptance/pi-core-offline-worker-2026-11-14.json)
- [Dev21 changed versions](acceptance/pi-mass-storage-delta-dev21-2026-11-14.json)
- [Earlier clean dev22 reboot](acceptance/pi-mass-storage-reboot-dev22-2026-11-14.json)
- [Earlier actual radio outage](acceptance/pi-actual-wifi-outage-dev22-2026-11-14.json)

[Plain-language updates and owner questions](https://github.com/rtariq2015/raspi-cloud-sync-pro/issues/2).
