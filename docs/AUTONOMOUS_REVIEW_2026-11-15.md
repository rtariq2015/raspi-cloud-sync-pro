# Raspi Cloud Sync Pro — autonomous development review

Date: 15 November 2026 (America/New_York). Branch: `development`.
Starting application source: `665977bd60e2f9afb6297ba2110b31157eb37f29`.
This session changes documentation and acceptance evidence only. The application
source stays at that tested baseline; the documentation checkpoint is identified
by the Git commit containing this report. No main merge or release is authorized.

## Executive result

**G4F PASS:** the ordinary Windows test computer copied one unique synthetic
1 MiB file to CLOUDSTICK. Windows source and USB read-back SHA-256 matched.
Safe Eject triggered the existing detached capture and automatic USB return.
The existing worker uploaded once using the active Production authorization.
A separate Google download matched the Windows original and staged copy.
There are now **19 verified backup records and 95 events**, including all
previous 18 full job records and 90 full events unchanged.

**Roadmap Stage 2 PARTIAL:** preparation for the existing reporting fix is
complete; its live deployment is **BLOCKED awaiting explicit approval**.
No new functionality was written and no live software was replaced.

## Checkpoint: G4F

- **STAGE:** G4F — final Production backup acceptance.
- **OBJECTIVE:** prove the accepted USB → Safe Eject → capture → Production
  Google Drive → independent SHA-256 workflow.
- **STATUS:** PASS within this one-file, one-host scope.
- **WORK COMPLETED:** actual Windows source creation, file copy, source/USB
  hashing, guarded normal media eject, passive Pi observation, backup/history
  verification, independent cloud download and final Windows retention check.
- **FILES CHANGED:** sanitized acceptance JSON, this cumulative report and the
  current project-status summary. Private helpers and evidence remain outside Git.
- **TESTS EXECUTED:** the physical test above; no full local 898-test rerun.
- **PASS/FAIL:** all final physical acceptance predicates passed.
- **HARDWARE TESTS:** Raspberry Pi Zero 2 W and the existing ordinary Windows 11
  Home test PC. No Siemens or other clinical system was used.
- **EVIDENCE:** [sanitized physical acceptance](acceptance/pi-windows-production-google-g4f-2026-11-15.json).
- **RISKS:** one file does not prove endurance, hospital authorization, scanner
  compatibility, permanent grant lifetime or retention of every historical object.
- **COMMIT:** application `665977b`; documentation checkpoint is this report's commit.
- **NEXT ACTION:** the reporting-fix preparation below; no extra cloud test.

The read-only preflight checked the exact existing Windows USB/volume identity,
healthy exFAT/removable status, approximately 816 MiB free on the 1 GiB image,
about 47 GiB free for local staging, connected built-in Wi-Fi, SSH, healthy
services, no rclone upload, no capture and the same Google account/folder.
The initial worker sample included an unclassified short-lived extra process;
neither sample had rclone or nonverified jobs, and the final sample contained
only the idle main worker. An extra helper alone was not treated as an upload.

The observer recorded loaded → configured empty LUN → USB detached → staging
while detached → loaded/configured host return. The existing controller performed
its previously accepted read-only capture; the observer changed no gadget state.
The Windows eject automation used an administrator token to lock, flush and eject
the exact non-system USB volume. It requested neither forced eject nor power-off.
This is not evidence of no-admin scanner compatibility.

The new job made one attempt and recorded exactly:
`queued → uploading → uploaded → verifying → verified`.
No other new or duplicate jobs appeared. Old full job/event rows were compared,
not just counts. The independent verification streamed one 1,048,576-byte object
from Google and computed SHA-256 separately from the worker's verification.

The first independent verifier stopped because its short-lived access token had
expired after the successful upload. One focused harness correction renewed
access in memory through the existing Production refresh grant. It preserved
the scope, refresh token and operational configuration bytes, then completed
the bounded download. There was no second physical copy, backup upload, consent
flow, service restart or authorization replacement.

A separate skeptical agent reviewed the cross-artifact hashes, sizes, paths,
event order and history invariants and found the scoped acceptance supported.
This was an internal review, not a review by the owner's separate ChatGPT chat.

## Checkpoint: roadmap Stage 2 preparation

- **STAGE:** deploy backup-failure reporting — offline preparation only.
- **OBJECTIVE:** prepare the already committed durable `failed_jobs` counter.
- **STATUS:** PARTIAL; deployment BLOCKED at the explicit approval gate.
- **WORK COMPLETED:** inspected `665977b`, exact installed worker source, launcher,
  report shape and actual dashboard binding; reviewed rollback and compatibility.
- **FILES CHANGED:** this deployment plan and progress documentation only.
- **TESTS EXECUTED:**
  `PYTHONPATH=src:tests .venv/bin/python -m unittest test_staged_worker test_dashboard test_engine`.
- **PASS/FAIL:** **57 tests passed** in the current local run.
- **HARDWARE TESTS:** read-only installation inspection; no live failure injected.
- **EVIDENCE:** source diff, existing regression tests and protected private
  installation observations. G4F's live state remained 19 verified jobs/95 events.
- **RISKS:** an older dashboard reader rejects the new field if later connected;
  avoid changing its report binding as part of this minimal deployment.
- **COMMIT:** reuse `665977bd60e2f9afb6297ba2110b31157eb37f29`.
- **NEXT ACTION:** obtain approval for the narrowly defined worker maintenance.

### Confirmed installation and compatibility

The active worker loads the root-protected fixed source directory through the
existing launcher at:

`/opt/raspi-cloud-sync-pro/qa/mass-storage-worker-dev22/launcher.py`

Its installed `site/raspi_cloud_sync_pro/staged_worker.py` exactly matches the
parent of `665977b` and does not contain the new failure counter. The current
worker report likewise lacks `failed_jobs`: absence means **unknown**, not zero.
Read-only history confirms there are currently no failed jobs.

Observed source hashes for the future comparison:

| Source | SHA-256 |
| --- | --- |
| Installed/pre-fix worker | `b4cd852d32f91ba78e0557696cbfdcfcc1d69da4e8b7d45c117fd46aaa292bf6` |
| Existing fixed worker in `665977b` | `38e4ce0484b23af375fbdcc64eba7fed3e433e0eb44612dd2717ef85a7650679` |

The live source is root-owned, mode `0644`. The launcher adds only this fixed
source directory to the module path; it has no per-file hash manifest to update.
The change is the existing three-line, destination-scoped SQL count, not a retry
or schema migration. Credentials, provider and destination options are unchanged.

The live older dashboard reader does not accept the added field, but its actual
service has no `--worker-state` binding and does not read the core history/report.
Therefore this deployment needs neither a dashboard patch nor dashboard restart.
Do not connect it to this report without a separately reviewed compatible reader.
The Rust reader ignores additional JSON fields and schedules pending work, not
terminal failures; no Rust binary replacement is needed.

### Exact proposed maintenance — not executed

1. Recheck Pi health, host-owned USB state, no capture, no active rclone, idle
   worker, current history and the exact installed source/launcher hashes. Stop
   if any state has changed unexpectedly. Record the current valid baseline;
   never require the historical 18-job count after G4F added job 19.
2. With explicit approval, stop **only**
   `raspi-cloud-mass-storage-backup.service`. Require inactive/dead, `MainPID=0`
   and no remaining worker/rclone processes. Keep the controller running.
3. Create a new root-private `0700` rollback directory with `0600` copies of the
   exact installed worker source, its uid/gid/mode, launcher/unit state and a
   canonical read-only snapshot of current jobs, events and scheduler receipts.
   Record credential/config hashes without printing credentials. Keep all older
   rollback records. No schema, history, staging or credential replacement.
4. Atomically install **only** the reviewed `staged_worker.py` from `665977b`,
   preserving root ownership and mode `0644`; flush the file and parent directory.
   Keep every other module, launcher, executable and configuration unchanged.
5. Start only the backup worker. Require a new stable process and fresh successful
   status with `pending_jobs=0` and `failed_jobs=0` for the present all-verified
   history. Compare every existing job, event and receipt, the unchanged
   destination/config/credential bindings, and the still-host-owned USB state.
   Run the existing failure/idle/reopen regression against the installed module
   with an isolated credential-free temporary fixture. For native process-restart
   evidence, reopen that fixture in a fresh process and confirm its failed count,
   attempts and payload remain unchanged. Keep all fixture state separate from
   live worker data. No new cloud upload or synthetic failure in live history
   is authorized.
6. If startup or validation fails, stop only that worker, confirm its children
   exited, atomically restore the immediately preceding worker source with the
   original owner/mode, restart it and verify fresh health. Never restore a
   history snapshot, rewind jobs, delete staged data or revoke authorization.

The 57 local tests prove a permanent fake-provider failure stays counted after
idle ticks and database reopen, with one attempt, no second upload and unchanged
payload. They also cover destination scoping, retries, legacy/new report parsing,
invalid failure counters and engine history behavior. These are isolated tests,
not a live failed-job acceptance result. The installed change and its fresh
status field must still be accepted after separately authorized maintenance.

The historical full baseline was 898 Python tests, 11 Rust tests and five CI
jobs at the earlier source checkpoint. Those were not rerun locally here.
No cargo executable was available on the current PATH or known earlier tool
locations; no toolchain was installed. The existing Rust code/test was reviewed.
Normal GitHub push checks are separate; inspect their actual result before
claiming current CI success.

## Remaining work and approval boundary

During G4F and Stage 2 offline preparation, no live service was stopped/restarted,
no application source was deployed, no USB or network setting was changed, and
no reboot, resize or formatting occurred. G4E's existing Production
credentials/rollback remain intact. The G4F
verifier made only read-only Drive operations and one in-memory access refresh;
the already-running worker made the one specifically approved new backup.

The 1 GiB storage limit remains. Reliability/endurance, practical storage
capacity and later installation acceptance remain unfinished Version 1 work.
No clinical deployment is authorized; organization/vendor and data-handling
approvals remain prerequisites. Stage 3 and later were not started because
Stage 2 reached a live-deployment approval boundary.

No-eject operation remains unimplemented and unproven. No completed research was
reopened, no experimental implementation was attempted, and no roadmap change
is needed. Preserve Safe Eject as the accepted Version 1 completion contract.

**Exactly one next major task:** approve and perform the minimal reporting-worker
maintenance above, then stop for review. No fresh sign-in, USB cable move,
network change or scanner access is required for this proposed maintenance.

### Ready-to-paste next-stage prompt

> Continue roadmap Stage 2 only. I approve stopping only the idle backup worker,
> backing up its exact installed source/state, atomically deploying only the
> existing three-line reporting fix from `665977b`, restarting that worker and
> read-only live validation plus an isolated credential-free failed-job/restart
> fixture using temporary data, with rollback on failure. Recheck the current Git HEAD,
> live source hashes, USB ownership and actual history baseline first. Preserve
> Production credentials, destination, history, staging, controller and network.
> Do not restart the dashboard, inject live failed jobs, create uploads, resize
> storage or begin Stage 3. Report actual installation, fresh counters, preserved
> history and rollback results, then STOP.
