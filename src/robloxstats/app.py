import argparse
import csv
import io
import json
import logging
import os
import re
import secrets
import signal
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .reports import run_reports, email_provider, email_configured, next_report
from .credentials import load_smtp_config
from .storage import Store, week_start
from .tracking import Tracker, default_log_dir
from .timer import Timer

LOG = logging.getLogger(__name__)


class InstanceLock:
    def __init__(self, path):
        self.handle = open(path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                self.handle.write(b'0')
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError('A tracker is already running with this data directory.') from None

    def close(self):
        self.handle.close()


def make_handler(store, tracker, port, timer=None):
    timer = timer or Timer(store, native=False)
    sample_lock = threading.Lock()
    sample_last_attempt = [float('-inf')]
    token = secrets.token_urlsafe(32)
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    static = Path(__file__).parent / 'static'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value, kind='application/json'):
            raw = json.dumps(value).encode() if kind == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if kind == 'text/csv':
                self.send_header('Content-Disposition', 'attachment; filename=robloxstats-sessions.csv')
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            if self.headers.get('Host') not in allowed_hosts:
                self.reply(403, {'error': 'Invalid host'})
                return False
            return True

        def do_GET(self):
            if not self.valid_host():
                return
            parsed = urlsplit(self.path)
            if parsed.path == '/api/sessions.csv':
                output = io.StringIO(newline='')
                writer = csv.writer(output)
                writer.writerow(['Session ID','Place ID','Start (UTC)','End (UTC)','Seconds','Estimated','End reason'])
                for row in store.sessions(-1):
                    def safe(value):
                        text = str(value or '')
                        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
                    writer.writerow([row['id'], safe(row['place']),
                        datetime.fromtimestamp(row['start'], timezone.utc).isoformat(),
                        datetime.fromtimestamp(row['end'], timezone.utc).isoformat() if row['end'] is not None else '',
                        round(row['duration'], 3), bool(row['estimated']), safe(row['reason'])])
                return self.reply(200, output.getvalue().encode('utf-8-sig'), 'text/csv')
            if parsed.path == '/api/dashboard':
                config = store.settings()
                today = datetime.now(ZoneInfo(config['timezone'])).date()
                try:
                    requested = parse_qs(parsed.query).get('week', [today.isoformat()])[0]
                    monday = week_start(date.fromisoformat(requested))
                    stats = store.stats(monday, config['timezone'])
                    report_week = parse_qs(parsed.query).get('report', [None])[0]
                    if report_week:
                        saved = next((r for r in store.reports() if r['week'] == report_week), None)
                        if not saved:
                            return self.reply(404, {'error': 'Report not found'})
                        stats = saved['payload']
                except (ValueError, OverflowError):
                    return self.reply(400, {'error': 'Invalid week'})
                current = store.stats(week_start(today), config['timezone'])
                previous = store.stats(week_start(today) - timedelta(days=7), config['timezone'])
                self.reply(200, {'stats': stats, 'today': current['days'][today.weekday()],
                                 'current_total': current['formatted'], 'current_stats': current, 'previous_stats': previous,
                                 'next_report': next_report(store), 'email_activity': store.email_activity(),
                                 'email_sender': os.environ.get('RESEND_FROM', 'onboarding@resend.dev') if email_provider() == 'resend' else os.environ.get('SMTP_FROM', ''),
                                 'email_cooldown': max(0, 30 - (time.monotonic() - sample_last_attempt[0])), 'sessions': store.sessions(),
                                 'reports': store.reports(), 'settings': {k: v for k, v in config.items() if k != 'created_at'},
                                 'timer': timer.snapshot(), 'status': tracker.status, 'active': store.active(), 'token': token,
                                 'email_provider': email_provider(), 'email_configured': email_configured()})
            elif parsed.path in {'/', '/app.js', '/style.css'}:
                name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[parsed.path]
                kind = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}[parsed.path]
                self.reply(200, (static / name).read_bytes(), kind)
            else:
                self.reply(404, {'error': 'Not found'})

        def send_sample(self, payload, *, weekly=False):
            recipient = payload.get('email', '')
            if not isinstance(recipient, str) or len(recipient) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', recipient):
                return self.reply(400, {'error': 'Enter a valid recipient email address first.'})
            if email_provider() != 'resend' or not email_configured():
                return self.reply(400, {'error': 'Configure RESEND_API_KEY and start the app with EMAIL_PROVIDER=resend first.'})
            if not sample_lock.acquire(blocking=False):
                return self.reply(429, {'error': 'An email is already being sent.'})
            try:
                if time.monotonic() - sample_last_attempt[0] < 30:
                    return self.reply(429, {'error': 'Please wait 30 seconds before sending another email.'})
                sample_last_attempt[0] = time.monotonic()
                activity_id = store.log_email('weekly' if weekly else 'sample', recipient)
                from .resend_mail import send_sample, send_report
                from resend.exceptions import ResendError
                try:
                    if weekly:
                        zone = store.settings()['timezone']
                        today = datetime.now(ZoneInfo(zone)).date()
                        report = store.stats(week_start(today), zone)
                        result = send_report(report, recipient, manual=True)
                    else:
                        result = send_sample(recipient)
                except ResendError as exc:
                    errors = {'401': 'Resend rejected the API key. Update it and restart the tracker.',
                              '403': 'Resend rejected this sender or recipient. With onboarding@resend.dev, use your Resend account email; otherwise use a verified domain.',
                              '429': 'Resend rate limit reached. Please try again later.'}
                    error = errors.get(str(exc.code), 'Resend did not confirm delivery. Check your Resend dashboard before retrying.')
                    status = 'rejected' if str(exc.code) in {'400','401','403','404','422','429'} else 'uncertain'
                    store.finish_email(activity_id, status, error=error)
                    return self.reply(502, {'error': error})
                except Exception:
                    store.finish_email(activity_id, 'uncertain', error='Delivery could not be confirmed. Check your provider before resending.')
                    return self.reply(502, {'error': 'Delivery could not be confirmed. Check your Resend dashboard before retrying.'})
                store.finish_email(activity_id, 'accepted', provider_id=result['id'])
                kind = 'weekly report (week so far)' if weekly else 'sample'
                return self.reply(200, {'message': f'Resend accepted the {kind} for {recipient}. Check your inbox or spam folder.', 'id': result['id']})
            finally:
                sample_lock.release()

        def do_POST(self):
            if not self.valid_host():
                return
            origin = self.headers.get('Origin')
            if (origin and origin not in {f'http://{h}' for h in allowed_hosts}) or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), token):
                return self.reply(403, {'error': 'Invalid request token'})
            if self.path not in {'/api/settings', '/api/timer', '/api/email/sample', '/api/email/weekly'}:
                return self.reply(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 4096:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Expected configuration object')
                if self.path in {'/api/email/sample', '/api/email/weekly'}:
                    return self.send_sample(payload, weekly=self.path == '/api/email/weekly')
                if self.path == '/api/timer':
                    result = timer.action(payload.get('action'), payload.get('id'), payload.get('minutes'))
                    return self.reply(200, result)
                store.configure(payload)
            except (ValueError, TypeError) as exc:
                return self.reply(400, {'error': str(exc)})
            self.reply(200, {'saved': True})
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Local Roblox playtime tracker and dashboard')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.robloxstats')
    parser.add_argument('--log-dir', type=Path, default=default_log_dir())
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    load_smtp_config(args.data_dir)
    lock = InstanceLock(args.data_dir / 'tracker.lock')
    store = Store(args.data_dir / 'playtime.sqlite3')
    tracker = Tracker(store, args.log_dir.expanduser())
    timer = Timer(store)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(store, tracker, args.port, timer))
    server.daemon_threads = True
    stop = threading.Event()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    def monitor():
        while not stop.is_set():
            try:
                tracker.tick()
            except Exception:
                store.stop(reason='monitor_error', estimated=True)
                tracker.status = 'Monitor error: tracking paused; retrying'
                LOG.exception('Monitor failed')
            stop.wait(2)

    def timer_worker():
        while not stop.is_set():
            try:
                timer.tick()
            except Exception:
                LOG.exception('Timer failed; will retry')
            stop.wait(0.5)

    def reports():
        while not stop.is_set():
            try:
                run_reports(store)
            except Exception:
                LOG.exception('Report scheduler failed')
            stop.wait(30)

    workers = [threading.Thread(target=monitor, daemon=True), threading.Thread(target=reports, daemon=True),
               threading.Thread(target=server.serve_forever, daemon=True),
               threading.Thread(target=timer_worker, daemon=True)]
    for worker in workers:
        worker.start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    url = f'http://127.0.0.1:{args.port}'
    print(f'RobloxStats dashboard: {url}\nDatabase: {args.data_dir / "playtime.sqlite3"}', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        stop.wait()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        workers[0].join(timeout=10)
        store.stop(reason='tracker_stopped', estimated=True)
        with timer.lock:
            timer.dismiss_popup()
        lock.close()


if __name__ == '__main__':
    main()
