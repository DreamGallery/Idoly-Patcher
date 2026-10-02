#!/usr/bin/env python3
"""Apply source-bound UI region patches to built-in Unity assets.

Adapted from DreamGallery/Idoly-localify tools/patch_images.py (GPL-3.0).
See image-patches/NOTICE.md for provenance and artwork attribution.
"""

import argparse
from copy import copy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZipFile

from PIL import Image, ImageChops, ImageDraw
import UnityPy


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or not manifest.get("assets"):
        raise ValueError("Expected image patch schema_version 2 and nonempty assets")
    seen = set()
    for asset in manifest["assets"]:
        name = asset["entry"]
        if (not name.startswith("assets/bin/Data/") or ".." in PurePosixPath(name).parts
                or name in seen or not asset.get("textures")):
            raise ValueError("Invalid or repeated Unity asset entry")
        seen.add(name)
        ids = set()
        for texture in asset["textures"]:
            if texture["path_id"] in ids:
                raise ValueError("Repeated texture path_id")
            ids.add(texture["path_id"])
            if not texture.get("regions"):
                raise ValueError("Texture must contain reviewed regions")
            for region in texture["regions"]:
                png = (path.parent / region["png"]).resolve()
                if not png.is_relative_to(path.parent.resolve()):
                    raise ValueError("PNG must be inside the image patch directory")
                if digest(png.read_bytes()) != region["png_sha256"]:
                    raise ValueError(f"Translated PNG checksum differs: {png.name}")
    return manifest


def check_regions(original: Image.Image, replacement: Image.Image, regions: list) -> None:
    """Require every changed channel, including transparent RGB, inside reviewed regions."""
    if original.size != replacement.size:
        raise ValueError("Replacement texture dimensions differ")
    if not regions:
        raise ValueError("At least one reviewed edit region is required")
    mask = Image.new("L", original.size)
    draw = ImageDraw.Draw(mask)
    width, height = original.size
    for region in regions:
        if (len(region) != 4 or any(type(x) is not int for x in region)
                or not 0 <= region[0] < region[2] <= width
                or not 0 <= region[1] < region[3] <= height):
            raise ValueError("Invalid edit region")
        draw.rectangle((region[0], region[1], region[2] - 1, region[3] - 1), fill=255)
    delta = ImageChops.difference(original.convert("RGBA"), replacement.convert("RGBA"))
    outside = ImageChops.invert(mask)
    if any(ImageChops.multiply(channel, outside).getbbox() for channel in delta.split()):
        raise ValueError("PNG changes pixels outside reviewed edit regions")


def sprite_padding_region(box: list[int], padding: int, atlas: dict, sprite_key,
                          texture_id: int, size: tuple[int, int], *,
                          edit_box: list[int] | None = None) -> list[int]:
    """Bound refreshed edge extrusion without touching another packed sprite."""
    if type(padding) is not int or not 0 <= padding <= 4:
        raise ValueError("Sprite clear_padding must be an integer from 0 to 4")
    edit = box if edit_box is None else edit_box
    if not (box[0] <= edit[0] < edit[2] <= box[2]
            and box[1] <= edit[1] < edit[3] <= box[3]):
        raise ValueError("Padding edit must be inside its packed sprite")
    width, height = size
    region = [max(0, edit[0] - (padding if edit[0] == box[0] else 0)),
              max(0, edit[1] - (padding if edit[1] == box[1] else 0)),
              min(width, edit[2] + (padding if edit[2] == box[2] else 0)),
              min(height, edit[3] + (padding if edit[3] == box[3] else 0))]
    for key, packed in atlas['m_RenderDataMap']:
        if key == sprite_key or packed['texture']['m_PathID'] != texture_id:
            continue
        rect = packed['textureRect']
        # Fractional bounds plus half a texel protect neighbors' filter samples.
        other = [rect['x'] - 0.5, height - rect['y'] - rect['height'] - 0.5,
                 rect['x'] + rect['width'] + 0.5, height - rect['y'] + 0.5]
        if (region[0] < other[2] and other[0] < region[2]
                and region[1] < other[3] and other[1] < region[3]):
            raise ValueError("Sprite padding overlaps another packed sprite")
    return region


def refresh_sprite_padding(image: Image.Image, box: list[int], region: list[int]) -> None:
    """Extrude updated edges; keep circles opaque and new label margins clear."""
    for y in range(region[1], region[3]):
        for x in range(region[0], region[2]):
            if box[0] <= x < box[2] and box[1] <= y < box[3]:
                continue
            source_x = min(max(x, box[0]), box[2] - 1)
            source_y = min(max(y, box[1]), box[3] - 1)
            image.putpixel((x, y), image.getpixel((source_x, source_y)))


def replace_asset(data: bytes, specification: dict, directory: Path) -> bytes:
    if digest(data) != specification["source_sha256"]:
        raise ValueError(f"Original Unity asset checksum differs: {specification['entry']}")
    environment = UnityPy.load(data)
    objects = {obj.path_id: obj for obj in environment.objects}
    baseline = {key: obj.get_raw_data() for key, obj in objects.items()}
    targets = {}
    for item in specification["textures"]:
        obj = objects.get(item["path_id"])
        if obj is None or obj.type.name != "Texture2D":
            raise ValueError("Texture path_id is absent or not Texture2D")
        texture = obj.read()
        if texture.m_Name != item["name"]:
            raise ValueError("Texture name differs")
        # Streamed textures need a separate resource-file writer; fail instead of
        # implicitly resolving files outside the reviewed APK entry.
        if texture.m_StreamData and texture.m_StreamData.size:
            raise ValueError("Only inline built-in textures are supported")
        original = texture.image.convert("RGBA")
        translated = original.copy()
        for region in item["regions"]:
            box = region["box"]
            # Validate bounds before cropping/pasting, including transparent RGB.
            check_regions(original, original, [box])
            with Image.open(directory / region["png"]) as image:
                if image.size != (box[2] - box[0], box[3] - box[1]):
                    raise ValueError("Region PNG dimensions differ")
                translated.paste(image.convert("RGBA"), (box[0], box[1]))
        check_regions(original, translated, [region["box"] for region in item["regions"]])
        if digest(translated.tobytes()) != item["rgba_sha256"]:
            raise ValueError("Reconstructed texture pixels differ from reviewed image")
        texture.set_image(translated, target_format=4)  # Lossless RGBA32.
        texture.save()
        targets[obj.path_id] = translated.tobytes()
    result = environment.file.save()
    checked = {obj.path_id: obj for obj in UnityPy.load(result).objects}
    if set(checked) != set(objects):
        raise ValueError("Texture patch changed the Unity object list")
    for key, obj in checked.items():
        if key in targets:
            if obj.read().image.convert("RGBA").tobytes() != targets[key]:
                raise ValueError("Repacked texture pixels differ")
        elif obj.get_raw_data() != baseline[key]:
            raise ValueError(f"Texture patch modified unrelated object {key}")
    return result


def patch_apk(source: Path, manifest_path: Path, output: Path) -> None:
    if source.resolve() == output.resolve():
        raise ValueError("Input APK must not be overwritten")
    manifest = load_manifest(manifest_path)
    patches = {}
    with ZipFile(source) as original:
        if len(original.namelist()) != len(set(original.namelist())):
            raise ValueError("Duplicate APK entries")
        for asset in manifest["assets"]:
            patches[asset["entry"]] = replace_asset(
                original.read(asset["entry"]), asset, manifest_path.parent)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".image-patch-", dir=output.parent) as temp:
            candidate = Path(temp) / "candidate.apk"
            with ZipFile(candidate, "w") as destination:
                destination.comment = original.comment
                for entry in original.infolist():
                    destination.writestr(copy(entry), patches.get(entry.filename)
                                         if entry.filename in patches else original.read(entry))
            with ZipFile(candidate) as checked:
                for name in original.namelist():
                    expected = patches[name] if name in patches else original.read(name)
                    if checked.read(name) != expected:
                        raise ValueError(f"APK verification failed: {name}")
            candidate.replace(output)


def verify_apk(apk: Path | BytesIO, manifest_path: Path) -> None:
    manifest = load_manifest(manifest_path)
    with ZipFile(apk) as package:
        for asset in manifest["assets"]:
            objects = {obj.path_id: obj for obj in UnityPy.load(package.read(asset["entry"])).objects}
            for item in asset["textures"]:
                texture = objects[item["path_id"]].read()
                if (texture.m_Name != item["name"]
                        or digest(texture.image.convert("RGBA").tobytes()) != item["rgba_sha256"]):
                    raise ValueError("Final APK texture differs from reviewed pixels")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    patch_apk(args.base, args.manifest, args.output)
    verify_apk(args.output, args.manifest)
    print("Verified texture edits; APK must be signed before installation")


if __name__ == "__main__":
    main()
