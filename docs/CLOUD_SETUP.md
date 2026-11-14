# Private cloud setup and acceptance

Cloud adapters and the ready-batch worker are implemented. The first controlled
Google Drive QA backup on the Pi passed real browser authorization, upload and
independent read-back verification. Recurring Google backups and Koofr/Dropbox
real-account acceptance remain pending. Earlier Pi pipeline trials used local
rclone aliases; those results remain separate from the real-account proof.

## Current Google Drive steps on the preview

Sign in as **owner**, open **Backups**, and check the Google Drive card. If
authorization is already saved, enter the intended app-folder label and choose
**Prepare**. Otherwise use **Connect** and Google's private sign-in/consent page
first. Never enter a Google password into the device's dashboard login form.

The preparation watcher is now persistent and the live dashboard permits this
request. A fresh owner action after restoration is still awaiting acceptance.
`Configuration prepared · Test required` means a disabled local configuration
was prepared; it does not mean a cloud folder exists or files were backed up.
The live Test/Enable controls and automatic worker remain off while their
integration is completed. Existing files stay retained.
[Restoration and limits](acceptance/pi-google-prepare-restoration-dev14-2026-11-14.json).

## Prepare privately

Use a dedicated test backup folder/account first. The worker's rclone config must
be a regular file owned by the worker identity with mode 0600, outside every
share, source tree and Git checkout. Back up an existing config privately before
changing it. Rclone's obfuscated values are not a substitute for OS permissions.
Do not send tokens/app passwords in chat, put them in command arguments, or
publish `rclone config show`, `dump`, configuration files or terminal transcripts.
Use an authenticated private terminal and the official interactive setup workflow.
Do not capture the terminal while credentials are being entered.

For **Koofr WebDAV**, configure an rclone `webdav` backend with the HTTPS endpoint
`https://app.koofr.net/dav/Koofr`, your Koofr account email and an app-specific
password generated privately in Koofr. The main Koofr account password is not the
WebDAV credential. Keep TLS certificate verification enabled. See
[Koofr's connection settings](https://koofr.eu/help/koofr_with_webdav/how-do-i-connect-a-service-to-koofr-through-webdav/)
and [rclone WebDAV](https://rclone.org/webdav/).

For **Google Drive**, configure an rclone `drive` backend and authorize it through
Google's browser consent flow. Use a dedicated OAuth client and review the scope
against the intended folder; do not assume `drive.file` grants access to arbitrary
existing folders. Use rclone's documented headless workflow when the Pi has no
browser. Keep the client secret and tokens private; organization policy may
require administrator approval. See [rclone Drive](https://rclone.org/drive/) and
[remote setup](https://rclone.org/remote_setup/).

The dev7 source adds Google Drive and Dropbox **Connect** controls to the
dashboard. They require private registered-app configuration first. The running
protected dashboard is now dev11, with its separate dev14 preparation broker.
Never enter cloud
credentials into the dashboard login/password form. No account authorization is
completed just by selecting a provider name.

## Guided account setup

The intended workflow is **Connect → choose a folder → verify a test file →
explicitly enable backups**. The current browser OAuth foundation implements the
connection and local preparation steps. It reports `authorization_saved_untested` after saving tokens, with
`folder_selected`, `test_verified` and `uploads_enabled` all false. The browser
can now request a disabled local configuration; it cannot enable uploads on the
current device.

The dev9 source adds an operator-only, private Google authorization handoff and
disabled rclone configuration preparation. That original operator-only boundary
is separate from the historical one-file acceptance below. Two processes running as
the separate dashboard and backup identities exchange credentials through a
dedicated inherited descriptor; no credential travels in command arguments or
stdout. The result is `prepared_untested`, with the account, proposed app folder
and uploads still unverified/disabled. No cloud request or token refresh occurs.
See [the exact commands and handoff contract](GOOGLE_HANDOFF.md) before using it.
The synthetic two-identity CLI trial passed on the Pi; it did not import real
credentials or connect the live dashboard to a production backup identity.
Owner-requested preparation subsequently passed on dev10 and has now been
restored with a persistent dev14 broker. The fresh owner flow, live Test/Enable
and recurring production worker remain unfinished.
[Historical handoff evidence](acceptance/pi-google-handoff-dev9-2026-11-13.json).

| Destination | Account connection | Current acceptance |
| --- | --- | --- |
| Google Drive | Google's own sign-in and consent page | Real browser consent and one controlled QA upload/read-back passed; enable wizard and recurring worker pending |
| Dropbox | Dropbox's own sign-in and consent page | OAuth and upload adapter source/tests; real consent/backup pending |
| Koofr | HTTPS WebDAV with a Koofr app password | Adapter implemented; private form and real-account tests pending |
| Synology NAS | Dedicated NAS account, validated HTTPS WebDAV or SFTP endpoint | Connector/setup design; no NAS acceptance yet |
| OneDrive / S3-compatible storage | Provider-specific authorization or scoped credentials | Future connectors |

Google's `drive.file` scope is intentionally limited to app-created or explicitly
selected files/folders. The current source has no Picker or existing-folder
browser; it must not imply access to arbitrary Drive folders. Dropbox's eventual
app registration should use App Folder access when that meets the backup need.
See [Google's scope guidance](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
and [Dropbox authorization](https://docs.dropboxapi.com/dropbox-api/docs/oauth).

### Private OAuth application configuration

Registered provider applications and exact HTTPS callbacks are needed before
real web sign-in. A stable hostname is required for reliable ongoing use. A
temporary tunnel can be used for a bounded test only if the provider accepts
that exact registered callback; its address can change. Register the paths:

- `https://YOUR-DEVICE-HOST/oauth/callback/google_drive`
- `https://YOUR-DEVICE-HOST/oauth/callback/dropbox`

An administrator installs `oauth-clients.json` in the dashboard's private state
directory, owned by its service identity with mode 0600; the directory stays 0700.
Use the [non-secret example](../config/oauth-clients.example.json) as the schema.
Either provider may be omitted. `public_origin` must equal the configured
dashboard HTTPS origin. Client secrets belong only in that private file, outside
Git, web uploads and Samba shares. Registration/organizational approval and the
user's private consent cannot be performed by the test fixtures.

Google's External/Testing mode is suitable for a disposable acceptance test,
not indefinite unattended backup: Drive-scope refresh tokens expire after seven
days in that mode. Production publishing/verification is separate from this
development test and requires owner approval. See
[Google's token-expiration rules](https://developers.google.com/identity/protocols/oauth2).

The owner starts Connect with CSRF protection. Fixed provider endpoints receive
an authorization-code request with S256 PKCE and five-minute one-use state bound
to the initiating owner session. The callback removes code/state from the next
page URL; a short-lived opaque cookie resumes same-origin completion. The Pi
rechecks the live owner session and password version before publishing tokens.
Provider errors and token responses are never reflected into the dashboard.
Token files remain private and are not returned by status APIs.

Each exchange bounds response bytes, socket waits and total caller wait, with
at most two transport threads. Slow transports keep their slot until they exit;
late results cannot publish tokens. Account replacement/revocation and token
refresh are separate work; an existing authorization is never silently replaced.
Restarting the dashboard discards unfinished in-memory authorization flows.

Before accepting a real provider, test browser SameSite behavior, expired/replayed
callbacks, revoked credentials, token refresh and the protected rclone handoff.
Configure every reverse proxy/access log to omit callback query strings; TLS
alone does not keep authorization codes out of an access log. Never capture
provider sign-in, callback URLs or tokens in screenshots or acceptance records.
See [Google's web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server).

### First controlled Google Drive backup — 13 November 2026

On the physical Zero 2 W running dev8, the real browser flow passed the
owner/session-bound PKCE and cookie-return path. A separate, private QA
configuration used the direct rclone `drive` backend to create a unique app-owned
test folder and upload one known `Sample-report.json`. The existing backup engine
recorded the job as verified after an independent download matched the fixture's
SHA-256. The original and private QA configuration remain retained; the source
credentials stayed unchanged. [Sanitized result](acceptance/pi-google-drive-2026-11-13.json).

Approved Wi-Fi was checked before and after; rclone was bound to its Wi-Fi IPv4
address. Wi-Fi supplied cloud Internet; the trial required no Windows Internet
Sharing. This does not establish an egress-firewall guarantee. Firmware reported
`throttled=0x0` afterward. The trial changed no USB/network configuration and
performed no automatic deletion.

This is one controlled proof, not complete automatic setup. The dashboard's
folder/test/enable wizard and persistent background worker remain unfinished;
background uploads stay disabled. Token refresh, outages, large/changed files and
unattended operation still need real-account trials. The application remains
External/Testing with the seven-day refresh-token limitation described above.

### Dropbox upload adapter — dev8 source

The disabled [Dropbox worker example](../config/worker-dropbox.example.json)
selects `dropbox_rclone` and expects a privately configured direct rclone
`dropbox` remote. The adapter uses immutable content-version paths, synchronous
batch commits, one transfer and 8 MiB upload chunks, followed by independently
downloaded SHA-256 verification. Failed verification retains the local source
and queues the failure/retry rather than recording a verified backup.

The smaller chunk bounds that backend's chunk buffer, not total rclone/appliance
memory. Synchronous commits wait for completion; this must not be confused with
rclone's destructive `sync` command, which the adapter never uses. See
[rclone's Dropbox options](https://rclone.org/dropbox/).

Tests exercise actual rclone commands with temporary local aliases and explicitly
report those aliases as backend mismatches in `cloud-check`. They do not establish
Dropbox credentials, real commit behavior, API limits or remote durability.
The dashboard's saved OAuth token is not yet connected to this rclone config.

## Check local configuration without contacting the cloud

```sh
raspi-cloud-sync-pro cloud-check \
  --config /absolute/private/worker.json \
  --rclone /absolute/protected/path/to/rclone
```

This dev5 command uses [rclone listremotes](https://rclone.org/commands/rclone_listremotes/)
to read names/backend types locally. It checks private config and protected
executable paths, strips ambient rclone overrides, disables password prompting,
bounds time/output and discards descriptions/unrelated fields/raw errors.
An encrypted config must be unlocked through the separately reviewed private
credential environment; the probe will not ask for or expose its password.

| Result | Meaning |
| --- | --- |
| `backend_matches` | Configured name has the expected direct `drive`, `webdav` or `dropbox` type |
| `missing_remote` | The selected name was not found |
| `backend_mismatch` | The type differs from the adapter's expected type |
| `alias_backend` | An alias is configured; its target may be local or another remote |
| Sanitized error | Permissions, executable, encrypted config, metadata or process check failed |

Exit 0 means all backend types match; exit 2 means they do not or the probe failed.
Neither result starts a worker, contacts a cloud endpoint, refreshes a token or
changes configuration. A matching WebDAV type does not establish Koofr's hostname,
TLS trust, account authorization, write permission, quota or destination ownership.
A matching Drive type does not establish a valid OAuth token.

## Real-account acceptance sequence

1. Confirm the account/backend, HTTPS endpoint where applicable, dedicated backup
   folder and approved uplink without exposing credentials. Keep the worker off.
2. Prepare disposable empty, Unicode, changed-version and representative large
   files as complete ready batches. Keep local originals and manifests.
3. Enable only that test configuration and run a bounded worker attempt. Confirm
   each recorded `verified` event follows actual remote read-back SHA-256 checking.
   Inspect the test account privately to confirm destination and version paths.
4. Test outage/retry, expired credentials, quota/rate failures and interrupted
   work. Policy-blocked work must spend no attempts; failures retain local data.
5. Repeat independently for each destination. One destination's success must not
   conceal another's failure. Measure Pi memory/temperature and sustained traffic.
6. Record exact artifacts, backend/account acceptance (without credentials),
   network/power conditions and remaining limits before unattended use.

There is no automatic deletion, mirroring of source deletions or silent reset of
exhausted jobs. A verified event records a completed check, not perpetual remote
integrity. Stable public hosting, actual Samba/USB acceptance, first-boot setup and
signed updates remain separate work. See [the worker guide](STAGED_WORKER.md).
