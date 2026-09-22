"""Create a source archive using an explicit allowlist; never include user data."""
import hashlib
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1] if len(sys.argv) > 1 else root / 'dist/release')
out.mkdir(parents=True, exist_ok=True)
version = tomllib.loads((root/'pyproject.toml').read_text())['project']['version']
files = ['pyproject.toml', 'uv.lock', 'README.md', '.env.example', '.gitignore',
         'Dockerfile', '.dockerignore', 'compose.yaml']
for directory in ['src', 'tests', 'macos', 'scripts', 'packaging']:
    for path in sorted((root/directory).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py','.m','.html','.css','.js','.md'}:
            files.append(str(path.relative_to(root)))
with zipfile.ZipFile(out/'robloxstats-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for relative in sorted(files):
        path=root/relative
        if path.is_symlink():
            raise RuntimeError(f'Symlink is not allowed in release: {relative}')
        info=zipfile.ZipInfo('robloxstats/'+relative, date_time=(2026,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        info.external_attr=0o100644 << 16
        archive.writestr(info,path.read_bytes())
shutil.copyfile(root/'packaging/SETUP.md', out/'SETUP.md')
with (out/'SHA256SUMS').open('w') as stream:
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name!='SHA256SUMS':
            stream.write(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n')
print(f'Packaged RobloxStats {version}: {out}')
