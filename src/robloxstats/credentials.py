"""Optional non-secret SMTP configuration with a macOS Keychain password."""
import json
import logging
import os
import subprocess
import sys

SMTP_FIELDS = {'SMTP_HOST', 'SMTP_PORT', 'SMTP_SECURITY', 'SMTP_FROM', 'SMTP_USER'}


def load_smtp_config(data_dir):
    provider = os.environ.get('EMAIL_PROVIDER', 'resend' if os.environ.get('RESEND_API_KEY') else 'smtp').lower()
    if provider != 'smtp':
        return
    path = data_dir / 'smtp.json'
    if not path.exists():
        return
    config = json.loads(path.read_text())
    if not isinstance(config, dict) or any(not isinstance(v, str) for v in config.values()):
        raise ValueError('SMTP configuration must contain string values.')
    if set(config) - SMTP_FIELDS - {'keychain_service'}:
        raise ValueError('SMTP configuration contains unsupported fields; passwords belong in Keychain or the environment.')
    for key in SMTP_FIELDS:
        if key in config:
            os.environ.setdefault(key, config[key])
    service = config.get('keychain_service')
    if (service and sys.platform == 'darwin' and not os.environ.get('SMTP_PASSWORD')
            and os.environ.get('SMTP_USER') == config.get('SMTP_USER')):
        result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', service,
                                 '-a', config['SMTP_USER'], '-w'], capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            os.environ['SMTP_PASSWORD'] = result.stdout.rstrip('\n')
        else:
            logging.getLogger(__name__).warning('SMTP password unavailable in Keychain; email requires credential setup.')


def main():
    import argparse
    import getpass
    import smtplib
    import ssl
    from pathlib import Path

    parser = argparse.ArgumentParser(description='Verify Gmail SMTP and save its app password to macOS Keychain')
    parser.add_argument('--gmail', required=True, help='Full Gmail sender address')
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.robloxstats')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Keychain setup requires macOS; use SMTP environment variables on other platforms.')
    password = getpass.getpass('New Gmail app password (hidden): ').replace(' ', '')
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(args.gmail, password)
    except smtplib.SMTPAuthenticationError:
        raise SystemExit('Gmail rejected the password. Nothing was saved. Create an app password for this same Google account.') from None
    except Exception:
        raise SystemExit('Could not verify Gmail SMTP. Nothing was saved. Check connectivity and try again.') from None
    result = subprocess.run(['/usr/bin/security', 'add-generic-password', '-U', '-s', 'RobloxStats SMTP',
                             '-a', args.gmail, '-w', password], capture_output=True)
    if result.returncode:
        raise SystemExit('Gmail authentication succeeded, but the password could not be saved to Keychain.')
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {'SMTP_HOST':'smtp.gmail.com', 'SMTP_PORT':'587', 'SMTP_SECURITY':'starttls',
              'SMTP_FROM':args.gmail, 'SMTP_USER':args.gmail, 'keychain_service':'RobloxStats SMTP'}
    fd = os.open(args.data_dir/'smtp.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(config, handle, indent=2)
    print('Gmail verified. Password saved to Keychain. Restart RobloxStats to apply. No email was sent.')


if __name__ == '__main__':
    main()
