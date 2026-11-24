import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

from service.bilibili_errors import BilibiliVerificationRequired
from service.share_service.share_one_dynamic import DynamicShareBase
from service.share_service.share_one_dynamic import SHANGHAI_TZ


class ShareOneDynamicTests(unittest.TestCase):
    def test_current_opus_page_uses_side_toolbar_action_selectors(self):
        self.assertIn('.side-toolbar__action.like', DynamicShareBase.LIKE_CSS)
        self.assertIn('.side-toolbar__action.forward', DynamicShareBase.FORWARD_CSS)

    def test_forward_detection_checks_own_dynamic_feed_by_referenced_id(self):
        share = DynamicShareBase()
        share.user_id = '35549700'
        browser = Mock()
        browser.current_url = 'https://www.bilibili.com/opus/1264178958128119833'
        browser.execute_script.return_value = 'complete'
        card = Mock()
        card.get_attribute.return_value = '...1264178958128119833...'
        card.find_elements.return_value = []
        browser.find_elements.return_value = [card]
        share.get_visible_elements = Mock(side_effect=[[], [card]])
        self.assertTrue(
            share.is_already_forwarded(browser, 'https://www.bilibili.com/opus/1264178958128119833')
        )
        browser.get.assert_any_call('https://space.bilibili.com/35549700/dynamic')
        browser.get.assert_any_call('https://www.bilibili.com/opus/1264178958128119833')

    def test_ambiguous_forward_state_stops_without_duplicate_post(self):
        browser = Mock()
        share = DynamicShareBase()
        with patch.object(share, 'wait_page_ready'), patch.object(
            share, 'resolve_lottery_time'
        ), patch.object(share, 'is_expired_dynamic', return_value=False), patch.object(
            share, 'verify_official_lottery', return_value=(True, '')
        ), patch.object(share, 'ensure_publish_cookie'), patch.object(
            share, 'click_follow_all'
        ), patch.object(share, 'click_like'), patch.object(
            share, 'click_reserve'
        ), patch.object(share, 'is_already_forwarded', return_value=None), patch.object(
            share, 'click_share'
        ) as click_share:
            share.share_one(
                browser, Mock(), 'https://www.bilibili.com/opus/1234567890123456789',
                'share', 'comment',
                lottery_end_time='2099-01-01 00:00:00',
                required_actions=['forward'],
            )
        click_share.assert_not_called()
        self.assertEqual(share.share_status, 3)
        self.assertIn('未能确认', share.last_error)

    def test_like_must_be_confirmed_on_the_page(self):
        browser = Mock()
        share = DynamicShareBase()
        button = Mock()
        button.get_attribute.return_value = 'side-toolbar__action like'
        share.wait_clickable_css = Mock(return_value=button)
        with patch.object(share, 'click_by_js'), patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ):
            with self.assertRaises(Exception):
                share.click_like(browser, Mock())

    def test_favorite_must_be_confirmed_on_the_page(self):
        browser = Mock()
        share = DynamicShareBase()
        button = Mock()
        button.get_attribute.return_value = 'side-toolbar__action favorite'
        share.wait_clickable_css = Mock(return_value=button)
        with patch.object(share, 'click_by_js'), patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ):
            with self.assertRaises(Exception):
                share.click_favorite(browser, Mock())

    def test_forward_detection_matches_rendered_page_text_without_card_id(self):
        share = DynamicShareBase()
        share.user_id = '35549700'
        share.forward_confirm_timeout_seconds = 0.2
        browser = Mock()
        browser.current_url = 'https://www.bilibili.com/opus/1268630998801711121'
        browser.execute_script.return_value = 'complete'
        body = Mock()
        body.text = (
            '梦与时光遇-- 7小时前 来了 三只松鼠官方 鼠鼠新品抢先尝 '
            '主人早上好 突然的降温还没准备好 转发+关注@三只松鼠官方 抽1位主人送冬季温暖新品'
        )
        card = Mock()
        card.get_attribute.side_effect = lambda name: '梦与时光遇-- 7小时前 来了' if name in ('innerText', 'outerHTML') else ''
        card.find_elements.return_value = []
        browser.find_element.return_value = body
        share.get_visible_elements = Mock(return_value=[card])
        with patch('service.share_service.share_one_dynamic.time.sleep'):
            result = share.is_already_forwarded(
                browser,
                'https://www.bilibili.com/opus/1268630998801711121',
                ['主人早上好 突然的降温还没准备好 转发+关注 抽1位主人送冬季温暖新品'],
            )
        self.assertTrue(result)

    def test_forward_detection_matches_saved_rule_text_without_original_id(self):
        share = DynamicShareBase()
        share.user_id = '35549700'
        browser = Mock()
        browser.current_url = 'https://www.bilibili.com/opus/1273316930516680745'
        browser.execute_script.return_value = 'complete'
        card = Mock()
        card.get_attribute.side_effect = lambda name: (
            '薄雪初积，轻揽冬辰 来酷Air 14酷睿版 990g轻薄机身 转发关注抽1个笔记本支架'
            if name == 'innerText' else ''
        )
        card.find_elements.return_value = []
        browser.find_elements.return_value = [card]
        share.get_visible_elements = Mock(return_value=[card])
        with patch('service.share_service.share_one_dynamic.time.sleep'):
            result = share.is_already_forwarded(
                browser,
                'https://www.bilibili.com/opus/1273316930516680745',
                ['薄雪初积，轻揽冬辰\n来酷Air 14酷睿版\n990g轻薄机身，踏处不留痕\n<转发+关注>抽1个【来酷笔记本支架】'],
            )
        self.assertTrue(result)

    def test_forward_detection_rechecks_slow_dynamic_feed(self):
        share = DynamicShareBase()
        share.user_id = '35549700'
        browser = Mock()
        browser.current_url = 'https://www.bilibili.com/opus/1270719989304786947'
        browser.execute_script.return_value = 'complete'
        card = Mock()
        card.get_attribute.return_value = '...1270719989304786947...'
        card.find_elements.return_value = []
        # First feed read is empty while the SPA is still loading; the second
        # read contains the actual card.
        share.get_visible_elements = Mock(side_effect=[[], [card]])
        with patch('service.share_service.share_one_dynamic.time.sleep'):
            result = share.is_already_forwarded(
                browser, 'https://www.bilibili.com/opus/1270719989304786947'
            )
        self.assertTrue(result)
        self.assertGreaterEqual(share.get_visible_elements.call_count, 2)

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
            share, 'is_already_forwarded', side_effect=[False, True]
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

    def test_detail_fetch_runs_from_www_origin(self):
        browser = Mock()
        browser.current_url = 'https://api.bilibili.com/x/web-interface/nav'
        browser.execute_async_script.return_value = {
            'data': {'item': {'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}}}
        }
        share = DynamicShareBase()
        share.detail_request_min_seconds = 0
        share.detail_request_max_seconds = 0

        share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/123'
        )

        browser.get.assert_called_once_with('https://www.bilibili.com/')

    def test_temporary_detail_timeout_retries_with_same_id_opus_origin_once(self):
        browser = Mock()
        browser.current_url = 'https://www.bilibili.com/'
        browser.execute_async_script.side_effect = [
            {'timeout': True},
            {'code': 0, 'data': {'item': {'modules': {'module_dynamic': {
                'type': 'ADDITIONAL_TYPE_LOTTERY'
            }}}}},
        ]
        share = DynamicShareBase()
        share.detail_request_min_seconds = 0
        share.detail_request_max_seconds = 0

        result = share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/1270912279290839112'
        )

        self.assertIsNotNone(result)
        self.assertEqual(browser.execute_async_script.call_count, 2)
        self.assertIn('opus/detail', browser.execute_async_script.call_args_list[-1].args[1])

    def test_html_detail_response_is_classified_as_risk_control(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'error': "SyntaxError: Unexpected token '<', '<!DOCTYPE '... is not valid JSON"
        }
        share = DynamicShareBase()
        share.detail_request_min_seconds = 0
        share.detail_request_max_seconds = 0

        result = share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/1270912279290839112'
        )

        self.assertIsNone(result)
        self.assertEqual(share.last_api_reason, 'html_risk_control')

    def test_detail_fetch_wait_is_configurable_and_paced(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'data': {'item': {'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}}}
        }
        share = DynamicShareBase()
        share.detail_request_min_seconds = 8
        share.detail_request_max_seconds = 15

        with patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ) as sleep:
            share._fetch_dynamic_detail_item(
                browser, 'https://www.bilibili.com/opus/123'
            )

        sleep.assert_called_once_with(start=8, end=15)

    def test_detail_fetch_reports_retryable_api_error(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': -9999,
            'message': '服务系统错误',
        }
        share = DynamicShareBase()

        result = share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertIsNone(result)
        self.assertEqual(share.last_api_reason, 'api_code:-9999')

    def test_major_draw_falls_back_to_current_page_text(self):
        item = {
            'modules': {'module_dynamic': {
                'desc': None,
                'major': {'type': 'MAJOR_TYPE_DRAW', 'draw': {'id': '416371293'}},
            }},
        }
        browser = Mock()
        browser.find_element.return_value.get_attribute.return_value = (
            '点赞、转发并关注 @HyperX极度未知 与 @极限竞速 Forza，'
            '评论区聊聊产品，即有机会赢取礼包。'
        )
        share = DynamicShareBase()

        with patch.object(share, 'get_dynamic_text', return_value='点赞、转发并关注，评论区聊聊产品，即有机会赢取礼包。'):
            restored = share._restore_draw_text_from_page(browser, item)

        self.assertTrue(restored)
        self.assertIn('转发并关注', DynamicShareBase._dynamic_item_text(item))

    def test_opus_detail_modules_list_is_normalized_with_text(self):
        item = {
            'modules': [
                {'module_type': 'MODULE_TYPE_TITLE', 'module_title': {
                    'text': '转发抽奖：好龙谷 1/35 古生物 恐象'
                }},
                {'module_type': 'MODULE_TYPE_AUTHOR', 'module_author': {
                    'mid': '386837540',
                    'name': '萌猴爱模玩',
                }},
                {'module_type': 'MODULE_TYPE_CONTENT', 'module_content': {
                    'paragraphs': [
                        {'text': {'nodes': [
                            {'type': 'TEXT_NODE_TYPE_WORD', 'word': {
                                'words': '福利抽奖：好龙谷 1/35 古生物 恐象\n抽奖规则：关注+评论+点赞+转发参与，12月5号开奖。',
                            }},
                        ]}},
                    ],
                }},
            ],
        }

        normalized = DynamicShareBase._normalize_dynamic_item(item)

        self.assertIn('module_dynamic', normalized['modules'])
        self.assertIn('12月5号开奖', DynamicShareBase._dynamic_item_text(normalized))

    def test_short_primary_text_falls_back_to_longer_opus_text(self):
        browser = Mock()
        browser.current_url = 'https://www.bilibili.com/'
        share = DynamicShareBase()
        share.detail_request_min_seconds = 0
        share.detail_request_max_seconds = 0
        short_item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '短视频预告'},
        ]}}}}
        long_item = {'modules': [{'module_content': {'summary': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，11月25日抽1位送零食箱。'},
        ]}}}]}
        browser.execute_async_script.side_effect = [
            {'code': 0, 'data': {'item': short_item}},
            {'code': 0, 'data': {'item': long_item}},
        ]

        item = share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/1271215276335038485'
        )

        self.assertIn('11月25日', share._dynamic_item_text(item))
        self.assertEqual(browser.execute_async_script.call_count, 2)
        self.assertIn('opus/detail', browser.execute_async_script.call_args_list[-1].args[1])

    def test_detail_api_falls_back_to_opus_detail_before_giving_up(self):
        browser = Mock()
        browser.execute_async_script.side_effect = [
            {'code': -9999, 'message': '服务系统错误'},
            {'code': 0, 'data': {'item': {
                'id_str': '1271215276335038485',
                'modules': {'module_dynamic': {'desc': {'text': '12月5日抽奖'}}},
            }}},
        ]
        share = DynamicShareBase()
        share.detail_request_min_seconds = 0
        share.detail_request_max_seconds = 0

        result = share._fetch_dynamic_detail_item(
            browser, 'https://www.bilibili.com/opus/1271215276335038485'
        )

        self.assertIsInstance(result, dict)
        self.assertIn('opus/detail', browser.execute_async_script.call_args_list[-1].args[1])
        self.assertEqual(browser.execute_async_script.call_count, 2)

    def test_notice_retries_stop_after_first_system_error(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': -9999,
            'message': '服务系统错误',
            'lottery_id': '',
            'status': None,
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=3
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertEqual(result['reason'], 'api_code:-9999')
        self.assertEqual(browser.execute_async_script.call_count, 1)

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

    def test_preverified_deadline_uses_bilibili_timezone(self):
        deadline = datetime.now(SHANGHAI_TZ) + timedelta(hours=1)
        self.assertFalse(DynamicShareBase._is_expired_deadline(deadline))

    def test_naive_db_deadline_is_interpreted_as_shanghai_time(self):
        deadline = datetime(2026, 11, 23, 10, 0)
        now = datetime(2026, 11, 22, 16, 45, tzinfo=timezone.utc)
        self.assertFalse(DynamicShareBase._is_expired_deadline(deadline, now=now))

    def test_plain_text_without_deadline_skips_page_expiry_guess(self):
        share = DynamicShareBase()
        browser = Mock()
        with patch.object(share, 'wait_page_ready'), patch.object(
            share, 'get_dynamic_text', return_value=''
        ), patch.object(share, 'ensure_publish_cookie'), patch.object(
            share, 'resolve_lottery_time'
        ) as resolve:
            share.share_one(
                browser,
                Mock(),
                'https://www.bilibili.com/opus/1272686422850011137',
                'share',
                'comment',
                lottery_kind='plain_text',
                required_actions=[],
            )

        self.assertEqual(share.share_status, 0)
        resolve.assert_not_called()

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
            {'code': 0, 'message': '', 'lottery_id': '777', 'lottery_time': 2099999999, 'status': 0},
        ]
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/1273146113244790787'
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['lottery_id'], '777')
        self.assertIn(
            'web-dynamic/v1/detail',
            browser.execute_async_script.call_args_list[0].args[1],
        )
        self.assertIn(
            'business_type=10', browser.execute_async_script.call_args_list[-1].args[1]
        )
        self.assertIn(
            'business_id=987654321', browser.execute_async_script.call_args_list[-1].args[1]
        )

    def test_verification_does_not_treat_missing_detail_as_non_lottery(self):
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value=None)
        result = share.fetch_official_lottery_notice(
            Mock(), 'https://www.bilibili.com/opus/123'
        )
        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertEqual(result['reason'], 'detail_unavailable')

    def test_dynamic_not_visible_is_excluded_instead_of_retried(self):
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value=None)
        share.last_api_reason = 'api_code:4101152'
        share.last_api_message = '动态不可见'

        result = share.fetch_official_lottery_notice(
            Mock(), 'https://www.bilibili.com/opus/1270908237708787713', retries=1
        )

        self.assertFalse(result['confirmed'])
        self.assertFalse(result['retryable'])
        self.assertEqual(result['reason'], 'dynamic_not_visible')
        self.assertEqual(result['api_message'], '动态不可见')

    def test_notice_api_dynamic_not_visible_is_excluded(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': 4101152,
            'message': '动态不可见',
            'lottery_id': '',
            'status': None,
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=3
        )

        self.assertFalse(result['confirmed'])
        self.assertFalse(result['retryable'])
        self.assertEqual(result['reason'], 'dynamic_not_visible')
        self.assertEqual(browser.execute_async_script.call_count, 1)

    def test_notice_api_risk_control_code_stops_without_retry(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': -352, 'message': '-352', 'lottery_id': '', 'status': None,
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=3
        )

        self.assertTrue(result['retryable'])
        self.assertEqual(result['reason'], 'api_code:-352')
        self.assertEqual(browser.execute_async_script.call_count, 1)

    def test_notice_api_invalid_json_is_retryable_transport(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'http_status': 200, 'invalid_json': True, 'raw': '<html>',
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertEqual(result['reason'], 'invalid_response')

    def test_notice_api_preserves_server_message_for_diagnostics(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': 4101152, 'message': '稍后再试', 'lottery_id': '', 'status': None,
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'api_code:4101152')
        self.assertEqual(result['api_message'], '稍后再试')

    def test_notice_api_timeout_is_retryable(self):
        browser = Mock()
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}})
        browser.execute_async_script.return_value = {'timeout': True}

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertIn('timeout', result['reason'])

    def test_notice_api_has_short_fetch_timeout(self):
        browser = Mock()
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}})
        browser.execute_async_script.return_value = {'timeout': True}

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertTrue(result['retryable'])
        self.assertIn('10000', browser.execute_async_script.call_args.args[0])
        self.assertIn('setTimeout', browser.execute_async_script.call_args.args[0])

    def test_relative_weekday_giveaway_is_recognized(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '转赞评本条动态，下周五抽取2位，送出周边礼包。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['kind'], 'plain_text_lottery')

    def test_required_actions_are_recorded_from_the_rule_text(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，评论区留言，11月27日抽3人送出礼包。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['action'], 'forward')
        self.assertEqual(result['required_actions'], ['follow', 'comment', 'forward'])

    def test_non_giveaway_social_text_does_not_call_official_notice(self):
        browser = Mock()
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '比赛真看爽了吧'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }},
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123', retries=1
        )

        self.assertFalse(result['confirmed'])
        self.assertFalse(result['retryable'])
        browser.execute_async_script.assert_not_called()

    def test_ambiguous_prize_wording_is_not_auto_confirmed(self):
        item = {'modules': {'module_dynamic': {
            'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '参与此次抽奖的人可以获得一张无偿呀'},
            ]},
            'major': {'type': 'MAJOR_TYPE_OPUS'},
        }}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertIn(result['kind'], ('suspected_giveaway', 'context_or_ad'))

    def test_giveaway_without_action_is_reviewed_not_auto_confirmed(self):
        item = {'modules': {'module_dynamic': {
            'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '给大伙送点夜宵吧，50份螺丝粉大套装'},
            ]},
            'major': {'type': 'MAJOR_TYPE_OPUS'},
        }}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'missing_action')

    def test_negative_giveaway_examples_are_rejected(self):
        samples = (
            '抽奖结果公布，恭喜以下3位中奖粉丝，开奖时间11月20日。',
            '抽奖合集更新，上期传送门，本期完。',
            '关注我，优惠券购买链接在商城，限时福利。',
            '直播间预约，直播中，人看过。',
        )
        for text in samples:
            with self.subTest(text=text):
                item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': text},
                ]}}}}
                result = DynamicShareBase.classify_lottery_item(item)
                self.assertFalse(result['confirmed'])

    def test_product_giveaway_with_nonstandard_short_action_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '点👈，关+转就可以了\n送的是主播准备用来收藏的海斗大赛zynooQ65键盘'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertEqual(result['required_actions'], ['follow', 'forward'])

    def test_giveaway_that_only_requires_replying_in_comments_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '毛到两个雷蛇虎抱枕，挺可爱的，抽给两位同学，评论区留言即可。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertEqual(result['required_actions'], ['comment'])

    def test_giveaway_without_explicit_action_is_not_auto_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '毛到两个雷蛇虎抱枕，挺可爱的，抽给两位同学'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'missing_action')

    def test_ambiguous_giveaway_examples_are_not_auto_confirmed(self):
        samples = (
            '抽奖活动即将开始，敬请期待。',
            '评论区随机掉落一张鼠标垫。',
            '抽奖合集链接，详情见专栏。',
            '关注并转发，奖品稍后公布。',
        )
        for text in samples:
            with self.subTest(text=text):
                item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': text},
                ]}}}}
                result = DynamicShareBase.classify_lottery_item(item)
                self.assertFalse(result['confirmed'])

    def test_short_compressed_giveaway_records_follow_comment_forward(self):
        item = {
            'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关转评，猜猜价格，11.27 抽一位送外设盲盒。'},
            ]}}},
        }

        result = DynamicShareBase.classify_lottery_item(item)

        self.assertTrue(result['confirmed'])
        self.assertEqual(
            result['required_actions'], ['follow', 'comment', 'forward']
        )

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

    def test_plain_text_giveaway_without_a_concrete_date_is_confirmed(self):
        item = {
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，抽取3位粉丝送上礼包。'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }},
        }
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['reason'], 'verified_text_rules')
        self.assertEqual(result['required_actions'], ['follow', 'forward'])

    def test_giveaway_need_with_repost_and_prize_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '转发这条动态，并关注UP主，12月1日抽3人送出周边。'},
        ]}, 'major': {'type': 'MAJOR_TYPE_OPUS'}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['action'], 'forward')

    def test_detail_html_response_is_unknown_not_non_lottery(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'error': 'SyntaxError: Unexpected token <'
        }
        share = DynamicShareBase()

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertIn('detail', result['reason'])

    def test_real_giveaway_mentioning_follow_me_is_not_filtered_as_ad(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '做视频这么久，离不开各位小伙伴的陪伴。福利安排上！抽1位宝子送1个【尼康礼盒】。'
                '开奖时间为11月25日下午17:00。【参与方式】：转发本条动态+评论+关注我，就可以参与抽奖啦！'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['kind'], 'plain_text_lottery')
        self.assertEqual(result['required_actions'], ['follow', 'comment', 'forward'])

    def test_real_collection_post_is_still_filtered_as_context(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '抽奖合集更新！【置顶抽奖①】开奖日期12.20，奖品为手机。'
                '【置顶抽奖②】开奖日期12.1，奖品为耳机。给up添加特别关注，第一时间收到合集更新通知。'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['kind'], 'context_or_ad')

    def test_live_giveaway_with_draw_and_prize_is_not_result_notice(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '十一月抽奖来了！【关注点赞转发评论抽奖】参与方式：关注+点赞+评论+转发本条动态，即可参与抽奖！'
                '奖品：南卡Clip E耳夹式耳机*1。【开奖日期】2026年11月25日晚上20:00开奖！名单届时也会动态公布！'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['kind'], 'plain_text_lottery')

    def test_deadline_or_draw_wording_does_not_make_live_giveaway_a_result(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '即日起~11月27日14:00关注+评论本条动态，抽5位送【哈根达斯兑换券35元*1】。'
                '11月27日开奖，我们会在评论区公布获奖名单，并私信获奖用户。'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['required_actions'], ['follow', 'comment'])

    def test_five_lian_emoji_is_parsed_as_like_comment_forward(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '🎁邀请尝鲜🎁 📝：5️⃣连 💬：你喜欢联名零食的哪一个口味？ '
                '⏰：即日起～12月18日15:00 🎁：揪1️⃣只小龙包→联名零食*1箱'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(
            sorted(result['required_actions']),
            sorted(['follow', 'comment', 'like', 'favorite', 'forward']),
        )

    def test_explicit_collect_in_participation_steps_is_recognized(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '怎么报名：关注 + 点赞 + 收藏 + 评论「想当体验官」，12.05随机抽1位。'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertIn('favorite', result['required_actions'])

    def test_win_prize_wording_is_recognized_as_giveaway(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '点赞、转发并关注官方，评论区聊聊，即有机会赢取：'
                '《极限竞速：地平线 6》尊享版1份、XBOX手柄1份。'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertEqual(
            sorted(result['required_actions']),
            sorted(['follow', 'comment', 'like', 'forward']),
        )

    def test_product_star_copy_does_not_add_favorite_action(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '来京东购机赠YOYO钉扣表带套装哦~⭐ '
                '关注+一键三连本条抽粉丝送OPPO数据线'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertNotIn('favorite', result['required_actions'])

    def test_deadline_after_draw_keyword_wins_over_other_date(self):
        text = (
            '开奖时间：12月30号\n'
            '参与需求：关注，评论转发本条帖子\n'
            '主页还有抽键盘的贴子 12月1号开奖'
        )
        result = DynamicShareBase._parse_plain_text_deadline(
            text, now=datetime(2026, 11, 25)
        )
        self.assertEqual(result, '2026-12-30 23:59:00')

    def test_spaced_chinese_month_day_deadline(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '12 月 5 日将会抽取 1 位幸运儿',
            now=datetime(2026, 11, 25),
        )
        self.assertEqual(result, '2026-12-05 23:59:00')

    def test_plq_comment_abbreviation_is_parsed_as_comment(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '在本条plq💬对腾哥的获奖祝福，11月23日12点随机🤏1位送早餐蛋！'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['required_actions'], ['comment'])

    def test_giveaway_with_prize_and_date_but_no_action_needs_review(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                'PS5/Xbox接上领航M8，HDR把霓虹氛围感拉满。11.26抽一位小伙伴送【HKC帆布袋】'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'missing_action')

    def test_ampersand_compact_four_actions_include_comment(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关&转&赞&评本动态，11月25日抽5位送出轴体。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(
            sorted(result['required_actions']),
            sorted(['follow', 'comment', 'like', 'forward']),
        )

    def test_emoji_pig_heart_star_comment_maps_to_social_actions(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '参与方式：🐷+❤️+⭐️+💬，11月25日抽1位送耳机。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(
            sorted(result['required_actions']),
            sorted(['follow', 'like', 'comment', 'favorite']),
        )

    def test_compressed_single_character_actions_are_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                'GT3 六连劝，条条对你好 239首发价，新品已发售，速速冲鸭~ 关转赞评，11.25竞妹jiu一宝带走新品GT3！'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertEqual(
            sorted(result['required_actions']),
            sorted(['follow', 'like', 'comment', 'forward']),
        )

    def test_emoji_and_symbol_compressed_giveaway_actions_are_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '抓1位幸运玩家🉐 🎁【那个游戏6➕游戏手柄】*1 参与方式：关+赞 + 转+评 活动时间：11.17-11.26'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertEqual(
            sorted(result['required_actions']),
            sorted(['follow', 'like', 'comment', 'forward']),
        )

    def test_book_giveaway_with_follow_and_forward_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '以防你不知道《地图谜案》中文版引进了 关注+转发此动态，抽3位送出《地图谜案》一本～'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['required_actions'], ['follow', 'forward'])

    def test_real_pocket_prize_word_is_recognized(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '恭喜BLG夺冠！给大家抽一台Pocket4助助兴！参与方式：关注+转发+评论本条动态。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['has_prize'])
        self.assertTrue(result['confirmed'])

    def test_real_bracket_product_prize_is_recognized(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '<转发+关注>抽1个【来酷笔记本支架】'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['has_prize'])
        self.assertTrue(result['confirmed'])

    def test_four_action_slang_and_pocket_book_prize_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '（此条有抽奖，卷管赞平4连揪1宝子迪士尼袖珍书！）'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertTrue({'follow', 'like', 'comment', 'forward'}.issubset(result['required_actions']))

    def test_four_action_slang_and_new_book_prize_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '（此条有抽奖，卷管赞平4连揪1宝子看新书啦！）'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertTrue({'follow', 'like', 'comment', 'forward'}.issubset(result['required_actions']))

    def test_comment_number_prompt_is_confirmed_as_comment_action(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '在工位的扣 1，在罪恶城的扣 2，在工位但魂在罪恶城的扣 3。11.26抽一位小伙伴送【HKC帆布袋】'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'], result)
        self.assertEqual(result['required_actions'], ['comment'])

    def test_audio_ad_copy_does_not_count_as_giveaway(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '🎵 抽到了别浪费：用傲希音响放这张原声，杜比全景声一开。🐷+❤️+⭐️+💬 评论区聊聊吧。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertNotEqual(result['kind'], 'plain_text_lottery')

    def test_explicit_collect_and_comment_requires_both_actions(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '名流趣味答题福利上线～在评论区写下答案，完成点赞、收藏、转发三连，随机揪 2 位答对的宝子，赠送名流粉 MO 003！'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        actions = set(result['required_actions'])
        self.assertTrue({'like', 'favorite', 'comment', 'forward'}.issubset(actions), actions)

    def test_explicit_like_comment_forward_set_requires_all_three_actions(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '在评论区写下答案，完成点赞、收藏、转发三连，随机揪 2 位答对的宝子，赠送名流粉 MO 003！'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        actions = set(result['required_actions'])
        self.assertTrue({'like', 'comment', 'forward'}.issubset(actions), actions)

    def test_draw_and_gift_without_interaction_is_not_forward_eligible(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '抽取5位幸运小伙伴，每人赠送随机卡，截止下周六。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'missing_action')
        self.assertFalse(result['has_action'])
        self.assertTrue(result['has_prize'])

    def test_giveaway_with_unknown_prize_noun_but_explicit_actions_is_confirmed(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': (
                '关注+转发+评论，12月15日开奖，一等奖【运动水壶吨吨桶】，二等奖【京东卡50元】。'
            )},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['required_actions'], ['follow', 'comment', 'forward'])

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
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注转发评论参与抽奖'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }},
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/123'
        )

        self.assertFalse(result['confirmed'])
        self.assertTrue(result['retryable'])
        self.assertEqual(result['reason'], 'missing_prize')
        browser.execute_async_script.assert_not_called()

    def test_deadline_is_parsed_from_numbered_chinese_giveaway_time(self):
        self.assertEqual(
            DynamicShareBase._parse_plain_text_deadline(
                '小金将在11月27日抽5个小可爱送出京东卡'
            ),
            '2026-11-27 23:59:00',
        )

    def test_explicit_month_only_activity_uses_month_end(self):
        text = '小驰的10月影驰日抽奖来啦，关注并转发此动态参与。'
        result = DynamicShareBase().parse_expire_date(
            text, publish_time=datetime(2026, 10, 1)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.date().isoformat(), '2026-10-31')

    def test_roundup_talk_copy_does_not_add_comment_action(self):
        item = {'modules': {'module_dynamic': {'desc': {'rich_text_nodes': [
            {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '聊聊本期播客，转+关抽两位送抱枕。'},
        ]}}}}
        result = DynamicShareBase.classify_lottery_item(item)
        self.assertTrue(result['confirmed'])
        self.assertNotIn('comment', result['required_actions'])
        self.assertEqual(result['required_actions'], ['follow', 'forward'])

    def test_plain_text_deadline_parses_dot_date_with_day_suffix(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '开奖时间：11.28日',
            now=datetime(2026, 11, 20),
        )
        self.assertEqual(result, '2026-11-28 23:59:00')

    def test_plain_text_deadline_parses_chinese_year_month_time(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '开奖时间：2026年12月15日下午18:00',
            now=datetime(2026, 11, 20),
        )
        self.assertEqual(result, '2026-12-15 18:00:00')

    def test_draw_song_wording_with_date_is_parsed(self):
        text = '关注并转发本动态，我们将于11月16日抽送以下周边。'
        result = DynamicShareBase().parse_expire_date(
            text, publish_time=datetime(2026, 11, 1)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.date().isoformat(), '2026-11-16')

    def test_publish_date_is_not_used_as_deadline_without_draw_wording(self):
        text = (
            '蔚来_NIO 2026年11月20日 18:32 川西山河，自有一番辽阔气象。'
            '关注并转发，评论区分享，我们将抽取1位朋友送出随行杯。'
        )
        self.assertIsNone(
            DynamicShareBase().parse_expire_date(
                text, publish_time=datetime(2026, 11, 20, 8, 32)
            )
        )

    def test_one_month_after_publish_is_relative_deadline(self):
        result = DynamicShareBase().parse_expire_date(
            '参与方式：关注并转发，一个月后开奖。',
            publish_time=datetime(2026, 10, 25, 11, 18),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.date().isoformat(), '2026-11-25')

    def test_plain_text_deadline_parses_month_only_activity(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '小驰的10月影驰日抽奖来啦，关注并转发。',
            now=datetime(2026, 10, 1),
        )
        self.assertEqual(result, '2026-10-31 23:59:00')

    def test_plain_text_deadline_parses_one_month_after(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '参与方式：关注并转发，一个月后开奖。',
            now=datetime(2026, 10, 25, 11, 18, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(result, '2026-11-25 23:59:00')

    def test_plain_text_deadline_parses_next_friday(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '转赞评本条动态，下周五抽取两名幸运小伙伴。',
            now=datetime(2026, 11, 16, 8, 45, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(result, '2026-11-27 23:59:00')

    def test_next_friday_relative_deadline_is_future_from_publish(self):
        text = '转赞评本条动态，下周五抽取两名幸运小伙伴。'
        result = DynamicShareBase().parse_expire_date(
            text, publish_time=datetime(2026, 11, 16)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.date().isoformat(), '2026-11-27')

    def test_this_friday_relative_deadline_is_current_week(self):
        text = '转赞评本条动态，本周五抽取两名幸运小伙伴。'
        result = DynamicShareBase().parse_expire_date(
            text, publish_time=datetime(2026, 11, 16)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.date().isoformat(), '2026-11-20')

    def test_plain_text_deadline_parses_activity_range_end(self):
        result = DynamicShareBase._parse_plain_text_deadline(
            '活动时间：10月2日-11月16日 上午10:00，关注并转发抽1台手机。',
            now=datetime(2026, 10, 2, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(result, '2026-11-16 10:00:00')

    def test_range_activity_end_date_is_parsed_as_deadline(self):
        text = '活动时间：10月2日-11月16日 上午10:00，关注并转发抽1台手机。'
        result = DynamicShareBase().parse_expire_date(
            text, publish_time=datetime(2026, 10, 2)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.month, 11)
        self.assertEqual(result.day, 16)

    def test_plain_text_branch_prefers_official_lottery_end_timestamp(self):
        browser = Mock()
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={
            'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，抽1位送周边。'},
                ]},
            }},
        })
        share.get_dynamic_time_metadata = Mock(return_value={
            'lottery_end_ts': 1795190400, 'publish_ts': 1794542405,
        })

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/1270039030789046355', retries=1
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['lottery_end_time'], '2026-11-21 00:00:00')

    def test_plain_text_giveaway_returns_parsed_deadline(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': 0,
            'data': {'item': {'modules': {'module_dynamic': {
                'desc': {'rich_text_nodes': [
                    {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，11月30日20:00抽取1位粉丝送出礼包。'},
                ]},
                'major': {'type': 'MAJOR_TYPE_OPUS'},
            }}}},
        }
        share = DynamicShareBase()
        share.detail_request_min_seconds = 0
        share.detail_request_max_seconds = 0

        with patch.object(
            share, '_parse_plain_text_deadline', return_value='2026-11-30 20:00:00'
        ) as parser:
            result = share.fetch_official_lottery_notice(
                browser, 'https://www.bilibili.com/opus/123', retries=1
            )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['lottery_end_time'], '2026-11-30 20:00:00')
        parser.assert_called_once()

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

    def test_unrelated_rid_field_is_not_treated_as_reserve_lottery(self):
        item = {'basic': {'rid_str': '999'}, 'modules': {'module_dynamic': {
            'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，11月25日抽1位送零食箱。'},
            ]},
            'additional': None,
            'major': {'rid': '123'},
        }}}
        self.assertFalse(DynamicShareBase._has_official_lottery_component(item))
        self.assertIsNone(DynamicShareBase._extract_reserve_rid(item))

    def test_plain_text_title_does_not_count_as_official_lottery_component(self):
        item = {'basic': {'title': '互动抽奖'}, 'modules': {'module_dynamic': {
            'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，11月25日抽1位送零食箱。'},
            ]},
            'additional': None,
        }}}
        self.assertFalse(DynamicShareBase._has_official_lottery_component(item))

    def test_confirmed_text_giveaway_does_not_require_notice_when_component_is_only_hint(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': -9999, 'message': '服务系统错误', 'lottery_id': '', 'status': None,
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={'modules': {'module_dynamic': {
            'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注并转发，11月25日抽1位送零食箱。'},
            ]},
            'additional': {'lottery': {'lottery_id': ''}},
        }}})

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/1269266233012781059', retries=1
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['source'], 'plain_text')
        browser.execute_async_script.assert_not_called()

    def test_official_lottery_preserves_confirmed_text_actions(self):
        browser = Mock()
        browser.execute_async_script.return_value = {
            'code': 0, 'message': '', 'lottery_id': '430459',
            'lottery_time': 2099999999, 'status': 0,
        }
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={'modules': {'module_dynamic': {
            'desc': {'rich_text_nodes': [
                {'type': 'RICH_TEXT_NODE_TYPE_TEXT', 'text': '关注我，转发此动态参与互动抽奖，奖品显卡。'},
            ]},
            'additional': {'lottery': {'lottery_id': '430459'}},
        }}})

        result = share.fetch_official_lottery_notice(
            browser, 'https://www.bilibili.com/opus/1265584402380881924', retries=1
        )

        self.assertTrue(result['confirmed'])
        self.assertEqual(result['lottery_id'], '430459')
        self.assertEqual(
            result['lottery_rule']['required_actions'],
            ['follow', 'forward'],
        )

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
        share = DynamicShareBase()
        share._fetch_dynamic_detail_item = Mock(return_value={'modules': {'module_dynamic': {'type': 'ADDITIONAL_TYPE_LOTTERY'}}})
        browser.execute_async_script.return_value = {
            'code': 0, 'message': '', 'lottery_id': '999',
            'lottery_time': 2099999999, 'status': 0,
        }

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

    def test_comment_is_not_reported_successful_without_confirmation(self):
        share = DynamicShareBase()
        browser = Mock()
        browser.execute_script.return_value = True
        share.wait_css = Mock(return_value=Mock())
        share.find_comment_editor = Mock(return_value=Mock())
        share.click_by_js = Mock()
        share.has_current_user_comment = Mock(return_value=False)

        with patch(
            'service.share_service.share_one_dynamic.WebDriverWait'
        ) as wait, patch(
            'service.share_service.share_one_dynamic.random_sleep'
        ):
            wait.return_value.until.side_effect = [
                Mock(),
                TimeoutException('comment not visible'),
            ]
            with self.assertRaisesRegex(Exception, '评论提交后未确认'):
                share.commit_comment(browser, Mock(), 'hello')

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
            share, 'is_already_forwarded', side_effect=[False, True]
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

    def test_up_uid_falls_back_to_www_opus_without_dynamic_api(self):
        browser = Mock()
        browser.current_url = 'https://t.bilibili.com/1268647448530649174'
        share = DynamicShareBase()
        share.get_up_id_from_initial_state = Mock(side_effect=[None, '3546802148084393'])
        share.get_up_id_from_dynamic_api = Mock(return_value=None)
        share.get_visible_elements = Mock(return_value=[])

        share.get_up_info(browser)

        self.assertEqual(share.upId, '3546802148084393')

    def test_up_uid_falls_back_to_www_opus_when_t_bilibili_lacks_author(self):
        browser = Mock()
        browser.current_url = 'https://t.bilibili.com/1268647448530649174'
        share = DynamicShareBase()
        share.get_up_id_from_initial_state = Mock(side_effect=[None, '3546802148084393'])
        share.get_up_id_from_dynamic_api = Mock(side_effect=[None, '3546802148084393'])
        share.get_visible_elements = Mock(return_value=[])

        share.get_up_info(browser)

        self.assertEqual(share.upId, '3546802148084393')
        browser.get.assert_any_call('https://www.bilibili.com/opus/1268647448530649174')

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
