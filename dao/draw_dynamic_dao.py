from datetime import datetime


class DrawDynamicDao(object):
    def __init__(self, db):
        self.table_name = 't_draw_dynamic'
        self.db = db

    def query_by_time(self, time, limit=2000, status=1):
        try:
            sql = ("SELECT * FROM " + self.table_name
                   + " where status = '" + str(status)
                   + "' and insert_time >= '" + time
                   + "' limit " + str(limit))
            data = self.db.select_db(sql)  # 用mysql_operate文件中的db的select_db方法进行查询
            return data
        except Exception as e:
            return {}

    def query_by_status(self, status=0, limit=2000, newest_first=False):
        try:
            if newest_first:
                order = (
                    " order by cast(substring_index(dyn_url, '/', -1) as unsigned) desc,"
                    " insert_time desc"
                )
            else:
                order = " order by insert_time"
            sql = (
                "SELECT * FROM " + self.table_name
                + " where status = '" + str(status) + "'" + order
            )
            if limit is not None and int(limit) > 0:
                sql = sql + " limit " + str(int(limit))
            return self.db.select_db(sql)
        except Exception:
            return {}

    def query_by_verification_priority(self, limit=30):
        """轮转验证队列：从未尝试的最先，其次最久未尝试的。"""
        limit = max(1, int(limit))
        sql = """
        SELECT d.*, c.update_time AS lottery_checked_at
        FROM t_draw_dynamic d
        LEFT JOIN t_scan_cache c
          ON c.scan_type = 'lottery' AND c.scan_url = d.dyn_url
        WHERE d.status IN ('0', '3')
        ORDER BY
          CASE WHEN c.update_time IS NULL THEN 0 ELSE 1 END,
          c.update_time ASC,
          CAST(SUBSTRING_INDEX(d.dyn_url, '/', -1) AS UNSIGNED) DESC
        LIMIT %s
        """ % limit
        try:
            return self.db.select_db(sql) or []
        except Exception:
            return []

    def query_by_dyn_url(self, dyn_url):
        try:
            sql = "SELECT * FROM " + self.table_name + " where dyn_url = '" + dyn_url + "'";
            data = self.db.select_db(sql)  # 用mysql_operate文件中的db的select_db方法进行查询
            return data
        except Exception as e:
            return {}

    def insert(self, dyn_url, source, note):
        try:
            params = {}
            params['dyn_url'] = str(dyn_url)
            params['source'] = str(source)
            params['status'] = '0'
            params['note'] = str(note)
            params['insert_time'] = str(datetime.now())
            self.db.insert(self.table_name, params)
            return True
        except Exception as e:
            return False

    def update_sharedUrl(self, url, status):
        try:
            params = {'status': str(status)}
            cond_dict = {'dyn_url': url}
            self.db.update(self.table_name, params, cond_dict)
        except Exception as e:
            print(e)
