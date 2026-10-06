"""Materialize workflow secrets and APK inputs without logging their values."""
import base64
import os
from pathlib import Path
import tempfile

from patcher import download, extract_apks, package, sha256
from ci_release import load_plan, REFERENCE
from game_source import verify_reference

DEFAULT_GAME_APKS_URL = 'https://d.apkpure.com/b/XAPK/game.qualiarts.idolypride?version=latest'


def main():
    url = os.environ.get('GAME_APKS_URL', '').strip() or DEFAULT_GAME_APKS_URL
    key = os.environ.get('PATCH_KEYSTORE_BASE64', '').strip()
    if not key or not os.environ.get('IDOLY_KS_PASS'):
        raise SystemExit('Configure PATCH_KEYSTORE_BASE64 and PATCH_KEYSTORE_PASSWORD Secrets')
    plan = load_plan()
    if sha256(REFERENCE) != plan['reference_sha256']:
        raise ValueError('Trusted reference changed after release planning')
    with tempfile.TemporaryDirectory() as directory:
        archive = download(url, Path(directory) / 'game.zip')
        extract_apks(archive, Path('inputs/game'))
    apks = sorted(Path('inputs/game').glob('*.apk'))
    verify_reference(apks, REFERENCE, plan['game_check'])
    if any(package(apk)['versionName'] != plan['game_version'] for apk in apks):
        raise ValueError('Downloaded game version differs from GAME_VERSION; no fallback to latest')
    download(plan['module_url'], Path('cache/module.apk'), plan['module_sha256'])
    destination = Path('secrets/patcher.jks')
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    destination.write_bytes(base64.b64decode(''.join(key.split()), validate=True))
    destination.chmod(0o600)
    print('Verified original ZIP/XAPK and pinned plugin; prepared private signing key')


if __name__ == '__main__':
    main()
