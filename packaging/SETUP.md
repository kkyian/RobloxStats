# RobloxStats shared release

This release contains the tracker source, Python wheel, and a native Mac app builder.
It contains no sender API key, playtime history, or preconfigured recipient.

## Run on your Mac

Install Python 3.14 or newer and uv (https://docs.astral.sh/uv/), then extract
`robloxstats-source.zip` and open a terminal in its `robloxstats` directory:

```sh
uv sync --locked
uv run robloxstats
```

To build a menu-bar Mac app, install Apple's Command Line Tools (`xcode-select
--install`) if needed, then run:

```sh
uv run python scripts/build_macos_app.py
```

Copy `dist/RobloxStats.app` to your Applications folder and open it. It uses the
Python installation on the Mac where it was built. Build it on the recipient's
Mac; do not distribute an app built against another user's Python path.
The local build is ad-hoc signed, not Apple Developer ID signed or notarized.

The app continues tracking with its window closed and has no Dock icon. Use the
clock menu-bar icon to reopen it or quit. Data is saved in `~/.robloxstats`.
Configure your own report recipient, schedule, and email provider; see README.md
and `.env.example`. Terminal environment variables are not automatically inherited
when launching an app from Finder.

## Python package alternative

On the player's computer (not inside Docker):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./robloxstats-0.1.0-py3-none-any.whl
.venv/bin/robloxstats
```

Dependencies are downloaded from PyPI. Windows detection exists but has not been
live-tested; the native Mac app builder requires macOS.

## Docker packaging image

The image packages the software; it is not a live Roblox tracker service.
Docker Desktop cannot observe macOS Roblox processes or show native timer popups.

Load the supplied image archive and extract its release artifacts:

```sh
docker load -i robloxstats-packager-0.1.0-linux-arm64.tar.gz
mkdir -p release
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/release,target=/out" \
  robloxstats-packager:0.1.0
```

The prebuilt image archive is Linux ARM64. On another architecture, rebuild from
the source ZIP using `docker build --target packager -t robloxstats-packager:0.1.0 .`.
`SHA256SUMS` verifies the inner release files; `SHA256SUMS-SHARE` verifies the files
in the share folder. These checksums detect corruption, not publisher identity.
