#!/usr/bin/env python3
"""Standalone IDOLY PRIDE ARM64 LSPatch packager."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from copy import copy
from io import BytesIO
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from zipfile import ZipFile, BadZipFile

if sys.version_info < (3, 11):
    raise SystemExit('Idoly-Patcher requires Python 3.11 or newer')

GAME = 'game.qualiarts.idolypride'
MODULE = 'io.github.dreamgallery.idoly.localify'
RELEASES = 'DreamGallery/Idoly-localify-translations'
TESTED_VERSION = '6.0.2'
LSPATCH_URL = 'https://github.com/JingMatrix/LSPatch/releases/download/v1.2/lspatch-v1.2-487-release.jar'
LSPATCH_SHA256 = 'd238fdc414d121b7fa454d8b4ccf420df3a8c97d563761861ff92bd9c5da2165'
LIMIT = 2 * 1024**3
DEFAULT_IMAGES = Path(__file__).resolve().parent / 'image-patches/ui-6.0.2/manifest.json'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(*args):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True)
    if result.returncode:
        # Never echo commands: callers may provide private local filenames.
        raise ValueError(f'{Path(args[0]).name} failed (exit {result.returncode}):\n'
                         f'{result.stderr[-4000:]}\n{result.stdout[-4000:]}')
    return result.stdout


def java_tool(name):
    suffix = '.exe' if os.name == 'nt' else ''
    home = os.environ.get('JAVA_HOME')
    path = Path(home) / 'bin' / (name + suffix) if home else None
    if path and path.is_file():
        return str(path)
    found = shutil.which(name)
    if not found:
        raise ValueError(f'Missing {name}; install JDK 17+ and set JAVA_HOME')
    return found


def sdk_tool(name):
    home = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not home:
        raise ValueError('Set ANDROID_HOME to the Android SDK directory')
    suffix = ('.bat' if name == 'apksigner' else '.exe') if os.name == 'nt' else ''
    candidates = list((Path(home) / 'build-tools').glob('*/' + name + suffix))
    candidates = [p for p in candidates if re.fullmatch(r'\d+\.\d+\.\d+', p.parent.name)]
    if name == 'zipalign':
        candidates = [p for p in candidates if tuple(map(int, p.parent.name.split('.'))) >= (35, 0, 0)]
    if not candidates:
        raise ValueError(f'Android SDK build-tools is missing {name}; install Build Tools 35.0.0 or newer')
    return str(max(candidates, key=lambda p: tuple(map(int, p.parent.name.split('.')))))


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing download redirect to a non-HTTPS URL')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_https(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Downloads require an HTTPS URL without embedded credentials')
    request = urllib.request.Request(url, headers={'User-Agent': 'Idoly-Patcher', 'Accept': '*/*'})
    try:
        return urllib.request.build_opener(HTTPSOnly()).open(request, timeout=120)
    except (urllib.error.URLError, ValueError):
        raise ValueError('Download failed; check the URL, expiry and network access') from None


def download(url, path, expected=None, limit=LIMIT):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with open_https(url) as response:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError('Download exceeds size limit')
                    stream.write(chunk)
            stream.close()
            if expected and sha256(temporary) != expected:
                raise ValueError('Downloaded file SHA-256 mismatch')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def github_json(endpoint):
    with open_https('https://api.github.com/repos/' + RELEASES + '/' + endpoint) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Release metadata exceeds size limit')
    return json.loads(data)


def plugin_asset(releases):
    for release in releases:
        if release.get('draft') or release.get('prerelease'):
            continue
        assets = [a for a in release.get('assets', [])
                  if re.fullmatch(r'idoly-localify-[0-9][A-Za-z0-9._-]*\.apk', a['name'])]
        if len(assets) == 1:
            asset = assets[0]
            digest = asset.get('digest') or ''
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('Plugin release has no SHA-256 digest')
            prefix = f'https://github.com/{RELEASES}/releases/download/'
            if not asset['browser_download_url'].startswith(prefix):
                raise ValueError('Unexpected plugin asset URL')
            return asset, digest[7:]
    raise ValueError('No stable plugin APK release found')


def find_module_release(tag=None, loader=None):
    """Resolve once so the detector and builder use the same immutable APK digest."""
    loader = loader or github_json
    if tag:
        releases = [loader('releases/tags/' + urllib.parse.quote(tag, safe=''))]
        asset, digest = plugin_asset(releases)
    else:
        for page in range(1, 11):
            releases = loader(f'releases?per_page=100&page={page}')
            try:
                asset, digest = plugin_asset(releases)
                break
            except ValueError as error:
                if str(error) != 'No stable plugin APK release found' or len(releases) < 100:
                    raise
        else:
            raise ValueError('Set --module-tag explicitly; no plugin found in recent releases')
    release = next(r for r in releases if asset in r.get('assets', []))
    if not re.fullmatch(r'v?[0-9][A-Za-z0-9._-]*', release.get('tag_name', '')):
        raise ValueError('Invalid plugin release tag')
    return release, asset, digest


def get_module(cache, tag=None):
    _, asset, digest = find_module_release(tag)
    path = cache / digest / asset['name']
    if not path.is_file() or sha256(path) != digest:
        download(asset['browser_download_url'], path, digest)
    return path


def package(apk):
    result = command(sdk_tool('aapt'), 'dump', 'badging', apk)
    first = result.splitlines()[0] if result else ''
    if not first.startswith('package: '):
        raise ValueError('APK has no readable package metadata')
    fields = dict(re.findall(r"([A-Za-z]+)='([^']*)'", first))
    if not fields.get('name') or not fields.get('versionCode'):
        raise ValueError('APK package name/versionCode is missing')
    return fields


def certificates(apk):
    result = command(sdk_tool('apksigner'), 'verify', '--print-certs', apk)
    certs = set(re.findall(r'^(?:V[0-9.]+ )?Signer(?: #\d+)?:? certificate SHA-256 digest: ([0-9a-fA-F]{64})$',
                           result, re.MULTILINE))
    if not certs:
        raise ValueError('APK has no readable signing certificate')
    return {c.lower() for c in certs}


def validate_game(apks, allow_untested=False):
    if not apks:
        raise ValueError('No APK files found in --game-dir')
    entries = [(path, package(path)) for path in apks]
    bases = [(p, info) for p, info in entries if not info.get('split')]
    if len(bases) != 1:
        raise ValueError('Provide exactly one base APK and its splits')
    base, info = bases[0]
    if info.get('versionName') != TESTED_VERSION and not allow_untested:
        raise ValueError(f'Game {info.get("versionName")} is untested; use --allow-untested-version after checking compatibility')
    certificate = certificates(base)
    splits = set()
    has_arm64 = False
    for path, metadata in entries:
        if metadata['name'] != GAME or metadata['versionCode'] != info['versionCode']:
            raise ValueError('All APKs must belong to the same IDOLY PRIDE version')
        split = metadata.get('split', '')
        if split and not re.fullmatch(r'[A-Za-z0-9_.-]+', split):
            raise ValueError('Invalid original split ID')
        if split in splits:
            raise ValueError('Duplicate base/split APK')
        splits.add(split)
        if certificates(path) != certificate:
            raise ValueError('Original APK signing certificates differ')
        with ZipFile(path) as archive:
            names = archive.namelist()
            if any(n.startswith('assets/lspatch/') for n in names):
                raise ValueError('Input is already patched; use original game APKs')
            has_arm64 |= 'lib/arm64-v8a/libil2cpp.so' in names
    if not has_arm64:
        raise ValueError('Game APKs must include ARM64 libil2cpp.so')
    return base, info, certificate


def validate_module(path):
    info = package(path)
    if info['name'] != MODULE:
        raise ValueError('Unexpected plugin package name')
    certificates(path)
    with ZipFile(path) as archive:
        names = archive.namelist()
        required = ['assets/xposed_init', 'assets/idoly-localify/dict.json',
                    'assets/idoly-localify/resource-han-rounded.bundle']
        if any(n not in names for n in required):
            raise ValueError('Plugin is missing Xposed entry point, dictionary or runtime font bundle')
        dictionary = json.loads(archive.read(required[1]))
        if dictionary.get('font_mode') not in ('replace-ui', 'preserve-non-cjk'):
            raise ValueError('Plugin requires legacy game font patching; use a current plugin')
        if not any(n.startswith('lib/arm64-v8a/') and n.endswith('.so') for n in names):
            raise ValueError('Plugin has no ARM64 native library')
        if archive.getinfo(required[2]).file_size < 1024:
            raise ValueError('Runtime font bundle is empty')
    return info


def sign(source, destination, keystore, alias):
    command(sdk_tool('apksigner'), 'sign', '--ks', keystore, '--ks-key-alias', alias,
            '--ks-pass', 'env:IDOLY_KS_PASS', '--key-pass', 'env:IDOLY_KEY_PASS',
            '--out', destination, source)


def verify_alignment(apk):
    command(sdk_tool('zipalign'), '-c', '-P', '16', '4', apk)


def align_apk(source, destination):
    command(sdk_tool('zipalign'), '-P', '16', '4', source, destination)
    verify_alignment(destination)
    # Alignment may change ZIP padding, never entry contents or membership.
    with ZipFile(source) as before, ZipFile(destination) as after:
        if before.namelist() != after.namelist():
            raise ValueError('APK alignment changed the entry list')
        for name in before.namelist():
            if before.read(name) != after.read(name):
                raise ValueError(f'APK alignment changed entry data: {name}')


def passwords():
    if not os.environ.get('IDOLY_KS_PASS'):
        raise ValueError('Set IDOLY_KS_PASS; passwords are read only from environment variables')
    if not os.environ.get('IDOLY_KEY_PASS'):
        os.environ['IDOLY_KEY_PASS'] = os.environ['IDOLY_KS_PASS']


def init_key(path, alias):
    passwords()
    if path.exists():
        raise ValueError('Keystore already exists; keep it for future updates')
    path.parent.mkdir(parents=True, exist_ok=True)
    command(java_tool('keytool'), '-genkeypair', '-keystore', path, '-storetype', 'JKS',
            '-storepass:env', 'IDOLY_KS_PASS', '-keypass:env', 'IDOLY_KEY_PASS',
            '-alias', alias, '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
            '-dname', 'CN=Idoly Patcher', '-noprompt')
    path.chmod(0o600)
    print('Created signing key. Keep a private backup for future APK updates.')


def verify_embedded(patched, original, module):
    with ZipFile(patched) as archive:
        expected = {'assets/lspatch/origin.apk': sha256(original),
                    f'assets/lspatch/modules/{MODULE}.apk': sha256(module)}
        for name, digest in expected.items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    raise ValueError('Embedded original game or module was changed')


def image_tools():
    try:
        import patch_images
    except ImportError as error:
        raise ValueError('UI images require: python -m pip install -r requirements-images.txt; '
                         'or use --no-ui-images') from error
    return patch_images


def patch_wrapper(source, destination, original, modified, manifest):
    """Update wrapper resources and embedded origin after LSPatch captures original signature."""
    with ZipFile(source) as before, ZipFile(original) as raw, ZipFile(modified) as translated:
        names = before.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate LSPatch APK entries')
        replacements = {'assets/lspatch/origin.apk': modified.read_bytes()}
        if before.read('assets/lspatch/origin.apk') != original.read_bytes():
            raise ValueError('LSPatch embedded origin differs before image patch')
        for asset in manifest['assets']:
            name = asset['entry']
            if before.read(name) != raw.read(name):
                raise ValueError(f'LSPatch wrapper resource differs: {name}')
            replacements[name] = translated.read(name)
        with ZipFile(destination, 'w') as after:
            after.comment = before.comment
            for entry in before.infolist():
                after.writestr(copy(entry), replacements.get(entry.filename)
                               if entry.filename in replacements else before.read(entry))
        with ZipFile(destination) as after:
            if after.namelist() != names:
                raise ValueError('Image patch changed wrapper entry list')
            for name in names:
                expected = replacements[name] if name in replacements else before.read(name)
                if after.read(name) != expected:
                    raise ValueError(f'Image patch changed unrelated wrapper data: {name}')


def check_images(base, manifest):
    images = image_tools()
    with tempfile.TemporaryDirectory(prefix='idoly-image-check-') as temporary:
        output = Path(temporary) / 'base.apk'
        images.patch_apk(base, manifest, output)
        images.verify_apk(output, manifest)
    print('Verified source compatibility, reviewed pixels and unchanged unrelated assets')


def stage_game_apks(apks, destination):
    """LSPatch recognizes split_ filenames; XAPK names are not authoritative."""
    destination.mkdir()
    staged = []
    for apk in apks:
        split = package(apk).get('split', '')
        if split and not re.fullmatch(r'[A-Za-z0-9_.-]+', split):
            raise ValueError('Invalid original split ID')
        name = 'split_' + split + '.apk' if split else 'base.apk'
        target = destination / name
        if target.exists():
            raise ValueError('Duplicate original APK identity')
        shutil.copyfile(apk, target)
        staged.append(target)
    return sorted(staged)


def patch(args):
    passwords()
    apks = sorted(args.game_dir.resolve().glob('*.apk'))
    base, game_info, original_certs = validate_game(apks, args.allow_untested_version)
    source_verification = None
    if args.game_reference:
        from game_source import verify_reference
        source_verification = verify_reference(apks, args.game_reference, args.game_check)
    if not args.keystore.is_file():
        raise ValueError('Keystore not found; run init-key once')
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Output directory is not empty; choose a new --output directory')
    cache = args.cache.resolve()
    module = args.module.resolve() if args.module else get_module(cache, args.module_tag)
    module_info = validate_module(module)
    lspatch = args.lspatch.resolve() if args.lspatch else cache / 'lspatch-v1.2-487.jar'
    if not lspatch.exists():
        if args.lspatch:
            raise ValueError('Specified LSPatch JAR does not exist')
        download(LSPATCH_URL, lspatch, LSPATCH_SHA256)
    if sha256(lspatch) != LSPATCH_SHA256:
        raise ValueError('LSPatch JAR differs from the pinned release')
    # Preserve original signatures inside origin.apk for signature emulation.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.idoly-patch-', dir=args.output.parent) as temporary:
        work = Path(temporary)
        image_manifest = None if args.no_ui_images else args.image_manifest.resolve()
        image_spec = None
        embedded_base = base
        if image_manifest:
            images = image_tools()
            image_spec = images.load_manifest(image_manifest)
            image_base = work / 'base-images.apk'
            images.patch_apk(base, image_manifest, image_base)
            embedded_base = work / 'base-images-aligned.apk'
            align_apk(image_base, embedded_base)
            images.verify_apk(embedded_base, image_manifest)
        patched = work / 'patched'
        patched.mkdir()
        canonical_apks = stage_game_apks(apks, work / 'game')
        command(java_tool('java'), '-Xmx4g', '-jar', lspatch, '-l', '2', '-m', module,
                '-o', patched, *canonical_apks)
        outputs = list(patched.glob('*.apk'))
        if len(outputs) != len(apks):
            raise ValueError('LSPatch output APK count does not match input')
        expected = {package(p).get('split', ''): p for p in apks}
        staged = work / 'output'
        staged.mkdir()
        output_certs = None
        seen = set()
        for candidate in outputs:
            metadata = package(candidate)
            split = metadata.get('split', '')
            if (split not in expected or split in seen or metadata['name'] != GAME
                    or metadata['versionCode'] != game_info['versionCode']):
                raise ValueError('Unexpected patched APK identity')
            seen.add(split)
            if split and not re.fullmatch(r'[A-Za-z0-9_.-]+', split):
                raise ValueError('Invalid split ID')
            name = 'base.apk' if not split else 'split_' + split + '.apk'
            target = staged / name
            if not split and image_manifest:
                # apksigner normalizes LSPatch's overlapping ZIP storage before
                # Python can safely inspect and rewrite all entries.
                normalized = work / 'wrapper-normalized.apk'
                sign(candidate, normalized, args.keystore.resolve(), args.key_alias)
                image_candidate = work / 'wrapper-images.apk'
                patch_wrapper(normalized, image_candidate, base, embedded_base, image_spec)
                candidate = work / 'wrapper-images-aligned.apk'
                align_apk(image_candidate, candidate)
            sign(candidate, target, args.keystore.resolve(), args.key_alias)
            verify_alignment(target)
            certs = certificates(target)
            if output_certs is not None and certs != output_certs:
                raise ValueError('Patched APK signatures differ')
            output_certs = certs
            if not split:
                verify_embedded(target, embedded_base, module)
                if image_manifest:
                    images.verify_apk(target, image_manifest)
                    with ZipFile(target) as wrapper:
                        extracted_origin = work / 'verified-origin.apk'
                        extracted_origin.write_bytes(wrapper.read('assets/lspatch/origin.apk'))
                        verify_alignment(extracted_origin)
                        images.verify_apk(BytesIO(wrapper.read('assets/lspatch/origin.apk')), image_manifest)
                        original_signature = json.loads(wrapper.read('assets/lspatch/config.json'))['originalSignature']
                        if hashlib.sha256(bytes.fromhex(original_signature)).hexdigest() not in original_certs:
                            raise ValueError('Original signature emulation was changed')
            else:
                with ZipFile(expected[split]) as before, ZipFile(target) as after:
                    for member in before.namelist():
                        if member.startswith('lib/'):
                            if before.read(member) != after.read(member):
                                raise ValueError('Split native library was changed')
        if seen != set(expected):
            raise ValueError('Patched APK set is incomplete')
        for idsig in staged.glob('*.idsig'):
            idsig.unlink()
        report = {'schema_version': 1, 'game_version': game_info.get('versionName'),
                  'game_version_code': game_info['versionCode'],
                  'module_version': module_info.get('versionName'), 'module_sha256': sha256(module),
                  'game_source_verification': source_verification,
                  'lspatch_sha256': LSPATCH_SHA256, 'original_certificates': sorted(original_certs),
                  'output_certificates': sorted(output_certs),
                  'ui_images': {'enabled': bool(image_manifest),
                                'manifest_sha256': sha256(image_manifest) if image_manifest else None,
                                'embedded_base_sha256': sha256(embedded_base),
                                'origin_alignment_verified': True if image_manifest else None},
                  'zip_alignment': {'bytes': 4, 'native_page_kb': 16, 'outputs_verified': True},
                  'input_apks': {p.name: sha256(p) for p in apks},
                  'output_apks': {p.name: sha256(p) for p in sorted(staged.glob('*.apk'))}}
        (staged / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        (staged / 'install.sh').write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nadb "$@" install-multiple -r ./*.apk\n', encoding='utf-8')
        (staged / 'install.ps1').write_text('$ErrorActionPreference = "Stop"\nSet-Location $PSScriptRoot\n$apks = @(Get-ChildItem *.apk | ForEach-Object { $_.FullName })\n& adb @args install-multiple -r @apks\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n', encoding='utf-8')
        if args.output.exists():
            args.output.rmdir()
        staged.rename(args.output)
    print(f'Created verified APKs: {args.output}\nGame {game_info.get("versionName")}, plugin {module_info.get("versionName")}')


def extract_apks(archive_path, destination):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Game input directory must be empty')
    with ZipFile(archive_path) as archive:
        if any(m.filename.lower().endswith('.obb') for m in archive.infolist()):
            raise ValueError('OBB expansion archives are not supported; provide the IDOLY PRIDE APK split set')
        members = [m for m in archive.infolist() if m.filename.endswith('.apk')]
        if not members or len(members) > 100 or sum(m.file_size for m in members) > LIMIT:
            raise ValueError('Invalid or oversized APK archive')
        names = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                    or ':' in member.filename or path.name.lower() in names):
                raise ValueError('Unsafe or duplicate APK archive entry')
            names.add(path.name.lower())
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            staged = Path(temporary) / 'apks'
            staged.mkdir()
            for member in members:
                with archive.open(member) as source, (staged / PurePosixPath(member.filename).name).open('wb') as target:
                    shutil.copyfileobj(source, target)
            if destination.exists():
                destination.rmdir()
            staged.rename(destination)


def pull(serial, destination):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Pull destination must be empty')
    listing = command('adb', '-s', serial, 'shell', 'pm', 'path', GAME)
    paths = [line.removeprefix('package:').strip() for line in listing.splitlines() if line.startswith('package:')]
    if not paths or len({PurePosixPath(p).name for p in paths}) != len(paths):
        raise ValueError('No unique installed game APKs found')
    destination.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.startswith('/') or not path.endswith('.apk'):
            raise ValueError('Unexpected installed APK path')
        command('adb', '-s', serial, 'pull', path, destination / PurePosixPath(path).name)
    print('Copied installed APKs only; no account data was read.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    key = commands.add_parser('init-key', help='Create a private, reusable signing key')
    key.add_argument('--keystore', type=Path, default=Path('secrets/patcher.jks'))
    key.add_argument('--key-alias', default='idoly-patcher')
    build = commands.add_parser('patch', help='Package game APKs with the localization module')
    build.add_argument('--game-dir', type=Path, default=Path('inputs/game'))
    selection = build.add_mutually_exclusive_group()
    selection.add_argument('--module', type=Path)
    selection.add_argument('--module-tag', help='Plugin release tag; default: latest stable APK release')
    build.add_argument('--lspatch', type=Path, help='Optional local copy of the pinned LSPatch JAR')
    build.add_argument('--keystore', type=Path, default=Path('secrets/patcher.jks'))
    build.add_argument('--key-alias', default='idoly-patcher')
    build.add_argument('--cache', type=Path, default=Path('cache'))
    build.add_argument('--output', type=Path, default=Path('output'))
    build.add_argument('--allow-untested-version', action='store_true')
    build.add_argument('--game-reference', type=Path, help='Trusted original reference from game_source.py')
    build.add_argument('--game-check', choices=('signature', 'exact'), default='signature',
                       help='Verify original signer or also require identical version and APK split bytes')
    images = build.add_mutually_exclusive_group()
    images.add_argument('--no-ui-images', action='store_true', help='Keep original game UI images')
    images.add_argument('--image-manifest', type=Path, default=DEFAULT_IMAGES,
                        help='Reviewed schema-2 image manifest; defaults to bundled 6.0.2 pack')
    check = commands.add_parser('check-images', help='Dry run image patch without signing or downloading')
    check.add_argument('--base', type=Path, default=Path('inputs/game/base.apk'))
    check.add_argument('--image-manifest', type=Path, default=DEFAULT_IMAGES)
    device = commands.add_parser('pull', help='Copy original installed APKs from a named device')
    device.add_argument('--serial', required=True)
    device.add_argument('--output', type=Path, default=Path('inputs/game'))
    args = parser.parse_args()
    try:
        if args.command == 'init-key':
            init_key(args.keystore, args.key_alias)
        elif args.command == 'check-images':
            check_images(args.base, args.image_manifest)
        elif args.command == 'patch':
            patch(args)
        else:
            pull(args.serial, args.output)
    except (ValueError, OSError, KeyError, BadZipFile) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
