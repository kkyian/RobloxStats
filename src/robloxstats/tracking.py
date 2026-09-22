"""Conservative desktop-client detection. Log messages are not a stable Roblox API."""
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil


@dataclass
class Event:
    at: float
    kind: str
    place: str | None = None


def parse(line):
    try:
        at = datetime.fromisoformat(line.split(',', 1)[0].replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        return None
    # Require engine log categories, not arbitrary experience Output messages.
    if '[FLog::SingleSurfaceApp]' in line:
        if any(s in line for s in ('leaveUGCGameInternal', 'returnToLuaApp:', 'setStage: (stage:LuaApp)', 'shutDown:')):
            return Event(at, 'end')
        if 'launchUGCGameInternal' in line:
            return Event(at, 'joining')
    if '[FLog::Network]' in line or '[DFLog::NetworkClient]' in line:
        if any(s in line for s in ('Connection lost:', 'Disconnection Notification.', 'Client:Disconnect', 'NetworkClient:Remove', 'Sending disconnect with reason:')):
            return Event(at, 'end')
        if 'Connection accepted from ' in line or 'serverId: ' in line:
            return Event(at, 'connected')
    if '[FLog::GameJoinLoadTime]' in line:
        match = re.search(r'placeid:(\d+)', line)
        if match:
            return Event(at, 'place', match[1])
    return None


def default_log_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Logs/Roblox'
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Roblox/logs'


@dataclass
class Client:
    identity: str
    path: Path


def discover(log_dir):
    """Associate an open Player log with a specific PID + creation time, never newest-file guesses."""
    clients, running, denied = [], False, False
    for p in psutil.process_iter(['name', 'create_time']):
        if (p.info['name'] or '').lower() not in {'robloxplayer', 'robloxplayerbeta.exe', 'robloxplayerbeta'}:
            continue
        running = True
        try:
            paths = [Path(f.path) for f in p.open_files()
                     if Path(f.path).parent.resolve() == log_dir.resolve()
                     and '_player_' in Path(f.path).name.lower() and f.path.endswith('.log')]
            if len(paths) == 1:
                clients.append(Client(f'{p.pid}:{p.info["create_time"]}', paths[0]))
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            denied = True
    return clients, running, denied


class Tail:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.inode = None
        self.connected = False
        self.place = None

    def read(self):
        stat = self.path.stat()
        reset = self.inode is not None and (stat.st_ino != self.inode or stat.st_size < self.offset)
        if reset:
            self.offset, self.connected, self.place = 0, False, None
        self.inode = stat.st_ino
        events = []
        with self.path.open('rb') as handle:
            handle.seek(self.offset)
            while True:
                pos = handle.tell()
                line = handle.readline()
                if not line or not line.endswith(b'\n'):
                    self.offset = pos  # Retry partial lines on the next poll.
                    break
                event = parse(line.decode('utf-8', errors='replace'))
                if event:
                    events.append(event)
                    if event.kind in ('end', 'joining'):
                        self.connected = False
                        if event.kind == 'joining':
                            self.place = None
                    elif event.kind == 'connected':
                        self.connected = True
                    elif event.kind == 'place':
                        self.place = event.place
        return events, reset


class Tracker:
    def __init__(self, store, log_dir):
        self.store, self.log_dir = store, log_dir
        self.tail = None
        self.source = None
        self.last_tick = None
        self.status = 'Starting tracker'
        store.recover()

    def drain_exit(self, now):
        """Use a flushed terminal event even if the process vanished between polls."""
        active = self.store.active()
        if self.tail is None or active is None:
            return
        try:
            events, reset = self.tail.read()
        except OSError:
            return
        if reset:
            return
        for event in events:
            if event.kind in ('end', 'joining') and active['start'] <= event.at <= now:
                self.store.stop(event.at, 'left_game')
                break

    def tick(self, now=None, discovery=None):
        now = time.time() if now is None else now
        clients, running, denied = discover(self.log_dir) if discovery is None else discovery
        gap = self.last_tick is not None and (now - self.last_tick > 15 or now < self.last_tick)
        if gap:
            self.store.stop(reason='sleep_or_monitoring_gap', estimated=True)
        self.last_tick = now
        if len(clients) != 1:
            if not clients and not running and not gap:
                self.drain_exit(now)
            self.store.stop(reason='process_exit_or_signal_unavailable', estimated=True)
            self.tail, self.source = None, None
            self.status = ('Multiple clients: tracking paused' if len(clients) > 1 else
                           'Log access unavailable: tracking paused' if denied or running else 'Roblox is closed')
            return
        client = clients[0]
        source = f'{client.identity}:{client.path}'
        fresh = source != self.source or self.tail is None
        if fresh:
            if not gap:
                self.drain_exit(now)
            self.store.stop(reason='client_changed', estimated=True)
            self.source, self.tail = source, Tail(client.path)
        previous_place = self.tail.place
        try:
            events, reset = self.tail.read()
        except OSError:
            self.store.stop(reason='log_unavailable', estimated=True)
            self.tail = None
            self.status = 'Log unavailable: tracking paused'
            return
        if fresh or gap or reset:
            if reset:
                self.store.stop(reason='log_rotated', estimated=True)
            # Snapshot of current process, never turn historical logs into new playtime.
            if self.tail.connected:
                self.store.start(source, now, self.tail.place, estimated=True)
        else:
            place = previous_place
            for event in events:
                # Bound clock changes/delayed writes to the observation window.
                at = min(now, max(now - 15, event.at))
                if event.kind == 'place':
                    place = event.place
                if event.kind == 'joining':
                    place = None
                if event.kind in ('end', 'joining'):
                    self.store.stop(at, 'left_game' if event.kind == 'end' else 'switching_game', at != event.at)
                elif event.kind == 'connected':
                    self.store.start(source, at, place, at != event.at)
        if self.tail.connected:
            self.store.heartbeat(now)
        self.status = 'In game · tracking' if self.store.active() else 'Roblox open · not in a game'
