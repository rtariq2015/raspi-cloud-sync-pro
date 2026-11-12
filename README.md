# Raspi Cloud Sync Pro

USB-to-cloud backup software for the **Raspberry Pi Zero 2 W**, built on
[RaspiAsUSBStickWithCloudSync by Koseng](https://github.com/Koseng/RaspiAsUSBStickWithCloudSync).
The goal is dependable USB storage with clear backup results, flexible cloud
destinations, simple Wi-Fi setup, and a responsive local web dashboard.

**Status: Phase 2 development, not ready for hardware deployment.** Selectable
USB Ethernet/Samba and mass-storage prototypes feed the staged-file backup core.
All live device/network operations, SMB sessions and automatic services remain
disabled pending a separately approved disposable-data Pi trial. Do not follow
the historical installation instructions for this release.

## Available today

- Preferred **USB Ethernet + Samba** mode integrates the official Raspberry Pi
  RNDIS/CDC-ECM provider as an external dependency, with secure sharing templates
  and SMB deny-write/delete snapshots. Windows can use a mapped network drive;
  the Pi keeps filesystem ownership. Physical interoperability remains untested.
- Classic **USB mass storage** remains an explicit-eject compatibility option.
  A guarded Linux controller adds read-only staging, resource checks and an
  intent journal; it never treats write quiet time as a host release.

- Complete upstream Git ancestry, original source locations, documentation and
  Windows Dropbox helper are retained, with documented safety changes.
- The original README is preserved byte-for-byte as [README.upstream.md](README.upstream.md).
- Automatic USB retention deletion and staged-folder deletion are removed.
  Legacy setup and runtime entry points fail before doing work.
- An installable Python package provides read-only status and configuration checks.
- An ownership state model rejects unsafe handoffs. The stager fully enumerates
  an exclusively owned snapshot, streams SHA-256 hashes, and atomically publishes
  ready batches. Failed batches and originals are retained.
- SQLite jobs/history track each destination and file version, with bounded
  exponential retries, interrupted-job recovery and a single-worker lock.
- An opt-in rclone adapter prepares Koofr WebDAV and Google Drive uploads into
  versioned paths, followed by downloaded-content SHA-256 verification.
- Wi-Fi profile validation covers home/hotspot, PEAP and EAP-TLS settings;
  unknown, captive-portal, offline and disallowed metered states pause uploads.
- Automated tests cover these components without requiring a Pi or cloud account.

## Planned improvements

| Area | Direction | Current state |
| --- | --- | --- |
| USB modes | Ethernet/Samba preferred; mass storage waits for clean eject | Controllers/ingestion simulated; hardware blocked |
| Koofr | WebDAV through a configured rclone remote and app password | Adapter tested locally; account testing pending |
| Google Drive | rclone remote and OAuth setup | Shared adapter tested; account testing pending |
| Wi-Fi | Saved networks, hotspots, enterprise and captive-portal guidance | Validation/policy tested; OS integration planned |
| Dashboard | Authenticated Overview, Wi-Fi, Cloud and History views | Planned |
| Reliability | Persistent jobs, retries, verification, crash recovery and space checks | Core/controller simulated; hardware recovery pending |

“Sync” initially means one-way backup. Automatic source deletion and remote
deletion propagation are outside the initial scope. Uploaded and verified will
be distinct states. See the [roadmap](docs/ROADMAP.md) and
[architecture](docs/ARCHITECTURE.md).

The same USB data cable can supply power and Ethernet access, subject to the
host/cable power budget. USB-local sharing works without Wi-Fi; cloud uploads
queue until approved Internet connectivity returns. Ethernet mode captures stable
SMB file versions without ejecting. Mass-storage mode requires a coordinated host
flush/eject before reading new files. Quiet time is not a safety guarantee.
See [Phase 2 design](docs/PHASE2.md), [official provider review](docs/OFFICIAL_USB_ETHERNET.md),
[Pi test guide](docs/PI_TEST_GUIDE.md), and [Wi-Fi design](docs/NETWORKING.md).

## Develop locally without hardware

Linux and Python 3.9 or newer are the development baseline. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
raspi-cloud-sync-pro status
raspi-cloud-sync-pro check-config config/config.example.json
python -m unittest discover -s tests -v
```

Alternatively, run directly from source without installing anything:

```sh
PYTHONPATH=src python3 -m raspi_cloud_sync_pro status
PYTHONPATH=src python3 -m raspi_cloud_sync_pro check-config config/config.example.json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Copy `config/config.example.json` to the ignored `config/config.local.json` for
local experiments. The schema contains planning settings only; it accepts planned
provider names but requires sync and automatic deletion to remain `false`.
Credentials do not belong in this file. There is no install-to-Pi or hardware
setup command in this release.

Inspect modes and readiness without applying settings:

```sh
PYTHONPATH=src python3 -m raspi_cloud_sync_pro mode-plan config/device.example.json
PYTHONPATH=src python3 -m raspi_cloud_sync_pro diagnostics --mode ethernet_samba
```

`check-hardware` reports blockers and returns 2 in this gated build. The optional
SMB dependency needs Python 3.10+; the core still supports 3.9+. Never run device
installation scripts or enable deployment templates on a development computer.

## Project map

```text
src/raspi_cloud_sync_pro/  Ownership, staging, backup engine, history, providers and policy
src/home/pi/              Upstream scripts and service units, quarantined
src/DropboxHelper/        Original Windows helper, retained for provenance
config/                   Non-secret example configuration
tests/                    Hardware-free regression tests
docs/                     Current review, safety design and development plans
deploy/                   Inactive systemd, Samba, DHCP, NM and firewall templates
doc/                      Historical upstream guides and images
```

Read the [upstream functionality review](docs/UPSTREAM_REVIEW.md),
[storage safety design](docs/SAFETY.md), and [contributor guide](CONTRIBUTING.md)
before changing the runtime. See [validation results](docs/VALIDATION.md) for
exact checks and remaining limits. Development happens on the
[`development` branch](https://github.com/rtariq2015/raspi-cloud-sync-pro/tree/development)
of the independent project. [Git workflow](docs/FIRST_COMMIT.md).

## Credits and license

**Koseng created the upstream Raspberry Pi USB mass-storage and cloud-sync
implementation.** Raspi Cloud Sync Pro preserves its original
[GNU GPL version 3 license](LICENSE) unchanged, its history, and its attribution.
This derivative project is distributed under that same GPL-3.0 license.
See [upstream provenance and local changes](docs/UPSTREAM_REVIEW.md).

The external USB Ethernet provider is **Raspberry Pi Ltd.'s rpi-usb-gadget**,
licensed Apache-2.0. Its credits/license and optional SMB client notices are
preserved in [third-party notices](THIRD_PARTY_NOTICES.md).

[Cloud Stick Pro](https://github.com/rtariq2015/cloud-stick-pro) is a separate
project used only as a feature and design reference. Its concepts of guided setup,
saved networks, verification, and clear status inform the roadmap. No code or
assets from it have been imported, and its repository is not changed.
