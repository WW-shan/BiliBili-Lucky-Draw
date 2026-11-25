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

    def test_status_update_also_clears_duplicate_url_form(self):
        db = Mock()
        dao = DrawDynamicDao(db)

        dao.update_sharedUrl('https://www.bilibili.com/opus/1268647448530649174', 5)

        db.update.assert_called_once_with(
            't_draw_dynamic',
            {'status': '5'},
            {'dyn_url': 'https://www.bilibili.com/opus/1268647448530649174'},
        )
        update_sql, update_args = db.cur.execute.call_args.args
        self.assertIn('LIKE', update_sql)
        self.assertEqual(update_args, ('5', '%/1268647448530649174', 'https://www.bilibili.com/opus/1268647448530649174'))
        db.con.commit.assert_called_once()

    def test_query_by_dynamic_id_matches_both_url_forms(self):
        db = Mock()
        db.select_db.return_value = [{
            'dyn_url': 'https://t.bilibili.com/1268647448530649174',
            'status': '5',
        }]
        dao = DrawDynamicDao(db)

        row = dao.query_by_dynamic_id('1268647448530649174')

        self.assertEqual(row, db.select_db.return_value[0])
        sql = db.select_db.call_args.args[0].lower()
        self.assertIn('substring_index', sql)
        self.assertIn("'1268647448530649174'", sql)

    def test_successful_share_marks_all_url_forms_completed(self):
        db = Mock()
        dao = DrawDynamicDao(db)

        dao.mark_shared_by_dynamic_id(
            'https://www.bilibili.com/opus/1262216984824643604', '35549700'
        )

        db.con.ping.assert_called_once_with(reconnect=True)
        sql, args = db.cur.execute.call_args.args
        self.assertIn("status = '1'", sql)
        self.assertIn("t_share_info", sql)
        self.assertIn('substring_index', sql.lower())
        self.assertEqual(args, ('1262216984824643604', '1262216984824643604', '35549700'))

    def test_reset_excluded_to_pending_is_guarded_by_current_status(self):
        db = Mock()
        db.cur.rowcount = 1
        dao = DrawDynamicDao(db)

        rows = dao.reset_excluded_to_pending(
            'https://www.bilibili.com/opus/1271567562424975400'
        )

        self.assertEqual(rows, 1)
        sql,args = db.cur.execute.call_args.args
        self.assertIn("SET status = '0'", sql)
        self.assertIn("status = '4'", sql)
        self.assertEqual(args, ('https://www.bilibili.com/opus/1271567562424975400',))
        db.con.commit.assert_called_once()

    def test_verification_queue_counts_separate_cooldown_rows(self):
        db = Mock()
        db.select_db.return_value = [{'total': 5, 'eligible': 2}]
        dao = DrawDynamicDao(db)

        counts = dao.verification_queue_counts(retry_minutes=15)

        self.assertEqual(counts, {'total': 5, 'eligible': 2})
        sql = db.select_db.call_args.args[0].lower()
        self.assertIn("d.status in ('0', '3')", sql)
        self.assertIn("c.status <> '3'", sql)
        self.assertIn('interval 15 minute', sql)

    def test_verification_priority_is_fair_round_robin(self):
        db = Mock()
        db.select_db.return_value = [{'dyn_url': 'https://www.bilibili.com/opus/123'}]
        dao = DrawDynamicDao(db)

        rows = dao.query_by_verification_priority(limit=30)

        self.assertEqual(rows, db.select_db.return_value)
        sql = db.select_db.call_args.args[0].lower()
        self.assertIn("d.status in ('0', '3')", sql)
        self.assertIn('1250000000000000000', sql)
        self.assertIn('left join t_scan_cache', sql)
        self.assertIn("c.scan_type = 'lottery'", sql)
        self.assertIn('c.update_time is null', sql)
        self.assertIn('c.update_time asc', sql)
        self.assertIn('limit 30', sql)


if __name__ == '__main__':
    unittest.main()
