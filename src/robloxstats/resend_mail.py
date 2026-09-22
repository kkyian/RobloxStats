"""Resend transport and an explicit Hello World smoke-test command."""
import argparse
import hashlib
import json
import os
import uuid

import resend


def configured():
    key = os.environ.get('RESEND_API_KEY', '').strip()
    return bool(key and key != 're_xxxxxxxxx')


def send_email(params, *, idempotency_key=None):
    if not configured():
        raise ValueError('Replace re_xxxxxxxxx with your real API key in RESEND_API_KEY.')
    resend.api_key = os.environ['RESEND_API_KEY'].strip()
    # Weekly reports reuse a content key; each deliberate sample gets its own key.
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    result = resend.Emails.send(params, {'idempotency_key': idempotency_key or f'robloxstats/{digest}'})
    if not isinstance(result, dict) or not result.get('id'):
        raise RuntimeError('Resend returned an unconfirmed delivery result.')
    return result


def send_report(payload, recipient, *, manual=False):
    from .reports import message
    sender = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
    msg = message(payload, recipient, sender)
    subject = str(msg['Subject'])
    if manual:
        subject = 'RobloxStats — week so far · ' + payload['week']
    return send_email({'from': sender, 'to': [recipient], 'subject': subject,
                       'html': msg.get_body(preferencelist=('html',)).get_content(),
                       'text': msg.get_body(preferencelist=('plain',)).get_content()},
                      idempotency_key=f'robloxstats/manual-report/{uuid.uuid4()}' if manual else None)


def send_sample(recipient):
    return send_email({'from': os.environ.get('RESEND_FROM', 'onboarding@resend.dev'),
                       'to': [recipient], 'subject': 'RobloxStats — sample email',
                       'html': '<h2>Your RobloxStats email is working!</h2><p>This is a sample email. Your scheduled weekly recaps will include daily playtime and your weekly total.</p>',
                       'text': 'Your RobloxStats email is working! This is a sample email. Weekly recaps will include daily playtime and your weekly total.'},
                      idempotency_key=f'robloxstats/sample/{uuid.uuid4()}')


def main():
    parser = argparse.ArgumentParser(description='Send the Resend Hello World example (one real email)')
    parser.add_argument('--to', required=True, help='Recipient for this sample email')
    args = parser.parse_args()
    if not configured():
        parser.exit(1, 'Replace re_xxxxxxxxx with your real API key in RESEND_API_KEY.\n')
    try:
        result = send_email({'from': os.environ.get('RESEND_FROM', 'onboarding@resend.dev'),
                             'to': [args.to], 'subject': 'Hello World',
                             'html': '<p>Congrats on sending your <strong>first email</strong>!</p>'})
    except Exception:
        parser.exit(1, 'Resend did not confirm delivery. Check your API key, verified sender, and Resend dashboard before retrying.\n')
    print(f'Resend accepted the email. Email ID: {result["id"]}')


if __name__ == '__main__':
    main()
