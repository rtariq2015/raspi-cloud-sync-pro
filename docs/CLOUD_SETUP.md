# Private cloud setup and acceptance

Cloud adapters and the ready-batch worker are implemented. Real Google Drive and
Koofr account acceptance is still pending. The Pi pipeline trials used two local
rclone aliases; those results must never be presented as cloud-account evidence.

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

This version does not yet provide cloud credential-entry or OAuth screens in the
dashboard. Never enter cloud credentials into the dashboard login/password form.
No account authorization is completed just by selecting a provider name.

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
| `backend_matches` | Configured name has the expected direct `drive` or `webdav` type |
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
