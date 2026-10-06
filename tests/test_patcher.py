from io import BytesIO
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import patcher


class PatcherTests(unittest.TestCase):
    def test_lspatch_java_rejects_old_runtime_before_patching(self):
        for version, supported in (('openjdk 17.0.20 2026-07-21', False),
                                   ('openjdk 21.0.9 2025-10-21 LTS', True),
                                   ('java 25 2025-09-16', True)):
            with self.subTest(version=version), \
                    patch.object(patcher, 'java_tool', return_value='/jdk/bin/java'), \
                    patch.object(patcher, 'command', return_value=version):
                if supported:
                    self.assertEqual(patcher.lspatch_java(), '/jdk/bin/java')
                else:
                    with self.assertRaisesRegex(ValueError, 'JDK 21'):
                        patcher.lspatch_java()

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

class ImageWrapperTests(unittest.TestCase):
    def test_real_zipalign_survives_embedding_and_preserves_payloads(self):
        try:
            patcher.sdk_tool('zipalign')
        except ValueError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, aligned, wrapper, extracted = [root / n for n in ('source.apk', 'aligned.apk', 'wrapper.apk', 'origin.apk')]
            with ZipFile(source, 'w') as apk:
                apk.writestr('resources.arsc', b'resource bytes')
                apk.writestr('lib/arm64-v8a/libtest.so', b'native bytes')
            patcher.align_apk(source, aligned)
            with ZipFile(wrapper, 'w') as apk:
                apk.writestr('assets/lspatch/origin.apk', aligned.read_bytes())
            with ZipFile(wrapper) as apk:
                extracted.write_bytes(apk.read('assets/lspatch/origin.apk'))
            patcher.verify_alignment(extracted)
            with ZipFile(extracted) as apk, extracted.open('rb') as stream:
                for name, alignment in [('resources.arsc', 4), ('lib/arm64-v8a/libtest.so', 16384)]:
                    entry = apk.getinfo(name)
                    stream.seek(entry.header_offset + 26)
                    name_length, extra_length = struct.unpack('<HH', stream.read(4))
                    offset = entry.header_offset + 30 + name_length + extra_length
                    self.assertEqual(offset % alignment, 0)

    def test_updates_both_resource_copies_and_preserves_signature_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, modified, wrapper, result = [root / n for n in ('original.apk', 'modified.apk', 'wrapper.apk', 'result.apk')]
            entry = 'assets/bin/Data/atlas'
            for path, pixels in ((original, b'original'), (modified, b'translated')):
                with ZipFile(path, 'w') as apk:
                    apk.writestr(entry, pixels)
                    apk.writestr('other', b'unchanged')
            with ZipFile(wrapper, 'w') as apk:
                apk.writestr(entry, b'original')
                apk.writestr('assets/lspatch/origin.apk', original.read_bytes())
                apk.writestr('assets/lspatch/config.json', b'original signing certificate')
                apk.writestr('classes.dex', b'loader')
            patcher.patch_wrapper(wrapper, result, original, modified, {'assets': [{'entry': entry}]})
            with ZipFile(result) as apk:
                self.assertEqual(apk.read(entry), b'translated')
                self.assertEqual(apk.read('assets/lspatch/origin.apk'), modified.read_bytes())
                self.assertEqual(apk.read('assets/lspatch/config.json'), b'original signing certificate')
                self.assertEqual(apk.read('classes.dex'), b'loader')
            with self.assertRaisesRegex(ValueError, 'origin differs'):
                patcher.patch_wrapper(result, root / 'repeat.apk', original, modified, {'assets': [{'entry': entry}]})
            self.assertFalse((root / 'repeat.apk').exists())


if __name__ == '__main__':
    unittest.main()
