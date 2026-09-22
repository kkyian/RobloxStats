"""Build a local macOS app. Run with `uv run python scripts/build_macos_app.py`."""
import json
import plistlib
import shutil
import subprocess
import sys
import sysconfig
from app_icon import make_icon
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if sys.platform != 'darwin':
    raise SystemExit('This app builder requires macOS.')
app = root / 'dist/RobloxStats.app'
if app.exists():
    shutil.rmtree(app)
contents = app / 'Contents'
resources = contents / 'Resources'
executable = contents / 'MacOS/RobloxStats'
executable.parent.mkdir(parents=True)
resources.mkdir()
make_icon(resources)
# Bundle the Python application and dependencies; use the installed Python runtime.
shutil.copytree(sysconfig.get_paths()['purelib'], resources / 'python', ignore=shutil.ignore_patterns('__pycache__', '*.pth'))
shutil.copytree(root / 'src/robloxstats', resources / 'python/robloxstats', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
(resources / 'runtime.json').write_text(json.dumps({'python': str(Path(sys.executable).resolve())}))
with (contents / 'Info.plist').open('wb') as stream:
    plistlib.dump({'CFBundleIconFile':'AppIcon','CFBundleName':'RobloxStats','CFBundleDisplayName':'RobloxStats','CFBundleIdentifier':'com.robloxstats.desktop','CFBundleExecutable':'RobloxStats','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.2.0','CFBundleVersion':'3','LSUIElement':True,'LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}}, stream)
subprocess.run(['xcrun','clang','-fobjc-arc','-fmodules','-O2','-mmacosx-version-min=13.0',str(root/'macos/RobloxStats.m'),'-o',str(executable),'-framework','Cocoa','-framework','WebKit','-framework','Security'],check=True)
subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
print(app)
