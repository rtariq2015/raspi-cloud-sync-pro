# Private Google destination preparation

The dev9 source can transfer an existing Google browser authorization between
separate ordinary service identities and prepare a disabled private rclone
configuration. It requires Python 3.9 or later on Linux. The dev10 candidate
adds an owner-requested preparation workflow and a protected local broker,
described below. The running protected dashboard remains dev8 until a separately
accepted upgrade. The dev10 synthetic Pi broker trial now passes; it is separate
from live dashboard deployment and systemd event/sandbox acceptance.

A synthetic CLI transfer has now passed on the physical Pi using two existing
restricted QA identities. The accounts could not read each other's private
state; unsafe source permissions and overwrite attempts were rejected.
[Sanitized Pi evidence](acceptance/pi-google-handoff-dev9-2026-11-13.json).
This did not use real credentials or connect the live dashboard to a production
backup identity. The running dashboard and its authorization files were untouched.
The [earlier one-file dev8 Google Drive proof](CLOUD_SETUP.md#first-controlled-google-drive-backup--13-november-2026)
remains historical evidence for that separate QA configuration, account and job.
It does not verify a new preparation, proposed folder or recurring worker.

## Identities and prerequisites

Use two different non-root identities:

| Process | Example identity | Private state it may open |
| --- | --- | --- |
| Exporter | `raspi-dashboard` | Its existing dashboard state, including `oauth-clients.json` and `oauth-google_drive.json` |
| Importer | `raspi-backup` | Its own existing backup state; it never opens the dashboard state |

Each state directory must be an absolute path, owned by its invoking identity
with mode **0700**. Private source and generated files must be regular files
owned by the relevant identity with mode **0600**. Descriptor-based traversal
rejects symlinks and untrusted writable ancestors. Both APIs reject root and
processes whose real and effective UIDs differ. They do not create users, change
ownership, relax permissions or install services.

Ancestor directories must also permit read and traversal by the invoking
identity because validation opens directory descriptors read-only. A trusted
root-owned 0755 container can hold the separate 0700 state directories; their
contents remain private. A root-owned execute-only container such as 0711 does
not satisfy this implementation's directory-open requirements. Do not widen a
private state directory to resolve an ancestor-access error.

The dashboard must already hold a privately saved browser grant for the exact
registered HTTPS origin. Export validates the source files, origin, Google
client/grant fingerprint binding, Bearer token fields and exactly the
`https://www.googleapis.com/auth/drive.file` scope. Files changed during reading
are rejected. Tokens and client values belong outside Git, shares, logs, command
arguments and terminal captures. Do not read or display their contents to check
whether preparation worked.

## Dev9 operator commands

These are the exact CLI forms. They are **child-process invocations for a trusted
operator**, not commands to run alone or join with a shell pipeline. The example
paths and HTTPS origin must match the reviewed deployment. Descriptor numbers
3 and 4 below are examples that the operator has already connected and inherited.

Exporter, launched as `raspi-dashboard` with only its dedicated write endpoint
available as descriptor 3:

```sh
raspi-cloud-sync-pro google-export-authorization \
  --state /var/lib/raspi-cloud-sync-pro/dashboard \
  --public-origin https://YOUR-DEVICE-HOST \
  --output-fd 3
```

Importer, launched concurrently as `raspi-backup` with only its dedicated read
endpoint available as descriptor 4:

```sh
raspi-cloud-sync-pro google-prepare-destination \
  --state /var/lib/raspi-cloud-sync-pro/smb-state \
  --input-fd 4 \
  --folder-name RaspiCloudSyncPro
```

The trusted operator creates an anonymous pipe, passes its write end only to the
exporter and its read end only to the importer, and closes every unused copy in
the parent and children. It preserves only the required endpoint across process
execution, selects the separate UIDs and waits for both exit results. Privileged
orchestration, if needed to select service identities, stays outside these APIs;
the exporter and importer themselves run unprivileged. No installed orchestration
or browser endpoint is provided by these two commands.

Stdout carries only a sanitized operation report. It is **not** the credential
channel. Stdin/stdout/stderr and duplicate descriptors referring to them are
rejected even when their descriptor number is greater than 2. Regular files,
named FIFOs, terminals, network sockets and unconnected sockets are rejected.
An exclusively owned, already connected Unix `SOCK_STREAM` endpoint is the
alternative to an anonymous pipe; this code does not open a connection.

## Descriptor and framing contract

An accepted descriptor is consumed and closed on success or failure. Its
original status flags are restored before closing; an invalid channel is not
consumed. The caller must ensure exclusive endpoint ownership and close unused
aliases. Numeric descriptor validation alone cannot establish that no other
process holds a copy.

The exporter sends one four-byte unsigned big-endian length followed by one
strict JSON bundle of 1–65,536 bytes. Duplicate JSON keys and invalid fields are
rejected. The importer requires the complete frame **and EOF**; extra bytes,
concatenated frames, truncation and an open unused writer fail. Both APIs use a
single monotonic handoff deadline, five seconds by default. Python callers may
choose a positive timeout of at most 30 seconds; the CLI uses the default. Run
the pair concurrently, since a sequential exporter can fill the pipe before an
importer starts. Neither command prints payloads or raw credential errors.

## Prepared files and failure recovery

The importer writes under its existing private state:

```text
google-drive.prepared/          mode 0700, backup-owned
  rclone.conf                  mode 0600, backup-owned
  preparation.json             mode 0600, backup-owned
```

`rclone.conf` defines the fixed **`rcsp_google`** remote with the direct **`drive`**
backend, limited `drive.file` scope, the bound client and saved authorization.
The proposed app-folder name is stored in the private manifest; preparation does
not create, select or prove that folder exists. It grants no access to arbitrary
existing Drive folders. Folder names are limited to a short single label of
letters, digits, spaces, underscores, periods and hyphens, beginning and ending
with a letter or digit.

An expired access token is valid preparation input when its saved refresh grant
is structurally valid. Preparation makes no attempt to refresh it or prove the
grant still works. Future refresh needs a controlled real-provider test; Google's
External/Testing seven-day refresh-token limitation still applies. App publishing
or verification is separate work requiring owner approval.

Files and directories are flushed before publication through Linux
`renameat2(RENAME_NOREPLACE)`. An existing `google-drive.prepared` is never
replaced, including a competing publication. Systems without that primitive fail
closed. Failures or interruptions may retain private
`.google-drive-<random>.incomplete` directories, and a publication followed by a
later error may already have left the final directory. Do not assume an error
means no files exist or blindly repeat the operation. A trusted operator must
inspect only metadata first and retain or archive incomplete/final artifacts
privately before planning explicit recovery. No automatic cleanup removes them.

The private manifest records hashes of the canonical exported bundle, including
its source origin, and the generated rclone configuration. These are private
bindings for future validation, not public dashboard data. A later grant
replacement, origin/client change, token refresh or config edit needs fresh
validation. The dev9 operator commands do not supply a dashboard status bridge.
The dev10 candidate adds a historical receipt bridge, without a current worker
readiness check, as described below.

## What success means

The exporter returns `authorization_exported: true` with `uploads_enabled: false`.
The importer returns only this non-secret projection:

```json
{
  "schema_version": 1,
  "provider": "google_drive",
  "preparation_state": "prepared_untested",
  "enabled": false,
  "test_required": true,
  "account_verified": false,
  "folder_exists_verified": false,
  "automatic_deletion": false,
  "approved_uplinks": ["wifi"],
  "allow_metered": false
}
```

CLI exit 0 reports that operation completed; exit 2 is a sanitized preparation
failure. The projection is historical, not a heartbeat or an active readiness
claim. It excludes credentials, account identifiers, origin, fingerprints,
remote name and folder name. Directory existence or a prior success report must
not make the dashboard show a currently usable destination.

Neither command invokes rclone, contacts Google or any cloud API, refreshes a
token, tests an account, creates a folder, uploads a file, changes networking,
starts a service or enables a worker. The Wi-Fi/metered values record intended
disabled policy; they establish no network or egress-firewall guarantee. No
automatic deletion occurs. Source authorization files remain unchanged, and no
backup identity gains dashboard account, authentication or web-inbox access.

Before unattended use, a later increment must check the current worker
configuration, privately test refresh and the proposed app folder, perform an
independent read-back check, and provide explicit enablement and recovery. The
[cloud acceptance sequence](CLOUD_SETUP.md#real-account-acceptance-sequence)
remains necessary.


## Dev10 candidate: owner preparation requests

The candidate adds `google_setup.py`, the Google preparation control in the
Backups page, and `google_broker.py`. This is local preparation of a disabled
configuration. It does not add a cloud test, folder creation, upload or worker
enablement. Dev9's accepted synthetic handoff and the earlier dev8 cloud job
remain historical evidence for their own configurations. The dev10 native
synthetic HTTP-handler/root-broker trial and interrupted-receipt recovery passed;
see its [sanitized receipt](acceptance/pi-google-setup-dev10-2026-11-13.json).
The live dev8 dashboard remains unchanged until its own accepted upgrade. The
trial does not establish actual systemd path delivery, sandbox operation or a
real-account handoff into production backup state.

The dashboard defaults to refusing preparation requests. The explicit
`dashboard --google-setup-requests` option allows the Google owner endpoint only
after the separate setup service is deliberately connected. That flag is a
configuration choice, not evidence that a service is running. The provided
dashboard service template does not add it. A signed-in owner submits only a
constrained `folder_name` through `POST /api/destinations/google_drive/prepare`.
The endpoint checks CSRF and uses the live owner/session commit guard. A viewer
cannot request preparation. Provider tokens, paths, commands, account identifiers
and enablement options are not accepted from this endpoint.

The request API rechecks the current owner role and password credential tag and
validates the saved Google source. It writes under dashboard-owned state:

```text
google-setup/                       mode 0700, dashboard-owned
  workflow.lock                     mode 0600
  current.json                      mode 0600, atomically replaced
  <32-lowercase-hex>.request.json    mode 0600, immutable request record
  <32-lowercase-hex>.receipt.json    mode 0600, immutable historical receipt
  .<random>.incomplete              retained private failed writes, if any
```

`current.json` contains exactly `schema_version: 1` and `request_id`; no owner or
source details are needed by the root coordinator to select intent. Request
records retain the owner name, private credential tag, proposed label, times and
canonical source binding. They never store a session ID, password, token or
client secret. The canonical authorization hash privately covers the normalized
origin, full client ID and secret, and complete validated grant. Request and
receipt files are flushed and published without replacement. The current pointer
is atomically replaced and its directory flushed. Prior records and failed
partial files remain private; failure after publication can leave a published
record even when the operation reports failure.

A source request expires after **ten minutes**. Export and receipt recording
both require the current, unexpired request, the same owner role and credential
tag, and the same source binding. A password change or owner revocation while
processing prevents the old request from exporting or recording a result. A
changed origin, client secret, grant or scope also invalidates it. A new guarded
owner request can replace expired or stale intent while retaining its records.
An identical live request is idempotent and keeps the same pointer; repeating it
does not emit another path change or schedule another attempt. Locks use the
order `auth.lock` then `workflow.lock`; request creation uses the caller's auth
commit guard and does not acquire that lock again.

## Protected broker and separate identities

The candidate broker is a bounded root coordinator for one preparation attempt.
It requires root real/effective UIDs, Linux aarch64, the exact reviewed Raspberry
Pi Zero 2 W model, a protected enabled configuration, a separate approval file,
and protected installed code/interpreter paths. It rejects service identities
with login shells or unlocked local password entries, identical source/backup
UIDs or GIDs, unsafe state roots and concurrent broker execution. Identity checks
read protected local account databases; they do not use a network identity
lookup.

The reviewed example split is:

| Role | Identity and state | Responsibility |
| --- | --- | --- |
| Dashboard/exporter/recorder | `raspi-dashboard`; `/var/lib/raspi-cloud-sync-pro/dashboard`, mode 0700 | Own authentication and Google grant; revalidate intent; export credentials and store private receipt |
| Importer/inspector | `raspi-backup`; `/var/lib/raspi-cloud-sync-pro/google-setup-backup`, mode 0700 | Own only per-request disabled preparations and inspect their configuration |
| Coordinator | root; protected release/config/approval/lock | Read the bounded current pointer and private metadata/receipt; launch constrained children under the two ordinary identities |

The root coordinator does not read the dashboard grant or credential pipe. It
passes the authorization read endpoint only to the backup importer and discards
child stdout/stderr. Root remains a trusted privileged boundary, not an identity
that is technically unable to read other users' files. Children drop all real,
effective and saved UIDs/GIDs to their service identity, clear supplementary
groups and capabilities, disable core dumps and set no-new-privileges before
executing the protected interpreter. They use isolated Python, a fixed argument
array, a restricted environment and only the dedicated inherited descriptors.
No shell, rclone, provider connection, networking change or worker start occurs.

For each current request, the source exporter validates one in-memory Google
snapshot and sends private metadata **before** authorization. The exact metadata
fields are `schema_version`, `provider`, `request_id`, `folder_name`,
`authorization_sha256` and `rclone_config_sha256`. The two hashes come from that
same snapshot: the canonical authorization bundle and the expected rendered
rclone configuration. The exporter closes the metadata endpoint after its frame
so the parent can require EOF before starting the credential consumer. Credentials
then flow directly to the ordinary backup importer.

The importer creates its request directory only after the UID drop, using the
inherited, validated backup-root directory descriptor. Its layout is:

```text
/var/lib/raspi-cloud-sync-pro/google-setup-backup/    mode 0700, raspi-backup
  <request-id>/                                    mode 0700, raspi-backup
    google-drive.prepared/                        mode 0700, raspi-backup
      rclone.conf                                 mode 0600
      preparation.json                            mode 0600
    .google-drive-<random>.incomplete/              retained on some failures
```

The existing dev9 no-replace importer prepares the files. It saves no extra copy
of the grant bundle. The backup inspector validates the exact direct-drive
configuration and disabled manifest, rereads both files to detect changes, and
reports the actual configuration SHA-256 privately. The broker compares both
hashes and the request/folder fields with source metadata. The source recorder
then independently reloads the current owner and source, renders the expected
configuration again, and rejects any receipt hash mismatch. Altering a valid
backup-owned configuration and recomputing its backup-owned manifest hash cannot
satisfy these independent source checks while retaining an old authorization
hash.

The exact private receipt fields are `schema_version`, `provider`, `request_id`,
`authorization_sha256`, `rclone_config_sha256`, `folder_name`,
`configuration_matches`, `prepared_at`, `enabled`, `test_required`,
`account_verified`, `folder_exists_verified` and `automatic_deletion`. It must
report a finite preparation time, `configuration_matches: true`,
`test_required: true`, and all four enablement/verification/deletion flags false.
The source requires a still-live request after receiving the complete receipt.
An identical existing receipt is idempotent; a different receipt never replaces
it.

The child commands are `google-setup-export`, `google-setup-import`,
`google-setup-receipt` and `google-setup-record`; they are broker plumbing, not
browser-executable commands or standalone shell pipelines. Metadata,
authorization and receipts use separate consumed channels with the same
anonymous-pipe/connected-Unix-stream restrictions described above. Stdio and its
aliases, aliases between the two export channels, named FIFOs, regular files,
terminals and network sockets are refused. The importer additionally consumes a
private directory anchor; it must refer to the configured backup root rather
than a stream. Accepted endpoints are closed on failure as well as success;
invalid endpoints remain caller-owned.

Frames and JSON are bounded and reject duplicate keys, excessive nesting,
truncation, trailing bytes and missing EOF. Authorization is at most 65,536
bytes; the coordinator accepts at most 4,096 bytes of metadata or receipt and a
256-byte current pointer. Export/receipt channels use five-second deadlines;
the coordinator has a twenty-second overall deadline. Public CLI reports omit
payloads, paths, labels, identifiers and hashes. Exit 0 can report a disabled
broker; exit 2 reports a failed operation. Neither exit code establishes cloud
access or runtime readiness.

## Historical dashboard status

`GET /api/destinations` includes a safe Google preparation projection. Its state
and age are useful for following an owner request, while all runtime claims stay
conservative:

```json
{
  "schema_version": 1,
  "provider": "google_drive",
  "preparation_state": "prepared_untested",
  "age_seconds": 12.0,
  "historical": true,
  "current_worker_state_checked": false,
  "enabled": false,
  "test_required": true,
  "account_verified": false,
  "folder_exists_verified": false,
  "automatic_deletion": false
}
```

The states are `not_requested`, `requested`, `expired`, `prepared_untested`,
`stale_source`, `stale_receipt`, `invalid` and `unavailable`. Pending owner/source
changes produce `stale_source`; a completed historical receipt can become
`stale_receipt` when its current owner/source or expected configuration binding
no longer matches. Invalid private records and unavailable validation do not
become success. The projection contains no origin, request ID, hashes, account
or client identifiers, folder label, path or credentials.

A completed receipt remains historical beyond the request's ten-minute window.
A later password change does not erase that history or authorize new actions.
Status validates the stored report against the current source but **does not
read the current backup-owned configuration**. A later worker-side edit,
deletion or failure therefore cannot be inferred from this status. The UI says
“Configuration prepared · Test required” and explains that the current worker
has not been checked. Account access, proposed-folder existence, refresh,
read-back verification and explicit activation still require separate work.

## Uninstalled templates and deliberate recovery

Reviewable templates are provided, with no installation script or automatic
startup:

- [Disabled broker configuration](../config/google-setup-broker.example.json).
- [One-shot service](../deploy/systemd/raspi-cloud-google-setup.service).
- [New-request path watcher](../deploy/systemd/raspi-cloud-google-setup.path).

The example pins the actual interpreter file at
`/opt/raspi-cloud-sync-pro/releases/0.1.0.dev10/venv/bin/python3`. A release venv
must be created with `--copies`; a symlinked interpreter or a `current` symlink in
this protected path is rejected. The interpreter, installed package and their
ancestors must remain root-owned and not writable by either service identity.
The source and backup leaves stay separately owned, mode 0700, under trusted
root-owned ancestors. The backup leaf is the dedicated
`google-setup-backup` state, not existing production worker or Samba state.

The installed config destination is
`/etc/raspi-cloud-sync-pro/google-setup-broker.json`. The example remains
`enabled: false`; the broker returns disabled before hardware/state/lock/process
operations. Deliberate activation additionally requires the root-owned mode-0600
`/etc/raspi-cloud-sync-pro/GOOGLE_SETUP_APPROVED` file containing exactly:

```json
{"schema_version": 1, "google_setup_broker_approved": true}
```

The config and approval path ancestors are protected and non-symlinked; config
must be a root-owned regular file without group/other writes. The marker's
presence satisfies the systemd condition, while the broker separately validates
its exact contents. The service is root, one-shot, has no restart policy, uses
`PrivateNetwork=yes`, restricts address families to Unix, and bounds runtime and
capabilities. It does not change host networking. Its root-owned mode-0700
`/run/raspi-cloud-google-setup` runtime directory is preserved across exits so
the lock inode remains stable. Both templates omit an `[Install]` section.

The watcher uses `PathChanged` on the exact
`dashboard/google-setup/current.json` pointer. It observes future pointer changes;
starting it does not execute an already-pending request, and it has no
`PathExists` retrigger loop. An old pending request requires an explicit reviewed
one-shot service start while the source intent is still live. A new owner request
can replace expired intent but never removes previous files.

If a run published configuration but failed before recording its receipt, a
manual retry of the same current, unexpired request may inspect that existing
per-request preparation and record it only when every source/configuration
binding matches. Mismatches fail closed; no existing configuration is replaced.
An expired request needs a fresh owner request and receives a new target ID.
Old completed and incomplete directories remain for private review; they are not
automatically migrated, deleted or enabled. The native Pi receipt covers direct
broker execution and synthetic HTTP handlers; path events and the systemd
sandbox still require their own acceptance before live activation.
