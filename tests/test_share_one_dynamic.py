import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import StaleElementReferenceException

from service.bilibili_errors import BilibiliVerificationRequired
from service.share_service.share_one_dynamic import DynamicShareBase


class ShareOneDynamicTests(unittest.TestCase):
    def test_current_opus_page_uses_side_toolbar_action_selectors(self):
        self.assertIn('.side-toolbar__action.like', DynamicShareBase.LIKE_CSS)
        self.assertIn('.side-toolbar__action.forward', DynamicShareBase.FORWARD_CSS)

    def test_share_flow_uses_opus_toolbar_and_confirms_forward(self):
        browser = Mock()
        chains = Mock()
        share = DynamicShareBase()

        with patch.object(share, 'wait_page_ready'), patch.object(
            share, 'resolve_lottery_time'
        ), patch.object(share, 'is_expired_dynamic', return_value=False), patch.object(
            share, 'verify_official_lottery', return_value=(True, '')
        ), patch.object(
            share, 'ensure_publish_cookie'
        ) as ensure_publish_cookie, patch.object(
            share, 'click_follow_all'
        ) as click_follow, patch.object(
            share, 'click_like'
        ) as click_like, patch.object(
            share, 'click_reserve'
        ) as click_reserve, patch.object(
            share, 'has_current_user_comment', return_value=False
        ), patch.object(
            share, 'commit_comment'
        ) as commit_comment, patch.object(
            share, 'is_already_forwarded', return_value=False
        ), patch.object(
            share, 'click_share'
        ) as click_share, patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ):
            share.share_one(
                browser, chains, 'https://www.bilibili.com/opus/123', 'share', 'comment'
            )

        ensure_publish_cookie.assert_called_once_with(browser)
        click_follow.assert_called_once_with(browser, chains)
        click_like.assert_called_once_with(browser, chains)
        click_reserve.assert_called_once_with(browser, chains)
        commit_comment.assert_called_once_with(browser, chains, 'comment')
        click_share.assert_called_once_with(browser, chains, 'share')
        self.assertEqual(share.share_status, 0)
        self.assertEqual(share.share_url, 'https://www.bilibili.com/opus/123')

    def test_unconfirmed_lottery_stops_before_any_social_action(self):
        share = DynamicShareBase()
        with patch.object(share, 'wait_page_ready'), patch.object(
            share, 'verify_official_lottery', return_value=(False, 'not_lottery')
        ), patch.object(share, 'click_follow_all') as click_follow, patch.object(
            share, 'click_like'
        ) as click_like, patch.object(share, 'commit_comment') as commit_comment, patch.object(
            share, 'click_share'
        ) as click_share:
            share.share_one(
                Mock(), Mock(), 'https://www.bilibili.com/opus/123', 'share', 'comment'
            )

        click_follow.assert_not_called()
        click_like.assert_not_called()
        commit_comment.assert_not_called()
        click_share.assert_not_called()
        self.assertEqual(share.share_status, 4)
        self.assertIsNone(share.last_error)

    def test_verification_block_stops_before_any_dynamic_action(self):
        share = DynamicShareBase()
        with patch.object(
            share,
            'wait_page_ready',
            side_effect=BilibiliVerificationRequired('manual verification'),
        ), patch.object(share, 'click_follow_all') as click_follow, patch.object(
            share, 'click_like'
        ) as click_like, patch.object(share, 'click_share') as click_share:
            share.share_one(
                Mock(), Mock(), 'https://www.bilibili.com/opus/123', 'share', 'comment'
            )

        click_follow.assert_not_called()
        click_like.assert_not_called()
        click_share.assert_not_called()
        self.assertEqual(share.share_status, 1)
        self.assertIn('manual verification', share.last_error)

    def test_publish_success_survives_stale_modal_during_confirmation(self):
        browser = Mock()
        share = DynamicShareBase()
        share.wait_clickable_css = Mock(return_value=Mock())
        share.wait_css = Mock(return_value=Mock())
        share.focus_contenteditable_start = Mock()
        share.click_by_js = Mock()
        share.find_share_publish_btn = Mock(return_value=Mock())
        share.wait_share_success = Mock(
            side_effect=StaleElementReferenceException('modal rerendered after publish')
        )

        with patch('service.share_service.share_one_dynamic.random_sleep'):
            share.click_share(browser, Mock(), 'share content')

        share.wait_share_success.assert_called_once_with(browser)

    def test_follow_uses_csrf_relation_api_instead_of_requiring_profile_button(self):
        browser = Mock()
        browser.get_cookies.return_value = [
            {'name': 'bili_jct', 'value': 'csrf-token'},
            {'name': 'SESSDATA', 'value': 'session'},
        ]
        browser.execute_async_script.return_value = {'code': 0, 'message': '0'}
        share = DynamicShareBase()

        result = share.follow_by_api(browser, '12345')

        self.assertEqual(result, {'code': 0, 'message': '0'})
        args = browser.execute_async_script.call_args.args
        self.assertIn('x/relation/modify', args[0])
        self.assertEqual(args[1], '12345')
        self.assertEqual(args[2], 'csrf-token')

    def test_follow_api_stops_when_csrf_cookie_is_missing(self):
        browser = Mock()
        browser.get_cookies.return_value = [
            {'name': 'SESSDATA', 'value': 'session'},
        ]
        share = DynamicShareBase()

        with self.assertRaises(Exception):
            share.follow_by_api(browser, '12345')

        browser.execute_async_script.assert_not_called()


if __name__ == '__main__':
    unittest.main()
