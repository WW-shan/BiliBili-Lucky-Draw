import unittest
from unittest.mock import Mock, patch

from service.bilibili_errors import BilibiliVerificationRequired
from utils import globals
import main


class MainWorkflowTests(unittest.TestCase):
    def test_run_cycle_verifies_candidates_before_sharing(self):
        events = []
        with patch.object(globals, 'ups', ['100680137']), patch(
            'main.SearchDynamicByUps'
        ) as scanner, patch(
            'main.VerifyLotteryService'
        ) as verifier, patch(
            'main.MultiUsersShareService'
        ) as share_service:
            scanner.return_value.init_search.side_effect = lambda: events.append('discover')
            verifier.return_value.verify_pending.side_effect = lambda *args: events.append('verify')
            share_service.return_value.do_multi_uses_share.side_effect = lambda: events.append('share')

            main.run_cycle()

        self.assertEqual(events, ['discover', 'verify', 'share'])

    def test_scheduler_registers_discovery_and_action_jobs_separately(self):
        scheduler = Mock()
        discovery_job = Mock()
        action_job = Mock()
        scheduler.every.side_effect = [discovery_job, action_job]

        with patch.object(globals, 'discovery_interval_hours', 4), patch.object(
            globals, 'action_interval_minutes', 5
        ):
            main.register_jobs(scheduler)

        self.assertEqual(
            scheduler.every.call_args_list,
            [unittest.mock.call(4), unittest.mock.call(5)],
        )
        discovery_job.hours.do.assert_called_once_with(main.run_discovery_safely)
        action_job.minutes.do.assert_called_once_with(main.run_actions_safely)

    def test_scan_verification_block_prevents_share_stage(self):
        with patch.object(globals, 'ups', ['100680137']), \
                patch('main.SearchDynamicByUps') as scanner, \
                patch('main.MultiUsersShareService') as share_service:
            scanner.return_value.init_search.side_effect = BilibiliVerificationRequired('manual check')

            with self.assertRaises(BilibiliVerificationRequired):
                main.run_cycle()

            share_service.assert_not_called()

    def test_verification_block_pauses_future_automatic_cycles(self):
        with patch.object(main, '_workflow_paused', False):
            with patch.object(
                main,
                'run_cycle',
                side_effect=BilibiliVerificationRequired('manual check'),
            ) as cycle:
                main.run_safely()
                self.assertTrue(main._workflow_paused)

                main.run_safely()
                cycle.assert_called_once()


if __name__ == '__main__':
    unittest.main()
