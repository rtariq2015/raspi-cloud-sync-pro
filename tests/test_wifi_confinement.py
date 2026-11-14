import ast
import copy
import ctypes
import errno
import os
import stat
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import wifi_confinement as confinement

GROUP = '/system.slice/raspi-cloud-google-test-' + 'a' * 32 + '.service'


class Kernel:
    """Read-only fake BPF kernel backed by real locally owned descriptors."""

    def __init__(self):
        self.attachments = {0: [101], 1: [102]}
        self.attach_flags = {0: 2, 1: 2}
        self.programs = {
            101: {'name': b'sd_restrictif_i', 'type': 8, 'uid': 0, 'maps': [201, 202]},
            102: {'name': b'sd_restrictif_e', 'type': 8, 'uid': 0, 'maps': [201, 202]},
        }
        self.maps = {
            201: {'name': b'sd_restrictif', 'type': 1, 'key_size': 4, 'value_size': 1,
                  'maximum': 1, 'flags': 0, 'values': {7: 0}},
            202: {'name': b'restrict.rodata', 'type': 2, 'key_size': 4, 'value_size': 1,
                  'maximum': 1, 'flags': 0x480, 'values': {0: 1}},
        }
        self.fds = {}
        self.opened = []
        self.calls = []
        self.chain = ((1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6))
        self.interface = 7
        self.missing_path = False

    def descriptor(self, kind, identifier):
        fd = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
        self.opened.append(fd)
        self.fds[fd] = kind, identifier
        return fd

    def open_chain(self, group, owner):
        if self.missing_path:
            raise FileNotFoundError('PRIVATE PATH MUST NOT LEAK')
        return owner.add(self.descriptor('cgroup', 0)), self.chain

    @staticmethod
    def u32(address):
        return ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32)).contents

    def bpf(self, number, attribute, *, missing=False):
        self.calls.append(number)
        if number == 16:
            values = self.attachments[attribute.attach_type]
            attribute.prog_cnt = len(values)
            attribute.attach_flags = self.attach_flags[attribute.attach_type]
            target = ctypes.cast(attribute.prog_ids, ctypes.POINTER(ctypes.c_uint32))
            for index, value in enumerate(values[:16]):
                target[index] = value
            return 0
        if number in (13, 14):
            return self.descriptor('program' if number == 13 else 'map', attribute.identifier)
        kind, identifier = self.fds[attribute.fd]
        assert kind == 'map'
        values = self.maps[identifier]['values']
        if number == 1:
            key = self.u32(attribute.key).value
            if key not in values:
                raise OSError(errno.ENOENT, 'PRIVATE MAP DETAIL')
            ctypes.cast(attribute.value, ctypes.POINTER(ctypes.c_ubyte)).contents.value = values[key]
            return 0
        if number == 4:
            keys = sorted(values)
            if attribute.key:
                current = self.u32(attribute.key).value
                keys = [key for key in keys if key > current]
            if not keys:
                if missing:
                    return None
                raise OSError(errno.ENOENT, 'PRIVATE MAP DETAIL')
            self.u32(attribute.value).value = keys[0]
            return 0
        raise AssertionError('mutating or unplanned BPF operation')

    def object_info(self, fd, target):
        kind, identifier = self.fds[fd]
        target.identifier = identifier
        source = self.programs[identifier] if kind == 'program' else self.maps[identifier]
        for name, value in source.items():
            if name not in ('maps', 'values'):
                setattr(target, name, value)
        if kind == 'program':
            supplied = target.map_count
            address = target.maps
            target.map_count = len(source['maps'])
            if address:
                values = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32))
                for index, value in enumerate(source['maps'][:supplied]):
                    values[index] = value
        return target

    @contextmanager
    def installed(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(confinement.os, 'getresuid', return_value=(0, 0, 0)))
            stack.enter_context(mock.patch.object(confinement.platform, 'system', return_value='Linux'))
            stack.enter_context(mock.patch.object(confinement.platform, 'machine', return_value='aarch64'))
            stack.enter_context(mock.patch.object(confinement, '_current_interface_index', side_effect=lambda: self.interface))
            stack.enter_context(mock.patch.object(confinement, '_open_chain', side_effect=self.open_chain))
            stack.enter_context(mock.patch.object(confinement, '_bpf', side_effect=self.bpf))
            stack.enter_context(mock.patch.object(confinement, '_object_info', side_effect=self.object_info))
            yield self

    def assert_closed(self, case):
        for fd in set(self.opened):
            with case.assertRaises(OSError) as raised:
                os.fstat(fd)
            case.assertEqual(raised.exception.errno, errno.EBADF)


class WifiConfinementTests(unittest.TestCase):
    def test_valid_held_proof_rechecks_and_all_descriptors_close(self):
        kernel = Kernel()
        with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
            self.assertEqual(proof.recheck(), {
                'bpf_policy_observed': True, 'inspection_held': True, 'canaries_performed': False,
                'strict_egress_accepted': False, 'credential_release_authorized': False})
            for fd in kernel.fds:
                try:
                    os.fstat(fd)
                except OSError:
                    continue  # Temporary fresh-path descriptors were already closed.
                self.assertFalse(os.get_inheritable(fd))
            self.assertEqual(set(kernel.calls), {1, 4, 13, 14, 16})
        kernel.assert_closed(self)
        self.assertFalse(proof.safe_projection()['inspection_held'])
        self.assertFalse(proof.safe_projection()['bpf_policy_observed'])

    def test_nonroot_and_partial_root_identity_refuse_before_any_io(self):
        for identity in ((1000, 1000, 1000), (0, 1000, 0), (1000, 0, 0), (0, 0, 1000)):
            with self.subTest(identity=identity), mock.patch.object(confinement.os, 'getresuid', return_value=identity), \
                    mock.patch.object(confinement.os, 'open') as opened, \
                    mock.patch.object(confinement, '_current_interface_index') as interface:
                with self.assertRaisesRegex(confinement.WifiConfinementError, '^wifi_confinement_unavailable$'):
                    with confinement.hold_wifi_confinement(GROUP, 7):
                        self.fail('yielded')
                opened.assert_not_called()
                interface.assert_not_called()

    def test_platform_refusals_precede_cgroup_and_bpf_reads(self):
        for system, machine in (('Darwin', 'aarch64'), ('Linux', 'x86_64')):
            with Kernel().installed(), mock.patch.object(confinement.platform, 'system', return_value=system), \
                    mock.patch.object(confinement.platform, 'machine', return_value=machine), \
                    mock.patch.object(confinement, '_open_chain') as opened:
                with self.assertRaises(confinement.WifiConfinementError):
                    with confinement.hold_wifi_confinement(GROUP, 7):
                        self.fail('yielded')
                opened.assert_not_called()

    def test_aarch64_uname_with_32bit_python_refuses_before_interface_or_bpf_io(self):
        with Kernel().installed(), mock.patch.object(confinement.ctypes, 'sizeof', return_value=4), \
                mock.patch.object(confinement, '_current_interface_index') as interface, \
                mock.patch.object(confinement, '_open_chain') as opened:
            with self.assertRaises(confinement.WifiConfinementError):
                with confinement.hold_wifi_confinement(GROUP, 7):
                    self.fail('yielded')
            interface.assert_not_called()
            opened.assert_not_called()

    def test_exact_new_unit_path_and_strict_numeric_interface_only(self):
        invalid = ('/system.slice/ssh.service', GROUP.replace('a' * 32, 'A' * 32),
                   GROUP.replace('a' * 32, 'a' * 31), GROUP + '/child',
                   GROUP.replace('/system.slice/', '/user.slice/'), '/system.slice/../' + GROUP.rsplit('/', 1)[1], None)
        with Kernel().installed(), mock.patch.object(confinement, '_open_chain') as opened:
            for group in invalid:
                with self.subTest(group=group), self.assertRaises(confinement.WifiConfinementError):
                    with confinement.hold_wifi_confinement(group, 7):
                        self.fail('yielded')
            for index in (True, 0, -1, 2 ** 31, '7', None):
                with self.subTest(index=index), self.assertRaises(confinement.WifiConfinementError):
                    with confinement.hold_wifi_confinement(GROUP, index):
                        self.fail('yielded')
            opened.assert_not_called()

    def test_current_wlan_index_is_required_at_acquire(self):
        kernel = Kernel()
        kernel.interface = 8
        with kernel.installed(), self.assertRaises(confinement.WifiConfinementError):
            with confinement.hold_wifi_confinement(GROUP, 7):
                self.fail('yielded')
        self.assertEqual(kernel.opened, [])

    def test_changed_wlan_index_invalidates_and_latches_closed(self):
        kernel = Kernel()
        with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
            kernel.interface = 8
            with self.assertRaises(confinement.WifiConfinementError):
                proof.recheck()
            kernel.interface = 7
            with self.assertRaisesRegex(confinement.WifiConfinementError, '^wifi_confinement_closed$'):
                proof.recheck()
            self.assertFalse(proof.safe_projection()['bpf_policy_observed'])
        kernel.assert_closed(self)

    def refused_kernel(self, kernel):
        with kernel.installed(), self.assertRaises(confinement.WifiConfinementError) as error:
            with confinement.hold_wifi_confinement(GROUP, 7):
                self.fail('yielded')
        self.assertNotIn(GROUP, str(error.exception))
        kernel.assert_closed(self)

    def test_missing_ingress_or_egress_is_not_a_proof(self):
        for attach_type in (0, 1):
            with self.subTest(attach_type=attach_type):
                kernel = Kernel()
                kernel.attachments[attach_type] = []
                self.refused_kernel(kernel)

    def test_duplicate_or_excess_attachment_ids_are_refused(self):
        for values in ([101, 101], [0], list(range(1, 18))):
            with self.subTest(values=values):
                kernel = Kernel()
                kernel.attachments[0] = values
                self.refused_kernel(kernel)

    def test_wrong_program_name_type_or_creator_is_refused(self):
        for key, value in (('name', b'unrelated'), ('type', 1), ('uid', 1000)):
            with self.subTest(key=key):
                kernel = Kernel()
                kernel.programs[101][key] = value
                self.refused_kernel(kernel)

    def test_two_matching_programs_are_ambiguous(self):
        kernel = Kernel()
        kernel.programs[103] = copy.deepcopy(kernel.programs[101])
        kernel.attachments[0].append(103)
        self.refused_kernel(kernel)

    def test_missing_extra_or_duplicate_program_maps_refused(self):
        for maps in ([201], [201, 202, 203], [201, 201], [201, 0]):
            with self.subTest(maps=maps):
                kernel = Kernel()
                kernel.programs[101]['maps'] = maps
                self.refused_kernel(kernel)

    def test_ingress_and_egress_must_share_both_map_objects(self):
        for original, replacement in ((201, 203), (202, 204)):
            with self.subTest(original=original):
                kernel = Kernel()
                kernel.maps[replacement] = copy.deepcopy(kernel.maps[original])
                kernel.programs[102]['maps'] = [replacement if value == original else value for value in (201, 202)]
                self.refused_kernel(kernel)

    def test_exact_map_names_layout_and_capacity_are_required(self):
        for identifier in (201, 202):
            for key, value in (('name', b'wrong'), ('type', 3), ('key_size', 8), ('value_size', 8), ('maximum', 2)):
                with self.subTest(identifier=identifier, key=key):
                    kernel = Kernel()
                    kernel.maps[identifier][key] = value
                    self.refused_kernel(kernel)

    def test_rodata_requires_allowlist_true_and_readonly_program_flag(self):
        for value in (0, 2):
            kernel = Kernel()
            kernel.maps[202]['values'] = {0: value}
            self.refused_kernel(kernel)
        for flags in (0, 0x400, 0x81, 0x481):
            kernel = Kernel()
            kernel.maps[202]['flags'] = flags
            self.refused_kernel(kernel)
        for flags in (0x80, 0x480):
            kernel = Kernel()
            kernel.maps[202]['flags'] = flags
            with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
                self.assertTrue(proof.recheck()['bpf_policy_observed'])
            kernel.assert_closed(self)

    def test_wlan_map_requires_one_current_key_and_expected_dummy_value(self):
        for values in ({}, {8: 0}, {7: 0, 8: 0}, {7: 1}):
            with self.subTest(values=values):
                kernel = Kernel()
                kernel.maps[201]['values'] = values
                self.refused_kernel(kernel)
        kernel = Kernel()
        kernel.maps[201]['flags'] = 1
        self.refused_kernel(kernel)

    def test_current_detachment_is_detected_despite_live_retained_objects(self):
        kernel = Kernel()
        with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
            held = tuple(proof._owner.fds)
            kernel.attachments[1] = []
            for fd in held:
                os.fstat(fd)
            with self.assertRaises(confinement.WifiConfinementError):
                proof.recheck()
        kernel.assert_closed(self)

    def test_replaced_program_or_changed_attachment_flags_are_detected(self):
        for change in ('program', 'flags', 'extra'):
            with self.subTest(change=change):
                kernel = Kernel()
                with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
                    kernel.programs[103] = copy.deepcopy(kernel.programs[101])
                    if change == 'program':
                        kernel.attachments[0] = [103]
                    elif change == 'extra':
                        kernel.attachments[0].append(103)
                    else:
                        kernel.attach_flags[0] = 0
                    with self.assertRaises(confinement.WifiConfinementError):
                        proof.recheck()
                kernel.assert_closed(self)

    def test_changed_map_values_are_detected_on_each_recheck(self):
        for identifier, values in ((201, {8: 0}), (201, {7: 0, 8: 0}), (202, {0: 0})):
            with self.subTest(identifier=identifier, values=values):
                kernel = Kernel()
                with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
                    kernel.maps[identifier]['values'] = values
                    with self.assertRaises(confinement.WifiConfinementError):
                        proof.recheck()
                kernel.assert_closed(self)

    def test_changed_map_ids_or_layout_are_detected(self):
        for change in ('id', 'layout'):
            kernel = Kernel()
            with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
                if change == 'id':
                    kernel.programs[101]['maps'] = [201, 203]
                else:
                    kernel.maps[202]['value_size'] = 2
                with self.assertRaises(confinement.WifiConfinementError):
                    proof.recheck()
            kernel.assert_closed(self)

    def test_missing_or_replaced_named_cgroup_never_uses_old_fd_as_evidence(self):
        for change in ('missing', 'replacement', 'ancestor'):
            kernel = Kernel()
            with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
                if change == 'missing':
                    kernel.missing_path = True
                elif change == 'replacement':
                    kernel.chain = kernel.chain[:-1] + ((1, 99),)
                else:
                    kernel.chain = ((1, 99),) + kernel.chain[1:]
                with self.assertRaises(confinement.WifiConfinementError) as error:
                    proof.recheck()
                self.assertNotIn('PRIVATE', str(error.exception))
            kernel.assert_closed(self)

    def test_body_exception_is_preserved_and_all_fds_close(self):
        kernel = Kernel()
        with kernel.installed(), self.assertRaisesRegex(ValueError, '^caller failure$'):
            with confinement.hold_wifi_confinement(GROUP, 7):
                raise ValueError('caller failure')
        kernel.assert_closed(self)

    def test_acquisition_failure_after_opened_fds_never_leaks_them(self):
        kernel = Kernel()
        kernel.maps[202]['values'] = {}
        self.refused_kernel(kernel)
        self.assertGreaterEqual(len(kernel.opened), 4)

    def test_safe_projection_and_repr_never_expose_paths_ids_or_authority(self):
        kernel = Kernel()
        with kernel.installed(), confinement.hold_wifi_confinement(GROUP, 7) as proof:
            self.assertEqual(repr(proof), '<WifiConfinementProof private>')
            projection = proof.safe_projection()
            self.assertTrue(all(type(value) is bool for value in projection.values()))
            self.assertFalse(projection['strict_egress_accepted'])
            self.assertFalse(projection['credential_release_authorized'])
            text = repr(projection) + repr(proof)
            for private in (GROUP, '101', '201', '202'):
                self.assertNotIn(private, text)
        with self.assertRaisesRegex(confinement.WifiConfinementError, '^wifi_confinement_closed$'):
            proof.recheck()

    def test_bpf_readonly_allowlist_and_enoent_only_for_next(self):
        library = mock.Mock()
        library.syscall.return_value = 0
        with mock.patch.object(confinement.ctypes, 'CDLL', return_value=library):
            for number in (0, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 17, 18):
                with self.subTest(number=number), self.assertRaises(confinement.WifiConfinementError):
                    confinement._bpf(number, confinement._Id())
            for number in (1, 13, 14, 15, 16):
                with self.assertRaises(confinement.WifiConfinementError):
                    confinement._bpf(number, confinement._Id(), missing=True)
            library.syscall.assert_not_called()
            library.syscall.return_value = -1
            with mock.patch.object(confinement.ctypes, 'get_errno', return_value=errno.ENOENT):
                self.assertIsNone(confinement._bpf(4, confinement._Element(), missing=True))
                with self.assertRaises(confinement.WifiConfinementError):
                    confinement._bpf(1, confinement._Element())
            with mock.patch.object(confinement.ctypes, 'get_errno', return_value=errno.EPERM), \
                    self.assertRaises(confinement.WifiConfinementError):
                confinement._bpf(4, confinement._Element(), missing=True)

    def test_bpf_abi_layout_matches_pinned_aarch64_kernel_uapi(self):
        self.assertEqual(ctypes.sizeof(confinement._Query), 32)
        self.assertEqual(confinement._ProgramInfo.name.offset, 64)
        self.assertEqual(confinement._MapInfo.name.offset, 24)
        self.assertEqual(ctypes.sizeof(confinement._Element), 32)
        self.assertEqual(confinement._Element.key.offset, 8)

    def test_directory_identity_requires_root_owned_nonwritable_directory(self):
        for mode, uid in ((stat.S_IFDIR | 0o777, 0), (stat.S_IFDIR | 0o755, 1000), (stat.S_IFREG | 0o600, 0)):
            with mock.patch.object(confinement.os, 'fstat', return_value=SimpleNamespace(
                    st_mode=mode, st_uid=uid, st_dev=1, st_ino=2)), self.assertRaises(confinement.WifiConfinementError):
                confinement._directory_identity(99)

    def test_open_chain_rejects_real_symlink_and_writable_ancestor_and_closes_all(self):
        real_open, real_fstat = os.open, os.fstat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'sys' / 'fs' / 'cgroup' / 'system.slice' / GROUP.rsplit('/', 1)[1]
            target.mkdir(parents=True)
            def rooted(path, flags, *, dir_fd=None):
                return real_open(root if path == '/' else path, flags, dir_fd=dir_fd)
            def root_stat(fd):
                value = real_fstat(fd)
                return SimpleNamespace(st_uid=0, st_mode=value.st_mode, st_dev=value.st_dev, st_ino=value.st_ino)
            for change in ('symlink', 'writable'):
                parent = root / 'sys' / 'fs'
                if change == 'symlink':
                    parent.rename(parent.with_name('fs-real'))
                    parent.symlink_to('fs-real')
                else:
                    parent.unlink()
                    parent.with_name('fs-real').rename(parent)
                    parent.chmod(0o777)
                owner = confinement._Descriptors()
                with mock.patch.object(confinement.os, 'open', side_effect=rooted), \
                        mock.patch.object(confinement.os, 'fstat', side_effect=root_stat), \
                        self.assertRaises((confinement.WifiConfinementError, OSError)):
                    confinement._open_chain(GROUP, owner)
                held = tuple(owner.fds)
                self.assertTrue(owner.close())
                for fd in held:
                    with self.assertRaises(OSError):
                        real_fstat(fd)

    def test_descriptor_cleanup_continues_after_one_close_error(self):
        owner = confinement._Descriptors()
        first = owner.add(os.open('/dev/null', os.O_RDONLY))
        second = owner.add(os.open('/dev/null', os.O_RDONLY))
        os.close(second)
        self.assertFalse(owner.close())
        with self.assertRaises(OSError):
            os.fstat(first)
        self.assertEqual(owner.fds, [])

    def test_source_python39_and_no_process_service_or_credential_dependencies(self):
        source = Path(confinement.__file__).read_text()
        tree = ast.parse(source, feature_version=(3, 9))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn('subprocess', imports)
        for forbidden in ('subprocess', 'oauth', 'rclone', 'google_test', 'providers'):
            self.assertFalse(any(isinstance(node, ast.Import) and any(alias.name == forbidden for alias in node.names)
                                 for node in ast.walk(tree)))
        for forbidden in ('Popen', 'systemctl', 'setuid', 'setgid', 'BPF_PROG_LOAD', 'update_elem'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
