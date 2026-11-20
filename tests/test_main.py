import unittest
from unittest.mock import patch

from service.bilibili_errors import BilibiliVerificationRequired
from utils import globals
import main


class MainWorkflowTests(unittest.TestCase):
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
