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


## Private operator CLI integration

**417 Python tests pass locally**, including real rclone against temporary local
storage. Fifteen new CLI tests exercise existing Google intent export, receipt
ingestion and default-denied per-batch admission through real private pipes,
plus completed web-batch describe/export/import, independent bytes and hashes,
no-replace reuse, invalid-receipt privacy, broken-pipe cleanup and accepted
descriptor closure. All 34 CLI tests pass. Normal stdout omits private batch
metadata; describe uses a separate framed inherited output descriptor.

The CLI cannot mint owner/session proof or Test/Enable/Disable requests and
starts no network process, service or cloud worker. These commands are not yet
installed in the current protected dev10 Pi runtime. Independent review, Ruff
F/E9, Python 3.9 grammar and diff checks pass.


## Fixed HTTPS recovery and persistent services on the Pi

A read-only inspection established that the Pi had rebooted, the transient
project services were gone and the old Quick Tunnel returned Cloudflare error
1033. The cause of the reboot was not established. Project releases, accounts,
private Google state and QA uploads remained present.

The official ngrok ARM64 agent 3.39.13 was verified against its publisher's
manifest and copied into protected versioned storage. A private browser copy
interceptor transferred the account token directly into the reviewed SSH
installer without writing the laptop clipboard or putting the token in command
arguments. A root600 file and systemd LoadCredential supply it to a separate
DynamicUser process; inspection and agent stdout/stderr are disabled.

The initial transient service passed a literal credential-path specifier instead
of its resolved path. The protected configuration passed a direct native check;
using the explicit runtime credential path fixed startup. The agent then passed
actual process, capabilities, namespace, seccomp and exact-owned-loopback API
checks. The restored protected dev10 dashboard passed local/public HTTP login
checks; actual Chrome passed the provider warning and reached the CSRF login
form. This does not establish fresh owner login or off-LAN phone acceptance.

The persistent unit transition passed native systemd-analyze verification and
process/public-health checks. Two attempts rolled back during enablement
validation. The instrumented repetition showed that the loaded manager still
reported disabled after the correct root-owned startup links were created.
Both automatic rollbacks stopped/disabled only the owned new services, restored
the previous transient commands, and retained every file. Separate inactive-unit
probes passed enablement, so the final continuation instrumented each active-unit
check rather than assuming the initial cause. Its trace recorded the exact
startup link together with the stale loaded disabled state.

The reviewed successor reloads the manager after enablement. It then passed exact
link checks, independent is-enabled checks, loaded enabled state, unchanged
before/after runtime proof and repeated local/public health. Only the dashboard
and connector units are enabled. Native preservation checks passed for Wi-Fi and
default routes, USB carrier, power, account/credential metadata, preparations and
uploads. Root-private backups and a standalone guarded rollback precede changes.
Twenty-two private successor-installer tests and independent review pass. These
are separate from the 417 repository tests. No reboot, OS/USB/network change or
cloud activation was performed.

Persistent startup is configured; actual reboot, prolonged outage and physical
off-LAN phone tests remain. At this acceptance point, Google setup requests stayed
disabled pending OAuth callback reconciliation. The later migration is recorded
below. The public address, tokens, account details
and private backup paths are omitted from the
[acceptance record](acceptance/pi-fixed-https-recovery-2026-11-13.json).

## Google callback migration and guarded Test controls

The existing dedicated Google OAuth client now retains both its original
callback and the new fixed HTTPS callback. Save persistence was verified by
reloading and reopening the exact client. Client credentials, scopes, Testing
status and billing were unchanged. A separately reviewed Pi helper changed only
the local client public origin, with a flushed private backup and standalone
guarded rollback prepared first. Seventeen private helper tests passed. The
actual write and ordinary-dashboard-identity readiness check passed; saved
authorization remains untested and the previous preparation is historical.
No service was restarted, account password changed or cloud request made by the
Pi migration. Broker restoration and fresh owner preparation remain necessary.
[Sanitized migration evidence](acceptance/pi-google-origin-migration-2026-11-13.json).

**457 Python tests pass locally**, including actual rclone against temporary local
storage. Forty new tests cover the source-only owner control integration and
private Google Test worker. Fifteen HTTP tests exercise the default-off flag,
owner/viewer/CSRF boundaries, exact bounded JSON, live password/role/logout
revocation through publication, stale source and failed/fresh Test prerequisites.
Five Node tests run the actual dashboard JavaScript, including transient status
failures, fixed deadlines, terminal outcomes and conservative labels.

Twenty private worker tests cover default refusal before state access, exact
root-peer release framing, protected executable validation, unchanged immutable
preparation, separate generations, folder conflicts, strict mutable settings,
forced expiry/refresh, downloaded fixture hashes, retained failures and child
timeouts/output bounds. Cloud transport is fake; filesystem/history and bounded
child execution are real local operations. These tests do not establish real
Google refresh or production Wi-Fi egress confinement. Independent review, Ruff
F/E9, Python 3.9 grammar, actual JavaScript syntax and diff checks pass. Rust is
unchanged and retains its previous ten-test acceptance.

The live Pi still runs protected dev10 with both Google request flags off.
The new tester has no installed coordinator and refuses without a private
root-authorized per-run release. A coordinator must prove actual confinement
before releasing credentials; a root frame or systemd property alone is not
network proof. Automatic cloud backup remains disabled.

## Partial native Wi-Fi restriction qualification

A credential-free trial passed on the physical Pi with kernel
`6.18.60+rpt-rpi-v8`, systemd `257.13-1~deb13u1` and the protected official
rclone 1.76.0 binary. Twenty private probe tests and independent reviews passed
before execution. A flushed root-private plan preceded all four fresh transient
units. Each ran as an existing restricted QA identity with zero capabilities,
no-new-privileges, seccomp, IPv4-only socket creation and no inherited network
descriptors. Actual IPv6, Unix, netlink, packet and io_uring attempts were denied.

While the strict process was held, root queried its actual cgroup ingress and
egress BPF attachments. Both referenced the same exact single-wlan0 map and the
same read-only `is_allow_list=true` byte. The proof was unchanged before and after
the network probes. Loopback TCP and UDP succeeded in surrounding positive
controls and failed in the strict process. Direct UDP/TCP DNS to the configured
Wi-Fi resolver and a bounded 64-byte HTTPS read from the official rclone source
succeeded in that process. Its private resolver and clean environment could not
use an inherited proxy. The policy interpretation follows the
[pinned systemd implementation](https://raw.githubusercontent.com/systemd/systemd/v257.13/src/core/bpf/restrict_ifaces/restrict-ifaces.bpf.c).

All owned QA units stopped. Wi-Fi identity, addresses, routes and DNS, USB carrier
and observed addresses, account/private-configuration metadata, live dashboard
and connector processes, protected binaries and healthy power flags were
preserved. No existing service was stopped, network setting changed, credential
read, provider called or USB probe sent. No memory-limit enforcement is claimed.
Private records and the fixture remain retained.

This accepts only the exact credential-free IPv4 platform fixture. No eligible
USB peer was available in the preceding read-only inspection; none was guessed
or discovered with probes. Full USB denial still requires successful USB positive
controls surrounding its negative test. The result explicitly keeps full USB
acceptance, strict egress acceptance and credential release false. Each future
credentialed executor needs its own held runtime proof and release gate.
[Sanitized native evidence](acceptance/pi-wifi-rni-partial-2026-11-13.json).

## Private Google Test CLI

**477 Python tests pass locally**, including actual rclone against temporary local
storage. Twenty new tests exercise the private `google-test` command with real
anonymous pipes and connected Unix streams. They cover receipt-first validation,
missing root-gate refusal before request/state access, channel/stdio aliases,
wrong-direction and malformed channels, frame/EOF requirements, private receipt
delivery, safe public output, parser/error suppression and accepted-descriptor
cleanup. The successful worker is stubbed; no Test fixture reaches a provider.

All 54 CLI tests, Ruff F/E9, Python 3.9 grammar and diff checks pass. Independent
review cleared the exact changed files. This command supplies coordinator
plumbing; it cannot create owner/session proof or root release, accept kernel
confinement, enable a worker or bypass the Test worker's default refusal. It is
not installed in the protected dev10 Pi runtime.

## Native four-channel worker handoff and lifecycle

A credential-free native primitive now passes with systemd 257.13 on the Pi.
Four separate `OpenFile=` properties connect root-private Unix listeners before
the ordinary QA worker starts. The child received four named descriptors above
stdio, authenticated its root peer, verified its actual UID/GID, zero
capabilities, no-new-privileges and seccomp, and could not create a new Unix
socket. The parent matched its PID against PID1 and opened a pidfd. All four
nonce round trips and EOF checks passed under an actual wlan0-only BPF policy.
The source of the launch ordering is
[systemd's pinned executor](https://raw.githubusercontent.com/systemd/systemd/v257.13/src/core/exec-invoke.c);
the [OpenFile contract](https://www.freedesktop.org/software/systemd/man/257/systemd.service.html#OpenFile=)
is not an instruction to modify the laptop.

After the child exited successfully, PID1 still reported active/exited because
`RemainAfterExit=yes`, but its ControlGroup field was empty and the independently
retained active cgroup path no longer existed. Current BPF enforcement therefore
could not be inspected after exit. The first follow-up diagnostic did not collect
that raw observation; its successor retained the validated active path and
observed the missing directory directly. These fixtures and their records remain
retained. Ten private successor tests and independent review pass.

All temporary units stopped, with network/account/private-configuration metadata
and the existing dashboard/connector processes preserved. No credentials were
read, provider called, existing service stopped or boot setting changed. This
primitive used an ordinary QA identity, not the production backup identity, and
did not test the production source modules or their private DNS bind mount.
The new source completion protocol must verify the finished Test while the
worker is still alive, then permit only closure and exit. A saved QA report or
an active/exited unit property cannot authorize a future run.
[Sanitized handoff evidence](acceptance/pi-openfile-handoff-2026-11-13.json).

## Dev11 supervised Google Test source

**571 Python tests pass locally**, including actual rclone against temporary local
storage. The new coordinator, bootstrap and read-only policy inspector add 94
tests: 40 coordinator, 24 bootstrap and 30 kernel-inspector tests. Source version
was set to `0.1.0.dev11` for a new protected installation; the Pi dashboard still
ran dev10 at this source milestone. Its later deployment is recorded below.
No old release was overwritten by this version change.

The inspector tests cover root/native-64-bit/aarch64 gates, exact current
interface and cgroup identity, ingress/egress program/map ABI, shared allowlist
data, policy detachment/replacement, changing maps and descriptor cleanup. The
bootstrap tests cover private activation channels/root peers, framing, sandbox
and resolver checks, bounded DNS/HTTPS canaries, same-PID CLI entry and a bounded
live completion ACK. Kernel/PID1/provider boundaries in these tests are fake;
private streams, filesystem operations and deadline/error handling are local.

Independent coordinator review found issues that the first mock suite missed:
joining multiple OpenFile specifications, command launch after deadline expiry,
cleanup using the exhausted work deadline, incomplete pre-start manager-path
override inspection, and checking the coordinator import tree rather than the
configured worker interpreter's environment. These were corrected before any
native coordinator activation, with focused regressions. The worker must use
the same isolated protected interpreter and trusted site-packages. Cleanup has
its own budget and verifies no remaining process in the captured unit/cgroup.

The coordinator tests also cover fresh source/network checks, failed or changed
canaries/maps, root-gate bindings, completion ACK ordering, private receipt
validation, scoped stop failures and conservative failure feedback. A failed
historical receipt requires confirmed cleanup and another identical, unexpired
owner/source export; revocation, expiration or unconfirmed cleanup cannot write
one. No existing receipt is replaced and no success or Enable is inferred.

All six new files pass Ruff F/E9 and Python 3.9 grammar checks; diff checks and
independent reviews pass. The full suite was rerun after the final source fixes
and version change. Rust is unchanged. Actual production source-worker startup,
private DNS mount, per-run BPF/canary/gate/completion and real Google Test remain
separate native acceptance steps. Automatic backups stay off.

## Small-upload parser repair — 14 November 2026

The owner reported a 122.7 KB file rejected with a misleading 100 MiB message.
Two independent authenticated local reproductions established the cause:
`MAX_FORM_MEMORY_SIZE=32 KiB` was smaller than Werkzeug's 64 KiB multipart read
chunks. Its decoder checks that memory bound before emitting file events, so a
32,768-byte file and a 122,700-byte file returned HTTP 413 before staging.
The [pinned decoder](https://raw.githubusercontent.com/pallets/werkzeug/3.1.9/src/werkzeug/sansio/multipart.py)
and [parser](https://raw.githubusercontent.com/pallets/werkzeug/3.1.9/src/werkzeug/formparser.py)
confirm the mechanism. The Pi's Flask 3.1.3 and Werkzeug 3.1.9 versions match the
reproduction environment. No proxy or cloud service was needed to reproduce it.

The narrow repair raises the bounded parser cap to 128 KiB, leaving room for
the chunk and retained boundary bytes. It preserves the separate 100 MiB file
limit, 102 MiB Flask/Waitress request limit, eight-part bound, CSRF, owner role
and retained partials. The browser's generic HTTP 413 message now describes a
rejected request rather than asserting that every failure exceeds 100 MiB.

**578 Python tests pass**, including seven new authenticated multipart tests
using real temporary files and streamed large inputs/downloads. They cover
32/64 KiB, 122,700 bytes, approximately 122.7 KiB and 1 MiB round trips with exact
bytes/SHA-256, exactly 100 MiB success, 100 MiB plus one byte refusal, oversized
requests/text fields, the eight/nine-part boundary, CSRF and viewer denial.
The oversized-file partial remains retained and is never published ready.
ResourceWarnings-as-errors, independent review, Ruff, Python 3.9 grammar and
JavaScript syntax checks pass. These are source/local HTTP results; native
deployment and the actual owner's retry are recorded separately below.

## Protected dev11 dashboard deployment — 14 November 2026

The fixed wheel (`2060882b…`) was installed into a new root-owned dev11 prefix,
preserving dev10. Exact wheel modules, dependency identities, copied interpreter
and imports/console execution as both ordinary service identities passed. The
inactive installer changed no existing service, account, credential or network
configuration. Its private helper passed 21 local tests.

A separate, reviewed dashboard upgrade helper passed 25 local tests, including
real Waitress multipart requests and guarded unit-only rollback cases. On the
Pi it started an isolated loopback Waitress instance as the dashboard identity
with synthetic private accounts/state. Authenticated 122,700-byte, 125,645-byte
and 1 MiB uploads returned HTTP 201; downloads matched bytes and SHA-256.
Oversized text fields and a ninth multipart part returned HTTP 413. Both Google
request routes remained disabled. The temporary unit stopped; test data is retained.

After native QA, a durable private backup and standalone rollback were saved.
Only the persistent dashboard unit's interpreter path and description changed.
The dashboard restarted into dev11; its fixed public HTTPS and local login pages
passed. The connector retained its process/invocation, and account/upload/private
configuration metadata, Wi-Fi/default routes, USB addressing/carrier, boot
enablement and healthy power status were preserved. The old prefix is retained.
Rollback restores the service definition, never old account or file contents.

The earlier installation transport failed before creating a prefix or backup.
Read-only reconciliation verified no installer remained before a guarded retry;
both existing service invocations and Pi uptime were unchanged. No reboot or
network reset was used, and the interruption's cause was not established.

Post-upgrade read-only checks confirmed the new process, stopped QA unit, rollback
files, unchanged connector and power status. The owner's password was not used
for these checks, and their actual file retry remains unobserved. The exactly
100 MiB boundary was tested locally, not on the physical Pi. No cloud backup,
Windows share or USB mass-storage acceptance is implied by these upload checks.
[Sanitized native evidence](acceptance/pi-dashboard-upload-dev11-2026-11-14.json).

## Actual-source Google worker preflight — 14 November 2026

The first credential-free trial imported the protected dev11 coordinator,
bootstrap and inspector and attempted their real systemd startup path. It
refused before creating a worker. Read-only diagnosis established that
`systemctl show --no-pager UNIT` returned success but omitted empty properties,
including `DropInPaths`. The coordinator required an explicit empty value, so
its fresh-unit conflict check refused the missing key. A direct `show --all`
query on the same Pi returned those keys explicitly with empty values.

The retained failed fixture's unit was independently confirmed not-found,
inactive/dead with no main/control PID, invocation or cgroup. Worker logs were
empty; no valid gate, credential release or provider request occurred. Existing
services, private configuration/account metadata, Wi-Fi/routes, USB and power
were preserved. The fixture's conservative cleanup result was false because its
capture-based cleanup could not capture an invocation that had never existed;
the later read-only inspection confirmed there was no worker to stop.

The narrow source correction requests `--all` and permits no-op cleanup only
after confirming exact absent-unit identity, empty runtime/load-path fields and
no process in its known cgroup. Three new tests cover explicit empty properties,
never-created cleanup and inconsistent/foreign/live resource refusal. All 43
coordinator tests pass, with independent review, Ruff and Python 3.9 grammar.
The corrected coordinator is not in the currently installed wheel. A new
protected build and native trial remain required; actual bootstrap/canary/gate
and Google account acceptance are still pending.
[Sanitized preflight evidence](acceptance/pi-google-test-source-preflight-2026-11-14.json).

## Dev12 operator and offline capture source — 14 November 2026

**615 Python tests pass locally**, with actual rclone against temporary local
storage. Dev12 adds ten one-shot coordinator CLI tests, the three native-facade
regressions above and 24 capture-only runtime tests. Eleven Rust tests pass;
formatting, clippy and the ARM64 cross-build pass. A reproduced test-fixture
`ETXTBSY` race was corrected without changing the production binary, as recorded
in [Rust evaluation](RUST_EVALUATION.md).

The new private `google-test-broker` command runs the existing coordinator once.
It accepts a protected configuration, emits only a strict fixed report and masks
private parser/runtime exceptions. Its dev12 configuration is disabled and its
service example is uninstalled, with no watcher or boot enablement. The proposed
administrative capability set is not natively qualified. A source command is
not evidence that an account, worker or live backup is accepted.

The [capture-only SMB API](SMB_CAPTURE.md) runs as the ordinary backup identity
and rechecks a separate root-protected readable policy/approval before each SMB
open. Credentials/index/queue stay private to that identity. Its optional reader
guard preserves the original global gate by default. Completed snapshots reuse
the existing encrypted deny-write/delete handle and successful-CLOSE publication
contract; no provider is constructed. Tests use temporary files and simulated
SMB handles, retaining failed/old versions and validating private filesystem
boundaries. A real unprivileged read of an existing nonsecret root-owned public
JSON file checks authority-file readability without privileged laptop writes.

Review reproduced starvation when 32 busy/oversized files consumed every scan.
A durable cursor in the existing private SQLite index now rotates the bounded
inventory; a 33-file regression reaches the later completed file on the next
runtime invocation while earlier writers stay busy. The verified-batch cache,
inventory, per-scan attempts and child probes are bounded. In-flight kernel/SMB
operations retain the documented timeout limits, rather than claiming a hard
wall-clock guarantee.

Independent reviews, changed-file Ruff F/E9, Python 3.9 grammar and whitespace
checks pass. The live dashboard remains dev11 with the accepted upload repair;
dev12, the corrected coordinator and this new capture runtime are not deployed.
Native coordinator/gate/Google acceptance, Samba provisioning, stable USB host
addressing, Windows mapping and dashboard projection of SMB batches remain.

## Dev12 native worker and dev13 query correction — 14 November 2026

Dev12 was installed into a fresh inactive protected prefix from wheel
`59a5c3cf…`. Exact package/dependency and both ordinary-identity import/console
checks passed. The live dev11 dashboard, connector, private metadata and network
configuration were preserved; no dashboard restart occurred.

Its actual-source negative-gate trial created the scoped transient worker, but
the parent refused manager output before completing capture. Read-only diagnosis
found legitimate repeated `OpenFile=` lines in full `systemctl show --all` output.
The strict duplicate-property parser therefore refused both capture and cleanup.
The worker exited with its fixed startup-refusal message, without a valid gate
or real prepared state. No separate executor fault has been established.

An independent guarded cleanup matched the exact saved unit name, description,
invocation, user, transient fragment and empty cgroup before stopping only that
failed unit. No worker processes remained, and all fixture data was retained.

Dev13 source retains `--all` and requests exactly the 21 single-valued fields
consumed by the coordinator. Repeated `OpenFile` and unrelated structured values
are excluded at the query. Unknown, malformed and duplicate selected properties
still refuse. Three new regressions cover projected real-shaped repeated output,
strict selected-property parsing and consumer coverage. All 46 coordinator tests
and the combined 618-test Python suite pass; independent review and static checks
pass. One initial existing simulated cleanup test returned a generic unavailable
result instead of the expected cleanup failure; subsequent local and independent
module runs passed, and that intermittent result's cause is unestablished.
No hardware or account conclusion is drawn from it.

The corrected source needs another protected build and native trial. Canary,
valid-gate, completion and actual Google acceptance remain pending; automatic
uploads and live USB sharing are still disabled.
[Sanitized dev12 evidence](acceptance/pi-google-test-source-dev12-preflight-2026-11-14.json).

## Dev13 native source-worker negative gate — 14 November 2026

The protected dev13 wheel (`998ef3e9…`, source `59d5c14`) was installed into a
new inactive prefix. Package bytes, existing dependency versions, copied
interpreter and imports/console execution as both ordinary identities passed.
The existing live dashboard and connector were preserved.

The isolated credential-free trial now passes with the actual installed
`google_test_broker`, `google_test_executor` and `wifi_confinement` modules.
Actual PID/pidfd, ordinary backup identity, argv, namespaces, capability-free
sandbox and four private root-peer descriptors were verified. The real private
resolver bind was read-only and matched its expected inode/content. Held kernel
program/map references were checked while the worker remained alive; direct
Wi-Fi UDP/TCP DNS and 64 bytes of official HTTPS content passed. Loopback TCP/UDP
was denied, with successful surrounding root controls and no received nonce.

After canary ACK/EOF, the helper sent only its hardcoded wrong-purpose gate and
a minimal fake request. The private CLI returned status 2 and its exact fixed
refusal, with no receipt bytes. Fresh empty QA preparation/operational leaves
kept identical metadata/content and produced no open/access/write events after
READY. Inotify does not observe metadata-only syscalls; mount-setup accesses
before READY are excluded from that event claim. No real prepared state,
credential release or provider test occurred.

The source cleanup stopped only its captured unit and confirmed no remaining
processes. Wi-Fi/routes/DNS, USB address/carrier, account/configuration metadata,
live service invocations and healthy power status were preserved. All private
fixture data and predecessor prefixes remain retained. There was no reboot,
format, packet capture, USB packet test or production dashboard restart.

This accepts the source bootstrap/canary/invalid-gate path, not the complete
coordinator API or its proposed administrative service, a valid credentialed
Google Test, token refresh/completion handshake, recurring uploads or physical
USB egress exclusion. Every future credentialed run still needs fresh owner,
worker, network and held-policy checks. Automatic backups and dashboard request
flags remain off; the working dashboard remains dev11 with its upload repair.
[Sanitized native evidence](acceptance/pi-google-test-source-dev13-2026-11-14.json).

## Dev14 capture and selected-batch integration — 14 November 2026

The final combined source suite passes **665 Python tests** in 32.590 seconds,
including actual rclone against temporary local storage. Ruff F/E9, Python 3.9
grammar checks for all fourteen changed Python files and whitespace checks pass.
The existing eleven Rust tests remain passing from the preceding unchanged Rust
build. Independent review cleared the capture CLI, control application, canonical
executable fix and upload/common deadline changes. No real cloud account was
used by the automated suite.

Added a one-scan `smb-capture` operator command with fixed sanitized results,
disabled configuration and uninstalled service/timer examples. Eleven new CLI
tests cover default disablement, dispatch, malformed results, private errors and
argument handling. The core SMB completion protocol is unchanged; no example
unit was installed or activated. USB/Windows sharing remains unaccepted.

The Google coordinator can apply an existing owner Enable/Disable decision
without starting a worker or contacting a provider. Source export and recorder
checks independently reject stale, changed, superseded and already-recorded
requests. Focused tests use real temporary account/workflow state as well as
fake process boundaries. The public `applied` result retains false upload,
account-verification and worker-readiness flags.

The private Google batch API requires a distinct consumed root-peer release,
bound to the selected ready batch and a fresh complete source admission. Tests
exercise real queue/engine state transitions with fake providers: downloaded
SHA-256 verification, refresh-compatible mutable token state, altered stable
settings, retained failures, path/manifest changes and cross-batch deduplication.
An optional engine/history selector prevents another pending root from being
claimed under the selected batch's permission. Previously verified content is
reported as historical reuse. These tests do not establish live cloud access or
enforced Wi-Fi egress.

Review found repeated common hash loops without per-chunk deadline checks after
the initial validation. Optional checkpoints now carry the same operation budget
through staging validation, queue ingestion, engine and provider rechecks, with
existing callers unchanged by default. Real temporary-file tests cover aborts
in later rehash phases without false verified history. Authorization freshness
uses the actual wall clock after gate EOF, with no new runtime clock override.
These checkpoints do not establish a hard bound on uninterruptible kernel I/O.

A separate native idle-service trial of the protected dev13 coordinator failed
before processing an intent, with exit 2 and a fixed unavailable result. Its
actual process identity, capabilities, NNP/seccomp and mount restrictions were
observed, but startup acceptance failed. A read-only host-root check reproduced
errno 40 in the fixed executable no-follow open; source and backup identity
checks passed. Debian's `/usr/sbin/ip` is a symlink to `/usr/bin/ip`. The latter
was independently observed as a root-owned mode-0755 regular file with one link
from iproute2 6.15.0-1. Dev14 gates and invokes that canonical path; the strict
symlink refusal remains tested. No capability expansion was needed for this
path correction. The following separate dev14 observations update startup only.

The captured test service stopped with no remaining processes. Existing live
services, private state, account/configuration metadata, Wi-Fi/routes/DNS, USB
address/carrier and healthy power observations were preserved. No worker,
Google authorization payload, provider call, boot change or network setting was
involved. All private fixture records remain retained.
[Sanitized startup evidence](acceptance/pi-google-test-broker-idle-dev13-2026-11-14.json).

The live dashboard remains the protected dev11 upload repair. Public HTTPS login
still returned HTTP 200 with the expected form during this follow-up. Source
changes do not imply a working Windows mapped drive, restored Google requests,
automatic backups, physical Android acceptance or a release.

The tested dev14 wheel (`386bacff…`, source `c5c0bfa`) was subsequently installed
into a fresh protected inactive prefix. Exact package/dependency bytes, copied
interpreter and imports/console execution as both ordinary identities passed;
the live dashboard and all previous prefixes were preserved. Both GitHub Actions
runs for that source passed Python 3.9/3.12/3.13, local rclone, isolated Samba,
Rust tests and ARM64 cross-build checks.

The dev14 idle-only native trial observed the actual protected coordinator's
expected idle JSON and its root/capability/NNP/seccomp/mount sample. The original
helper still returned failure: the successful one-shot service cleared its
terminal InvocationID, which the helper required for its post-exit ownership
checks. This is retained as a helper failure, not relabeled as a full pass.

Independent read-only reconciliation checked the root-owned unit bytes against
its durable plan, unchanged description/fragment, the captured invocation's exact
idle journal, inactive/dead state, zero main/control PIDs and an empty/missing
cgroup. A separate dependency-link check found no wants/requires links for this
unit under `/etc` or `/run`. No reconciliation write or re-run was performed.
The full coordinator service, cross-identity source children, BPF capability
sufficiency, valid release, real Google Test/refresh and recurring worker remain
unaccepted. Live services, private configuration and networking were preserved.
[Dev14 installation and idle observations](acceptance/pi-google-test-broker-idle-dev14-2026-11-14.json).

## Persistent Google Prepare restoration and dev15 upload CLI — 14 November 2026

The final dev15 source suite passes **680 Python tests** in 32.571 seconds,
including actual rclone against temporary local storage. Fifteen new private
upload CLI tests cover receipt-first validation, missing gates, stdio/descriptor
aliases, framed EOF, private result delivery, result bindings, closed/reused
descriptors and fixed parser/exception diagnostics. The worker is stubbed in
these transport tests; the batch API's separate tests cover its authority,
generation, queue and verification behavior. No cloud provider is contacted.
The source command starts no executor or service and is not yet installed on
the Pi. Its valid pending/failed outcomes carry a private receipt despite exit
two; future completion handling must preserve that distinction.

The live preparation repair uses the already protected dev14 package, with the
live dev11 dashboard retained. Independent review and 25 local helper/rollback
tests passed before execution. Read-only preflight confirmed the old broker
origin, missing preparation units, disabled dashboard flags and existing private
file modes. Durable root-private configuration/unit backups and a guarded saved
rollback preceded changes. The helper updated only the broker's origin and
interpreter/settings, installed persistent preparation service/path files and
one owned path boot link, then enabled the dashboard's Prepare flag. The control
flag remains off. Only the dashboard restarted; its password is unchanged.

Source-UID offline checks validated the saved grant and current client origin
without a provider request. Static unit parsing, loaded settings, active/waiting
watcher, exact wanted-link ownership and fresh disk/manager enablement checks
passed. The source, existing preparations, accounts, connector invocation,
existing dashboard boot link and working network observations were preserved.
An independent read-only follow-up verified the saved rollback/configuration and
unit bytes, current dashboard identity/arguments, fixed origin, active enabled
watcher, inactive broker, unchanged connector and healthy USB/Wi-Fi/power.

The actual Chrome page loaded the new sign-in form. An API request with the old
session returned the expected 401 after restart; no saved autofill was available. A
fresh owner sign-in and preparation request were unobserved at that check;
the later owner flow is recorded below. There was no
reboot, password reset, old-auth restoration, cloud request, token refresh,
Test/Enable activation or automatic upload. The repair does not prove a new
kernel-worker sandbox. One-shot inactivity also does not prove that no brief
attempt occurred; the result correctly reports only final inactivity.
[Sanitized restoration evidence](acceptance/pi-google-prepare-restoration-dev14-2026-11-14.json).

Separate read-only capture prerequisites found the official USB auto-switcher
active/enabled and a profile outside the capture runtime's manual local-only
contract. Loopback TCP445 was free; the protected dev14 environment has no
optional SMB client installed. These findings leave capture gated. No USB,
NetworkManager, firewall or host networking change was made to bypass them.

## Upload bootstrap and current Pi checks — development 0.1.0.dev16

The combined source suite passes **696 Python tests** in 32.805 seconds,
including actual rclone against temporary local storage. Sixteen new behavior
tests use real Unix streams and the actual private CLI, with explicit fake
activation, sandbox, canaries and upload API. They cover all four receipt states,
matching owner control IDs before API/gate work, exact upload completion/EOF,
closed and reused descriptors, write failures and sanitized diagnostics. Ruff
and Python 3.9 grammar checks pass. The accepted Test executor is byte-identical.
No native upload bootstrap, dispatcher, cloud account or recurring worker was
accepted by these source tests.

The exact target SDK was installed offline into a new isolated QA prefix on the
Pi: smbprotocol 1.17.0, pyspnego 0.12.4, cryptography 50.0.2, cffi 2.1.1 and
pycparser 3.12. The copied CPython 3.13.5 interpreter, native extensions, glibc
2.41, module identities and native encryption operation passed ordinary-reader
checks. The five official PyPI wheels, dependency closure, licenses and hashes
were retained. Four retained pinned Debian Samba archives were extracted without
maintainer scripts or global package changes. The immutable dev14 application
and original environment were preserved.

The actual `LocalSambaReader` and `stage_smb_version` passed against Samba
4.22.11 on the sole verified 127.0.0.1:445 listener, negotiating encrypted SMB
3.1.1. An open writer deferred capture before batch creation; a held snapshot
handle denied new write/delete opens; the backup account could not write.
Completed bytes and SHA-256 matched, an injected read failure retained an
incomplete copy, and changing a file retained the earlier snapshot. A fresh
Root-owned, expiring fixture approval guarded every real reader open. The full
capture guard stayed unmodified and was not exercised by this core trial.

Twenty-one installer/fixture tests and independent review passed before the
bounded native run. The exact owned transient server was stopped, its cgroup was
empty or absent, and all installer children were reaped. A separately reviewed
read-only verifier, with seven local tests, then checked 332 installed wheel
files, the copied interpreter, licenses, source fixture bytes, two ready snapshots
and one 8,192-byte incomplete copy. Live invocations, private metadata, OS
accounts, package database, USB/Wi-Fi observations and healthy power were
preserved. This does not qualify Windows, the current USB profile, the full
capture service, the dashboard bridge or any cloud worker.
[Native SDK/core evidence](acceptance/pi-smb-sdk17-core-dev14-2026-11-14.json).

After the owner signed in privately, the actual HTTPS Backups form submitted one
Prepare request using its unchanged default folder label. HTTP 202, requested
then prepared status, and the eventual **Configuration prepared · Test required**
label were observed. The enabled persistent watcher and protected dev14 broker
processed the request while the dev11 dashboard stayed running. The control
form remained absent and automatic uploads stayed off.

A separate verifier checked the current owner/source bindings twice as the
ordinary source identity, with an ordinary backup receipt export between them.
The actual prepared configuration bytes matched the source's expected digest,
the stored receipt and the new request. Private frames required EOF and zero
child exits; only metadata reached Root. The first verifier refused the
8,773-byte restoration metadata file under an 8,192-byte bound. Its retained,
reviewed successor changed only that fixed snapshot's bound to 16,384 bytes,
with identity and unique-JSON validation unchanged and eleven tests passing.
The successor passed without another owner request or any state rewrite.
Persistent watcher status, unchanged live invocations and the broker's last
zero exit were verified. This is offline preparation proof, without a provider
request, token refresh, current cloud worker, Test/Enable or reboot acceptance.
[Fresh owner preparation evidence](acceptance/pi-google-prepare-fresh-dev14-2026-11-14.json).
