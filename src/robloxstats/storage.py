"""Durable UTC intervals; calendar boundaries are calculated in the reporting zone."""
import json
import sqlite3
import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULTS = {"email": "", "emails_enabled": False, "report_day": 0,
            "report_time": "09:00", "timezone": "Asia/Singapore"}


def week_start(day):
    return day - timedelta(days=day.weekday())


def pretty(seconds):
    minutes = int(seconds // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL,
                start REAL NOT NULL, end REAL, last_seen REAL NOT NULL,
                place TEXT, reason TEXT, estimated INTEGER NOT NULL DEFAULT 0,
                CHECK(end IS NULL OR end >= start));
            CREATE UNIQUE INDEX IF NOT EXISTS one_active ON sessions((1)) WHERE end IS NULL;
            CREATE INDEX IF NOT EXISTS session_dates ON sessions(start, end);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS email_activity (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, recipient TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, provider_id TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS reports (
                week TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                recipient TEXT, error TEXT, sent_at REAL);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('created_at', ?)",
                            (json.dumps(datetime.now().timestamp()),))

    def settings(self):
        with self.lock:
            values = {r['key']: json.loads(r['value']) for r in self.db.execute('SELECT * FROM settings')}
        return DEFAULTS | values

    def configure(self, values):
        if set(values) != set(DEFAULTS):
            raise ValueError('All six configuration fields are required.')
        if type(values['emails_enabled']) is not bool or type(values['report_day']) is not int:
            raise ValueError('Invalid email switch or weekday.')
        if not 0 <= values['report_day'] <= 6:
            raise ValueError('Choose a weekday from Monday to Sunday.')
        if not isinstance(values['email'], str) or len(values['email']) > 254:
            raise ValueError('Invalid email address.')
        import re
        if values['email'] and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', values['email']):
            raise ValueError('Enter a valid email address.')
        if values['emails_enabled'] and not values['email']:
            raise ValueError('An email address is required to enable reports.')
        if not isinstance(values['report_time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', values['report_time']):
            raise ValueError('Use a 24-hour time, such as 09:00.')
        try:
            ZoneInfo(values['timezone'])
        except (KeyError, ValueError, TypeError):
            raise ValueError('Use an IANA time zone, such as Asia/Singapore.') from None
        with self.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                                [(k, json.dumps(v)) for k, v in values.items()])

    def active(self):
        with self.lock:
            row = self.db.execute('SELECT * FROM sessions WHERE end IS NULL').fetchone()
        return dict(row) if row else None

    def start(self, source, at, place=None, estimated=False):
        with self.lock, self.db:
            # Global active guard also prevents two Roblox clients counting twice.
            if self.active():
                return
            last = self.db.execute('SELECT MAX(end) FROM sessions').fetchone()[0]
            at = max(at, last or at)
            self.db.execute('INSERT INTO sessions(source,start,last_seen,place,estimated) VALUES(?,?,?,?,?)',
                            (source, at, at, place, int(estimated)))

    def heartbeat(self, at):
        with self.lock, self.db:
            self.db.execute('UPDATE sessions SET last_seen=MAX(start,last_seen,?) WHERE end IS NULL', (at,))

    def stop(self, at=None, reason='left_game', estimated=False):
        with self.lock, self.db:
            self.db.execute('''UPDATE sessions SET end=MAX(start,COALESCE(?,last_seen)),
                reason=?,estimated=MAX(estimated,?) WHERE end IS NULL''', (at, reason, int(estimated)))

    def recover(self):
        self.stop(reason='tracker_interrupted', estimated=True)
        with self.lock, self.db:
            self.db.execute("UPDATE email_activity SET status='uncertain', error='App restarted during sending. Check your mailbox before resending.' WHERE status='sending'")
        with self.lock, self.db:
            self.db.execute("UPDATE reports SET status='uncertain', error='Delivery was interrupted; check your mailbox before retrying externally.' WHERE status='sending'")

    def sessions(self, limit=100):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT *, COALESCE(end,last_seen)-start AS duration FROM sessions ORDER BY start DESC LIMIT ?', (limit,))]

    def stats(self, monday, zone):
        tz = ZoneInfo(zone)
        days = []
        with self.lock:
            for i in range(7):
                day = monday + timedelta(days=i)
                lo = datetime.combine(day, time(), tz).timestamp()
                hi = datetime.combine(day + timedelta(days=1), time(), tz).timestamp()
                intervals = self.db.execute('''SELECT start, COALESCE(end,last_seen) AS finish FROM sessions
                    WHERE start < ? AND COALESCE(end,last_seen) > ? ORDER BY start''', (hi, lo)).fetchall()
                # Union defensively, including imported/legacy overlapping intervals.
                total, cursor = 0, lo
                for row in intervals:
                    begin, end = max(lo, row['start'], cursor), min(hi, row['finish'])
                    total += max(0, end - begin)
                    cursor = max(cursor, end)
                days.append({'date': day.isoformat(), 'day': day.strftime('%A'), 'seconds': total, 'formatted': pretty(total)})
        total = sum(d['seconds'] for d in days)
        return {'week': monday.isoformat(), 'timezone': zone, 'days': days, 'total': total,
                'formatted': pretty(total), 'average': pretty(total / 7),
                'most_played': max(days, key=lambda d: d['seconds'])['day'] if total else '—'}

    def reports(self):
        with self.lock:
            return [dict(r) | {'payload': json.loads(r['payload'])} for r in
                    self.db.execute('SELECT * FROM reports ORDER BY week DESC')]

    def save_report(self, payload):
        with self.lock, self.db:
            self.db.execute("""INSERT INTO reports(week,payload,status) VALUES(?,?,'ready')
                            ON CONFLICT(week) DO UPDATE SET payload=excluded.payload WHERE reports.status='ready'""",
                            (payload['week'], json.dumps(payload)))

    def report_state(self, week, status, recipient=None, error=None, sent_at=None):
        with self.lock, self.db:
            self.db.execute('UPDATE reports SET status=?,recipient=?,error=?,sent_at=? WHERE week=?',
                            (status, recipient, error, sent_at, week))

    def log_email(self, kind, recipient):
        with self.lock, self.db:
            cursor = self.db.execute("INSERT INTO email_activity(kind,recipient,status,created_at) VALUES(?,?,'sending',?)",
                                     (kind, recipient, datetime.now().timestamp()))
            return cursor.lastrowid

    def finish_email(self, ident, status, provider_id=None, error=None):
        with self.lock, self.db:
            self.db.execute('UPDATE email_activity SET status=?,provider_id=?,error=? WHERE id=?',
                            (status, provider_id, error, ident))

    def email_activity(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM email_activity ORDER BY id DESC LIMIT 20')]
