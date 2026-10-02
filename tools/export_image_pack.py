#!/usr/bin/env python3
"""Export only reviewed regions from an Idoly-localify schema-1 image manifest."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from zipfile import ZipFile

from PIL import Image
import UnityPy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from patch_images import check_regions


def export(base, source, output):
    manifest = json.loads(source.read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('Expected original schema-1 manifest')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty')
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 2, 'game_version': '6.0.2',
              'source_manifest_sha256': sha256(source.read_bytes()).hexdigest(), 'assets': []}
    with ZipFile(base) as apk:
        for asset in manifest['assets']:
            data = apk.read(asset['entry'])
            if sha256(data).hexdigest() != asset['source_sha256']:
                raise ValueError('Original Unity asset checksum differs')
            objects = {o.path_id: o for o in UnityPy.load(data).objects}
            entry = {k: asset[k] for k in ('entry', 'source_sha256')}
            entry['textures'] = []
            for item in asset['textures']:
                png = (source.parent / item['png']).resolve()
                if not png.is_relative_to(source.parent.resolve()):
                    raise ValueError('PNG must be inside source directory')
                if sha256(png.read_bytes()).hexdigest() != item['png_sha256']:
                    raise ValueError('Source PNG checksum differs')
                texture = objects[item['path_id']].read()
                if texture.m_Name != item['name']:
                    raise ValueError('Texture name differs')
                with Image.open(png) as im:
                    translated = im.convert('RGBA')
                check_regions(texture.image, translated, item['edit_regions'])
                target = {k: item[k] for k in ('path_id', 'name')}
                target['rgba_sha256'] = sha256(translated.tobytes()).hexdigest()
                target['regions'] = []
                for index, box in enumerate(item['edit_regions']):
                    name = f'texture-{item["path_id"]}-{index:02}.png'
                    translated.crop(tuple(box)).save(output / name)
                    target['regions'].append({'box': box, 'png': name,
                                              'png_sha256': sha256((output / name).read_bytes()).hexdigest()})
                entry['textures'].append(target)
            result['assets'].append(entry)
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.base, args.manifest, args.output)
