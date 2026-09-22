import os
import unittest
from datetime import date
from unittest.mock import patch

from resend.exceptions import ResendError
from robloxstats import resend_mail
from robloxstats.reports import email_provider, email_configured, send, RetryableDeliveryError
from robloxstats.storage import Store


class ResendTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'EMAIL_PROVIDER':'resend','RESEND_API_KEY':'re_test_only'}, clear=True)
        self.env.start()
        self.store = Store(':memory:')
        self.payload = self.store.stats(date(2026,9,21),'UTC')

    def tearDown(self):
        self.store.db.close()
        self.env.stop()

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_weekly_payload_and_idempotency(self, api):
        api.return_value = {'id':'test-email'}
        send(self.payload, 'recipient@example.com')
        args = api.call_args.args
        self.assertEqual(args[0]['from'],'onboarding@resend.dev')
        self.assertEqual(args[0]['to'],['recipient@example.com'])
        self.assertIn('Monday',args[0]['html'])
        self.assertIn('Weekly Total',args[0]['text'])
        self.assertNotIn('re_test_only', str(args))
        send(self.payload, 'recipient@example.com')
        self.assertEqual(api.call_args.args[1],args[1])
        send(self.payload, 'another@example.com')
        self.assertNotEqual(api.call_args.args[1],args[1])

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_placeholder_prevents_network_call(self, api):
        for key in ['', 're_xxxxxxxxx']:
            os.environ['RESEND_API_KEY'] = key
            self.assertFalse(email_configured())
            with self.assertRaises(ValueError): resend_mail.send_email({})
        api.assert_not_called()

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_custom_sender(self, api):
        api.return_value={'id':'test-email'}
        os.environ['RESEND_FROM']='reports@example.com'
        send(self.payload,'recipient@example.com')
        self.assertEqual(api.call_args.args[0]['from'],'reports@example.com')

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_request_rejections_are_retryable_without_leaking_response(self, api):
        api.side_effect=ResendError(403,'validation_error','secret response','check key')
        with self.assertRaises(RetryableDeliveryError) as caught:
            send(self.payload,'recipient@example.com')
        self.assertNotIn('secret',str(caught.exception))

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_ambiguous_response_is_not_retryable(self, api):
        api.return_value={}
        with self.assertRaises(RuntimeError): send(self.payload,'recipient@example.com')
        api.side_effect=ResendError(500,'server_error','secret response','retry')
        with self.assertRaises(RuntimeError) as caught: send(self.payload,'recipient@example.com')
        self.assertNotIn('secret',str(caught.exception))

    @patch('robloxstats.reports.send_smtp')
    def test_explicit_smtp_override(self, smtp):
        os.environ['EMAIL_PROVIDER']='smtp'
        send(self.payload,'recipient@example.com')
        smtp.assert_called_once()
        del os.environ['EMAIL_PROVIDER']
        self.assertEqual(email_provider(),'resend')

    @patch('robloxstats.resend_mail.resend.Emails.send')
    @patch('sys.argv',['resend_mail','--to','recipient@example.com'])
    def test_hello_world_example(self, api):
        api.return_value={'id':'test-email'}
        with patch('builtins.print'):
            resend_mail.main()
        params=api.call_args.args[0]
        self.assertEqual(params['to'],['recipient@example.com'])
        self.assertEqual(params['subject'],'Hello World')
        self.assertEqual(params['html'],'<p>Congrats on sending your <strong>first email</strong>!</p>')

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_repeated_samples_get_distinct_delivery_keys(self, api):
        api.return_value = {'id': 'sample-id'}
        resend_mail.send_sample('recipient@example.com')
        resend_mail.send_sample('recipient@example.com')
        first, second = api.call_args_list
        self.assertEqual(first.args[0], second.args[0])
        self.assertNotEqual(first.args[1]['idempotency_key'], second.args[1]['idempotency_key'])
        self.assertTrue(first.args[1]['idempotency_key'].startswith('robloxstats/sample/'))

    @patch('robloxstats.resend_mail.resend.Emails.send')
    def test_repeated_manual_reports_have_fresh_keys_and_real_totals(self, api):
        api.return_value = {'id': 'manual-report'}
        resend_mail.send_report(self.payload, 'recipient@example.com', manual=True)
        resend_mail.send_report(self.payload, 'recipient@example.com', manual=True)
        first, second = api.call_args_list
        self.assertNotEqual(first.args[1], second.args[1])
        self.assertIn('week so far', first.args[0]['subject'])
        self.assertIn('Weekly Total', first.args[0]['text'])
        self.assertIn('Sunday', first.args[0]['html'])

    @patch('robloxstats.resend_mail.resend.Emails.send')
    @patch('sys.argv',['resend_mail'])
    def test_sample_cli_requires_an_explicit_recipient(self, api):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            resend_mail.main()
        self.assertEqual(failure.exception.code,2)
        api.assert_not_called()
