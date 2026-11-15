"""Explicit Pi-only creation of ONE NEW disposable image; never format a disk.

Not an installer or boot service. Run only after approval of this disposable test.
Existing paths, symlinks, mounted loops and non-Pi hosts are refused. Partial
images/records are retained for inspection; rerunning never overwrites them.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import time

from raspi_cloud_sync_pro.mass_storage_image import (
    EXPANDED_SIZE, LEGACY_SIZE, image_layout, validate_image_headers, validate_image_size,
)

DIRECTORY = Path("/var/lib/raspi-cloud-sync-pro/mass-storage-trial")
IMAGE = DIRECTORY / "disk.img"
SIZE = LEGACY_SIZE
OFFSET = 1024 ** 2
RESERVE = 256 * 1024 ** 2


def mbr(size=SIZE, offset=OFFSET):
    """One aligned type-07 partition; the first MiB remains outside exFAT."""
    if size % 512 or offset % 512 or not 512 <= offset < size <= 2 ** 41:
        raise ValueError("invalid_image_layout")
    sector = bytearray(512)
    sector[440:444] = os.urandom(4)
    sector[446:462] = struct.pack("<B3sB3sII", 0, b"\xfe\xff\xff", 7,
                                  b"\xfe\xff\xff", offset // 512, (size - offset) // 512)
    sector[510:512] = b"\x55\xaa"
    return bytes(sector)


def command(arguments):
    if arguments[0] not in {"/usr/sbin/losetup", "/usr/sbin/mkfs.exfat",
                            "/usr/sbin/fsck.exfat", "/usr/bin/findmnt"}:
        raise RuntimeError("unsupported_command")
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=90,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("image_tool_timed_out_read_only_reconciliation_required") from exc
    if result.returncode:
        raise RuntimeError("image_tool_failed")
    return result.stdout.strip()


def protected_directory(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("unprotected_image_directory")


def image_identity(path=IMAGE, *, size=None):
    size = validate_image_size(SIZE if size is None else size)
    protected_directory(path.parent)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 \
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != size:
        raise RuntimeError("unsafe_image")
    return (info.st_dev, info.st_ino, info.st_size)


def loops():
    return json.loads(command(["/usr/sbin/losetup", "--list", "--json", "--output",
                               "NAME,BACK-FILE,OFFSET,SIZELIMIT,RO"]))["loopdevices"]


def verify_loop(loop, identity, *, size=None):
    size = validate_image_size(SIZE if size is None else size)
    if not re.fullmatch(r"/dev/loop[0-9]+", loop) or image_identity(size=size) != identity:
        raise RuntimeError("image_or_loop_identity_changed")
    related = [entry for entry in loops() if entry.get("back-file") == str(IMAGE)]
    if len(related) != 1 or related[0].get("name") != loop \
            or related[0].get("ro") not in (False, 0) \
            or int(related[0].get("offset", -1)) != OFFSET \
            or int(related[0].get("sizelimit", -1)) != size - OFFSET:
        raise RuntimeError("exclusive_offset_loop_unverified")
    info = Path(loop).stat()
    if not stat.S_ISBLK(info.st_mode) or os.major(info.st_rdev) != 7:
        raise RuntimeError("not_a_loop_device")
    device = f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
    if any(line.split()[2] == device for line in Path("/proc/self/mountinfo").read_text().splitlines()):
        raise RuntimeError("loop_is_mounted")


def wait_detached(loop, timeout=5):
    """losetup -d is lazy; no export is allowed until inventory confirms removal."""
    deadline = time.monotonic() + timeout
    while True:
        related = [entry for entry in loops() if entry.get("back-file") == str(IMAGE)]
        if not related:
            return
        if len(related) != 1 or related[0].get("name") != loop:
            raise RuntimeError("unexpected_loop_after_detach")
        if time.monotonic() >= deadline:
            raise RuntimeError("loop_detach_unverified")
        time.sleep(0.05)


def receipt(name, data):
    fd = os.open(DIRECTORY / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def preflight(*, size=None):
    size = validate_image_size(SIZE if size is None else size)
    if os.geteuid() != 0 or platform.machine() != "aarch64" \
            or not Path("/proc/device-tree/model").read_text().startswith("Raspberry Pi Zero 2 W"):
        raise RuntimeError("requires_reviewed_pi_root")
    if os.stat("/proc/self/ns/mnt").st_ino != os.stat("/proc/1/ns/mnt").st_ino:
        raise RuntimeError("host_mount_namespace_required")
    protected_directory(DIRECTORY.parent)
    if os.path.lexists(DIRECTORY):
        raise RuntimeError("trial_directory_already_exists")
    root = command(["/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--target", str(DIRECTORY.parent)])
    if root.split() != ["/dev/mmcblk0p2", "ext4"]:
        raise RuntimeError("unexpected_target_filesystem")
    if shutil.disk_usage(DIRECTORY.parent).free < size + RESERVE:
        raise RuntimeError("insufficient_image_space")
    if any(entry.get("back-file") == str(IMAGE) for entry in loops()):
        raise RuntimeError("image_loop_already_exists")
    for tool in ("/usr/sbin/losetup", "/usr/sbin/mkfs.exfat", "/usr/sbin/fsck.exfat"):
        if not os.access(tool, os.X_OK):
            raise RuntimeError("image_tool_missing")


def prepare(*, size=None, approve_8gib=False):
    size = validate_image_size(SIZE if size is None else size)
    if size == EXPANDED_SIZE and approve_8gib is not True:
        raise RuntimeError("explicit_8gib_image_approval_required")
    preflight(size=size)  # Nothing is created before every target/space/tool check passes.
    DIRECTORY.mkdir(mode=0o700)
    parent_fd = os.open(DIRECTORY.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    receipt("creation-intent.json", {"schema_version": 1, "image": str(IMAGE),
                                    "size": size, "offset": OFFSET, "filesystem": "exfat"})
    fd = os.open(IMAGE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    loop = None
    identity = None
    try:
        os.posix_fallocate(fd, 0, size)
        partition_table = mbr(size, OFFSET)
        if os.pwrite(fd, partition_table, 0) != len(partition_table):
            raise RuntimeError("short_partition_table_write")
        os.fsync(fd)
        identity = image_identity(size=size)
        receipt("allocated.json", {"image_identity": identity})
        receipt("loop-attach-intent.json", {"image_identity": identity,
                                          "reconcile_loop_inventory_if_interrupted": True})
        loop = command(["/usr/sbin/losetup", "--find", "--show", "--offset", str(OFFSET),
                        "--sizelimit", str(size - OFFSET), str(IMAGE)])
        verify_loop(loop, identity, size=size)
        receipt("format-intent.json", {"loop": loop, "image_identity": identity})
        command(["/usr/sbin/mkfs.exfat", "-L", "CLOUDSTICK", loop])
        verify_loop(loop, identity, size=size)
        command(["/usr/sbin/fsck.exfat", "-n", loop])
        os.fsync(fd)
    finally:
        try:
            if loop is not None:
                verify_loop(loop, identity, size=size)
                command(["/usr/sbin/losetup", "--detach", loop])
                wait_detached(loop)
        finally:
            os.close(fd)
    if any(entry.get("back-file") == str(IMAGE) for entry in loops()):
        raise RuntimeError("loop_detach_unverified")
    fd = os.open(IMAGE, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev,opened.st_ino,opened.st_size) != identity \
                or image_identity(size=size) != identity or os.pread(fd, 512, 0) != partition_table:
            raise RuntimeError("partition_table_readback_failed")
        # No host export exists here; fsck above supplements these geometry checks.
        validate_image_headers(partition_table, os.pread(fd, 512, OFFSET), size)
        after = os.fstat(fd)
        if (after.st_dev,after.st_ino,after.st_size) != identity or image_identity(size=size) != identity:
            raise RuntimeError("image_identity_changed_during_readback")
    finally:
        os.close(fd)
    result = {"schema_version": 1, "image": str(IMAGE), "image_identity": image_identity(size=size),
              "filesystem": "exfat", "partition": 1, "volume_label": "CLOUDSTICK",
              "formatted_and_checked": True, "exported": False, "mounted": False}
    if size == EXPANDED_SIZE:
        result.update(schema_version=2, image_size_bytes=size, image_layout=image_layout(size))
    receipt("prepared.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-approved-disposable-image", action="store_true")
    parser.add_argument("--image-size-gib", type=int, choices=(1, 8), default=1)
    parser.add_argument("--approve-8gib-disposable-image", action="store_true")
    args = parser.parse_args()
    if not args.prepare_approved_disposable_image:
        parser.error("explicit disposable-image approval is required")
    if args.image_size_gib == 8 and not args.approve_8gib_disposable_image:
        parser.error("separate explicit 8 GiB approval is required")
    print(json.dumps(prepare(size=args.image_size_gib * LEGACY_SIZE,
                             approve_8gib=args.approve_8gib_disposable_image), indent=2))


if __name__ == "__main__":
    main()
