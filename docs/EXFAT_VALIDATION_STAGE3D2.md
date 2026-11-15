# Stage 3D2 — Real Disposable exFAT Validation Report

15 November 2026. Accepted source checkpoint:
`271c6770abc6e6301183acf6b30a77bb31fdbef6` on `development`.

**BLOCKED AT ENVIRONMENT PREFLIGHT — real exFAT validation has not run.**
Required formatting/checking tools are absent. The owner's Stage 3D2 instruction
requires separate approval for package installation and privileged operations.
No workaround, tool download, package installation, sudo, loop setup, mount,
format, image creation or filesystem repair was attempted. This checkpoint
contains documentation only and is not filesystem or migration acceptance.

## 1. Exact observed environment

Read-only observations at **2026-11-15 12:30:46 UTC / 07:30:46 EST**:

| Item | Observation |
| --- | --- |
| Computer | Development computer; not the Pi or Windows host |
| OS | Zorin OS 18.1, Ubuntu `noble` base |
| Kernel / architecture | `7.0.0-42-generic` / `x86_64` |
| Python / effective user | 3.12.3 / UID 1000, unprivileged |
| Branch / HEAD | `development` / accepted checkpoint above |
| Initial working tree | No tracked edits; pre-existing untracked `website/` left untouched |
| Proposed scratch parent | `/tmp`, ext4, writable, mode 1777 |
| Available to ordinary processes | 696,436,600,832 bytes = 648.607 GiB |
| Available inodes | 59,411,830 |
| exFAT kernel support | Already listed in `/proc/filesystems`; module file exists |

Free space exceeds the earlier 29 GiB migration planning envelope. That
observation is not a reservation and must be repeated before actual allocation.
No test image has been allocated, including a sparse image.

The proposed scratch directory is a new randomly named
`/tmp/raspi-stage3d2-<random>/`, created with mode 0700 and owned by the test user.
**It does not yet exist**, so no exact child path or test-device identity can
honestly be recorded. Before later creation, validate its parent, ownership,
permissions and available space. Never accept an external image path as input.

Cleanup is confined to that newly created directory and, only after separate
approval, its positively identified test mounts/loops. Never delete elsewhere,
detach unrelated loops or remove a directory while it is mounted. Interrupted
or corrupt candidates must remain available for review until cleanup is approved.
No cleanup was needed or performed at this checkpoint.

## 2. Tool availability and blocker

Both PATH lookup and standard executable-path inspection found no
`mkfs.exfat` or `fsck.exfat`. `dpkg-query` reports **exfatprogs not-installed**.
`dump.exfat`, exFAT FUSE tools and libguestfs tools are also unavailable.
`/dev/fuse` existing does not provide an installed exFAT filesystem driver/tool.

Installed ancillary tools are util-linux `losetup` **2.39.3** and GNU `parted`
**3.6**. These do not replace an exFAT formatter or consistency checker.
An exFAT userspace-tool version cannot be reported because none is installed.

The existing preparation helper intentionally requires Raspberry Pi Zero 2 W,
root, the expected Pi filesystem and verified offset loops. It was inspected
but **not executed or weakened** to make it run on this development computer.

**Established blocker:** missing required exFAT tools, not insufficient space,
an observed filesystem defect or a demonstrated controller incompatibility.
Whether a privileged rehearsal would succeed remains untested. Do not infer
permission to mount from available kernel support or membership in sudo groups.

## 3. Required geometry, not actual filesystem evidence

The existing Stage 3B contract was reviewed without changing it:

| Field | Required value |
| --- | ---: |
| Image length | 8,589,934,592 bytes (8 GiB) |
| Logical sector size | 512 bytes |
| Partition table | MBR; sole first primary partition, type `0x07` |
| Partition start | LBA 2048 / byte offset 1,048,576 |
| Partition length | 16,775,168 sectors / 8,588,886,016 bytes |
| Partition last LBA | 16,777,215 |
| MBR and exFAT boot signatures | `55 aa` |
| exFAT OEM name / sector shift | `EXFAT   ` / 9 |
| exFAT VolumeLength | 16,775,168 sectors |
| Accepted exFAT PartitionOffset | 0 or 2048 |
| Required clean/media flags | `VolumeFlags & 0x6 == 0` |

These are expected values, not measurements of a newly formatted image.
Real filesystem correctness additionally requires a successful nonrepairing
`fsck.exfat -n`; header validation alone is insufficient.

## 4. Creation, migration, integrity and failure results

| Requested validation | This stage's actual result |
| --- | --- |
| Real 8 GiB image allocation and exFAT formatting | NOT RUN; tools unavailable |
| Actual geometry and nonrepairing fsck | NOT RUN |
| Nested/Unicode names, empty files/directories, larger files | NOT RUN |
| Per-file SHA-256, source retention, repeated-copy no-clobber | NOT RUN; no new hashes exist |
| Insufficient space, wrong size/geometry/identity | No new execution; prior source tests are not real exFAT acceptance |
| Interrupted copy/publication, failed fsck, corrupt metadata | NOT RUN |
| Candidate collisions and changed-candidate rollback refusal | NOT RUN |
| Controller/hold/ownership/low-space regressions | Not rerun after the preflight stop; no source changed |
| Physical Windows/Pi USB acceptance | Outside this isolated stage; NOT RUN |

**New filesystem tests passed: 0; failed: 0; deferred: all real-exFAT cases.**
These are deferred tasks, not unittest skip results. The earlier Stage 3D
982-pass/3-skip result remains historical evidence at the accepted source
checkpoint; it is not a newly executed Stage 3D2 result.

## 5. Controller compatibility and independent review

A separate read-only engineering review confirmed the same missing-tool
blocker and expected geometry. It agreed that the Pi-only helper must not be
bypassed. It did not certify an actual filesystem, copied files or migration.

The existing schema-2 approval must bind the new image's exact device, inode,
size and approved layout. Old 1 GiB approval remains separate. New image
creation or matching hashes alone do not authorize enrolling its identity,
rewriting an ownership journal, releasing maintenance or exporting it over USB.
Stages 3B–3D code and all disabled hardware gates remain unchanged.

## 6. Minimum approval plan and proposed commands — NOT EXECUTED

**Single next action: approve installation of only the official-distribution
`exfatprogs` package on the development computer.** Review the package-manager
preview first; stop if it proposes removals or unrelated upgrades. No package
repository changes, third-party drivers or general system update are proposed.

```bash
# Proposed preview, followed only by a separately approved installation:
apt-get -s --no-install-recommends install exfatprogs
sudo apt-get --no-install-recommends install exfatprogs
```

Installation approval alone does not approve formatting, loop devices or mounts.
After installation, recheck the installed tool versions/help and preflight;
prepare the reviewed, fenced rehearsal before asking for any additional
privileged operations. Do not patch the Pi-only helper's host/root checks.

For a later **separately approved** offset-loop rehearsal, the proposed command
arguments below bind the expected 8 GiB geometry. They are a command plan, not
a runnable installation script: `$stage3d2_image` must first be a freshly created,
fully allocated regular file of exactly 8,589,934,592 bytes inside the new private
directory, with the existing MBR generator's output written and fsynced.
`$stage3d2_loop` must be the single returned loop whose backing device/inode,
offset, size limit and unmounted state have been verified before each operation.
No such variables, image or loop exist yet.

```bash
# NOT AUTHORIZED OR EXECUTED:
sudo losetup --find --show --offset 1048576 --sizelimit 8588886016 -- "$stage3d2_image"
sudo mkfs.exfat -L CLOUDSTICK "$stage3d2_loop"
sudo fsck.exfat -n "$stage3d2_loop"
# After separate, reviewed mount/file-copy acceptance and verified unmount:
sudo losetup --detach "$stage3d2_loop"
```

Mount commands are deliberately not approved or ready to execute. Any later
proposal must use only generated disposable source/destination paths, private
mount isolation, no USB export, safe mount options and verified normal cleanup.
No force/lazy unmount, global loop detach, repair option or physical-device
formatting is permitted. If required privileges or kernel support are absent
at that time, stop again rather than bypassing a check.

## 7. Remaining prerequisites and checkpoint

Real filesystem creation, complete migration/hash tests, failure injection and
skeptical review of their evidence remain pending. Disposable acceptance still
would not establish Windows/Pi compatibility, SD power-loss durability or
authorize a live migration. A fresh live capacity/health preflight, approved
deployment, hardware maintenance-hold acceptance, explicit identity/journal
transition plan and reviewed rollback remain separate prerequisites.

The live Pi, USB image, Wi-Fi, credentials, cloud objects and backup history were
not accessed or modified. The 19-version/95-event baseline remains historical;
no live counts were measured or reset. No source or service was changed.

The Stage 3D2 preflight-documentation commit is the commit containing this report;
its parent is `271c6770abc6e6301183acf6b30a77bb31fdbef6`.
No main merge or release. **Stopped for the package-installation approval.**
