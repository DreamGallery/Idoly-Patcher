from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import ci_release
import ci_prepare
import game_source
import patcher


@contextmanager
def workspace():
    with tempfile.TemporaryDirectory() as temporary:
        previous = Path.cwd()
        os.chdir(temporary)
        try:
            yield Path(temporary)
        finally:
            os.chdir(previous)


def reference():
    return {'schema_version': 1, 'package': patcher.GAME, 'version_name': '6.0.2',
            'version_code': '405', 'certificates_sha256': ['a' * 64],
            'splits_sha256': {'base': 'b' * 64, 'config.arm64_v8a': 'c' * 64}}


class SourceTests(unittest.TestCase):
    def test_xapk_names_are_normalized_before_lspatch_without_changing_bytes(self):
        with workspace():
            base = Path(patcher.GAME + '.apk')
            split = Path('config.arm64_v8a.apk')
            base.write_bytes(b'original base')
            split.write_bytes(b'original native split')
            metadata = {base: {}, split: {'split': 'config.arm64_v8a'}}
            with patch.object(patcher, 'package', side_effect=lambda p: metadata[p]):
                staged = patcher.stage_game_apks([split, base], Path('canonical'))
            self.assertEqual([p.name for p in staged], ['base.apk', 'split_config.arm64_v8a.apk'])
            self.assertEqual(staged[0].read_bytes(), base.read_bytes())
            self.assertEqual(staged[1].read_bytes(), split.read_bytes())

    def verify(self, actual, mode='signature'):
        with workspace():
            path = Path('game-reference.json')
            path.write_text(json.dumps(reference()))
            with patch.object(game_source, 'fingerprint', return_value=actual):
                return game_source.verify_reference([], path, mode)

    def test_old_play_certificate_accepts_new_version_but_exact_does_not(self):
        actual = reference()
        actual.update(version_name='6.1.0', version_code='406', splits_sha256={'base': 'd' * 64})
        result = self.verify(actual)
        self.assertTrue(result['signer_match'])
        self.assertFalse(result['reference_version_match'])
        self.assertFalse(result['all_split_bytes_match'])
        with self.assertRaisesRegex(ValueError, 'exact verification failed'):
            self.verify(actual, 'exact')

    def test_matching_hashes_and_wrong_certificate(self):
        self.assertTrue(self.verify(reference(), 'exact')['all_split_bytes_match'])
        actual = reference()
        actual['certificates_sha256'] = ['d' * 64]
        for mode in ('signature', 'exact'):
            with self.assertRaisesRegex(ValueError, 'certificates_sha256'):
                self.verify(actual, mode)

    def test_different_split_variant_is_never_claimed_identical(self):
        actual = reference()
        actual['splits_sha256']['config.ja'] = 'e' * 64
        self.assertFalse(self.verify(actual)['all_split_bytes_match'])
        with self.assertRaises(ValueError):
            self.verify(actual, 'exact')

    def test_play_reference_rejects_sideload_and_never_overwrites(self):
        with workspace():
            path = Path('game-reference.json')
            with patch.object(patcher, 'command', return_value=f'package:{patcher.GAME} installer=null\n'), \
                    patch.object(patcher, 'pull') as pull:
                with self.assertRaisesRegex(ValueError, 'Google Play'):
                    game_source.record_play_reference('device', path)
                pull.assert_not_called()
            path.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                game_source.record_play_reference('device', path)
            self.assertEqual(path.read_text(), 'keep')

    def test_xapk_metadata_and_renamed_base_are_supported_but_obb_is_not(self):
        with workspace():
            archive = Path('game.xapk')
            with ZipFile(archive, 'w') as z:
                z.writestr('manifest.json', '{"package_name":"game.qualiarts.idolypride"}')
                z.writestr(patcher.GAME + '.apk', b'base')
                z.writestr('config.arm64_v8a.apk', b'split')
            with game_source.apk_inputs(None, archive) as apks:
                self.assertEqual(len(apks), 2)
                self.assertEqual(sorted(p.read_bytes() for p in apks), [b'base', b'split'])
            with ZipFile(archive, 'a') as z:
                z.writestr('Android/obb/main.obb', b'extra')
            with self.assertRaisesRegex(ValueError, 'OBB'):
                with game_source.apk_inputs(None, archive):
                    pass


def plugin_release():
    tag = 'v0.2.5'
    return {'tag_name': tag, 'assets': [{
        'name': 'idoly-localify-0.2.5.apk', 'digest': 'sha256:' + 'f' * 64,
        'browser_download_url': f'https://github.com/{patcher.RELEASES}/releases/download/{tag}/idoly-localify-0.2.5.apk'}]}


class PlanningTests(unittest.TestCase):
    def test_text_releases_do_not_trigger_and_existing_build_skips(self):
        with workspace(), patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/patcher'}):
            Path('game-reference.json').write_text(json.dumps(reference()))
            response = [{'tag_name': 'text-new', 'assets': [{'name': 'manifest.json'}]}, plugin_release()]
            with patch.object(ci_release, 'recipe_hash', return_value='1' * 64), \
                    patch.object(ci_release, 'api', side_effect=[response, None, []]):
                plan, needed = ci_release.build_plan(None, '6.0.2', 'signature', False)
            self.assertTrue(needed)
            self.assertEqual(plan['module_tag'], 'v0.2.5')
            self.assertEqual(plan['module_sha256'], 'f' * 64)
            published = {'draft': False, 'assets': [{'name': n} for n in ci_release.release_assets(plan)]}
            with patch.object(ci_release, 'recipe_hash', return_value='1' * 64), \
                    patch.object(ci_release, 'api', side_effect=[response, published]):
                repeated, needed = ci_release.build_plan(None, '6.0.2', 'signature', False)
            self.assertEqual(repeated, plan)
            self.assertFalse(needed)
            with patch.object(ci_release, 'recipe_hash', return_value='1' * 64), \
                    patch.object(ci_release, 'api', side_effect=[response, {'draft': True}]):
                _, needed = ci_release.build_plan(None, '6.0.2', 'signature', False)
            self.assertTrue(needed)

    def test_permission_failure_is_not_mistaken_for_missing_release(self):
        for code in (401, 403, 500):
            result = subprocess.CompletedProcess([], 1, '', f'gh: error (HTTP {code})')
            with patch.object(subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'not treated as a missing release'):
                    ci_release.api('repos/a/b/releases/tags/test', True)

    def test_missing_reference_never_queries_upstream(self):
        with workspace(), patch.object(ci_release, 'api') as api:
            with self.assertRaises(FileNotFoundError):
                ci_release.build_plan(None, '6.0.2', 'signature', False)
            api.assert_not_called()

    def test_draft_is_found_when_the_tag_endpoint_returns_404(self):
        draft = {'id': 42, 'tag_name': 'game-6.0.2-plugin-0.3.0-test', 'draft': True}
        with patch.object(ci_release, 'api', side_effect=[None, [draft]]) as api:
            self.assertEqual(ci_release.find_release('owner/patcher', draft['tag_name']), draft)
        self.assertIn('releases?per_page=100&page=1', api.call_args_list[-1].args[0])

    def test_prepare_refuses_wrong_game_version_before_signing_or_plugin_download(self):
        with workspace(), patch.dict(os.environ, {'GAME_APKS_URL': 'https://example.invalid/game.xapk',
                                                 'PATCH_KEYSTORE_BASE64': 'dGVzdA==', 'IDOLY_KS_PASS': 'test'}):
            Path('game-reference.json').write_text(json.dumps(reference()))
            Path('inputs/game').mkdir(parents=True)
            Path('inputs/game/base.apk').write_bytes(b'test')
            plan = {'game_version': '6.0.2', 'game_check': 'signature',
                    'reference_sha256': patcher.sha256('game-reference.json')}
            with patch.object(ci_prepare, 'load_plan', return_value=plan), \
                    patch.object(ci_prepare, 'download', return_value=Path('game.xapk')) as download, \
                    patch.object(ci_prepare, 'extract_apks'), \
                    patch.object(ci_prepare, 'verify_reference'), \
                    patch.object(ci_prepare, 'package', return_value={'versionName': '6.1.0'}):
                with self.assertRaisesRegex(ValueError, 'no fallback to latest'):
                    ci_prepare.main()
            self.assertEqual(download.call_count, 1)
            self.assertFalse(Path('secrets/patcher.jks').exists())

    def test_prepare_accepts_play_split_without_version_name(self):
        with workspace(), patch.dict(os.environ, {'GAME_APKS_URL': 'https://example.invalid/game.xapk',
                                                 'PATCH_KEYSTORE_BASE64': 'dGVzdA==', 'IDOLY_KS_PASS': 'test'}):
            Path('game-reference.json').write_text(json.dumps(reference()))
            Path('inputs/game').mkdir(parents=True)
            for name in ('base.apk', 'config.arm64_v8a.apk'):
                Path('inputs/game', name).write_bytes(b'test')
            plan = {'game_version': '6.0.2', 'game_check': 'signature',
                    'reference_sha256': patcher.sha256('game-reference.json'),
                    'module_url': 'https://example.invalid/module.apk', 'module_sha256': 'f' * 64}
            metadata = {'base.apk': {'versionName': '6.0.2', 'versionCode': '405'},
                        'config.arm64_v8a.apk': {'split': 'config.arm64_v8a',
                                                'versionName': '', 'versionCode': '405'}}
            with patch.object(ci_prepare, 'load_plan', return_value=plan), \
                    patch.object(ci_prepare, 'download', return_value=Path('game.xapk')) as download, \
                    patch.object(ci_prepare, 'extract_apks'), \
                    patch.object(ci_prepare, 'verify_reference') as verify, \
                    patch.object(ci_prepare, 'package', side_effect=lambda p: metadata[p.name]):
                ci_prepare.main()
            verify.assert_called_once()
            self.assertEqual(download.call_count, 2)
            self.assertEqual(Path('secrets/patcher.jks').read_bytes(), b'test')


class PublicationTests(unittest.TestCase):
    def fixture(self):
        Path('cache').mkdir()
        Path('output').mkdir()
        Path('output/base.apk').write_bytes(b'verified APK fixture')
        Path('output/install.sh').write_text('install')
        Path('output/install.ps1').write_text('install')
        plan = {'repository': 'owner/patcher', 'release_tag': 'game-6.0.2-plugin-0.2.5-test',
                'game_version': '6.0.2', 'module_tag': 'v0.2.5', 'module_sha256': 'f' * 64,
                'reference_sha256': 'a' * 64, 'game_check': 'signature'}
        report = {'game_version': '6.0.2', 'module_version': '0.2.5', 'module_sha256': 'f' * 64,
                  'game_resources_unchanged': True, 'output_apks': {'base.apk': patcher.sha256('output/base.apk')},
                  'game_source_verification': {'mode': 'signature', 'signer_match': True,
                                               'reference_sha256': 'a' * 64, 'all_split_bytes_match': True}}
        Path('output/report.json').write_text(json.dumps(report))
        return plan

    def assets(self, plan):
        return [{'name': name, 'size': (Path('dist') / name).stat().st_size,
                 'digest': 'sha256:' + patcher.sha256(Path('dist') / name)}
                for name in ci_release.release_assets(plan)]

    def test_bundle_excludes_unexpected_files_and_checks_post_build_tamper(self):
        with workspace():
            plan = self.fixture()
            Path('output/private.key').write_text('not for upload')
            ci_release.bundle(plan)
            with ZipFile(Path('dist') / ci_release.release_assets(plan)[0]) as archive:
                self.assertEqual(set(archive.namelist()), {'base.apk', 'report.json', 'install.sh', 'install.ps1'})
            Path('output/base.apk').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'modified after verification'):
                ci_release.bundle(plan)

    def test_publish_occurs_only_after_remote_checksums_match(self):
        with workspace(), patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/patcher', 'GITHUB_SHA': '1' * 40}):
            plan = self.fixture()
            ci_release.bundle(plan)
            with patch.object(ci_release, 'find_release', return_value=None), \
                    patch.object(ci_release, 'api', side_effect=[{'id': 42, 'draft': True},
                        {'draft': True, 'assets': self.assets(plan)}, {'draft': False}]) as api, \
                    patch.object(ci_release, 'gh') as gh:
                ci_release.publish(plan)
            self.assertEqual(api.call_args_list[0].kwargs['method'], 'POST')
            self.assertTrue(api.call_args_list[0].kwargs['data']['draft'])
            self.assertEqual(api.call_args_list[1].args, ('repos/owner/patcher/releases/42',))
            self.assertEqual(api.call_args_list[-1].kwargs, {'method': 'PATCH', 'data': {'draft': False}})
            self.assertEqual(gh.call_args_list[0].args[:2], ('release', 'upload'))

    def test_failed_upload_and_bad_remote_hash_leave_draft_unpublished(self):
        with workspace(), patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/patcher', 'GITHUB_SHA': '1' * 40}):
            plan = self.fixture()
            ci_release.bundle(plan)
            with patch.object(ci_release, 'find_release', return_value=None), \
                    patch.object(ci_release, 'api', return_value={'id': 42, 'draft': True}) as api, \
                    patch.object(ci_release, 'gh', side_effect=ValueError('upload failed')):
                with self.assertRaisesRegex(ValueError, 'upload failed'):
                    ci_release.publish(plan)
                self.assertFalse(any(c.kwargs.get('data') == {'draft': False} for c in api.call_args_list))
            assets = self.assets(plan)
            assets[0]['digest'] = 'sha256:' + '0' * 64
            with patch.object(ci_release, 'find_release', return_value={'id': 42, 'draft': True}), \
                    patch.object(ci_release, 'api', return_value={'id': 42, 'draft': True, 'assets': assets}) as api, \
                    patch.object(ci_release, 'gh') as gh:
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    ci_release.publish(plan)
                self.assertFalse(any(c.kwargs.get('data') == {'draft': False} for c in api.call_args_list))

    def test_published_release_is_never_overwritten(self):
        with workspace(), patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/patcher'}):
            plan = self.fixture()
            with patch.object(ci_release, 'find_release', return_value={'draft': False}), patch.object(ci_release, 'gh') as gh:
                with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
                    ci_release.publish(plan)
                gh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
