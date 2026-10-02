from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image

from patch_images import (check_regions, load_manifest, patch_apk, replace_asset,
                          refresh_sprite_padding, sprite_padding_region)


class ImagePatchTests(unittest.TestCase):
    def test_padding_clears_old_letters_but_preserves_opaque_circle_edges(self):
        image = Image.new('RGBA', (12, 12), (255, 255, 255, 120))
        box = [3, 3, 9, 9]
        image.paste((255, 255, 255, 0), tuple(box))
        image.putpixel((3, 5), (0, 0, 255, 200))
        before = image.copy()
        region = sprite_padding_region(box, 2, {'m_RenderDataMap': []}, 'self', 1, image.size)
        refresh_sprite_padding(image, box, region)
        self.assertEqual(image.getpixel((5, 2)), (255, 255, 255, 0))
        self.assertEqual(image.getpixel((1, 5)), (0, 0, 255, 200))
        self.assertEqual(image.crop(tuple(box)).tobytes(), before.crop(tuple(box)).tobytes())
        check_regions(before, image, [region])

    def test_partial_label_padding_preserves_icon_above_and_clips_texture_edge(self):
        box = [0, 2, 8, 12]
        region = sprite_padding_region(box, 2, {'m_RenderDataMap': []}, 'self', 1,
                                       (12, 12), edit_box=[0, 8, 8, 12])
        self.assertEqual(region, [0, 8, 10, 12])
        image = Image.new('RGBA', (12, 12), (255, 255, 255, 120))
        image.paste((0, 0, 0, 0), (0, 8, 8, 12))
        before = image.copy()
        refresh_sprite_padding(image, box, region)
        self.assertEqual(image.crop((0, 0, 12, 8)).tobytes(), before.crop((0, 0, 12, 8)).tobytes())
        self.assertIsNone(image.crop((8, 8, 10, 12)).getchannel('A').getbbox())

    def test_padding_protects_fractional_neighbor_and_its_filter_margin(self):
        atlas = {'m_RenderDataMap': [('neighbor', {
            'texture': {'m_PathID': 1},
            'textureRect': {'x': 8.25, 'y': 4, 'width': 2, 'height': 4}})]}
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            sprite_padding_region([2, 2, 6, 6], 2, atlas, 'self', 1, (12, 12))
        atlas['m_RenderDataMap'][0][1]['texture']['m_PathID'] = 2
        self.assertEqual(sprite_padding_region([2, 2, 6, 6], 2, atlas, 'self', 1,
                                              (12, 12)), [0, 0, 8, 8])

    def test_region_guard_checks_rgb_even_under_transparency(self):
        original = Image.new("RGBA", (8, 8))
        changed = original.copy()
        changed.putpixel((2, 2), (255, 255, 255, 255))
        check_regions(original, changed, [[1, 1, 4, 4]])
        changed.putpixel((7, 7), (255, 0, 0, 0))
        with self.assertRaisesRegex(ValueError, "outside"):
            check_regions(original, changed, [[1, 1, 4, 4]])

    def test_dimensions_and_invalid_regions_are_rejected(self):
        image = Image.new("RGBA", (8, 8))
        for bounds in ([], [[0, 0, 9, 8]], [[4, 4, 4, 5]], [[0, 0, 3.5, 3]]):
            with self.assertRaises(ValueError):
                check_regions(image, image, bounds)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            check_regions(image, Image.new("RGBA", (9, 8)), [[0, 0, 8, 8]])

    def test_updated_game_data_is_not_silently_patched(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            replace_asset(b"new version", {"entry": "asset", "source_sha256": "old"}, Path("."))

    def test_manifest_binds_png_and_refuses_parent_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image.png").write_bytes(b"reviewed")
            item = {"path_id": 19, "png": "image.png", "png_sha256": sha256(b"reviewed").hexdigest()}
            data = {"schema_version": 2, "assets": [{"entry": "assets/bin/Data/atlas", "textures": [{"path_id": 19, "regions": [item]}]}]}
            path = root / "manifest.json"
            path.write_text(json.dumps(data))
            load_manifest(path)
            (root / "image.png").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_manifest(path)
            item["png"] = "../image.png"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "inside"):
                load_manifest(path)

    def test_texture_id_name_and_streamed_resource_guards(self):
        data = b"bound source"
        spec = {"entry": "asset", "source_sha256": sha256(data).hexdigest(),
                "textures": [{"path_id": 19, "name": "expected"}]}
        obj = Mock(path_id=19)
        obj.type.name = "Texture2D"
        obj.get_raw_data.return_value = b"raw"
        obj.read.return_value = SimpleNamespace(m_Name="different")
        with patch("patch_images.UnityPy.load", return_value=SimpleNamespace(objects=[obj])):
            with self.assertRaisesRegex(ValueError, "name differs"):
                replace_asset(data, spec, Path("."))
            obj.path_id = 20
            with self.assertRaisesRegex(ValueError, "path_id"):
                replace_asset(data, spec, Path("."))
            obj.path_id = 19
            obj.read.return_value = SimpleNamespace(m_Name="expected", m_StreamData=SimpleNamespace(size=10))
            with self.assertRaisesRegex(ValueError, "inline"):
                replace_asset(data, spec, Path("."))

    def test_zip_writer_preserves_other_entries_and_failed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.apk", root / "output.apk"
            with ZipFile(source, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("assets/bin/Data/atlas", b"atlas")
                archive.writestr("other", b"other-data")
            manifest = {"assets": [{"entry": "assets/bin/Data/atlas"}]}
            with patch("patch_images.load_manifest", return_value=manifest), patch(
                    "patch_images.replace_asset", return_value=b"translated-atlas"):
                patch_apk(source, root / "manifest.json", output)
            with ZipFile(output) as archive:
                self.assertEqual(archive.read("AndroidManifest.xml"), b"manifest")
                self.assertEqual(archive.read("other"), b"other-data")
                self.assertEqual(archive.read("assets/bin/Data/atlas"), b"translated-atlas")
            good = output.read_bytes()
            with patch("patch_images.load_manifest", return_value=manifest), patch(
                    "patch_images.replace_asset", side_effect=ValueError("bad source")):
                with self.assertRaises(ValueError):
                    patch_apk(source, root / "manifest.json", output)
            self.assertEqual(output.read_bytes(), good)


if __name__ == "__main__":
    unittest.main()
