import re

from dao.init_db import init_db
from service.share_service.share_one_dynamic import DynamicShareBase


class ShareInfoDao(object):
    def __init__(self, db):
        self.table_name = 't_share_info'
        self.db = db

    def query_shareInfo_by_userIdAndTime(self,  user_id, share_time):
        """
        根据转发的url查询
        :param user_id:
        :param share_url:
        :return:
        """
        try:
            sql = "SELECT * FROM " + self.table_name + " where user_id = '" + user_id + "'" + " and share_time >= '" + share_time + "'";
            data = self.db.select_db(sql)
            return data
        except Exception as e:
            return {}

    def query_shareInfo_by_shareUrl(self, share_url, user_id):
        """
        根据转发的url查询
        :param user_id:
        :param share_url:
        :return:
        """
        try:
            sql = "SELECT * FROM " + self.table_name + " where share_url = '" + share_url + "'" + " and user_id = '" + user_id + "'";
            data = self.db.select_db(sql)
            return data
        except Exception as e:
            return {}

    def query_shareInfo_by_dynamic_id(self, share_url, user_id):
        """Find an existing share record for either t/opus URL form."""
        match = re.search(r'/(\d+)(?:\?|$)', str(share_url or ''))
        if not match:
            return []
        dynamic_id = match.group(1)
        try:
            sql = (
                "SELECT * FROM " + self.table_name
                + " WHERE SUBSTRING_INDEX(SUBSTRING_INDEX(share_url, '/', -1), '?', 1) = '"
                + dynamic_id + "' AND user_id = '" + str(user_id) + "'"
            )
            return self.db.select_db(sql) or []
        except Exception:
            return []


    def insert_shareInfo(self, shareInfo):
        try:
            if self.query_shareInfo_by_dynamic_id(
                getattr(shareInfo, 'share_url', ''), getattr(shareInfo, 'user_id', '')
            ):
                return False
            params = {}
            params['upId'] = str(shareInfo.upId)
            params['upUrl'] = str(shareInfo.upUrl)
            params['share_url'] = str(shareInfo.share_url)
            params['status'] = str(shareInfo.status)
            params['machine_ip'] = str(shareInfo.machine_ip)
            params['share_time'] = str(shareInfo.share_time)
            params['user_id'] = str(shareInfo.user_id)
            params['share_status'] = str(shareInfo.share_status)
            self.db.insert(self.table_name, params)
            return True
        except Exception as e:
            print(e)
            return False

if __name__ == '__main__':
    db = init_db()
    shareInfodao = ShareInfoDao(db)
    # dyn = DynamicShareBase()
    # dyn.upUrl = '1'
    # dyn.upId = '2'
    # dyn.share_url = '2share_url'
    # dyn.status = 1
    # dyn.machine_ip = '2machine_ip'
    # dyn.share_time = '2022-12-12'
    # dyn.share_status = 1


