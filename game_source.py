"""Record an independently obtained Play installation and verify downloaded APK sets."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

import patcher


def load_reference(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    digest = re.compile(r'[0-9a-f]{64}')
    if (data.get('schema_version') != 1 or data.get('package') != patcher.GAME
            or not re.fullmatch(r'\d+(?:\.\d+)+', str(data.get('version_name', '')))
            or not re.fullmatch(r'\d+', str(data.get('version_code', '')))):
        raise ValueError('Invalid game reference identity')
    certs, splits = data.get('certificates_sha256'), data.get('splits_sha256')
    if (not isinstance(certs, list) or not certs
            or any(not isinstance(c, str) or not digest.fullmatch(c) for c in certs)
            or len(set(certs)) != len(certs)):
        raise ValueError('Game reference requires trusted signing certificate SHA-256 values')
    if (not isinstance(splits, dict) or 'base' not in splits
            or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', k)
                   or not isinstance(v, str) or not digest.fullmatch(v) for k, v in splits.items())):
        raise ValueError('Invalid game reference APK hashes')
    return data


def fingerprint(apks):
    _, info, certs = patcher.validate_game(apks, allow_untested=True)
    splits = {}
    for apk in apks:
        key = patcher.package(apk).get('split') or 'base'
        if key in splits:
            raise ValueError('Duplicate APK identity in reference')
        splits[key] = patcher.sha256(apk)
    return {'schema_version': 1, 'package': patcher.GAME,
            'version_name': info['versionName'], 'version_code': info['versionCode'],
            'certificates_sha256': sorted(certs), 'splits_sha256': splits}


def verify_reference(apks, reference_path, mode='signature'):
    if mode not in ('signature', 'exact'):
        raise ValueError('Unknown game verification mode')
    reference = load_reference(reference_path)
    actual = fingerprint(apks)
    for field in ('package', 'certificates_sha256'):
        expected, found = reference[field], actual[field]
        if field == 'certificates_sha256':
            expected, found = sorted(expected), sorted(found)
        if found != expected:
            raise ValueError(f'Game does not match trusted reference: {field}')
    same_version = all(reference[k] == actual[k] for k in ('version_name', 'version_code'))
    identical = same_version and reference['splits_sha256'] == actual['splits_sha256']
    if mode == 'exact' and not identical:
        raise ValueError('APK split hashes differ from the Play reference; exact verification failed')
    return {'mode': mode, 'reference_sha256': patcher.sha256(reference_path),
            'signer_match': True, 'reference_version_match': same_version,
            'all_split_bytes_match': identical}


def record_play_reference(serial, output):
    if output.exists():
        raise ValueError('Reference already exists; choose a new --output and review changes')
    # Installer metadata is a provenance check, not a remote Google attestation.
    listing = patcher.command('adb', '-s', serial, 'shell', 'pm', 'list', 'packages', '-i', patcher.GAME)
    match = re.search(r'^package:' + re.escape(patcher.GAME) + r'\s+installer=com\.android\.vending\s*$',
                      listing, re.MULTILINE)
    if not match:
        raise ValueError('Device does not report a Google Play installation; no trusted reference recorded')
    with tempfile.TemporaryDirectory(prefix='idoly-play-reference-') as temporary:
        folder = Path(temporary) / 'game'
        patcher.pull(serial, folder)
        data = fingerprint(sorted(folder.glob('*.apk')))
    data['source'] = {'kind': 'google-play-device', 'installer': 'com.android.vending',
                      'recorded_at': datetime.now(timezone.utc).isoformat()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Recorded signing certificate and APK hashes: {output}')


@contextmanager
def apk_inputs(game_dir, archive):
    if archive is None:
        yield sorted(game_dir.glob('*.apk'))
    else:
        with tempfile.TemporaryDirectory(prefix='idoly-xapk-') as temporary:
            folder = Path(temporary) / 'game'
            patcher.extract_apks(archive, folder)
            yield sorted(folder.glob('*.apk'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    record = commands.add_parser('record', help='Read APKs from an independently installed Google Play game')
    record.add_argument('--serial', required=True)
    record.add_argument('--output', type=Path, default=Path('game-reference.json'))
    verify = commands.add_parser('verify', help='Check ZIP/XAPK or an extracted APK directory')
    source = verify.add_mutually_exclusive_group()
    source.add_argument('--archive', type=Path)
    source.add_argument('--game-dir', type=Path, default=Path('inputs/game'))
    verify.add_argument('--reference', type=Path, default=Path('game-reference.json'))
    verify.add_argument('--mode', choices=('signature', 'exact'), default='signature')
    args = parser.parse_args()
    if args.command == 'record':
        record_play_reference(args.serial, args.output)
    else:
        with apk_inputs(args.game_dir, args.archive) as apks:
            print(json.dumps(verify_reference(apks, args.reference, args.mode), indent=2))


if __name__ == '__main__':
    main()
