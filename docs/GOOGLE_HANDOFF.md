# Private Google destination preparation

The dev9 source can transfer an existing Google browser authorization between
separate ordinary service identities and prepare a disabled private rclone
configuration. It requires Python 3.9 or later on Linux. This is an operator
boundary for the future account wizard; the running protected dashboard remains
dev8 until a separately accepted upgrade.

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

## The two commands

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
validation. This increment has no current-readiness reader or sanitized status
bridge between the identities.

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

A later increment must revalidate the current source/config binding, privately
test refresh and the proposed app folder, perform an independent read-back check,
and provide an explicit enable/recovery workflow before unattended use. The
[cloud acceptance sequence](CLOUD_SETUP.md#real-account-acceptance-sequence)
remains necessary.
