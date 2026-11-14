# Completed-batch backup worker

The opt-in worker processes only previously published `.ready` batches. It does
not mount images, scan a live USB filesystem, create a Samba session, change
routes or configure Wi-Fi. It can be run once, kept running, or invoked by the
[Rust watcher](RUST_EVALUATION.md). The default example is disabled.

```sh
PYTHONPATH=src python3 -m raspi_cloud_sync_pro worker-plan --config config/worker.example.json
```

To prepare a real account test, install a reviewed rclone executable, configure
the intended Koofr WebDAV or Google Drive account privately, and confirm its actual
backend/account and destination folder. An adapter kind is not proof the named
remote has that backend. Keep the config mode 0600, owned by the worker identity.
Use dedicated test accounts and disposable data first. Do not paste OAuth tokens,
app passwords or enterprise passwords into a project JSON file, Git or logs.

Create a private local worker configuration from the example, review the absolute
paths and approved uplinks, and set `enabled` to true only for that accepted test:

```sh
PYTHONPATH=src python3 -m raspi_cloud_sync_pro backup-staged \
  --config /absolute/path/to/private-worker.json --once
```

Without `--once`, the process waits the configured interval between ticks. Use
either one persistent Python worker or the Rust wrapper, not both. The scheduler
lock rejects an overlapping worker. Missing/insecure rclone prerequisites fail
before queue creation or consuming attempts. Defaults never enable a service.

## Completion, deduplication and retry

New batches receive full manifest/payload checks. Every destination gets its own
durable job. The worker records scheduler registration receipts in an auxiliary
`staged_receipts` table; existing version-1 job/event history stays readable.
Receipts avoid full payload rehash on every short-lived invocation. They do not
authorize upload: the engine checks the actual staged bytes before sending,
checks the immutable remote version by downloaded SHA-256, and checks the source
again before recording verified.

An interrupted ready-publication→queue→receipt sequence can be retried without
duplicate jobs. A corrupt batch is retained and rejected; incomplete batches are
ignored. Successfully uploaded/verified source data is retained. Corrupt immutable
remote objects are not silently overwritten. A failed/exhausted job needs future
explicit recovery tooling rather than an automatic reset of its retry history.

The auxiliary `staged_rejections` table retains malformed-batch fingerprints
across short-lived child invocations, preventing rejected retained batches from
starving later work. A `reconciliation_pending` report keeps the Rust scheduler
running when the 100-new-batch scan budget is exhausted, even if no jobs have yet
been queued. Removing a destination retains its jobs without keeping the active
scheduler busy for that removed destination.

The worker exposes sanitized status including pending jobs, rejected batches,
observed uplink, metering and per-destination outcomes. It never writes raw
provider output or credentials into status. Each tick is bounded by the number
of new batches processed and the providers' individual process timeouts. Very
large inventories and whole-file retries still need Pi throughput/endurance tests.

## Network limits and deployment

Cached global connectivity, the preferred IPv4 default interface and metering
must identify an approved link. Unknown/portal/unapproved/disallowed-metered
conditions pause without spending attempts. USB as the preferred route remains
`usb_host_ics`, even if Wi-Fi is also connected. The worker does not change that
preference or implicitly approve Internet sharing. This observation is not an
egress firewall; IPv6, VPN/policy routing and proxies still need acceptance before
using multiple/untrusted uplinks in deployment.

The default example places state and web staging under a single private identity
for a developer test. Production separates dashboard authentication from the
backup identity and must review staging access/permissions explicitly. The Rust
service template instead illustrates the separate existing SMB staging/state
paths; it is not a ready-made web-inbox deployment. Its runner points to a reviewed
installed Python build with these CLI commands. No service is enabled in this
source checkout.

Automated tests include two-destination retry independence, offline restart
reconciliation, receipt reuse without trusting changed payloads, busy-worker
exclusion and malformed/disabled configuration. A real-rclone local-alias test
does not constitute Google Drive/Koofr account acceptance.


The dashboard can read a deliberately selected report directory with
`dashboard --worker-state /absolute/private/worker-state` and an independently
selected `--history` database. It projects queue counts and report age only;
it does not read credentials, start a worker or infer cloud account acceptance.
The parser recognizes NetworkManager's actual C-locale `no (guessed)` /
`yes (guessed)` metering labels; unknown values still pause uploads.
