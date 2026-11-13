# Dashboard and Android web app preview

Development version `0.1.0.dev3` adds an optional Flask/Waitress dashboard. USB
controller and Samba hardware gates remain disabled. Opening the dashboard does
not enable a cloud worker, change a network, expose a disk image or start Samba.

## Run in an isolated environment

Use a private state directory, outside every share and source tree. Run as an
unprivileged user. The dashboard stores password hashes and its signing key there;
do not add it to Git or publish it as a share.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dashboard]'
install -d -m 700 "$HOME/.local/state/raspi-cloud-sync-pro-dashboard"
.venv/bin/raspi-cloud-sync-pro dashboard-init \
  --state "$HOME/.local/state/raspi-cloud-sync-pro-dashboard" --username owner
.venv/bin/raspi-cloud-sync-pro dashboard-init \
  --state "$HOME/.local/state/raspi-cloud-sync-pro-dashboard" --username viewer --role viewer
.venv/bin/raspi-cloud-sync-pro dashboard \
  --state "$HOME/.local/state/raspi-cloud-sync-pro-dashboard" --port 8080
```

Account creation prompts privately, rejects short passwords and existing-account
overwrite, stores scrypt hashes and backs up an existing auth file before adding
an account. Restart the app after changing the account file. The owner can upload;
both roles can browse, preview and download. Viewers cannot upload, delete, execute
commands or change device/network configuration. Session expiry is 30 minutes;
logout revokes the server-side session. A process restart revokes all sessions.

The server always binds **127.0.0.1**, not every interface. Open it locally or use
a verified SSH local forward. For a reviewed HTTPS proxy, set
`--public-origin https://your-approved-hostname.example`; this restricts Host
headers and enables Secure cookies/HSTS. Do not trust forwarded identity headers
or publish the loopback service without an authentication/deployment review.

## Files and truthful backup state

The preview file page lists completed web uploads in the private `web-inbox`.
It does not yet expose the Samba source tree, cloud objects or arbitrary Pi paths.
Uploads are owner-only and limited to 100 MiB each. Only a single filename is
accepted; path traversal, dotfiles, symlinks and special files are rejected.

`stage_upload` streams the request file, computes SHA-256, enforces free-space
reserve/size limits, fsyncs payload and manifest and atomically publishes a ready
batch. Interrupted/oversized/low-space batches remain incomplete and cannot be
downloaded or queued as complete. Originals and completed uploads are not pruned.
No space-pressure eviction policy is hidden in the web server.

The API reports **staged locally**, never cloud-verified for these uploads. Cloud
account and worker integration are still pending. Existing Phase 1 history can
be read with the optional `--history` path; a missing database is not created.
The current overview count covers the bounded recent history returned, not an
unbounded lifetime total. Preview is bounded text (64 KiB); downloads are served
as attachments with content sniffing disabled. PDF/image viewing is future work.

## Phone installation and public access

The responsive layout, app manifest, icon and service worker provide the PWA
foundation. Use Android Chrome's install/add-to-home-screen action on the reviewed
HTTPS URL when available. Actual installation on a physical phone has not yet
been accepted. There is no native APK in this version.

Private pages/files/API responses are marked no-store. The service worker does
not keep an offline file cache. Offline phone access and background phone uploads
are not implied. See [the product plan](PRODUCT_PLAN.md) for the browser-only
tunnel design, access roles, network onboarding and remaining account steps.

## Live Pi development trial

On 2026-11-13, installed the wheel in a private user-level virtual environment
on the verified Zero 2 W. A transient user service runs it with NoNewPrivileges
and loopback binding; it is not enabled at boot. An SSH local forward provided
the browser test path. Owner/viewer passwords were generated and stored privately
on the Pi, never committed or shown in logs. This user-level preview is not the
restricted system-user/public deployment.

Three disposable QA files passed authenticated upload, preview/download and
byte/SHA-256 comparisons. Anonymous access, viewer upload, missing CSRF and a
logged-out session were rejected. Both USB/Wi-Fi stayed connected. No public
tunnel, cloud account or real Samba session was used for these checks.

The README screenshots are from this real preview, using disposable files.
The Wi-Fi address was replaced with a generic label for the public screenshots.
