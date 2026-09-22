"""Draw the app's simple geometric icon without external image dependencies."""
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


def make_icon(resources):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory) / 'AppIcon.iconset'
        folder.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                width = size * scale
                raw = bytearray()
                for y in range(width):
                    raw.append(0)
                    for x in range(width):
                        a, b = abs((x+.5)/width-.5), abs((y+.5)/width-.5)
                        corner = max(a-.30, 0)**2 + max(b-.30, 0)**2
                        if a > .46 or b > .46 or corner > .16**2:
                            pixel = (0,0,0,0)
                        elif .245 < a+b < .30 or a+b < .13:
                            pixel = (214,230,181,255)
                        else:
                            pixel = (41,66,55,255)
                        raw.extend(pixel)
                png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B',width,width,8,6,0,0,0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
                suffix = '@2x' if scale == 2 else ''
                (folder/f'icon_{size}x{size}{suffix}.png').write_bytes(png)
        subprocess.run(['iconutil','-c','icns',str(folder),'-o',str(resources/'AppIcon.icns')],check=True)
