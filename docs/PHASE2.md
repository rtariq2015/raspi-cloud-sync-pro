# Phase 2: USB controller and network sharing

Development version `0.1.0.dev2`. All live USB/configuration mutations and live
SMB sessions are disabled by a source-code gate. No config flag, environment
variable, service marker or dashboard request bypasses it. This is preparation
for a separately approved disposable-data Pi trial, not a hardware release.

## Architecture decision

| Mode | Windows experience | Filesystem owner | Safe automatic capture |
| --- | --- | --- | --- |
| `ethernet_samba` — preferred | USB Ethernet adapter plus mapped network drive | Pi/Linux; Windows sends SMB file operations | Hold an SMB read handle denying write/delete opens, stage that version, then release it |
| `mass_storage` — compatibility option | Removable block disk / flash-drive interface | Windows while exported | Only after a supported clean host eject, closed LUN and detached gadget |

Mass storage cannot satisfy “keep Windows writable ownership and automatically
read newly copied files without a release protocol.” Quiet periods, timestamps,
Pi `sync`, read-only second mounts and copies of a live backing image do not fix
the problem. The [kernel explains host buffering](https://docs.kernel.org/usb/mass-storage.html).
Mass-storage mode therefore waits for a supported eject; it never times out into
a read. Windows removal may disable the entire USB device rather than just eject
its LUN, so automatic reappearance is hardware/driver dependent.

Ethernet/Samba meets the desired continuous-access architecture, subject to real
server/driver acceptance. It is a network drive, not a physical removable disk:
software/cameras requiring a block device must use the compatibility mode and
accept its handoff requirement. Composite Ethernet plus writable mass storage is
not implemented; never expose the Samba filesystem as a writable USB LUN.

## Implemented files

| Component | Files and behavior |
| --- | --- |
| Mode policy | `device_modes.py`: selectable modes, approved uplinks, metered policy and a future offline-switch guard |
| Official Ethernet integration | `ethernet.py`: pinned provider review, no-side-effect plan and gated offline provisioning adapter |
| Mass-storage controller | `gadget.py`: Phase 1 ownership integration, process lock, fsynced intent journal, live-session eject observation, staging and conservative recovery |
| Linux backend | `linux_gadget.py`: detached configfs profile, protected regular image, loop/mount/device-number checks, `fsck.exfat -n`, read-only mount, ordinary unmount/detach and reattach |
| Network ingestion | `smb_ingest.py`: periodic candidate inventory, SMB deny-write/delete handle, streamed ready batches, crash reconciliation and Phase 1 queue/engine integration |
| Prototype runtimes | `device_service.py`: gated controller and share worker; observed NetworkManager/default-route policy; no network reconfiguration by the worker |
| Diagnostics | `hardware.py`: Pi model, UDC state/speeds, tools, power flags and blockers; never mounts or authorizes a device |
| Deployment references | `deploy/`: static systemd units, separate Samba/DHCP, NetworkManager local-link and scoped firewall templates |

## Samba completion and staging contract

All share writers must use the same Samba server. No local process may write
directly into the shared tree. The initial share filesystem is Pi-owned ext4,
separate from the mass-storage image; the worker rejects loop-backed shares.
Samba users and Unix permissions distinguish the Windows writer from a read-only
backup identity. Symlinks and alternate data streams are unsupported.

A candidate notification or metadata change does not establish completion. The
backup reader requests an encrypted SMB read handle with `share_access="r"`,
granting neither write nor delete sharing. Existing incompatible writer handles
must make that open fail; new writes/deletes must remain denied until the snapshot
finishes. Samba must resolve relevant leases/oplocks before granting the handle.
The [client share-access implementation](https://github.com/jborean93/smbprotocol/blob/master/src/smbclient/_io.py)
is reviewed, but actual Samba/Windows behavior still requires integration tests.

While holding that handle, the reader gets the file size, streams all bytes and
hashes them, verifies the byte count, flushes payload/manifest/directories, and
closes the SMB handle successfully. Only then does it atomically publish a
`.ready` batch. Busy writers defer with no scan cursor advance. Read/close failures,
short reads, low space or interruption leave no uploadable batch. A scan after
restart covers lost notifications; candidate fingerprints avoid repeated work
in a running process. Ready-batch reconciliation repairs publication→queue crashes.
Filename suffixes do not prove completeness and are not silently excluded; even
temporary-looking names require the same server-enforced stable-version read.

This captures a stable closed file version, not an application's final intent or
an atomic multi-file transaction. Applications can reopen files; those become
later versions. Long-lived write handles may defer indefinitely. Large snapshots
temporarily block edits to that file. Cross-file consistency requires an explicit
application commit protocol. No quiet-period fallback is provided.

## Queue and Internet behavior

USB networking, Samba file copies and staging work without Wi-Fi. A local USB
link is not proof of Internet availability. The worker reads cached NetworkManager
connectivity and the lowest-metric IPv4 default route. Unknown, ambiguous, portal,
unapproved and disallowed metered states pause cloud attempts without spending
attempts. Host ICS must be explicitly allowed as `usb_host_ics`; it is not enabled
by the worker. An empty approved-uplink list permits an offline-only first trial.

Koofr WebDAV and Google Drive continue using Phase 1's rclone adapter, immutable
version paths, independent SHA-256 read-back checks, SQLite history and bounded
retries. Originals are never automatically removed. A cloud timeout may delay
the next capture scan, but Samba runs separately and can keep accepting files.
Staging/disk exhaustion pauses the worker rather than deleting data. Windows
Offline Files caching is disabled in the share template.

Observed uplink policy is not an egress firewall. VPNs, policy routing, multiple
interfaces, IPv6 and administrator-managed network policy need acceptance before
deployment. This prototype is deliberately conservative about unknown routes.

## Mass-storage handoff and recovery

The controller holds a cross-process lock for its whole session. It observes a
loaded removable LUN under a configured UDC before arming. A subsequent empty LUN
in that uninterrupted, exclusively administered session is the candidate kernel
eject signal. Link loss disarms it; startup with an empty LUN never qualifies.
Only the controller may administer its configfs tree during a trial. Validate
the target kernel's close/unbind semantics before relying on this mechanism.

After release it unbinds the gadget, confirms closed backing storage, attaches a
read-only loop with partition 1, verifies read-only flags, and requires clean
`fsck.exfat -n` completion. It mounts exFAT read-only with `nosuid,nodev,noexec`,
verifies actual device numbers and mount flags, then uses Phase 1 staging.
Reattachment requires successful normal unmount, loop detach, no remaining local
references and healthy current power telemetry. `forced_eject`, forced/lazy unmount,
repair, formatting and automatic deletion are absent.

Every intent is flushed before mutation. The journal cannot authorize replay.
An interrupted/ambiguous state stops without re-exporting the image. Explicit
recovery can clean only a matching, journal-owned, read-only loop/mount while the
gadget and backing LUN are closed; it never repairs, stages or reattaches. Changed
image identity, corrupt/missing evidence or foreign resources require manual review.
Mass-storage ready batches remain available for Phase 1 ingestion; its prototype
controller does not run a cloud worker itself.

## Switching modes and service safety

`mode-plan` selects a policy without applying it. A future dashboard can request
an offline switch, but cannot supply its own evidence: a trusted coordinator must
freeze ingress first, drain active SMB handles and pending writes, finish staging,
detach the gadget and release all local image references. Unknown/busy evidence
is rejected. Phase 2 supports only reviewed offline provisioning plus reboot;
there is no live switch or automatic driver unload.

All Phase 2 units are static (no `[Install]` section), with no automatic restart.
Device/Samba units also require an absent-by-default approval marker and the
Python code gate. The marker alone enables nothing. The mass-storage controller
requires root in the host mount namespace and is not a hardened production
service. The share worker runs as `raspi-backup`; Samba/DHCP are separate units.
Do not copy/install/enable templates on the development computer.

The later dashboard preview has a separate static template with its own approval
marker and unprivileged identity. It does not use the gadget code gate because
it exposes no device/network mutation API; it does not enable the gated services.
See [DASHBOARD.md](DASHBOARD.md) for its separate acceptance evidence.

## Before real testing

Follow [PI_TEST_GUIDE.md](PI_TEST_GUIDE.md), including separate approval, exact
OS/provider/driver versions, disposable data, offline capture first, power checks,
server-enforced lock tests and failure injection. See
[official provider findings](OFFICIAL_USB_ETHERNET.md) and
[validation](VALIDATION.md). No physical Pi, live USB device, Samba server or cloud
account has been used to validate the Phase 2 device/sharing runtimes during
development. The later [management connectivity baseline](PI_CONNECTIVITY.md)
records USB/Wi-Fi checks on a physical Pi separately; it does not validate those
runtimes or lift their code gates.
