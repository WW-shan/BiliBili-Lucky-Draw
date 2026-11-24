import unittest
from unittest.mock import Mock, patch

from service.bilibili_errors import BilibiliAuthenticationRequired
from service.bilibili_errors import BilibiliVerificationRequired
from service.share_service.share_from_biliLink import BiliLinkShare
from utils import globals


class BiliLinkShareTests(unittest.TestCase):
    def build_service(self, url='https://www.bilibili.com/opus/1270000000000000001'):
        service = BiliLinkShare.__new__(BiliLinkShare)
        service.user_id = '35549700'
        service.bro = Mock()
        service.chains = Mock()
        service.owns_browser = False
        service.statistics_dao = Mock()
        service.draw_dynamic_dao = Mock()
        service.share_info_dao = Mock()
        service.follow_up_dao = Mock()
        service.lottery_ready_dao = Mock()
        service.scan_cache_dao = Mock()
        service.get_today_dynamic_links = Mock(return_value=[{'dyn_url': url}])
        service.get_ignore_link = Mock(return_value=[])
        return service

    def test_constructor_defers_browser_until_verified_queue_has_work(self):
        with patch(
            'service.share_service.share_from_biliLink.init_db'
        ), patch(
            'service.share_service.share_from_biliLink.ShareInfoDao'
        ), patch(
            'service.share_service.share_from_biliLink.FollowUpInfoDao'
        ), patch(
            'service.share_service.share_from_biliLink.DrawDynamicDao'
        ), patch(
            'service.share_service.share_from_biliLink.LotteryReadyDao'
        ), patch(
            'service.share_service.share_from_biliLink.StatisticsDao'
        ), patch(
            'service.share_service.share_from_biliLink.init_webdriver'
        ) as webdriver:
            service = BiliLinkShare('35549700')

        webdriver.assert_not_called()
        self.assertTrue(service.owns_browser)
        self.assertIsNone(service.bro)

    def test_rule_texts_are_extracted_for_successful_forward_confirmation(self):
        service = self.build_service()
        texts = service._rule_texts({
            'lottery_rule': "{'text': '薄雪初积，轻揽冬辰 转发关注抽1个支架'}",
        })
        self.assertEqual(texts, ['薄雪初积，轻揽冬辰 转发关注抽1个支架'])

    def test_action_queue_reads_only_verified_ready_rows(self):
        service = BiliLinkShare.__new__(BiliLinkShare)
        service.user_id = '35549700'
        service.lottery_ready_dao = Mock()
        service.lottery_ready_dao.query_ready.return_value = [{'dyn_url': 'ready'}]

        with patch.object(globals, 'share_batch_limit', 20):
            rows = BiliLinkShare.get_today_dynamic_links(service)

        self.assertEqual(rows, [{'dyn_url': 'ready'}])
        service.lottery_ready_dao.query_ready.assert_called_once_with(limit=20, user_id='35549700')

    def test_action_failure_removes_rule_to_avoid_immediate_retry_loop(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        login.ensure_publish_cookie.return_value = None
        dynamic = Mock()
        dynamic.share_status = 3
        dynamic.last_error = '转发状态未能确认，稍后重试'
        dynamic.get_risk_control_evidence.return_value = None
        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ), patch(
            'service.share_service.share_from_biliLink.random_sleep'
        ):
            service.do_share_by_links()

        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1270000000000000001', status=0
        )
        service.scan_cache_dao.save_failure.assert_called_once()
        service.lottery_ready_dao.delete_by_url.assert_called_once_with(
            'https://www.bilibili.com/opus/1270000000000000001'
        )

    def test_historical_dynamic_is_not_executed_as_a_lottery(self):
        service = self.build_service()
        service.get_today_dynamic_links.return_value = [
            {'dyn_url': 'https://www.bilibili.com/opus/1249999999999999999'}
        ]
        # The filter must run before any browser/login/social work.
        service.owns_browser = True
        service.bro = None
        with patch(
            'service.share_service.share_from_biliLink.init_webdriver'
        ) as webdriver, patch(
            'service.share_service.share_from_biliLink.LoginService'
        ) as login, patch(
            'service.share_service.share_from_biliLink.DynamicShareBase'
        ) as dynamic_share:
            service.do_share_by_links()

        webdriver.assert_not_called()
        login.assert_not_called()
        dynamic_share.assert_not_called()
        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1249999999999999999', status=4
        )
        service.lottery_ready_dao.delete_by_url.assert_called_once_with(
            'https://www.bilibili.com/opus/1249999999999999999'
        )

    def test_verified_queue_starts_browser_before_login(self):
        service = self.build_service()
        service.owns_browser = True
        service.bro = None
        service.chains = None
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        browser = Mock()
        chains = Mock()
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 0
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.init_webdriver',
            return_value=(browser, chains),
        ) as webdriver, patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ) as login_service, patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        webdriver.assert_called_once_with()
        login_service.assert_called_once_with(browser, chains, '35549700')

    def test_ready_queue_is_read_twice_before_skipping_action_cycle(self):
        service = self.build_service()
        service.get_today_dynamic_links = Mock(side_effect=[[], []])

        service.do_share_by_links()

        self.assertEqual(service.get_today_dynamic_links.call_count, 2)

    def test_empty_ready_queue_does_not_start_a_browser_login(self):
        service = self.build_service()
        service.get_today_dynamic_links = Mock(side_effect=[[], []])

        with patch(
            'service.share_service.share_from_biliLink.LoginService'
        ) as login:
            service.do_share_by_links()

        login.assert_not_called()

    def test_missing_up_info_does_not_write_none_follow_record(self):
        service = self.build_service()
        service.get_today_dynamic_links.return_value = [{
            'dyn_url': 'https://www.bilibili.com/opus/1270000000000000001',
        }]
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 0
        dynamic.status = 0
        dynamic.upId = None
        dynamic.upUrl = None
        dynamic.share_time = '2026-11-20 12:00:00'
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        service.follow_up_dao.saverUpdate.assert_not_called()

    def test_successful_share_records_result(self):
        service = self.build_service()
        service.get_today_dynamic_links.return_value = [{
            'dyn_url': 'https://www.bilibili.com/opus/1270000000000000001',
            'lottery_end_time': '2026-11-25 20:00:00',
        }]
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 0
        dynamic.status = 0
        dynamic.upId = '456'
        dynamic.upUrl = 'https://space.bilibili.com/456'
        dynamic.share_time = '2026-11-20 12:00:00'
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1270000000000000001', status=1
        )
        dynamic.share_one.assert_called_once_with(
            service.bro,
            service.chains,
            'https://www.bilibili.com/opus/1270000000000000001',
            'share',
            'comment',
            lottery_end_time='2026-11-25 20:00:00',
            lottery_kind='official',
            lottery_rule=None,
            required_actions=None,
            original_texts=[],
        )
        service.share_info_dao.insert_shareInfo.assert_called_once_with(dynamic)
        service.draw_dynamic_dao.mark_shared_by_dynamic_id.assert_called_once_with(
            'https://www.bilibili.com/opus/1270000000000000001', '35549700'
        )
        service.follow_up_dao.saverUpdate.assert_called_once_with(
            '456', 'https://space.bilibili.com/456', '35549700'
        )

    def test_uid_parse_failure_retries_canonical_opus_once(self):
        service = self.build_service()
        service.get_today_dynamic_links.return_value = [{
            'dyn_url': 'https://t.bilibili.com/1268647448530649174',
            'lottery_kind': 'official',
            'lottery_end_time': '2026-11-23 18:00:00',
        }]
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        first = Mock()
        first.last_error = '未能从动态标题解析UP主UID'
        first.get_risk_control_evidence.return_value = None
        second = Mock()
        second.last_error = None
        second.share_status = 0
        second.status = 0
        second.upId = '3546802148084393'
        second.upUrl = 'https://space.bilibili.com/3546802148084393'
        second.share_time = '2026-11-23 05:00:00'

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            side_effect=[first, second],
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        second.share_one.assert_called_once()
        self.assertEqual(
            second.share_one.call_args.args[2],
            'https://www.bilibili.com/opus/1268647448530649174',
        )
        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1268647448530649174', status=1
        )

    def test_duplicate_url_forms_execute_only_once_with_explicit_rule(self):
        service = self.build_service()
        service.get_today_dynamic_links.return_value = [
            {
                'dyn_url': 'https://t.bilibili.com/1273348285941153816',
                'lottery_kind': 'official',
                'lottery_end_time': '2026-11-25 20:00:00',
                'required_actions': None,
            },
            {
                'dyn_url': 'https://www.bilibili.com/opus/1273348285941153816',
                'lottery_kind': 'plain_text',
                'lottery_end_time': '2026-11-25 20:00:00',
                'required_actions': "['follow']",
            },
        ]
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 0
        dynamic.status = 0
        dynamic.upId = '456'
        dynamic.upUrl = 'https://space.bilibili.com/456'
        dynamic.share_time = '2026-11-25 06:00:00'
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        dynamic.share_one.assert_called_once()
        self.assertEqual(
            dynamic.share_one.call_args.args[2],
            'https://www.bilibili.com/opus/1273348285941153816',
        )
        self.assertEqual(
            dynamic.share_one.call_args.kwargs['required_actions'],
            "['follow']",
        )

    def test_risk_control_failure_stops_instead_of_being_silent(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 1
        dynamic.status = 1
        dynamic.last_error = '提交失败，错误号: 412，触发安全风控策略'
        dynamic.get_risk_control_evidence.return_value = '错误号: 412'

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            with self.assertRaises(BilibiliVerificationRequired):
                service.do_share_by_links()

        service.share_info_dao.insert_shareInfo.assert_not_called()
        service.draw_dynamic_dao.update_sharedUrl.assert_not_called()

    def test_missing_csrf_cookie_stops_before_any_dynamic_action(self):
        service = BiliLinkShare.__new__(BiliLinkShare)
        service.user_id = '35549700'
        service.bro = Mock()
        service.chains = Mock()
        service.owns_browser = True
        service.statistics_dao = Mock()
        service.get_today_dynamic_links = Mock(return_value=[
            {'dyn_url': 'https://www.bilibili.com/opus/1270000000000000001'}
        ])
        service.get_ignore_link = Mock(return_value=[])
        service.share_info_dao = Mock()

        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        login.ensure_publish_cookie.side_effect = BilibiliAuthenticationRequired(
            'missing bili_jct'
        )

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase'
        ) as dynamic_share:
            with self.assertRaises(BilibiliAuthenticationRequired):
                service.do_share_by_links()

        login.ensure_publish_cookie.assert_called_once_with()
        dynamic_share.assert_not_called()
        service.share_info_dao.query_shareInfo_by_shareUrl.assert_not_called()
        service.bro.quit.assert_called_once_with()

    def test_expired_dynamic_is_removed_from_ready_rule_queue(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 2
        dynamic.status = 2
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        service.lottery_ready_dao.delete_by_url.assert_called_once_with(
            'https://www.bilibili.com/opus/1270000000000000001'
        )

    def test_expired_dynamic_is_removed_from_pending_queue(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 2
        dynamic.status = 2
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1270000000000000001', status=2
        )
        service.share_info_dao.insert_shareInfo.assert_not_called()

    def test_unconfirmed_lottery_is_excluded_without_share_record(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 4
        dynamic.status = 4
        dynamic.last_error = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1270000000000000001', status=4
        )
        service.share_info_dao.insert_shareInfo.assert_not_called()

    def test_non_risk_failure_is_reverified_instead_of_being_terminal(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = []
        login = Mock()
        login.login_by_cookie.return_value = '35549700'
        dynamic = Mock()
        dynamic.share_status = 1
        dynamic.status = 1
        dynamic.last_error = '关注操作结果无法确认'
        dynamic.get_risk_control_evidence.return_value = None

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase',
            return_value=dynamic,
        ), patch(
            'service.share_service.share_from_biliLink.get_random_share_content',
            return_value='share',
        ), patch(
            'service.share_service.share_from_biliLink.get_random_comment_content',
            return_value='comment',
        ):
            service.do_share_by_links()

        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1270000000000000001', status=0
        )
        service.share_info_dao.insert_shareInfo.assert_not_called()

    def test_existing_share_record_marks_pending_queue_done(self):
        service = self.build_service()
        service.share_info_dao.query_shareInfo_by_shareUrl.return_value = [{'status': 0}]
        login = Mock()
        login.login_by_cookie.return_value = '35549700'

        with patch(
            'service.share_service.share_from_biliLink.LoginService',
            return_value=login,
        ), patch(
            'service.share_service.share_from_biliLink.DynamicShareBase'
        ) as dynamic_share:
            service.do_share_by_links()

        service.draw_dynamic_dao.update_sharedUrl.assert_called_once_with(
            url='https://www.bilibili.com/opus/1270000000000000001', status=1
        )
        dynamic_share.assert_not_called()


if __name__ == '__main__':
    unittest.main()
