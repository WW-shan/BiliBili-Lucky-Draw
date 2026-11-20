import unittest
from unittest.mock import Mock, patch

from service.bilibili_errors import BilibiliAuthenticationRequired
from service.bilibili_errors import BilibiliVerificationRequired
from service.share_service.share_from_biliLink import BiliLinkShare


class BiliLinkShareTests(unittest.TestCase):
    def build_service(self, url='https://www.bilibili.com/opus/123'):
        service = BiliLinkShare.__new__(BiliLinkShare)
        service.user_id = '35549700'
        service.bro = Mock()
        service.chains = Mock()
        service.owns_browser = False
        service.statistics_dao = Mock()
        service.draw_dynamic_dao = Mock()
        service.share_info_dao = Mock()
        service.follow_up_dao = Mock()
        service.get_today_dynamic_links = Mock(return_value=[{'dyn_url': url}])
        service.get_ignore_link = Mock(return_value=[])
        return service

    def test_successful_share_records_result(self):
        service = self.build_service()
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
            url='https://www.bilibili.com/opus/123', status=1
        )
        service.share_info_dao.insert_shareInfo.assert_called_once_with(dynamic)
        service.follow_up_dao.saverUpdate.assert_called_once_with(
            '456', 'https://space.bilibili.com/456', '35549700'
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
        service.owns_browser = False
        service.statistics_dao = Mock()
        service.get_today_dynamic_links = Mock(return_value=[
            {'dyn_url': 'https://www.bilibili.com/opus/123'}
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
            url='https://www.bilibili.com/opus/123', status=2
        )
        service.share_info_dao.insert_shareInfo.assert_not_called()

    def test_non_risk_failure_is_not_retried_every_cycle(self):
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
            url='https://www.bilibili.com/opus/123', status=3
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
            url='https://www.bilibili.com/opus/123', status=1
        )
        dynamic_share.assert_not_called()


if __name__ == '__main__':
    unittest.main()
