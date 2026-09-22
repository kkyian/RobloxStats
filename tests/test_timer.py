import unittest
from unittest.mock import Mock, patch

from robloxstats.storage import Store
from robloxstats.timer import Timer, quit_roblox


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.quitter = Mock()
        self.timer = Timer(self.store, self.quitter, native=False)

    def tearDown(self):
        self.store.db.close()

    def start(self):
        return self.timer.action('start', minutes=1, now=100)['id']

    def test_expiry_waits_for_choice_then_exit_quits(self):
        ident = self.start()
        self.timer.tick(159)
        self.assertEqual(self.timer.snapshot(159)['remaining'], 1)
        self.timer.tick(160)
        self.assertEqual(self.timer.state['status'], 'expired')
        self.quitter.assert_not_called()
        self.timer.action('exit', ident, now=160)
        self.quitter.assert_called_once()
        self.assertEqual(self.timer.state['status'], 'finished')

    def test_snooze_exactly_once_then_automatic_quit(self):
        ident = self.start()
        self.timer.tick(160)
        self.timer.action('snooze', ident, now=170)
        self.assertEqual(self.timer.state['deadline'], 290)
        for action in ['snooze', 'cancel', 'start']:
            with self.assertRaises(ValueError):
                self.timer.action(action, ident, minutes=30, now=200)
        self.timer.tick(289)
        self.quitter.assert_not_called()
        self.timer.tick(290)
        self.timer.tick(291)
        self.quitter.assert_called_once()
        self.assertEqual(self.timer.state['status'], 'finished')

    def test_restart_preserves_deadline_and_snooze(self):
        ident = self.start()
        self.timer.tick(160)
        self.timer.action('snooze', ident, now=160)
        restored = Timer(self.store, self.quitter, native=False)
        self.assertTrue(restored.state['snoozed'])
        self.assertEqual(restored.state['deadline'], 280)
        restored.tick(500)
        self.quitter.assert_called_once()

    def test_restart_with_overdue_initial_timer(self):
        self.start()
        restored = Timer(self.store, self.quitter, native=False)
        restored.tick(500)
        self.assertEqual(restored.state['status'], 'expired')
        self.quitter.assert_not_called()

    def test_cancel_only_before_expiry_and_not_after_snooze(self):
        ident = self.start()
        with self.assertRaises(ValueError):
            self.timer.action('cancel', ident, now=160)
        self.timer.action('cancel', ident, now=150)
        self.timer.tick(160)
        self.assertEqual(self.timer.state['status'], 'cancelled')
        self.quitter.assert_not_called()

    def test_duplicate_and_stale_actions(self):
        ident = self.start()
        with self.assertRaises(ValueError): self.timer.action('start', minutes=2, now=120)
        self.timer.tick(160)
        with self.assertRaises(ValueError): self.timer.action('exit', 'old-id', now=160)
        self.timer.action('exit', ident, now=160)
        with self.assertRaises(ValueError): self.timer.action('exit', ident, now=160)
        self.quitter.assert_called_once()

    def test_invalid_duration(self):
        for minutes in [0,-1,1441,1.5,True,'5',None]:
            with self.assertRaises(ValueError): self.timer.action('start', minutes=minutes)

    def test_failed_quit_can_be_retried_but_not_snoozed(self):
        ident = self.start()
        self.timer.tick(160)
        self.quitter.side_effect = OSError('denied')
        result = self.timer.action('exit', ident, now=160)
        self.assertEqual(result['status'], 'expired')
        self.assertTrue(result['error'])
        with self.assertRaises(ValueError): self.timer.action('snooze', ident, now=160)
        self.quitter.side_effect = None
        self.timer.action('exit', ident, now=170)
        self.assertEqual(self.timer.state['status'], 'finished')

    def test_interrupted_exit_requires_retry(self):
        self.start()
        self.timer.state['status'] = 'exiting'
        self.timer.save()
        restored = Timer(self.store, self.quitter, native=False)
        self.assertEqual(restored.state['status'], 'expired')
        self.assertTrue(restored.state['snoozed'])

    @patch('robloxstats.timer.subprocess.Popen')
    def test_native_popup_snooze_routes_to_persistent_timer(self, popen):
        self.start()
        self.timer.native = True
        process = popen.return_value
        process.poll.return_value = None
        self.timer.tick(160)
        popen.assert_called_once()
        self.timer.tick(161)
        popen.assert_called_once()
        process.poll.return_value = 0
        process.returncode = 0
        process.communicate.return_value = ('button returned:Snooze once for 2 minutes', None)
        self.timer.tick(162)
        self.assertEqual(self.timer.state['deadline'], 282)
        self.assertTrue(self.timer.state['snoozed'])

    @patch('robloxstats.timer.subprocess.Popen')
    def test_native_failure_leaves_dashboard_choice(self, popen):
        self.start()
        self.timer.native = True
        process = popen.return_value
        process.poll.return_value = 1
        process.returncode = 1
        process.communicate.return_value = ('', None)
        self.timer.tick(160)
        self.timer.tick(161)
        self.assertFalse(self.timer.native)
        self.assertEqual(self.timer.state['status'], 'expired')
        self.assertIn('Desktop popup unavailable', self.timer.state['error'])

    @patch('robloxstats.timer.psutil.wait_procs')
    @patch('robloxstats.timer.psutil.process_iter')
    @patch('robloxstats.timer.psutil.Process')
    def test_quit_targets_only_own_player_and_escalates(self, current, processes, wait):
        current.return_value.username.return_value = 'me'
        player = Mock(info={'name':'RobloxPlayer','username':'me'})
        studio = Mock(info={'name':'RobloxStudio','username':'me'})
        other = Mock(info={'name':'RobloxPlayer','username':'someone-else'})
        browser = Mock(info={'name':'Chrome','username':'me'})
        processes.return_value = [player,studio,other,browser]
        wait.side_effect = [([], [player]), ([player], [])]
        quit_roblox()
        player.terminate.assert_called_once()
        player.kill.assert_called_once()
        for process in [studio,other,browser]: process.terminate.assert_not_called()
