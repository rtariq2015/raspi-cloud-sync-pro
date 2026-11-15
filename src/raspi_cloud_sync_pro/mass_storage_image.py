"""Pure image policy; never opens, mounts, creates or approves a device.

Header checks supplement, never replace, exclusive ownership and fsck.exfat -n.
"""

import json

from .errors import BackupError

LEGACY_SIZE = 1024 ** 3
EXPANDED_SIZE = 8 * LEGACY_SIZE
PARTITION_OFFSET = 1024 ** 2


def _require(value):
    if not value:
        raise BackupError('mass_storage_image_refused')


def validate_image_size(size):
    _require(type(size) is int and size in (LEGACY_SIZE, EXPANDED_SIZE))
    return size


def image_layout(size):
    validate_image_size(size)
    return {'partition_table': 'mbr', 'partition': 1, 'start_sector': 2048,
            'partition_sectors': size // 512 - 2048,
            'filesystem': 'exfat', 'bytes_per_sector': 512}


def validate_image_approval(approval, identity):
    """Metadata only: schema 1 stays 1 GiB; schema 2 explicitly binds capacity."""
    _require(type(approval) is dict and type(identity) in (tuple, list)
             and len(identity) == 3 and all(type(i) is int and i >= 0 for i in identity))
    size = validate_image_size(identity[2])
    version = approval.get('schema_version')
    _require(type(version) is int and version in (1, 2))
    expected = {'schema_version': version, 'mass_storage_appliance_approved': True,
                'image_identity': list(identity)}
    if version == 1:
        _require(size == LEGACY_SIZE)
    else:
        expected.update(image_size_bytes=size, image_layout=image_layout(size))
    # JSON comparison also rejects bool/float aliases for integer fields.
    try:
        exact = json.dumps(approval, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True)
    except (ValueError, TypeError):
        exact = False
    _require(exact)
    return size


def validate_image_headers(mbr, boot, size):
    """Called only after exclusive access; fsck still checks full exFAT integrity."""
    layout = image_layout(size)
    _require(len(mbr) == len(boot) == 512 and mbr[510:] == boot[510:] == b'\x55\xaa'
             and mbr[446] == 0 and mbr[450] == 7
             and int.from_bytes(mbr[454:458], 'little') == layout['start_sector']
             and int.from_bytes(mbr[458:462], 'little') == layout['partition_sectors']
             and not any(mbr[462:510]) and boot[3:11] == b'EXFAT   '
             and boot[108] == 9
             and int.from_bytes(boot[64:72], 'little') in (0, layout['start_sector'])
             and int.from_bytes(boot[72:80], 'little') == layout['partition_sectors']
             and int.from_bytes(boot[106:108], 'little') & 6 == 0)
