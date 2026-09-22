import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from robloxstats.app import make_handler
from robloxstats.storage import Store, DEFAULTS


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(':memory:')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
        cls.port = cls.server.server_port
        cls.server.RequestHandlerClass = make_handler(cls.store, SimpleNamespace(status='Roblox is closed'), cls.port)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()
        cls.store.db.close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request(method,path,body,headers or {})
        response=conn.getresponse()
        status, data, headers=response.status,response.read(),dict(response.getheaders())
        conn.close()
        return status, data, headers

    def test_dashboard_and_assets(self):
        for path in ['/', '/app.js', '/style.css', '/api/dashboard']:
            code, data, headers=self.request('GET',path)
            self.assertEqual(code,200)
            self.assertIn('Content-Security-Policy',headers)
            self.assertTrue(data)

    def test_host_validation(self):
        self.assertEqual(self.request('GET','/api/dashboard',headers={'Host':'attacker.example'})[0],403)

    def test_settings_csrf_and_origin(self):
        payload=json.dumps(DEFAULTS)
        self.assertEqual(self.request('POST','/api/settings',payload)[0],403)
        token=json.loads(self.request('GET','/api/dashboard')[1])['token']
        headers={'X-CSRF-Token':token,'Origin':'https://attacker.example'}
        self.assertEqual(self.request('POST','/api/settings',payload,headers)[0],403)
        headers['Origin']=f'http://127.0.0.1:{self.port}'
        self.assertEqual(self.request('POST','/api/settings',payload,headers)[0],200)
        self.assertEqual(self.request('POST','/api/settings','{}',headers)[0],400)
        self.assertEqual(self.request('POST','/api/settings','x'*4097,headers)[0],400)

    def test_bad_week_and_missing_paths(self):
        self.assertEqual(self.request('GET','/api/dashboard?week=invalid')[0],400)
        self.assertEqual(self.request('GET','/unknown')[0],404)
        self.assertEqual(self.request('GET','/api/dashboard?report=2000-01-01')[0],404)

    def test_saved_report_uses_its_snapshot(self):
        from datetime import date
        payload=self.store.stats(date(2000,1,3),'UTC')
        self.store.save_report(payload)
        result=json.loads(self.request('GET','/api/dashboard?report=2000-01-03')[1])
        self.assertEqual(result['stats']['timezone'],'UTC')
        self.assertEqual(result['stats']['week'],'2000-01-03')

    def test_sample_email_auth_validation_and_missing_configuration(self):
        from unittest.mock import patch
        payload=json.dumps({'email':'test@example.com'})
        self.assertEqual(self.request('POST','/api/email/sample',payload)[0],403)
        token=json.loads(self.request('GET','/api/dashboard')[1])['token']
        headers={'X-CSRF-Token':token}
        self.assertEqual(self.request('POST','/api/email/sample',json.dumps({'email':'bad'}),headers)[0],400)
        with patch('robloxstats.app.email_configured',return_value=False):
            self.assertEqual(self.request('POST','/api/email/sample',payload,headers)[0],400)

    def test_sample_email_sends_without_changing_preferences_and_throttles(self):
        from unittest.mock import patch
        token=json.loads(self.request('GET','/api/dashboard')[1])['token']
        headers={'X-CSRF-Token':token}
        before=self.store.settings()
        payload=json.dumps({'email':'sample@example.com'})
        with patch('robloxstats.app.email_provider',return_value='resend'), patch('robloxstats.app.email_configured',return_value=True), patch('robloxstats.resend_mail.send_sample',return_value={'id':'sample-id'}) as send:
            self.assertEqual(self.request('POST','/api/email/sample',payload,headers)[0],200)
            send.assert_called_once_with('sample@example.com')
            self.assertEqual(self.request('POST','/api/email/sample',payload,headers)[0],429)
        self.assertEqual(self.store.settings(),before)

    def test_weekly_manual_report_is_repeatable_without_marking_schedule_sent(self):
        from unittest.mock import patch
        token=json.loads(self.request('GET','/api/dashboard')[1])['token']
        headers={'X-CSRF-Token':token}
        payload=json.dumps({'email':'recipient@example.com'})
        before=self.store.reports()
        self.assertEqual(self.request('POST','/api/email/weekly',payload)[0],403)
        with patch('robloxstats.app.email_provider',return_value='resend'), patch('robloxstats.app.email_configured',return_value=True), patch('robloxstats.resend_mail.send_report',return_value={'id':'weekly-id'}) as send, patch('robloxstats.app.time.monotonic',return_value=10**12) as clock:
            self.assertEqual(self.request('POST','/api/email/weekly',payload,headers)[0],200)
            report,recipient=send.call_args.args
            self.assertEqual(len(report['days']),7)
            self.assertEqual(recipient,'recipient@example.com')
            self.assertTrue(send.call_args.kwargs['manual'])
            self.assertEqual(self.request('POST','/api/email/weekly',payload,headers)[0],429)
            clock.return_value+=31
            self.assertEqual(self.request('POST','/api/email/weekly',payload,headers)[0],200)
            self.assertEqual(send.call_count,2)
        self.assertEqual(self.store.reports(),before)

    def test_csv_export_includes_all_sessions_without_local_paths(self):
        import csv
        import io
        for i in range(105):
            self.store.start('/private/user/log',100+i*10,place='=1+1')
            self.store.stop(105+i*10)
        try:
            code,body,headers=self.request('GET','/api/sessions.csv')
            self.assertEqual(code,200)
            self.assertIn('attachment',headers['Content-Disposition'])
            rows=list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
            self.assertEqual(len(rows),106)
            self.assertEqual(rows[1][1],"'=1+1")
            self.assertEqual(rows[1][4],'5.0')
            self.assertNotIn('/private/user/log',body.decode())
            self.assertEqual(self.request('GET','/api/sessions.csv',headers={'Host':'attacker.example'})[0],403)
        finally:
            self.store.db.execute('DELETE FROM sessions')
            self.store.db.commit()

    def test_dashboard_includes_report_and_comparison_metadata(self):
        result=json.loads(self.request('GET','/api/dashboard')[1])
        for key in ['next_report','email_activity','email_sender','email_cooldown','previous_stats','current_stats']:
            self.assertIn(key,result)
        self.assertEqual(len(result['previous_stats']['days']),7)
