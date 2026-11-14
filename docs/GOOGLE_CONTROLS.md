# Google controls and completed-file transfer

These are development library components, tested with synthetic private state
and temporary files. They are not connected to the live dashboard, cloud tester
or background worker yet. No action in these components uploads a file.

## Owner decisions

`google_control.py` records separate Test, Enable and Disable decisions. The
caller must validate CSRF and supply the dashboard's live owner/session guard.
The guard remains held until the decision is durably published. Changing the
owner password, role, current authorization or prepared configuration invalidates
a decision made against the previous state.

Test never enables backups. Enable requires a recent matching successful Test
receipt: a specific app-owned folder, deterministic fixture upload and downloaded
SHA-256 verification, and verified token refresh. The receipt is a private report
from a future trusted tester, not independent cloud verification by this module.
A failed or missing Test cannot become enabled.

The operational binding includes the actual backend, OAuth client, scope and
folder. Mutable token bytes are excluded from this binding so a valid token
refresh does not change the destination. The future executor must parse and
compare all actual configuration settings before each admitted batch; it must
reject extra options rather than constructing a report from expected values.

A completed Enable can outlive its original request deadline. Every batch still
requires a fresh private admission decision and revalidation of the current
owner and source. Disable immediately denies subsequent admissions. A pending
new Test or Enable also denies them. Stopping an already admitted process remains
the coordinator's responsibility; this local module cannot stop a service.

Requests and receipts use private, immutable files and an atomic current pointer.
Framed inherited descriptors carry private coordination data. Public status
reports historical decisions and explicitly leaves current worker/account state
unverified. The browser supplies actions, not filesystem paths, config hashes or
cloud folder identifiers.

## Move completed web uploads between service identities

`batch_transfer.py` bridges a selected completed web upload from the dashboard
identity into backup-owned staging. The coordinator independently selects its
batch identifier and canonical manifest SHA-256. It passes opened private root
directories and anonymous pipe or Unix-stream descriptors to separate children.
The backup identity does not gain access to dashboard account or OAuth files.

Payload bytes stream in bounded chunks, with per-file size and SHA-256 checks.
The exporter rechecks its source descriptors and paths before sending a final
completion frame. The importer requires that frame and EOF, rehashes its disk
copy and flushes files/directories before atomic no-replace publication. The
coordinator must require successful exits from both children before scheduling
the transferred batch. This protocol concerns already completed web uploads; it
does not establish safety for concurrent USB mass-storage reads.

Failed and interrupted copies remain incomplete. Existing published batches are
reused only after independent verification; conflicts are refused. Originals,
partial files and earlier versions are retained. Timeout and manifest-size bounds
limit protocol work. The dashboard's current 100 MiB upload limit is unchanged;
internal transfer bounds are not evidence of large-file Pi acceptance.

## Remaining integration

1. Wire owner forms and private operator commands into these APIs.
2. Implement and accept the real Google tester, including forced token refresh
   and independent downloaded-content verification in a new app-owned folder.
3. Enforce approved Wi-Fi egress independently of a working USB Internet route.
4. Run a coordinator that obtains fresh admission, verifies actual operational
   settings and transfers only selected completed batches.
5. Publish private per-file results to the dashboard without sharing credentials
   or the backup database, then test Disable, outages and restart recovery.

See [private preparation](GOOGLE_HANDOFF.md), [architecture](ARCHITECTURE.md)
and [the worker guide](STAGED_WORKER.md). Automatic cloud uploads remain disabled
until that integration passes real acceptance.
