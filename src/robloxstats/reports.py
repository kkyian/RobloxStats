"""Completed calendar-week reports and conservative email delivery bookkeeping."""
import html
import os
import smtplib
import ssl
import time
from datetime import datetime, timedelta, time as clock_time
from email.message import EmailMessage
from .storage import week_start
from zoneinfo import ZoneInfo


class RetryableDeliveryError(Exception):
    """Failure known to have happened before the message was accepted."""


def message(payload, recipient, sender):
    msg = EmailMessage()
    msg['Subject'] = f"Roblox playtime · week of {payload['week']}"
    msg['From'], msg['To'] = sender, recipient
    msg['Message-ID'] = f"<robloxstats-{payload['week']}-{os.environ.get('ROBLOXSTATS_REPORT_ID', 'local')}@{sender.split('@')[-1]}>"
    rows = [(d['day'], d['formatted']) for d in payload['days']] + [('Weekly Total', payload['formatted'])]
    msg.set_content(f"Roblox playtime — week of {payload['week']} ({payload['timezone']})\n\n" +
                    '\n'.join(f'{day:15} {duration}' for day, duration in rows))
    body = ''.join(f'<tr><td style="padding:10px 18px">{html.escape(day)}</td><td>{duration}</td></tr>' for day, duration in rows)
    msg.add_alternative(f'<h2>Your week on Roblox</h2><p>Week of {payload["week"]} · {html.escape(payload["timezone"])}</p><table><tr><th>Day</th><th>Roblox playtime</th></tr>{body}</table><p>Connected in-game time recorded by your local tracker. Estimated boundaries may be included.</p>', subtype='html')
    return msg


def email_provider():
    return os.environ.get('EMAIL_PROVIDER', 'resend' if os.environ.get('RESEND_API_KEY') else 'smtp').lower()


def email_configured():
    if email_provider() == 'resend':
        from .resend_mail import configured
        return configured()
    return email_provider() == 'smtp' and bool(os.environ.get('SMTP_HOST') and os.environ.get('SMTP_FROM'))


def send(payload, recipient):
    if email_provider() == 'resend':
        from .resend_mail import send_report
        from resend.exceptions import ResendError
        try:
            return send_report(payload, recipient)
        except ResendError as exc:
            if str(exc.code) in {'400', '401', '403', '404', '422', '429'}:
                raise RetryableDeliveryError() from None
            raise RuntimeError('Resend delivery is uncertain.') from None
    if email_provider() != 'smtp':
        raise ValueError('EMAIL_PROVIDER must be smtp or resend.')
    return send_smtp(payload, recipient)


def send_smtp(payload, recipient):
    host, sender = os.environ.get('SMTP_HOST'), os.environ.get('SMTP_FROM')
    if not host or not sender:
        raise ValueError('Set SMTP_HOST and SMTP_FROM before enabling delivery.')
    mode = os.environ.get('SMTP_SECURITY', 'starttls')
    if mode not in {'starttls', 'ssl'}:
        raise ValueError('SMTP_SECURITY must be starttls or ssl.')
    port = int(os.environ.get('SMTP_PORT', '465' if mode == 'ssl' else '587'))
    msg = message(payload, recipient, sender)
    context = ssl.create_default_context()
    server = None
    try:
        if mode == 'ssl':
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        if mode == 'starttls':
            server.starttls(context=context)
        if os.environ.get('SMTP_USER'):
            server.login(os.environ['SMTP_USER'], os.environ.get('SMTP_PASSWORD', ''))
    except Exception as exc:
        if server:
            server.close()
        raise RetryableDeliveryError() from exc
    try:
        try:
            server.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
            raise RetryableDeliveryError() from exc
    finally:
        # A QUIT failure after acceptance must not turn a successful send into a retry.
        server.close()


def run_reports(store, now=None, sender=send):
    now = time.time() if now is None else now
    config = store.settings()
    tz = ZoneInfo(config['timezone'])
    local = datetime.fromtimestamp(now, tz)
    current = week_start(local.date())
    first = week_start(datetime.fromtimestamp(config['created_at'], tz).date())
    week = first
    while week < current:
        store.save_report(store.stats(week, config['timezone']))
        week += timedelta(days=7)
    if not config['emails_enabled']:
        return
    for report in reversed(store.reports()):
        if report['status'] != 'ready':
            continue
        week = datetime.fromisoformat(report['week']).date()
        due_day = week + timedelta(days=7 + config['report_day'])
        due = datetime.combine(due_day, clock_time.fromisoformat(config['report_time']), tz)
        if local < due:
            continue
        # Missing configuration is safely retryable. Other failures can occur after SMTP acceptance.
        if sender is send and not email_configured():
            store.report_state(report['week'], 'ready', error='Email provider is not configured. Set RESEND_API_KEY for Resend, or SMTP_HOST and SMTP_FROM for SMTP.')
            continue
        store.report_state(report['week'], 'sending', config['email'])
        try:
            sender(report['payload'], config['email'])
        except RetryableDeliveryError:
            store.report_state(report['week'], 'ready', config['email'],
                               'Email connection, authentication, or delivery was rejected. Will retry automatically.')
        except Exception:
            # Never persist exception text that might contain credentials or protocol payloads.
            store.report_state(report['week'], 'uncertain', config['email'],
                               'Email delivery failed or is uncertain. Check your email provider configuration and mailbox; automatic retry is disabled to avoid duplicates.')
        else:
            store.report_state(report['week'], 'sent', config['email'], sent_at=now)


def next_report(store, now=None):
    """Actual next automatic send, including installation week and already sent reports."""
    now = time.time() if now is None else now
    config = store.settings()
    if not config['emails_enabled']:
        return None
    tz = ZoneInfo(config['timezone'])
    first = week_start(datetime.fromtimestamp(config['created_at'], tz).date())
    finished = {r['week'] for r in store.reports() if r['status'] != 'ready'}
    week = first
    while week.isoformat() in finished:
        week += timedelta(days=7)
    due = datetime.combine(week + timedelta(days=7 + config['report_day']),
                           clock_time.fromisoformat(config['report_time']), tz)
    return {'at': due.isoformat(), 'week': week.isoformat(),
            'through': (week + timedelta(days=6)).isoformat(), 'overdue': due.timestamp() <= now}
