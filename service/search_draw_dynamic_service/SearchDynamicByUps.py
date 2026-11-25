import re
import time
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait

from dao.draw_dynamic_dao import DrawDynamicDao
from dao.init_db import init_db
from dao.scan_cache_dao import ScanCacheDao
from dao.statistics_dao import StatisticsDao
from service.bilibili_errors import (
    BilibiliVerificationRequired,
    document_response_status,
    ensure_page_access,
    is_verification_block,
)
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.notify_service.notify_service import NotifyService
from utils.ip_util import remove_query_string
from utils.webdriver_util import force_delete_selenium_session, init_webdriver
from utils import globals


mylogger = MyLogger('SearchDynamicByUps.py').getLogger()


class SearchDynamicByUps(object):
    DYNAMIC_CARD_SELECTORS = (
        '.bili-dyn-list__item > .bili-dyn-item',
        '.bili-dyn-item',
    )
    DYNAMIC_LINK_SELECTORS = (
        'a.opus-text-rich-hl[href]',
        'a[href*="t.bilibili.com/"]',
        'a[href*="/opus/"]',
        '[data-url*="t.bilibili.com/"]',
        '[data-url*="/opus/"]',
    )
    MAX_DETAIL_PAGES_PER_SOURCE = 2
    FAILED_DETAIL_RETRY_HOURS = 24
    DYNAMIC_SOURCES = {
        '你的工具人老公': 'https://space.bilibili.com/100680137/dynamic',
        '_大锦鲤_': 'https://space.bilibili.com/226257459/dynamic',
        'J君名': 'https://space.bilibili.com/126038161/dynamic',
        '糯米是个背包': 'https://space.bilibili.com/492426375/dynamic',
        '抽奖工具人转发池': 'https://space.bilibili.com/280604312/dynamic',
    }
    FORWARD_POOL_SOURCES = frozenset({'抽奖工具人转发池'})
    SOURCE_SPECS = {
        '你的工具人老公': {
            'mid': '100680137',
            'mode': 'readlists',
            'readlist_families': ('抽奖合集', '官方抽奖合集'),
            'article_limit': 1,
        },
        '_大锦鲤_': {
            'mid': '226257459',
            'mode': 'readlists',
            'readlist_names': ('抽奖专栏',),
            'article_limit': 1,
        },
        'J君名': {
            'mid': '126038161',
            'mode': 'readlists',
            'readlist_names': ('抽奖',),
            'article_limit': 1,
        },
        '糯米是个背包': {
            'mid': '492426375',
            'mode': 'year_readlist',
            'article_limit': 1,
        },
        '抽奖工具人转发池': {
            'mid': '280604312',
            'mode': 'forward_feed',
        },
    }
    READLIST_API = 'https://api.bilibili.com/x/article/up/lists?mid=%s&sort=0'
    ARTICLE_LIST_API = 'https://api.bilibili.com/x/article/list/web/articles?id=%s'
    SPACE_ARTICLE_API = (
        'https://api.bilibili.com/x/space/article'
        '?mid=%s&pn=1&ps=2&sort=publish_time'
    )
    GIVEAWAY_MARKERS = ('抽奖', '福利', '奖品', '抽取', '有奖', '中奖')
    FEED_ITEM_MAX_AGE_DAYS = 7
    MIN_CURRENT_DYNAMIC_ID = 1_250_000_000_000_000_000
    FEED_API_TEMPLATE = (
        'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space'
        '?host_mid=%s&offset=&timezone_offset=-480'
    )
    FEED_API_SCRIPT = r'''
        const endpoint = arguments[0];
        const done = arguments[1];
        const timer = setTimeout(() => done({timeout: true}), 20000);
        function collect(value, out, depth) {
          if (depth > 6 || value === null || value === undefined) return;
          if (typeof value === 'string') {
            if (value.trim()) out.push(value.trim());
            return;
          }
          if (Array.isArray(value)) {
            for (const item of value.slice(0, 30)) collect(item, out, depth + 1);
            return;
          }
          if (typeof value === 'object') {
            for (const [key, child] of Object.entries(value)) {
              if (['avatar', 'decorate', 'badge', 'like_icon', 'jump_url', 'resource'].includes(key)) continue;
              collect(child, out, depth + 1);
            }
          }
        }
        fetch(endpoint, {credentials: 'include'})
          .then(response => response.json())
          .then(payload => {
            clearTimeout(timer);
            const items = ((payload.data || {}).items || []).map(item => {
              const modules = item.modules || {};
              const author = modules.module_author || {};
              const major = (modules.module_dynamic || {}).major || {};
              const original = item.orig || {};
              const originalModules = original.modules || {};
              const originalAuthor = originalModules.module_author || {};
              const originalMajor = (originalModules.module_dynamic || {}).major || {};
              const texts = [];
              collect(modules.module_dynamic || {}, texts, 0);
              if (item.orig && item.orig.modules) {
                collect(item.orig.modules.module_dynamic || {}, texts, 0);
              }
              return {
                id: String(item.id_str || item.id || ''),
                type: item.type || '',
                author: author.name || '',
                pub_ts: author.pub_ts || null,
                text: Array.from(new Set(texts)).join(' ').slice(0, 1600),
                major_type: major.type || '',
                has_draw: Boolean(major.draw),
                orig_id: String(original.id_str || original.id || ''),
                orig_type: original.type || '',
                orig_pub_ts: originalAuthor.pub_ts || null,
                orig_major_type: originalMajor.type || '',
                orig_has_draw: Boolean(originalMajor.draw),
              };
            });
            done({code: payload.code, message: payload.message, items: items});
          })
          .catch(error => {
            clearTimeout(timer);
            done({error: String(error)});
          });
    '''
    JSON_FETCH_SCRIPT = r'''
        const endpoint = arguments[0];
        const done = arguments[1];
        const timer = setTimeout(() => done({timeout: true}), 20000);
        fetch(endpoint, {credentials: 'include'})
          .then(response => response.json())
          .then(payload => {
            clearTimeout(timer);
            done(payload);
          })
          .catch(error => {
            clearTimeout(timer);
            done({error: String(error)});
          });
    '''
    DETAIL_LINKS_SNAPSHOT_SCRIPT = '''
        return Array.from(document.querySelectorAll('a[href], [data-url]')).map(link => ({
            href: link.getAttribute('href') || '',
            dataUrl: link.getAttribute('data-url') || '',
        }));
    '''
    DETAIL_CONTENT_READY_SCRIPT = '''
        const dynamicLinks = Array.from(document.querySelectorAll('a[href], [data-url]')).filter(link => {
            const value = link.getAttribute('href') || link.getAttribute('data-url') || '';
            return /(?:bilibili\\.com\\/opus\\/|t\\.bilibili\\.com\\/)\\d+/.test(value);
        });
        return {
            readyState: document.readyState,
            bodyLength: (document.body && document.body.innerText || '').length,
            dynamicLinkCount: dynamicLinks.length,
        };
    '''
    CARD_SNAPSHOT_SCRIPT = '''
        const cards = Array.from(document.querySelectorAll(
            '.bili-dyn-list__item > .bili-dyn-item'
        ));
        return cards.map((card) => ({
            text: (card.innerText || '').replace(/\\s+/g, ' ').trim(),
            links: Array.from(card.querySelectorAll('a[href], [data-url]')).map((link) => ({
                href: link.getAttribute('href') || '',
                dataUrl: link.getAttribute('data-url') || '',
            })),
        }));
    '''

    def __init__(self, user_id):
        self.count = 0
        self.user_id = user_id
        self.search_note = ''
        db = init_db()
        self.draw_dynamic_dao = DrawDynamicDao(db)
        self.scan_cache_dao = ScanCacheDao(db)
        self.statistics_dao = StatisticsDao(db)
        self.scan_cache_skip_count = 0
        self.scan_cache_retry_skip_count = 0

    def should_scan_detail_page(self, detail_url):
        if self.scan_cache_dao.has_success('detail', detail_url):
            self.scan_cache_skip_count += 1
            mylogger.info('详情页已扫描，跳过：%s', detail_url)
            return False
        if self.scan_cache_dao.has_recent_failure(
            'detail', detail_url, self.FAILED_DETAIL_RETRY_HOURS
        ):
            self.scan_cache_retry_skip_count += 1
            mylogger.info('详情页近期扫描失败，暂不重试：%s', detail_url)
            return False
        return True

    def select_detail_entries_to_scan(self, entries):
        selected = []
        for entry in entries:
            if not self.should_scan_detail_page(entry['url']):
                continue
            selected.append(entry)
            if len(selected) >= self.MAX_DETAIL_PAGES_PER_SOURCE:
                break
        return selected

    def select_latest_detail_entry(self, entries):
        """只检查最新合集；已扫描则等待下一篇，不回头扫历史积压。"""
        if not entries:
            return []
        latest = entries[0]
        if not self.should_scan_detail_page(latest['url']):
            return []
        return [latest]

    @staticmethod
    def is_verification_block(status_code=None, page_title='', page_text=''):
        return is_verification_block(status_code, page_title, page_text)

    @classmethod
    def document_response_status(cls, browser, target_url):
        return document_response_status(browser, target_url)

    @classmethod
    def ensure_page_access(cls, browser, target_url):
        ensure_page_access(browser, target_url)

    @staticmethod
    def find_dynamic_cards(browser):
        for selector in SearchDynamicByUps.DYNAMIC_CARD_SELECTORS:
            cards = browser.find_elements(By.CSS_SELECTOR, selector)
            if cards:
                return cards
        return []

    @classmethod
    def normalize_dynamic_url(cls, url):
        if not url:
            return None
        value = str(url).strip()
        if value.startswith('//'):
            value = 'https:' + value
        elif value.startswith('/opus/'):
            value = 'https://www.bilibili.com' + value
        elif value.startswith('t.bilibili.com/'):
            value = 'https://' + value

        parsed = urlparse(value)
        host = (parsed.hostname or '').lower()
        if host == 't.bilibili.com' and re.fullmatch(r'/\d+', parsed.path):
            pass
        elif host in ('www.bilibili.com', 'bilibili.com') and re.fullmatch(r'/opus/\d+', parsed.path):
            pass
        else:
            return None

        normalized_host = 'www.bilibili.com' if host == 'bilibili.com' else host
        normalized = urlunparse((
            'https',
            normalized_host,
            parsed.path.rstrip('/'),
            '',
            '',
            '',
        ))
        return normalized if parsed.path.strip('/') else None

    @classmethod
    def append_unique_link(cls, links, url):
        normalized = cls.normalize_dynamic_url(url)
        if normalized and normalized not in links:
            links.append(normalized)

    @classmethod
    def is_current_dynamic_link(cls, url):
        normalized = cls.normalize_dynamic_url(url)
        if not normalized:
            return False
        match = re.search(r'/(\d+)$', normalized)
        if not match:
            return False
        return int(match.group(1)) >= cls.MIN_CURRENT_DYNAMIC_ID

    @classmethod
    def extract_links_from_current_page(cls, browser):
        links = []
        for element in browser.execute_script(cls.DETAIL_LINKS_SNAPSHOT_SCRIPT) or []:
            if not isinstance(element, dict):
                continue
            for attribute in ('href', 'dataUrl'):
                cls.append_unique_link(links, element.get(attribute))
        return links

    @classmethod
    def extract_feed_snapshot_from_api(cls, browser, up_id):
        try:
            browser.set_script_timeout(45)
        except Exception:
            pass
        result = browser.execute_async_script(
            cls.FEED_API_SCRIPT,
            cls.FEED_API_TEMPLATE % str(up_id),
        )
        if not isinstance(result, dict):
            raise RuntimeError('空间动态接口返回格式异常')
        if result.get('timeout'):
            raise TimeoutException('空间动态接口读取超时')
        if result.get('error'):
            raise RuntimeError('空间动态接口读取失败：%s' % result['error'])
        if result.get('code') != 0:
            raise RuntimeError(
                '空间动态接口返回异常：code=%s，message=%s'
                % (result.get('code'), result.get('message') or '未知')
            )
        return result

    @classmethod
    def fetch_json(cls, browser, endpoint):
        try:
            browser.set_script_timeout(45)
        except Exception:
            pass
        result = browser.execute_async_script(cls.JSON_FETCH_SCRIPT, endpoint)
        if not isinstance(result, dict):
            raise RuntimeError('B站接口返回格式异常')
        if result.get('timeout'):
            raise TimeoutException('B站接口读取超时')
        if result.get('error'):
            raise RuntimeError('B站接口读取失败：%s' % result['error'])
        return result

    @staticmethod
    def normalize_readlist_title(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))

    @classmethod
    def parse_readlist_suffix(cls, value):
        suffix = cls.normalize_readlist_title(value)
        if suffix.startswith('系列'):
            suffix = suffix[2:]
        if not suffix:
            return 1
        if suffix.isdigit():
            return int(suffix)
        digits = {
            '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3,
            '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
        }
        units = {'十': 10, '百': 100, '千': 1000}
        if any(char not in digits and char not in units for char in suffix):
            return 0
        total = 0
        current = 0
        for char in suffix:
            if char in digits:
                current = digits[char]
                continue
            unit = units[char]
            total += (current or 1) * unit
            current = 0
        return total + current

    @classmethod
    def select_readlists(cls, spec, rows):
        candidates = [row for row in rows if isinstance(row, dict)]
        selected = []
        mode = spec.get('mode')
        if mode == 'readlists':
            titles = spec.get('readlist_families') or spec.get('readlist_names') or ()
            for title in titles:
                normalized_title = cls.normalize_readlist_title(title)
                matches = [
                    row for row in candidates
                    if cls.normalize_readlist_title(row.get('name') or row.get('title'))
                    .startswith(normalized_title)
                ]
                if spec.get('readlist_names'):
                    matches = [
                        row for row in matches
                        if cls.normalize_readlist_title(row.get('name') or row.get('title'))
                        == normalized_title
                    ]
                if not matches:
                    continue

                def rank(row):
                    raw_title = cls.normalize_readlist_title(
                        row.get('name') or row.get('title')
                    )
                    suffix = raw_title[len(normalized_title):]
                    return (
                        cls.parse_readlist_suffix(suffix),
                        int(row.get('update_time') or 0),
                        int(row.get('id') or 0),
                    )

                selected.append(max(matches, key=rank))
        elif mode == 'year_readlist':
            years = [
                row for row in candidates
                if str(row.get('name') or row.get('title') or '').isdigit()
                and len(str(row.get('name') or row.get('title') or '')) == 4
                and 2000 <= int(row.get('name') or row.get('title')) <= 2100
            ]
            if years:
                selected.append(max(years, key=lambda row: int(row.get('name') or row.get('title'))))

        unique = []
        seen = set()
        for row in selected:
            row_id = str(row.get('id') or '')
            if row_id and row_id not in seen:
                seen.add(row_id)
                unique.append(row)
        return unique

    @staticmethod
    def select_latest_articles(rows, limit=1):
        if int(limit) < 1:
            return []

        def timestamp(row):
            try:
                return int(row.get('publish_time') or 0)
            except (TypeError, ValueError):
                return 0

        ordered = sorted(
            [row for row in rows if isinstance(row, dict)],
            key=lambda row: (timestamp(row), int(row.get('id') or 0)),
            reverse=True,
        )
        return ordered[:int(limit)]

    @staticmethod
    def select_recent_articles(rows, days=7, now=None):
        now = now or datetime.now()
        cutoff = now - timedelta(days=max(1, int(days)))
        selected = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                published = datetime.fromtimestamp(int(row.get('publish_time') or 0))
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            if published >= cutoff:
                selected.append(row)
        return SearchDynamicByUps.select_latest_articles(selected, len(selected))

    def should_scan_article(self, article_url):
        if self.scan_cache_dao.has_success('article', article_url):
            self.scan_cache_skip_count += 1
            mylogger.info('合集文章已扫描，跳过：%s', article_url)
            return False
        if self.scan_cache_dao.has_recent_failure(
            'article', article_url, self.FAILED_DETAIL_RETRY_HOURS
        ):
            self.scan_cache_retry_skip_count += 1
            mylogger.info('合集文章近期扫描失败，暂不重试：%s', article_url)
            return False
        return True

    def search_readlist_source(self, browser, spec, note):
        mid = str(spec['mid'])
        base_url = 'https://space.bilibili.com/%s/dynamic' % mid
        readlist_payload = self.fetch_json(browser, self.READLIST_API % mid)
        if readlist_payload.get('code') != 0:
            mylogger.warning(
                '合集列表接口异常 %s：code=%s，继续检查空间最新文章',
                note,
                readlist_payload.get('code'),
            )
            readlists = []
        else:
            readlists = (readlist_payload.get('data') or {}).get('lists') or []
        selected = self.select_readlists(spec, readlists)

        articles_to_scan = []
        space_payload = self.fetch_json(browser, self.SPACE_ARTICLE_API % mid)
        if space_payload.get('code') == 0:
            space_articles = (space_payload.get('data') or {}).get('articles') or []
            articles_to_scan.extend(
                self.select_recent_articles(space_articles, days=7)
            )
        else:
            mylogger.warning(
                '空间最新文章接口异常 %s：code=%s', note, space_payload.get('code')
            )

        for readlist in selected:
            readlist_id = str(readlist.get('id') or '')
            if not readlist_id.isdigit():
                continue
            payload = self.fetch_json(browser, self.ARTICLE_LIST_API % readlist_id)
            if payload.get('code') != 0:
                mylogger.warning(
                    '合集文章列表异常 %s/%s：code=%s',
                    note,
                    readlist_id,
                    payload.get('code'),
                )
                continue
            articles = (payload.get('data') or {}).get('articles') or []
            articles_to_scan.extend(
                self.select_recent_articles(articles, days=7)
            )

        links = []
        scanned_articles = set()
        for article in articles_to_scan:
            article_id = str(article.get('id') or '')
            if not article_id.isdigit() or article_id in scanned_articles:
                continue
            scanned_articles.add(article_id)
            article_url = 'https://www.bilibili.com/read/cv%s' % article_id
            if not self.should_scan_article(article_url):
                continue
            article_links = self.extract_links_from_detail_page(browser, article_url)
            if article_links is None:
                self.scan_cache_dao.save_failure(
                    'article', article_url, base_url, note, '合集文章加载失败'
                )
                continue
            self.scan_cache_dao.save_success(
                'article', article_url, base_url, note, len(article_links)
            )
            links.extend(article_links)

        unique_links = []
        for link in links:
            self.append_unique_link(unique_links, link)
        self.dynLinks_to_db(unique_links, base_url, note)

    @classmethod
    def select_feed_giveaway_entries(
        cls,
        snapshot,
        now=None,
        allow_direct_draw=True,
        allow_forward=True,
    ):
        now = now or datetime.now()
        cutoff = now - timedelta(days=cls.FEED_ITEM_MAX_AGE_DAYS)
        entries = []
        seen = set()
        for item in snapshot.get('items') or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get('id') or '').strip()
            try:
                publish_time = datetime.fromtimestamp(int(item.get('pub_ts')))
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            if publish_time < cutoff or publish_time > now + timedelta(days=1):
                continue

            item_type = str(item.get('type') or '')
            major_type = str(item.get('major_type') or '')
            has_draw = bool(item.get('has_draw'))
            source_is_forward = item_type == 'DYNAMIC_TYPE_FORWARD'
            if item_type == 'DYNAMIC_TYPE_FORWARD':
                if not allow_forward:
                    continue
                item_id = str(item.get('orig_id') or '').strip()
                item_type = str(item.get('orig_type') or '')
                major_type = str(item.get('orig_major_type') or '')
                has_draw = bool(item.get('orig_has_draw'))
            if not item_id.isdigit():
                continue

            if (
                not allow_direct_draw
                and not source_is_forward
                and item_type != 'DYNAMIC_TYPE_ARTICLE'
            ):
                continue

            text = str(item.get('text') or '').casefold()
            has_giveaway_marker = any(
                marker.casefold() in text for marker in cls.GIVEAWAY_MARKERS
            )
            if not has_giveaway_marker and not has_draw and major_type != 'MAJOR_TYPE_DRAW':
                continue
            if item_type == 'DYNAMIC_TYPE_ARTICLE':
                kind = 'detail'
            elif item_type in ('DYNAMIC_TYPE_AV', 'DYNAMIC_TYPE_FORWARD'):
                continue
            else:
                kind = 'direct'
            url = cls.normalize_dynamic_url(
                'https://www.bilibili.com/opus/' + item_id
            )
            if not url or url in seen:
                continue
            seen.add(url)
            entries.append({
                'url': url,
                'kind': kind,
                'publish_time': publish_time,
            })
        return entries

    @classmethod
    def extract_detail_entries_from_cards(cls, cards):
        entries = []
        seen = set()
        for card in cards:
            text = (card.text or '').casefold()
            if not any(marker.casefold() in text for marker in cls.GIVEAWAY_MARKERS):
                continue

            elements = [card]
            elements.extend(card.find_elements(By.CSS_SELECTOR, '[data-url], a[href]'))
            for element in elements:
                for attribute in ('data-url', 'href'):
                    url = cls.normalize_dynamic_url(element.get_attribute(attribute))
                    if url and url not in seen:
                        seen.add(url)
                        entries.append({'url': url, 'publish_time': None})
        return entries

    @classmethod
    def extract_giveaway_entries_from_page(cls, browser):
        snapshots = browser.execute_script(cls.CARD_SNAPSHOT_SCRIPT) or []
        entries = []
        seen = set()
        for card in snapshots:
            if not isinstance(card, dict):
                continue
            text = str(card.get('text') or '').casefold()
            if not any(marker.casefold() in text for marker in cls.GIVEAWAY_MARKERS):
                continue
            for link in card.get('links') or []:
                if not isinstance(link, dict):
                    continue
                for attribute in ('dataUrl', 'href'):
                    url = cls.normalize_dynamic_url(link.get(attribute))
                    if url and url not in seen:
                        seen.add(url)
                        entries.append({'url': url, 'publish_time': None})
        return entries

    @staticmethod
    def is_missing_dynamic_page(browser):
        title = (getattr(browser, 'title', '') or '').strip()
        if '404' in title or '出错啦' in title:
            return True
        try:
            body_text = browser.find_element(By.TAG_NAME, 'body').text or ''
        except Exception:
            return False
        markers = ('页面不存在', '动态不存在', '内容不存在', '已被删除')
        return any(marker in body_text for marker in markers)

    @staticmethod
    def _wait_for_body(browser):
        WebDriverWait(browser, 15).until(
            lambda driver: driver.execute_script('return document.readyState')
            in ('interactive', 'complete')
        )

    @classmethod
    def wait_detail_content(cls, browser, timeout=35):
        def content_ready(driver):
            result = driver.execute_script(cls.DETAIL_CONTENT_READY_SCRIPT) or {}
            return (
                int(result.get('dynamicLinkCount') or 0) > 0
                or int(result.get('bodyLength') or 0) > 500
            )
        WebDriverWait(browser, timeout).until(content_ready)

    @staticmethod
    def _stop_page_load(browser):
        try:
            browser.execute_script('window.stop();')
        except Exception:
            pass

    @classmethod
    def extract_links_from_detail_page(cls, browser, detail_url):
        try:
            browser.get(detail_url)
            cls.ensure_page_access(browser, detail_url)
            cls.wait_detail_content(browser)
        except TimeoutException:
            cls._stop_page_load(browser)
            try:
                cls.ensure_page_access(browser, detail_url)
            except BilibiliVerificationRequired:
                raise
            except Exception:
                pass
            mylogger.warning('详情页加载超时，跳过：%s', detail_url)
            return None
        except BilibiliVerificationRequired:
            raise
        except Exception as exc:
            mylogger.warning('详情页加载失败，跳过：%s，原因：%s', detail_url, exc)
            return None

        cls.ensure_page_access(browser, detail_url)
        if cls.is_missing_dynamic_page(browser):
            mylogger.warning('动态详情页不存在或已删除，跳过：%s', detail_url)
            return None

        detail_url = cls.normalize_dynamic_url(detail_url)
        return [
            link for link in cls.extract_links_from_current_page(browser)
            if link != detail_url
        ]

    @staticmethod
    def _open_page(browser, url):
        try:
            browser.get(url)
        except TimeoutException:
            SearchDynamicByUps._stop_page_load(browser)
            SearchDynamicByUps.ensure_page_access(browser, url)
            raise
        SearchDynamicByUps.ensure_page_access(browser, url)

    def search_dynamic_links_by_up(self, browser, base_url, note):
        self._open_page(browser, base_url)
        up_match = re.search(r'space\.bilibili\.com/(\d+)', base_url)
        if not up_match:
            raise RuntimeError('无法从订阅地址解析 UP UID：%s' % base_url)
        up_id = up_match.group(1)
        try:
            snapshot = self.extract_feed_snapshot_from_api(browser, up_id)
        except TimeoutException:
            mylogger.warning('空间动态接口超时，重载页面后重试一次：%s', note)
            browser.get('about:blank')
            self._open_page(browser, base_url)
            snapshot = self.extract_feed_snapshot_from_api(browser, up_id)

        source_is_forward_pool = note in self.FORWARD_POOL_SOURCES
        giveaway_entries = self.select_feed_giveaway_entries(
            snapshot,
            allow_direct_draw=False,
            allow_forward=source_is_forward_pool,
        )
        detail_entries = [entry for entry in giveaway_entries if entry['kind'] == 'detail']
        direct_entries = [entry for entry in giveaway_entries if entry['kind'] == 'direct']
        # 最近 7 天内的文章型动态都要扫描；缓存负责跳过已经处理过的文章。
        selected_details = detail_entries
        links = []
        for entry in selected_details:
            detail_links = self.extract_links_from_detail_page(browser, entry['url'])
            if detail_links is None:
                self.scan_cache_dao.save_failure(
                    'detail', entry['url'], remove_query_string(base_url), note,
                    '详情页加载超时',
                )
                continue
            self.scan_cache_dao.save_success(
                'detail', entry['url'], remove_query_string(base_url), note,
                len(detail_links),
            )
            links.extend(detail_links)
        links.extend(entry['url'] for entry in direct_entries)
        self.dynLinks_to_db(links, remove_query_string(base_url), note)

    def normalize_up_source(self, item):
        value = str(item or '').strip()
        if not value:
            return None, None
        if value in self.DYNAMIC_SOURCES:
            return self.DYNAMIC_SOURCES[value], value
        if value.isdigit():
            return 'https://space.bilibili.com/%s/dynamic' % value, value
        match = re.search(r'space\.bilibili\.com/(\d+)', value)
        if match:
            return 'https://space.bilibili.com/%s/dynamic' % match.group(1), value
        mylogger.warning('无法识别订阅 UP 配置，跳过：%s', value)
        return None, None

    def search_configured_up(self, browser, item):
        base_url, note = self.normalize_up_source(item)
        if not base_url:
            return
        try:
            spec = self.SOURCE_SPECS.get(note)
            if spec and spec.get('mode') in ('readlists', 'year_readlist'):
                self.search_readlist_source(browser, spec, note)
            else:
                self.search_dynamic_links_by_up(browser, base_url, note)
        except BilibiliVerificationRequired:
            raise
        except Exception as exc:
            mylogger.error('从订阅 UP 查找抽奖动态出错 %s: %s', note, exc, exc_info=True)
            try:
                NotifyService().fangtang_msg_push_by_content(
                    title='订阅 UP 查找抽奖动态出错',
                    content='从“%s”查找抽奖动态出错' % note,
                )
            except Exception:
                mylogger.warning('推送扫描错误通知失败', exc_info=True)
            raise

    def dynLinks_to_db(self, dynLinks, source, note):
        cnt = 0
        ignore_links = globals.ignore_link.split('|') if globals.ignore_link else []
        for link in dynLinks:
            normalized = self.normalize_dynamic_url(link)
            if not normalized or not self.is_current_dynamic_link(normalized):
                continue
            if any(ignored in normalized for ignored in ignore_links):
                continue
            try:
                existing = self.draw_dynamic_dao.query_by_dyn_url(normalized)
                if not existing:
                    self.draw_dynamic_dao.insert(normalized, source, note)
                    cnt += 1
            except Exception as exc:
                mylogger.error('[动态插入数据库出错 %s]', exc, exc_info=True)
        self.count += cnt
        self.search_note += note + ':' + str(cnt) + ';  '

    def init_search(self):
        browser = None
        try:
            browser, chains = init_webdriver(
                proxy_url=globals.verification_proxy_url or None
            )
            LoginService(browser, chains, self.user_id).login_by_cookie()
            configured_sources = globals.ups or []
            mylogger.info(
                '本轮使用登录会话扫描订阅 UP 数量：%s',
                len(configured_sources),
            )
            for item in configured_sources:
                try:
                    self.search_configured_up(browser, item)
                except BilibiliVerificationRequired:
                    raise
                except Exception as exc:
                    mylogger.error(
                        '订阅源扫描失败，继续处理其他来源：%s（%s）',
                        item,
                        exc,
                        exc_info=True,
                    )
            self.search_note += (
                '详情页缓存跳过:' + str(self.scan_cache_skip_count)
                + '; 详情页失败冷却跳过:' + str(self.scan_cache_retry_skip_count)
                + '; '
            )
            self.statistics_dao.insert(
                '',
                '搜索到的抽奖动态条数为: ' + str(self.count),
                self.search_note,
            )
        except BilibiliVerificationRequired:
            mylogger.error('Bilibili 要求人工验证，停止本轮搜索及后续转发。')
            raise
        except Exception:
            mylogger.error('[搜索抽奖动态列表主流程出错]', exc_info=True)
            raise
        finally:
            if browser is not None:
                try:
                    browser.quit()
                except Exception:
                    mylogger.warning('关闭 Selenium 浏览器失败', exc_info=True)
                force_delete_selenium_session(browser)


if __name__ == '__main__':
    SearchDynamicByUps(globals.my_user_id).init_search()
