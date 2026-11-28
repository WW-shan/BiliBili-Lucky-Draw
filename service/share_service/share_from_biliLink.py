import ast
import json
import re

from dao.draw_dynamic_dao import DrawDynamicDao
from dao.follow_up_dao import FollowUpInfoDao
from dao.init_db import init_db
from dao.lottery_ready_dao import LotteryReadyDao
from dao.scan_cache_dao import ScanCacheDao
from dao.share_info_dao import ShareInfoDao
from dao.statistics_dao import StatisticsDao
from service.bilibili_errors import (
    BilibiliAuthenticationRequired,
    BilibiliVerificationRequired,
)
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.notify_service.notify_service import NotifyService
from service.remove_msg import RemoveMsgService
from service.share_service.share_one_dynamic import DynamicShareBase
from utils import globals
from utils.globals import get_random_comment_content, get_random_share_content
from utils.ip_util import remove_query_string
from utils.time_util import random_sleep
from utils.webdriver_util import force_delete_selenium_session, init_webdriver

mylogger = MyLogger('share_from_biliLick.py').getLogger()


class BiliLinkShare(object):
    MIN_CURRENT_DYNAMIC_ID = 1_250_000_000_000_000_000

    def __init__(self, user_id, bro=None, chains=None):
        db = init_db()
        self.share_note = ""
        self.user_id = user_id
        self.bro = bro
        self.chains = chains
        self.owns_browser = bro is None
        self.share_info_dao = ShareInfoDao(db)
        self.follow_up_dao = FollowUpInfoDao(db)
        self.draw_dynamic_dao = DrawDynamicDao(db)
        self.lottery_ready_dao = LotteryReadyDao(db)
        self.scan_cache_dao = ScanCacheDao(db)
        self.statistics_dao = StatisticsDao(db)
        mylogger.info("启动：根据B站up主的分享链接进行抽奖动态转发!")

    def do_share_by_links(self):
        do_share_cnt = 0
        success_share_cnt = 0
        try:
            datas = self.get_today_dynamic_links()
            if not datas:
                datas = self.get_today_dynamic_links()
            if not datas:
                mylogger.info('暂无已确认的抽奖，跳过本轮转发')
                return

            # Filter stale IDs before creating a browser session. A historic
            # post cannot be made safe merely by having a stale lottery rule.
            ignore_links = self.get_ignore_link()
            filtered_datas = []
            for data in datas:
                lucky_dynamic_url = remove_query_string(data['dyn_url'])
                try:
                    dynamic_id = int(re.search(r'/(\d+)(?:\?|$)', lucky_dynamic_url).group(1))
                except (AttributeError, TypeError, ValueError):
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=3
                    )
                    continue
                if dynamic_id < self.MIN_CURRENT_DYNAMIC_ID:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=4
                    )
                    self.lottery_ready_dao.delete_by_url(lucky_dynamic_url)
                    mylogger.warning('跳过过期/历史动态，不执行抽奖：%s', lucky_dynamic_url)
                    continue
                filtered_datas.append(data)
            datas = self._dedupe_ready_links(filtered_datas)
            if not datas:
                mylogger.info('本轮没有现代动态ID，跳过登录与执行')
                return

            if self.owns_browser and self.bro is None:
                self.bro, self.chains = init_webdriver()
            login_service = LoginService(self.bro, self.chains, self.user_id)
            authenticated_uid = login_service.login_by_cookie()
            if str(self.user_id) != authenticated_uid:
                mylogger.warning(
                    '配置的转发 UID 与登录会话不一致，按已验证账号 UID=%s 执行。',
                    authenticated_uid,
                )
                self.user_id = authenticated_uid
            login_service.ensure_publish_cookie()

            for data in datas:
                lucky_dynamic_url = remove_query_string(data['dyn_url'])
                if any(ign_lnk in lucky_dynamic_url for ign_lnk in ignore_links):
                    continue
                shared = self.share_info_dao.query_shareInfo_by_shareUrl(
                    lucky_dynamic_url, self.user_id
                )
                if len(shared) == 0:
                    alternate_share = self.share_info_dao.query_shareInfo_by_dynamic_id(
                        lucky_dynamic_url, self.user_id
                    )
                    if isinstance(alternate_share, (list, dict)) and alternate_share:
                        shared = alternate_share
                if len(shared) != 0:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=1
                    )
                    continue
                do_share_cnt += 1
                if do_share_cnt > 1:
                    random_sleep(start=20, end=30)
                dyn = DynamicShareBase()
                dyn.user_id = self.user_id
                dyn.share_one(
                    self.bro,
                    self.chains,
                    lucky_dynamic_url,
                    get_random_share_content(),
                    get_random_comment_content(),
                    lottery_end_time=data.get('lottery_end_time'),
                    lottery_kind=data.get('lottery_kind') or 'official',
                    lottery_rule=data.get('lottery_rule'),
                    required_actions=data.get('required_actions'),
                    original_texts=self._rule_texts(data),
                )
                if dyn.last_error:
                    evidence = dyn.get_risk_control_evidence(dyn.last_error)
                    if evidence:
                        raise BilibiliVerificationRequired(
                            'B站安全风控（%s），已停止自动转发：%s'
                            % (evidence, lucky_dynamic_url)
                        )
                    if '未能从动态标题解析UP主UID' in str(dyn.last_error):
                        canonical_url = self._canonical_opus_url(lucky_dynamic_url)
                        if canonical_url and canonical_url != lucky_dynamic_url:
                            mylogger.warning(
                                'UP UID 解析失败，改用 opus 地址重试一次：%s',
                                canonical_url,
                            )
                            dyn = DynamicShareBase()
                            dyn.user_id = self.user_id
                            dyn.share_one(
                                self.bro,
                                self.chains,
                                canonical_url,
                                get_random_share_content(),
                                get_random_comment_content(),
                                lottery_end_time=data.get('lottery_end_time'),
                                lottery_kind=data.get('lottery_kind') or 'official',
                                lottery_rule=data.get('lottery_rule'),
                                required_actions=data.get('required_actions'),
                                original_texts=self._rule_texts(data),
                            )
                            if not dyn.last_error:
                                lucky_dynamic_url = canonical_url
                    if dyn.last_error:
                        mylogger.warning(
                            '单条动态参与失败，保留验证队列等待复验：%s，原因：%s',
                            lucky_dynamic_url,
                            dyn.last_error,
                        )
                        self.draw_dynamic_dao.update_sharedUrl(
                            url=lucky_dynamic_url, status=0
                        )
                        # Record a failure tombstone before removing the ready
                        # rule. restore_live_ready_statuses() checks this cache
                        # and will not immediately promote the row back to 5.
                        self.scan_cache_dao.save_failure(
                            'lottery',
                            lucky_dynamic_url,
                            data.get('source') or '',
                            data.get('note') or '',
                            (dyn.last_error or 'action_failed')[:500],
                        )
                        self.lottery_ready_dao.delete_by_url(lucky_dynamic_url)
                        continue
                if dyn.share_status == 2:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=2
                    )
                    self.lottery_ready_dao.delete_by_url(lucky_dynamic_url)
                    continue
                if dyn.share_status == 4:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=4
                    )
                    continue
                if dyn.share_status == 0:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=1
                    )
                    self.share_info_dao.insert_shareInfo(dyn)
                    self.draw_dynamic_dao.mark_shared_by_dynamic_id(
                        lucky_dynamic_url, self.user_id
                    )
                    up_id = str(getattr(dyn, 'upId', '') or '').strip()
                    up_url = str(getattr(dyn, 'upUrl', '') or '').strip()
                    if up_id and up_id.lower() != 'none' and up_url and up_url.lower() != 'none':
                        self.follow_up_dao.saverUpdate(up_id, up_url, self.user_id)
                    elif 'follow' in (getattr(dyn, 'required_actions', []) or []):
                        mylogger.warning(
                            '关注规则已执行但未解析到UP信息，跳过关注记录写入：%s',
                            lucky_dynamic_url,
                        )
                    success_share_cnt += 1
                else:
                    mylogger.warning(
                        '单条动态返回未知状态 %s：%s',
                        dyn.share_status,
                        lucky_dynamic_url,
                    )
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=3
                    )
        except (BilibiliAuthenticationRequired, BilibiliVerificationRequired):
            raise
        except Exception:
            mylogger.error("[do_share_by_links 根据url转发动态出错]", exc_info=True)
        finally:
            percentage = (success_share_cnt / do_share_cnt) * 100 if do_share_cnt else 0
            succ_percentage = f"成功率为 : {percentage:.2f}%"
            content = " 成功的转发条数为：" + str(success_share_cnt) + ";" + succ_percentage
            if 50 > percentage > 0:
                NotifyService().fangtang_msg_push_by_content(
                    title="程序预警，需要处理！", content=content
                )
            try:
                self.statistics_dao.insert(self.user_id, content, "")
            except Exception:
                mylogger.error('写入转发统计失败', exc_info=True)
            if self.owns_browser and self.bro is not None:
                try:
                    self.bro.quit()
                except Exception:
                    mylogger.warning('关闭转发浏览器失败', exc_info=True)
                force_delete_selenium_session(self.bro)

    @staticmethod
    def _canonical_opus_url(url):
        match = re.search(r'/(\d+)(?:\?|$)', str(url))
        if not match:
            return None
        return 'https://www.bilibili.com/opus/' + match.group(1)

    def get_ignore_link(self):
        links = globals.ignore_link
        if len(links) != 0:
            return links.split('|')
        return {}

    @staticmethod
    def _rule_texts(data):
        """Extract stable text fingerprints from the verified rule payload."""
        raw = data.get('lottery_rule')
        if isinstance(raw, (dict, list, tuple)):
            rules = raw
        elif raw:
            try:
                rules = json.loads(raw)
            except Exception:
                try:
                    # t_lottery_ready stores str(dict), which is valid Python
                    # but not strict JSON.
                    rules = ast.literal_eval(str(raw))
                except Exception:
                    rules = []
        else:
            rules = []
        if isinstance(rules, dict):
            rules = [rules]
        texts = []
        for rule in rules if isinstance(rules, (list, tuple)) else []:
            if isinstance(rule, dict):
                text = str(rule.get('text') or '').strip()
            else:
                text = str(rule or '').strip()
            if text and text not in texts:
                texts.append(text)
        return texts

    def get_today_dynamic_links(self):
        """只读取已确认官方 lottery_id 的可执行队列。"""
        return self.lottery_ready_dao.query_ready(limit=globals.share_batch_limit, user_id=self.user_id)

    @staticmethod
    def _has_explicit_required_actions(data):
        raw = data.get('required_actions') if isinstance(data, dict) else None
        if isinstance(raw, (list, tuple)):
            return bool(raw)
        if not isinstance(raw, str) or not raw.strip():
            return False
        text = raw.strip()
        try:
            parsed = json.loads(text)
        except Exception:
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                return False
        return isinstance(parsed, (list, tuple)) and bool(parsed)

    @classmethod
    def _dedupe_ready_links(cls, datas):
        """Execute each dynamic once, preferring a verified plain-text rule."""
        selected = {}
        order = []
        for data in datas:
            url = (data or {}).get('dyn_url') if isinstance(data, dict) else None
            match = re.search(r'/(\d+)(?:\?|$)', str(url or ''))
            key = match.group(1) if match else str(url or '')
            if not key:
                continue
            explicit = cls._has_explicit_required_actions(data)
            priority = (
                0 if explicit and data.get('lottery_kind') == 'plain_text'
                else 1 if explicit
                else 2
            )
            if key not in selected:
                order.append(key)
                selected[key] = (priority, data)
            elif priority < selected[key][0]:
                selected[key] = (priority, data)
        return [selected[key][1] for key in order]

