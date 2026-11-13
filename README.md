<div align="center">

# Raspi Cloud Sync Pro

### A small Raspberry Pi. A clear view of your files and backups.

A file and backup appliance for the **Raspberry Pi Zero 2 W**, with a responsive
web dashboard, an Android web-app foundation, and verified cloud-backup components.

[![Development checks](https://github.com/rtariq2015/raspi-cloud-sync-pro/actions/workflows/tests.yml/badge.svg?branch=development)](https://github.com/rtariq2015/raspi-cloud-sync-pro/actions/workflows/tests.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Development preview](https://img.shields.io/badge/status-development%20preview-orange)](docs/ROADMAP.md)

[Feature plan](docs/PRODUCT_PLAN.md) · [Dashboard guide](docs/DASHBOARD.md) ·
[Install on another Pi](docs/REPLICATION.md) · [Pi test guide](docs/PI_TEST_GUIDE.md) · [Validation](docs/VALIDATION.md)

</div>

![Raspi Cloud Sync Pro dashboard running on a Raspberry Pi with disposable QA files](docs/images/dashboard-desktop.png)

**Development preview `0.1.0.dev3`.** The authenticated dashboard has been tested
on a real Zero 2 W. Web uploads, text previews and downloads work in an isolated
Pi preview. USB/Samba runtimes remain gated; cloud account acceptance, the staged
upload scheduler, public website deployment and physical Android installation
are still pending. This is not a production-ready backup appliance or release.

## What the experience will be

Connect one USB data cable for power and file access. Copy files from the computer,
then open the dashboard from a computer or phone to see local files, destinations
and backup history. Uploads queue when approved Internet access is unavailable.
Your originals stay on the Pi. “Verified” means remote contents were read back
and checked, not merely that an upload command returned successfully.

The preferred USB mode is **Ethernet + authenticated Samba**: Linux owns the
filesystem and the computer uses a network drive. Classic **USB mass storage**
remains an explicit-eject compatibility option. The Pi never reads a host-writable
block filesystem based on a quiet period.

## Built so far

| Area | Available in development | Still needs acceptance |
| --- | --- | --- |
| Web dashboard | Overview, Files, Backups, Wi-Fi/access and History screens | Operational setup controls and public deployment |
| File access | Owner uploads; owner/viewer browse, text preview and download | Samba folder browsing, large/resumable uploads, image/PDF previews |
| Phone experience | Responsive UI, PWA manifest/icon/service worker | Physical Android HTTPS installation; no native APK yet |
| Access control | Hashed passwords, owner/viewer roles, CSRF, expiring/revocable sessions | Account recovery/invitations and public access policy |
| Cloud backup core | Koofr WebDAV + Google Drive rclone adapters, immutable versions, retries, SQLite history, read-back SHA-256 | Private account authorization, scheduler and real remote tests |
| USB modes | Gated mass-storage controller and Samba snapshot ingestion | Real host-eject, Samba share/lease locking and power tests |
| Connectivity | Working Pi USB + home Wi-Fi baseline; read-only network diagnostics | Enterprise provisioning, portal login and endurance tests |
| Another Pi | Python package/wheel and documented virtual-environment workflow | Repeatable deployment and second-device acceptance |

Opening the dashboard does not activate cloud uploads, change Wi-Fi, start Samba
or enable gadget operations. The current Files page shows completed **web uploads**,
separately from the future USB/Samba source view. Failed/incomplete uploads remain
retained and cannot be mistaken for completed batches.

## On your phone

<table><tr><td width="320">
<img src="docs/images/dashboard-mobile.png" alt="Actual dashboard in a phone-sized browser" width="300">
</td><td>

**One interface across devices.**

The Android experience starts as an installable web app, sharing the same login,
files and history as desktop. Normal remote installation needs HTTPS. Private
files are not cached by the service worker.

**Access from elsewhere without a VPN app.**

The design uses an authenticated HTTPS website through an outbound tunnel. Your
manager can use a phone browser and a viewer account. A stable hostname and access
policy still need setup; Tailscale is not required.

**Wi-Fi that explains what it needs.**

Home/hotspot, enterprise certificate authentication and captive-portal sign-in are
separate workflows. Accepting a portal on a phone may not authorize the Pi. See
the researched [product plan](docs/PRODUCT_PLAN.md) and [networking design](docs/NETWORKING.md).

</td></tr></table>

The screenshots show the actual Pi preview with disposable QA files and no
claimed cloud backups. The Wi-Fi address is replaced with a generic label.

## Run the dashboard locally

Use Python 3.9+ and a virtual environment. Dashboard dependencies are optional;
the backup core still uses the Python standard library.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dashboard]'
install -d -m 700 "$HOME/.local/state/raspi-cloud-sync-pro-dashboard"
.venv/bin/raspi-cloud-sync-pro dashboard-init \
  --state "$HOME/.local/state/raspi-cloud-sync-pro-dashboard" --username owner
.venv/bin/raspi-cloud-sync-pro dashboard \
  --state "$HOME/.local/state/raspi-cloud-sync-pro-dashboard" --port 8080
```

Open `http://127.0.0.1:8080`. Account creation prompts privately; there is no default
password. The server binds only to loopback. See [the dashboard guide](docs/DASHBOARD.md)
for a viewer account, SSH access, HTTPS configuration and current limits.

Do not run historical installation scripts, expose the live root filesystem as
USB storage, or replace a working Pi network configuration to match an example.
Use [the existing Pi baseline](docs/PI_CONNECTIVITY.md) and the separately reviewed
[device trial sequence](docs/PI_TEST_GUIDE.md).

## Development and tests

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m raspi_cloud_sync_pro status
PYTHONPATH=src .venv/bin/python -m raspi_cloud_sync_pro network-status
PYTHONPATH=src .venv/bin/python -m raspi_cloud_sync_pro diagnostics --mode ethernet_samba
```

`check-hardware` intentionally returns 2 while the hardware gate is disabled.
Tests use temporary files and simulated devices; the optional real-rclone test
uses a temporary local alias, not cloud accounts. Install `.[dashboard]` to run
the web tests. See [validation evidence and limits](docs/VALIDATION.md).

| Directory | Contents |
| --- | --- |
| `src/raspi_cloud_sync_pro/` | Dashboard, staging, backup engine, history, policy and guarded device adapters |
| `tests/` | Regression, fault and access-control tests |
| `docs/` | Research, architecture, acceptance records and guides |
| `deploy/` | Inactive deployment templates |
| `src/home/pi/` | Retained upstream scripts and services, safety-quarantined |
| `src/DropboxHelper/` | Retained original Windows helper |
| `doc/` | Historical upstream documentation; not current install instructions |

Development stays on [`development`](https://github.com/rtariq2015/raspi-cloud-sync-pro/tree/development).
Merging to main and publishing a release require owner approval. Read the
[contributor guide](CONTRIBUTING.md), [architecture](docs/ARCHITECTURE.md) and
[roadmap](docs/ROADMAP.md).

## Credits and license

**Koseng created [RaspiAsUSBStickWithCloudSync](https://github.com/Koseng/RaspiAsUSBStickWithCloudSync),
the Raspberry Pi USB mass-storage and cloud-sync foundation of this project.**
Its complete Git ancestry, original source locations and original
[GPL-3.0 license](LICENSE) are preserved. The original README is retained exactly
as [README.upstream.md](README.upstream.md). This derivative remains GPL-3.0.
See [the upstream review](docs/UPSTREAM_REVIEW.md) for provenance and safety changes.

The optional external USB Ethernet provider is **Raspberry Pi Ltd.'s rpi-usb-gadget**
(Apache-2.0). See [third-party notices](THIRD_PARTY_NOTICES.md) for its credits and
other optional components. Dashboard screenshots and the simple project icon
were created for this project.

[Cloud Stick Pro](https://github.com/rtariq2015/cloud-stick-pro) is a separate,
unchanged feature/design reference. No code, assets, history or credentials from
it were imported. Automatic original-file and staged-folder deletion remain off.
