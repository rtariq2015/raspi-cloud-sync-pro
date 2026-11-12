# Raspi Cloud Sync Pro

USB-to-cloud backup software for the **Raspberry Pi Zero 2 W**, built on
[RaspiAsUSBStickWithCloudSync by Koseng](https://github.com/Koseng/RaspiAsUSBStickWithCloudSync).
The goal is dependable USB storage with clear backup results, flexible cloud
destinations, simple Wi-Fi setup, and a responsive local web dashboard.

**Status: Phase 1 development, not ready for hardware deployment.** The backup
core operates on staged copies; live USB access, automatic services, and setup
scripts remain disabled. Real Pi/cloud integration has not been verified. Do not follow
the historical installation instructions for this release.

## Available today

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
| USB storage | Explicit host/Pi handoff and consistent staged copies | Model/stager tested; hardware adapter blocked |
| Koofr | WebDAV through a configured rclone remote and app password | Adapter tested locally; account testing pending |
| Google Drive | rclone remote and OAuth setup | Shared adapter tested; account testing pending |
| Wi-Fi | Saved networks, hotspots, enterprise and captive-portal guidance | Validation/policy tested; OS integration planned |
| Dashboard | Authenticated Overview, Wi-Fi, Cloud and History views | Planned |
| Reliability | Persistent jobs, retries, verification and low-space protection | Core tested; hardware recovery pending |

“Sync” initially means one-way backup. Automatic source deletion and remote
deletion propagation are outside the initial scope. Uploaded and verified will
be distinct states. See the [roadmap](docs/ROADMAP.md) and
[architecture](docs/ARCHITECTURE.md).

While the cable remains connected, cloud uploads may use completed staging batches.
Reading newly written USB files requires a coordinated host flush/eject, exclusive
Pi access, staging, and host re-enumeration. Quiet time is not a safety guarantee.
See [Phase 1 scope and usage](docs/PHASE1.md) and [Wi-Fi design](docs/NETWORKING.md).

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

## Project map

```text
src/raspi_cloud_sync_pro/  Ownership, staging, backup engine, history, providers and policy
src/home/pi/              Upstream scripts and service units, quarantined
src/DropboxHelper/        Original Windows helper, retained for provenance
config/                   Non-secret example configuration
tests/                    Hardware-free regression tests
docs/                     Current review, safety design and development plans
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

[Cloud Stick Pro](https://github.com/rtariq2015/cloud-stick-pro) is a separate
project used only as a feature and design reference. Its concepts of guided setup,
saved networks, verification, and clear status inform the roadmap. No code or
assets from it have been imported, and its repository is not changed.
