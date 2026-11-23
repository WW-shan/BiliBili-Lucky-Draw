import time

from service.bilibili_errors import (
    BilibiliAuthenticationRequired,
    BilibiliVerificationRequired,
)
from service.log_service.log_printer_service import MyLogger
from service.lottery_service.verify_lottery_service import VerifyLotteryService
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.multi_users_share import MultiUsersShareService
from utils import globals
from utils.webdriver_util import cleanup_stale_selenium_sessions


mylogger = MyLogger('main.py').getLogger()
_workflow_paused = False


def get_queue_counts():
    """Read the small amount of state needed to schedule the next step."""
    verify_service = VerifyLotteryService(globals.my_user_id)
    return {
        'ready': len(verify_service.ready_dao.query_ready(limit=1) or []),
        'pending': len(
            verify_service.draw_dao.query_by_verification_priority(limit=1) or []
        ),
    }


def run_actions():
    if not globals.ups:
        raise RuntimeError('没有配置 ups 订阅源，本轮不执行验证和转发。')
    stats = VerifyLotteryService(globals.my_user_id).verify_pending(
        globals.verify_batch_limit
    )
    MultiUsersShareService().do_multi_uses_share()
    return stats


def run_discovery():
    if not globals.ups:
        raise RuntimeError('没有配置 ups 订阅源，本轮不执行发现。')
    return SearchDynamicByUps(globals.my_user_id).init_search()


def _share_ready_queue():
    MultiUsersShareService().do_multi_uses_share()


def run_pipeline_iteration():
    """One continuous unit of work: drain ready work, verify, then discover."""
    counts = get_queue_counts()
    ready = counts.get('ready', 0)
    pending = counts.get('pending', 0)
    progressed = False

    if ready:
        _share_ready_queue()
        progressed = True

    counts = get_queue_counts()
    pending = counts.get('pending', 0)
    if pending:
        stats = VerifyLotteryService(globals.my_user_id).verify_pending(
            globals.verify_batch_limit
        )
        stats = stats if isinstance(stats, dict) else {}
        progressed = bool(
            stats.get('checked')
            or stats.get('confirmed')
            or stats.get('excluded')
            or stats.get('expired')
        )
        # Newly-confirmed rows must be executed immediately, not on a timer.
        if (get_queue_counts().get('ready') or 0):
            _share_ready_queue()
            progressed = True
        return progressed

    # Verification may have been empty because all pending rows are cooling;
    # only discover when both queues are actually empty.
    if not (get_queue_counts().get('ready') or 0):
        run_discovery()
        progressed = True
    return progressed


def _run_safely(job):
    global _workflow_paused
    if _workflow_paused:
        return
    try:
        return job()
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
        # A transient Selenium session exhaustion must not permanently stop the
        # continuous queue. Let the next short poll retry.
        if 'Could not start a new session' in str(exc) or 'SessionNotCreated' in type(exc).__name__:
            return False
    return False


def run_safely():
    return _run_safely(run_pipeline_iteration)


def run_continuous_worker():
    """Run immediately and continuously; sleep only when no work progressed."""
    try:
        removed = cleanup_stale_selenium_sessions()
        if removed:
            mylogger.warning('启动时清理 Selenium 残留会话：%s', removed)
    except Exception:
        mylogger.warning('启动时清理 Selenium 残留会话失败', exc_info=True)
    mylogger.info('连续流水线已启动：无固定 5 分钟/4 小时等待。')
    while True:
        try:
            progressed = run_safely()
            if not progressed:
                time.sleep(max(1, int(globals.idle_poll_seconds)))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            mylogger.error('连续流水线出错：%s', exc, exc_info=True)
            time.sleep(max(1, int(globals.idle_poll_seconds)))


if __name__ == '__main__':
    run_continuous_worker()
