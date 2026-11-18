#!/usr/bin/env python3
"""Build pinned patched uMTP and run synthetic C fixtures; NEVER starts umtprd.

Requires an already-cloned upstream checkout. It does not download/install
packages, open USB endpoints, configure gadgets, mount storage or contact cloud.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
CASES = ("complete", "zero-file", "short-write", "eintr", "consecutive", "enospc",
         "zero-write", "fsync-failure", "open-failure", "disconnect", "cancel",
         "cancel-before-data", "early-zlp", "early-short", "path-replaced", "symlink",
         "wrong-transaction", "wrong-length", "unarmed", "unknown-size", "too-large",
         "above-ceiling", "ceiling-metadata", "oversized-first-packet", "partial-edit",
         "helper-failure", "helper-timeout", "session-replay", "session-closed", "close-open-same-session",
         "usb-reset-before-data", "usb-reset-during-read", "malformed-command-replay", "unsupported-command-replay",
         "low-space-start", "low-space-mid", "stale-info-epoch", "reset-between-info-command")
SUCCESS = {"complete": 1, "zero-file": 1, "short-write": 1, "eintr": 1, "consecutive": 3}

FIXTURE_HELPER = r'''#!/usr/bin/python3
# Synthetic callback only. Production ReadyBatch publisher is tested separately.
import argparse,fcntl,hashlib,json,os,time,uuid
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('fd','size','session','transaction','object'):p.add_argument('--'+key,type=int,required=True)
p.add_argument('--relative',required=True);a=p.parse_args()
s=os.fstat(a.fd);root=Path('/proc/self/fd/'+str(a.fd)).resolve().parent
assert a.fd==3 and fcntl.fcntl(a.fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY
assert s.st_nlink==1 and s.st_size==a.size
if root.name=='helper-failure':raise SystemExit(2)
if root.name=='helper-timeout':time.sleep(4)
digest=hashlib.sha256();count=0
while True:
 data=os.read(a.fd,65536)
 if not data:break
 count+=len(data);digest.update(data)
assert count==a.size
identity=uuid.uuid4().hex
out=root/('checkpoint-'+identity+'.json')
with out.open('x') as f:
 json.dump({'synthetic_callback':True,'relative':a.relative,'bytes':count,'sha256':digest.hexdigest(),'session':a.session,'transaction':a.transaction,'object':a.object},f)
 f.flush();os.fsync(f.fileno())
'''


def run(args, *, cwd=None, timeout=120):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Source-only fixture command failed: " + str(args[0]) + "\n" + result.stderr.decode(errors="replace")[-3000:])
    return result.stdout


def prepare_source(upstream: Path, work: Path):
    provenance = json.loads((HERE / "upstream.json").read_text())
    commit = provenance["commit"]
    observed = run(["git", "-C", upstream, "rev-parse", "HEAD"]).decode().strip()
    if observed != commit:
        raise RuntimeError("Refuse unpinned upstream HEAD")
    patch = (HERE / "umtp-completion.patch").read_bytes()
    if hashlib.sha256(patch).hexdigest() != provenance["patch_sha256"]:
        raise RuntimeError("Patch provenance hash mismatch")
    archive = run(["git", "-C", upstream, "archive", commit])
    source = work / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for entry in tar.getmembers():
            path = Path(entry.name)
            if path.is_absolute() or ".." in path.parts or entry.issym() or entry.islnk() or entry.isdev():
                raise RuntimeError("Unexpected pinned source archive member")
        tar.extractall(source, filter="data")
    run(["git", "apply", "--check", HERE / "umtp-completion.patch"], cwd=source)
    run(["git", "apply", HERE / "umtp-completion.patch"], cwd=source)
    return source, provenance


def validate(upstream: Path, work: Path, *, helper=None, cases=CASES):
    source, provenance = prepare_source(upstream, work)
    run(["make", "-j2", "DEBUG=1"], cwd=source)
    # Only build the daemon; it is deliberately NEVER executed.
    compiler = shutil.which("cc")
    if compiler is None:
        raise RuntimeError("Existing system C compiler is required")
    if helper is None:
        helper = work / "synthetic-helper.py"
        helper.write_text(FIXTURE_HELPER)
        helper.chmod(0o700)
        mock_helper = True
    else:
        helper = Path(helper).resolve(strict=True)
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise RuntimeError("Explicit test helper is not executable")
        mock_helper = False
    executable = work / "sendobject-fixture"
    run([compiler, "-O0", "-g", "-Wall", "-Wextra", "-I" + str(source / "inc"),
         '-DRCSP_MTP_COMPLETION_HELPER="' + str(helper) + '"', "-DRCSP_MTP_HELPER_TIMEOUT=1" if mock_helper else "-DRCSP_MTP_HELPER_TIMEOUT=300",
         HERE / "sendobject_fixture.c", source / "src/mtp_operations/mtp_op_sendobject.c",
         source / "src/rcsp_mtp_completion.c", source / "src/rcsp_mtp_lifecycle.c",
         source / "src/mtp_operations/mtp_op_opensession.c", source / "src/mtp_operations/mtp_op_closesession.c", "-Wl,--wrap=write", "-Wl,--wrap=fsync", "-Wl,--wrap=fstatvfs64", "-pthread", "-o", executable])
    observations = []
    for name in cases:
        if name not in CASES:
            raise RuntimeError("Unknown synthetic case")
        root = work / name
        root.mkdir(mode=0o700)
        result = json.loads(run([executable, root, name], timeout=330))
        if mock_helper:
            checkpoints = [json.loads(p.read_text()) for p in root.glob("checkpoint-*.json")]
            if len(checkpoints) != SUCCESS.get(name, 0):
                raise RuntimeError("Unexpected completion callback: " + name)
            for receipt in checkpoints:
                size = receipt["bytes"]
                transaction = receipt["transaction"]
                expected = bytes((n + transaction) % 251 for n in range(size))
                if receipt["sha256"] != hashlib.sha256(expected).hexdigest():
                    raise RuntimeError("Snapshot differs from synthetic sent bytes")
                if receipt["relative"] != "report.bin":
                    raise RuntimeError("Completion relative path changed")
        observations.append(result)
        print("PASS:", name, flush=True)
    summary = {"status": "PASS", "upstream_commit": provenance["commit"], "full_patched_daemon_build": True,
               "patched_daemon_started": False, "native_fixture_cases": len(observations),
               "synthetic_callback_helper": mock_helper, "actual_python_publisher": not mock_helper,
               "no_usb_mount_or_cloud_operations": True, "max_file_bytes": provenance["max_file_bytes"],
               "reserve_bytes": provenance["reserve_bytes"],
               "cases": observations}
    (work / "evidence.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--work", type=Path, help="New, nonexistent disposable output directory")
    parser.add_argument("--helper", type=Path, help="Explicit synthetic launcher for real Python publisher integration")
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()
    os.umask(0o077)
    if args.work is None:
        work = Path(tempfile.mkdtemp(prefix="rcsp-mtp-native-"))
    else:
        work = args.work.absolute()
        work.mkdir(mode=0o700)  # No reuse/deletion of previous evidence.
    print("Disposable source-only evidence:", work, flush=True)
    result = validate(args.upstream.resolve(strict=True), work, helper=args.helper, cases=args.case or CASES)
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, sort_keys=True))


if __name__ == "__main__":
    main()
