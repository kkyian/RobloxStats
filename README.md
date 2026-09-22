# RobloxStats

A local Roblox desktop playtime tracker with a responsive dashboard, SQLite history, calendar-aware statistics, and scheduled weekly email reports. Built for macOS, with a Windows desktop-client adapter (not yet live-tested on Windows). No Roblox account password or API key is required.

## Start

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/). From this project directory:

```sh
uv sync --locked
uv run robloxstats
```

The dashboard opens at **http://127.0.0.1:8765**. Join a Roblox game and the tracker will begin recording automatically. The app must be running to observe playtime. Closing the dashboard tab does not stop the tracker; stopping its terminal process does.

```sh
uv run robloxstats --no-browser
uv run robloxstats --port 8766 --data-dir /path/to/private/data
uv run robloxstats --log-dir /path/to/Roblox/logs
```

Defaults:

- Database: `~/.robloxstats/playtime.sqlite3` (persistent across app/computer restarts).
- macOS logs: `~/Library/Logs/Roblox`.
- Windows logs: `%LOCALAPPDATA%/Roblox/logs`.
- Reporting time zone: `Asia/Singapore`. Change it in Settings before tracking if needed.
- Week: Monday 00:00 through the following Monday 00:00, in the selected time zone.
- Poll interval: 2 seconds. Dashboard refresh: 5 seconds. Email scheduler: 30 seconds.

## What “playtime” means and how detection works

**This measures connected in-game time, including idle/AFK and background time. It does not infer keyboard activity, attention, or whether a character can already move.** The Roblox launcher/home screen and an unsuccessful join attempt do not count. An in-experience pause menu still counts because the game remains connected; returning to the Roblox home menu stops the session.

1. Find a Roblox desktop player process, excluding Studio and launchers. Identity includes process creation time to guard against PID reuse.
2. Associate its open `_Player_*.log` file using OS process information. Never guess based on the newest log in a folder. If attribution is unavailable or multiple clients are open, pause and display a diagnostic.
3. Start only on engine network `Connection accepted from` / `serverId` evidence. A join request, `UGCGame` stage, or load-time report alone cannot start a session.
4. Stop on engine disconnect, connection-loss, return-to-menu, game-switch, or shutdown events. Parse a final flushed exit event even if the process has just disappeared.
5. Use the log event’s UTC timestamp for normal boundaries, retaining subsecond precision. Duplicate connection messages are idempotent. A database uniqueness guard and an OS instance lock prevent concurrent sessions and duplicate trackers using the same database.
6. Checkpoint the current session every poll. If the process crashes without an exit event, a log is unavailable, monitoring pauses for more than 15 seconds, the clock jumps backwards, or the tracker restarts, close at the last checkpoint and mark the boundary **estimated** (`≈` in the dashboard). A still-connected client resumes from the new observation, excluding the unobserved gap.
7. Read complete log lines only; handle truncation and replacement. At first launch or restart, use the existing log only to determine current state. Do not import historical hours or count tracker downtime.

Normal joins/leaves retain the engine event timestamp. Crash/process-exit detection has approximately a two-second observation limit; there is no honest way to reconstruct an exact unlogged crash time. Delayed event timestamps outside a 15-second observation window are clamped and marked estimated. Clock changes, buffering, and unobserved periods can therefore undercount. A silent internet failure is observable only when Roblox emits a disconnect; time until its network timeout can be included. If Roblox hangs without a disconnect while its process stays alive, it can continue to appear connected. Resume after sleep uses the client's latest recorded state, which may temporarily remain connected until Roblox logs the failure.

These log strings are **not a supported, stable Roblox API**. The parser was checked against local macOS 0.739 client logs, including disconnect and menu transitions. Future client changes may require parser updates. The app fails closed if the live process/log cannot be identified. A live join/leave test is still recommended after client updates. Windows uses `psutil.open_files`, which can omit file handles on some systems; tracking pauses if attribution is unavailable. Mobile, console, Store/UWP clients, and Linux compatibility runtimes are not supported.

## Statistics and storage

UTC intervals are the source of truth. Each session stores its start, end, last verified observation, place ID when available, close reason, and estimated-boundary flag. Duration is derived from interval endpoints instead of an independently mutable counter. Dates and weeks are derived in the reporting zone rather than baked into the database.

Daily aggregation splits intervals at each local midnight, supports 23- and 25-hour daylight-saving days, and unions overlaps defensively. Multiple sessions add together. The dashboard includes today's total, current-week total, a seven-day chart, weekly average, most-played day, the latest 100 sessions, and a week picker for all historical totals. Displayed durations floor to whole minutes; stored calculations retain fractional seconds. The average includes all seven days, including future/zero days.

SQLite uses WAL and full synchronous commits. Back up with SQLite's backup API, or stop the app before copying its database. Do not copy only the main database file while it is running without accounting for its WAL.

## Weekly email reports

Configure recipient, reporting time zone, send weekday/time, and the email switch in dashboard Settings. Emails are **disabled by default**. Reports always cover a **completed Monday–Sunday week**. For example, Monday 09:00 sends the week that ended nine hours earlier; choosing Sunday sends that same completed week on the following Sunday, not the current partial week.

Configure SMTP credentials in the launching process environment. `.env.example` documents the names; the app does not automatically source a `.env` file. Use a password manager, OS secret store, or protected environment injection. Never commit a real password.

```sh
export SMTP_HOST='smtp.example.com'
export SMTP_PORT='587'
export SMTP_SECURITY='starttls'
export SMTP_FROM='reports@example.com'
export SMTP_USER='reports@example.com'
# Supply SMTP_PASSWORD securely through your shell/password manager.
uv run robloxstats
```

`SMTP_SECURITY=ssl` supports implicit TLS (default port 465); `starttls` defaults to 587. Both validate certificates. The email has a plain-text version and an HTML table containing Monday–Sunday durations and the weekly total. No external email has been sent during development.

Completed weeks are saved locally even when email is disabled, starting with the week of installation. Pending reports reflect corrections/settings until delivery; sent reports retain the exact snapshot sent. Selecting a saved report shows its stored totals and time zone. Historical weeks selected through the date picker use the current configured zone.

The scheduler catches up missed delivery times after the app restarts, including multiple completed weeks. Enabling email can send previously unsent reports. An off/asleep computer cannot send on schedule. Missing SMTP settings and known connection/authentication/rejection failures remain pending and retry every scheduler pass. A durable `sending` state prevents automatic repeats after a crash. If the connection drops during message submission, SMTP cannot guarantee exactly-once delivery: the report is marked **uncertain** and is not automatically resent. Check your mailbox/provider and use the saved report; do not assume an uncertain send failed. Report errors never persist SMTP responses or credentials.

## Start automatically after login

### macOS

Generate a LaunchAgent after `uv sync`:

```sh
uv run python -m robloxstats.autostart
```

Stop any manually running tracker first (Ctrl-C), then load it:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.robloxstats.plist
```

Open http://127.0.0.1:8765 whenever needed. The generated agent uses absolute paths to this project's virtual environment, starts at login, and restarts after a crash. Keep the project/venv at that path. Logs are in `~/.robloxstats/service.log` and `service-error.log`. This generator creates the file only; it does not silently install or start a service. Login agents do not inherit your interactive shell's SMTP variables; provide them through a secure service wrapper/environment if enabling emails.

To stop/unload the service:

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.robloxstats.plist
```

To remove autostart, also remove the generated plist after unloading it. Your database remains intact.

### Windows desktop client (experimental)

In Task Scheduler, create an **At log on** task for the same Windows user who plays Roblox. Set the program to the absolute path of `.venv\Scripts\python.exe`, arguments to `-m robloxstats --no-browser`, and “Start in” to the project directory. Set “If the task is already running” to **Do not start a new instance**. Set needed SMTP environment variables securely and restart the task after changing them. No administrator privileges should be necessary for your own Roblox process.

## Validation

```sh
uv run python -m unittest discover -s tests -v
uv build
```

Tests exercise multiple sessions, duplicates, normal exits, disconnects, process crashes, PID reuse, tracker/database restarts, sleep gaps, backward clock jumps, partial writes, truncation, game switches, local midnight/week boundaries, DST transitions, overlap unioning, settings validation, instance locking, report schedules, catch-up, disabled emails, delivery interruptions, and report rendering. The tests use temporary databases/logs and fake email delivery.

For a live smoke test: open Roblox home (no session); join a game (one session); return home (session ends); join again (a second session); quit Roblox; restart RobloxStats and confirm history persists. Compare timestamps with the engine log. To validate sleep or crash behavior, check for the `≈` indicator and exclusion of the unobserved interval. Actual SMTP delivery requires your provider credentials and recipient; automated tests do not validate a provider's deliverability.

## Structure

```text
src/robloxstats/
  tracking.py      Process/log association, parser, session state machine
  storage.py       SQLite persistence, settings, calendar aggregation
  reports.py       Completed-week scheduling, MIME email, SMTP delivery states
  app.py           Local-only HTTP API, instance lock, background workers
  autostart.py     Optional macOS login-agent generator
  static/          HTML, CSS, and JavaScript dashboard (no CDN dependencies)
tests/             Isolated tracking, statistics, reporting, and HTTP tests
```

The server binds only to loopback, validates Host/Origin, requires a per-process CSRF token for changes, and serves a restrictive content-security policy. It has no public hosting requirement, cloud account, telemetry, or external assets. Settings store only non-secret preferences. Do not expose this local server through a public tunnel.

## Break timer

Use **Time for a little balance** on the dashboard to choose 1–1440 minutes and start the countdown. This is a wall-clock timer: menus, AFK time, breaks, and computer sleep all count toward the deadline, independently of the playtime statistics.

At the deadline, a dashboard popup offers **Snooze once for 2 minutes** or **Exit Roblox**. On macOS the running tracker also opens a desktop dialog, so the dashboard does not need to be in front. Exit terminates this user's Roblox player processes; if they do not exit within three seconds, it force-quits them. Studio and other users' processes are excluded. If Roblox cannot be closed, the popup reports the failure and offers another exit attempt.

The single snooze grants exactly 120 seconds from the accepted choice. At its end, Roblox closes automatically. A snoozed/expired timer cannot be cancelled or replaced, and a refreshed dashboard or restarted tracker does not reset the allowance. The original countdown can be cancelled before it expires. After completion, a new timer can be started.

The deadline and snooze state are saved to SQLite. The tracker must remain running to display prompts or quit Roblox; overdue timers are handled when it restarts or the computer wakes. This is a personal reminder, not a tamper-proof parental control or a block on reopening Roblox. Changing the system clock changes the wall-clock deadline. On platforms without a desktop dialog, or if the desktop dialog fails, keep the dashboard open for the first-expiry choice. Automatic exit after snooze runs in the backend regardless of browser tabs. Quitting a live game can lose unsaved in-game progress.

### Persistent SMTP credentials on macOS

The app can also load non-secret SMTP defaults from `~/.robloxstats/smtp.json`. Supported keys are `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_FROM`, `SMTP_USER`, and `keychain_service`. When `keychain_service` is provided, the app reads the password from the matching macOS generic-password Keychain item, using `SMTP_USER` as its account. Environment variables take precedence. Plaintext passwords are rejected in this JSON file. This works when starting with `uv run robloxstats`, without re-entering the password in every terminal session; the login Keychain must be unlocked and access allowed.

## Resend API (alternative to Gmail SMTP)

Resend is integrated using its official Python SDK, matching the project's Python backend. Set `EMAIL_PROVIDER=resend` and supply `RESEND_API_KEY` in the launching environment. **Replace `re_xxxxxxxxx` with your real API key locally; never put it into source code or chat.** A configured API key also selects Resend automatically unless `EMAIL_PROVIDER` explicitly chooses SMTP. Resend skips the old Gmail Keychain setup.

In **Nushell**, use a hidden prompt:

```nu
$env.EMAIL_PROVIDER = "resend"
$env.RESEND_API_KEY = (input --suppress-output "Your real Resend API key: " | str trim)
$env.RESEND_FROM = "onboarding@resend.dev"
```

To send the Hello World example to your chosen recipient:

```nu
uv run python -m robloxstats.resend_mail --to address@example.com
```

This command sends a real email only when you run it. You must supply the recipient with `--to address@example.com`. Its subject is `Hello World` and its HTML is `<p>Congrats on sending your <strong>first email</strong>!</p>`.

Stop any existing tracker, then start it in this same terminal with `uv run robloxstats`. Weekly reports use Resend and the recipient/schedule saved in dashboard Settings. Set your own report recipient there. These environment variables last for the terminal session; the key is never returned through the dashboard API or saved to SQLite.

The default `onboarding@resend.dev` sender can only send to the email address associated with your Resend account. To send to other recipients, verify a domain with Resend and set `RESEND_FROM` to an address on that domain. See [Resend's sender restriction](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain).

Weekly reports include HTML and plain text. Successful API acceptance is marked sent; actual inbox delivery can be checked in Resend. The app supplies a stable, content-derived idempotency key; Resend retains those keys for 24 hours. SQLite's sent/uncertain state prevents later automatic duplicate sends. Known request rejections remain pending; network failures and ambiguous responses are marked uncertain rather than blindly retried. See [Resend idempotency documentation](https://resend.com/docs/dashboard/emails/idempotency-keys).

The dashboard’s **Send sample email** button sends a fresh email on each accepted click, with a 30-second cooldown and no overlapping sends. Sample sends use unique request keys; weekly report deduplication is unchanged.

**Send weekly report now** sends the current Monday–Sunday week's totals so far to the recipient entered in Settings. You can repeat it with the same 30-second cooldown shared by manual email buttons. Each deliberate send gets a new request key. These manual reports do not mark scheduled reports as sent or change the automatic delivery schedule. The subject identifies the report as “week so far.”

## Dashboard tools

- See today's total, weekly comparisons, days played, and the current verified session at a glance.
- Choose a 15, 30, or 60 minute break timer, or enter a custom duration. The countdown includes menus and breaks; it is separate from recorded in-game time. One two-minute snooze is allowed.
- Search the most recent 100 sessions by place, date, or ending reason, and filter estimated boundaries. **Export history** downloads every session as CSV with UTC timestamps and exact recorded seconds.
- **Preview this week's report** shows the seven daily totals and recipient before sending. Manual reports use the latest totals and can be sent repeatedly, with a shared 30-second cooldown.
- The reports panel shows the actual next automatic report date and the complete week it covers. Keep the app running for scheduled delivery; overdue reports are handled when it returns.
- **Recent email activity** keeps the latest 20 manual send attempts across restarts. “Accepted” means the provider accepted the request, not that it reached the inbox. Interrupted requests are marked uncertain. Scheduled delivery status remains in the weekly report archive.
- Preferences show the configured sender and preserve unsaved edits while live statistics refresh. Email secrets stay in environment variables or the configured secure credential store.

## Mac desktop app

Open **RobloxStats.app** from `~/Applications` to use the dashboard in its own native window. The app runs without a Dock icon. A clock in the menu bar lets you open, hide, or reload the dashboard. Closing or hiding the window keeps tracking and weekly emails active. Closing the window keeps tracking and scheduled reports running; **RobloxStats → Quit RobloxStats** stops the tracker if the app started it. If a terminal tracker was already running, the app connects to it and leaves that independent process running when you quit.

Build the local app after changing the project:

```sh
uv run python scripts/build_macos_app.py
```

The result is `dist/RobloxStats.app`. Copy it to `~/Applications`. This local build bundles the dashboard and Python dependencies but uses the Python installation on this Mac; keep that Python installation available. It is not a standalone installer for other Macs. Session data remains in `~/.robloxstats/playtime.sqlite3`, independent of the app bundle. Desktop logs are in `~/.robloxstats/desktop.log`.

Desktop Resend credentials use the **RobloxStats Resend** item in macOS Keychain, with non-secret sender preferences in `~/.robloxstats/desktop-email.json`. No API key is included in the app bundle. CSV exports open a native Save dialog.

## Docker tests

Docker Desktop can build and test the Python backend in an isolated Linux container:

```sh
docker compose build tests
docker compose run --rm tests
```

The test container runs as a non-root user with networking disabled at runtime. It does not mount your playtime database, Roblox logs, Keychain, or email credentials, and does not send real email. Rebuild after editing source or tests. The native Mac app continues to run independently.

This configuration is a test environment, not a containerized live tracker. Docker Desktop cannot see macOS Roblox processes or display the Mac timer popup. Running the dashboard and email service in Docker would require a separate authenticated Mac helper to supply activity observations and perform timer actions. Mounting Roblox logs alone cannot reliably detect process exits and crashes.

## Package and share with Docker

Docker can build distributable artifacts and a shareable packaging image. It does
not replace the native tracking process. All tests must pass during packaging.

```sh
# Export the source ZIP, wheel, source distribution, setup guide, and checksums:
docker build --target release --output type=local,dest=dist/release .
# Build an image other Docker users can load to extract the same artifacts:
docker build --target packager -t robloxstats-packager:0.1.0 .
docker save robloxstats-packager:0.1.0 | gzip > dist/robloxstats-packager-0.1.0-linux-arm64.tar.gz
```

Share the release folder or the packaging image archive. The archive architecture
matches the build machine; the example filename is for Apple Silicon/Linux ARM64.
See [recipient instructions](packaging/SETUP.md). Each recipient supplies their own
email credentials and runs detection on their own computer. The source ZIP includes
the Mac app builder; Docker cannot compile the Cocoa app for macOS. Native app
builds currently depend on the recipient's local Python and are not notarized.
