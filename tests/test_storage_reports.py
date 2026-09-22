import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from robloxstats.storage import Store, DEFAULTS, pretty
from robloxstats.reports import run_reports, message
from robloxstats.app import InstanceLock


def ts(value):
    return datetime.fromisoformat(value).timestamp()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')

    def tearDown(self):
        self.store.db.close()

    def add(self, a, b):
        self.store.start('test', ts(a))
        self.store.stop(ts(b))

    def test_multiple_sessions_same_day(self):
        self.add('2026-09-21T10:00:00+08:00', '2026-09-21T11:00:00+08:00')
        self.add('2026-09-21T12:00:00+08:00', '2026-09-21T12:42:00+08:00')
        s = self.store.stats(date(2026,9,21), 'Asia/Singapore')
        self.assertEqual(s['days'][0]['formatted'], '1h 42m')
        self.assertEqual(s['total'], 6120)
        self.assertEqual(s['days'][1]['formatted'], '0m')

    def test_midnight_and_week_boundary(self):
        self.add('2026-09-27T23:40:00+08:00', '2026-09-28T00:30:00+08:00')
        self.assertEqual(self.store.stats(date(2026,9,21),'Asia/Singapore')['days'][6]['seconds'],1200)
        self.assertEqual(self.store.stats(date(2026,9,28),'Asia/Singapore')['days'][0]['seconds'],1800)

    def test_dst_spring_and_fall(self):
        for a,b,week,hours in [
            ('2026-03-08T00:00:00-05:00','2026-03-09T00:00:00-04:00',date(2026,3,2),23),
            ('2026-11-01T00:00:00-04:00','2026-11-02T00:00:00-05:00',date(2026,10,26),25)]:
            self.add(a,b)
            self.assertEqual(self.store.stats(week,'America/New_York')['total'],hours*3600)

    def test_overlaps_are_unioned(self):
        self.store.db.executemany('INSERT INTO sessions(source,start,end,last_seen) VALUES(?,?,?,?)', [('a',ts('2026-09-21T00:00:00Z'),ts('2026-09-21T02:00:00Z'),0),('b',ts('2026-09-21T01:00:00Z'),ts('2026-09-21T03:00:00Z'),0)])
        self.assertEqual(self.store.stats(date(2026,9,21),'UTC')['total'],10800)

    def test_only_one_active_session(self):
        self.store.start('a',100)
        self.store.start('b',110)
        self.assertEqual(len(self.store.sessions()),1)

    def test_invalid_settings(self):
        for update in [{'report_day':7},{'report_time':'25:00'},{'timezone':'Invalid'},{'emails_enabled':True},{'email':'bad\r\naddress'},{'emails_enabled':'true'}]:
            with self.assertRaises(ValueError):
                self.store.configure(DEFAULTS | update)

    def test_format(self):
        self.assertEqual(pretty(0),'0m')
        self.assertEqual(pretty(39900),'11h 05m')

    def report_setup(self):
        self.store.db.execute("UPDATE settings SET value=? WHERE key='created_at'", (str(ts('2026-09-21T00:00:00+08:00')),))
        self.store.db.commit()
        self.store.configure(DEFAULTS | {'email':'test@example.com','emails_enabled':True})
        self.add('2026-09-21T10:00:00+08:00','2026-09-21T11:15:00+08:00')

    def test_weekly_schedule_and_no_duplicate_send(self):
        self.report_setup()
        calls=[]
        sender=lambda *args: calls.append(args)
        run_reports(self.store,ts('2026-09-27T23:59:59+08:00'),sender)
        self.assertEqual(len(self.store.reports()),0)
        run_reports(self.store,ts('2026-09-28T08:59:59+08:00'),sender)
        self.assertEqual(len(calls),0)
        for _ in range(2):
            run_reports(self.store,ts('2026-09-28T09:00:00+08:00'),sender)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0]['formatted'],'1h 15m')
        self.assertEqual(self.store.reports()[0]['status'],'sent')

    def test_missed_schedule_catches_up(self):
        self.report_setup()
        calls=[]
        run_reports(self.store,ts('2026-10-07T12:00:00+08:00'),lambda *args: calls.append(args))
        self.assertEqual(len(calls),2)

    def test_uncertain_delivery_is_not_automatically_resent(self):
        self.report_setup()
        def fail(*args): raise OSError('secret protocol response')
        run_reports(self.store,ts('2026-09-28T09:00:00+08:00'),fail)
        self.assertEqual(self.store.reports()[0]['status'],'uncertain')
        calls=[]
        run_reports(self.store,ts('2026-09-28T09:01:00+08:00'),lambda *args:calls.append(args))
        self.assertEqual(calls,[])
        self.assertNotIn('secret',self.store.reports()[0]['error'])

    def test_email_disabled_still_archives_report(self):
        self.report_setup()
        self.store.configure(DEFAULTS)
        calls=[]
        run_reports(self.store,ts('2026-09-28T09:00:00+08:00'),lambda *args:calls.append(args))
        self.assertEqual(calls,[])
        self.assertEqual(self.store.reports()[0]['status'],'ready')

    def test_interrupted_send_recovers_as_uncertain(self):
        self.store.save_report(self.store.stats(date(2026,9,21),'UTC'))
        self.store.report_state('2026-09-21','sending')
        self.store.recover()
        self.assertEqual(self.store.reports()[0]['status'],'uncertain')

    def test_email_has_seven_days_and_total(self):
        payload=self.store.stats(date(2026,9,21),'UTC')
        msg=message(payload,'to@example.com','from@example.com')
        plain=msg.get_body(preferencelist=('plain',)).get_content()
        body=msg.get_body(preferencelist=('html',)).get_content()
        self.assertIn('Monday',plain)
        self.assertIn('Sunday',plain)
        self.assertIn('Weekly Total',plain)
        self.assertEqual(body.count('<tr>'),9)

    def test_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            a=InstanceLock(Path(directory)/'lock')
            try:
                with self.assertRaises(RuntimeError): InstanceLock(Path(directory)/'lock')
            finally: a.close()
            b=InstanceLock(Path(directory)/'lock')
            b.close()

    def test_known_pre_delivery_failure_retries(self):
        from robloxstats.reports import RetryableDeliveryError
        self.report_setup()
        def fail(*args): raise RetryableDeliveryError()
        run_reports(self.store,ts('2026-09-28T09:00:00+08:00'),fail)
        self.assertEqual(self.store.reports()[0]['status'],'ready')
        calls=[]
        run_reports(self.store,ts('2026-09-28T09:01:00+08:00'),lambda *args:calls.append(args))
        self.assertEqual(len(calls),1)
        self.assertEqual(self.store.reports()[0]['status'],'sent')

    def test_sent_report_snapshot_is_immutable(self):
        self.report_setup()
        run_reports(self.store,ts('2026-09-28T09:00:00+08:00'),lambda *args:None)
        self.store.configure(DEFAULTS | {'timezone':'UTC'})
        run_reports(self.store,ts('2026-09-29T09:00:00+08:00'),lambda *args:None)
        self.assertEqual(self.store.reports()[0]['payload']['timezone'],'Asia/Singapore')

    def test_next_report_dates_follow_completed_weeks(self):
        from robloxstats.reports import next_report
        self.report_setup()
        self.store.configure(DEFAULTS | {'email':'test@example.com','emails_enabled':True,'report_day':1,'report_time':'19:13'})
        upcoming=next_report(self.store,ts('2026-09-22T12:00:00+08:00'))
        self.assertEqual(upcoming['at'],'2026-09-29T19:13:00+08:00')
        self.assertEqual(upcoming['through'],'2026-09-27')
        self.assertFalse(upcoming['overdue'])
        self.assertTrue(next_report(self.store,ts('2026-09-30T12:00:00+08:00'))['overdue'])
        run_reports(self.store,ts('2026-09-29T19:13:00+08:00'),lambda *args:None)
        self.assertEqual(next_report(self.store)['week'],'2026-09-28')
        self.store.configure(DEFAULTS)
        self.assertIsNone(next_report(self.store))

    def test_email_activity_survives_restart_and_recovers_interrupted_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'activity.sqlite3'
            store=Store(path)
            accepted=store.log_email('sample','test@example.com')
            store.finish_email(accepted,'accepted','provider-id')
            store.log_email('weekly','test@example.com')
            store.db.close()
            store=Store(path)
            try:
                store.recover()
                rows=store.email_activity()
                self.assertEqual(rows[0]['status'],'uncertain')
                self.assertEqual(rows[1]['status'],'accepted')
                self.assertEqual(rows[1]['provider_id'],'provider-id')
            finally:
                store.db.close()
