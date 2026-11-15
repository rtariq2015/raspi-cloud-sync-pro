# Stage 3D2 — Real exFAT Rehearsal Acceptance Report

**PASS — approved disposable development-host rehearsal only.**
Accepted parent: `6c7414e48119ede2bf6c39307b05b867e771fe6d`, branch `development`.
No Raspberry Pi, live CLOUDSTICK, Windows USB, network, OAuth, cloud object,
service or backup-history operation was performed. The live image remains 1 GiB.

## 1. Environment and actual execution

Run: **15 November 2026, 08:06:58–08:07:07 EST**
(13:06:58–13:07:07 UTC), following private owner sudo authentication.

- Zorin OS 18.1 / Ubuntu noble, x86_64, kernel `7.0.0-42-generic`.
- Official `exfatprogs` package `1.2.2-1build1`; tools report `1.2.2`.
- `losetup` and `mount`: util-linux `2.39.3`.
- Newly created private scratch: `/tmp/raspi-stage3d2-y_35xbka/`.
- Before allocation: 696,397,299,712 bytes available to ordinary processes.
- Four new, fully allocated regular files: source, destination, corruption and
  low-space. Each has exactly **8,589,934,592 bytes**; physical allocation was
  8,589,938,688 bytes each. No existing image or physical disk was used.

Executed entry point:

```bash
sudo /usr/bin/unshare --mount --propagation private /usr/bin/python3 -I -B /home/home/raspi-cloud-sync-pro/tools/acceptance/storage_capacity/exfat_rehearsal.py --approved-stage3d2-only
```

This is an approved-run record, not authorization to rerun it. The runner accepts
no image, device or scratch-path argument. It refuses other OS/architecture profiles, unprivileged
execution, nonprivate mount namespaces, insufficient capacity and missing tools.
It does not weaken or execute the Pi-only preparation helper; it reuses only its
pure MBR generator and the existing source validation/capture components.

Every loop was bound to a fresh image's device/inode/size, backing path,
partition offset, size limit, read-only mode and 512-byte sector size. Formatting,
checking, mounting and cleanup were preceded by mapping verification, with
unexpected mounts in PID1's namespace refused. No force/lazy unmount or broad
loop detach exists in the runner.

## 2. Exact commands and filesystem geometry

The [unaltered run evidence](acceptance/exfat-stage3d2-2026-11-15.json) records
all 126 tool invocations and results, including read-only mapping checks.
Unrelated loop inventory output is omitted. It contains synthetic names/hashes
only and no credentials or real backup records. The raw evidence SHA-256 is
`f8e560acd0167c8ce9b55f38ad5a98a895dcfa1f4a791b17ee680b4c70b8a96e`.

Representative exact calls from this run:

```bash
/usr/sbin/losetup --find --show --nooverlap --offset 1048576 --sizelimit 8588886016 --sector-size 512 -- /tmp/raspi-stage3d2-y_35xbka/source.img
/usr/sbin/mkfs.exfat -L CLOUDSTICK /dev/loop1
/usr/sbin/fsck.exfat -n /dev/loop1
/usr/sbin/dump.exfat /dev/loop1
/usr/bin/mount -t exfat -o rw,nosuid,nodev,noexec,uid=0,gid=0,fmask=0077,dmask=0077 /dev/loop1 /tmp/raspi-stage3d2-y_35xbka/source-mount-vajbxji0
/usr/bin/umount /tmp/raspi-stage3d2-y_35xbka/source-mount-vajbxji0
/usr/sbin/losetup --detach /dev/loop1
```

These device numbers are **historical only**. Never reuse one without fresh
positive identity verification. The source's later mapping/mount was explicitly
read-only; the JSON includes all corresponding commands and unique mount paths.

| Observed field, initially identical across all four filesystems | Value |
| --- | ---: |
| Image length | 8,589,934,592 bytes |
| MBR partition | sole primary #1, type `0x07` |
| Partition start | sector 2048 / byte 1,048,576 |
| Partition length / exFAT VolumeLength | 16,775,168 sectors |
| Logical sector | 512 bytes, shift 9 |
| exFAT PartitionOffset | 0, accepted by the existing offset-loop contract |
| FAT offset / length | 2048 / 2048 sectors within the partition |
| Cluster heap offset | 4096 sectors within the partition |
| Cluster size / count | 32,768 bytes / 262,048 |
| Root cluster | 4 |
| Label, verified from raw root-directory entry | `CLOUDSTICK` |
| Initial clean/media flags | 0 |

All four real formats exited **0**. Existing Stage 3B header and layout
validation accepted the real structures. **Twelve clean, nonrepairing fsck
invocations exited 0**, including after ordinary copying, interrupted copying
and filling the separate low-space volume. A thirteenth fsck was the deliberate
corruption refusal below. No repair command was run.

## 3. File migration and SHA-256 evidence

Five files totaling **68,157,504 bytes** were copied into the source exFAT image,
then migrated from its read-only mount to the separate writable destination.
Every destination size and SHA-256 matched the retained synthetic source.
The original source tree, exFAT source tree and successful destination tree
were compared again after the failure scenarios.

| Synthetic file | Bytes | SHA-256 |
| --- | ---: | --- |
| `empty.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `large-64-MiB.bin` | 67,108,864 | `103f23a15401a701b73587902f16e3b5b3bf38a039d5c94b675a9a8e84dbd5b5` |
| `nested/QA Δ/报告-α.txt` | 37 | `1a6fc22b061a6bd5fc95c9532d3d68df45aaea0d5bae0321652dd85c81ce7d5b` |
| `nested/with spaces.csv` | 27 | `08a1cb06d7d9947306418799296c483730563d4175d366c3e20e8e14596df1c1` |
| `one-MiB.bin` | 1,048,576 | `4e29ad18ab9f42d7c233500771a39d7c852b200baf328fd00fbbe3fecea1eb56` |

The three directory paths also matched, including `empty-directory` and
`nested/QA Δ`. This verifies directory/name/content preservation by the
**rehearsal copy helper**. It does not establish POSIX ownership/ACL/timestamp or
all Windows metadata preservation. Source identity/size/mtime/ctime changes
during copying are refused; an injected metadata change is covered by a guard test.

## 4. Failure results and controller compatibility

| Scenario | Actual result and boundary |
| --- | --- |
| Repeat copy / existing candidate | Refused before overwrite; verified destination retained |
| Process exits during actual exFAT copying | Child exits 73 after a flushed/fsynced chunk; partial retained; original intact |
| Retrying the interrupted candidate path | Refused rather than overwriting partial evidence |
| Candidate changes after copy | Extra synthetic file makes manifest comparison refuse blind rollback; both trees retained |
| Wrong image inode / 4 GiB capacity | Existing approval validator refuses disposable metadata; no real enrollment |
| Unexpected MBR geometry / dirty flags | Existing validator refuses in-memory mutations of the real read-back headers |
| Corrupt separate filesystem | Main and backup boot OEM bytes deliberately invalidated while detached; read-only `fsck -n` exits **8**, reporting `Bad fs_name in boot sector` |
| Nonrepair confirmation | Corrupt 24-sector boot-region SHA-256 unchanged before/after; loop was read-only; no repair |
| Real full-volume behavior | Separate 8 GiB exFAT filled to **ENOSPC / errno 28**, after 8,586,690,560 bytes; free bytes become 0 |
| Admission on full destination | Copy refuses before creating its candidate; source remains intact; normal unmount and fsck remain clean |
| Stage 3D real capture | Existing `stage_changed_snapshot` + `CaptureSpacePolicy` captures the actual RO exFAT source into private **ext4** staging; all five file hashes match |

The full-volume fill is bounded by its positively verified disposable loop
filesystem. It does not fill the host ext4 filesystem. Actual ext4 staging
exhaustion/fsync ENOSPC remains simulated in existing regressions, not physically
accepted here. The policy's reserves and inode checks were not weakened.

Existing staging captures regular files and does **not** preserve empty
directories; the ordinary migration-copy fixture separately preserved them.
The owner-state evidence inside this isolated capture test is modeled: there
was no USB export, genuine Windows Eject or physical gadget ownership transition.

## 5. Tests, review and CI

- **17 grouped rehearsal checks passed**, with zero unexpected failures.
  Some checks are logical refusals applied to real-image metadata, not 17
  separate physical filesystem tests.
- **301 distinct focused automated tests passed**, zero failures/skips:
  230 controller/storage/ownership/maintenance/engine tests; 35 storage tests
  (15 existing simulations + 20 new hardware-free guard tests); 36 USB operator
  tests. The 20 new tests are included in 35, not added twice.
- Independent pre-execution and actual-evidence reviews passed. The latter
  independently checked current mount/loop inventories and the saved results.
- No production application source changed. The broader existing GitHub workflow
  runs on push; exact final CI status and commit are reported with this checkpoint.
  CI runs guard/simulation tests, **not** the privileged real-filesystem runner.

Commands used for the focused automated suites:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_capture_space test_capture_space_controller test_storage test_gadget_controller test_linux_gadget test_mass_storage_appliance test_mass_storage_capacity test_mass_storage_image_prepare test_maintenance_hold test_maintenance_appliance test_engine test_staged_worker
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/storage_capacity -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/mass_storage -p 'test_*.py'
```

## 6. Cleanup, retained evidence and limits

**Four mounts / four normal unmounts, nine loop attachments / nine detachments.**
The root runner verified no related mappings remained. Subsequent read-only
checks, including the independent review, found no matching mounts in self or
PID1's namespace and no visible related loop mappings. Image identities still
match. See [postcheck evidence](acceptance/exfat-stage3d2-postcheck-2026-11-15.json).

All four disposable images remain **unmounted and detached**, retained with the
original synthetic files, partial copy and private staging as evidence. The
images alone occupy approximately **32 GiB**, plus synthetic files/staging.
The scratch directory is private to the owner after completion; individual
root-created fixtures retain their original restrictive permissions. Nothing
outside this uniquely created directory was deleted. Do not manually attach,
mount or erase these fixtures as part of another task.

The helper is test-only verified copying, **not an atomic production migration
or cutover tool**. Manifest-based refusal does not prove actual inode/journal
rollback. Prior opaque-file phase/crash simulations remain separate evidence.
This source-to-destination rehearsal used two new 8 GiB filesystems; it did not
clone the live 1 GiB image or physically enroll an 8 GiB replacement.

Remaining prerequisites include a fresh live capacity/health preflight, approved
deployment of the capacity/hold/admission source, actual Pi maintenance-hold
acceptance, a reviewed identity/journal transition and rollback procedure, then
separately approved Windows/Pi acceptance. No SD power-loss durability, new cloud
backup or live history/credential measurement is claimed. The 19-version/95-event
checkpoint remains historical and was not read, reset or changed.

## 7. Checkpoint and one next action

Changes are limited to the disposable runner, its guard tests and documentation/
synthetic acceptance evidence. The commit containing this report is the Stage
3D2 checkpoint. No main merge or release.

**Single recommended next action: review and approve a controlled Pi deployment
and maintenance-hold acceptance plan while preserving the working 1 GiB image.**
Do not deploy or begin live 8 GiB migration from this report. Stage 3D2 stops here.
