import time
from datetime import datetime

from dao.draw_dynamic_dao import DrawDynamicDao
from dao.init_db import init_db
from dao.lottery_ready_dao import LotteryReadyDao
from dao.scan_cache_dao import ScanCacheDao
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.share_service.share_one_dynamic import DynamicShareBase
from utils import globals
from utils.webdriver_util import init_webdriver


mylogger = MyLogger('verify_lottery_service.py').getLogger()


class VerifyLotteryService(object):
    RETRY_BACKOFF_MINUTES = 5

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
        return datetime.now() >= end_time

    def __init__(
        self,
        user_id,
        bro=None,
        chains=None,
        checker=None,
        pause_seconds=1.0,
    ):
        db = init_db()
        self.user_id = str(user_id)
        self.draw_dao = DrawDynamicDao(db)
        self.ready_dao = LotteryReadyDao(db)
        self.scan_cache_dao = ScanCacheDao(db)
        self.checker = checker or DynamicShareBase()
        self.pause_seconds = pause_seconds
        self.bro = bro
        self.chains = chains
        self.owns_browser = bro is None

    def _ensure_browser(self):
        if self.owns_browser and self.bro is None:
            self.bro, self.chains = init_webdriver()
            LoginService(self.bro, self.chains, self.user_id).login_by_cookie()
            self.bro.get('https://www.bilibili.com/')

    def verify_pending(self, limit=None):
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
        }
        eligible = []
        for candidate in candidates:
            url = candidate.get('dyn_url')
            if not url:
                continue
            if self.scan_cache_dao.has_recent_failure(
                'lottery', url, self.RETRY_BACKOFF_MINUTES / 60.0
            ):
                stats['skipped_recent'] += 1
                continue
            if str(candidate.get('status')) == '3':
                stats['reverified_after_failure'] += 1
            eligible.append(candidate)
            if len(eligible) >= limit:
                break
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
                    stats['confirmed'] += 1
                    lottery_end_time = result.get('lottery_end_time')
                    if self._is_expired(lottery_end_time):
                        self.draw_dao.update_sharedUrl(url=url, status='2')
                        self.scan_cache_dao.save_success('lottery', url, source, note, 1)
                        stats['expired'] += 1
                        continue
                    self.ready_dao.upsert(
                        url,
                        result.get('lottery_id'),
                        lottery_end_time,
                    )
                    self.draw_dao.update_sharedUrl(url=url, status='5')
                    self.scan_cache_dao.save_success('lottery', url, source, note, 1)
                elif not result.get('retryable'):
                    self.draw_dao.update_sharedUrl(url=url, status='4')
                    self.scan_cache_dao.save_success('lottery', url, source, note, 0)
                    stats['excluded'] += 1
                else:
                    self.scan_cache_dao.save_failure(
                        'lottery', url, source, note, result.get('reason') or 'unknown'
                    )
                    stats['deferred'] += 1
                    mylogger.info(
                        '抽奖资格暂无法确认，保留待重试：%s（%s）',
                        url,
                        result.get('reason') or 'unknown',
                    )
                    # API 连续返回 -9999 时先做短退避，避免把整个批次打满；
                    # 该条不会进入 24 小时全局冻结。
                    time.sleep(1.5)
                if self.pause_seconds:
                    time.sleep(self.pause_seconds)
        finally:
            if self.owns_browser and self.bro is not None:
                try:
                    self.bro.quit()
                except Exception:
                    mylogger.warning('关闭抽奖验证浏览器失败', exc_info=True)
        mylogger.info('抽奖资格验证完成：%s', stats)
        return stats
