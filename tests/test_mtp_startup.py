"""Disposable, in-memory MTP startup safety tests; never touch Linux gadgets."""
from collections import Counter
import unittest

from raspi_cloud_sync_pro import mtp_startup as startup


MTP = 'raspi-cloud-mtp-disposable'
MSC = 'raspi-cloud-disposable-msc'


class RecordingStartupIO:
    """Fake kernel/service observations and mutations, with deterministic time."""
    def __init__(self):
        self.events = []
        self.calls = Counter()
        self.hooks = {}
        self.snapshot = ('approved-source', 7, 42, 'same-staging-mount')
        self.inventory = {MSC: ''}
        self.profile_valid = True
        self.ready_values = [True]
        self.clock = 0.0
        self.receipt = None

    def event(self, operation):
        self.calls[operation] += 1
        self.events.append(operation)
        hook = self.hooks.get((operation, self.calls[operation]))
        if hook:
            hook(self)

    def preflight(self, settings):
        self.event('preflight')
        return self.snapshot

    def profiles(self):
        self.event('profiles')
        return dict(self.inventory)

    def prepare_profile(self, settings):
        self.event('prepare_profile')
        if MTP in self.inventory:
            raise startup.StartupError('fixture_profile_collision')
        self.inventory[MTP] = ''

    def verify_profile(self, settings):
        self.event('verify_profile')
        if MTP not in self.inventory or not self.profile_valid:
            raise startup.StartupError('fixture_profile_unverified')

    def seal(self, settings):
        self.event('seal')
        self.receipt = self.snapshot

    def verify_receipt(self, settings):
        self.event('verify_receipt')
        if self.receipt != self.snapshot:
            raise startup.StartupError('fixture_receipt_unverified')

    def ready(self, settings):
        self.event('ready')
        if len(self.ready_values) > 1:
            return self.ready_values.pop(0)
        return self.ready_values[0]

    def bind_udc(self, settings):
        self.event('bind_udc')
        if any(self.inventory.values()):
            raise startup.StartupError('fixture_udc_busy')
        self.inventory[MTP] = settings.udc

    def sleep(self, seconds):
        self.event('sleep')
        self.clock += seconds

    def monotonic(self):
        return self.clock


class MtpStartupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.settings = startup.StartupSettings(source_uid=1234, source_gid=1234,
                                                max_power_ma=100)
        self.io = RecordingStartupIO()
        self.controller = startup.MtpStartup(self.settings, self.io)

    def prepared(self):
        self.io.inventory[MTP] = ''
        self.io.receipt = self.io.snapshot
        return self.controller

    def assert_no_export(self):
        self.assertEqual(self.io.calls['bind_udc'], 0)
        self.assertTrue(all(not value for value in self.io.inventory.values()))

    @staticmethod
    def fail(message='fixture_preflight_refused'):
        def operation(io):
            raise startup.StartupError(message)
        return operation

    def test_cold_prepare_stays_detached_and_preserves_original_msc(self):
        self.controller.prepare()
        self.assertEqual(self.io.inventory, {MSC: '', MTP: ''})
        self.assertEqual(self.io.calls['prepare_profile'], 1)
        self.assertGreater(self.io.events.index('prepare_profile'),
                           self.io.events.index('preflight'))
        self.assertGreaterEqual(self.io.calls['verify_profile'], 1)
        self.assertGreaterEqual(self.io.calls['preflight'], 2)
        self.assert_no_export()

    def test_preflight_failure_precedes_profile_creation(self):
        self.io.hooks['preflight', 1] = self.fail()
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertEqual(self.io.inventory, {MSC: ''})
        self.assertEqual(self.io.calls['prepare_profile'], 0)
        self.assert_no_export()

    def test_existing_mtp_profile_is_not_silently_recreated_or_adopted(self):
        self.prepared()
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertEqual(self.io.calls['prepare_profile'], 0)
        self.assert_no_export()

    def test_foreign_bound_profile_prevents_prepare_without_detaching_it(self):
        self.io.inventory['foreign'] = self.settings.udc
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertEqual(self.io.inventory['foreign'], self.settings.udc)
        self.assertEqual(self.io.calls['prepare_profile'], 0)
        self.assertEqual(self.io.calls['bind_udc'], 0)

    def test_changed_preflight_snapshot_after_prepare_retains_partial_profile(self):
        self.io.hooks['preflight', 2] = lambda io: setattr(io, 'snapshot', ('changed-source',))
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertIn(MTP, self.io.inventory)
        self.assertEqual(self.io.calls['prepare_profile'], 1)
        self.assert_no_export()

    def test_unexpected_profile_created_during_prepare_is_not_ignored(self):
        self.io.hooks['verify_profile', 1] = lambda io: io.inventory.update({'unexpected': ''})
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertIn('unexpected', self.io.inventory)
        self.assert_no_export()

    def test_incomplete_profile_after_prepare_is_retained_without_export(self):
        self.io.hooks['verify_profile', 1] = lambda io: setattr(io, 'profile_valid', False)
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertIn(MTP, self.io.inventory)
        self.assert_no_export()

    def test_prepare_seals_only_verified_detached_setup(self):
        self.controller.prepare()
        self.assertEqual(self.io.receipt, self.io.snapshot)
        self.assertEqual(self.io.events[-1], 'seal')
        self.assert_no_export()

    def test_failed_seal_retains_partial_setup_without_export(self):
        self.io.hooks['seal', 1] = self.fail('fixture_receipt_write_failed')
        with self.assertRaises(startup.StartupError):
            self.controller.prepare()
        self.assertIn(MTP, self.io.inventory)
        self.assertIsNone(self.io.receipt)
        self.assert_no_export()

    def test_missing_or_stale_receipt_refuses_bind_before_readiness(self):
        for receipt in (None, ('different-boot-or-configuration',)):
            with self.subTest(receipt=receipt):
                self.setUp()
                self.prepared()
                self.io.receipt = receipt
                with self.assertRaises(startup.StartupError):
                    self.controller.bind()
                self.assertEqual(self.io.calls['ready'], 0)
                self.assert_no_export()

    def test_receipt_replacement_while_waiting_prevents_bind(self):
        self.prepared()
        self.io.ready_values = [False, True]
        self.io.hooks['sleep', 1] = lambda io: setattr(io, 'receipt', None)
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assert_no_export()

    def test_identity_change_at_ready_boundary_refuses_export(self):
        self.prepared()
        self.io.hooks['ready', 1] = lambda io: setattr(io, 'snapshot', ('changed-at-ready',))
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assert_no_export()

    def test_bind_waits_for_readiness_and_preserves_original_msc(self):
        self.prepared()
        self.io.ready_values = [False, False, True]
        self.controller.bind()
        self.assertGreaterEqual(self.io.calls['sleep'], 2)
        self.assertGreaterEqual(self.io.calls['preflight'], 3)
        self.assertGreaterEqual(self.io.calls['verify_profile'], 3)
        self.assertEqual(self.io.inventory, {MSC: '', MTP: self.settings.udc})
        self.assertEqual(self.io.calls['bind_udc'], 1)
        self.assertGreater(self.io.events.index('bind_udc'), self.io.events.index('ready'))

    def test_foreign_bound_profile_prevents_bind_without_detaching_it(self):
        self.prepared()
        self.io.inventory['foreign'] = self.settings.udc
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertEqual(self.io.inventory['foreign'], self.settings.udc)
        self.assertEqual(self.io.calls['bind_udc'], 0)

    def test_bound_mtp_is_not_blindly_rebound_on_restart(self):
        self.prepared()
        self.io.inventory[MTP] = self.settings.udc
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertEqual(self.io.inventory[MTP], self.settings.udc)
        self.assertEqual(self.io.calls['bind_udc'], 0)

    def test_missing_or_invalid_profile_prevents_bind(self):
        for present in (False, True):
            with self.subTest(present=present):
                self.setUp()
                if present:
                    self.prepared()
                    self.io.profile_valid = False
                with self.assertRaises(startup.StartupError):
                    self.controller.bind()
                self.assert_no_export()

    def test_readiness_timeout_never_exports_or_cleans_up(self):
        self.prepared()
        self.io.ready_values = [False]
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertLessEqual(self.io.clock, 21)
        self.assertGreaterEqual(self.io.clock, 20)
        self.assertIn(MTP, self.io.inventory)
        self.assert_no_export()

    def test_changed_source_identity_while_waiting_prevents_bind(self):
        self.prepared()
        self.io.ready_values = [False, True]
        self.io.hooks['sleep', 1] = lambda io: setattr(io, 'snapshot', ('new-inode',))
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assert_no_export()

    def test_foreign_binding_appearing_while_waiting_is_preserved_and_refused(self):
        self.prepared()
        self.io.ready_values = [False, True]
        self.io.hooks['sleep', 1] = lambda io: io.inventory.update({'foreign': self.settings.udc})
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertEqual(self.io.inventory['foreign'], self.settings.udc)
        self.assertEqual(self.io.calls['bind_udc'], 0)

    def test_profile_mutation_while_waiting_prevents_bind(self):
        self.prepared()
        self.io.ready_values = [False, True]
        self.io.hooks['sleep', 1] = lambda io: setattr(io, 'profile_valid', False)
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assert_no_export()

    def test_unsafe_socket_or_service_readiness_is_a_refusal_not_pending(self):
        self.prepared()
        self.io.hooks['ready', 1] = self.fail('fixture_unsafe_completion_peer')
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertEqual(self.io.calls['sleep'], 0)
        self.assert_no_export()

    def test_final_udc_race_is_not_resolved_by_forcing_foreign_detach(self):
        self.prepared()
        self.io.hooks['bind_udc', 1] = lambda io: io.inventory.update({'foreign': self.settings.udc})
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertEqual(self.io.inventory[MTP], '')
        self.assertEqual(self.io.inventory['foreign'], self.settings.udc)

    def test_failed_postbind_observation_does_not_blindly_unbind_or_retry(self):
        self.prepared()
        def changed_after_bind(io):
            io.hooks['profiles', io.calls['profiles'] + 1] = lambda current: current.inventory.update({'unexpected': ''})
        self.io.hooks['bind_udc', 1] = changed_after_bind
        with self.assertRaises(startup.StartupError):
            self.controller.bind()
        self.assertEqual(self.io.inventory[MTP], self.settings.udc)
        self.assertEqual(self.io.calls['bind_udc'], 1)


if __name__ == '__main__':
    unittest.main()
