import unittest
from unittest.mock import Mock, patch

from service.lottery_service.verify_lottery_service import VerifyLotteryService


class VerifyLotteryServiceTests(unittest.TestCase):
    def test_constructor_defers_browser_until_there_are_candidates(self):
        with patch(
            'service.lottery_service.verify_lottery_service.init_db'
        ), patch(
            'service.lottery_service.verify_lottery_service.DrawDynamicDao'
        ), patch(
            'service.lottery_service.verify_lottery_service.LotteryReadyDao'
        ), patch(
            'service.lottery_service.verify_lottery_service.ScanCacheDao'
        ), patch(
            'service.lottery_service.verify_lottery_service.DynamicShareBase'
        ), patch(
            'service.lottery_service.verify_lottery_service.init_webdriver'
        ) as webdriver:
            service = VerifyLotteryService('35549700')

        webdriver.assert_not_called()
        self.assertTrue(service.owns_browser)
        self.assertIsNone(service.bro)

    def build_service(self):
        service = VerifyLotteryService.__new__(VerifyLotteryService)
        service.user_id = '35549700'
        service.bro = Mock()
        service.chains = Mock()
        service.owns_browser = False
        service.draw_dao = Mock()
        service.ready_dao = Mock()
        service.scan_cache_dao = Mock()
        service.checker = Mock()
        service.pause_seconds = 0
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/1', 'status': '0'},
            {'dyn_url': 'https://www.bilibili.com/opus/2', 'status': '0'},
            {'dyn_url': 'https://www.bilibili.com/opus/3', 'status': '0'},
        ]
        service.scan_cache_dao.has_recent_failure.return_value = False
        return service

    def test_empty_candidate_queue_does_not_start_a_browser(self):
        service = self.build_service()
        service.owns_browser = True
        service.bro = None
        service.chains = None
        service.draw_dao.query_by_verification_priority.return_value = []

        with patch(
            'service.lottery_service.verify_lottery_service.init_webdriver'
        ) as webdriver:
            stats = service.verify_pending(limit=3)

        webdriver.assert_not_called()
        self.assertEqual(stats['checked'], 0)

    def test_newer_pending_candidate_is_verified_before_older_cooling_rows(self):
        service = self.build_service()
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/new', 'status': '0'},
            {'dyn_url': 'https://www.bilibili.com/opus/old', 'status': '0'},
        ]
        service.scan_cache_dao.has_recent_failure.side_effect = [False, True]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': False,
            'retryable': False,
            'reason': 'not_lottery',
        }

        service.verify_pending(limit=1)

        service.checker.fetch_official_lottery_notice.assert_called_once_with(
            service.bro, 'https://www.bilibili.com/opus/new'
        )

    def test_candidate_verification_starts_browser_before_fetching_notice(self):
        service = self.build_service()
        service.owns_browser = True
        service.bro = None
        service.chains = None
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/1', 'status': '0'}
        ]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': False,
            'retryable': False,
            'reason': 'not_lottery',
        }
        browser = Mock()
        chains = Mock()
        login = Mock()

        with patch(
            'service.lottery_service.verify_lottery_service.init_webdriver',
            return_value=(browser, chains),
        ) as webdriver, patch(
            'service.lottery_service.verify_lottery_service.LoginService',
            return_value=login,
        ) as login_service:
            service.verify_pending(limit=1)

        webdriver.assert_called_once_with()
        login_service.assert_called_once_with(browser, chains, '35549700')
        login.login_by_cookie.assert_called_once_with()
        service.checker.fetch_official_lottery_notice.assert_called_once_with(
            browser, 'https://www.bilibili.com/opus/1'
        )
        browser.quit.assert_called_once()

    def test_confirmed_and_explicit_non_lottery_are_moved_to_separate_queues(self):
        service = self.build_service()
        service.checker.fetch_official_lottery_notice.side_effect = [
            {
                'confirmed': True,
                'lottery_id': '456',
                'lottery_end_time': '2026-11-25 20:00:00',
                'reason': '',
            },
            {
                'confirmed': False,
                'code': 0,
                'reason': 'not_lottery',
            },
            {
                'confirmed': False,
                'retryable': True,
                'reason': 'api_code:-9999',
            },
        ]

        stats = service.verify_pending(limit=3)

        self.assertEqual(stats['checked'], 3)
        self.assertEqual(stats['confirmed'], 1)
        self.assertEqual(stats['excluded'], 1)
        self.assertEqual(stats['deferred'], 1)
        service.ready_dao.upsert.assert_called_once_with(
            'https://www.bilibili.com/opus/1',
            '456',
            '2026-11-25 20:00:00',
        )
        self.assertEqual(
            service.draw_dao.update_sharedUrl.call_args_list,
            [
                unittest.mock.call(url='https://www.bilibili.com/opus/1', status='5'),
                unittest.mock.call(url='https://www.bilibili.com/opus/2', status='4'),
            ],
        )
        service.scan_cache_dao.save_failure.assert_called_once()

    def test_confirmed_but_expired_lottery_is_not_queued_for_action(self):
        service = self.build_service()
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/1', 'status': '0'}
        ]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': True,
            'lottery_id': '456',
            'lottery_end_time': '2020-01-01 00:00:00',
            'reason': '',
        }

        stats = service.verify_pending(limit=1)

        self.assertEqual(stats['confirmed'], 1)
        self.assertEqual(stats['expired'], 1)
        service.ready_dao.upsert.assert_not_called()
        service.draw_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1', status='2'
        )

    def test_action_failure_is_reverified_instead_of_being_terminal(self):
        service = self.build_service()
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/9', 'status': '3'},
        ]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': True,
            'lottery_id': '999',
            'lottery_end_time': '2099-01-01 00:00:00',
            'reason': '',
        }

        stats = service.verify_pending(limit=1)

        self.assertEqual(stats['reverified_after_failure'], 1)
        service.draw_dao.query_by_verification_priority.assert_called_once_with(
            limit=4
        )
        service.ready_dao.upsert.assert_called_once_with(
            'https://www.bilibili.com/opus/9', '999', '2099-01-01 00:00:00'
        )
        service.draw_dao.update_sharedUrl.assert_called_with(
            url='https://www.bilibili.com/opus/9', status='5'
        )

    def test_unknown_response_is_deferred_not_excluded(self):
        service = self.build_service()
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/1', 'status': '0'}
        ]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': False,
            'retryable': True,
            'reason': 'code0_without_lottery_id',
        }

        stats = service.verify_pending(limit=1)

        self.assertEqual(stats['deferred'], 1)
        self.assertEqual(stats['excluded'], 0)
        service.draw_dao.update_sharedUrl.assert_not_called()
        service.ready_dao.upsert.assert_not_called()

    def test_consecutive_unknown_responses_stop_the_batch_early(self):
        service = self.build_service()
        service.draw_dao.query_by_verification_priority.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/%s' % index, 'status': '0'}
            for index in range(30)
        ]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': False,
            'retryable': True,
            'reason': 'api_code:-9999',
        }

        stats = service.verify_pending(limit=30)

        self.assertLess(stats['checked'], 30)
        self.assertEqual(stats['deferred'], stats['checked'])

    def test_recent_verification_failure_is_skipped(self):
        service = self.build_service()
        service.scan_cache_dao.has_recent_failure.side_effect = [True, False, False, True, False, False]
        service.checker.fetch_official_lottery_notice.return_value = {
            'confirmed': False,
            'code': 0,
            'reason': 'not_lottery',
        }

        stats = service.verify_pending(limit=3)

        self.assertEqual(stats['checked'], 2)
        self.assertEqual(stats['skipped_recent'], 1)
        self.assertEqual(stats['reverified_after_failure'], 0)
        service.checker.fetch_official_lottery_notice.assert_any_call(
            service.bro,
            'https://www.bilibili.com/opus/2',
        )


if __name__ == '__main__':
    unittest.main()
