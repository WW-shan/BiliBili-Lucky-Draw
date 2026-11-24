import unittest
from unittest.mock import Mock, patch

from service.bilibili_errors import BilibiliVerificationRequired
from utils import globals
import main


class MainWorkflowTests(unittest.TestCase):
    def setUp(self):
        main._last_discovery_monotonic = None

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

        self.assertEqual(events, ['verify', 'share'])
        scanner.assert_not_called()

    def test_pipeline_discovers_only_when_both_queues_are_empty(self):
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

    def test_pipeline_reports_no_progress_when_only_cooling_rows_remain(self):
        counts = iter([
            {'ready': 0, 'pending': 5},
            {'ready': 0, 'pending': 5},
            {'ready': 0, 'pending': 5},
        ])
        with patch.object(main, 'get_queue_counts', side_effect=lambda: next(counts)), patch(
            'main.VerifyLotteryService'
        ) as verifier:
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
        ) as verifier, patch.object(main, 'time') as clock:
            verifier.return_value.verify_pending.return_value = {
                'checked': 3, 'confirmed': 0, 'excluded': 0, 'deferred': 3,
                'expired': 0, 'cooldown_started': 1,
            }
            progressed = main.run_pipeline_iteration()

        self.assertFalse(progressed)
        clock.sleep.assert_not_called()

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

    def test_verification_block_pauses_future_automatic_cycles(self):
        with patch.object(main, '_workflow_paused', False):
            with patch.object(
                main,
                'run_pipeline_iteration',
                side_effect=BilibiliVerificationRequired('manual check'),
            ) as cycle:
                main.run_safely()
                self.assertTrue(main._workflow_paused)

                main.run_safely()
                cycle.assert_called_once()


if __name__ == '__main__':
    unittest.main()
