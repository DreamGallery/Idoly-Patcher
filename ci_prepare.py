"""Materialize workflow secrets and APK inputs without logging their values."""
import base64
import os
from pathlib import Path
import tempfile

from patcher import download, extract_apks


def main():
    url = os.environ.get('GAME_APKS_URL', '').strip()
    key = os.environ.get('PATCH_KEYSTORE_BASE64', '').strip()
    if not url or not key or not os.environ.get('IDOLY_KS_PASS'):
        raise SystemExit('Configure GAME_APKS_URL, PATCH_KEYSTORE_BASE64 and PATCH_KEYSTORE_PASSWORD Secrets')
    destination = Path('secrets/patcher.jks')
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    destination.write_bytes(base64.b64decode(''.join(key.split()), validate=True))
    destination.chmod(0o600)
    with tempfile.TemporaryDirectory() as directory:
        archive = download(url, Path(directory) / 'game.zip')
        extract_apks(archive, Path('inputs/game'))
    print('Prepared private signing key and game APK inputs')


if __name__ == '__main__':
    main()
