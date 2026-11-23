import json
import re
import time
from datetime import date
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from datetime import time as datetime_time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from service.log_service.log_printer_service import MyLogger
from utils import globals
from utils.ip_util import get_host_ip
from utils.time_util import random_sleep

mylogger = MyLogger('share_one_dynamic.py').getLogger()
SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
# Bilibili timestamps are Unix timestamps. Normalize them to naive
# Asia/Shanghai wall time at the boundary so DB rows and comparisons are
# consistent regardless of the container's system timezone.
UTC_TZ = timezone.utc


class DynamicShareBase(object):
    PAGE_READY_CSS = '.bili-dyn-item, .bili-dyn-content, .side-toolbar'
    FOLLOW_CSS = '.space-head-follow, .follow-btn, .space-follow-btn'
    LIKE_CSS = '.side-toolbar__action.like, .bili-dyn-action.like, [class*="side-toolbar__action"][class*="like"]'
    FORWARD_CSS = '.side-toolbar__action.forward, .bili-dyn-action.forward, [class*="side-toolbar__action"][class*="forward"]'
    SHARE_EDITOR_CSS = '.bili-dyn-share-publishing .bili-rich-textarea__inner[contenteditable="true"]'
    SHARE_PUBLISH_CSS = '.bili-dyn-share-publishing__action, .bili-dyn-share-publishing button'
    DYNAMIC_TEXT_CSS = (
        '.bili-dyn-item, .bili-dyn-card, .bili-dyn-content, '
        '.bili-dyn-content__orig, .bili-dyn-share-publishing__reference'
    )
    UP_TITLE_CSS = '.bili-dyn-title__text, .bili-dyn-title'
    UP_AT_CSS = '.bili-dyn-item [data-type="at"][data-oid], .bili-dyn-content [data-type="at"][data-oid]'
    LOTTERY_NOTICE_API_TEMPLATE = (
        'https://api.vc.bilibili.com/lottery_svr/v1/lottery_svr/lottery_notice'
        '?business_type=%s&business_id=%s'
    )
    LOTTERY_NOTICE_API = LOTTERY_NOTICE_API_TEMPLATE % (1, '%s')
    LOTTERY_NOTICE_SCRIPT = r'''
        const endpoint = arguments[0];
        const done = arguments[1];
        let finished = false;
        const finish = (payload) => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          done(payload);
        };
        const timer = setTimeout(() => finish({timeout: true}), 10000);
        fetch(endpoint, {credentials: 'include'})
          .then(response => response.json())
          .then(payload => {
            const data = payload.data || {};
            finish({
              code: payload.code,
              message: payload.message || '',
              lottery_id: data.lottery_id || '',
              lottery_time: data.lottery_time || null,
              status: data.status,
            });
          })
          .catch(error => {
            finish({error: String(error)});
          });
    '''

    def __init__(self):
        # 公有属性，可以在类外部访问
        self.upId = None
        self.upUrl = None
        self.upIds = []
        self.upUrls = []
        self.share_url = None
        self.status = 1
        self.machine_ip = None
        self.share_time = None
        self.share_status = 1
        self.user_id = None
        self.expire_date = None
        self.lottery_time = None
        self.lottery_source = None
        self.notice_lottery_time = None
        self.publish_time = None
        self.current_user_name = None
        self.last_error = None
        self.last_api_reason = None
        self.detail_request_min_seconds = globals.detail_request_min_seconds
        self.detail_request_max_seconds = globals.detail_request_max_seconds

    def share_one(
        self,
        bro,
        chains,
        lucky_dynamic_url,
        share_content,
        comment_content,
        lottery_end_time=None,
        lottery_kind='official',
        lottery_rule=None,
        required_actions=None,
    ):
        """
        提供需要转发的抽奖动态URL，然后“执行状态、up主的ID、up主的主页URL”等信息
        :param bro:
        :param chains:
        :param lucky_dynamic_url: 需要进行转发的抽奖动态的URL
        :return:
        """
        self.notice_lottery_time = None
        self.lottery_time = self._coerce_datetime(lottery_end_time)
        if required_actions is None:
            required_actions = ['follow', 'like', 'reserve', 'comment', 'forward']
        elif isinstance(required_actions, str):
            try:
                required_actions = json.loads(required_actions)
            except Exception:
                required_actions = ['follow', 'like', 'reserve', 'comment', 'forward']
        self.required_actions = list(required_actions or [])
        try:
            bro.get(lucky_dynamic_url)
            self.wait_page_ready(bro)
            self.share_url = lucky_dynamic_url
            if lottery_kind == 'plain_text':
                # 普通动态抽奖没有官方 lottery_id，规则已在验证阶段保存。
                self.lottery_source = 'plain_text'
                self.notice_lottery_time = self._coerce_datetime(lottery_end_time)
            else:
                confirmed, reason = self.verify_official_lottery(bro, lucky_dynamic_url)
                if not confirmed:
                    if reason == 'not_lottery':
                        self.status = 4
                        self.share_status = 4
                        mylogger.info('官方接口明确返回该动态无抽奖，排除url : ' + lucky_dynamic_url)
                        return
                    self.status = 3
                    self.share_status = 3
                    self.last_error = '无法确认官方互动抽奖：' + reason
                    return
            if self.notice_lottery_time is not None:
                self.lottery_time = self.notice_lottery_time
            if self.lottery_time is None:
                self.resolve_lottery_time(bro)
            if self.is_expired_dynamic(bro):
                self.status = 2
                self.share_status = 2
                mylogger.info("动态已过期，跳过转发url : " + lucky_dynamic_url)
                return
            self.ensure_publish_cookie(bro)
            if 'follow' in self.required_actions:
                self.click_follow_all(bro, chains)
                random_sleep()
            if 'like' in self.required_actions:
                self.click_like(bro, chains)
                random_sleep()
            if 'reserve' in self.required_actions:
                self.click_reserve(bro, chains)
                random_sleep()
            if 'comment' in self.required_actions:
                if self.has_current_user_comment(bro):
                    mylogger.info("当前账号已评论，跳过评论")
                else:
                    self.commit_comment(bro, chains, comment_content)
                random_sleep()
            if 'forward' in self.required_actions:
                if self.is_already_forwarded(bro):
                    mylogger.info("当前账号已转发，跳过转发")
                else:
                    self.click_share(bro, chains, share_content)
                random_sleep()
            # 回填状态
            self.share_status = 0
            self.status = 0
        except Exception as e:
            self.last_error = str(e)
            self.share_status = 3
            self.status = 3
            try:
                body_text = bro.find_element(By.TAG_NAME, "body").text or ""
                title = bro.title or ""
                evidence = self.get_risk_control_evidence(title + "\n" + body_text)
                if evidence:
                    self.last_error += " B站安全风控（错误号 412，特征：" + evidence + "）"
            except Exception:
                pass
            mylogger.error("share_one 转发单条动态主流程 出错url : " + lucky_dynamic_url)
            mylogger.error("[share_one 出错原因为：%s]" % e, exc_info=True)
        finally:
            self.share_time = str(datetime.now())
            self.machine_ip = get_host_ip()
            mylogger.info('单条动态转发--执行结束')

    @staticmethod
    def _coerce_datetime(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            timestamp = int(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                return None
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp, SHANGHAI_TZ).replace(tzinfo=None)
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _notice_datetime(value):
        if value is None:
            return None
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return None
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        try:
            return str(datetime.fromtimestamp(timestamp, SHANGHAI_TZ).replace(tzinfo=None))
        except (OSError, OverflowError, ValueError):
            return None

    RESERVE_RID_PATTERN = re.compile(r'["\']rid["\']\s*:\s*["\']?(\d+)')

    DETAIL_SCRIPT = r'''
const endpoint = arguments[0];
const dynamicId = arguments[1];
const features = arguments[2];
const done = arguments[3];
let finished = false;
let timer = null;
const finish = (payload) => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  done(payload);
};
timer = setTimeout(() => finish({timeout: true}), 15000);
const controller = new AbortController();
const abortTimer = setTimeout(() => controller.abort(), 12000);
let target = endpoint;
if (features) {
  target += '?id=' + encodeURIComponent(dynamicId)
    + '&features=' + encodeURIComponent(features);
} else {
  target += '?id=' + encodeURIComponent(dynamicId);
}
fetch(target, {credentials: 'include', signal: controller.signal})
  .then(response => response.json())
  .then(payload => { clearTimeout(abortTimer); finish(payload); })
  .catch(error => { clearTimeout(abortTimer); finish({error: String(error)}); });
'''

    def _request_detail_endpoint(
        self,
        bro,
        dynamic_id,
        endpoint,
        features='',
        reason_prefix='detail',
    ):
        try:
            result = bro.execute_async_script(
                self.DETAIL_SCRIPT, endpoint, dynamic_id, features
            )
        except Exception:
            self.last_api_reason = '%s_request_error' % reason_prefix
            return None
        if not isinstance(result, dict):
            self.last_api_reason = '%s_invalid_response' % reason_prefix
            return None
        if result.get('timeout'):
            self.last_api_reason = '%s_timeout' % reason_prefix
            return None
        if result.get('error'):
            self.last_api_reason = '%s_invalid_response' % reason_prefix
            return None
        code = result.get('code')
        if code not in (None, 0):
            self.last_api_reason = 'api_code:%s' % code
            return None
        item = ((result.get('data') or {}).get('item') or None)
        if item is None:
            self.last_api_reason = '%s_empty_detail' % reason_prefix
        return item

    def _fetch_dynamic_detail_item(self, bro, lucky_dynamic_url):
        match = re.search(r'/(\d+)(?:\?|$)', str(lucky_dynamic_url))
        if not match:
            self.last_api_reason = 'invalid_dynamic_url'
            return None
        try:
            random_sleep(
                start=self.detail_request_min_seconds,
                end=self.detail_request_max_seconds,
            )
        except Exception:
            # 测试或本地调用中若没有可用的休眠实现，不能阻断校验。
            pass
        self.last_api_reason = 'detail_unavailable'
        dynamic_id = match.group(1)
        # B站对跨站 API 请求有来源风控：从 api.bilibili.com 页面直接
        # fetch polymer detail 会稳定返回 -352，而从 www 页面发起正常。
        try:
            current_host = urlparse(bro.current_url).hostname or ''
        except Exception:
            current_host = ''
        if not (current_host == 'www.bilibili.com'):
            bro.get('https://www.bilibili.com/')
        item = self._request_detail_endpoint(
            bro,
            dynamic_id,
            'https://api.bilibili.com/x/polymer/web-dynamic/v1/detail',
        )
        # Keep the primary item for official components even if its body is
        # empty; otherwise opus fallback is still needed to recover plain text.
        if item is not None and (
            self._dynamic_item_text(item).strip()
            or self._has_official_lottery_component(item)
        ):
            return item

        # 新接口在限流/部分动态上可能返回 HTML 或 -9999；
        # 旧版 opus detail 是独立入口，可作为一次无风险回退。
        try:
            random_sleep(
                start=self.detail_request_min_seconds,
                end=self.detail_request_max_seconds,
            )
        except Exception:
            pass
        fallback = self._request_detail_endpoint(
            bro,
            dynamic_id,
            'https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail',
            features='htmlNewStyle,ugcDelete,editable,opusPrivateVisible',
            reason_prefix='opus_detail',
        )
        if fallback is not None:
            return self._normalize_dynamic_item(fallback)
        return None

    @staticmethod
    def _normalize_dynamic_item(item):
        """将 opus/detail 的 modules 列表统一为 dynamic/detail 的字典结构。"""
        if not isinstance(item, dict):
            return item
        modules = item.get('modules')
        if not isinstance(modules, list):
            return item
        normalized_modules = {}
        content_summary = {}
        for module in modules:
            if not isinstance(module, dict):
                continue
            for key in ('module_stat', 'module_author', 'module_dynamic', 'module_title'):
                if module.get(key):
                    normalized_modules[key] = module[key]
            content = module.get('module_content')
            if isinstance(content, dict):
                summary = content.get('summary') or {}
                paragraphs = content.get('paragraphs') or []
                content_summary = dict(summary) if isinstance(summary, dict) else {}
                if paragraphs:
                    content_summary['paragraphs'] = paragraphs
        if 'module_dynamic' not in normalized_modules and content_summary:
            normalized_modules['module_dynamic'] = {'desc': content_summary}
        title = normalized_modules.get('module_title') or {}
        if title.get('text'):
            normalized_modules.setdefault('module_dynamic', {})['title'] = str(title['text'])
        normalized = dict(item)
        normalized['modules'] = normalized_modules
        return normalized

    @classmethod
    def _dynamic_item_text(cls, item):
        if not isinstance(item, dict):
            return ''
        modules = item.get('modules') or {}
        if isinstance(modules, list):
            module_dynamic = {}
            for module in modules:
                if not isinstance(module, dict):
                    continue
                if module.get('module_dynamic'):
                    module_dynamic = module.get('module_dynamic') or {}
                    break
                content = module.get('module_content') or {}
                if content:
                    module_dynamic = {'desc': content.get('summary') or content}
                    break
        else:
            module_dynamic = modules.get('module_dynamic') or {}
        desc = module_dynamic.get('desc') or {}
        nodes = desc.get('rich_text_nodes') or []
        text = ' '.join(
            str(node.get('text') or '')
            for node in nodes
            if isinstance(node, dict)
        )
        if text.strip():
            return text
        opus_parts = []
        for paragraph in desc.get('paragraphs') or []:
            paragraph_text = ((paragraph.get('text') or {}).get('nodes') or [])
            for node in paragraph_text:
                if not isinstance(node, dict):
                    continue
                word = node.get('word') or {}
                value = word.get('words') or node.get('text')
                if value:
                    opus_parts.append(str(value))
        if opus_parts:
            return ' '.join(opus_parts)
        title = (modules.get('module_title') or {}).get('text') if isinstance(modules, dict) else ''
        return ' '.join(part for part in (title, desc.get('text') or '') if part)

    @classmethod
    def classify_lottery_item(cls, item):
        """Classify an item by explicit giveaway evidence, not keywords alone."""
        text = cls._dynamic_item_text(item)
        normalized = re.sub(r'\s+', '', text)
        result = {
            'confirmed': False,
            'kind': 'not_lottery',
            'action': None,
            'reason': '',
            'text': text,
        }
        if not normalized:
            return result

        result_words = (
            '中奖名单', '获奖名单', '开奖名单', '抽奖结果', '开奖结果',
            '恭喜获奖', '获奖公告', '已开奖', '中奖公布',
        )
        if any(word in normalized for word in result_words):
            result.update(kind='result_or_notice', reason='result_announcement')
            return result

        context_words = (
            '抽奖合集', '合集更新', '上期传送门', '本期完', '往期',
            '关注我', '求关注', '广告', '购买', '商城', '优惠券',
        )
        if any(word in normalized for word in context_words):
            result.update(kind='context_or_ad', reason='collection_or_ad')
            return result

        giveaway_words = ('抽', '揪', '送', '赠', '福利', '奖品', '有奖')
        prize_words = ('元', '礼包', '奖品', '名额', '红包', '礼物', '门票', '周边', '卡', '券', '位')
        action_text = normalized.replace('&', '').replace('＋', '').replace('+', '')
        action_words = ('关注', '转发', '评论', '点赞', '预约', '参与', '@')
        action_aliases = (
            '转赞评', '关转赞评', '关转评', '转评', '评论抽',
            '评论区抽', '关注转发评论', '关转评',
        )
        # “送出机械臂”这类奖品词可能本身不含“礼包/元/卡”等常见词，抽奖语义已足够。
        date_words = (
            '开奖', '截止', '日期', '月', '日', '时间',
            '下周五', '本周五', '周五', '下周一', '本周一',
        )
        has_numeric_date = bool(re.search(r'\d{1,2}[./月-]\d{1,2}', normalized))
        if not any(word in normalized for word in giveaway_words):
            return result
        if not any(word in normalized for word in prize_words):
            # 真实案例常见“送出机械臂/同款产品”等具体物品名；
            # 有明确抽奖动作和时间时，不因奖品词表缺失就丢弃。
            if not (
                any(alias in action_text for alias in action_aliases)
                and any(word in normalized for word in date_words)
            ):
                result.update(kind='suspected_giveaway', reason='missing_prize')
                return result
        if (
            not any(word in action_text for word in action_words)
            and not any(alias in action_text for alias in action_aliases)
        ):
            result.update(kind='suspected_giveaway', reason='missing_action')
            return result
        if not any(word in normalized for word in date_words) and not has_numeric_date:
            result.update(kind='suspected_giveaway', reason='missing_concrete_time')
            return result

        has_forward = (
            '转发' in normalized
            or '转赞评' in action_text
            or '转评' in action_text
            or '关转评' in action_text
            or '关&转' in normalized
        )
        has_comment = '评论' in normalized or '留言' in normalized or '评' in action_text
        has_follow = '关注' in normalized or '关' in action_text
        has_like = '点赞' in normalized or '赞' in action_text
        has_reserve = '预约' in normalized
        if has_reserve:
            action = 'reserve'
        elif has_forward:
            action = 'forward'
        elif has_comment:
            action = 'comment'
        elif has_follow:
            action = 'follow'
        else:
            action = 'forward'
        required_actions = []
        if has_follow:
            required_actions.append('follow')
        if has_comment:
            required_actions.append('comment')
        if has_like:
            required_actions.append('like')
        if has_forward:
            required_actions.append('forward')
        if has_reserve:
            required_actions.append('reserve')
        result.update(
            confirmed=True,
            kind='plain_text_lottery',
            action=action,
            required_actions=required_actions,
            reason='verified_text_rules',
        )
        return result

    @staticmethod
    def _has_official_lottery_component(item):
        if not isinstance(item, dict):
            return False
        # Walk the actual JSON structure instead of stringifying it.  The old
        # serialized check could match unrelated text and gave false positives.
        def walk(value, depth=0):
            if depth > 8:
                return False
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(key, str) and key.lower() in (
                        'rich_text_node_type_lottery',
                        'additional_type_lottery',
                        'additional_type_reserve',
                    ):
                        return True
                    if isinstance(child, str):
                        lowered = child.lower()
                        if (
                            'rich_text_node_type_lottery' in lowered
                            or 'additional_type_lottery' in lowered
                            or 'additional_type_reserve' in lowered
                            or 'lottery/result?' in lowered
                        ):
                            return True
                    if walk(child, depth + 1):
                        return True
            elif isinstance(value, list):
                for child in value:
                    if walk(child, depth + 1):
                        return True
            return False
        if walk(item):
            return True
        try:
            if DynamicShareBase._extract_reserve_rid(item):
                return True
        except Exception:
            pass
        return False

    @classmethod
    def _extract_reserve_rid(cls, item):
        if not isinstance(item, dict):
            return None
        modules = item.get('modules') or {}
        if isinstance(modules, list):
            module_dynamic = {}
            for module in modules:
                if isinstance(module, dict) and module.get('module_dynamic'):
                    module_dynamic = module.get('module_dynamic') or {}
                    break
        else:
            module_dynamic = modules.get('module_dynamic') or {}
        additional = module_dynamic.get('additional') or {}
        reserve = additional.get('reserve') or {}
        rid = reserve.get('rid')
        if rid:
            return str(rid)
        try:
            serialized = str(item)
        except Exception:
            return None
        match = cls.RESERVE_RID_PATTERN.search(serialized)
        return match.group(1) if match else None

    @staticmethod
    def _notice_endpoint(business_id, business_type):
        return (
            DynamicShareBase.LOTTERY_NOTICE_API_TEMPLATE
            % (business_type, business_id)
        )

    @staticmethod
    def _parse_plain_text_deadline(text, now=None):
        """Parse an explicit giveaway deadline from plain text as Shanghai time."""
        if not text:
            return None
        normalized = str(text).replace('：', ':').replace('．', '.')
        current = now or datetime.now(SHANGHAI_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI_TZ)
        # Prefer a date near the opening / draw wording, and require a time or
        # an explicit "开奖" marker so unrelated dates are not used.
        patterns = (
            r'(?:(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?)\s*(?P<hour>\d{1,2})[:点](?P<minute>\d{2})?',
            r'(?P<month>\d{1,2})[/.-](?P<day>\d{1,2})\s*(?P<hour>\d{1,2})[:点](?P<minute>\d{2})?',
            r'(?:(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?)(?=[^\n]{0,60}(?:开奖|抽取|截止|揪\d|抽\d|[0-9]+个))',
            r'(?:(?P<month>\d{1,2})[/.-](?P<day>\d{1,2}))(?=[^\n]{0,40}(?:开奖|抽取|截止|抱走|抽\d|[0-9]+个))',
        )
        for pattern in patterns:
            for match in re.finditer(pattern, normalized):
                try:
                    month = int(match.group('month'))
                    day = int(match.group('day'))
                    hour = int(match.group('hour')) if match.groupdict().get('hour') else 23
                    minute = int(match.group('minute') or 0) if match.groupdict().get('minute') else 59
                    year = current.year
                    deadline = datetime(year, month, day, hour, minute, tzinfo=SHANGHAI_TZ)
                    if deadline < current - timedelta(days=30):
                        deadline = deadline.replace(year=year + 1)
                    return str(deadline.replace(tzinfo=None))
                except (TypeError, ValueError, OverflowError):
                    continue
        return None

    def fetch_official_lottery_notice(self, bro, lucky_dynamic_url, retries=3):
        """返回结构化官方抽奖校验结果，不执行任何社交动作。"""
        match = re.search(r'/(\d+)(?:\?|$)', str(lucky_dynamic_url))
        if not match:
            return {
                'confirmed': False,
                'retryable': False,
                'reason': 'invalid_dynamic_url',
            }

        dynamic_id = match.group(1)
        # 互动抽奖固定 business_type=1；预约抽奖需要先解析 reserve.rid，
        # 再用 business_type=10 查询。不能把这两种查询混成一个参数。
        item = self._fetch_dynamic_detail_item(bro, lucky_dynamic_url)
        if item is None:
            return {
                'confirmed': False,
                'retryable': True,
                'reason': self.last_api_reason or 'detail_unavailable',
            }
        classification = self.classify_lottery_item(item)
        has_official_component = self._has_official_lottery_component(item)
        if isinstance(item, dict) and not has_official_component:
            if classification.get('confirmed'):
                self.lottery_source = 'plain_text'
                plain_text_deadline = self._parse_plain_text_deadline(
                    classification.get('text') or ''
                )
                return {
                    'confirmed': True,
                    'retryable': False,
                    'reason': '',
                    'source': 'plain_text',
                    'lottery_rule': classification,
                    'lottery_id': '',
                    'lottery_end_time': plain_text_deadline,
                    'status': None,
                }
            # 详情里没有官方组件时，不能仅凭关键词判断不是抽奖；
            # 仍保留一次接口查询，避免把普通互动抽奖或接口隐藏组件漏掉。
        reserve_rid = self._extract_reserve_rid(item)
        # 互动抽奖先查 business_type=1；预约抽奖必须使用 reserve.rid。
        # 不应在详情接口失败时用动态 ID 冒充预约业务 ID 重复查询。
        requests = [(1, dynamic_id)]
        if reserve_rid:
            requests.append((10, reserve_rid))

        last_reason = 'empty_response'
        attempts = max(1, int(retries))
        for candidate_type, candidate_id in requests:
            endpoint = self._notice_endpoint(candidate_id, candidate_type)
            for attempt in range(attempts):
                result = bro.execute_async_script(self.LOTTERY_NOTICE_SCRIPT, endpoint)
                if not isinstance(result, dict):
                    last_reason = 'invalid_response'
                elif result.get('timeout'):
                    last_reason = 'timeout'
                    break
                elif result.get('error'):
                    last_reason = 'request_error'
                    break
                else:
                    code = result.get('code')
                    if code == 0 and result.get('lottery_id'):
                        self.lottery_source = 'official_notice'
                        self.notice_lottery_time = self._coerce_datetime(
                            result.get('lottery_time')
                        )
                        return {
                            'confirmed': True,
                            'retryable': False,
                            'reason': '',
                            'lottery_id': str(result.get('lottery_id')),
                            'lottery_business_type': candidate_type,
                            'lottery_business_id': candidate_id,
                            'lottery_end_time': self._notice_datetime(
                                result.get('lottery_time')
                            ),
                            'status': result.get('status'),
                        }
                    if code == 0:
                        # code=0 但没有 lottery_id 不足以证明“不是抽奖”；
                        # 官方接口可能正常返回空 data，保留为未知并继续尝试其它业务类型。
                        last_reason = 'code0_without_lottery_id'
                        break
                    last_reason = 'api_code:%s' % code
                    # -9999/429/412 等系统性错误重试会继续放大限流。
                    # 交给上层批级冷却，下一轮再验证该条。
                    if code in (-9999, -429, 429, -412, 412, -352, -509):
                        break
                if attempt < attempts - 1:
                    if last_reason in (
                        'api_code:-9999', 'api_code:-429', 'api_code:429',
                        'api_code:-412', 'api_code:412',
                        'api_code:-352', 'api_code:-509',
                    ):
                        break
                    time.sleep(0.4 * (attempt + 1))
            if last_reason == 'code0_without_lottery_id':
                continue
        return {
            'confirmed': False,
            'retryable': True,
            'reason': last_reason,
        }

    def verify_official_lottery(self, bro, lucky_dynamic_url):
        """只有查到官方互动抽奖 lottery_id 才允许执行关注/评论/转发。"""
        result = self.fetch_official_lottery_notice(bro, lucky_dynamic_url)
        if result.get('confirmed'):
            return True, ''
        return False, result.get('reason') or 'not_confirmed'

    @staticmethod
    def get_risk_control_evidence(text):
        normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        patterns = (
            ("错误号: 412", r"错误号\s*[:：]\s*412"),
            ("错误号 412", r"错误号\s+412"),
            ("安全风控策略", r"触发哔哩哔哩安全风控策略"),
            ("请求被拒绝", r"该次访问请求被拒绝"),
            ("security control policy", r"security control policy"),
            ("request was rejected", r"request was rejected"),
        )
        for label, pattern in patterns:
            if re.search(pattern, normalized):
                return label
        return None

    def wait_page_ready(self, bro, timeout=20):
        WebDriverWait(bro, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'body')))
        WebDriverWait(bro, timeout).until(lambda driver: self.is_dynamic_page_ready(driver))

    def is_dynamic_page_ready(self, bro):
        if self.is_bili_error_page(bro):
            raise Exception("B站错误页：" + str(bro.current_url))
        return len(bro.find_elements(By.CSS_SELECTOR, self.PAGE_READY_CSS)) > 0

    def is_bili_error_page(self, bro):
        title = bro.title or ''
        if '出错啦' in title:
            return True
        try:
            body_text = bro.find_element(By.TAG_NAME, 'body').text or ''
        except Exception:
            return False
        return '返回上一页' in body_text and '换一张' in body_text

    def wait_css(self, bro, selector, timeout=10):
        return WebDriverWait(bro, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))

    def wait_clickable_css(self, bro, selector, timeout=10):
        return WebDriverWait(bro, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))

    def click_by_js(self, bro, ele):
        bro.execute_script("arguments[0].scrollIntoView({block:'center'});", ele)
        bro.execute_script("arguments[0].click();", ele)

    def click_first_by_text(self, bro, texts, timeout=3):
        xpath = "//*[self::button or self::div or self::span or self::a][" + " or ".join(
            ["contains(normalize-space(.), '%s')" % text for text in texts]
        ) + "]"
        ele = WebDriverWait(bro, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        self.click_by_js(bro, ele)
        return ele

    def get_visible_elements(self, bro, selector):
        return [ele for ele in bro.find_elements(By.CSS_SELECTOR, selector) if ele.is_displayed()]

    def get_up_info(self, bro):
        self.upId = None
        self.upUrl = None
        self.upIds = []
        self.upUrls = []
        up_id = self.get_up_id_from_initial_state(bro)
        if up_id:
            self.add_up_id(up_id)
        else:
            up_id = self.get_up_id_from_dynamic_api(bro)
            if up_id:
                self.add_up_id(up_id)
        for link in self.get_visible_elements(bro, self.UP_AT_CSS):
            up_id = (link.get_attribute('data-oid') or '').strip()
            if up_id.isdigit():
                self.add_up_id(up_id)
        if self.upIds:
            self.upId = self.upIds[0]
            self.upUrl = self.upUrls[0]
            return
        raise Exception("未能从动态标题解析UP主UID")

    def add_up_id(self, up_id):
        up_id = str(up_id)
        if up_id.isdigit() and up_id not in self.upIds:
            self.upIds.append(up_id)
            self.upUrls.append('https://space.bilibili.com/' + up_id)

    def get_up_id_from_initial_state(self, bro):
        try:
            up_id = bro.execute_script("""
const state = window.__INITIAL_STATE__ || {};
const basicUid = state.detail && state.detail.basic && state.detail.basic.uid;
if (basicUid) return String(basicUid);
const modules = state.detail && Array.isArray(state.detail.modules) ? state.detail.modules : [];
for (const module of modules) {
  const author = module.module_author || {};
  const mid = author.mid || author.uid;
  if (mid) return String(mid);
}
return null;
""")
            if up_id and str(up_id).isdigit():
                return str(up_id)
        except Exception:
            return None
        return None

    def get_up_id_from_dynamic_api(self, bro):
        dynamic_id = self.get_dynamic_id_from_url(bro.current_url)
        if not dynamic_id:
            return None
        try:
            up_id = bro.execute_async_script("""
const dynamicId = arguments[0];
const done = arguments[1];
let finished = false;
let timer = null;
const finish = (payload) => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  done(payload);
};
timer = setTimeout(() => finish(null), 12000);
const controller = new AbortController();
const abortTimer = setTimeout(() => controller.abort(), 9000);
fetch('https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id=' + encodeURIComponent(dynamicId), {credentials: 'include', signal: controller.signal})
  .then(response => response.json())
  .then(data => {
    clearTimeout(abortTimer);
    const item = data.data && data.data.item;
    const modules = item && item.modules;
    let author = modules && modules.module_author;
    if (!author && Array.isArray(modules)) {
      const mod = modules.find((entry) => entry && entry.module_author);
      author = mod && mod.module_author;
    }
    const mid = author && (author.mid || author.uid);
    finish(mid ? String(mid) : null);
  })
  .catch(() => { clearTimeout(abortTimer); finish(null); });
""", dynamic_id)
            if up_id and str(up_id).isdigit():
                return str(up_id)
        except Exception:
            return None
        return None

    def get_dynamic_id_from_url(self, url):
        path = urlparse(url).path.strip('/')
        match = re.search(r'(\d+)$', path)
        if match:
            return match.group(1)
        return None

    def get_dynamic_author_name(self, bro):
        for ele in self.get_visible_elements(bro, self.UP_TITLE_CSS):
            text = self.get_element_text(ele).replace('\u200b', '').strip()
            if text:
                return text
        raise Exception("未能从动态标题获取UP主名称")

    def parse_up_id(self, href):
        if href is None:
            return None
        path = urlparse(href).path.strip('/')
        if path.isdigit():
            return path
        return None

    def is_done_text(self, ele, done_words):
        text = (ele.get_attribute('innerText') or ele.text or '').strip()
        cls = ele.get_attribute('class') or ''
        return any(word in text for word in done_words) or any(word in cls for word in done_words)

    def has_text(self, ele, words):
        text = (ele.get_attribute('innerText') or ele.text or '').strip()
        return any(word in text for word in words)

    @staticmethod
    def _is_expired_deadline(value, now=None):
        if value is None:
            return False
        if isinstance(value, datetime):
            deadline = value
        else:
            try:
                deadline = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=SHANGHAI_TZ)
        current = now or datetime.now(SHANGHAI_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI_TZ)
        return current.timestamp() >= deadline.timestamp()

    def is_expired_dynamic(self, bro):
        if self.lottery_time is None:
            return False
        self.expire_date = str(self.lottery_time)
        return self._is_expired_deadline(self.lottery_time)

    def resolve_lottery_time(self, bro):
        metadata = self.get_dynamic_time_metadata(bro)
        publish_ts = metadata.get('publish_ts')
        lottery_end_ts = metadata.get('lottery_end_ts')

        if publish_ts is not None:
            self.publish_time = datetime.fromtimestamp(publish_ts)

        candidates = []
        if lottery_end_ts is not None:
            candidate = datetime.fromtimestamp(lottery_end_ts)
            if self.publish_time is None or candidate > self.publish_time:
                candidates.append(candidate)
            elif candidate <= self.publish_time:
                mylogger.warning(
                    "开奖接口时间不晚于动态发布时间，忽略接口时间：开奖=%s，发布=%s",
                    candidate, self.publish_time
                )

        text_date = self.parse_expire_date(
            self.get_dynamic_text(bro),
            self.publish_time
        )
        if text_date is not None:
            candidates.append(text_date)

        if candidates:
            self.lottery_time = max(candidates)
            self.lottery_source = 'explicit'
            return

        if self.publish_time is not None:
            self.lottery_time = self.publish_time + timedelta(days=120)
            self.lottery_source = 'fallback_120d'
            mylogger.info("未找到明确开奖时间，使用动态发布时间+120天：%s" % self.lottery_time)
        else:
            mylogger.warning("未获取到动态发布时间，无法计算120天保底时间")

    def get_dynamic_time_metadata(self, bro):
        dynamic_id = self.get_dynamic_id_from_url(bro.current_url)
        if not dynamic_id:
            return {}
        try:
            result = bro.execute_async_script("""
const dynamicId = arguments[0];
const done = arguments[1];
const timeKeys = [
  'lottery_time', 'lottery_end_time', 'end_time', 'end_ts',
  'draw_time', 'award_time', 'deadline'
];

function toTimestamp(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 100000000000 ? Math.floor(value / 1000) : Math.floor(value);
  }
  if (typeof value !== 'string' || value.trim() === '') return null;
  const numberValue = Number(value);
  if (Number.isFinite(numberValue)) {
    return numberValue > 100000000000 ? Math.floor(numberValue / 1000) : Math.floor(numberValue);
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

function findLotteryTimestamp(value, visited) {
  if (!value || typeof value !== 'object' || visited.has(value)) return null;
  visited.add(value);
  for (const key of timeKeys) {
    if (value[key] != null) {
      const timestamp = toTimestamp(value[key]);
      if (timestamp != null) return timestamp;
    }
  }
  for (const key of Object.keys(value)) {
    const timestamp = findLotteryTimestamp(value[key], visited);
    if (timestamp != null) return timestamp;
  }
  return null;
}

Promise.all([
  fetch('https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id='
    + encodeURIComponent(dynamicId), {credentials: 'include'})
    .then(response => response.json())
    .catch(() => ({})),
  fetch('https://api.vc.bilibili.com/lottery_svr/v1/lottery_svr/lottery_notice'
    + '?business_type=4&business_id=' + encodeURIComponent(dynamicId),
    {credentials: 'include'})
    .then(response => response.json())
    .catch(() => ({}))
]).then(results => {
  const detail = results[0] || {};
  const lottery = results[1] || {};
  const item = detail.data && (detail.data.item || detail.data);
  const author = item && item.modules && item.modules.module_author;
  const publishTs = author && Number(author.pub_ts);
  const lotteryData = lottery.data || {};
  const lotteryEndTs = findLotteryTimestamp(lotteryData, new Set());
  done({
    publish_ts: Number.isFinite(publishTs) && publishTs > 0 ? publishTs : null,
    lottery_end_ts: lotteryEndTs
  });
}).catch(() => done({}));
""", dynamic_id)
            return result if isinstance(result, dict) else {}
        except Exception as e:
            mylogger.warning("获取动态发布时间/开奖时间失败：%s" % e)
            return {}

    def get_dynamic_text(self, bro):
        texts = []
        for ele in self.get_visible_elements(bro, self.DYNAMIC_TEXT_CSS):
            text = (ele.get_attribute('innerText') or ele.text or '').strip()
            if text:
                texts.append(text)
        if len(texts) == 0:
            body = bro.find_element(By.TAG_NAME, 'body')
            texts.append((body.get_attribute('innerText') or body.text or '').strip())
        return "\n".join(texts)

    def parse_expire_date(self, text, publish_time=None):
        primary_keyword_pattern = (
            r'(开奖|开獎|抽奖结果|抽獎結果|中奖名单|中獎名單|公布名单|公布名單|'
            r'公布结果|公布結果)'
        )
        secondary_keyword_pattern = (
            r'(截止|截至|报名截止|報名截止|参与截止|參與截止|'
            r'结束时间|結束時間|结束|結束)'
        )
        giveaway_keyword_pattern = r'(揪|抽取|抽奖|抽中|抽到|幸运粉丝|幸运观众)'
        keyword_candidates = []
        secondary_candidates = []
        all_dates = []
        current_year = publish_time.year if publish_time is not None else date.today().year
        date_pattern = r'((?:20\d{2})年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?'
        for match in re.finditer(date_pattern, text):
            year = int(match.group(1)[:-1]) if match.group(1) else current_year
            parsed = self.parse_date_time_nearby(
                text, match.start(), match.end(), year,
                int(match.group(2)), int(match.group(3))
            )
            if parsed is not None:
                all_dates.append(parsed)
        for match in re.finditer(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', text):
            parsed = self.parse_date_time_nearby(
                text, match.start(), match.end(), current_year,
                int(match.group(1)), int(match.group(2))
            )
            if parsed is not None:
                all_dates.append(parsed)
        for match in re.finditer(r'(\d{1,2})\s*月\s*(?:初|上旬?|中旬?|底|末)', text):
            month = int(match.group(1))
            year = current_year
            try:
                vague_date = date(year + (1 if month == 12 else 0), month % 12 + 1, 1)
                all_dates.append(datetime.combine(vague_date, datetime_time.min))
            except ValueError:
                pass
        chinese_months = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
            '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12,
        }
        for match in re.finditer(r'([一二三四五六七八九十]+)\s*月\s*(?:初|上旬?|中旬?|底|末)', text):
            month = chinese_months.get(match.group(1))
            if month is None:
                continue
            year = current_year + (1 if month == 12 else 0)
            vague_date = date(year, month % 12 + 1, 1)
            all_dates.append(datetime.combine(vague_date, datetime_time.min))

        all_dates = self.filter_lottery_candidates(all_dates, publish_time)
        keyword_matches = []
        keyword_matches.extend(
            (match, True)
            for match in re.finditer(primary_keyword_pattern, text, re.IGNORECASE)
        )
        keyword_matches.extend(
            (match, False)
            for match in re.finditer(secondary_keyword_pattern, text, re.IGNORECASE)
        )
        for keyword, is_primary in keyword_matches:
            start = max(0, keyword.start() - 40)
            end = min(len(text), keyword.end() + 60)
            context = text[start:end]
            preceding_candidates = []
            following_candidates = []
            for match in re.finditer(date_pattern, context):
                year = int(match.group(1)[:-1]) if match.group(1) else current_year
                parsed = self.parse_date_time_nearby(
                    context, match.start(), match.end(), year,
                    int(match.group(2)), int(match.group(3))
                )
                if parsed is not None:
                    absolute_start = start + match.start()
                    target = preceding_candidates if absolute_start < keyword.start() else following_candidates
                    target.append((abs(absolute_start - keyword.start()), parsed))
            for match in re.finditer(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', context):
                parsed = self.parse_date_time_nearby(
                    context, match.start(), match.end(), current_year,
                    int(match.group(1)), int(match.group(2))
                )
                if parsed is not None:
                    absolute_start = start + match.start()
                    target = preceding_candidates if absolute_start < keyword.start() else following_candidates
                    target.append((abs(absolute_start - keyword.start()), parsed))
            for match in re.finditer(r'(\d{1,2})\s*月\s*(?:初|上旬?|中旬?|底|末)', context):
                month = int(match.group(1))
                year = current_year + (1 if month == 12 else 0)
                vague_date = date(year, month % 12 + 1, 1)
                absolute_start = start + match.start()
                target = preceding_candidates if absolute_start < keyword.start() else following_candidates
                target.append((
                    abs((start + match.start()) - keyword.start()),
                    datetime.combine(vague_date, datetime_time.min)
                ))
            for match in re.finditer(r'([一二三四五六七八九十]+)\s*月\s*(?:初|上旬?|中旬?|底|末)', context):
                month = chinese_months.get(match.group(1))
                if month is None:
                    continue
                year = current_year + (1 if month == 12 else 0)
                vague_date = date(year, month % 12 + 1, 1)
                absolute_start = start + match.start()
                target = preceding_candidates if absolute_start < keyword.start() else following_candidates
                target.append((
                    abs((start + match.start()) - keyword.start()),
                    datetime.combine(vague_date, datetime_time.min)
                ))
            context_candidates = preceding_candidates or following_candidates
            if context_candidates:
                target = min(context_candidates, key=lambda item: item[0])[1]
                (keyword_candidates if is_primary else secondary_candidates).append(target)
        keyword_candidates = self.filter_lottery_candidates(
            keyword_candidates, publish_time
        )
        if keyword_candidates:
            return max(keyword_candidates)
        if secondary_candidates:
            secondary_candidates = self.filter_lottery_candidates(
                secondary_candidates, publish_time
            )
            if secondary_candidates:
                return max(secondary_candidates)
        giveaway_matches = list(re.finditer(
            giveaway_keyword_pattern, text, re.IGNORECASE
        ))
        if giveaway_matches and all_dates:
            return max(all_dates)
        # 没有任何开奖语义时，普通日期可能是活动开始、发货或其他时间，
        # 不能直接当作开奖时间；有开奖语义但日期不在关键词附近时，才允许全局兜底。
        if keyword_matches and all_dates:
            return max(all_dates)
        return None

    @staticmethod
    def filter_lottery_candidates(candidates, publish_time):
        if publish_time is None:
            return candidates
        return [candidate for candidate in candidates if candidate > publish_time]

    @staticmethod
    def parse_date_time_nearby(text, start, end, year, month, day):
        try:
            base = date(year, month, day)
        except ValueError:
            return None
        nearby = text[end:min(len(text), end + 20)]
        time_match = re.search(
            r'(上午|下午|晚上|凌晨|中午)?\s*(\d{1,2})(?::(\d{2}))?\s*点半?',
            nearby
        )
        if not time_match:
            time_match = re.search(
                r'(上午|下午|晚上|凌晨|中午)?\s*(\d{1,2}):(\d{2})',
                nearby
            )
        if not time_match:
            return datetime.combine(base, datetime_time(23, 59, 59))
        period = time_match.group(1) or ''
        hour = int(time_match.group(2))
        minute = int(time_match.group(3) or 0)
        if '下午' in period or '晚上' in period:
            if hour < 12:
                hour += 12
        elif '凌晨' in period and hour == 12:
            hour = 0
        if '半' in time_match.group(0):
            minute = 30
        if hour > 23 or minute > 59:
            return None
        return datetime.combine(base, datetime_time(hour, minute, 0))

    def append_valid_date(self, dates, year, month, day):
        try:
            dates.append(date(year, month, day))
        except ValueError:
            return

    def ensure_publish_cookie(self, bro):
        names = [cookie.get('name') for cookie in bro.get_cookies()]
        if 'bili_jct' not in names:
            raise Exception("缺少 bili_jct Cookie，无法提交评论/转发，已停止执行防止假成功")

    def get_current_user_id(self, bro):
        for avatar in self.get_visible_elements(
                bro, 'a.header-entry-mini, .header-entry-avatar, .right-entry-avatar'):
            href = avatar.get_attribute('href')
            if not href:
                try:
                    href = avatar.find_element(By.XPATH, './ancestor-or-self::a[1]').get_attribute('href')
                except Exception:
                    href = None
            user_id = self.parse_up_id(href)
            if user_id:
                return user_id
        return None

    def get_current_user_name(self, bro):
        if self.current_user_name:
            return self.current_user_name
        try:
            result = bro.execute_async_script("""
const done = arguments[0];
fetch('https://api.bilibili.com/x/web-interface/nav', {credentials: 'include'})
  .then(response => response.json())
  .then(data => done(data.data || {}))
  .catch(() => done({}));
""")
            self.current_user_name = result.get('uname')
        except Exception:
            self.current_user_name = None
        return self.current_user_name

    def has_current_user_comment(self, bro):
        user_name = self.get_current_user_name(bro)
        if not user_name:
            return False
        bro.execute_script("""
const comments = document.querySelector('.comment-wrap, .bili-comment-container, bili-comments');
if (comments) comments.scrollIntoView({block: 'center'});
""")
        body = bro.find_element(By.TAG_NAME, 'body').text or ''
        editor_marker = body.rfind('请输入内容')
        if editor_marker >= 0:
            body = body[:editor_marker]
        comment_marker = re.search(r'评论\s*[\d.万]+', body)
        if comment_marker:
            body = body[comment_marker.start():]
        return any(line.strip() == user_name for line in body.splitlines())

    def is_already_forwarded(self, bro):
        buttons = self.get_visible_elements(bro, self.FORWARD_CSS)
        for button in buttons:
            text = (button.get_attribute('innerText') or button.text or '').strip()
            cls = (button.get_attribute('class') or '').lower()
            aria_pressed = button.get_attribute('aria-pressed')
            if (
                any(word in text for word in ["已转发", "已分享"])
                or "forwarded" in cls
                or "active" in cls
                or aria_pressed == "true"
            ):
                return True
        return False

    def click_follow_all(self, bro, chains):
        dynamic_url = bro.current_url
        self.get_up_info(bro)
        for up_id, up_url in zip(self.upIds, self.upUrls):
            self.upId = up_id
            self.upUrl = up_url
            self.click_follow_one(bro, chains, dynamic_url)
        self.upId = self.upIds[0]
        self.upUrl = self.upUrls[0]

    def click_follow_one(self, bro, chains, dynamic_url):
        """
        点击关注，并且返回相关的up主信息
        :param bro:
        :param chains:
        :return:
        """
        try:
            relation = self.get_follow_relation(bro)
            if relation.get('followed'):
                mylogger.info("UP主已关注，跳过关注")
                return
            self.follow_by_api(bro, self.upId)
            random_sleep(start=1, end=2)
            if not self.wait_follow_relation(bro, expected=True):
                raise Exception("关注操作结果无法确认")
        except Exception as e:
            mylogger.error("[click_follow 点击“关注” 出错 %s]" % e, exc_info=True)
            raise

    def follow_by_api(self, bro, up_id=None):
        """使用当前登录会话的 CSRF Cookie 关注 UP，避免依赖空间页按钮渲染。"""
        fid = str(up_id or self.upId or '').strip()
        if not fid.isdigit():
            raise Exception('关注接口缺少有效 UP UID')
        csrf = next(
            (
                cookie.get('value')
                for cookie in bro.get_cookies()
                if cookie.get('name') == 'bili_jct' and cookie.get('value')
            ),
            None,
        )
        if not csrf:
            raise Exception('缺少 bili_jct Cookie，无法执行关注')
        result = bro.execute_async_script("""
const fid = arguments[0];
const csrf = arguments[1];
const done = arguments[2];
const body = new URLSearchParams({fid: fid, act: '1', re_src: '11', csrf: csrf, jsonp: 'jsonp'});
let timer = setTimeout(() => done({code: -1, message: 'timeout'}), 10000);
fetch('https://api.bilibili.com/x/relation/modify', {
  method: 'POST',
  credentials: 'include',
  headers: {'Content-Type': 'application/x-www-form-urlencoded'},
  body: body.toString(),
}).then(response => response.json())
  .then(data => { clearTimeout(timer); done(data); })
  .catch(error => { clearTimeout(timer); done({code: -1, message: String(error)}); });
""", fid, csrf)
        if not isinstance(result, dict):
            raise Exception('关注接口返回格式异常')
        if result.get('code') != 0:
            raise Exception(
                '关注接口失败：code=%s，message=%s'
                % (result.get('code'), result.get('message') or '未知')
            )
        return result

    def get_element_text(self, ele):
        return (ele.get_attribute('innerText') or ele.text or '').strip()

    def get_space_follow_button(self, bro):
        for selector in ['.space-follow-btn', '.follow-btn', '.space-head-follow']:
            for button in self.get_visible_elements(bro, selector):
                if self.has_text(button, ["关注", "已关注", "已互粉"]):
                    return button
        return None

    def is_followed_on_space(self, bro):
        button = self.get_space_follow_button(bro)
        return button is not None and self.has_text(button, ["已关注", "已互粉"])

    def is_followed_by_relation_api(self, bro):
        return self.get_follow_relation(bro).get('followed', False)

    def get_follow_relation(self, bro):
        if not self.upId:
            return {'ok': False, 'followed': False, 'code': None}
        try:
            result = bro.execute_async_script("""
const fid = arguments[0];
const done = arguments[1];
let timer = setTimeout(() => done({code: -1}), 8000);
fetch('https://api.bilibili.com/x/relation?fid=' + encodeURIComponent(fid), {credentials: 'include'})
  .then(response => response.json())
  .then(data => { clearTimeout(timer); done(data); })
  .catch(() => { clearTimeout(timer); done({}); });
""", self.upId)
            if result.get('code') != 0:
                return {'ok': False, 'followed': False, 'code': result.get('code')}
            attribute = int(result.get('data', {}).get('attribute') or 0)
            return {'ok': True, 'followed': (attribute & 2) == 2, 'code': 0}
        except Exception:
            return {'ok': False, 'followed': False, 'code': None}

    def wait_follow_relation(self, bro, expected, attempts=3):
        for attempt in range(attempts):
            relation = self.get_follow_relation(bro)
            if relation.get('followed') is expected:
                mylogger.info("关注状态确认成功：%s", "已关注" if expected else "已取关")
                return True
            if relation.get('code') in (429, -429):
                mylogger.warning("关注状态查询触发 429，第 %s/%s 次重试", attempt + 1, attempts)
            if attempt < attempts - 1:
                random_sleep(start=2, end=4)
        return False

    def click_like(self, bro, chains):
        """
        点赞
        :param bro:
        :param chains:
        :return:
        """
        try:
            like_btn = self.wait_clickable_css(bro, self.LIKE_CSS)
            if self.is_done_text(like_btn, ["active", "liked", "已赞"]):
                mylogger.info("动态已点赞，跳过点赞")
                return
            self.click_by_js(bro, like_btn)
        except Exception as e:
            mylogger.error("[click_like 点击“点赞” 出错 %s]" % e, exc_info=True)
            raise

    def click_reserve(self, bro, chains):
        """
        预约抽奖
        :param bro:
        :param chains:
        :return:
        """
        try:
            try:
                self.click_first_by_text(bro, ["预约", "参与"], timeout=3)
            except TimeoutException:
                mylogger.info("未找到预约/参与按钮，跳过预约")
        except Exception as e:
            mylogger.error("[click_like 点击“预约抽奖” 出错 %s]" % e, exc_info=True)
            raise

    def commit_comment(self, bro, chains, comment_content):
        """
        评论
        :param comment_content:
        :param bro:
        :param chains:
        :return:
        """
        try:
            self.wait_css(bro, 'bili-comments', timeout=20)
            editor = WebDriverWait(bro, 10).until(lambda driver: self.find_comment_editor(driver))
            self.click_by_js(bro, editor)
            editor.send_keys(str(comment_content))
            random_sleep(start=1, end=2)
            ok = bro.execute_script("""
function visible(el) {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
}
function walk(root) {
  const buttons = root.querySelectorAll ? root.querySelectorAll('button') : [];
  for (const btn of buttons) {
    const text = (btn.innerText || btn.textContent || '').trim();
    const cls = String(btn.className || '');
    if (visible(btn) && text === '发布' && !btn.disabled && !cls.includes('disabled')) {
      btn.click();
      return true;
    }
  }
  const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
  for (const el of all) {
    if (el.shadowRoot && walk(el.shadowRoot)) return true;
  }
  return false;
}
return walk(document);
""")
            if not ok:
                raise Exception("未找到评论发布按钮")
            try:
                WebDriverWait(bro, 3).until(lambda driver: self.has_current_user_comment(driver))
            except TimeoutException:
                mylogger.info("评论已提交，评论列表尚未刷新，继续转发")
        except Exception as e:
            mylogger.error("[commit_comment 点击“评论” 出错 %s]" % e, exc_info=True)
            raise

    def find_comment_editor(self, bro):
        return bro.execute_script("""
function visible(el) {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
}
function walk(root) {
  const editor = root.querySelector && root.querySelector('.brt-editor[contenteditable="true"], [contenteditable="true"].brt-editor');
  if (editor && visible(editor)) return editor;
  const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
  for (const el of all) {
    if (el.shadowRoot) {
      const found = walk(el.shadowRoot);
      if (found) return found;
    }
  }
  return null;
}
return walk(document);
""")

    def click_share(self, bro, chains, share_content):
        """
        执行转发动态
        :param bro:
        :param chains:
        :return:
        """
        try:
            share_btn = self.wait_clickable_css(bro, self.FORWARD_CSS)
            self.click_by_js(bro, share_btn)
            share_text = self.wait_css(bro, self.SHARE_EDITOR_CSS, timeout=10)
            self.focus_contenteditable_start(bro, share_text)
            share_text.send_keys(str(share_content))
            random_sleep(start=1, end=2)
            publish_btn = WebDriverWait(bro, 10).until(lambda driver: self.find_share_publish_btn(driver))
            self.click_by_js(bro, publish_btn)
            try:
                self.wait_share_success(bro)
            except StaleElementReferenceException:
                # 发布成功后页面会重渲染，轮询中的弹窗节点可能立刻失效；
                # 此时发布动作已经提交，不能把一次真实转发误记为失败后重复提交。
                mylogger.info('转发已提交，发布确认期间弹窗刷新，继续后续校验')
        except Exception as e:
            mylogger.error("[click_share 点击“分享” 出错 %s]" % e, exc_info=True)
            raise

    def focus_contenteditable_start(self, bro, ele):
        bro.execute_script("""
const ele = arguments[0];
ele.focus();
const range = document.createRange();
range.selectNodeContents(ele);
range.collapse(true);
const selection = window.getSelection();
selection.removeAllRanges();
selection.addRange(range);
""", ele)

    def wait_share_success(self, bro, timeout=20):
        def result(driver):
            modal = self.get_visible_elements(driver, '.bili-dyn-share-publishing')
            modal_text = " ".join(
                (element.get_attribute('innerText') or element.text or '').strip()
                for element in modal
            )
            # 转发弹窗会引用原动态正文，正文可能出现“操作失败”等词。
            # 只把明确的发布结果提示当作失败；同时优先识别成功提示。
            fail_words = [
                "发布失败", "转发失败", "提交失败", "csrf",
                "验证码", "请求过于频繁", "系统繁忙",
            ]
            if "发布成功" in modal_text or "转发成功" in modal_text:
                return True
            if any(word in modal_text for word in fail_words):
                raise Exception("转发提交失败，页面提示：" + self.get_page_tip(modal_text))
            if len(modal) == 0:
                message_text = self.get_visible_message_text(driver)
                if any(word in message_text for word in fail_words):
                    raise Exception("转发提交失败，页面提示：" + self.get_page_tip(message_text))
                return True
            return False

        WebDriverWait(bro, timeout).until(result)

    def get_visible_message_text(self, bro):
        messages = bro.find_elements(
            By.CSS_SELECTOR,
            '[role="alert"], [class*="toast"], [class*="Toast"], '
            '[class*="notification"], [class*="Notification"]'
        )
        return " ".join(
            (element.get_attribute('innerText') or element.text or '').strip()
            for element in messages
            if element.is_displayed()
        )

    def get_page_tip(self, body):
        text = body.replace('\n', ' ').strip()
        if len(text) > 200:
            return text[-200:]
        return text

    def find_share_publish_btn(self, bro):
        buttons = self.get_visible_elements(bro, self.SHARE_PUBLISH_CSS)
        for button in buttons:
            text = (button.get_attribute('innerText') or button.text or '').strip()
            cls = button.get_attribute('class') or ''
            if "发布" in text and "disabled" not in cls:
                return button
        return False

    def to_old_version(self, bro, chains):
        """
        新版转旧版
        :param bro:
        :param chains:
        :return:
        """
        mylogger.info("当前已使用新版动态页结构，跳过旧版切换")
