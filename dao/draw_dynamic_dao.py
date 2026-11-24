from datetime import datetime
import re


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
          AND CAST(SUBSTRING_INDEX(d.dyn_url, '/', -1) AS UNSIGNED)
              >= 1250000000000000000
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

    def verification_queue_counts(self, retry_minutes=15):
        """Return total modern pending rows and rows eligible now."""
        retry_minutes = max(0, int(retry_minutes))
        sql = """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(
                   CASE
                     WHEN c.scan_key IS NULL OR c.status <> '3'
                          OR COALESCE(c.update_time, c.insert_time)
                             <= NOW() - INTERVAL %s MINUTE
                     THEN 1 ELSE 0
                   END
               ), 0) AS eligible
        FROM t_draw_dynamic d
        LEFT JOIN t_scan_cache c
          ON c.scan_type = 'lottery' AND c.scan_url = d.dyn_url
        WHERE d.status IN ('0', '3')
          AND CAST(SUBSTRING_INDEX(d.dyn_url, '/', -1) AS UNSIGNED)
              >= 1250000000000000000
        """ % retry_minutes
        try:
            rows = self.db.select_db(sql) or []
            row = rows[0] if rows else {}
            total = int(row.get('total') or 0)
            eligible = min(total, int(row.get('eligible') or 0))
            return {'total': total, 'eligible': eligible}
        except Exception:
            return {'total': 0, 'eligible': 0}

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

    def reset_excluded_to_pending(self, url):
        """Re-open one explicitly audited excluded row as pending."""
        try:
            self.db.con.ping(reconnect=True)
            self.db.cur.execute(
                "UPDATE " + self.table_name
                + " SET status = '0' WHERE status = '4' AND dyn_url = %s",
                (str(url),),
            )
            rows = self.db.cur.rowcount
            self.db.con.commit()
            return rows
        except Exception:
            try:
                self.db.con.rollback()
            except Exception:
                pass
            raise

    def mark_shared_by_dynamic_id(self, url, user_id):
        """Mark every URL form completed when the user has a share record."""
        match = re.search(r'/(\d+)(?:\?|$)', str(url))
        if not match:
            return 0
        dynamic_id = match.group(1)
        self.db.con.ping(reconnect=True)
        self.db.cur.execute(
            "UPDATE " + self.table_name
            + " SET status = '1' WHERE SUBSTRING_INDEX(SUBSTRING_INDEX(dyn_url, '/', -1), '?', 1) = %s"
            + " AND EXISTS (SELECT 1 FROM t_share_info s"
            + " WHERE SUBSTRING_INDEX(SUBSTRING_INDEX(s.share_url, '/', -1), '?', 1) = %s"
            + " AND s.user_id = %s)",
            (dynamic_id, dynamic_id, str(user_id)),
        )
        rows = self.db.cur.rowcount
        self.db.con.commit()
        return rows

    def update_sharedUrl(self, url, status):
        try:
            params = {'status': str(status)}
            cond_dict = {'dyn_url': url}
            self.db.update(self.table_name, params, cond_dict)
            # The same dynamic can be discovered from both t.bilibili.com and
            # www.bilibili.com/opus. Keep both URL variants in sync; otherwise
            # the stale variant remains in status 0 and the worker repeats the
            # same detail request forever.
            match = re.search(r'/(\d+)(?:\?|$)', str(url))
            if match:
                dynamic_id = match.group(1)
                self.db.con.ping(reconnect=True)
                self.db.cur.execute(
                    "UPDATE " + self.table_name
                    + " SET status = %s WHERE dyn_url LIKE %s AND dyn_url <> %s",
                    (str(status), '%/' + dynamic_id, str(url)),
                )
                self.db.con.commit()
        except Exception as e:
            print(e)
