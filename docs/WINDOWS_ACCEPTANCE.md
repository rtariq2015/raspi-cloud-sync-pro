# Windows USB-to-Google acceptance

**PASS — 14 November 2026**, on the tested Windows 11 Home computer and existing
Raspberry Pi Zero 2 W. This is the approved 1 GiB disposable-image development
trial, not a release or a guarantee for every Windows/managed computer.

| Real check | Result |
| --- | --- |
| CLOUDSTICK appears as removable storage | PASS; Windows assigned D:, using its signed Microsoft storage driver |
| Small and larger Windows copies | PASS; 156 bytes and 64 MiB, flushed/closed and read-back SHA-256 checked |
| Safe Windows media Eject | PASS; exclusive lock, buffer flush, locked dismount and ordinary media-eject request; no force or USB power-off |
| Independent Pi ownership evidence | PASS; fresh configured/loaded → configured/empty LUN, unbind, exclusive read-only staging and clean release |
| Automatic Google backup | PASS; both Windows versions uploaded once and downloaded for SHA-256 verification |
| Drive reappearance and local retention | PASS; original/copied files still matched after reattachment |
| Repeat identical Windows copy | PASS after management recovery; the same contents remained and no extra job/upload appeared |
| Offline Windows batch and automatic reconnection | PASS; a further 1 MiB file queued with zero attempts while offline, then verified after the tested guard reconnected |

The first Windows backup pass brought history to 16 verified versions. The final
controlled outage/reconnection test brought it to **17 versions / 212,866,521
bytes**, each with one cloud attempt. The independent reconnect failsafe was
available but did not need to activate. The Pi boot/core PIDs stayed the same
through that controlled test, and follow-up health checks covered 443 seconds.
Windows SSH was used only to automate the acceptance work. Ordinary file copying
requires no Windows application, custom storage driver or USB networking.

## Using the accepted first workflow

1. Keep the Pi's USB **data** socket connected to Windows with a data-capable
   cable; the same cable supplies power. On Zero 2 W this is the socket nearest HDMI.
2. Open **CLOUDSTICK** in File Explorer and copy files normally. Windows assigns
   the drive letter; another computer may choose something other than D:.
3. Finish copying, then choose **CLOUDSTICK → Eject**. Keep the cable connected
   so the Pi stays powered. Do not unplug its power to start a backup.
4. The Pi captures the released batch, returns the drive and uploads over Wi-Fi.
   Files remain locally queued if the approved connection is unavailable.

A copied file is not a verified cloud backup until the upload and downloaded
contents check succeeds. Automatic capture **without Eject is not implemented**;
a timer or unchanged file size is not sufficient proof of safe access.

## Incident and remaining limits

An unexpected Wi-Fi management fault occurred after the first Windows pass.
USB file reads still worked. The owner separately approved one unclean power
cycle after Windows-only quiescence; that was not a clean Pi shutdown. All 16
versions survived, but the prior boot journal was unavailable, leaving the cause
**unknown**. The subsequent focused reconnect guard passed its own controlled
test; that does not establish the original cause or arbitrary power-loss safety.

The guard timer is enabled and active; an additional reboot specifically testing
that new timer has not been performed. Core USB/backup boot recovery had passed
a separate earlier clean-reboot test. Before permanent office use, resolve the
current Google External/Testing authorization's seven-day refresh-grant limit.
Longer workloads, suspend/power behavior and other managed hosts remain open.
Public hardware gates and configuration examples stay disabled. No main merge or
release is authorized by this record.

Evidence: [first Windows pass and fault](acceptance/pi-windows-mass-storage-google-dev22-2026-11-14.json),
[approved power-cycle recovery and repeat check](acceptance/pi-windows-recovery-powercycle-dev22-2026-11-14.json),
[automatic reconnect and queued backup](acceptance/pi-wifi-recovery-guard-dev22-2026-11-14.json).
Follow the [plain-language progress thread](https://github.com/rtariq2015/raspi-cloud-sync-pro/issues/2).
