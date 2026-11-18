# Stage 3B — explicit 8 GiB controller support

15 November 2026. Source-only development on `development`, starting at
`efc5d08ce0f34378efb14b0838ac4d6217e4ceb9`.

The protected appliance and manual operator source now support an explicitly
approved **8 GiB** image alongside the existing **1 GiB** behavior. This is
capacity validation, not a migration or deployment. The live 1 GiB CLOUDSTICK,
installed services, credentials and data were not accessed or changed.
Safe Eject remains required. No 8 GiB physical acceptance is claimed.

## Code changes

- `src/raspi_cloud_sync_pro/mass_storage_image.py`: one shared pure policy for
  strict capacity, exact approval records and bounded MBR/exFAT header checks.
  It performs no file or device operations and grants no ownership.
- `mass_storage_appliance.py`: reuse that policy for approval and detached
  filesystem checking. Recheck the named file identity after header reads to
  detect replacement during validation. No state-machine or journal rewrite.
- `tools/acceptance/mass_storage/prepare_image.py`: default remains 1 GiB;
  propagate explicit capacity through allocation, exact loop limits, identity,
  header validation and receipts. 8 GiB creation requires an additional explicit
  approval flag. Existing-directory refusal is unchanged: this creates a new
  disposable image only and cannot replace the existing live image.
- `trial.py`: keep legacy plans; bind 8 GiB capacity/layout into a new plan
  schema covered by the existing explicit plan-hash approval. Attach/eject
  observation keeps metadata-only image checks while the host owns storage.
- `capture.py`: validate the saved capacity contract on both release and manual
  handoff routes; reuse geometry validation only after verified host release.
- Tests cover both capacities, malformed records/layouts, ownership failures,
  stale identities, journal/inventory retention and preparation failure handling.
  The old synthetic appliance header fixture now contains actual sector-size
  and volume-length fields; accepting malformed exFAT is not compatibility.

## Approval and validation contract

Only exact integer byte sizes **1,073,741,824** and **8,589,934,592** are accepted
by the appliance/operator policy. Other sizes, off-by-one values, booleans,
floats and strings are refused. The generic pure MBR generator remains usable
for existing disposable geometry simulations; generating bytes is not approval
to create or export an image.

**Existing schema 1 is unchanged and remains 1 GiB only:**

```text
schema_version = 1
mass_storage_appliance_approved = true
image_identity = [exact device, exact inode, 1073741824]
```

**Schema 2 requires all of these fields, with no extras:**

```text
schema_version = 2
mass_storage_appliance_approved = true
image_identity = [exact device, exact inode, exact approved size]
image_size_bytes = exact approved size
image_layout = {
  partition_table: "mbr", partition: 1, start_sector: 2048,
  partition_sectors: image_size_bytes / 512 - 2048,
  filesystem: "exfat", bytes_per_sector: 512
}
```

Schema 2 can explicitly describe 1 or 8 GiB; an 8 GiB image can never reuse
schema 1. These symbolic examples are not deployable approval files. Records
must still be root-protected and the protected-test-build gate must be enabled
only through separately reviewed installation. No approval file is generated or
installed automatically. Selecting a creation size does not authorize export.

The manual operator's 8 GiB plan likewise uses schema 2, with size/layout and
its existing five-field identity including timestamps. Its existing
`ATTACH_APPROVED.json` must still bind the exact plan bytes by SHA-256.

The header contract requires one nonbootable type-07 MBR partition, 1 MiB
alignment, an exact partition end at the image boundary, no other partitions,
exFAT signature, 512-byte sectors, matching volume length and clean error/dirty
flags. exFAT PartitionOffset may be 0 or 2048: the existing formatter uses an
offset loop, so 0 is legitimate. Headers supplement **`fsck.exfat -n`**, never
replace it or authorize automatic repair. Actual filesystem consistency still
requires a successful real fsck under exclusive access.

## Safeguards preserved

Approval remains metadata-only. `_approved()` runs during host ownership and
does not open the backing image for payload or header reads. Filesystem checks
remain behind detached USB, empty backing LUN, the exact read-only loop and no
mount. Every fresh controller export retains its existing read-only fsck/check
sequence. Adopting an already-loaded host remains metadata-only and does not
claim a fresh filesystem validation; it cannot prove host release.

The disabled public build, Pi/root/namespace checks, trusted paths, single-link
regular-file checks, command allowlists, power checks, clean-eject observation,
read-only mounting and failure retention remain. No forced eject, automatic
repair or deletion was added. Existing 1 GiB configuration files need no edit.

Journal and capture-inventory schemas are unchanged. Their existing image
path/device/inode/size bindings still reject a resized or replacement image.
An approval record cannot migrate those records. No automatic rebinding,
history rewind, rollback, cutover or capture-cache reset was introduced.

## Tests and review

| Test group | Result |
| --- | --- |
| Capacity/appliance/Linux backend/controller/ownership, focused | 109 passed |
| Image preparation, including final default-1-GiB lifecycle regression | 16 passed |
| Final full `tests/` discovery | **864 run: 861 passed, 3 skipped; zero failures/errors** |
| USB manual operator/capture | **36 passed**, ResourceWarning treated as error |
| Accepted Stage 3 disposable simulations | **15 passed**, ResourceWarning treated as error |

The focused 109 and 16 are subsets of the full main suite; do not add them
again. Three optional real-rclone local-only tests were skipped because the
local executable was unavailable: metadata inspection and two local-alias
readback checks. No package installation or provider access was needed. Across
the three distinct suites, **912 tests passed and 3 were skipped**. The final
full discovery used a programmatic unittest runner to report exact skipped
IDs/counts; the equivalent standard discovery command is shown below.

```bash
PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_mass_storage_capacity test_mass_storage_appliance test_linux_gadget test_gadget_controller test_storage
PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_mass_storage_image_prepare
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/mass_storage -p 'test_*.py' -v
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/storage_capacity -p 'test_*.py' -v
```

**Regression findings:** no failure in the final runs. Existing 1 GiB approval
and configuration shapes remain compatible, including default preparation and
legacy receipt shape. Malformed synthetic headers were corrected in fixtures
to include the geometry now checked. Preparation's shared-policy import now
requires the project package/environment, as documented in the operator README.

All fixtures are generated temporary files; larger fixtures are sparse header
containers with bounded reads. Format/fsck/loop commands are mocked. They do
not establish real exFAT validity or actual Windows compatibility.

Independent source reviews checked the approval/read boundary, capacity
propagation, manual handoff route and stale-state behavior. No protection was
bypassed to obtain a passing capacity test. Reviews found no remaining concrete
blocker. No Rust code or binary was changed; the existing CI still runs its
normal Python matrix, Rust and isolated Samba checks on the pushed checkpoint.

## Remaining risks and next prerequisite

The installed Pi still runs its previously approved 1 GiB build. New source
requires separate deployment approval. Real 8 GiB creation/fsck/file-copy tests,
Windows enumeration, stable-power tests and migration acceptance remain undone.
The accepted 19-backup/95-event baseline was not re-read in this offline stage;
no live history, staged files, remote objects or credentials were modified.

**The single next prerequisite is a reviewed maintenance hold.** The controller
currently reattaches after capture, and stopping its service does not detach USB.
A future separately authorized stage must prove clean host release, prevent
automatic reattachment and verify safe detachment before migration access. This
stage does not implement that hold.

Suitable low-space admission also remains separately required: the existing
16 MiB capture reserve and preparation's 256 MiB spare-space check do not
implement Stage 3's proposed appliance budget. No new retention or cleanup
policy was added. Preserve the actual original inode for eventual rollback;
refuse blind rollback after candidate host writes. These remain migration-plan
requirements, not capabilities added here.

No physical migration, deployment, next milestone, merge or release is part of
Stage 3B. The Git commit containing this report is its source-only checkpoint.
