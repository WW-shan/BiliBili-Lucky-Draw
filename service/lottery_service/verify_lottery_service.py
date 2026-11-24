import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dao.draw_dynamic_dao import DrawDynamicDao
from dao.init_db import init_db
from dao.lottery_ready_dao import LotteryReadyDao
from dao.scan_cache_dao import ScanCacheDao
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.share_service.share_one_dynamic import DynamicShareBase
from utils import globals
from utils.webdriver_util import force_delete_selenium_session, init_webdriver


mylogger = MyLogger('verify_lottery_service.py').getLogger()
SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')


class VerifyLotteryService(object):
    RETRY_BACKOFF_MINUTES = 15
    MIN_CURRENT_DYNAMIC_ID = 1_250_000_000_000_000_000
    # A transient timeout must not end the whole batch.  Circuit-break only
    # when the remote endpoint is visibly blocking us.
    UNKNOWN_CIRCUIT_BREAKER = 3
    RISK_CONTROL_REASONS = frozenset({
        'html_risk_control', 'api_code:-352', 'api_code:-412',
        'api_code:-509', 'api_code:412', 'request_error',
        'opus_detail_invalid_response',
    })
    # These mean the post cannot be proven executable from this pass, not
    # that the remote endpoint is unavailable. They must not circuit-break a
    # batch: otherwise three ambiguous posts can starve every later candidate.
    SEMANTIC_DEFER_REASONS = frozenset({
        'missing_prize', 'missing_action', 'code0_without_lottery_id',
        'api_code:4101152',
    })

    @staticmethod
    def _is_expired(lottery_end_time):
        if lottery_end_time is None:
            return False
        if isinstance(lottery_end_time, datetime):
            end_time = lottery_end_time
        else:
            try:
                end_time = datetime.fromisoformat(str(lottery_end_time))
            except (TypeError, ValueError):
                return False
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=SHANGHAI_TZ)
        return datetime.now(SHANGHAI_TZ).timestamp() >= end_time.timestamp()

    def __init__(
        self,
        user_id,
        bro=None,
        chains=None,
        checker=None,
        pause_seconds=1.0,
        min_current_dynamic_id=None,
    ):
        db = init_db()
        self.user_id = str(user_id)
        self.draw_dao = DrawDynamicDao(db)
        self.ready_dao = LotteryReadyDao(db)
        self.scan_cache_dao = ScanCacheDao(db)
        self.checker = checker or DynamicShareBase()
        self.pause_seconds = pause_seconds
        self.min_current_dynamic_id = (
            self.MIN_CURRENT_DYNAMIC_ID
            if min_current_dynamic_id is None
            else int(min_current_dynamic_id)
        )
        self.bro = bro
        self.chains = chains
        self.owns_browser = bro is None

    def _ensure_browser(self):
        if self.owns_browser and self.bro is None:
            self.bro, self.chains = init_webdriver(
                proxy_url=globals.verification_proxy_url or None
            )
            LoginService(self.bro, self.chains, self.user_id).login_by_cookie()
            self.bro.get('https://www.bilibili.com/')

    def verify_pending(self, limit=None):
        batch_started = time.monotonic()
        limit = max(1, int(limit or globals.verify_batch_limit))
        candidates = self.draw_dao.query_by_verification_priority(
            limit=min(max(limit * 4, limit), 200)
        ) or []
        stats = {
            'checked': 0,
            'confirmed': 0,
            'excluded': 0,
            'deferred': 0,
            'expired': 0,
            'skipped_recent': 0,
            'reverified_after_failure': 0,
            'cooldown_started': 0,
        }
        eligible = []
        seen_dynamic_ids = set()
        for candidate in candidates:
            url = candidate.get('dyn_url')
            if not url:
                continue
            if str(candidate.get('status')) == '5':
                # A status-5 row already has a verified rule. Never send it
                # back through text classification just because it lost a
                # transient race with the worker; otherwise a newer rule can
                # be downgraded by an older parse.
                continue
            dynamic_match = re.search(r'/(\d+)(?:\?|$)', str(url))
            dynamic_id = dynamic_match.group(1) if dynamic_match else url
            if dynamic_match and self.min_current_dynamic_id:
                try:
                    if int(dynamic_id) < self.min_current_dynamic_id:
                        stats['excluded'] += 1
                        continue
                except (TypeError, ValueError):
                    continue
            if dynamic_id in seen_dynamic_ids:
                # t.bilibili.com/<id> and www.bilibili.com/opus/<id> are the
                # same object; verify it once and sync both rows together.
                continue
            # Action failures are tombstones, not permission to re-execute.
            # Check the cache before the ready-rule shortcut; otherwise a stale
            # rule can be promoted from 0 back to 5 immediately.
            if self.scan_cache_dao.has_recent_failure(
                'lottery', url, self.RETRY_BACKOFF_MINUTES / 60.0,
            ):
                stats['skipped_recent'] += 1
                continue
            existing_rule = self.ready_dao.query_by_url(url)
            if isinstance(existing_rule, dict) and existing_rule:
                if not self._is_expired(existing_rule.get('lottery_end_time')):
                    self.draw_dao.update_sharedUrl(url=url, status='5')
                    stats['skipped_recent'] += 1
                    continue
            seen_dynamic_ids.add(dynamic_id)
            if str(candidate.get('status')) == '3':
                stats['reverified_after_failure'] += 1
            eligible.append(candidate)
            if len(eligible) >= limit:
                break
        consecutive_unknown = 0
        try:
            if eligible:
                self._ensure_browser()
            for candidate in eligible:
                url = candidate.get('dyn_url')
                result = self.checker.fetch_official_lottery_notice(self.bro, url)
                stats['checked'] += 1
                source = candidate.get('source') or ''
                note = candidate.get('note') or ''
                if result.get('confirmed'):
                    lottery_end_time = result.get('lottery_end_time')
                    stats['confirmed'] += 1
                    if self._is_expired(lottery_end_time):
                        self.draw_dao.update_sharedUrl(url=url, status='2')
                        self.scan_cache_dao.save_success('lottery', url, source, note, 1)
                        stats['expired'] += 1
                        continue
                    required_actions = (
                        result.get('lottery_rule') or {}
                    ).get('required_actions') or ''
                    upsert_result = self.ready_dao.upsert(
                        url,
                        result.get('lottery_id') or '',
                        lottery_end_time,
                        lottery_kind=(
                            'plain_text'
                            if result.get('source') == 'plain_text'
                            else 'official'
                        ),
                        lottery_rule=str(result.get('lottery_rule') or ''),
                        required_actions=str(required_actions),
                    )
                    if isinstance(upsert_result, str) and upsert_result.startswith('error:'):
                        raise RuntimeError(
                            '已确认抽奖但执行规则写入失败：%s' % upsert_result
                        )
                    self.draw_dao.update_sharedUrl(url=url, status='5')
                    self.scan_cache_dao.save_success('lottery', url, source, note, 1)
                elif not result.get('retryable'):
                    self.draw_dao.update_sharedUrl(url=url, status='4')
                    self.scan_cache_dao.save_success('lottery', url, source, note, 0)
                    stats['excluded'] += 1
                else:
                    reason = str(result.get('reason') or 'unknown')
                    diagnostic = reason
                    api_message = str(result.get('api_message') or '').strip()
                    if api_message:
                        diagnostic += ': ' + api_message[:200]
                    self.scan_cache_dao.save_failure(
                        'lottery', url, source, note, diagnostic
                    )
                    stats['deferred'] += 1
                    mylogger.info(
                        '抽奖资格暂无法确认，保留待重试：%s（%s%s）',
                        url,
                        reason,
                        ('，接口消息：' + api_message[:80]) if api_message else '',
                    )
                    if reason not in self.SEMANTIC_DEFER_REASONS:
                        consecutive_unknown += 1
                    else:
                        consecutive_unknown = 0
                    if reason in self.RISK_CONTROL_REASONS:
                        stats['cooldown_started'] = 1
                        mylogger.warning(
                            '检测到B站风控（%s），提前结束本轮验证并保留待重试',
                            reason,
                        )
                        break
                    if consecutive_unknown >= self.UNKNOWN_CIRCUIT_BREAKER:
                        stats['cooldown_started'] = 1
                        mylogger.warning(
                            '连续 %s 条临时接口异常，提前结束本轮验证并保留待重试',
                            consecutive_unknown,
                        )
                        break
                if self.pause_seconds:
                    time.sleep(self.pause_seconds)
        finally:
            # Rules are the source of truth. A duplicate URL can otherwise be
            # reset to status 0/2 by a stale verification pass after a valid
            # rule was written.
            try:
                self.ready_dao.restore_live_ready_statuses(self.user_id)
            except Exception:
                mylogger.warning('恢复有效抽奖执行状态失败', exc_info=True)
            if self.owns_browser and self.bro is not None:
                try:
                    self.bro.quit()
                except Exception:
                    mylogger.warning('关闭抽奖验证浏览器失败', exc_info=True)
                force_delete_selenium_session(self.bro)
        stats['elapsed_seconds'] = round(time.monotonic() - batch_started, 2)
        mylogger.info('抽奖资格验证完成：%s', stats)
        return stats
