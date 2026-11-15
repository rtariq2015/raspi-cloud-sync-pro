# Optional rclone administration GUI

**Source preparation only. Not installed, started, enabled or publicly exposed.**
The normal authenticated Raspi Cloud Sync Pro dashboard remains the place to
view sanitized USB-backup status. This optional advanced tool is separate.

## Optional dashboard entry

The source includes a default-disabled, owner-only advanced entry. Without
explicit `--rclone-admin-access`, its status helper performs no probes. A later
approved opt-in only checks the two fixed loopback endpoints: HTML at the GUI
and an unauthenticated API request that must receive a Basic authentication
challenge. It never supplies credentials, follows redirects, consults proxies,
invokes rclone or opens configuration files. The API URL is not returned to the
frontend.

“Ready” means those local login endpoints responded as expected. It does not
validate an admin password, a cloud account, process identity or an SSH tunnel.
The browser must require the owner to confirm that both SSH forwards are active
on that browsing device before enabling **Open official GUI**. No automatic
redirect/open is appropriate: a phone or remote browser's `127.0.0.1` is that
device, not the Pi. Keep the entry disabled if that device has no tunnel. A
reviewed public proxy or account-sharing system is not implemented here.

## Compatibility checked

The read-only Pi inventory reports the existing protected executable
`/opt/raspi-cloud-sync-pro/tools/mass-storage-bin/rclone` as **v1.76.0**. It is a
symlink whose target has an ELF header, not a command-filtering script. Do not
install another rclone or change the working worker PATH. Resolve the symlink,
check its target identity/protection and ordinary-user accessibility again
before installation; version text alone is insufficient.
The observed target is `/opt/raspi-cloud-sync-pro/tools/rclone-1.76.0`, owned by
root and not writable by its group or others. Its read-only `gui --help` exited
successfully and advertised `--addr`, `--api-addr`, `--no-open-browser`,
`--no-auth`, `--rc-no-auth` and `--rc-htpasswd`. No GUI process was started.

Official [GUI documentation](https://rclone.org/gui/) describes the embedded
interface introduced in v1.74. The [v1.76.0 implementation](https://github.com/rclone/rclone/blob/v1.76.0/cmd/gui/gui.go)
uses the bundled archive, so the prepared service does not download UI assets.
The older `rcd --rc-web-gui` recipe is rejected by
[v1.76.0 RC server code](https://github.com/rclone/rclone/blob/v1.76.0/fs/rc/rcserver/rcserver.go).
Do not substitute that recipe or enable automatic GUI downloads/updates.

## Prepared boundary

The [disabled service example](../deploy/systemd/raspi-cloud-rclone-admin.service.example)
uses a dedicated unprivileged `raspi-rclone-admin` account, an initially empty
private `rclone.conf` in its own state directory, and an independent cache.
It neither copies nor opens the USB worker's Google credentials. Core state and
configuration directories are inaccessible inside this service. No core service
dependency, restart, timer or boot-enablement instruction is included.

The GUI binds only `127.0.0.1:5573`; its RC API binds only `127.0.0.1:5572`.
Both authentication-disable switches are explicitly false. A separately created
bcrypt htpasswd file is delivered through systemd `LoadCredential`, rather than
putting passwords in argv, environment variables or Git. The
[authentication implementation](https://github.com/rclone/rclone/blob/v1.76.0/lib/http/server.go)
selects htpasswd before generated basic credentials. Missing credentials or
missing explicit configuration prevents startup. The template suppresses both
output streams because the GUI can otherwise log a generated credential-bearing
URL; service exit status remains available, but detailed GUI logs do not.
Static GUI assets are served locally; API data and actions require authentication.

The template initially permits only loopback network traffic. It cannot connect
to Google or other cloud servers. Granting network access, adding remotes or
importing credentials would require a new reviewed approval; none is prepared
as an automatic step here.

The [official RC security guidance](https://rclone.org/rc/) treats API access as
full authority of its service account, including configuration and file changes.
It is not a read-only backup-status interface. Authentication has no per-method
permission separation. Never proxy either port through the public dashboard,
ngrok, Cloudflare, a router forward or a LAN/wildcard bind.

## Access after separate deployment approval

The owner must separately approve account/unit/credential provisioning and one
isolated startup test. That review must confirm the existing binary supports
`gui --help`, directory ownership, bcrypt authentication, loopback binds,
firewall-filter enforcement and inability to read the live worker configuration.
Create the private htpasswd through a secure terminal prompt, never in chat or
a password-bearing command. The approved marker is not created by this project.

For a later approved manual start, an authenticated administrator opens an SSH
tunnel from their own computer (replace the placeholder locally):

```sh
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5573:127.0.0.1:5573 \
  -L 127.0.0.1:5572:127.0.0.1:5572 pi@PI_HOST
```

They then open `http://127.0.0.1:5573/`, select API
`http://127.0.0.1:5572/`, and enter the independent admin credentials privately.
Use those same local ports so the configured CORS origin matches. Do not copy
the GUI's credential-bearing login URL into chat, documentation or bookmarks.

This isolated GUI starts with **no Google remote and no USB-worker jobs**. It
cannot monitor, control or repair the existing worker by default. Showing the
live worker's backups belongs to the sanitized dashboard status bridge, not to
this separate rclone process.

Acceptance must prove unauthenticated API calls are refused, authenticated local
access succeeds, both listeners remain loopback-only, internet access is denied,
and core credentials/history/USB services are unchanged. Those live tests have
not been performed. On failure, stop only this optional service; retain its
private state, remove its approval marker if authorized, and leave the working
Cloud Stick services alone.
