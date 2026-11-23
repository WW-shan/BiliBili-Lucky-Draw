import re

from dao.draw_dynamic_dao import DrawDynamicDao
from dao.follow_up_dao import FollowUpInfoDao
from dao.init_db import init_db
from dao.lottery_ready_dao import LotteryReadyDao
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
        self.statistics_dao = StatisticsDao(db)
        mylogger.error("启动：根据B站up主的分享链接进行抽奖动态转发!")

    def do_share_by_links(self):
        do_share_cnt = 0
        success_share_cnt = 0
        break_flag = 0
        try:
            datas = self.get_today_dynamic_links()
            if not datas:
                # Verification and action batches run at different intervals.
                # If the verifier just marked rows status 5, retry the action
                # queue once before ending the cycle so it does not sit idle
                # until the next 5-minute tick.
                datas = self.get_today_dynamic_links()
            if not datas:
                mylogger.info('暂无已确认的抽奖，跳过本轮转发')
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
            ignore_links = self.get_ignore_link()
            for data in datas:
                lucky_dynamic_url = remove_query_string(data['dyn_url'])
                # lucky_dynamic_url = 'https://www.bilibili.com/opus/886341319897645089'
                for ign_lnk in ignore_links:
                    if ign_lnk in lucky_dynamic_url:
                        break_flag = 1
                # 跳过已经转发过的
                if break_flag == 1:
                    break_flag = 0
                    continue
                break_flag = 0
                shared = self.share_info_dao.query_shareInfo_by_shareUrl(lucky_dynamic_url, self.user_id)
                if len(shared) != 0:
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=1)
                    continue
                do_share_cnt = do_share_cnt + 1
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
                )
                if dyn.last_error:
                    evidence = dyn.get_risk_control_evidence(dyn.last_error)
                    if evidence:
                        raise BilibiliVerificationRequired(
                            'B站安全风控（%s），已停止自动转发：%s'
                            % (evidence, lucky_dynamic_url)
                        )
                    # A stale t.bilibili.com shell can temporarily omit author
                    # metadata. Retry the same dynamic once on the canonical
                    # opus URL before putting it back into verification.
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
                        continue
                if dyn.share_status == 2:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=2
                    )
                    continue
                if dyn.share_status == 4:
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=4
                    )
                    continue
                # 保存转发状态和关注的up主信息
                if dyn.share_status == 0:
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=1)
                    self.share_info_dao.insert_shareInfo(dyn)
                    self.follow_up_dao.saverUpdate(dyn.upId, dyn.upUrl, self.user_id)
                    success_share_cnt = success_share_cnt + 1
                else:
                    mylogger.warning('单条动态返回未知状态 %s：%s', dyn.share_status, lucky_dynamic_url)
                    self.draw_dynamic_dao.update_sharedUrl(
                        url=lucky_dynamic_url, status=3
                    )
        except (BilibiliAuthenticationRequired, BilibiliVerificationRequired):
            raise
        except Exception:
            mylogger.error("[do_share_by_links 根据url转发动态出错]", exc_info=True)
        finally:
            if do_share_cnt == 0:
                percentage = 0
            else:
                percentage = (success_share_cnt / do_share_cnt) * 100
            succ_percentage = f"成功率为 : {percentage:.2f}%"
            content = " 成功的转发条数为：" + str(success_share_cnt) + ";" + succ_percentage
            if 50 > percentage > 0:
                NotifyService().fangtang_msg_push_by_content(title="程序预警，需要处理！", content=content)
            try:
                self.statistics_dao.insert(self.user_id, content, "")
            except Exception:
                mylogger.error('写入转发统计失败', exc_info=True)
            # 暂时停止移除
            # RemoveMsgService(self.user_id, success_share_cnt, bro=self.bro, chains=self.chains).do_remove()
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

    def get_today_dynamic_links(self):
        """只读取已确认官方 lottery_id 的可执行队列。"""
        return self.lottery_ready_dao.query_ready(limit=globals.share_batch_limit)

