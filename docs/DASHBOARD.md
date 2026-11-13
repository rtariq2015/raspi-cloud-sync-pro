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

## Restricted HTTPS candidate — 2026-11-13

The follow-up installed the reviewed wheel in a root-owned versioned directory
and created a dedicated no-login `raspi-dashboard` identity. A transient candidate
runs with NoNewPrivileges, read-only system/home restrictions, private state and
loopback-only binding. It has not been enabled at boot. Account/system settings
were backed up before installation; the user-level preview remains retained.

A verified official cloudflared binary runs as a separate restricted transient
service and connects outbound to the loopback backend. The dashboard is configured
with the exact public HTTPS origin and Secure cookies. Through the public URL,
owner/viewer login, all three QA-file download/hash checks, anonymous rejection,
viewer upload rejection, missing-CSRF rejection and logout revocation passed.
USB and Wi-Fi remained connected. No router ports or laptop network settings
were changed. This is a temporary development tunnel, not stable hosting.

Quick Tunnel hostnames change when the tunnel is recreated; a named tunnel and
stable hostname/access policy remain account setup work. Do not publish generated
account passwords or the temporary preview address in Git. This public-path test
was made from the development laptop; an actual phone on another network and
Android installation still need acceptance. No real cloud account was connected.

To stop the temporary public preview, stop only
`raspi-cloud-tunnel-preview.service`; this does not stop USB, Wi-Fi or SSH.
The transient dashboard candidate is `raspi-cloud-dashboard-candidate.service`.
Retain its state and uploads when stopping or replacing it.

## Read-only worker report — development 0.1.0.dev4

Pass `--worker-state /absolute/private/worker-state` to read only that directory's
fixed `worker-status.json`. Select `--history /absolute/path/history.sqlite3`
separately if recorded jobs should be shown. Neither option activates a worker
or reads its cloud credential configuration. The Backups panel distinguishes
not connected, unavailable, malformed, error, observed and older-than-five-minute
reports. An older report does not establish the service is dead; an idle Rust
scheduler may be parked. Queue/destination counts are projected without exposing
remote names or provider output. Reports cannot declare cloud accounts accepted
or set per-file cloud verification on the web-inbox listing.


## Password management — development 0.1.0.dev5

Open **Account** while signed in to change your own password. Both owner and
viewer accounts must enter their current password and matching new password
(12–256 characters). CSRF and attempt limits apply. Account roles cannot be
changed in this form. Successful changes sign that account out on all devices;
unrelated accounts remain signed in.

For local recovery, run this as the OS identity that owns the private dashboard
state, in a trusted interactive terminal:

```sh
raspi-cloud-sync-pro dashboard-reset-password \
  --state /absolute/private/dashboard-state --username owner
```

It prompts privately twice; there is no password command-line argument or public
password-reset endpoint. Private state access is the recovery authority. The
restricted system service's state belongs to `raspi-dashboard`, so an ordinary
`pi` shell needs administrator authentication to run recovery as that identity.
Do not change permissions to make recovery available to unrelated users.

Writes use a cross-process lock, private hash-only backups, fsynced files and
atomic replacement. Current dev5 app requests compare the session's credential
tag with current auth state, so CLI resets revoke that account's existing
sessions on the next request without restarting a dev5 server. An already-running
older build needs replacement/restart to gain this behavior. No public signup,
email reset or account invitations are implemented yet.

Actual Pi loopback HTTP and private PTY recovery tests passed using disposable
accounts. At the time of that isolated trial, the public service remained on dev3.
The later protected-service upgrade is recorded below. The test did not change its
accounts, USB profiles, Wi-Fi or stored user files.


Dev6 additionally refuses getpass's potentially echoed no-terminal fallback.
Initialization and recovery require a secure interactive prompt; piped or
noninteractive password entry is not a supported recovery channel. This refusal
happens before account creation or an auth update. Actual Pi PTY recovery and
no-terminal refusal both passed, with no generated credential printed.


## Restricted dev6 deployment — 2026-11-13

After private root-terminal authentication, the checksum-verified dev6 wheel was
installed in a new root-owned version directory. Existing account hashes and
uploads stayed in the same private service-owned state. Prior state/unit records
and standalone rollback scripts were saved before changing the project service;
the dev3 installation remains retained. Preparation and dependency checks completed
before the old process stopped. Expected user/group, process arguments and bounded
loopback HTTP health checks passed for the new restricted process.

The temporary public HTTPS URL then passed owner/viewer login, three existing
QA-file byte/SHA-256 downloads, anonymous/viewer-write/CSRF rejection and logout
revocation. The Account form is reachable; real account passwords were not changed
by acceptance. Cloud setup remained unconfigured. Wi-Fi stayed connected; the
existing USB addressing instability remains a separate unresolved issue.

The backend stays loopback-only under `raspi-dashboard`, NoNewPrivileges and
read-only system/home restrictions. Neither dashboard nor tunnel is enabled at
boot. No router/laptop networking, USB gadget settings, OS password or sudo policy
was changed. The public URL is temporary; no physical off-LAN phone or stable
hostname acceptance is claimed. Results: [deployment](acceptance/pi-restricted-dev6-2026-11-13.json)
and [HTTPS](acceptance/pi-https-dev6-2026-11-13.json).

## Guided cloud connection foundation — dev7 source

Backup destinations now includes owner-only **Connect Google Drive** and
**Connect Dropbox** controls. The registered-app configuration must be installed
privately before either can start sign-in. The provider handles its own login and
consent; the dashboard never asks for a Google/Dropbox password. Viewers cannot
start or complete an authorization.

The current implementation stops after saving a private authorization and labels
it **Not tested**. Folder selection, a real read-back verification, token refresh,
rclone credential handoff and explicit enablement are still required. It does not
start a worker or claim a cloud backup from a successful consent response.
Koofr's app-password form and Synology NAS setup are separate planned connectors.

See [private cloud setup](CLOUD_SETUP.md) for the example schema, fixed callback
paths, permissions and acceptance limits. These controls are now deployed in the
protected dev8 preview. Do not restart a dashboard during an account
change or provider consent flow; unfinished in-memory flows will be discarded.

The dev8 installation retained all account credentials and QA files. Its preflight
caught root-only permissions on newly installed public code before stopping dev6;
only the verified release directory/venv permissions were corrected. Private
artifacts, account/token state and backups kept their original restrictions.
The actual service identity then passed app construction and process/HTTP health.
Public HTTPS repeated login, role, CSRF, logout and all three file SHA checks.
Dev6 remains available for rollback; no boot service, USB or Wi-Fi setting changed.
[Deployment](acceptance/pi-restricted-dev8-2026-11-13.json) ·
[HTTPS checks](acceptance/pi-https-dev8-2026-11-13.json).
