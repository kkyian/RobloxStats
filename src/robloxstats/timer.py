"""Persistent wall-clock countdown, one snooze, and local Roblox-only termination."""
import json
import subprocess
import sys
import threading
import time
import uuid

import psutil

PLAYER_NAMES = {'robloxplayer', 'robloxplayerbeta', 'robloxplayerbeta.exe'}


def quit_roblox():
    """Only stop this user's Roblox players; never Studio or unrelated processes."""
    owner = psutil.Process().username()
    targets = []
    for process in psutil.process_iter(['name', 'username']):
        if (process.info['name'] or '').lower() not in PLAYER_NAMES or process.info['username'] != owner:
            continue
        try:
            process.terminate()
            targets.append(process)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(targets, timeout=3)
    for process in alive:
        try:
            process.kill()  # psutil checks identity to guard against PID reuse.
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    if alive:
        raise RuntimeError('Roblox did not close. Try Exit Roblox again.')


class Timer:
    def __init__(self, store, quitter=quit_roblox, native=True):
        self.store, self.quitter = store, quitter
        self.lock = threading.RLock()
        self.native = native and sys.platform == 'darwin'
        self.popup = None
        self.popup_id = None
        with store.lock, store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS timer (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
            row = store.db.execute('SELECT value FROM timer WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else {'id': '', 'status': 'idle', 'deadline': None, 'snoozed': False, 'error': ''}
        # An interrupted exit must be retried, not treated as successful.
        if self.state['status'] == 'exiting':
            self.state['status'] = 'expired'
            self.state['snoozed'] = True

    def save(self):
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO timer VALUES(1,?)', (json.dumps(self.state),))

    def snapshot(self, now=None):
        with self.lock:
            now = time.time() if now is None else now
            return self.state | {'remaining': max(0, (self.state['deadline'] or now) - now), 'native_popup': self.native}

    def dismiss_popup(self):
        if self.popup is not None:
            if self.popup.poll() is None:
                self.popup.terminate()
            self.popup.communicate(timeout=3)
            self.popup = None

    def action(self, action, timer_id=None, minutes=None, now=None):
        now = time.time() if now is None else now
        with self.lock:
            status = self.state['status']
            if action == 'start':
                if status not in {'idle', 'finished', 'cancelled'}:
                    raise ValueError('A timer is already active.')
                if type(minutes) is not int or not 1 <= minutes <= 1440:
                    raise ValueError('Choose a whole number from 1 to 1440 minutes.')
                self.state = {'id': uuid.uuid4().hex, 'status': 'running', 'deadline': now + minutes * 60,
                              'snoozed': False, 'error': '', 'duration': minutes * 60}
            else:
                if timer_id != self.state['id']:
                    raise ValueError('This timer has changed. Refresh and try again.')
                if action == 'cancel':
                    if status != 'running' or now >= self.state['deadline'] or self.state['snoozed']:
                        raise ValueError('An expired or snoozed timer cannot be cancelled.')
                    self.state['status'] = 'cancelled'
                elif action == 'snooze':
                    if status != 'expired' or self.state['snoozed']:
                        raise ValueError('Only one two-minute snooze is allowed.')
                    self.state.update(status='running', snoozed=True, deadline=now + 120, duration=120, error='')
                elif action == 'exit':
                    if status != 'expired':
                        raise ValueError('The timer is not awaiting an exit.')
                    self.state.update(status='exiting', error='')
                else:
                    raise ValueError('Unknown timer action.')
            self.save()
            self.dismiss_popup()
            if action == 'exit':
                try:
                    self.quitter()
                except Exception:
                    self.state.update(status='expired', snoozed=True,
                                      error='Unable to close Roblox. Check permissions and try Exit Roblox again.')
                else:
                    self.state.update(status='finished', error='')
                self.save()
            return self.snapshot(now)

    def tick(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if self.state['status'] == 'running' and now >= self.state['deadline']:
                self.state['status'] = 'expired'
                self.save()
                if self.state['snoozed']:
                    self.action('exit', self.state['id'], now=now)
                    return
            if self.state['status'] != 'expired' or not self.native:
                return
            if self.popup is not None:
                if self.popup.poll() is None:
                    return
                output, _ = self.popup.communicate()
                failed = self.popup.returncode != 0
                popup_id = self.popup_id
                self.popup = None
                if failed:
                    self.native = False
                    self.state['error'] = 'Desktop popup unavailable. Use this dashboard to snooze or exit Roblox.'
                    self.save()
                    return
                if popup_id == self.state['id']:
                    if 'Snooze once for 2 minutes' in output and not self.state['snoozed']:
                        self.action('snooze', popup_id, now=now)
                        return
                    if 'Exit Roblox' in output:
                        self.action('exit', popup_id, now=now)
                        return
            buttons = '{"Exit Roblox"}' if self.state['snoozed'] else '{"Snooze once for 2 minutes", "Exit Roblox"}'
            script = ('activate\n'
                      'display dialog "Your Roblox timer is done. Take a break. The two-minute snooze can only be used once; Roblox will close automatically when it ends." '
                      'with title "RobloxStats — Time is up" buttons ' + buttons +
                      ' default button "Exit Roblox" with icon caution')
            try:
                self.popup = subprocess.Popen(['/usr/bin/osascript', '-e', script], stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, text=True)
                self.popup_id = self.state['id']
            except OSError:
                self.native = False  # Dashboard dialog remains available.
