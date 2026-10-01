from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import patcher


class PatcherTests(unittest.TestCase):
    def test_plugin_release_skips_text_only_latest_and_prerelease(self):
        asset = {'name': 'idoly-localify-0.2.1.apk', 'digest': 'sha256:' + 'a' * 64,
                 'browser_download_url': 'https://github.com/' + patcher.RELEASES + '/releases/download/v0.2.1/idoly-localify-0.2.1.apk'}
        releases = [{'assets': [{'name': 'manifest.json'}]},
                    {'prerelease': True, 'assets': [asset]}, {'assets': [asset]}]
        self.assertEqual(patcher.plugin_asset(releases), (asset, 'a' * 64))
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            patcher.plugin_asset([{'assets': [{**asset, 'digest': None}]}])

    def test_signer_formats(self):
        for name in ('Signer', 'Signer #1', 'V3.0 Signer:'):
            with patch.object(patcher, 'sdk_tool', return_value='apksigner'), \
                 patch.object(patcher, 'command', return_value=f'{name} certificate SHA-256 digest: ' + 'a' * 64 + '\nSource Stamp Signer: certificate SHA-256 digest: ' + 'b' * 64):
                self.assertEqual(patcher.certificates(Path('test.apk')), {'a' * 64})

    def test_game_rejects_mismatched_versions_and_missing_arm64(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('base.apk', 'split.apk')]
            for path in paths:
                with ZipFile(path, 'w') as archive:
                    archive.writestr('AndroidManifest.xml', b'test')
            base = {'name': patcher.GAME, 'versionCode': '405', 'versionName': '6.0.2'}
            with patch.object(patcher, 'package', side_effect=[base, {**base, 'versionCode': '406', 'split': 'config.arm64_v8a'}]), \
                 patch.object(patcher, 'certificates', return_value={'a' * 64}):
                with self.assertRaisesRegex(ValueError, 'same IDOLY PRIDE version'):
                    patcher.validate_game(paths)
            with patch.object(patcher, 'package', return_value=base), \
                 patch.object(patcher, 'certificates', return_value={'a' * 64}):
                with self.assertRaisesRegex(ValueError, 'ARM64'):
                    patcher.validate_game(paths[:1])

    def test_archive_rejects_traversal_duplicate_and_empty(self):
        for names in (['../base.apk'], ['/base.apk'], ['a/base.apk', 'b/base.apk'],
                      ['a.apk', 'A.apk'], ['C:\\base.apk'], ['readme.txt']):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'game.zip'
                with ZipFile(archive, 'w') as bundle:
                    for name in names:
                        bundle.writestr(name, b'test')
                with self.assertRaises(ValueError):
                    patcher.extract_apks(archive, root / 'game')
                self.assertFalse((root / 'game').exists())

    def test_archive_flattens_split_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ZipFile(root / 'game.zip', 'w') as bundle:
                bundle.writestr('game/base.apk', b'base')
                bundle.writestr('game/split_config.arm64_v8a.apk', b'split')
            patcher.extract_apks(root / 'game.zip', root / 'game')
            self.assertEqual((root / 'game/base.apk').read_bytes(), b'base')
            self.assertEqual(len(list((root / 'game').iterdir())), 2)

    def test_embedded_original_and_plugin_must_be_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, module, output = [root / name for name in ('base.apk', 'module.apk', 'output.apk')]
            original.write_bytes(b'original')
            module.write_bytes(b'module')
            with ZipFile(output, 'w') as archive:
                archive.writestr('assets/lspatch/origin.apk', b'original')
                archive.writestr(f'assets/lspatch/modules/{patcher.MODULE}.apk', b'module')
            patcher.verify_embedded(output, original, module)
            module.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'changed'):
                patcher.verify_embedded(output, original, module)

    def test_download_checksum_failure_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'download'
            with patch.object(patcher, 'open_https', return_value=BytesIO(b'wrong')):
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    patcher.download('https://example.invalid/file', target, 'a' * 64)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_sdk_version_order_is_numeric(self):
        with tempfile.TemporaryDirectory() as directory:
            for version in ('9.0.0', '35.0.0', '36.0.0-rc1'):
                path = Path(directory) / 'build-tools' / version / 'aapt'
                path.parent.mkdir(parents=True)
                path.touch()
            with patch.dict('os.environ', {'ANDROID_HOME': directory}), patch.object(patcher.os, 'name', 'posix'):
                self.assertIn('35.0.0', patcher.sdk_tool('aapt'))


if __name__ == '__main__':
    unittest.main()
