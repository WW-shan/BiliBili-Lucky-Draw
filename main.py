import time

import schedule

from service.bilibili_errors import (
    BilibiliAuthenticationRequired,
    BilibiliVerificationRequired,
)
from service.log_service.log_printer_service import MyLogger
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.multi_users_share import MultiUsersShareService
from utils import globals


mylogger = MyLogger('main.py').getLogger()
_workflow_paused = False


def run_cycle():
    if not globals.ups:
        raise RuntimeError('没有配置 ups 订阅源，本轮不执行转发。')
    SearchDynamicByUps(globals.my_user_id).init_search()
    MultiUsersShareService().do_multi_uses_share()


def run_safely():
    global _workflow_paused
    if _workflow_paused:
        return
    try:
        run_cycle()
    except BilibiliVerificationRequired as exc:
        _workflow_paused = True
        mylogger.error(
            'Bilibili 当前要求人工验证；自动任务已暂停。完成验证后重启 worker 恢复：%s',
            exc,
        )
    except BilibiliAuthenticationRequired as exc:
        _workflow_paused = True
        mylogger.error(
            'Bilibili 登录会话无效；自动任务已暂停。更新会话后重启 worker 恢复：%s',
            exc,
        )
    except Exception as exc:
        mylogger.error('自动任务执行失败：%s', exc, exc_info=True)


def run_scheduler():
    if globals.auto_run_on_start:
        run_safely()

    schedule.every(globals.schedule_interval_hours).hours.do(run_safely)
    mylogger.info('自动调度已启动，间隔 %s 小时。', globals.schedule_interval_hours)
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as exc:
            mylogger.error('调度循环出错：%s', exc, exc_info=True)
            time.sleep(5)


if __name__ == '__main__':
    run_scheduler()
