import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

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
        self.assertIsNone(share.last_error)
        self.assertEqual(share.share_url, 'https://www.bilibili.com/opus/123')

    def test_detail_fetch_has_hard_timeout(self):
        browser = Mock()
        browser.set_script_timeout.side_effect = Exception('must not change global timeout')
        browser.execute_async_script.return_value = {'timeout': True}
        share = DynamicShareBase()

        result = share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertIsNone(result)
        self.assertIn('setTimeout', browser.execute_async_script.call_args.args[0])

    def test_preverified_expired_lottery_stops_before_page_time_parsing_or_actions(self):
        share = DynamicShareBase()
        with patch.object(share, 'wait_page_ready'), patch.object(
            share, 'verify_official_lottery', return_value=(True, '')
        ), patch.object(share, 'resolve_lottery_time') as resolve_time, patch.object(
            share, 'click_follow_all'
        ) as click_follow, patch.object(
            share, 'click_like'
        ) as click_like, patch.object(
            share, 'click_share'
        ) as click_share:
            share.share_one(
                Mock(),
                Mock(),
                'https://www.bilibili.com/opus/123',
                'share',
                'comment',
                lottery_end_time='2020-01-01 00:00:00',
            )

        resolve_time.assert_not_called()
        click_follow.assert_not_called()
        click_like.assert_not_called()
        click_share.assert_not_called()
        self.assertEqual(share.share_status, 2)

    def test_reserve_lottery_uses_rid_and_business_type_10(self):
        browser = Mock()
        browser.get_cookies.return_value = [
            {'name': 'bili_jct', 'value': 'csrf-token'},
        ]
        browser.execute_async_script.side_effect = [
            {'code': 0, 'data': {'item': {'modules': {'module_dynamic': {
                'additional': {
                    'reserve': {'rid': '987654321'},
                    'type': 'ADDITIONAL_TYPE_RESERVE',
                },
            }}}}},
            {'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None},
            {'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None},
            {'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None},
            {'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None},
            {'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None},
            {'code': 0, 'message': '', 'lottery_id': '777', 'lottery_time': 2099999999, 'status': 0},
        ]
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/1273146113244790787'
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['lottery_id'], '777')
        self.assertIn(
            'business_type=10', browser.execute_async_script.call_args_list[-1].args[1]
        )
        self.assertIn(
            'business_id=987654321', browser.execute_async_script.call_args_list[-1].args[1]
        )

    def test_notice_api_timeout_is_retryable(self):
        browser = Mock()
        browser.execute_async_script.return_value = {'timeout': True}
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertIn('timeout', result['reason'])

    def test_notice_api_has_short_fetch_timeout(self):
        browser = Mock()
        browser.execute_async_script.return_value = {'timeout': True}
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertTrue(result['retryable'])
        self.assertIn('8000', browser.execute_async_script.call_args.args[0])
        self.assertIn('setTimeout', browser.execute_async_script.call_args.args[0])

    def test_relative_weekday_giveaway_is_recognized(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '转赞评本条动态，下周五抽取2位，送出周边礼包。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['kind'], 'plain_text_lottery')

    def test_compressed_giveaway_actions_are_recognized(self):
        samples = (
            ('转赞评本条动态，11月27日抽取2位，送出周边礼包。', 'forward'),
            ('评论区抽1人，11月27日送出机械臂。', 'comment'),
            ('关&转&赞&评本动态，11月27日抽5位送出轴体。', 'forward'),
        )
        for text, action in samples:
            with self.subTest(text=text):
                item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': text},
                ]}}}}
                result = DynamicShareBase.classify_lottery_item(item)
                self.assertTrue(result['confirmed'])
                self.assertEqual(result['action'], action)

    def test_plain_text_giveaway_with_rules_is_confirmed_without_official_component(self):
        item = {
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发本条动态，12月5日抽取3位粉丝，每人送出一份零食礼包，评论也可参与。'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }},
        }
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertEqual(result['action'], 'forward')
        self.assertEqual(result['kind'], 'plain_text_lottery')
        self.assertTrue(result['confirmed'])

    def test_plain_follow_only_giveaway_is_confirmed_as_follow_action(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注UP主，12月1日抽3人送出周边礼包。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['action'], 'follow')

    def test_plain_comment_only_giveaway_is_confirmed_as_comment_action(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '在评论区留言，12月1日抽3人送出周边礼包。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['action'], 'comment')

    def test_plain_text_giveaway_without_a_concrete_date_is_not_confirmed(self):
        item = {
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，抽取3位粉丝送上礼包。'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }},
        }
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'missing_concrete_time')

    def test_giveaway_need_with_repost_and_prize_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '转发这条动态，并关注UP主，12月1日抽3人送出周边。'},
        ]}, 'major': {'type': 'MAJOR_TYPE_OPUS'}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['action'], 'forward')

    def test_giveaway_result_announcement_is_not_participable(self):
        item = {
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '上次抽奖中奖名单公布，恭喜以下3位粉丝，开奖时间11月20日。'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }},
        }
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['kind'], 'result_or_notice')

    def test_incomplete_text_giveaway_is_reviewed_not_auto_executed(self):
        browser = Mock()
        unknown = {'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None}
        browser.execute_async_script.side_effect = [
            {'code': 0, 'data': {'item': {'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注转发评论参与抽奖'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }}}}},
            unknown, unknown, unknown, unknown, unknown, unknown,
        ]
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertIn(result['reason'], ('missing_prize', 'api_code:-9999'))
        self.assertGreater(browser.execute_async_script.call_count, 1)

    def test_plain_text_giveaway_is_accepted_by_notice_verifier_without_lottery_id(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': 0,
            'data': {'item': {'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，12月5日抽取3位粉丝，每人送出礼包。'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }}}},
        }
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['source'], 'plain_text')
        self.assertEqual(result['lottery_rule']['action'], 'forward')

    def test_zero_without_lottery_id_is_unknown_not_explicit_non_lottery(self):
        browser = Mock()
        browser.execute_async_script.side_effect = [
            {'code': 0, 'data': {'item': {}}},
            {'code': 0, 'message': '', 'lottery_id': '', 'status': None},
        ]
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertNotEqual(result['reason'], 'not_lottery')

    def test_confirmed_activity_notice_is_not_treated_as_not_lottery(self):
        browser = Mock()
        browser.execute_async_script.side_effect = [
            {'code': 0, 'data': {'item': {}}},
            {'code': 0, 'message': '', 'lottery_id': '999', 'lottery_time': 2099999999, 'status': 0},
        ]
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['lottery_id'], '999')

    def test_share_success_detection_ignores_original_card_failure_words(self):
        share = DynamicShareBase()
        browser = Mock()
        browser.find_elements.return_value = []
        modal = Mock()
        modal.get_attribute.return_value = '参与抽奖 操作失败 发布 999'
        modal.text = ''
        share.get_visible_elements = Mock(return_value=[modal])

        with patch.object(share, 'get_visible_message_text', return_value=''):
            with self.assertRaises(TimeoutException):
                share.wait_share_success(browser, timeout=1)

    def test_comment_editor_timeout_marks_participation_incomplete(self):
        share = DynamicShareBase()
        browser = Mock()
        chains = Mock()
        share.wait_page_ready = Mock()
        share.verify_official_lottery = Mock(return_value=(True, ''))
        share.resolve_lottery_time = Mock()
        share.is_expired_dynamic = Mock(return_value=False)
        share.ensure_publish_cookie = Mock()
        share.get_up_info = Mock()
        share.get_up_id_from_initial_state = Mock(return_value='123')
        share.add_up_id = Mock()
        share.upIds = ['123']
        share.upUrls = ['https://space.bilibili.com/123']
        share.get_follow_relation = Mock(return_value={'followed': True})
        share.wait_clickable_css = Mock(return_value=Mock())
        share.wait_css = Mock(side_effect=TimeoutException('comment editor missing'))
        share.click_by_js = Mock()
        share.wait_follow_relation = Mock(return_value=True)
        share.is_done_text = Mock(return_value=True)

        with patch.object(share, 'click_follow_all'), patch.object(
            share, 'click_like'
        ), patch.object(
            share, 'click_reserve'
        ), patch.object(
            share, 'has_current_user_comment', return_value=False
        ), patch.object(
            share, 'is_already_forwarded', return_value=False
        ), patch.object(
            share, 'click_share'
        ) as click_share, patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ):
            share.commit_comment = Mock(side_effect=TimeoutException('comment editor missing'))
            share.share_one(
                browser, chains, 'https://www.bilibili.com/opus/123', 'share', 'comment'
            )

        click_share.assert_not_called()
        self.assertEqual(share.share_status, 3)
        self.assertIn('comment editor missing', share.last_error)

    def test_plain_text_lottery_does_not_call_official_notice_before_actions(self):
        share = DynamicShareBase()
        share.wait_page_ready = Mock()
        share.is_expired_dynamic = Mock(return_value=False)
        share.ensure_publish_cookie = Mock()
        share.verify_official_lottery = Mock(
            side_effect=AssertionError('plain text must not call official API')
        )
        with patch.object(share, 'click_follow_all'), patch.object(
            share, 'click_like'
        ), patch.object(
            share, 'click_reserve'
        ), patch.object(
            share, 'has_current_user_comment', return_value=False
        ), patch.object(
            share, 'commit_comment'
        ), patch.object(
            share, 'is_already_forwarded', return_value=False
        ), patch.object(
            share, 'click_share'
        ) as click_share, patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ):
            browser = Mock()
            chains = Mock()
            share.share_one(
                browser, chains, 'https://www.bilibili.com/opus/123',
                'share', 'comment', lottery_kind='plain_text',
                lottery_end_time='2099-12-31 00:00:00',
            )

        share.verify_official_lottery.assert_not_called()
        click_share.assert_called_once_with(browser, chains, 'share')

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
        self.assertEqual(share.share_status, 3)
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
