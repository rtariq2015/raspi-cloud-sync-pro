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

## OAuth connection foundation — development 0.1.0.dev7

The full local suite passes **195 Python tests**, with no skipped tests, using
the verified rclone 1.76.0 binary against temporary local storage. Eighteen new
tests cover Google/Dropbox PKCE and fixed endpoints, owner/CSRF/session binding,
expired/replayed callbacks, Strict-cookie omission/resumption, credential changes
during exchange, bounded transports, private no-replace storage and sanitized
errors. Ruff F/E9, JavaScript syntax, compilation and whitespace checks pass.
An independent source review and targeted rerun found no blocking defect.

The packaged dev7 wheel includes the callback template/script and OAuth module,
with no credential files. SHA-256:
`ef355d2c404e1329ecc6e35362814b9eafa4da7768072c13a9969fd4ca3792d4`.
This is source/package acceptance; real browser/provider consent, registered
stable callback hosting, refresh, folder selection, rclone handoff and actual
cloud backups remain unaccepted. The live dev6 dashboard has not been replaced.

That exact wheel also passed a separate unprivileged trial on the Pi: actual
ephemeral loopback HTTP checked owner/viewer access, CSRF and missing-setup
refusal. Flask's HTTPS-origin test client on the Pi checked a fake provider
exchange, callback handoff/replay, private token storage and password revocation
during exchange. The unrelated viewer remained valid. These cookie/provider
semantics were simulated; no real provider request, browser consent, production
account or service replacement occurred. Disposable fixture state stays retained.
[Pi result](acceptance/pi-oauth-dev7-2026-11-13.json).

## Actual Pi Samba protocol acceptance

An isolated Samba 4.22.11 server on the physical Zero 2 W passed encrypted
SMB 3.1.1 checks using disposable files. An active writer deferred staging before
batch creation; the snapshot handle blocked new writes/deletes; the backup
identity could not write; post-close staged bytes and SHA-256 matched. An injected
read failure retained an incomplete batch, originals stayed present and a changed
version preserved the previous snapshot. [Result](acceptance/pi-samba-protocol-2026-11-13.json).

The server bound only `127.0.0.1:1445` and stopped after the test. Official Debian
packages were privately extracted, without package maintainer hooks or global
Samba configuration. Fresh private account-database backups preceded two new
locked/no-login QA identities; all prior account records stayed unchanged. QA
files, identities and backups remain retained; no boot service was enabled.

Firmware power flags were `0x0` before and after. Wi-Fi stayed connected; USB
carrier stayed present with its existing DHCP state70. No network, firewall,
gadget setting or production hardware gate changed. This establishes the
same-Pi protocol contract, not Windows/USB driver or transfer behavior, electrical
power-failure recovery, whole-appliance memory limits or real cloud backup.

Earlier private harness attempts failed before protocol acceptance: a sandboxed
firmware-device read, a chmod on read-only package code, then an unsupported
`useradd -K CREATE_MAIL_SPOOL=no` override. The final trial moved the read-only
power probe outside the device sandbox, prepared only reviewed public-code
permissions before sandboxing, and used Trixie's existing no-mail-spool default.
The last failed attempt's proven empty QA group was validated and reused; no
existing group was deleted or recreated. Failed-attempt evidence remains private.

## Dropbox adapter — development 0.1.0.dev8

The full local suite passes **200 Python tests** with the verified real rclone
binary and no skipped tests; the Rust component remains at ten passing tests.
Dropbox configuration/readiness requires the direct `dropbox` backend and reports
aliases/type mismatches without claiming cloud authorization. Uploads retain
immutable version paths, wait for synchronous batch commits and request an 8 MiB
chunk buffer. Verification downloads and hashes actual remote bytes.

New tests cover disabled defaults, strict/secret-free configuration, private
credential-file enforcement, commit options, failed verification/source retention
and actual rclone command compatibility against a temporary local alias. That
alias is explicitly rejected as proof of a Dropbox backend by the readiness test.
No Dropbox account, credential refresh or remote service behavior was tested.
CLI planning confirms the separate example stays disabled with deletion false;
Ruff and whitespace checks pass.

The dev8 wheel SHA-256 is
`ce2cc75295dec88ddd95f5337ed5bd23d3fc1e02f80c1a78d4d28edebe58edfb`.
It has not replaced the running protected dev6 dashboard. The accepted dev7 Pi
OAuth fixture is retained separately, and the dashboard-to-rclone token handoff
and real cloud-account acceptance remain pending.

The exact dev8 wheel subsequently passed a separate unprivileged Pi trial with
the official ARM64 rclone binary: three local-alias files (including empty and
Unicode names) verified, deliberate remote-fixture corruption was rejected, and
all source batches remained retained. Readiness correctly refused to call the
alias a direct Dropbox backend. No actual Dropbox API/chunk-buffer behavior,
credentials, production service or network setting was exercised.
[Pi result](acceptance/pi-dropbox-dev8-2026-11-13.json).

## Restricted dev8 deployment and HTTPS acceptance

The protected dashboard now runs the exact dev8 artifact under its existing
restricted service identity. The first preparation stopped safely before service
replacement because umask 0077 made the new public release code inaccessible.
A read-only identity probe confirmed that cause. The continuation verified the
installed source/assets and bytecode against the tested wheel, took fresh private
backups, normalized only public release/venv access, then passed actual-identity
app construction and exact process/nonredirect HTTP health. Private artifact,
account/token state and backup permissions stayed unchanged. Dev6 is retained
with checked rollback commands. [Deployment record](acceptance/pi-restricted-dev8-2026-11-13.json).

Actual public HTTPS repeated owner/viewer authentication, all three retained QA
file bytes/SHA checks, anonymous rejection, viewer upload rejection, CSRF and
logout revocation. Missing OAuth-client setup was reported without enabling
uploads. No new file upload, real cloud account, boot enablement or network change
is claimed. Wi-Fi remained connected; USB was still in its existing DHCP state70.
[HTTPS record](acceptance/pi-https-dev8-2026-11-13.json).

## First real Google Drive QA backup — development 0.1.0.dev8

On the physical Zero 2 W, real Chrome authorization passed the dashboard's
owner/session-bound PKCE and cookie-return path. A private, controlled QA run
then used the direct rclone `drive` backend and the existing backup engine to
upload exactly one known `Sample-report.json` into a unique app-owned QA folder.
The job reached `verified` after independently downloaded remote contents matched
the fixture's SHA-256. The original and private QA configuration remain retained;
the source credentials stayed unchanged.
[Sanitized evidence](acceptance/pi-google-drive-2026-11-13.json).

Approved Wi-Fi was observed before and after, and rclone was bound to its Wi-Fi
IPv4 address. Wi-Fi supplied cloud Internet without Windows Internet Sharing.
This does not establish an egress-firewall guarantee. No USB/network settings
changed, no automatic deletion occurred and firmware reported `throttled=0x0`
afterward. No duration, throughput, endurance or electrical power-failure claim
is established by this one-file proof.

The Google application remains External/Testing, with its seven-day Drive
refresh-token limitation. The OAuth-to-rclone folder/test/enable wizard and a
persistent recurring worker remain unfinished; background uploads stay disabled.
Large/changed/interrupted transfers, refresh, outages and other providers still
need real-account acceptance.

## Inactive service-template network diagnostics

The dashboard and Rust worker unit templates now allow `AF_NETLINK` so read-only
`ip route` observations can work inside their address-family restrictions.
Independent disposable-child seccomp probes reproduced `EAFNOSUPPORT` and
`ip route` exit 1 without that family; allowing it produced a successful
`NETLINK_ROUTE` socket and `ip route` exit 0 with valid JSON. Empty capability
bounding sets and `NoNewPrivileges` remain; neither `CAP_NET_ADMIN` nor
`AF_PACKET` was added.

Focused reruns passed nine device-mode, twelve staged-worker and six
network-diagnostics tests. `systemd-analyze verify` passed temporary unit copies
with only `ExecStart` replaced by `/usr/bin/true`. This template fix installed or
started no units and changed no network settings or Pi configuration.

## Private Google preparation — development 0.1.0.dev9

The full local Python suite passed **229 tests**, including real rclone against
temporary local storage. Twenty-seven new preparation tests cover framed channel
limits, standard-stream aliases, deadlines, strict client/origin/scope bindings,
credential-safe errors, private filesystem paths, no-replace publication and
retained interrupted data. Two additional CLI tests check standard-stream refusal
and exception redaction. Python 3.9 grammar, compilation and focused Ruff checks
pass; the CI matrix provides separate runtime compatibility evidence.

The reviewed wheel was installed only into a new isolated QA environment on the
Pi. The live dev8 dashboard was not upgraded. Two existing locked no-login QA
identities ran the actual exporter/importer commands with synthetic credentials,
cleared supplementary groups, no-new-privileges and no effective capabilities.
The complete private handoff and configuration hashes passed. Real kernel reads
across their private state boundaries were denied; unsafe source permissions and
an attempt to replace an existing preparation were rejected. No real cloud
credentials were read, no cloud request was made and no service was enabled.
[Sanitized receipt](acceptance/pi-google-handoff-dev9-2026-11-13.json).

The first fixture's root-owned 0711 container prevented the descriptor walk from
opening ancestors read-only. Direct probes confirmed EACCES for that parent and
successful access to each identity's own 0700 leaf. The failed fixture remains
retained. The accepted continuation used a new root-owned 0755 container around
unchanged 0700 private leaves; it did not relax real application state. It reused
the frozen public installation read-only after checking wheel-provided source,
assets and metadata, rejecting extra bytecode and directory symlinks.

Wi-Fi and default routes were preserved, USB carrier remained available, accounts
were unchanged and firmware reported `throttled=0x0` before and after. USB
addresses differed between observations; this establishes no cause and does not
resolve the previously observed automatic profile switching. No network/boot
settings were changed by this trial. These synthetic tests do not establish
real-account import, refresh, folder access or unattended backup readiness.

The first dev9 CI run exposed a fixture-only Python 3.9 issue: that interpreter
does not expose `fcntl.F_SETPIPE_SZ`. The backpressure test now fills an ordinary
pipe with bounded nonblocking writes, leaves room for a partial frame, and checks
the timeout and restored flags. All 33 preparation/CLI tests then passed under
actual CPython 3.9.25 in a non-root, network-disabled, read-only container. The
application module and the Pi-tested wheel were unchanged by this correction.

## Owner-requested Google preparation — development 0.1.0.dev10

The local suite passed **326 Python tests**, including actual rclone against
temporary local storage and seven Node VM tests of the dashboard's actual
preparation polling functions. The polling tests cover temporary lock/network
failures, bounded retries, terminal states, and stopping after a result. The new
workflow keeps preparation separate from a cloud test and upload enablement.

An additional **108 Google/CLI tests passed under CPython 3.9.25** in a non-root,
network-disabled, read-only container. These include the source/worker config
binding, private channel lifecycle, expiry, owner credential revocation, failed
transactions, conservative status, CLI dispatch and private error suppression.
Dashboard HTTP tests use fake authorization and run locally with the optional
Flask dependencies. Focused Ruff checks, JavaScript syntax, Python 3.9 grammar
and whitespace checks pass.

The reviewed dev10 wheel is installed in a separate protected Pi prefix. Its
interpreter is an actual copied file, with protected existing dashboard
dependencies reused offline. Archive-provided code/assets/metadata and runtime
versions were checked. This installation changed no accounts, live services,
private dashboard state or network settings. Live dev8 remains active while the
new integration is accepted.

The new root broker and path unit templates are disabled by default. Static
tests check the explicit approval gate, private network namespace, restricted
address families/capabilities, process cleanup and pinned interpreter. A local
`systemd-analyze verify` check used temporary copies with the intentionally
absent executable replaced by `/usr/bin/true`; it does not establish actual
systemd sandbox or event-delivery acceptance on the Pi.

The native synthetic trial now passes on the Pi with the reviewed wheel:
owner-only CSRF-protected Flask HTTP handlers write intent; the actual root CLI
broker transfers authorization between distinct ordinary identities; source
HTTP handlers report the saved preparation as historical and untested. Actual
kernel reads across the private state boundary are denied. A separate recovery
fixture prepares configuration without recording its receipt, then the broker
reconciles it without replacing the existing files. A repeat of a completed
request is refused; source and prepared files remain unchanged.
[Sanitized receipt](acceptance/pi-google-setup-dev10-2026-11-13.json).

The initial trial was correctly refused with `google_broker_identity_unsafe`:
the reused Samba QA users had separate UIDs but shared their primary GID. That
failed fixture remains intact. A separate installation step backed up the
account databases, created locked no-login `raspi-backup` with a dedicated group
and private empty backup state, and verified every existing account/password
entry remained byte-for-byte unchanged. A new fixture requires distinct positive
UIDs/GIDs before creation; the application and wheel were not changed to bypass
the safety check. Account databases were unchanged throughout the accepted trial.

The live dashboard process/state metadata, Wi-Fi/default routes and USB carrier
were preserved. USB addresses were also unchanged during this observation;
that does not resolve earlier auto-switcher behavior. Firmware reported
`throttled=0x0` before and after. This exercised Flask `test_client`, not a TCP
server, browser, HTTPS proxy or real provider account. No real credential was
read and no cloud request, automatic upload or service activation occurred.

### Actual systemd capability and cleanup checks

Three isolated systemd probes on the Zero 2 W reproduced a service-policy issue
on systemd 257.13: explicitly setting `User=root` with the tested restrictions
removed effective `CAP_SETUID`, preventing the broker's ordinary-child identity
transition. Using the system manager's default root identity preserved that
capability while retaining no-new-privileges, empty ambient capabilities and the
private network/filesystem restrictions. Both ordinary children then passed the
actual UID/GID drop and had no effective, permitted, inheritable or ambient
capabilities. Interpreter file-capability attributes were absent.

A separate comparison demonstrated that the coordinator also needs `CAP_KILL`
to stop its own tracked child process groups after their UID drop. Three bounded
capabilities produced permission denial; adding only `CAP_KILL` allowed the
actual cleanup function to terminate and reap its tracked child. These probes
created no authorization intents, used no provider credentials and stopped their
own temporary services. [Sanitized comparative evidence](acceptance/pi-google-setup-capabilities-2026-11-13.json).
They do not establish automatic preparation, event delivery or cloud access.

The service template now uses those four bounded capabilities and the default
root identity. It retries a failed preparation at most three times in a
five-minute start-limit window; the window exceeds three ordinary configured
start/stop timeout cycles. This also handles atomic pointer publication while
the owner's authorization lock is still held. No boot enablement is added.
Event delivery, contention recovery and slow-failure limits have separate
acceptance records below.

### Actual systemd event delivery and lock recovery

A fresh synthetic fixture now passes the actual systemd path/service flow on
the Pi. Atomic publication occurred while the source held its real account lock;
an inherited private pipe released that lock only after a completed failed broker
attempt was observed. The event then produced exactly one receipt on the second
attempt. A separate held-lock case produced three failures and a trusted PID1
rate-limit event. Releasing the lock caused no further automatic attempt;
explicitly resetting those temporary units and starting the retained request
prepared it successfully. Earlier configurations retained their inodes and bytes.

The accepted trial observed seven broker starts and three successful
preparations, kernel sandbox properties and cross-identity read denials. It
stopped its own QA units, retained all synthetic originals and preserved existing
accounts, the live dashboard, Wi-Fi and default routes. USB addresses differed
between observations; this does not establish a cause or resolve the existing
auto-switcher behavior. Power flags were zero before and after.
[Sanitized event/recovery evidence](acceptance/pi-google-setup-systemd-2026-11-13.json).

The first contention fixture used a fixed two-second delay that did not actually
force failure on this Pi. A slow-failure fixture also expected `start-limit-hit`
while this systemd retained `exit-code` alongside an explicit rate-limit event.
Those incomplete fixtures remain retained. The accepted event fixture uses
observed failure barriers and trusted PID1 events instead of either assumption.
Its HTTP handlers use Flask `test_client`, not a browser or TCP server, and all
credentials are synthetic. Real-account preparation, slow-failure timing and
production deployment have separate acceptance checks.

A fresh slow-failure trial also passes. Its only command is a fixed Python
sleep followed by exit status 2, under the final service policy. Three actual
executions lasted 25.407, 25.340 and 25.370 seconds; the span reached 78.498
seconds. Trusted PID1 events proved that the next start was rate-limited, and
a further 3.152-second observation saw no fourth start. Three unique live kernel
sandbox samples were flushed to private records during execution. The temporary
service stopped, its empty provider state stayed empty, and existing accounts,
the live dashboard, Wi-Fi/default routes and observed USB addresses were
preserved. Power flags stayed zero.
[Sanitized slow-failure evidence](acceptance/pi-google-setup-slow-retries-2026-11-13.json).
This fault command does not invoke the broker, exercise path events or establish
timing guarantees for uninterruptible kernel I/O.

### Live protected upgrade and actual owner preparation

The production preparation files were installed inactive first, then their
future-request watcher was activated after validating all three accepted private
trial receipts. It started no broker until an owner request arrived. Neither
unit is enabled at boot. The protected dashboard was upgraded from dev8 to the
reviewed dev10 wheel with private backups and a standalone service rollback
prepared beforehand. Its exact process, ordinary service UID, empty capability
bound, no-new-privileges, mode mask 0077 and loopback HTTP health passed. The old
dev8 installation remains retained; a live rollback was not exercised.

Preflight first refused a missing configuration directory, then an audit that
incorrectly treated a root-owned internal `lib64` symlink's 0777 mode as writable
ordinary code. The new private directory was separately recorded; the audit now
checks link ownership, resolved target and protected ancestors without changing
the link. Three old QA upload manifests also had mode 0664. Each was backed up
before tightening it to 0600 in place; contents, inodes and owners were preserved.
The source uploader now explicitly creates private manifests independently of a
permissive process umask, with a real filesystem regression. The new live service
also sets umask 0077. No account credential or USB/Wi-Fi setting was changed.

After the restart cleared old sessions, the owner signed in privately again.
The actual HTTPS Backups form submitted one request, received HTTP 202 and
reached historical `prepared_untested`. One mode-0700 backup-owned preparation
with mode-0600 files was independently observed without opening its credential
contents. The existing three QA files remain listed. Account/folder verification,
current worker validation and upload enablement remain false. Preparation made
no cloud request or token refresh. Authorized upgrade backups do contain private
credential copies; the broker itself keeps its separate direct-pipe contract.
[Sanitized live acceptance](acceptance/pi-google-setup-live-dev10-2026-11-13.json).

Wi-Fi/default routes and USB carrier were preserved. USB addresses differed
during deployment, consistent with the separately observed auto-switcher issue;
this does not establish causality. Firmware reported zero throttling flags.

### Separate operational generation foundation

The complete local suite now passes **351 Python tests**, including actual rclone
against temporary local storage. Twenty-four new filesystem/history tests cover
source-approved preparation bindings, private no-replace generation publication,
retained failures, unchanged prepared bytes/inodes and distinct destination job
identities. A real SQLite history test verifies that an old verified job is
deduplicated for its old destination but queued for a new folder generation.
Inspection reports mutable seed equality only, always disabled and untested;
it never authorizes refreshed bytes or current cloud access. Independent review
and Python 3.9 syntax checks pass. This module has no live CLI/dashboard/worker
integration and ran no provider request.


## Private Google controls and completed web-batch transfer — source foundation

**402 Python tests pass locally**, including the real rclone test against
temporary local storage. No cloud credentials or real network settings are used
by the new tests. Rust is unchanged; its previous 10-test acceptance remains.

Twenty-four Google control tests cover durable Test/Enable/Disable decisions,
owner/session guard coverage through publication, current credential/source
bindings, request and Test freshness, receipt validation, token-independent
operational settings and fresh per-batch admission. They distinguish a historical
coordinator receipt from actual account access or a currently running worker.
Disable denies subsequent admission even when the cloud is unavailable.

Twenty-seven transfer tests stream selected completed web batches through
anonymous inherited descriptors into separately anchored staging. They cover
Unicode/nested/empty content, bounded payload chunks, independent disk hashes,
completion-frame/EOF requirements, source changes, path/descriptor rejection,
partial disconnects, publication races and retained failures. Independent review
reproduced two false-success cases: replacement of the configured target root
during publication and expiry while the final fsync completed. Both now fail
without deleting retained data; targeted regressions and bounded recursive-flush
tests pass. These tests use ordinary local identities and temporary files, not
a physical cross-identity Pi deployment.

Changed Python files pass Ruff F/E9 and Python 3.9 grammar checks. These components
make no cloud or service calls and are not wired into the live dashboard or
executor. They do not establish actual Google refresh, automatic backup, Wi-Fi
egress enforcement or USB/Samba acceptance. See [the contracts](GOOGLE_CONTROLS.md).
