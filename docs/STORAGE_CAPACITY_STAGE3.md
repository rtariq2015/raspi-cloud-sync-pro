# Stage 3 — storage capacity planning and safe simulation

15 November 2026. Branch: `development`. Accepted starting checkpoint:
`eeb0cc21458da4f28988ccdd6d9f9c200fece1be`.

**Result: planning and disposable simulations complete. Recommend an 8 GiB
replacement image, subject to the prerequisites below. No live migration took
place.** CLOUDSTICK remains the existing 1 GiB development image. Safe Eject
remains required. No Pi service, USB, network, credential or cloud setting was
changed; no file was uploaded or deleted.

## 1. Measured baseline

Read-only Pi observation: **2026-11-15 10:39:35 UTC / 05:39:35 EST**.
Ordinary Windows test-PC volume observation: **10:39:33 UTC**. Values below are
binary units: 1 GiB = 1,073,741,824 bytes; 1 MiB = 1,048,576 bytes.

| Item | Bytes | GiB or MiB |
| --- | ---: | ---: |
| Physical nominal 64 GB microSD | 63,864,569,856 | 59.479 GiB |
| Boot partition | 536,870,912 | 0.500 GiB |
| Root partition | 63,319,310,336 | 58.971 GiB |
| Root filesystem capacity | 62,254,190,592 | 57.979 GiB |
| Root filesystem used | 8,922,038,272 | 8.309 GiB |
| Free including filesystem reserve | 53,332,152,320 | 49.669 GiB |
| **Available to ordinary backup processes** | **50,725,036,032** | **47.241 GiB** |
| Filesystem reserve/unavailable difference | 2,607,116,288 | 2.428 GiB |
| Current image, apparent size | 1,073,741,824 | 1.000 GiB |
| Current image, physically allocated | 1,073,745,920 | 1.000 GiB |
| Windows exFAT filesystem capacity | 1,070,596,096 | 1,021 MiB |
| Windows exFAT free | 854,884,352 | 815.281 MiB |
| Retained published staging, allocated | 536,219,648 | 511.379 MiB |
| Retained published staging, file lengths | 535,857,742 | 511.034 MiB |
| Sum of the 19 verified job payload sizes | 214,963,673 | 205.005 MiB |

The budget uses **47.241 GiB**, never the larger free-including-reserve figure.
Partition capacity, filesystem capacity and usable filesystem space differ.
The future 8 GiB image will likewise provide less than 8 GiB of file payload.

Staging contains **54 retained files across 15 ready batches**, not just the
19 unique verified job payloads. Older captures and retained duplicate payloads
consume real space; job deduplication does not remove their local bytes.
There is no active incomplete capture. The handoff from private capture to
published staging is a same-filesystem rename, not another full payload copy.

The project data tree uses 1,635,086,336 allocated bytes, including its current
image and staging. Worker state uses 69,632 allocated bytes. The selected project
rollback directory uses 172,032 bytes; `/var/log` uses 1,454,080 allocated bytes.
Other root usage is approximately **6.785 GiB**, including the OS, applications,
other retained/user data and filesystem overhead. It is not an OS-only measure.
The package archive cache is about 595 MiB and is already included in other
root usage; no cache deletion is proposed or assumed. These figures overlap
where a child directory is part of its parent; do not add them all together.

At observation, Windows reported healthy removable exFAT CLOUDSTICK. The Pi
confirmed the existing approved image identity, configured host-owned USB,
no local capture mount or loop mapping, active/enabled controller and worker,
zero pending/failed jobs and no rclone process. Full job, event and scheduler
rows matched the protected Stage 2 snapshot: **19 verified versions / 95 events**.
This inspected metadata and status only; it did not read the Windows-owned image
or independently download all historical Google objects.

## 2. Capacity recommendation and retention budget

Use a conservative **additional-space** envelope from the measured free space:

`new image C + one full changed capture C + retained interrupted capture C
 + 4 GiB OS/log/runtime margin + 1 GiB migration workspace = 3C + 5 GiB`.

The old 1 GiB image and existing staging are already in current used space.
They remain retained; charging them again against current free space would
double-count them. The extra 1 GiB is temporary file-level migration workspace,
not a second charge for the old image. The interrupted-capture allowance is a
separate conservative recovery allowance, not a claim that handoff duplicates
every batch. Payload allowances also need small manifest/directory overhead;
the remaining headroom must cover that overhead and future retained versions.

| Proposed image | Image + full capture + interrupted allowance | Fixed margins | Additional envelope | Remaining headroom |
| --- | ---: | ---: | ---: | ---: |
| 4 GiB | 12 GiB | 5 GiB | 17 GiB | 30.241 GiB |
| **8 GiB** | **24 GiB** | **5 GiB** | **29 GiB** | **18.241 GiB** |
| 16 GiB | 48 GiB | 5 GiB | 53 GiB | **5.759 GiB short** |

**Recommend 8 GiB:** it offers substantially more Windows space while retaining
room for a full changed capture, a retained failed capture and recovery margins.
4 GiB remains a reasonable smaller option if actual reports are small; 16 GiB
can physically fit as a file but does not meet this conservative envelope on
the measured card. No claim is made that a larger card is currently required.

**Actual daily QA volume and retention period are UNKNOWN.** The owner should
confirm expected changed bytes/day, largest batch, maximum file count and desired
local retention before physical migration. Synthetic acceptance files do not
measure that workload. As arithmetic examples only, 18.241 GiB corresponds to
about 1,867 / 186 / 37 days at 10 / 100 / 500 MiB of extra retained bytes/day,
before metadata, duplicate retained captures and other growth. These are upper
bounds, not retention guarantees. The USB filesystem can fill before staging
does because originals remain on CLOUDSTICK; cloud success does not free it.

## 3. Existing limits and proposed low-space/power-loss protection

Existing code already preserves originals, hashes changed files, retains
`.incomplete` capture directories on failure and publishes `.ready` only after
successful capture. However, the default staging reserve is only **16 MiB**
(`staging.py`); it is not the proposed appliance operating reserve. A low-space
failure can put the controller into `recovery_required` and leave storage
detached. No automatic USB return or polished low-space alert is promised.

For a future 8 GiB implementation, use these **proposed, not installed** gates:

- Fully allocate and check candidate-image space before any live cutover; do
  not rely on a sparse image's apparent size as allocated capacity.
- Warn before available space falls below `2C + 4 GiB` (20 GiB for C = 8).
- Admit a new capture only if available space covers the estimated changed
  payload, its metadata, one C-sized interrupted-capture allowance and the
  4 GiB operating margin. Recheck while copying because estimates and free space
  can change. Retained incomplete bytes already reduce the measured free space.
- Refuse safely if the budget cannot be satisfied. Preserve source, completed
  versions and incomplete evidence; require an explicit recovery/retention
  decision instead of deleting files or consuming the OS reserve.
- Keep logs bounded within the operating margin and monitor retained-version
  growth. No new logging, cleanup or retention policy was deployed in Stage 3.

Existing capture/handoff limits also remain: **10,000 entries, depth 32 and
bounded capture metadata (1 MiB)**. Increasing GiB does not remove these limits.
Each must be checked against real QA usage before practical deployment.

For power-loss handling, the future migration needs persistent phases, fsynced
files and parent directories, an unexported incomplete candidate, verified
filesystem cleanliness and boot-time reconciliation of actual image identities.
Do not blindly repeat a rename based only on a journal phase. The existing
controller's bad/unknown-power and ownership gates must remain intact. Process
crash simulations below do **not** prove SD/controller durability under physical
power loss. No power interruption, repair or automatic filesystem repair was run.

## 4. Migration choices and identity preservation

| Option | Benefits | Limits and risks | Decision |
| --- | --- | --- | --- |
| Grow the live image in place | Could retain inode and existing filesystem identity | Alters the only original; changes size identity; container extension alone does not grow the partition or exFAT; no validated exFAT growth procedure here | Reject for this migration |
| Clone old image, then grow clone | Keeps original; byte clone initially preserves partition/filesystem bytes | Clone has a different inode; same unvalidated exFAT growth problem and duplicate-volume identity hazards | Not recommended |
| **New separate exFAT image; verified file-level migration** | Original remains intact; explicit larger geometry; can validate before use | Needs clean exclusive source access, verified copy, new identity enrollment and real Windows acceptance | **Safest proposed approach** |

The installed `mass_storage_appliance.py` explicitly requires **exactly 1 GiB**
and an approved `(device, inode, size)` identity. The preparation/trial helpers
also pin 1 GiB. Controller journals and capture manifests bind the image identity.
A new image changes inode; growing changes size. **Changing a file or bypassing
the approval JSON is not a supported migration. A separately reviewed
size-capable controller/creation change is a prerequisite.** None was implemented
or deployed in this stage.

There is a second cutover prerequisite: the existing controller automatically
reattaches after capture, and stopping its service does not detach USB. A
separately reviewed **maintenance hold** must prevent automatic reattachment,
observe clean host release, and safely verify gadget detachment before local
access. Service shutdown alone is insufficient. Never force detachment or clear
a still-loaded LUN to manufacture exclusive ownership. If the reviewed hold
sequence is unavailable, physical migration must stop.

Keep the current original **inode** through a same-filesystem rename after clean
release. A byte-for-byte copy is useful evidence, but cannot by itself satisfy
the old backing identity on rollback. Preserve the approved USB descriptors and
CLOUDSTICK label. A new filesystem normally has a new identity; if retaining an
exFAT serial is desired, first validate the installed tool's supported mechanism
on a disposable filesystem, and never expose both copies together. No exFAT
serial/UUID was read or changed on the live Windows-owned volume in Stage 3.

Upstream exfatprogs documents label and serial tools but supplies no established
grow procedure used by this plan. Increasing a container alone does not update
the exFAT geometry fields. Preserving a label or serial also does not guarantee
the same Windows volume GUID or drive letter.
[exfatprogs tools](https://github.com/exfatprogs/exfatprogs/blob/master/README.md),
[Microsoft exFAT specification](https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification),
[Microsoft volume naming](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-volume).

Keep the **same Google account, exact remote folder, destination key and history
database**, along with existing ready-batch paths. History deduplicates by
destination, relative path and content hash. Preserve existing source-path/hash
inventory through a reviewed identity transition only after all migrated files
match; do not force a journal match or trust an unverified cache. Existing
backup history must never be rewound during image rollback.

## 5. Disposable simulations and results

New offline harness:
`tools/acceptance/storage_capacity/test_capacity_simulation.py`.
It accepts only its generated temporary fixture directories. Images are **4 MiB
opaque regular files with a generated MBR, not real exFAT filesystems**. Pure
partition-layout calculations cover 4/8/16 GiB without allocating those images.
No mounts, device operations, services, network calls or clinical data are used.

| Simulation | Result and exact scope |
| --- | --- |
| 4/8/16 GiB budget | PASS; conservative 47 GiB unit fixture accepts 4/8 and rejects 16 under `3C+5`; measured report uses 47.241 GiB |
| 4/8/16 partition layouts | PASS; generated 512-byte MBR, type 0x07, 1 MiB alignment and sector counts; no exFAT validity claim |
| Exact image clone | PASS; SHA-256/MBR bytes retained, new inode correctly distinguished, original retained |
| Container extension | PASS; disposable file grows 4→8 MiB but partition stays unchanged and old copy receipt no longer matches |
| Insufficient migration reserve | PASS; one byte below threshold refuses before candidate/partial creation |
| Existing candidate or receipt | PASS; candidate bytes/inode and receipt preserved; refuses overwrite |
| Candidate appearing during publication | PASS; atomic no-clobber publication fails; existing candidate and completed partial retained |
| Interrupted copy/new interpreter | PASS; 64 KiB incomplete artifact retained, no completed candidate/receipt; fresh-process artifact reconciliation |
| Process exit after original rename | PASS; retained actual original inode recovered |
| Process exit after candidate publish | PASS; actual files reconciled, candidate retained and original inode recovered |
| Candidate changed after cutover | PASS; blind rollback raises a refusal and retains both images |
| Staging/history continuity | PASS; real temporary staging/history APIs suppress duplicate jobs and retain old rows/payload when content changes |
| Low space during staging | PASS; existing staging API reports failure, owner enters ERROR, incomplete retained, no ready batch, original intact |
| Injected ENOSPC at fsync | PASS; incomplete copied payload retained, no ready publication, source intact |
| Symlink/out-of-root fixture | PASS; rejected before image operations |

**15 simulations passed** with `ResourceWarning` treated as an error.
**73 existing storage, gadget-controller and appliance tests passed.** Total:
**88 focused local tests**, not a rerun of the historical 898-test suite.

```bash
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/storage_capacity -p 'test_*.py' -v
PYTHONPATH=src:tests .venv/bin/python -m unittest test_storage test_gadget_controller test_mass_storage_appliance
```

The existing GitHub Python check step now discovers these simulations. An
independent agent reviewed the harness; its candidate-overwrite finding was
corrected and covered by two tests. The rollback guard was also made explicit.
No failing simulation required iterative repair; all final tests passed.

**Not tested:** actual 8 GiB exFAT creation, fsck, exFAT identity transfer,
file-level copying on a real exFAT candidate, larger-volume Windows enumeration,
metadata/empty-directory preservation on that filesystem, true power-loss
durability, source-controller upgrade or live rollback. Required exFAT formatting
and checking tools were unavailable on the development computer; none were
installed. Child `os._exit` models process death only; the fixture is not a
production durable cutover tool and does not prove directory-fsync guarantees.
Temporary history tests concern fixture rows, not all historical cloud bytes.
No real Windows copy/eject or new cloud acceptance was repeated in Stage 3.

## 6. Safest proposed physical migration

This is a plan, **not executable authorization or an installed migration tool**.

1. First finish separately approved size-capable source and maintenance-hold
   support and validate
   real disposable exFAT creation/check/copy with the installed stable tools.
   Review rollback and the 8 GiB admission budget. Confirm actual report usage.
2. Obtain explicit maintenance approval. Recheck exact Pi/card/image identities,
   free space, stable power, healthy services and the current history/destination.
   If the accepted baseline has changed, inventory it; never discard later work.
3. Invoke the reviewed maintenance-hold sequence, finish Windows writes and
   perform a normal Safe Eject, leaving power attached. Observe capture finish
   without automatic reattachment, then verify clean host release and safe gadget
   detachment. Require no host LUN, no open capture, no loop/mount, idle worker
   and no pending transfers. Stop only specifically approved services. The
   current service-stop command alone cannot establish this state; STOP if the
   required hold/detachment sequence is absent or any ownership check fails.
4. Make protected configuration/journal/inventory backups and a consistent
   history snapshot for comparison. Retain all ready batches and all credentials.
   Record the old image's inode/size and current approval. Do not restore the
   history snapshot later as a rollback mechanism.
5. Under exclusive access only, check the old filesystem read-only and inventory
   **all** source files with relative paths, lengths and SHA-256, plus empty
   directories and relevant timestamps/attributes. Stop on damage or read error;
   do not repair it automatically. Never mount an image still exported to Windows.
6. Build a fully allocated separate 8 GiB candidate, using the reviewed partition
   and exFAT tools on that **regular candidate file only**. Never format a block
   device. Copy from the exclusively owned read-only original; compare every
   file hash and required metadata, then unmount/check the candidate. A manifest
   mismatch, low space or interruption prevents publication; retain all artifacts.
7. Retain the original by same-filesystem rename; use a durable reviewed cutover
   record. Enroll the new identity consistently through the reviewed controller
   mechanism, preserving all history, destination and staged paths. Do not edit
   a journal merely to make a check pass. Never expose old/new images together.
8. Re-enable only approved components and accept larger-volume Windows behavior
   on the ordinary test PC. A new synthetic write/eject/cloud backup requires
   explicit acceptance-test approval. Do not use the Siemens CT workstation.
   Retain the original until the owner accepts the migration and retention plan.

## 7. Rollback and interrupted-migration recovery

- **Before cutover:** keep the old active path/approval unchanged. Retain failed
  candidate, logs and incomplete copies privately; publish no incomplete image.
- **After cutover but before any new host write:** cleanly release the candidate,
  stop only approved maintenance services, retain it, and return the actual
  original inode to its original path. Restore only matching old image approval,
  ownership journal and capture-identity metadata through the reviewed recovery
  mechanism. Verify no loops/mounts/host handles remain before re-export.
- **After a new host write, or if that cannot be excluded:** refuse blind rollback.
  Retain both images. Obtain exclusive access and reconcile/preserve new candidate
  data before returning the old image as the sole Windows drive. A checksum change
  is a warning, not authority to delete or overwrite newer data.
- **After interruption:** reconcile actual retained image identities and complete
  manifests with the durable phase record. If ambiguous, keep USB detached and
  request recovery approval. Do not automatically format, repair or re-export.
- Keep current Google credentials, exact destination, staged files and **current
  history** throughout. Never delete remote objects, roll back the history DB,
  or erase new versions to make counts match an old checkpoint.

## 8. Approval requirements and remaining blockers

Stage 3 authorizes none of these live operations: Windows Eject/copy, stopping
or restarting Pi services, exclusive source mount/check, candidate creation on
the Pi, live identity/journal enrollment, path cutover or rollback. They need
explicit physical-migration authorization. Any filesystem repair, reboot,
power-cut test, retained-data deletion or new cloud test needs its own stated
approval; it is not implicit in a capacity recommendation.

Remaining prerequisites are **reviewed support beyond the hard 1 GiB gate and
a verified maintenance hold/detachment sequence**, real disposable exFAT
validation, actual usage/retention confirmation and a
reviewed low-space admission implementation appropriate for the larger image.
The next minimum implementation stage should close those prerequisites, without
changing the live image. Physical migration must stop if they are missing.

## 9. Ready-to-paste request for a later physical migration

> Raspi Cloud Sync Pro — controlled 8 GiB physical migration.
>
> Use the accepted Stage 3 report and existing Safe Eject implementation.
> First verify that separately reviewed size-capable controller/creation support,
> maintenance hold with verified clean release/detachment, low-space admission
> and actual disposable exFAT tests are complete. If any are
> missing, STOP and report the unmet prerequisite; do not bypass the 1 GiB gate.
>
> After those prerequisites pass, recheck exact card/image identity, available
> space, stable power, the latest verified history and unchanged Google destination.
> Prepare the exact maintenance/rollback commands and STOP for my final approval
> before Windows Eject, service maintenance or live path/configuration changes.
> The proposed change is a separate fully allocated 8 GiB exFAT image with a
> hash-verified file-level copy under proven exclusive access. Retain the actual
> original inode, all credentials, ready batches, current history and remote files.
> Never format a block device or expose two images together. Refuse cutover on
> low space, mismatch or ambiguous ownership. Refuse blind rollback after new host
> writes; preserve both images and reconcile data. Do not rewind history.
>
> Use only the ordinary Windows test PC and synthetic data. Do not change Wi-Fi,
> Google settings, dashboards or other features; do not use a clinical workstation.
> New cloud uploads, filesystem repair, reboot or power-loss tests require separate
> approval. Report actual Windows capacity/compatibility, every preservation check,
> rollback readiness, tests and commit IDs, then STOP.

## Source-only changes and evidence

This commit adds the offline simulation file, one discovery command to the
existing CI workflow, sanitized measurements, this report and progress updates.
Production Python/Rust code and Pi installation are unchanged. The Git commit
containing this report is the Stage 3 source-only checkpoint; its parent accepted
checkpoint is `eeb0cc21458da4f28988ccdd6d9f9c200fece1be`.

[Sanitized measurements and test evidence](acceptance/storage-capacity-stage3-2026-11-15.json).
Private credentials, filenames, local addresses and operational identity values
are excluded. Stage 3 ends here; no live migration or next implementation stage
has been started.

The final read-only Pi observation at **10:53:40 UTC / 05:53:40 EST** confirmed
the same boot, service process identities, image identity/allocation and full
history/scheduler snapshot. Available space was unchanged at 50,725,036,032
bytes; USB remained host-owned, with no capture, pending/failed jobs or rclone.
