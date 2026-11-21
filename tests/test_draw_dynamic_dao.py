import unittest
from unittest.mock import Mock

from dao.draw_dynamic_dao import DrawDynamicDao


class DrawDynamicDaoTests(unittest.TestCase):
    def test_pending_queue_query_is_a_select_ordered_newest_first(self):
        db = Mock()
        db.select_db.return_value = [{'dyn_url': 'https://www.bilibili.com/opus/123'}]
        dao = DrawDynamicDao(db)

        rows = dao.query_by_status(status=0, limit=3, newest_first=True)

        self.assertEqual(rows, db.select_db.return_value)
        sql = db.select_db.call_args.args[0].lower()
        self.assertTrue(sql.startswith('select * from t_draw_dynamic'))
        self.assertIn("status = '0'", sql)
        self.assertIn(
            "order by cast(substring_index(dyn_url, '/', -1) as unsigned) desc, "
            'insert_time desc',
            sql,
        )
        self.assertIn('limit 3', sql)

    def test_verification_priority_is_fair_round_robin(self):
        db = Mock()
        db.select_db.return_value = [{'dyn_url': 'https://www.bilibili.com/opus/123'}]
        dao = DrawDynamicDao(db)

        rows = dao.query_by_verification_priority(limit=30)

        self.assertEqual(rows, db.select_db.return_value)
        sql = db.select_db.call_args.args[0].lower()
        self.assertIn("d.status in ('0', '3')", sql)
        self.assertIn('left join t_scan_cache', sql)
        self.assertIn("c.scan_type = 'lottery'", sql)
        self.assertIn('c.update_time is null', sql)
        self.assertIn('c.update_time asc', sql)
        self.assertIn('limit 30', sql)


if __name__ == '__main__':
    unittest.main()
