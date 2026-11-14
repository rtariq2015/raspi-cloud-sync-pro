# Google controls and completed-file transfer

These are development components, tested with synthetic private state and
temporary files. The current dev11 Pi dashboard permits Google Prepare requests
through the restored persistent dev14 broker. A fresh owner request remains
unobserved. The control-request flag and automatic backups remain off. The
accepted dev11 wheel contains guarded worker components; the protected dev13
bootstrap/invalid-gate trial passed separately. The coordinator command and
service example below are not connected to the live dashboard.
The control and transfer APIs make no cloud requests;
the separately gated tester can upload a small fixture when deliberately run by
an accepted coordinator.

## Owner decisions

`google_control.py` records separate Test, Enable and Disable decisions. The
caller must validate CSRF and supply the dashboard's live owner/session guard.
The guard remains held until the decision is durably published. Changing the
owner password, role, current authorization or prepared configuration invalidates
a decision made against the previous state.

Test never enables backups. Enable requires a recent matching successful Test
receipt: a specific app-owned folder, deterministic fixture upload and downloaded
SHA-256 verification, and verified token refresh. The receipt is a private report
from a future trusted tester, not independent cloud verification by this module.
A failed or missing Test cannot become enabled.

The operational binding includes the actual backend, OAuth client, scope and
folder. Mutable token bytes are excluded from this binding so a valid token
refresh does not change the destination. The future executor must parse and
compare all actual configuration settings before each admitted batch; it must
reject extra options rather than constructing a report from expected values.

A completed Enable can outlive its original request deadline. Every batch still
requires a fresh private admission decision and revalidation of the current
owner and source. Disable immediately denies subsequent admissions. A pending
new Test or Enable also denies them. Stopping an already admitted process remains
the coordinator's responsibility; this local module cannot stop a service.

Requests and receipts use private, immutable files and an atomic current pointer.
Framed inherited descriptors carry private coordination data. Public status
reports historical decisions and explicitly leaves current worker/account state
unverified. The browser supplies actions, not filesystem paths, config hashes or
cloud folder identifiers.

## Move completed web uploads between service identities

`batch_transfer.py` bridges a selected completed web upload from the dashboard
identity into backup-owned staging. The coordinator independently selects its
batch identifier and canonical manifest SHA-256. It passes opened private root
directories and anonymous pipe or Unix-stream descriptors to separate children.
The backup identity does not gain access to dashboard account or OAuth files.

Payload bytes stream in bounded chunks, with per-file size and SHA-256 checks.
The exporter rechecks its source descriptors and paths before sending a final
completion frame. The importer requires that frame and EOF, rehashes its disk
copy and flushes files/directories before atomic no-replace publication. The
coordinator must require successful exits from both children before scheduling
the transferred batch. This protocol concerns already completed web uploads; it
does not establish safety for concurrent USB mass-storage reads.

Failed and interrupted copies remain incomplete. Existing published batches are
reused only after independent verification; conflicts are refused. Originals,
partial files and earlier versions are retained. Timeout and manifest-size bounds
limit protocol work. The dashboard's current 100 MiB upload limit is unchanged;
internal transfer bounds are not evidence of large-file Pi acceptance.

## Dashboard controls, disabled by default

The new `dashboard --google-control-requests` option exposes owner Test, Enable
and Disable controls only after a separate coordinator is connected. It defaults
to false and is independent of `--google-setup-requests`. The deployed Pi command
supplies only `--google-setup-requests`. A flag enables request submission; it does
not establish a running or accepted backup service.

`POST /api/destinations/google_drive/control` accepts only an exact bounded JSON
object containing `action`: `test`, `enable` or `disable`. The handler checks
CSRF and the current owner session and holds its authorization guard through
durable publication. It accepts no browser-provided identifiers, file paths,
hashes, cloud folder IDs or credentials. Anonymous users and viewers cannot
submit decisions. There are no service or network calls in this handler.

The browser distinguishes a queued Test, a passed Test with backups still off,
saved Enable permission with unconfirmed service status, and paused new backups.
Disable denies future admissions; an already admitted transfer may finish until
the coordinator stops it. A new Test also pauses new admissions, including after
a previous Enable. Bounded status retries stop at the original request deadline
and never promote unavailable or expired status to success.

## Private Google Test worker

`google_test.py` supplies `run_google_test` and a separate strict validator for
actual mutable rclone configuration. It refuses by default before reading
credentials or state. A consumed inherited Unix-stream descriptor must identify
a root peer through kernel credentials and release the exact request and worker
PID with a short-lived private frame. A root release proves coordinator
authority; it does **not** prove network confinement. The coordinator must accept
actual per-run Wi-Fi confinement before releasing it. Its private operator CLI
and source coordinator are described below. Their presence in an installed wheel
does not activate a coordinator or establish cloud acceptance.

The ordinary backup identity validates the protected rclone executable's exact
digest and creates a new operational generation from the bound preparation.
It leaves the original preparation unchanged. It locates or creates one uniquely
named app-owned Drive folder, checks its exact identity and parent, and pins its
ID in the operational copy. It uploads a deterministic tiny fixture through the
existing job/history engine. Before independent downloaded-content SHA-256
verification, it expires only the operational access token, requiring rclone to
refresh and persist a fresh token successfully. This tests refresh without
modifying the dashboard grant or immutable preparation.

The immutable seed validator remains separate from mutable token validation.
The latter permits supported token formatting and expiry changes, but requires
the exact direct Drive backend, limited scope, OAuth client and selected folder.
Extra remotes, endpoints, credential sources and other settings are rejected.
Token refresh cannot silently change destination identity or reuse another
generation's verified jobs.

Rclone runs with an empty inherited environment except fixed basic locale/path
and resolver settings, one transfer/checker/retry, bounded output and one hard
deadline. It inherits no proxy, alternate CA or rclone override. Failures stop
the owned process group and retain private partial artifacts. Raw provider
output and private receipts never reach the browser. A successful receipt still
records uploads disabled; it does not create Enable permission.

The current tests use fake cloud transport with real private files, generations,
history and local child processes. They do not establish live Google refresh,
per-run Wi-Fi confinement or recurring backup acceptance. The documented
External/Testing refresh-token lifetime still applies.

## Dev11 supervised worker, not activated

`google_test_broker.py` now supplies `GoogleTestBrokerSettings.load` and
`run_google_test_broker`. Its protected configuration defaults to disabled;
`{"schema_version": 1, "enabled": false}` starts nothing. Enabling it requires
reviewed root-owned configuration/approval files, a protected copied interpreter
and package/import tree, a pinned rclone executable, separate ordinary service
identities and private state directories. It accepts only the tested Pi and
systemd platform. There is no installer, startup watcher or public bypass option.

The enabled coordinator privately re-exports an existing owner's current Test
intent as the dashboard identity. Root receives metadata, not the authorization
tokens. It checks the approved Wi-Fi connection UUID, metered policy and direct
numeric DNS route, then creates one fresh transient worker with four separate
`OpenFile=` connections to root-private Unix listeners. Those channels carry
canary control, the request, the root gate and the private receipt separately.
Actual manager load paths and applicable overrides are checked before startup;
an unreviewed pre-start command must not run before later sandbox checks.

`google_test_executor.py` runs as the ordinary backup identity. It checks the
four descriptors/root peers, its actual kernel identity and sandbox, the exact
private resolver mount and protected executable. Before touching preparation or
operational credentials, it sends READY and waits for root's GO. The root
coordinator independently binds the live PID/pidfd, invocation, cgroup, argv,
mounts and capabilities. `wifi_confinement.py` holds actual kernel references
and rechecks current cgroup identity, attachments, both shared map IDs, the
single current wlan0 index and read-only allowlist byte. A retained old FD or
saved QA receipt is insufficient.

The same worker runs bounded Wi-Fi DNS/HTTPS positive probes and loopback
negative probes, surrounded by root's nonce-only positive controls. It closes
its canary channel before the Test CLI. Root freshly checks the policy, Wi-Fi
and owner/source export before sending the exact PID/request-bound gate. The
worker cannot enable recurring uploads or select another destination.

After the Test, a CLOEXEC receipt duplicate keeps the bootstrap alive for a final
completion barrier. It half-closes its write side so root can validate the
receipt and EOF. Root requires the original process alone in its cgroup, only
its Unix completion stream remaining, unchanged current policy and source, then
sends a bounded ACK permitting only closure and exit. This is necessary because
the [native handoff trial](acceptance/pi-openfile-handoff-2026-11-13.json) observed
that PID1 removes the empty cgroup after exit despite `RemainAfterExit=yes`.
The coordinator requires clean exit and source-recorder acceptance before success.

Timeouts are checked before launching commands. Cleanup gets its own bounded
budget, stops only the freshly captured unit/invocation and requires no remaining
worker or cgroup processes. A failure can publish a conservative failed-Test
receipt only after confirmed cleanup and another fresh, identical, unexpired
owner/source export. It cannot replace an existing receipt or claim upload,
read-back or refresh success. Interrupted private generations remain retained.

These source components pass local protocol/filesystem and fault-injection tests
with fake PID1/network/provider boundaries. The later dev13 native trial accepts
the actual source bootstrap, private DNS mount, held kernel policy, canaries and
invalid-gate refusal. It used no credentials. Full coordinator-service, valid
Test/completion and Google account acceptance remain required before enabling
this integration. Each credentialed run still needs fresh confinement checks.
Empirical USB packet tests remain separate from an exact wlan0-only kernel
allowlist. [Dev13 evidence](acceptance/pi-google-test-source-dev13-2026-11-14.json).

## One-shot coordinator command and disabled examples

The private operator command is:

```sh
/opt/raspi-cloud-sync-pro/releases/0.1.0.dev14/venv/bin/python3 -I -B -m raspi_cloud_sync_pro google-test-broker --config /etc/raspi-cloud-sync-pro/google-test-broker.json
```

The example targets dev14 source; it does not install that build. A protected
dev14 prefix has separately passed inactive installation checks. The older inactive
protected dev13 build also contains the command, but its separate idle-service
trial failed before handling requests: Debian's `/usr/sbin/ip` symlink was
correctly refused by the no-follow executable check. Dev14 uses the verified
canonical `/usr/bin/ip` path. Its actual idle result and process sandbox were
observed, but the helper refused a post-exit check when systemd cleared the
terminal invocation ID. Independent read-only checks confirmed the owned unit
inactive with zero PIDs and no cgroup. Full service/worker acceptance remains
separate. [Dev14 observations](acceptance/pi-google-test-broker-idle-dev14-2026-11-14.json).
[Startup evidence](acceptance/pi-google-test-broker-idle-dev13-2026-11-14.json).
The live dev11 upload-fix wheel does not contain this command. It runs once and
selects an existing current owner intent through the source identity's private
export. It does not create an owner/session proof, request or Enable decision. A private root
release can be sent only by the existing coordinator after its required fresh
worker, confinement, canary and source checks.
Disabled configuration and an absent current request start no worker. There is
no path watcher, timer, boot enablement or automatic retry in this first route.
An owner request alone therefore does not cause this command to run.

Stdout contains only the fixed `status`, `code`, `uploads_enabled: false`,
`account_verified: false` and `current_worker_state_checked: false` projection.
Exit status is zero for disabled, idle, an applied control or a recorded verified Test, and two for
failure. Parser/configuration exceptions use one fixed error without echoing
paths or private values. A successful Test still needs a separate owner Enable
and future per-batch admission; it does not establish a running backup service.

The [disabled configuration example](../config/google-test-broker.example.json)
uses `enabled: false`. Its zero executable digest, zero Wi-Fi profile UUID,
documentation DNS address, sample origin and rclone path are placeholders, not
accepted device values. A reviewed operator must bind the actual protected
copied interpreter/current package, rclone bytes, HTTPS origin, Wi-Fi profile,
numeric DNS and metered policy. The loader requires a root-owned mode-0600
configuration and approval file under protected, non-symlink parents. Source,
preparation and operational roots stay private mode-0700 directories owned by
their distinct ordinary identities; the operational root belongs to
`raspi-backup`. The configured runtime directory is root-owned mode 0700. No path is supplied
by the browser.

The separately reviewed approval file, named `GOOGLE_TEST_APPROVED` in the
example, has exactly these private JSON bytes (JSON whitespace is immaterial):

```json
{"schema_version": 1, "google_test_broker_approved": true}
```

The [one-shot systemd example](../deploy/systemd/raspi-cloud-google-test-broker.service.example)
has an `.example` suffix and no Install section. It is not an installed unit.
Before any reviewed installation, its command/configuration must match the
protected release actually containing this CLI. The root coordinator needs the
host network namespace for its nonce controls and wlan0 observation; its
read-only iproute queries use netlink. These permissions belong to the root
coordinator. The separately created backup worker retains its own capability-free,
IPv4-only, wlan0-confined policy and inherited Unix channels.

The administrative capability list is a candidate, not a native-qualified
least-privilege claim. In particular, the current inspector's read-only
get-program/map-by-ID operations require `CAP_SYS_ADMIN` in
[upstream Linux 6.18](https://raw.githubusercontent.com/torvalds/linux/v6.18/kernel/bpf/syscall.c).
`CAP_BPF` alone cannot replace that requirement. Cross-identity process inspection
also needs appropriate process/filesystem access. The example preserves
NoNewPrivileges, an empty ambient set, protected filesystems and denied mount,
swap and reboot syscalls. Fresh actual worker identity, held kernel policy and
all canary/source checks remain mandatory; a service template or old QA receipt
never supplies those proofs.

Failures retain generations and root-private run records. After confirmed
cleanup, a fresh matching unexpired source authority may receive a conservative
failed-Test receipt. Changed or revoked authority, expiry and unconfirmed
cleanup prevent that update. Repeating a completed intent cannot overwrite its
receipt. Manual recovery must inspect the retained fixed result, restore the
approved prerequisites and select the same still-current intent or a new owner
Test. A previously exhausted systemd start limit needs a scoped reset of only
this coordinator unit before another deliberate start. Native qualification of
this exact coordinator service and actual Google account/refresh acceptance are
still required; these examples install or activate nothing.

## Applying controls and uploading one admitted batch

Dev14 adds a local application path for existing Enable/Disable decisions.
The coordinator exports the current decision twice and the source identity's
recorder independently rechecks owner, source, expiry and successful Test
bindings before writing an immutable `applied` receipt. This performs no cloud
request and starts no upload worker. Its public result is
`status: applied`, `code: google_test_broker_control_applied`, with all public
upload/account/worker-readiness flags false. Disable already denies new admissions
when its intent becomes current; receipt recording completes its status.

`google_upload.run_google_batch_upload` is a private library API, not a public
activation command or running service. With no root release it refuses before
opening staging or credentials. A consumed kernel-root-peer stream must bind
the current PID, selected batch ID, canonical manifest digest and complete fresh
source admission digest. Its purpose differs from the Google Test release.
The future coordinator must still obtain fresh owner admission and qualify the
actual worker's Wi-Fi policy and canaries before sending that release.

The API validates the complete ready batch and the admitted preparation,
operational generation, stable settings and exact tested folder. It uses the
clean pinned rclone runner for uploads and downloaded SHA-256 verification;
ordinary uploads do not force token expiry as the separate Test does.
Configuration and source bindings are checked around every provider command.
The immutable preparation remains unchanged. No folder is created by this API.
The same operation deadline is checked during initial and repeated local hashes,
queue ingestion, engine checks and provider checks. Optional checkpoints leave
other callers' behavior unchanged. These Python checkpoints and bounded child
processes are not a hard deadline for uninterruptible kernel I/O. Release and
admission freshness always use the actual wall clock, sampled after gate EOF.

The existing generation history retains cross-batch deduplication. The engine's
optional `batch_root` selector limits claims to the admitted canonical batch.
An older pending duplicate remains attached to its original root; a new batch's
permission cannot read or rebind it. That work remains pending until its actual
root is independently admitted. Prior verified content is reported as historical
reuse, separately from files verified by this attempt. Original, staged and
partial files are retained on success and failure.

This path has synthetic provider/authority tests. It has no deployed executor,
recurring scheduler, live source-admission bridge or real account acceptance.
The dashboard-to-backup transfer must independently finish framing, EOF and both
child exit checks before a coordinator can schedule the received ready batch.

## Private selected-batch upload command

Dev15 adds `google-upload` as an operator-only transport for the batch API. It is
not present in the protected dev14 prefix used by the live preparation service.
It requires protected preparation/operational/staging paths, a selected batch ID
and canonical manifest SHA-256, a pinned rclone executable/digest, and three
separate inherited descriptors: `--admission-fd`, `--root-gate-fd` and
`--receipt-fd`. Omitting the root gate refuses before admission or state reads.
No browser endpoint supplies these arguments.

Receipt-channel validation comes first. Stdio, repeated descriptors and aliases
are refused; accepted channels are consumed on all outcomes. Admission metadata
is bounded, framed and EOF-checked. The API alone authenticates the root release
and its current PID/batch/manifest/admission binding. Paths and arguments never
appear in parser errors or public failure diagnostics. The operation deadline is
at most 300 seconds, with the API's existing checkpoint/kernel-I/O distinction.

Only the inherited private receipt channel receives the detailed result. Stdout
contains fixed state/count/false-flag fields. Verified or historical reuse exits
zero; a valid pending/failed result sends its private receipt and exits two.
Exceptions also exit two but produce no receipt. A future bootstrap/coordinator
must distinguish these cases and require validated receipt delivery plus live
completion proof for both exit outcomes. Exit zero alone is not acceptance.

Fifteen new transport tests use a stub worker and no provider/network release.
The confined upload bootstrap, root per-batch dispatcher, live source admission,
real account tests and automatic scheduling remain separate integration work.

## Remaining integration

The private `google-test` operator command now exposes the Test worker. A trusted
coordinator supplies `--prepared-state`, `--operational-state`, a pinned absolute
`--rclone` executable and `--rclone-sha256`, plus separate inherited
`--request-fd`, `--root-gate-fd` and `--receipt-fd` channels. The optional gate
argument deliberately defaults to refusal. These are already connected private
descriptors, not standard input/output or a shell pipeline.

The command validates its receipt channel before reading a request or calling
the worker. It rejects stdio, duplicate channels and descriptor aliases, consumes
accepted channels on all outcomes, and sends its private receipt only through
the framed output descriptor. Ordinary stdout contains a fixed disabled summary;
parser errors and worker exceptions do not echo arguments or private values.
Request framing has a five-second deadline; the Test defaults to 120 seconds
and cannot exceed 300. The command cannot create an owner intent or root release.
It remains unavailable without the inherited private root gate; installation
alone does not activate it.

Private operator CLI commands now expose `google-control-status`,
`google-control-export`, `google-control-record` and `google-control-admission`
for existing owner intents. They cannot create an owner request or start a
worker. `web-batch-describe`, `web-batch-export` and `web-batch-import` require
inherited private descriptors; describe sends identifiers and manifest hashes
only through its framed output descriptor. Normal stdout contains a fixed
disabled summary. These are coordinator interfaces, not browser endpoints or
general file-copy commands. They are source-tested interfaces in the dev11
package, not activated dashboard or recurring-backup services.

1. Observe a fresh owner Prepare request through the restored broker for the
   current HTTPS origin, then deploy the source-tested owner controls with the
   separately reviewed coordinator.
2. Accept the Google tester on the Pi, including forced token refresh and
   independent downloaded-content verification in a new app-owned folder.
3. Prove approved Wi-Fi egress independently of a working USB Internet route,
   with actual kernel attachments and positive/negative network controls.
4. Run a coordinator that obtains fresh admission, verifies actual operational
   settings and transfers only selected completed batches.
5. Publish private per-file results to the dashboard without sharing credentials
   or the backup database, then test Disable, outages and restart recovery.

See [private preparation](GOOGLE_HANDOFF.md), [architecture](ARCHITECTURE.md)
and [the worker guide](STAGED_WORKER.md). Automatic cloud uploads remain disabled
until that integration passes real acceptance.
