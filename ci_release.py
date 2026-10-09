"""Plan, bundle and publish a verified game build; never publish before all uploads succeed."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from zipfile import ZipFile, ZIP_DEFLATED

import patcher
from game_source import load_reference

PLAN = Path('cache/release-plan.json')
REFERENCE = Path('game-reference.json')


def gh(*args):
    result = subprocess.run(['gh', *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('GitHub request failed; check permissions and connectivity')
    return result.stdout


def api(path, missing_ok=False, *, method='GET', data=None):
    arguments = ['gh', 'api', '--method', method, path]
    if data is not None:
        arguments += ['--input', '-']
    result = subprocess.run(arguments, input=json.dumps(data) if data is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        if missing_ok and '(HTTP 404)' in result.stderr:
            return None
        raise ValueError('GitHub API request failed; this is not treated as a missing release')
    return json.loads(result.stdout)


def find_release(repo, tag):
    current = api(f'repos/{repo}/releases/tags/{quote(tag, safe="")}', True)
    if current is not None:
        return current
    # GitHub's tag endpoint omits drafts until their tag is published.
    # Listing releases with the same authenticated client includes own drafts.
    for page in range(1, 101):
        releases = api(f'repos/{repo}/releases?per_page=100&page={page}')
        matches = [item for item in releases if item.get('tag_name') == tag]
        if len(matches) > 1:
            raise ValueError('Multiple releases use the selected tag')
        if matches:
            return matches[0]
        if len(releases) < 100:
            return None
    raise ValueError('Release listing exceeded pagination limit')


def repository():
    name = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', name):
        raise ValueError('Set GITHUB_REPOSITORY to the destination owner/repository')
    return name


def boolean(value):
    if value not in ('true', 'false'):
        raise ValueError('Boolean settings must be true or false')
    return value == 'true'


def recipe_hash():
    files = [Path(p) for p in ('patcher.py', 'game_source.py', 'ci_prepare.py', 'ci_release.py')]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.as_posix().encode() + b'\0' + bytes.fromhex(patcher.sha256(path)))
    return digest.hexdigest()


def release_assets(plan):
    return [plan['release_tag'] + '.zip', 'report.json', 'SHA256SUMS']


def build_plan(tag, game_version, game_check, allow_untested):
    load_reference(REFERENCE)
    if not re.fullmatch(r'\d+(?:\.\d+)+', game_version):
        raise ValueError('GAME_VERSION must be an explicit version such as 6.0.3')
    if game_check not in ('signature', 'exact'):
        raise ValueError('GAME_CHECK must be signature or exact')
    if game_version not in patcher.COMPATIBLE_VERSIONS and not allow_untested:
        raise ValueError('New game versions require explicit allow_untested_version approval')
    release, asset, digest = patcher.find_module_release(
        tag, loader=lambda endpoint: api(f'repos/{patcher.RELEASES}/{endpoint}'))
    plan = {'schema_version': 1, 'repository': repository(), 'game_version': game_version,
            'module_tag': release['tag_name'], 'module_sha256': digest,
            'module_url': asset['browser_download_url'],
            'game_check': game_check, 'allow_untested_version': allow_untested,
            'reference_sha256': patcher.sha256(REFERENCE), 'recipe_sha256': recipe_hash()}
    key = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:12]
    plan['release_tag'] = f'game-{game_version}-plugin-{release["tag_name"].removeprefix("v")}-{key}'
    current = find_release(plan['repository'], plan['release_tag'])
    build_needed = current is None or current.get('draft', False)
    if not build_needed:
        names = {a['name'] for a in current.get('assets', [])}
        if not set(release_assets(plan)) <= names:
            raise ValueError('Published release is incomplete; inspect it before rebuilding')
    return plan, build_needed


def save_plan(plan, build_needed):
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with Path(output).open('a', encoding='utf-8') as stream:
            stream.write(f'build_needed={str(build_needed).lower()}\n')
            stream.write(f'release_tag={plan["release_tag"]}\n')
    print(f'Plugin {plan["module_tag"]}: ' + ('build required' if build_needed else 'already published; skipped'))


def load_plan():
    return json.loads(PLAN.read_text(encoding='utf-8'))


def bundle(plan, output=Path('output'), destination=Path('dist')):
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    if report['module_sha256'] != plan['module_sha256'] or report['game_version'] != plan['game_version']:
        raise ValueError('Build differs from the selected game or plugin')
    check = report.get('game_source_verification') or {}
    if (check.get('reference_sha256') != plan['reference_sha256'] or not check.get('signer_match')
            or check.get('mode') != plan['game_check']
            or (plan['game_check'] == 'exact' and not check.get('all_split_bytes_match'))):
        raise ValueError('Build did not pass the required game source verification')
    if report.get('game_resources_unchanged') is not True:
        raise ValueError('Original game resource preservation was not verified')
    expected = report['output_apks']
    if not expected or 'base.apk' not in expected or set(expected) != {p.name for p in output.glob('*.apk')}:
        raise ValueError('Incomplete output APK set')
    for name, digest in expected.items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.apk', name) or patcher.sha256(output / name) != digest:
            raise ValueError('Output APK was modified after verification')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Release bundle directory is not empty')
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / release_assets(plan)[0]
    # Explicit allowlist: never package cache, source URLs, keys or input archives.
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=1) as zipped:
        for name in [*sorted(expected), 'report.json', 'install.sh', 'install.ps1']:
            path = output / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Invalid release file')
            zipped.write(path, name)
    (destination / 'report.json').write_bytes((output / 'report.json').read_bytes())
    (destination / 'SHA256SUMS').write_text(''.join(
        f'{patcher.sha256(p)}  {p.name}\n' for p in (archive, destination / 'report.json')), encoding='utf-8')
    notes = (f'《IDOLY PRIDE》日服 {report["game_version"]} + Idoly-localify {report["module_version"]}。\n\n'
             f'- ARM64；LSPatch 修补，无需 Root。\n'
             '- 字体和 UI 汉化图片由插件在运行时提供，游戏原始资源保持不变。\n'
             f'- 原包签名匹配可信基准；全部 APK 与基准字节一致：{"是" if check["all_split_bytes_match"] else "否（签名校验通过）"}。\n'
             f'- 插件来源：[正式 Release](https://github.com/{patcher.RELEASES}/releases/tag/{plan["module_tag"]})。\n\n'
             '下载 ZIP 并解压，使用其中的安装脚本安装全部 APK；不能只安装 base.apk。\n'
             '首次安装与商店原版签名不同，请先绑定账号并确认可重新登录；后续覆盖升级须使用同一签名密钥。\n')
    Path('cache/release-notes.md').write_text(notes, encoding='utf-8')
    print(f'Prepared verified release bundle: {archive.name}')


def publish(plan, destination=Path('dist')):
    if repository() != plan['repository']:
        raise ValueError('Release destination changed after planning')
    repo, tag = plan['repository'], plan['release_tag']
    current = find_release(repo, tag)
    if current and not current.get('draft'):
        raise ValueError('Release is already published; refusing to overwrite it')
    commit = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Missing exact source commit for release')
    title = f'IDOLY PRIDE {plan["game_version"]} / Idoly-localify {plan["module_tag"]}'
    metadata = {'name': title, 'body': Path('cache/release-notes.md').read_text(encoding='utf-8')}
    if not current:
        current = api(f'repos/{repo}/releases', method='POST',
                      data={**metadata, 'tag_name': tag, 'target_commitish': commit, 'draft': True})
    else:
        current = api(f'repos/{repo}/releases/{current["id"]}', method='PATCH', data=metadata)
    if not current or not current.get('draft') or not isinstance(current.get('id'), int):
        raise ValueError('Cannot locate the expected release draft')
    paths = [destination / name for name in release_assets(plan)]
    gh('release', 'upload', tag, *paths, '--repo', repo, '--clobber')
    uploaded = api(f'repos/{repo}/releases/{current["id"]}')
    if not uploaded.get('draft'):
        raise ValueError('Release was published concurrently before upload verification')
    assets = {a['name']: a for a in uploaded['assets']}
    if set(assets) != {p.name for p in paths}:
        raise ValueError('Unexpected release assets; draft remains unpublished')
    for path in paths:
        asset = assets[path.name]
        if asset['size'] != path.stat().st_size or asset.get('digest') != 'sha256:' + patcher.sha256(path):
            raise ValueError('Uploaded release checksum mismatch; draft remains unpublished')
    # Publishing is the final mutation. A failed upload can resume the same draft.
    api(f'repos/{repo}/releases/{current["id"]}', method='PATCH', data={'draft': False})
    print(f'Published https://github.com/{repo}/releases/tag/{tag}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'bundle', 'publish'))
    args = parser.parse_args()
    if args.command == 'plan':
        plan, needed = build_plan(os.environ.get('MODULE_TAG', '').strip() or None,
                                 os.environ.get('GAME_VERSION', patcher.TESTED_VERSION).strip(),
                                 os.environ.get('GAME_CHECK', 'signature'),
                                 boolean(os.environ.get('ALLOW_UNTESTED', 'false')))
        save_plan(plan, needed)
    elif args.command == 'bundle':
        bundle(load_plan())
    else:
        publish(load_plan())


if __name__ == '__main__':
    main()
