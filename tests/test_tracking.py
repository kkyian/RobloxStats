import tempfile
import unittest
from pathlib import Path
from robloxstats.storage import Store
from robloxstats.tracking import Client, Tracker, Tail, parse


def line(at, category, text):
    from datetime import datetime, timezone
    return f'{datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00","Z")},1,1,7 [{category}] {text}\n'


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'sample_Player_abc.log'
        self.path.touch()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.tracker = Tracker(self.store, Path(self.tmp.name))
        self.client = Client('123:1', self.path)
        self.tick(1000)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def tick(self, at, clients=None):
        self.tracker.tick(at, ([self.client] if clients is None else clients, bool(clients is None or clients), False))

    def append(self, at, text, category='FLog::Network'):
        with self.path.open('a') as f:
            f.write(line(at, category, text))

    def join(self, at=1001):
        self.append(at, 'Connection accepted from example')
        self.tick(at+1)

    def test_home_and_join_attempt_are_not_playtime(self):
        self.append(1001, 'launchUGCGameInternal', 'FLog::SingleSurfaceApp')
        self.append(1002, 'Report game_join_loadtime: placeid:123,', 'FLog::GameJoinLoadTime')
        self.tick(1003)
        self.assertIsNone(self.store.active())

    def test_connection_duplicates_and_exact_exit(self):
        self.join()
        self.append(1003, 'serverId: example')
        self.tick(1004)
        self.append(1005.125, 'leaveUGCGameInternal', 'FLog::SingleSurfaceApp')
        self.append(1005.2, 'Client:Disconnect', 'DFLog::NetworkClient')
        self.tick(1006)
        rows = self.store.sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['duration'], 4.125)
        self.assertFalse(rows[0]['estimated'])

    def test_network_disconnect(self):
        self.join()
        self.append(1004, 'Connection lost: AckTimeout 1')
        self.tick(1005)
        self.assertEqual(self.store.sessions()[0]['end'], 1004)
        self.assertIsNone(self.store.active())

    def test_crash_ends_at_last_heartbeat(self):
        self.join()
        self.tick(1004)
        self.tick(1006, [])
        row = self.store.sessions()[0]
        self.assertEqual(row['end'], 1004)
        self.assertTrue(row['estimated'])

    def test_restart_does_not_count_downtime_or_duplicate_old_logs(self):
        self.join()
        self.tick(1004)
        self.tracker = Tracker(self.store, Path(self.tmp.name))
        self.tick(1100)
        self.tick(1102)
        rows = self.store.sessions()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r['duration'] for r in rows), 5)
        self.assertEqual(rows[0]['start'], 1100)

    def test_sleep_excludes_gap(self):
        self.join()
        self.tick(1004)
        self.tick(1500)
        rows = self.store.sessions()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r['duration'] for r in rows), 3)

    def test_exit_during_sleep_does_not_resume(self):
        self.join()
        self.append(1200, 'Connection lost: timeout')
        self.tick(1500)
        self.assertIsNone(self.store.active())
        self.assertEqual(len(self.store.sessions()), 1)

    def test_clock_moves_backwards(self):
        self.join()
        self.tick(900)
        self.assertTrue(all(r['duration'] >= 0 for r in self.store.sessions()))

    def test_old_logs_do_not_create_historical_time(self):
        self.append(900, 'Connection accepted from example')
        self.append(950, 'Client:Disconnect', 'DFLog::NetworkClient')
        self.tracker = Tracker(self.store, Path(self.tmp.name))
        self.tick(1000)
        self.assertEqual(self.store.sessions(), [])

    def test_multiple_clients_pause(self):
        self.join()
        self.tick(1005, [self.client, Client('456:2', self.path)])
        self.assertIsNone(self.store.active())
        self.assertIn('Multiple', self.tracker.status)

    def test_partial_line(self):
        text = line(1001, 'FLog::Network', 'Connection accepted from example')
        self.path.write_text(text[:-1])
        self.tick(1002)
        self.assertIsNone(self.store.active())
        with self.path.open('a') as f:
            f.write('\n')
        self.tick(1003)
        self.assertEqual(self.store.active()['start'], 1001)

    def test_truncation_stops_old_session(self):
        self.join()
        self.path.write_text('')
        self.tick(1004)
        self.assertIsNone(self.store.active())
        self.assertEqual(self.store.sessions()[0]['end'], 1002)

    def test_teleport_creates_nonoverlapping_sessions(self):
        self.join()
        self.append(1004, 'launchUGCGameInternal', 'FLog::SingleSurfaceApp')
        self.append(1005, 'Connection accepted from example2')
        self.tick(1006)
        rows = self.store.sessions()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['end'], 1004)
        self.assertEqual(rows[0]['start'], 1005)

    def test_game_output_cannot_start_session(self):
        self.assertIsNone(parse(line(1000, 'FLog::Output', 'Connection accepted from example')))
        self.assertIsNone(parse('malformed line'))

    def test_pid_reuse_changes_source(self):
        self.join()
        self.client = Client('123:2', self.path)
        self.tick(1005)
        self.assertEqual(len(self.store.sessions()), 2)

    def test_database_reopen_recovers_active_session(self):
        self.join()
        self.store.db.close()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.store.recover()
        self.assertEqual(self.store.sessions()[0]['end'], 1002)
        self.assertIsNone(self.store.active())

    def test_final_exit_log_is_read_after_process_exits(self):
        self.join()
        self.append(1003.75, 'Client:Disconnect', 'DFLog::NetworkClient')
        self.tick(1004, [])
        row = self.store.sessions()[0]
        self.assertEqual(row['end'], 1003.75)
        self.assertFalse(row['estimated'])

    def test_multiple_games_in_one_poll_keep_their_place_ids(self):
        self.append(1001,'Report game_join_loadtime: placeid:123,','FLog::GameJoinLoadTime')
        self.append(1002,'Connection accepted from first')
        self.append(1003,'launchUGCGameInternal','FLog::SingleSurfaceApp')
        self.append(1004,'Report game_join_loadtime: placeid:456,','FLog::GameJoinLoadTime')
        self.append(1005,'Connection accepted from second')
        self.tick(1006)
        rows=self.store.sessions()
        self.assertEqual([r['place'] for r in rows],['456','123'])
        self.assertEqual(rows[1]['duration'],1)
