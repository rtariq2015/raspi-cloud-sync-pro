#!/usr/bin/env python3
"""Source-only real patched C receiver -> FD broker -> existing backup worker.

All USB reads are synthetic and the provider is a temporary local directory.
No daemon, real USB endpoint, image, cloud credentials or Google API is used.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import tempfile
import threading
import time

REPOSITORY = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY / "src"))
from raspi_cloud_sync_pro.device_modes import DevicePolicy
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.mtp_broker import handle_connection
from raspi_cloud_sync_pro.mtp_ingest import CompletionSettings
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.staged_worker import StagedWorker, WorkerSettings
from raspi_cloud_sync_pro.staging import ReadyBatch


class SyntheticRemote:
    destination_id = "synthetic-mtp-local-only"

    def __init__(self, root):
        self.root = root
        self.starts = []
        self.hashes = []

    def upload(self, batch, item):
        self.starts.append(time.monotonic())
        destination = self.root / item.remote_key
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination.write_bytes((batch.root / "files" / item.relative).read_bytes())

    def verify(self, batch, item):
        downloaded = (self.root / item.remote_key).read_bytes()
        digest = hashlib.sha256(downloaded).hexdigest()
        if len(downloaded) != item.size or digest != item.sha256:
            raise BackupError("synthetic_download_mismatch")
        self.hashes.append(digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("Run this disposable source fixture without root")
    os.umask(0o077)
    base = Path(tempfile.mkdtemp(prefix="rcsp-mtp-pipeline-"))
    native = base / "native"
    native.mkdir(mode=0o700)
    work, staging, control = [base / name for name in ("private", "staging", "control")]
    for directory in (work, staging, control):
        directory.mkdir(mode=0o700)
    # Only 'consecutive': actual C sends three different full objects, same name.
    source = native / "consecutive"
    settings = CompletionSettings(source, work, staging, os.getuid(), os.getuid(), os.getgid(), reserve_bytes=0)
    address = control / "completion.sock"
    helper = base / "fixture-complete-object"
    helper.write_text("#!" + sys.executable + " -I\n" +
        "import argparse,os,sys\n" +
        "sys.dont_write_bytecode=True\n" +
        "sys.path.insert(0," + repr(str(REPOSITORY / "src")) + ")\n" +
        "from raspi_cloud_sync_pro.mtp_broker import send_completion\n" +
        "from raspi_cloud_sync_pro.mtp_ingest import CompletionRequest\n" +
        "p=argparse.ArgumentParser()\n" +
        "for key in ('fd','size','session','transaction','object'):p.add_argument('--'+key,type=int,required=True)\n" +
        "p.add_argument('--relative',required=True)\n" +
        "a=p.parse_args()\n" +
        "send_completion(a.fd,CompletionRequest(a.relative,a.size,a.session,a.transaction,a.object)," +
        repr(str(address)) + ",expected_server_uid=" + str(os.getuid()) + ")\n")
    helper.chmod(0o700)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(str(address))
    listener.listen(1)
    listener.settimeout(120)
    observations, errors = [], []

    def serve_three():
        try:
            provider = SyntheticRemote(base / "synthetic-remote")
            worker_settings = WorkerSettings(base / "worker", staging, base / "unused-rclone.conf",
                                            (("google_drive_rclone", "synthetic:"),), DevicePolicy())
            with History(base / "history.sqlite3") as history:
                worker = StagedWorker(worker_settings, history, [provider])
                for index in range(3):
                    connection, _ = listener.accept()
                    began = time.monotonic()
                    with connection:
                        result = handle_connection(connection, settings)
                    published = time.monotonic()
                    if result.get("status") != "ready":
                        raise RuntimeError("Real publisher refused synthetic complete transfer")
                    batch = ReadyBatch.load(staging / result["batch"])
                    metadata = json.loads((batch.root / "mtp-completion.json").read_text())
                    size, transaction = metadata["size"], metadata["transaction"]
                    sent = bytes((number + transaction) % 251 for number in range(size))
                    expected_hash = hashlib.sha256(sent).hexdigest()
                    if batch.files[0].sha256 != expected_hash:
                        raise RuntimeError("C sent bytes and durable Python snapshot differ")
                    tick = worker.tick(now=100 + index, observation=("wifi", Connectivity.ONLINE, False))
                    if tick["jobs_added"] != 1 or tick["cloud_results"] != ["verified"]:
                        raise RuntimeError("Existing worker did not immediately process completed object")
                    observations.append({"size": size, "sha256": expected_hash,
                        "snapshot_and_download_hash_match": provider.hashes[-1] == expected_hash,
                        "completion_publish_ms": round((published - began) * 1000, 3),
                        "publish_to_first_worker_tick_upload_ms": round((provider.starts[-1] - published) * 1000, 3)})
                if len(history.jobs()) != 3 or any(row["state"] != "verified" for row in history.jobs()):
                    raise RuntimeError("Unexpected synthetic backup history")
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))

    server = threading.Thread(target=serve_three, daemon=True)
    server.start()
    validator_path = Path(__file__).resolve().parents[1] / "validate_upstream.py"
    spec = importlib.util.spec_from_file_location("rcsp_mtp_native_fixture", validator_path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    try:
        native_result = validator.validate(args.upstream, native, helper=helper, cases=("consecutive",))
        server.join(timeout=30)
        if server.is_alive() or errors or len(observations) != 3:
            raise RuntimeError("Pipeline fixture incomplete: " + repr(errors))
        result = {"status": "PASS", "real_patched_receiver": True, "real_fd_broker": True,
            "existing_staged_worker": True, "consecutive_transfers_without_eject": 3,
            "verified_synthetic_backups": 3, "metrics": observations,
            "upstream_commit": native_result["upstream_commit"], "usb_or_gadget_operations": False,
            "real_google_calls": False, "real_windows_test": False,
            "timing_scope": "Development computer synthetic IO and first manual worker tick, not USB/cloud latency"}
        (base / "pipeline-evidence.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"status": "PASS", "consecutive_transfers": 3, "evidence": str(base / "pipeline-evidence.json"),
                          "metrics": observations}, sort_keys=True))
    finally:
        listener.close()


if __name__ == "__main__":
    main()
