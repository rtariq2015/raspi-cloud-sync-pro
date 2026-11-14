# Raspberry Pi OS Lite preparation and first disposable-data trial

**Do not apply device setup to the development computer.** This guide prepares a
future trial; Phase 2 does not authorize flashing, formatting, changing boot/network
settings or starting hardware services. Live operations remain code-gated off.
Use the preferred Ethernet/Samba mode first; mass storage is a separate trial.

An existing Pi now has a verified USB/Wi-Fi management baseline; see
[PI_CONNECTIVITY.md](PI_CONNECTIVITY.md). Preserve that working access. Do not
reflash it or replace its USB profiles just to match the example configuration.
Connectivity acceptance does not enable the project hardware gate or approve
Samba/cloud services. Sections describing fresh-device provisioning apply only
to the separately approved trial, not as an instruction to replace this baseline.

## 1. Approval and equipment record

Obtain separate approval naming the disposable SD/image/share, Pi Zero 2 W,
selected mode, Windows host and test scope. Back up anything valuable elsewhere.
Record OS image, kernel, architecture, Python, provider package, Samba, smbprotocol
and rclone versions. The official provider reviewed here targets `pios/trixie`;
do not assume the same package is available or behaves identically on Bookworm.
Use a fresh approved Raspberry Pi OS Lite installation prepared separately.
No project script creates, partitions, formats or repairs storage.

Use one data-capable micro-USB cable in the Zero 2 W's USB/OTG port nearest HDMI.
Check the intended host's power capability with suitable measurement equipment,
including Wi-Fi/CPU/SD load. Avoid unreviewed dual-power wiring. Keep the host
awake for the initial test. Agree how to stop safely and retain logs after failure.

## 2. Read-only checks before activation

On the Pi, inspect `/proc/device-tree/model`, `/etc/os-release`, `uname -a`,
`python3 --version`, `/sys/class/udc`, and `vcgencmd get_throttled`. Query
`apt-cache policy rpi-usb-gadget samba dnsmasq network-manager exfatprogs` without
installing anything. Record the result. The checker does not prove a host flush,
electrical stability, Windows driver compatibility or approval.

These project commands are safe on a development machine too:

```sh
PYTHONPATH=src python3 -m raspi_cloud_sync_pro status
PYTHONPATH=src python3 -m raspi_cloud_sync_pro mode-plan config/device.example.json
PYTHONPATH=src python3 -m raspi_cloud_sync_pro diagnostics --mode ethernet_samba
PYTHONPATH=src python3 -m raspi_cloud_sync_pro network-status
PYTHONPATH=src python3 -m raspi_cloud_sync_pro check-hardware --mode ethernet_samba
```

`check-hardware` intentionally returns exit code 2: live operations are not
authorized by this build. Missing/non-Pi hardware and unknown power are reported
as blockers, not guessed. No root permission is needed for basic diagnostics;
some observations may be unavailable to an unprivileged account.

## 3. Controlled package/project installation — approved Pi only

Before package installation, prevent package hooks from starting untested Samba,
DHCP or profile watchers. On a **fresh approved Pi only**, have the operator mask
`smbd.service`, `nmbd.service`, `samba-ad-dc.service`, `dnsmasq.service` and
`rpi-usb-gadget-ics.service`. Inspect existing units first; do not disrupt an
existing file/network server. The project supplies no unattended installer.

Install the reviewed distribution packages for Python/venv, NetworkManager,
official `rpi-usb-gadget`, Samba, dnsmasq and exfatprogs as appropriate to the mode.
Their package hooks can change the Pi; installation is part of the separate
approval, not a read-only check. Never run the retained Koseng setup scripts or
the official source checkout's build/install scripts during development.

Install the reviewed project build into a root-owned
`/opt/raspi-cloud-sync-pro/venv`, using its Python `pip install .[ethernet]` for
Ethernet mode. The core supports Python 3.9+, while the optional current SMB client
requires Python 3.10+. Use the distribution's Python on the reviewed Lite image.
Do not install Python packages into the system interpreter with a PEP-668 override.

For actual device operations, a separately reviewed **test-only build** must
enable `HARDWARE_OPERATIONS_ENABLED` in `linux_gadget.py`; record its exact commit
and wheel checksum. The committed Phase 2 build leaves it false. Do not add an
environment override or edit an installed file invisibly. A marker file alone
does not enable any operation. Do not create the approval marker until all
configuration and service templates have been reviewed.

## 4. Prepare separate identities and paths

On the approved Pi, create no-login Unix identities and corresponding Samba
accounts, choosing private passwords interactively. Never commit credentials.

| Path/identity | Required ownership and purpose |
| --- | --- |
| `raspi-share` | Windows authenticated writer; no interactive Unix shell |
| `raspi-backup` | Read-only Samba identity; owns private staging/queue; no share write permission |
| `raspi-sync` group | Both identities; read/traverse access to source files |
| `/srv/raspi-cloud-sync-pro/share` | Pi-owned ext4, owner `raspi-share:raspi-sync`, mode 0750; no symlinks/local writers |
| `/var/lib/raspi-cloud-sync-pro/smb-staging` | `raspi-backup`, mode 0700, outside the share |
| `/var/lib/raspi-cloud-sync-pro/smb-state` | `raspi-backup`, mode 0700; queue/rclone config, not shared |
| `/etc/raspi-cloud-sync-pro/smb-credentials.json` | `raspi-backup`, mode 0600; only `username`/`password` for its read-only Samba account |
| Public device/service config | Root-owned, not writable by the share/backup accounts |

Use the example JSON files as references. For the first trial, set
`approved_uplinks` to `[]` in the private device policy: this explicitly disables
cloud attempts even if Wi-Fi is online. Keep real cloud credentials absent until
the local snapshot/locking test is accepted. Select `ethernet_samba` exclusively.

## 5. Provision USB Ethernet offline

Verify the mass-storage controller is stopped, no writable LUN is exported, no
image loop/mount exists, ingress is frozen and no file handles/transfers remain.
Remove conflicting boot gadget configuration through a reviewed offline change;
do not unload a busy driver. Leave existing data/image files intact.

Only on this approved, quiescent Pi, provision the official provider. Its `on`
operation writes boot/NM configuration and may try to enable its watcher. Keep
the watcher masked/disabled. Disable autoconnect on both official `USB Gadget`
NM profiles by their verified UUIDs, preserving unrelated Wi-Fi profiles.

Review/install our dedicated local NetworkManager profile as mode 0600, DHCP
configuration, standalone Samba config and scoped firewall rules from `deploy/`.
The templates intentionally do not autoconnect/start: enable the reviewed local
profile only after confirming the subnet does not overlap Wi-Fi/VPN/other Pis.
Its default is `10.12.194.1/28`; the current worker checks that exact address.
Changing it requires reviewing/updating all matching configuration and checks.
Reboot into the selected mode; never enable `g_ether` and mass storage together.

On the Pi, use `testparm -s /etc/raspi-cloud-sync-pro/smb.conf`,
`dnsmasq --test --conf-file=/etc/raspi-cloud-sync-pro/usb-dhcp.conf` and review the
firewall with the administrator before applying it. Do not replace the existing
system-wide firewall or Samba configuration blindly. Permit SMB only on loopback
and `usb0`, DHCP only on `usb0`; do not enable NAT, forwarding or ICS by default.

Review/copy the static service templates only on the approved Pi. They have no
boot-enable section. Start only the needed DHCP and Samba test units manually,
after reviewing the code gate and approval marker. Do not start the mass-storage
controller in Ethernet mode. `Restart=no` preserves failures for inspection.

## 6. Windows 11 binding and mapping

Obtain administrator/IT approval for the official signed RNDIS driver matching
VID `2E8A`, PID `0013`. Validate publisher/catalog trust; do not weaken Secure Boot,
device policy, NTLM policy or firewall controls. ARM64 Windows remains unverified.
Check Device Manager and `ipconfig`: the USB NIC should receive `.2`–`.14` on the
local subnet while its normal Internet default route stays intact.

After approved binding/DHCP, map a drive with a prompted password:

```bat
net use Z: \\10.12.194.1\CloudSync * /user:raspi-share /persistent:yes
```

This creates a network drive, not a removable disk. Test that guest access fails,
the backup identity cannot write, SMB1 is rejected, and port 445 is inaccessible
from the Pi's Wi-Fi interface. If managed-computer policy prevents this, stop and
consult IT rather than working around it.

## 7. First local-only acceptance run

Keep the device policy's approved-uplink list empty. Start the approved share
worker with `--once` as `raspi-backup`, then inspect its read-only history. After
the one-shot checks, the static worker unit can be started manually for the trial.

Copy a small folder, an empty file, Unicode names and a large disposable file.
Keep a write handle open: the backup must defer that file. Attempt a second write
while the backup holds its deny-write handle: it must block/fail, never alter the
staged bytes. Test lease breaks, reconnects, edits after close and rename/delete
conflicts. Compare original/staged hashes. A folder is not an atomic transaction.

Disconnect Wi-Fi: USB copying/capture must still work and queued attempts remain
unspent. Interrupt the worker at read, close, publication and queue-commit points;
restart and verify only complete batches reconcile. Simulate space exhaustion
with controlled disposable storage. Confirm originals are never automatically
deleted. Do not perform uncontrolled power cuts on valuable media.

Only after local acceptance, configure approved **test accounts** in a protected
rclone config, review the actual Internet route, allow Wi-Fi in device policy and
restart just the worker. Verify upload → read-back verification, cloud outages,
bounded retries and recovery. VPN/policy-routing behavior needs separate review.

## 8. Separate mass-storage trial

Shut down sharing and freeze ingress, prove no active/pending I/O, then change
mode offline and reboot with the official Ethernet provider inactive. Use an
existing, separately approved disposable partitioned exFAT image, not a real block
device. No project command creates/formats it. Set the observed UDC and unique
serial in the reviewed gadget config. Sample VID/PID values are lab references,
not assigned product identifiers for shipping.

Use protected root-owned image/controller/staging paths and an empty dedicated
mount directory. Parse/verify the static controller unit, then start it manually
using the approved test build. Copy and safely eject from Windows. Confirm the
LUN-close observation, synchronous unbind, read-only loop/mount, clean check,
snapshot, normal unmount/detach and re-enumeration. If Windows removes the entire
device or evidence is missing, the controller must wait/stop; do not force eject.

The separate approved boot preparation must provide `dwc2` in peripheral mode,
`libcomposite`, a mounted configfs at `/sys/kernel/config`, and exactly the selected
UDC. Do not load legacy `g_mass_storage`. The controller deliberately does not load
modules, mount configfs, edit boot files or repair a volume itself; missing
prerequisites must be resolved explicitly on the test Pi before starting it.

Test failure at each transition and process/reboot recovery. Recovery must never
automatically re-export an ambiguous or mounted image. Preserve the journal and
incomplete/ready batches for analysis. Controller recovery only cleans proven
resources and still requires manual review; do not delete a journal merely to
make startup proceed.

## Stop conditions

Stop on undervoltage, unexpected driver/device ownership, invalid read-only flags,
dirty filesystems, failed unmount/detach, unexplained resets or wrong credentials.
Retain source data and diagnostics, redact secrets, and record exact test results.
No merge or release follows automatically from a passing disposable-data trial.
