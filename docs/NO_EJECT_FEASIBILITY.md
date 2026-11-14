# No-eject feasibility decision

Reviewed 14 November 2026 against accepted baseline
`9a9aab0eba54bbbe8f090a237b4396add1a14102` on `development`.

**Decision: preserve the working safe-Eject implementation. No evaluated design
establishes all original requirements together: an ordinary Windows USB flash
drive, no custom Windows application, automatic recognition of completed files
without Eject, and demonstrably consistent capture. Do not enter Stage 2.**

This is a finding about the guarantees available in the evaluated designs, not
a proof that every conceivable custom storage device is impossible. A timeout,
flush, clean flag or snapshot alone does not supply the missing completion
contract. No no-eject implementation or prototype was added.

## Scope and preserved baseline

This investigation covered source, saved acceptance evidence, primary technical
documentation and local automated tests. It made no connection to the live Pi,
Windows host, backing image or Google Drive. Current device health is **NOT
TESTED**. The earlier unexplained loss of management access remains unresolved.

The source review covered `storage.py`, `gadget.py`, `linux_gadget.py`,
`mass_storage_appliance.py`, `staging.py`, `history.py`, `engine.py`,
`providers.py` and `staged_worker.py`. Existing behavior remains:

1. Windows owns the writable USB image while it is exposed.
2. Proper Windows safe Eject releases media. The running observer must see a
   fresh configured/loaded to configured/empty transition. A saved state or cable
   disappearance is insufficient.
3. The controller detaches the gadget, verifies exclusive access, checks the
   filesystem without repair, captures through a read-only mount, unmounts and
   publishes a hashed ready batch before returning storage to Windows. Failed
   checks stop the handoff.
4. The separate worker queues retained versions, retries eligible failures,
   uploads versioned objects and verifies downloaded contents with SHA-256.
   Existing path/content duplicate suppression and local retention remain.

Google upload operates on the captured batch, rather than waiting for the
network while holding the Windows drive detached. Eject is still required for
each capture batch; leaving the USB cable connected maintains power. Eject does
not shut down Raspberry Pi OS.

Historical evidence is retained in [Windows acceptance](WINDOWS_ACCEPTANCE.md),
[core recovery](CORE_RECOVERY.md), and the
[Windows-to-Google record](acceptance/pi-windows-mass-storage-google-dev22-2026-11-14.json).
Those records include small and 64 MiB Windows copies, release/capture/reattach,
downloaded cloud verification, and a later total of 17 verified versions.
The [Wi-Fi outage record](acceptance/pi-actual-wifi-outage-dev22-2026-11-14.json)
covers a queued offline file and recovery. These are previous physical results,
not new tests in this mission or proof of unattended reliability.

## What a safe completion contract must establish

Four different properties must not be conflated:

| Property | Meaning | Insufficient substitute |
| --- | --- | --- |
| Durability | Submitted bytes survive the promised storage boundary | FUA/flush does not announce the end of a file transfer |
| Stable view | A selected block/file version cannot change during capture | Read-only mounting does not stop Windows writes |
| Filesystem coherence | Metadata and file data belong to a consistent state | Copying changing blocks is not a snapshot |
| Completed transfer | A declared file/version transfer succeeded | Valid metadata or a long pause does not establish success |

**Logical counterexample, not an executed test:** two applications can issue
identical block writes for a valid 4 MiB file and flush them. One finishes; the
other pauses before adding the remaining content. There can be an arbitrarily
long interval with identical storage-visible history. A device-only quiet timer
cannot distinguish them. Publishing risks treating the paused transfer as
finished; waiting indefinitely misses the finished transfer.

The same ambiguity occurs with cancellation and in-place replacement. A genuine
completed version may later be reopened and backed up as a new version; the
requirement is a trustworthy boundary for each version, not permanent
immutability of the user's editable file. Windows can also extend a file before
writing its contents, so apparent final size is insufficient.
[Microsoft SetEndOfFile](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setendoffile).

## Architecture comparison

The assessments below distinguish documented mechanisms from proposed
engineering. Resource and complexity assessments are qualitative unless stated
otherwise; no new Pi benchmark was performed.

| Design | Ordinary USB disk? | Additional host requirement | Credible completion boundary | All original requirements? |
| --- | --- | --- | --- | --- |
| A. Stock USB mass storage | Yes | Proper safe Eject for accepted capture | Host release plus independent Pi checks | No: requires Eject |
| B. Filesystem-aware block controller | Intended, unproven | None intended | Missing for arbitrary application transfers | Not established |
| C. Block copy-on-write snapshots | Can retain disk interface | None for block snapshot alone | Missing without host/producer coordination | Not established |
| D. MTP/PTP object transfer | No: portable device/camera | Compatible Windows class support | Successful complete object transaction, then durable receiver commit | No: changes interface |
| D. USB Ethernet + SMB | No: network share | Network/share setup and optional mapping | Explicit producer commit or validated completion protocol | No: changes interface/setup |
| E. Cooperating Windows producer | Yes during exposure | Custom software/cooperation; volume operations need privileges | Explicit batch commit plus coordinated release | No: changes host requirement |

### A. Standard USB Mass Storage

**Mechanism and safety.** Linux's gadget documentation warns that reads of a
host-writable backing file do not have well-defined contents under host
buffering. It documents removable-media Eject closing the backing file until
userspace supplies media again.
[Linux mass-storage gadget](https://docs.kernel.org/usb/mass-storage.html).

Upstream Linux v6.18 implements stop/eject by closing the removable LUN while
holding its filesystem semaphore. Cache synchronization instead flushes the
backing file; supported FUA requests use synchronous writes. Neither operation
defines an application file-completion event.
[Mass-storage function](https://github.com/torvalds/linux/blob/v6.18/drivers/usb/gadget/function/f_mass_storage.c),
[backing-file operations](https://github.com/torvalds/linux/blob/v6.18/drivers/usb/gadget/function/storage_common.c).
This is upstream source evidence, not a fresh inspection of the running Pi's
kernel binary. A raw eject command alone is not proof that Windows previously
finished its filesystem work: the accepted path requires proper Windows Eject
and the subsequent independent checks.

**Windows compatibility.** Standard disk behavior is historically demonstrated
on the accepted Windows host. Quick removal, the default since Windows 10 1809,
disables disk-write caching; Better performance permits it. Thus the argument is
not that every Windows host always caches disk writes. **Engineering inference:**
neither removal policy communicates per-file completion or prevents a producer
from resuming writes.
[Microsoft removal policies](https://learn.microsoft.com/en-us/windows/client-management/client-tools/change-default-removal-policy-external-storage-media).

**Resources, complexity and reuse.** This is the existing Zero 2 W path with
ordinary image, staging, queue and worker costs. Reuse it unchanged. The kernel
gadget's small buffers are not a measurement of whole-device RAM consumption.
No safe small change supplies no-eject completion. Automatic detachment after
inactivity can interrupt a later host write; read-only media prevents copying.

**Proof gate.** A proposed extension must first supply a documented completion
and coherent-capture boundary without Eject or host assistance, then qualify
actual Windows behavior. No such boundary was identified.

### B. Filesystem-aware USB storage

**Mechanism.** exFAT initialized-data length, total length, directory checksums
and the volume dirty bit describe data/metadata state. A clear dirty bit denotes
probable consistency and may be cleared between metadata updates. None is an
application transfer-success marker.
[Microsoft exFAT specification, sections 3.1.13.2, 7.4, 7.6.5 and 8.1](https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification).

**Engineering inference and safety.** A custom controller could validate
allocation maps/chains, directory sets, lengths and ordering. It must also
handle cached writes, barriers, open/incomplete files, rename/replacement,
in-place overwrite, malformed metadata and crash recovery. Valid initialized
content can still be a paused prefix or partly overwritten file. Parsing more
metadata does not resolve that ambiguity.

**Compatibility, resources and complexity.** The intended host interface could
remain an inbox Windows disk, but replacing the storage backend requires new
USB/block/filesystem qualification on the Pi. This is substantial storage-system
engineering, not an extension to the staging poll loop. Streaming is possible;
buffers, indexes, recovery metadata and retained copies still consume RAM/SD
space. There is no demonstrated memory, throughput or endurance budget.

**Reuse and proof gate.** Existing ready-batch/queue/provider code becomes useful
only after an immutable, completed version exists. Require a real commit
boundary, consistent ordering, crash recovery, bounded resources and rejection
of every ambiguous case. That first proof is missing; do not implement a parser
as if it provided no-eject completion.

### C. Copy-on-write and snapshots

**Established mechanism.** Device mapper can preserve previous chunks as its
origin changes. COW exhaustion invalidates a snapshot; creation/removal also has
required origin-suspension ordering.
[Linux device-mapper snapshots](https://docs.kernel.org/admin-guide/device-mapper/snapshot.html).

**Engineering inference and safety.** Proper COW can give a stable block view,
but a point between filesystem updates is at best a crash-recovery candidate,
not assured filesystem coherence. Even a coherent snapshot can contain a
paused or canceled file. A byte-for-byte copy of a changing image lacks even
that stable-point property. Linux filesystem freezing controls its local
superblock; freezing the Pi filesystem containing the image does not freeze the
Windows filesystem inside it.
[Linux filesystem freezing API](https://docs.kernel.org/filesystems/api-summary.html#c.freeze_super).

**Compatibility, resources and complexity.** The Windows disk interface could
remain, but current Pi kernel/backend integration and crash behavior are
unqualified. COW adds bookkeeping, space monitoring and SD write amplification.
For origin size `N`, chunk size `C` and `U_i` uniquely changed chunks retained
for each snapshot, approximate payload provisioning is:

```text
N + sum(U_i * C) + COW metadata + retained ready versions + partials + reserve
```

A fully changed snapshot can need roughly another origin's worth of payload.
RAM must also cover buffers/indexes beside the OS and backup worker. These are
planning relationships, not measurements or a capacity guarantee.

**Reuse and proof gate.** Preserve existing staging/queue verification; do not
fabricate host-release evidence to pass a snapshot into the current controller.
A completion contract, recoverably coherent snapshot and fail-safe overflow
behavior are required. COW alone supplies no completion contract; Stage 2 is
not justified.

### D. Alternative USB protocols and hybrids

**MTP/PTP.** Windows provides class support for compliant portable/imaging
devices. This is not mass-storage drive-letter semantics, and class-driver
availability is not acceptance for every Explorer operation or managed PC.
[Windows USB classes](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/supported-usb-classes).
PTP's imaging model is poorly matched to arbitrary QA documents/folders.

Windows WPD exposes an explicit stream `Commit()` after object transfer.
[Microsoft WPD transfer example](https://learn.microsoft.com/en-us/windows/win32/wpd_sdk/transferring-an-image-or-music-file-to-the-device).
**Proposed receiver model, not implemented:** Pi owns its filesystem; stream an
object into private incomplete storage, validate length and successful protocol
completion, fsync and atomically publish a new immutable version. Cancellation,
disconnect or process death must leave the object unpublished. Mapping actual
USB protocol completion to durable receiver commit still needs proof; the WPD
sample alone is not that proof.

A new responder/object database and recovery path are substantial work.
Streaming avoids whole-file RAM buffering, but partials and versions need SD
space. Only the downstream ready-batch and backup engine are reusable. Gate any
prototype on owner acceptance of portable-device behavior, then prove generic
file/folder compatibility, completion/cancellation and bounded Pi resources.

**USB Ethernet + SMB.** A mapped network drive is a different Windows interface;
it needs network/share configuration and authentication. The official Raspberry
Pi project supports RNDIS/CDC-ECM and NetworkManager integration, but this does
not turn a share into a USB disk.
[Official Raspberry Pi USB Ethernet implementation](https://github.com/raspberrypi/rpi-usb-gadget).
Pi filesystem ownership avoids the shared-block-filesystem race. A closed SMB
handle alone still cannot distinguish a canceled partial copy from a deliberately
short file. Strong completion needs producer cooperation, such as a validated
temporary-name/commit contract. Samba/session/locking costs need measurement;
existing share/capture concepts could be reused. Owner-approved interface/setup
changes and exact Windows completion tests are prerequisites. No Samba or
Ethernet deployment is authorized by this comparison.

**Hybrids.** Adding a second LUN, composite interface or read-only view does not
make the writable MSC path communicate file completion. A separate coordinated
transfer channel could help only by changing the host/interface contract.
No unvalidated composite gadget was activated.

### E. Host-assisted completion

**Credible compromise.** A cooperating producer knows the intended copy length
or hash, explicitly commits the batch, closes its handles and coordinates
ordinary release. Windows volume locking checks for open files and flushes
cached data; the lock must be retained during exclusive access. Dismount alone
can permit remount and is insufficient.
[FSCTL_LOCK_VOLUME](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume),
[FSCTL_DISMOUNT_VOLUME](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_dismount_volume).
An agent must defer on conflicting handles, never force a dismount to obtain
access. Direct volume access has administrative requirements.
[Microsoft CreateFile volume requirements](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilea).

A background idle/close watcher or `FlushFileBuffers` alone lacks this contract;
flushing persists buffered information rather than declaring a transfer finished.
[Microsoft FlushFileBuffers](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers).
A commit-marker file is also insufficient if the Pi must inspect the still
host-owned writable filesystem to find it.

**Compatibility, resources and complexity.** This keeps ordinary disk appearance
while exposed, with temporary disappearance during handoff. It requires custom
Windows software or producer integration and host-policy/privilege approval.
The existing Pi release observer, staging and backup worker can be reused;
new Windows coordination/recovery still needs engineering. Pi storage remains
image plus retained versions/partials; Windows agent cost is unmeasured.

**Proof gate.** Owner must relax the no-custom-Windows-software requirement.
Then test explicit completion versus pause/cancel/reopen, lock conflicts,
crashes, reconnects and the independent Pi release checks. Automatic safe Eject
is a compromise, not device-only no-eject capture.

## Resources and deployment proof

The target is Zero **2** W, with a 1 GHz quad-core Cortex-A53, 512 MB RAM,
built-in 2.4 GHz Wi-Fi and USB OTG; it is not the ARMv6 original Zero W.
[Official hardware specifications](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/).
No language change creates a missing storage protocol guarantee. Rust and the
existing backup components remain unchanged.

The present 1 GiB image is a development limit, not the usable capacity of the
entire microSD. Retained versions and staging consume additional space outside
that image. No resizing, capacity claim or automatic deletion is proposed.

Any separately approved candidate must reject partial, paused, canceled,
interrupted and uncertain transfers; safely version reopened/overwritten files;
handle multiple files, conflicting access, process death and insufficient space;
and recover without deleting prior versions. Require durable publication and
exact Windows/Pi behavior before deployment. Simulating a known commit marker
would prove a marker protocol, not that ordinary Explorer supplies one.

## Current automated baseline

The following ran locally in this mission on Python 3.12.3, before this
documentation-only change. **895 tests passed; none failed or skipped.**

| Suite | Tests | Reported execution time | Result |
| --- | ---: | ---: | --- |
| Package | 841 | 42.030 seconds | PASS |
| USB operator fixtures | 28 | 0.115 seconds | PASS |
| Wi-Fi recovery operator fixtures | 26 | 0.123 seconds | PASS |

Exact test invocations, from the repository root:

```sh
.venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -q
.venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tools/acceptance/mass_storage -p 'test_*.py' -q
.venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tools/acceptance/wifi_recovery -p 'test_*.py' -q
```

Environment: `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`; inherited `RCLONE_*`
variables removed. `RASPI_TEST_RCLONE` pointed to the cached rclone v1.76.0 Linux
amd64 executable. Its archive matched the CI-pinned SHA-256
`b00f00b3266c3764413afe599d3ec316f6f43b3e0ea8a0bc1d186d2133e46df7`;
the extracted member matched the executable byte for byte. Tests used temporary
local alias remotes, fake providers, mock device I/O and temporary data. No real
operator script, gadget, network repair or production cloud operation ran.

Limits: this existing virtual environment allows system packages; it is not a
new hermetic build. These are development-computer tests, not Pi ARM execution,
Windows acceptance, endurance or no-eject proof. Rust was unaffected and was
not rerun locally; the historical 11 Rust tests/five CI jobs remain historical
until a new CI run completes. No new simulated no-eject tests were manufactured.

## Stage checkpoint record

| Field | Stage 0 | Stage 1 | Stage 5 documentation checkpoint |
| --- | --- | --- | --- |
| OBJECTIVE | Preserve and verify accepted source/test baseline | Evaluate A-E against unchanged requirements | Record the negative feasibility result and stop |
| WORK COMPLETED | Master report/evidence/source review; local regression baseline; remote HEAD/provenance checks | Primary-source research; completion/safety/resource/proof comparison | This decision record; exact tests, risks and owner decision |
| FILES CHANGED | None | `docs/NO_EJECT_FEASIBILITY.md` | Same file only |
| TESTS RUN | 841 + 28 + 26 local Python tests | Source/document review; logical counterexamples, not executed POC tests | Diff, links, privacy and baseline-preservation checks |
| PASS/FAIL | PASS | PASS research deliverable; implementation gate NOT MET | PASS documentation; no implementation acceptance claimed |
| RISKS | Live health unknown; historical management interruption unresolved | No completion guarantee for unchanged MSC constraints | No device change; existing deployment limitations remain |
| COMMIT | Accepted `9a9aab0`; no reset/discard | Source-only documentation commit on `development` | Same documentation commit; final report records its ID |
| NEXT STAGE | Stage 1 | Skip experimental Stages 2-4; document and stop | STOP for owner completion-contract decision |

Stages **2, 3 and 4 were NOT ENTERED** because Stage 1 supplied no candidate
meeting all original requirements. This follows the mission's explicit negative
feasibility branch; it is not approval to implement an interface compromise.
Stage 5 records that outcome only. Application, configuration, services,
historical acceptance records, Rust, Koseng credits, GPL-3.0 license and the
preserved upstream README remain unchanged. Upstream history is still an
ancestor; the license and upstream README match their originals byte for byte.
Source rollback is the preceding accepted commit; a later revert of this
documentation commit is sufficient. No live rollback is needed.

Existing deployment limits remain: 1 GiB test storage, historical Google
External/Testing authorization expiry, unresolved management instability and
broader host/power/endurance acceptance. Their current live state is unknown.
No Wi-Fi, credentials, USB image, services, reboot or real cloud tests were
changed or initiated.

## Exactly one next milestone

**Owner approval of the first-version completion contract.** Recommend retaining
normal USB disk behavior and safe Windows Eject for the first version, since
this is the demonstrated path. If no-Eject is mandatory, the owner must instead
explicitly approve an interface or cooperating-host compromise before further
implementation. No alternative is silently selected here.

Ready-to-paste prompt for the recommended decision:

> I accept safe Windows Eject after copying each batch for the first version.
> Preserve ordinary CLOUDSTICK USB-drive behavior and the existing Google backup
> engine. Propose exactly one small acceptance milestone for this contract,
> separating previous evidence from tests still needed and listing any required
> live-operation approvals. Do not deploy, change networking or credentials,
> restart services, modify storage, or begin secondary features.

No next milestone was executed.
