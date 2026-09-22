"""Generate a macOS login agent without embedding SMTP credentials."""
import argparse
import plistlib
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Generate a macOS LaunchAgent for RobloxStats')
    parser.add_argument('--output', type=Path, default=Path.home() / 'Library/LaunchAgents/local.robloxstats.plist')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.robloxstats')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper is for macOS. See README for Windows Task Scheduler setup.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error(f'{args.output} already exists. Review it before replacing it.')
    data = args.data_dir.expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Keep the venv path: resolving the executable symlink would bypass the venv.
    executable = str(Path(sys.executable).absolute())
    value = {'Label': 'local.robloxstats',
             'ProgramArguments': [executable, '-m', 'robloxstats', '--no-browser', '--data-dir', str(data)],
             'WorkingDirectory': str(Path.cwd()), 'RunAtLoad': True, 'KeepAlive': True,
             'ThrottleInterval': 15, 'StandardOutPath': str(data / 'service.log'),
             'StandardErrorPath': str(data / 'service-error.log')}
    args.output.write_bytes(plistlib.dumps(value))
    print(f'Created {args.output}. Follow README to load the service.')


if __name__ == '__main__':
    main()
