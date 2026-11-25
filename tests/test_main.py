import unittest
from unittest.mock import Mock, patch

from service.bilibili_errors import BilibiliVerificationRequired
from utils import globals
import main


class MainWorkflowTests(unittest.TestCase):
    def setUp(self):
        main._last_discovery_monotonic = None
        main._workflow_paused = False
        main._workflow_pause_until = 0.0

    def test_ready_check_uses_accounts_configured_for_actions(self):
        with patch.dict('os.environ', {'multi_users': '123|456'}), patch(
            'main.VerifyLotteryService'
        ) as verifier:
            service = verifier.return_value
            service.draw_dao.verification_queue_counts.return_value = {
                'eligible': 0, 'total': 0,
            }
            service.ready_dao.query_ready.side_effect = [[], [{'dyn_url': 'example'}]]

            self.assertEqual(main.get_queue_counts()['ready'], 1)

        self.assertEqual(
            [call.kwargs for call in service.ready_dao.query_ready.call_args_list],
            [{'limit': 1, 'user_id': '123'}, {'limit': 1, 'user_id': '456'}],
        )

    def test_pipeline_runs_ready_actions_before_verification(self):
        events = []
        counts = iter([
            {'ready': 2, 'pending': 5},
            {'ready': 0, 'pending': 5},
            {'ready': 0, 'pending': 5},
        ])

        with patch.object(main, 'get_queue_counts', side_effect=lambda: next(counts)), patch(
            'main.MultiUsersShareService'
        ) as share_service, patch(
            'main.VerifyLotteryService'
        ) as verifier, patch(
            'main.SearchDynamicByUps'
        ) as scanner:
            share_service.return_value.do_multi_uses_share.side_effect = lambda: events.append('share')
            verifier.return_value.verify_pending.side_effect = lambda *args: (
                events.append('verify') or {'checked': 1, 'confirmed': 0, 'excluded': 0, 'expired': 0}
            )
            scanner.return_value.init_search.side_effect = lambda: events.append('discover')

            main.run_pipeline_iteration()

        self.assertEqual(events[:2], ['share', 'verify'])

    def test_pipeline_drains_ready_queue_created_by_verification(self):
        events = []
        counts = iter([
            {'ready': 0, 'pending': 5},
            {'ready': 1, 'pending': 3},
            {'ready': 1, 'pending': 3},
            {'ready': 0, 'pending': 3},
        ])

        with patch.object(main, 'get_queue_counts', side_effect=lambda: next(counts)), patch(
            'main.MultiUsersShareService'
        ) as share_service, patch(
            'main.VerifyLotteryService'
        ) as verifier, patch(
            'main.SearchDynamicByUps'
        ) as scanner:
            share_service.return_value.do_multi_uses_share.side_effect = lambda: events.append('share')
            verifier.return_value.verify_pending.side_effect = lambda *args: (
                events.append('verify') or {'checked': 1, 'confirmed': 0, 'excluded': 0, 'expired': 0}
            )
            scanner.return_value.init_search.side_effect = lambda: events.append('discover')

            main.run_pipeline_iteration()

        self.assertEqual(events, ['verify', 'share', 'discover'])

    def test_pipeline_discovers_when_both_queues_are_empty(self):
        with patch.object(main, 'get_queue_counts', return_value={'ready': 0, 'pending': 0}), patch(
            'main.SearchDynamicByUps'
        ) as scanner:
            scanner.return_value.init_search.return_value = None
            main.run_pipeline_iteration()

        scanner.return_value.init_search.assert_called_once_with()

    def test_pipeline_discovers_while_pending_rows_are_cooling(self):
        with patch.object(
            main,
            'get_queue_counts',
            return_value={'ready': 0, 'pending': 0, 'blocked': 2},
        ), patch.object(main, 'time') as clock, patch(
            'main.SearchDynamicByUps'
        ) as scanner:
            clock.monotonic.return_value = 1000
            scanner.return_value.init_search.return_value = None
            self.assertTrue(main.run_pipeline_iteration())

        scanner.return_value.init_search.assert_called_once_with()

    def test_discovery_is_throttled_after_first_attempt(self):
        with patch.object(
            main,
            'get_queue_counts',
            return_value={'ready': 0, 'pending': 0, 'blocked': 1},
        ), patch.object(main, 'time') as clock, patch(
            'main.SearchDynamicByUps'
        ) as scanner:
            clock.monotonic.side_effect = [1000, 1001]
            scanner.return_value.init_search.return_value = None
            self.assertTrue(main.run_pipeline_iteration())
            self.assertFalse(main.run_pipeline_iteration())

        scanner.return_value.init_search.assert_called_once_with()

    def test_discovery_uses_configured_interval_instead_of_verification_backoff(self):
        now = [1000.0]
        with patch.object(
            main,
            'get_queue_counts',
            return_value={'ready': 0, 'pending': 0, 'blocked': 1},
        ), patch.object(main, 'time') as clock, patch.object(
            globals, 'discovery_interval_hours', 1
        ), patch('main.SearchDynamicByUps') as scanner:
            clock.monotonic.side_effect = lambda: now[0]
            scanner.return_value.init_search.return_value = None

            self.assertTrue(main.run_pipeline_iteration())
            now[0] += 901
            self.assertFalse(main.run_pipeline_iteration())
            now[0] += 2699
            self.assertTrue(main.run_pipeline_iteration())

        self.assertEqual(scanner.return_value.init_search.call_count, 2)

    def test_pipeline_reports_no_progress_when_only_cooling_rows_remain(self):
        counts = iter([
            {'ready': 0, 'pending': 5},
            {'ready': 0, 'pending': 5},
            {'ready': 0, 'pending': 5},
        ])
        with patch.object(main, 'get_queue_counts', side_effect=lambda: next(counts)), patch(
            'main.VerifyLotteryService'
        ) as verifier, patch.object(main, '_discovery_due', return_value=False):
            verifier.return_value.verify_pending.return_value = {
                'checked': 0, 'confirmed': 0, 'excluded': 0, 'deferred': 0,
                'expired': 0, 'skipped_recent': 5, 'cooldown_started': 0,
            }
            progressed = main.run_pipeline_iteration()

        self.assertFalse(progressed)

    def test_continuous_loop_sleeps_only_after_no_progress(self):
        with patch.object(main, '_workflow_paused', False), patch.object(
            main, 'run_pipeline_iteration', side_effect=[True, False, KeyboardInterrupt]
        ) as iteration, patch.object(
            main, 'cleanup_stale_selenium_sessions', return_value=0
        ), patch.object(main, 'time') as clock:
            clock.sleep.side_effect = [None, KeyboardInterrupt]
            with self.assertRaises(KeyboardInterrupt):
                main.run_continuous_worker()

        self.assertEqual(iteration.call_count, 3)
        self.assertEqual(clock.sleep.call_count, 1)

    def test_pipeline_honors_verification_cooldown(self):
        counts = iter([
            {'ready': 0, 'pending': 5},
            {'ready': 0, 'pending': 5},
        ])
        with patch.object(main, 'get_queue_counts', side_effect=lambda: next(counts)), patch(
            'main.VerifyLotteryService'
        ) as verifier, patch.object(main, 'time') as clock, patch.object(
            main, 'run_discovery'
        ) as discover:
            verifier.return_value.verify_pending.return_value = {
                'checked': 3, 'confirmed': 0, 'excluded': 0, 'deferred': 3,
                'expired': 0, 'cooldown_started': 1,
            }
            progressed = main.run_pipeline_iteration()

        self.assertFalse(progressed)
        clock.sleep.assert_not_called()
        discover.assert_not_called()

    def test_transient_selenium_session_failure_does_not_pause_worker(self):
        with patch.object(main, '_workflow_paused', False), patch.object(
            main,
            'run_pipeline_iteration',
            side_effect=RuntimeError('Could not start a new session. New session request timed out'),
        ) as cycle:
            main.run_safely()
            main.run_safely()

        self.assertFalse(main._workflow_paused)
        self.assertEqual(cycle.call_count, 2)

    def test_verification_block_enters_timed_cooldown_and_auto_recovers(self):
        now = [1000.0]
        with patch.object(
            main,
            'run_pipeline_iteration',
            side_effect=[BilibiliVerificationRequired('manual check'), None],
        ) as cycle, patch.object(globals, 'verification_cooldown_minutes', 30), patch.object(
            main, 'time'
        ) as clock:
            clock.monotonic.side_effect = lambda: now[0]

            self.assertFalse(main.run_safely())
            self.assertGreater(main._workflow_pause_until, now[0])
            self.assertFalse(main._workflow_paused)

            self.assertFalse(main.run_safely())
            self.assertEqual(cycle.call_count, 1)

            now[0] += 1801
            self.assertIsNone(main.run_safely())
            self.assertEqual(cycle.call_count, 2)
            self.assertEqual(main._workflow_pause_until, 0.0)


if __name__ == '__main__':
    unittest.main()
