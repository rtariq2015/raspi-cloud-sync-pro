# Phase 2 validation record

Date: 2026-11-12. Linux x86-64 development environment, Python 3.12.3.
Package `0.1.0.dev2`; target branch `development`. Hardware gate remains false.

| Check | Result and limits |
| --- | --- |
| Automated suite | 111 tests pass locally, including all 62 Phase 1 tests and real rclone against a temporary local alias |
| Controller simulations | Session-bound eject, quiet host, link loss, startup-empty LUN refusal, dirty filesystem, power/profile/RO faults, detach/mount/unmount/reattach failures, interruption, resource recovery and controller contention |
| Linux adapter | Production gates before mutations/config reads, detached-profile creation, driver/composite rejection, image identity, partition/device numbers, RO flags, non-repairing fsck command and no force-eject/unmount |
| Samba ingestion | Active writer deferral, deny-write/delete snapshot contract, successful close before publication, interruption/short read/low space, queue reconciliation, changed versions, Wi-Fi/cloud outage and approved/metered policy |
| Official provider | Pinned source reviewed; offline adapter commands tested only with a fake executor; no upstream installer executed |
| Network and templates | NM/IP/DHCP/Samba policies structurally tested; real dnsmasq syntax parser passes without opening a server |
| systemd | Static-unit syntax passes on temporary copies with executable substitutions; no unit installation, start, enable or restart |
| CLI | Status, device mode plan, read-only diagnostics and disabled controller/share/device gates checked |
| Packaging/provenance | Wheel build/install, current core and GPL/Apache notices; original README/LICENSE exact upstream bytes, ancestry retained |
| Documentation/Git | Current relative links resolve; Python compilation, shell parsing and staged whitespace checks pass |

The official provider was reviewed at
`05ff0940af823868ff995808286bc0bcca8b86b7`. Its Apache license text is preserved,
but no provider installer/watcher/driver binary is vendored. Cloud Stick Pro was
not modified. Root `LICENSE` and `README.upstream.md` remain unchanged.

Rclone 1.76.0 integration uses only a temporary local alias remote, not a cloud
account. SMB tests use simulated server handles and a mocked client API, not an
actual Samba/Windows connection. `testparm` is unavailable on the development
machine; full Samba parameter/runtime validation belongs to the approved Pi.
NetworkManager profiles and firewall rules were not loaded. The systemd parser
check does not establish installed paths, account permissions or device readiness.

No real USB image was mounted, no block device formatted, no Pi flashed, no USB
or network settings changed, no service enabled, and no installation script run.
Read-only diagnostics correctly report this development machine is not a ready Pi.
The firmware power API is unavailable here; voltage/current adequacy is unknown.

GitHub Actions covers Python 3.9, 3.12 and 3.13, with the local rclone integration
enabled on 3.12. Local execution used 3.12; remote results belong to the associated
commit's workflow run. Optional SMB deployment requires Python 3.10 or newer.

Actual host-eject/kernel-unbind behavior, electrical stability, Windows RNDIS
binding, DHCP, authenticated SMB3, lease/share-mode enforcement, ext4/SD durability,
enterprise/managed host restrictions and real cloud accounts remain unverified.
The [first Pi trial guide](PI_TEST_GUIDE.md) defines the separate approval and
acceptance steps. Simulations are not evidence of safe concurrent block access.

## Connectivity follow-up — 2026-11-13

Added `network-status`, a read-only CLI inventory of selected non-secret USB/Wi-Fi
device fields, IPv4 default routes and the official USB auto-switcher. Six new
regression tests cover existing USB client addressing, route priority/ties,
malformed/multipath routes, missing tools, timeouts, unknown observations and CLI
output without hardware authorization. All 117 tests run successfully locally;
the optional real-rclone integration is skipped because rclone is unavailable.
No cloud-account or real Samba test is claimed.

Executed the new diagnostic function in memory on the verified Zero 2 W through
the existing management session, without installing or enabling the project. It
correctly observed connected USB and Wi-Fi interfaces, USB as the preferred IPv4
route, the active official profile auto-switcher, and the mismatch with the fixed
USB-local share configuration. The command changed no network/device settings.
Also exercised the CLI on the development laptop; missing target interfaces and
an unavailable provider service remained unknown rather than ready.

The separate manual OS connectivity repair and five-minute Wi-Fi evidence are
recorded in [PI_CONNECTIVITY.md](PI_CONNECTIVITY.md). They update the management
baseline, not the earlier device/SMB/cloud acceptance limits. Reboot persistence,
authenticated Wi-Fi SSH login and project runtime deployment remain untested.

## Dashboard preview — 2026-11-13

Version `0.1.0.dev3`: 131 tests ran locally with the dashboard dependencies;
130 passed and the optional rclone integration was skipped. New tests cover
account hashes/permissions, login throttling, owner/viewer roles, CSRF, revoked
sessions, host restrictions, Secure-cookie/HTTPS settings, private-cache exclusion,
traversal/symlink refusal, streamed upload manifests, client interruption, low
space and failed publication. No test formats a disk or configures a gadget.

Built a wheel including templates, CSS/JavaScript, icon and GPL/Apache notices.
Installed it in an isolated user virtual environment on the verified Pi (Python
3.13.5) and started a transient NoNewPrivileges user service bound to loopback.
It was not enabled at boot. A pinned-host-key SSH session forwarded the browser
connection. A fresh authenticated SSH connection over Wi-Fi succeeded in this
follow-up; the older connectivity record's SSH-banner-only limitation is historical.

Live acceptance: three disposable QA uploads; downloaded byte and SHA-256 matches
for all three; anonymous API rejection, viewer upload rejection, CSRF rejection,
logout revocation and both USB/Wi-Fi still connected. The browser rendered the
Pi-backed dashboard at desktop and phone widths with no reported API errors.
README screenshots show this preview with the Wi-Fi address replaced by a generic
label. No cloud verification, public tunnel, physical Android installation or
real Samba session is claimed. System-wide deployment awaits administrator
authentication; the local preview did not change system services or networks.

## Hybrid worker and HTTPS follow-up — 2026-11-13

**145 Python tests pass**, including the real-rclone local-alias integration with
rclone 1.76.0. Four Rust tests pass; formatting and clippy with warnings denied
pass in the pinned Rust 1.100.0 container. The ARM64 release build succeeds.
Additional Python checks cover durable per-destination reconciliation, retry and
network-policy deferral, receipt reuse without trusting changed payloads,
concurrent scheduler refusal and non-secret enterprise Wi-Fi planning.

The 398,048-byte ARM64 watcher ran on the actual Zero 2 W with a protected fixture
runner: incomplete work ignored, one ready batch triggered, unchanged completed
work parked. Idle parent VmRSS/VmHWM was 1,796 KiB with one thread. This does not
measure a real cloud transfer or the whole appliance. Details and Python dashboard
measurements are in [RUST_EVALUATION.md](RUST_EVALUATION.md).

After private administrator authentication, a root-owned versioned dashboard
installation and dedicated no-login identity were installed with account-file
backups. The restricted transient system candidate passed the QA-file/access
checks, then passed them again through a temporary outbound HTTPS tunnel using
a verified official cloudflared binary. No passwords were printed or committed.
The app backend remained loopback-only; neither service was enabled at boot.

Public-path acceptance: owner/viewer logins; three existing QA files downloaded
with matching bytes/SHA-256; anonymous API access, viewer upload, missing CSRF and
revoked sessions rejected. USB and Wi-Fi were still connected. Actual new uploads
were tested through the earlier loopback preview; public rechecks reused the
completed QA files. The cloud state correctly stayed `not_configured`.

No actual cloud account, off-LAN physical phone, real Samba session, image flash,
new gadget configuration, service boot enablement or release publication is
claimed. No source or staging deletion was introduced. A temporary tunnel is not
a stable public deployment. Restricted service and tunnel rollback are described
in [DASHBOARD.md](DASHBOARD.md).

## Operational worker follow-up — 2026-11-13, development 0.1.0.dev4

153 Python tests and six Rust tests pass. New regressions cover more than 100
ready batches, malformed retained batches across worker restarts, failed Rust
one-shot execution, actual nmcli guessed-metering labels, and dashboard reporting
of unavailable, invalid, stale and sanitized worker state. Formatting, clippy,
Ruff F/E9, Python and JavaScript parsing pass.

A reviewed wheel (`d1331ce0cf822dd0fc675a9b863af33d4ddbefeb16f968c101fd99764646d055`)
was installed in a new unprivileged Pi virtual environment, preserving existing
preview/system installations. Actual Rust → Python CLI → ARM64 rclone execution
produced six independently verified local copies from three files across two
temporary alias destinations. Blocked-policy jobs spent no attempts; incomplete
and completed payloads were retained; repeat runs deduplicated. Controlled
process-group termination and a subsequent run retained/reconciled completed
work. This was not a power cut or real cloud account test.

The [sanitized Pi record](acceptance/pi-worker-2026-11-13.json) includes exact
artifacts. No disk image, device configuration or network profile was modified.
A follow-up found the existing official USB auto-switcher repeatedly trying DHCP
and changing USB addressing. A generic autoconnected Ethernet profile was also
observed on usb0; its causal role is not established.
Wi-Fi SSH remained available. USB carrier presence does not prove working USB IP
access; stable USB addressing remains unresolved and requires a focused repair.

An actual Debian Trixie Samba server in an isolated `--network none` Docker
container passed encrypted SMB3 share-mode, access-control, staged-content,
interrupted-read retention and changed-version checks. See the
[result](acceptance/samba-container-2026-11-13.json) and
[reproducible fixture](../tools/acceptance/samba/README.md). This is stronger than
mocked SMB handles, but it does not replace Pi/Windows/USB acceptance. No host
ports, device access or production hardware gates were enabled.

The restricted system preview still runs the earlier dev3 artifact. Its upgrade
and actual Pi Samba provisioning need a privately authenticated administrator
session; the earlier authentication expired. Unprivileged development/testing
continues. No administrator policy or account password was changed to bypass that.


The dev4 dashboard API was also exercised with Flask's test client **on the Pi**
against the real local trial database and worker report. Authenticated responses
showed two destinations, zero pending jobs and six recorded local verifications;
cloud configuration remained unclaimed and the separate empty web inbox stayed
empty. This is an application/API test on the target CPU, not a new public HTTP
or physical-phone acceptance test. [Result](acceptance/pi-dashboard-worker-2026-11-13.json).


## Five-minute Pi retention trial and dev5 accounts

The dev4 Rust/Python/rclone loop ran for 300.8 seconds on the physical Pi with
local alias destinations. Ten added files produced twenty added verified copies
(26 total jobs including the earlier six), all sources and the incomplete batch
remained present, and 20/20 Wi-Fi-bound gateway probes passed. Firmware reported
`throttled=0x0`. [Result](acceptance/pi-retention-soak-2026-11-13.json).

Sampled process high-water RSS was 1,812 KiB for the Rust watcher, 22,224 KiB for
Python and 60,672 KiB for rclone; minimum observed system MemAvailable was
117,988 KiB. These per-process samples are not a whole-appliance total, a hard
memory cap or a guarantee for large/cloud transfers. No electrical power cut,
cloud account or multi-day endurance is implied.

Dev5: **176 Python tests and six Rust tests pass**. Account changes add locked,
private, durable auth writes and backups, browser current/new/confirmation checks,
CSRF/throttling, per-account session revocation and interactive CLI recovery.
Read-only cloud readiness adds protected config/executable checks, bounded
local-only metadata capture, raw-output suppression, backend distinctions,
malformed-output/timeout cases and actual rclone metadata integration.

The dev5 wheel SHA-256 is
`2083db4f02307547305b1159e49e2d6fe44c078da6501fc7269aafcb8e0ac870`.
It was installed in another private unprivileged Pi virtual environment.
A temporary NoNewPrivileges loopback service passed actual HTTP password change,
old-session rejection, preserved unrelated viewer session, role retention and
private backup checks. Actual CLI recovery passed through an echo-disabled PTY;
no generated credential appeared in captured prompt output or the report. The
service was stopped after acceptance; test data remains retained.

The same Pi artifact inspected the earlier fixture's real rclone metadata and
correctly reported alias backends, exit2 and no cloud acceptance without changing
configuration. [Account/readiness result](acceptance/pi-accounts-2026-11-13.json).
The restricted public dev3 service and its accounts were not replaced by this
trial. Its dev5 upgrade is prepared with backup/rollback and awaits a privately
authenticated administrator terminal.


## Secure-prompt follow-up — development 0.1.0.dev6

Review found Python getpass can fall back to potentially echoed input without a
controlling terminal. Dev6 makes that warning fatal before fallback. Both account
initialization and recovery reject insecure input without creating/changing auth.
The full local suite now passes **177 Python tests**; all six Rust tests still pass.
GitHub's dev5 checks also passed the reviewed Debian-container ARM64 cross-build.

The new dev6 wheel SHA-256 is
`5167a35b013fe966b6b30249ef5a90d598a2fd1be705b90ff1eb570370868e32`.
It was checksum-verified and installed in a separate private Pi venv. Actual
loopback HTTP password change, session revocation/isolation, private PTY CLI
recovery and real-rclone local metadata checks passed again. An actual invocation
without a secure terminal refused with exit2 and no new state. No production
account/service/network was changed. [Result](acceptance/pi-secure-prompt-2026-11-13.json).

The latest private upgrade transaction targets dev6, validates its exact artifact,
backs up private state and prior unit configuration, prepares the new version
before stopping dev3, and includes checked process/HTTP rollback. It has not run:
a privately authenticated administrator terminal remains required. Static and
mocked transaction helper checks do not establish an executed upgrade.


## Restricted dev6 deployment and public HTTPS follow-up

Private root authentication was restored. The reviewed transaction validated the
exact dev6 artifact, saved private state/unit and checked rollback commands,
prepared the new root-owned environment, checked app construction as the actual
service identity, then replaced only the transient project candidate. Expected
systemd identity/MainPID/argv and nonredirecting loopback HTTP health passed.
Dev3 remains retained; installation did not change account passwords, USB/Wi-Fi
settings, sudo policy or boot enablement. [Deployment record](acceptance/pi-restricted-dev6-2026-11-13.json).

Through public HTTPS, dev6 passed owner/viewer logins, all three existing QA-file
byte/SHA checks, anonymous API rejection, viewer upload rejection, missing-CSRF
rejection, logout revocation and Account-form access. The file checks reused
existing staged QA samples; no real cloud account or new public upload is claimed.
Wi-Fi stayed connected. USB NM state was30 (disconnected) at the sampled request;
this test does not establish working USB IP connectivity. The upgrade changed no
network profile. [HTTPS record](acceptance/pi-https-dev6-2026-11-13.json).

## Rust paused-worker scheduling

Ten Rust tests pass, including policy/error backoff, prompt response to changed
inventory/configuration, reconciliation and invalid reports. A virtual 300-second
test invokes real fixture child processes: 31 baseline launches versus six with
backoff. Formatting, clippy and the ARM64 release build pass.

On the physical Pi, a separate twelve-second window at a five-second poll produced
three baseline child launches versus one optimized launch. New-ready and config
changes woke the optimized watcher in 3.033 and 5.055 seconds respectively; its
parent RSS/high-water RSS was 1,784 KiB. Only disposable fixture children ran; no
real worker, cloud account, USB setting or Wi-Fi setting was used. Fixture data
remains retained. [Result](acceptance/pi-rust-paused-2026-11-13.json).
This is a process-launch comparison, not an energy or whole-appliance benchmark.
