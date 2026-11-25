import unittest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, call, patch

from selenium.common.exceptions import TimeoutException

from service.bilibili_errors import BilibiliVerificationRequired, ensure_page_access
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from utils import globals


class SearchDynamicRegressionTests(unittest.TestCase):
    def test_regular_draw_up_is_not_used_as_an_aggregate_source(self):
        self.assertNotIn('和微论件', SearchDynamicByUps.DYNAMIC_SOURCES)

    def test_select_readlists_keeps_newest_normal_and_official_family(self):
        rows = [
            {'id': '1', 'name': '抽奖合集②', 'articles_count': 500, 'update_time': 200},
            {'id': '2', 'name': '抽奖合集③', 'articles_count': 500, 'update_time': 100},
            {'id': '3', 'name': '官方抽奖合集②', 'articles_count': 500, 'update_time': 300},
            {'id': '4', 'name': '官方抽奖合集③', 'articles_count': 289, 'update_time': 100},
            {'id': '5', 'name': '随想随写', 'articles_count': 2, 'update_time': 999},
        ]
        spec = SearchDynamicByUps.SOURCE_SPECS['你的工具人老公']

        selected = SearchDynamicByUps.select_readlists(spec, rows)

        self.assertEqual([row['id'] for row in selected], ['2', '4'])

    def test_select_readlists_supports_exact_name_and_current_year(self):
        exact_spec = SearchDynamicByUps.SOURCE_SPECS['J君名']
        exact_rows = [
            {'id': '11', 'name': '抽奖', 'update_time': 1},
            {'id': '12', 'name': '抽奖 旧', 'update_time': 999},
        ]
        year_spec = SearchDynamicByUps.SOURCE_SPECS['糯米是个背包']
        year_rows = [
            {'id': '21', 'name': '2025', 'update_time': 999},
            {'id': '22', 'name': '2026', 'update_time': 1},
        ]

        self.assertEqual(
            [row['id'] for row in SearchDynamicByUps.select_readlists(exact_spec, exact_rows)],
            ['11'],
        )
        self.assertEqual(
            [row['id'] for row in SearchDynamicByUps.select_readlists(year_spec, year_rows)],
            ['22'],
        )

    def test_select_latest_articles_sorts_by_publish_time_and_limits(self):
        rows = [
            {'id': 1, 'publish_time': 100, 'title': 'old'},
            {'id': 2, 'publish_time': 300, 'title': 'new'},
            {'id': 3, 'publish_time': 200, 'title': 'middle'},
        ]

        selected = SearchDynamicByUps.select_latest_articles(rows, limit=2)

        self.assertEqual([row['id'] for row in selected], [2, 3])

    def test_readlist_discovery_scans_recent_articles_and_caches_each(self):
        now = datetime.now()
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.count = 0
        scanner.search_note = ''
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = False
        scanner.scan_cache_dao.has_recent_failure.return_value = False
        scanner.fetch_json = Mock(side_effect=[
            {
                'code': 0,
                'data': {'lists': [
                    {'id': '845787', 'name': '抽奖', 'update_time': 1},
                ]},
            },
            {
                'code': 0,
                'data': {'articles': [
                    {'id': 54011152, 'publish_time': int((now - timedelta(days=1)).timestamp())},
                ]},
            },
            {
                'code': 0,
                'data': {'articles': [
                    {'id': 40384169, 'publish_time': int((now - timedelta(days=2)).timestamp())},
                    {'id': 40384168, 'publish_time': int((now - timedelta(days=3)).timestamp())},
                ]},
            },
        ])
        scanner.extract_links_from_detail_page = Mock(return_value=[
            'https://www.bilibili.com/opus/1271215276335038485'
        ])
        scanner.dynLinks_to_db = Mock()
        spec = SearchDynamicByUps.SOURCE_SPECS['J君名']

        scanner.search_readlist_source(Mock(), spec, 'J君名')

        self.assertEqual(
            [item.args[1] for item in scanner.extract_links_from_detail_page.call_args_list],
            [
                'https://www.bilibili.com/read/cv54011152',
                'https://www.bilibili.com/read/cv40384169',
                'https://www.bilibili.com/read/cv40384168',
            ],
        )
        self.assertEqual(scanner.scan_cache_dao.save_success.call_count, 3)
        scanner.dynLinks_to_db.assert_called_once_with(
            ['https://www.bilibili.com/opus/1271215276335038485'],
            'https://space.bilibili.com/126038161/dynamic',
            'J君名',
        )

    def test_select_recent_articles_covers_all_articles_from_last_seven_days(self):
        now = datetime(2026, 11, 21, 12, 0, 0)
        rows = [
            {'id': 1, 'publish_time': int((now - timedelta(days=1)).timestamp())},
            {'id': 2, 'publish_time': int((now - timedelta(days=6, hours=23)).timestamp())},
            {'id': 3, 'publish_time': int((now - timedelta(days=7, minutes=1)).timestamp())},
            {'id': 4, 'publish_time': int((now - timedelta(days=20)).timestamp())},
        ]

        selected = SearchDynamicByUps.select_recent_articles(rows, days=7, now=now)

        self.assertEqual([row['id'] for row in selected], [1, 2])

    def test_readlist_discovery_scans_every_article_from_last_seven_days(self):
        now = datetime.now()
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.count = 0
        scanner.search_note = ''
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = False
        scanner.scan_cache_dao.has_recent_failure.return_value = False
        scanner.fetch_json = Mock(side_effect=[
            {'code': 0, 'data': {'lists': [
                {'id': '845787', 'name': '抽奖', 'update_time': 1},
            ]}},
            {'code': 0, 'data': {'articles': [
                {'id': 54011152, 'publish_time': int((now - timedelta(days=1)).timestamp())},
            ]}},
            {'code': 0, 'data': {'articles': [
                {'id': 40384169, 'publish_time': int((now - timedelta(days=2)).timestamp())},
                {'id': 40384168, 'publish_time': int((now - timedelta(days=3)).timestamp())},
                {'id': 40384167, 'publish_time': int((now - timedelta(days=8)).timestamp())},
            ]}},
        ])
        scanner.extract_links_from_detail_page = Mock(return_value=[])
        scanner.dynLinks_to_db = Mock()

        scanner.search_readlist_source(
            Mock(), SearchDynamicByUps.SOURCE_SPECS['J君名'], 'J君名'
        )

        scanned_urls = [
            call.args[1]
            for call in scanner.extract_links_from_detail_page.call_args_list
        ]
        self.assertEqual(scanned_urls, [
            'https://www.bilibili.com/read/cv54011152',
            'https://www.bilibili.com/read/cv40384169',
            'https://www.bilibili.com/read/cv40384168',
        ])

    def test_space_feed_scans_all_recent_detail_articles_not_only_latest(self):
        now = datetime.now()
        browser = Mock()
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner._open_page = Mock()
        scanner.extract_feed_snapshot_from_api = Mock(return_value={
            'code': 0,
            'items': [
                {
                    'id': str(1270000000000000001 + index),
                    'type': 'DYNAMIC_TYPE_ARTICLE',
                    'pub_ts': str(int((now - timedelta(days=index + 1)).timestamp())),
                    'text': '抽奖合集',
                }
                for index in range(5)
            ],
        })
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = False
        scanner.scan_cache_dao.has_recent_failure.return_value = False
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.extract_links_from_detail_page = Mock(return_value=[])
        scanner.dynLinks_to_db = Mock()

        scanner.search_dynamic_links_by_up(
            browser, 'https://space.bilibili.com/123/dynamic', 'source'
        )

        self.assertEqual(scanner.extract_links_from_detail_page.call_count, 5)

    def test_dynamic_card_selectors_are_independent_css_selectors(self):
        selectors = SearchDynamicByUps.DYNAMIC_CARD_SELECTORS

        self.assertEqual(len(selectors), 2)
        self.assertEqual(len(set(selectors)), len(selectors))
        self.assertTrue(all(isinstance(selector, str) and selector for selector in selectors))

    def test_missing_dynamic_cards_returns_empty_without_element_lookup(self):
        browser = Mock()
        browser.find_elements.return_value = []

        cards = SearchDynamicByUps.find_dynamic_cards(browser)

        self.assertEqual(cards, [])
        self.assertEqual(browser.find_elements.call_count, 2)
        browser.find_element.assert_not_called()

    def test_dynamic_url_normalization_drops_tracking_and_non_dynamic_urls(self):
        self.assertEqual(
            SearchDynamicByUps.normalize_dynamic_url(
                "//t.bilibili.com/123456?from=share"
            ),
            "https://t.bilibili.com/123456",
        )
        self.assertIsNone(
            SearchDynamicByUps.normalize_dynamic_url("https://example.com/123456")
        )

    def test_http_412_is_a_manual_verification_block(self):
        self.assertTrue(
            SearchDynamicByUps.is_verification_block(
                status_code=412,
                page_title="",
                page_text="",
            )
        )
        self.assertFalse(
            SearchDynamicByUps.is_verification_block(
                status_code=200,
                page_title="动态",
                page_text="正常内容",
            )
        )

    def test_detail_page_navigation_timeout_stops_page_and_returns_none(self):
        browser = Mock()
        browser.get.side_effect = TimeoutException('navigation timed out')
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.scan_cache_dao = Mock()

        with patch(
            'service.search_draw_dynamic_service.SearchDynamicByUps.ensure_page_access'
        ), patch(
            'service.search_draw_dynamic_service.SearchDynamicByUps.time.monotonic',
            side_effect=[0, 100],
        ):
            result = SearchDynamicByUps.extract_links_from_detail_page(
                browser, 'https://www.bilibili.com/read/cv123'
            )

        self.assertIsNone(result)
        browser.execute_script.assert_called_with('window.stop();')

    def test_document_http_412_raises_typed_verification_error(self):
        url = 'https://space.bilibili.com/100680137/dynamic'
        event = {
            'message': json.dumps({
                'message': {
                    'method': 'Network.responseReceived',
                    'params': {
                        'type': 'Document',
                        'response': {'url': url, 'status': 412},
                    },
                },
            }),
        }
        browser = Mock()
        browser.get_log.return_value = [event]
        browser.title = ''
        browser.find_element.return_value.text = ''

        with self.assertRaises(BilibiliVerificationRequired):
            ensure_page_access(browser, url)

    def test_detail_page_scan_is_bounded_and_advances_past_cached_entries(self):
        entries = [{'url': 'https://t.bilibili.com/%s' % i} for i in range(12)]
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.side_effect = [True] * 2 + [False] * 10
        scanner.scan_cache_dao.has_recent_failure.return_value = False

        selected = scanner.select_detail_entries_to_scan(entries)

        self.assertEqual(selected, entries[2:4])
        self.assertEqual(scanner.scan_cache_skip_count, 2)

    def test_latest_article_is_not_advanced_to_older_backlog(self):
        entries = [
            {'url': 'https://www.bilibili.com/opus/1272441798722060296'},
            {'url': 'https://www.bilibili.com/opus/1272047404752830466'},
        ]
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = True

        selected = scanner.select_latest_detail_entry(entries)

        self.assertEqual(selected, [])
        self.assertEqual(scanner.scan_cache_skip_count, 1)

    def test_successfully_scanned_detail_page_is_skipped(self):
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = True

        should_scan = scanner.should_scan_detail_page('https://t.bilibili.com/123')

        self.assertFalse(should_scan)
        self.assertEqual(scanner.scan_cache_skip_count, 1)

    def test_recently_failed_detail_page_respects_retry_cooldown(self):
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = False
        scanner.scan_cache_dao.has_recent_failure.return_value = True

        should_scan = scanner.should_scan_detail_page('https://t.bilibili.com/123')

        self.assertFalse(should_scan)
        self.assertEqual(scanner.scan_cache_retry_skip_count, 1)

    def test_page_snapshot_uses_one_script_and_rejects_non_giveaway_marker(self):
        giveaway_url = 'https://t.bilibili.com/123'
        unrelated_url = 'https://t.bilibili.com/456'
        browser = Mock()
        browser.execute_script.return_value = [
            {
                'text': '关注并转发即可参与抽奖',
                'links': [
                    {'href': giveaway_url, 'dataUrl': ''},
                    {'href': giveaway_url + '?from=card', 'dataUrl': ''},
                ],
            },
            {
                'text': '投稿了文章',
                'links': [{'href': unrelated_url, 'dataUrl': ''}],
            },
        ]

        entries = SearchDynamicByUps.extract_giveaway_entries_from_page(browser)

        self.assertEqual([entry['url'] for entry in entries], [giveaway_url])
        browser.execute_script.assert_called_once()
        browser.find_elements.assert_not_called()

    def test_snapshot_limits_cards_to_top_level_feed_items(self):
        # 二级引用动态位于顶层的嵌套 .bili-dyn-item 中；只应读取空间列表的直接卡片。
        self.assertIn('.bili-dyn-list__item > .bili-dyn-item', SearchDynamicByUps.CARD_SNAPSHOT_SCRIPT)
        self.assertIn('querySelectorAll', SearchDynamicByUps.CARD_SNAPSHOT_SCRIPT)
        self.assertNotIn("querySelectorAll('.bili-dyn-item')", SearchDynamicByUps.CARD_SNAPSHOT_SCRIPT)

    def test_feed_snapshot_filters_old_items_and_classifies_recent_articles(self):
        now = datetime(2026, 11, 20, 12, 0, 0)
        snapshot = {
            'code': 0,
            'items': [
                {
                    'id': '1270000000000000001',
                    'type': 'DYNAMIC_TYPE_ARTICLE',
                    'pub_ts': str(int((now - timedelta(days=2)).timestamp())),
                    'text': '最新抽奖合集，转发参与',
                },
                {
                    'id': '880000000000000001',
                    'type': 'DYNAMIC_TYPE_ARTICLE',
                    'pub_ts': str(int((now - timedelta(days=400)).timestamp())),
                    'text': '旧抽奖合集，转发参与',
                },
            ],
        }

        entries = SearchDynamicByUps.select_feed_giveaway_entries(snapshot, now=now)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['url'], 'https://www.bilibili.com/opus/1270000000000000001')
        self.assertEqual(entries[0]['kind'], 'detail')

    def test_feed_snapshot_accepts_recent_textless_draw_post(self):
        now = datetime(2026, 11, 20, 12, 0, 0)
        snapshot = {
            'code': 0,
            'items': [
                {
                    'id': '1270000000000000002',
                    'type': 'DYNAMIC_TYPE_DRAW',
                    'major_type': 'MAJOR_TYPE_DRAW',
                    'has_draw': True,
                    'pub_ts': str(int((now - timedelta(days=1)).timestamp())),
                    'text': '',
                },
            ],
        }

        entries = SearchDynamicByUps.select_feed_giveaway_entries(snapshot, now=now)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['kind'], 'direct')

    def test_feed_snapshot_uses_original_dynamic_from_forward_pool(self):
        now = datetime(2026, 11, 20, 12, 0, 0)
        snapshot = {
            'code': 0,
            'items': [
                {
                    'id': '1272785254350848003',
                    'type': 'DYNAMIC_TYPE_FORWARD',
                    'pub_ts': str(int((now - timedelta(minutes=5)).timestamp())),
                    'text': '转发动态',
                    'major_type': '',
                    'has_draw': False,
                    'orig_id': '1272768482539208721',
                    'orig_type': 'DYNAMIC_TYPE_DRAW',
                    'orig_major_type': 'MAJOR_TYPE_DRAW',
                    'orig_has_draw': True,
                },
            ],
        }

        entries = SearchDynamicByUps.select_feed_giveaway_entries(snapshot, now=now)

        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]['url'],
            'https://www.bilibili.com/opus/1272768482539208721',
        )
        self.assertEqual(entries[0]['kind'], 'direct')

    def test_article_source_skips_aggregator_own_draw_card(self):
        now = datetime(2026, 11, 20, 12, 0, 0)
        snapshot = {
            'code': 0,
            'items': [
                {
                    'id': '1272793337515999241',
                    'type': 'DYNAMIC_TYPE_DRAW',
                    'major_type': 'MAJOR_TYPE_DRAW',
                    'has_draw': True,
                    'pub_ts': str(int((now - timedelta(hours=1)).timestamp())),
                    'text': '',
                },
            ],
        }

        entries = SearchDynamicByUps.select_feed_giveaway_entries(
            snapshot, now=now, allow_direct_draw=False, allow_forward=False
        )

        self.assertEqual(entries, [])

    def test_forward_pool_skips_own_draw_and_keeps_forwarded_origin(self):
        now = datetime(2026, 11, 20, 12, 0, 0)
        snapshot = {
            'code': 0,
            'items': [
                {
                    'id': '1272793337515999241',
                    'type': 'DYNAMIC_TYPE_DRAW',
                    'major_type': 'MAJOR_TYPE_DRAW',
                    'has_draw': True,
                    'pub_ts': str(int((now - timedelta(hours=2)).timestamp())),
                    'text': '',
                },
                {
                    'id': '1272785254350848003',
                    'type': 'DYNAMIC_TYPE_FORWARD',
                    'pub_ts': str(int((now - timedelta(minutes=5)).timestamp())),
                    'text': '转发动态',
                    'orig_id': '1272768482539208721',
                    'orig_type': 'DYNAMIC_TYPE_DRAW',
                    'orig_major_type': 'MAJOR_TYPE_DRAW',
                    'orig_has_draw': True,
                },
            ],
        }

        entries = SearchDynamicByUps.select_feed_giveaway_entries(
            snapshot, now=now, allow_direct_draw=False, allow_forward=True
        )

        self.assertEqual(
            [entry['url'] for entry in entries],
            ['https://www.bilibili.com/opus/1272768482539208721'],
        )

    def test_feed_scan_opens_recent_detail_and_enqueues_resolved_links(self):
        giveaway_url = 'https://t.bilibili.com/123'
        direct_url = 'https://www.bilibili.com/opus/456'
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner._open_page = Mock()
        scanner.ensure_page_access = Mock()
        scanner.extract_feed_snapshot_from_api = Mock(return_value={'code': 0})
        scanner.select_feed_giveaway_entries = Mock(return_value=[
            {'url': 'https://www.bilibili.com/opus/789', 'kind': 'detail', 'publish_time': None},
            {'url': direct_url, 'kind': 'direct', 'publish_time': None},
        ])
        scanner.scan_cache_dao = Mock()
        scanner.scan_cache_dao.has_success.return_value = False
        scanner.scan_cache_dao.has_recent_failure.return_value = False
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.extract_links_from_detail_page = Mock(return_value=[giveaway_url])
        scanner.dynLinks_to_db = Mock()
        browser = Mock()

        scanner.search_dynamic_links_by_up(
            browser, 'https://space.bilibili.com/123/dynamic', 'source'
        )

        scanner.extract_links_from_detail_page.assert_called_once_with(
            browser, 'https://www.bilibili.com/opus/789'
        )
        self.assertEqual(
            scanner.dynLinks_to_db.call_args.args[0], [giveaway_url, direct_url]
        )

    def test_existing_terminal_states_are_not_reactivated_by_rescan(self):
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.count = 0
        scanner.search_note = ''
        scanner.draw_dynamic_dao = Mock()
        scanner.draw_dynamic_dao.query_by_dyn_url.side_effect = [
            [{'status': '1'}],
            [{'status': '2'}],
            [{'status': '3'}],
            [{'status': '4'}],
            [{'status': '5'}],
        ]

        scanner.dynLinks_to_db(
            [
                'https://www.bilibili.com/opus/1271215276335038401',
                'https://www.bilibili.com/opus/1271215276335038402',
                'https://www.bilibili.com/opus/1271215276335038403',
                'https://www.bilibili.com/opus/1271215276335038404',
                'https://www.bilibili.com/opus/1271215276335038405',
            ],
            'https://space.bilibili.com/126038161/dynamic',
            'J君名',
        )

        scanner.draw_dynamic_dao.update_sharedUrl.assert_not_called()
        scanner.draw_dynamic_dao.insert.assert_not_called()

    def test_stale_dynamic_ids_are_not_enqueued(self):
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.count = 0
        scanner.search_note = ''
        scanner.draw_dynamic_dao = Mock()
        scanner.draw_dynamic_dao.query_by_dyn_url.return_value = []

        scanner.dynLinks_to_db(
            [
                'https://t.bilibili.com/599221420391003596',
                'https://www.bilibili.com/opus/1271215276335038485',
            ],
            'https://space.bilibili.com/126038161/dynamic',
            'J君名',
        )

        scanner.draw_dynamic_dao.insert.assert_called_once_with(
            'https://www.bilibili.com/opus/1271215276335038485',
            'https://space.bilibili.com/126038161/dynamic',
            'J君名',
        )

    def test_detail_links_are_read_in_one_javascript_snapshot(self):
        browser = Mock()
        browser.execute_script.return_value = [
            {'href': 'https://www.bilibili.com/opus/123?spm=1', 'dataUrl': ''},
            {'href': '', 'dataUrl': 'https://t.bilibili.com/456?from=card'},
            {'href': 'https://example.com/789', 'dataUrl': ''},
        ]

        links = SearchDynamicByUps.extract_links_from_current_page(browser)

        self.assertEqual(
            links,
            [
                'https://www.bilibili.com/opus/123',
                'https://t.bilibili.com/456',
            ],
        )
        browser.execute_script.assert_called_once()
        browser.find_elements.assert_not_called()

    def test_one_source_failure_does_not_skip_remaining_sources(self):
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.user_id = '35549700'
        scanner.count = 0
        scanner.search_note = ''
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.statistics_dao = Mock()
        browser = Mock()

        with patch(
            'service.search_draw_dynamic_service.SearchDynamicByUps.init_webdriver',
            return_value=(browser, Mock()),
        ), patch(
            'service.search_draw_dynamic_service.SearchDynamicByUps.LoginService'
        ), patch.object(
            scanner,
            'search_configured_up',
            side_effect=[RuntimeError('source timeout'), None],
        ) as search_up, patch.object(globals, 'ups', ['100680137', '226257459']):
            scanner.init_search()

        self.assertEqual(search_up.call_count, 2)
        scanner.statistics_dao.insert.assert_called_once()
        browser.quit.assert_called_once()

    def test_init_search_authenticates_before_scanning_feeds(self):
        scanner = SearchDynamicByUps.__new__(SearchDynamicByUps)
        scanner.user_id = '35549700'
        scanner.count = 0
        scanner.search_note = ''
        scanner.scan_cache_skip_count = 0
        scanner.scan_cache_retry_skip_count = 0
        scanner.statistics_dao = Mock()
        browser = Mock()
        events = []

        with patch(
            'service.search_draw_dynamic_service.SearchDynamicByUps.init_webdriver',
            return_value=(browser, Mock()),
        ) as webdriver, patch.object(
            scanner,
            'search_configured_up',
            side_effect=lambda *args: events.append('scan'),
        ) as search_up, patch(
            'service.login_service.login_service.LoginService.login_by_cookie'
        ) as login:
            login.side_effect = lambda *args: events.append('login')
            with patch.object(globals, 'ups', ['100680137']):
                scanner.init_search()

        search_up.assert_called_once_with(browser, '100680137')
        login.assert_called_once()
        webdriver.assert_called_once_with(
            proxy_url=globals.verification_proxy_url or None
        )
        self.assertEqual(events, ['login', 'scan'])
        browser.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
