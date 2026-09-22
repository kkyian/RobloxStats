import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from robloxstats.credentials import load_smtp_config


class CredentialsTests(unittest.TestCase):
    def test_keychain_loads_without_storing_password_in_config(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch('robloxstats.credentials.sys.platform', 'darwin'), patch('robloxstats.credentials.subprocess.run') as run:
            path = Path(directory)
            (path/'smtp.json').write_text(json.dumps({'SMTP_HOST':'smtp.example.com','SMTP_USER':'test@example.com','keychain_service':'test-service'}))
            run.return_value = SimpleNamespace(returncode=0, stdout='test-only-secret\n')
            load_smtp_config(path)
            self.assertEqual(os.environ['SMTP_PASSWORD'], 'test-only-secret')
            self.assertNotIn('test-only-secret', (path/'smtp.json').read_text())
            self.assertNotIn('test-only-secret', str(run.call_args))

    def test_environment_password_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SMTP_PASSWORD':'override'}, clear=True), patch('robloxstats.credentials.subprocess.run') as run:
            path=Path(directory)
            (path/'smtp.json').write_text(json.dumps({'SMTP_USER':'test@example.com','keychain_service':'test-service'}))
            load_smtp_config(path)
            self.assertEqual(os.environ['SMTP_PASSWORD'],'override')
            run.assert_not_called()

    def test_plaintext_password_in_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'smtp.json').write_text(json.dumps({'SMTP_PASSWORD':'not-allowed'}))
            with self.assertRaises(ValueError): load_smtp_config(path)
