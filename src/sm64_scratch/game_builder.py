"""Packaging helpers for the full game; media is generated locally."""
from __future__ import annotations
from pathlib import Path
from io import BytesIO
import hashlib,json,zipfile
from PIL import Image
from sm64_scratch.sb3_builder import _write_project
from sm64_scratch.decoders.decode_models import _texture_tokens

def compact_archive(sb3_path: Path):
    """Prune unused media and compress the finished SB3 once after all patches."""
    temporary = sb3_path.with_name(sb3_path.name + '.compact.tmp')
    try:
        with zipfile.ZipFile(sb3_path) as source:
            project = json.loads(source.read('project.json'))
            referenced = {'project.json'} | {
                asset['md5ext'] for target in project['targets']
                for kind in ('costumes', 'sounds') for asset in target.get(kind, [])}
            with zipfile.ZipFile(temporary, 'w') as target:
                for item in source.infolist():
                    if item.filename in referenced:
                        target.writestr(item, source.read(item.filename),
                                        compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        temporary.replace(sb3_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def replace_costume_assets(sb3_path: Path, assets_by_sprite: dict[str, list[tuple[str, Image.Image | bytes]]]):
    """Replace markers with locally decoded images or generated SVG primitives."""
    encoded = {}
    for sprite, costumes in assets_by_sprite.items():
        for costume_name, image in costumes:
            if isinstance(image, bytes):
                from xml.etree import ElementTree
                if ElementTree.fromstring(image).tag != '{http://www.w3.org/2000/svg}svg':
                    raise ValueError('Generated costume bytes must be SVG')
                image_bytes, data_format = image, 'svg'
            else:
                buffer = BytesIO()
                image.save(buffer, format='PNG', optimize=False)
                image_bytes, data_format = buffer.getvalue(), 'png'
            asset_id = hashlib.md5(image_bytes).hexdigest()
            encoded[(sprite, costume_name)] = (asset_id + '.' + data_format, image_bytes)

    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        targets = {target.get('name'): target for target in project.get('targets', [])}
        for sprite, costumes in assets_by_sprite.items():
            target = targets.get(sprite)
            if target is None:
                raise ValueError('Missing costume sprite in BASE: ' + sprite)
            by_name = {costume.get('name'): costume for costume in target.get('costumes', [])}
            if len(by_name) != len(target.get('costumes', [])):
                raise ValueError('Duplicate costumes in BASE: ' + sprite)
            for costume_name, _image in costumes:
                costume = by_name.get(costume_name)
                if costume is None:
                    raise ValueError(f'Missing costume: {sprite}/{costume_name}')
                md5ext, _image_bytes = encoded[(sprite, costume_name)]
                costume['assetId'] = md5ext.rsplit('.',1)[0]
                costume['md5ext'] = md5ext
                costume['dataFormat'] = md5ext.rsplit('.',1)[1]
                if costume['dataFormat'] == 'svg':
                    costume['bitmapResolution'] = 1

        payload = json.dumps(project, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        referenced = {'project.json'}
        for target in project.get('targets', []):
            for asset in target.get('costumes', []) + target.get('sounds', []):
                if asset.get('md5ext'):
                    referenced.add(asset['md5ext'])
        bytes_by_name = {}
        for _key, (md5ext, image_bytes) in encoded.items():
            bytes_by_name[md5ext] = image_bytes
        temporary = sb3_path.with_name(sb3_path.name + '.hud.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                seen = set()
                for item in source.infolist():
                    if item.filename not in referenced:
                        continue
                    seen.add(item.filename)
                    if item.filename == 'project.json':
                        _write_project(target, item, payload)
                    elif item.filename in bytes_by_name:
                        target.writestr(item, bytes_by_name[item.filename])
                    else:
                        target.writestr(item, source.read(item.filename))
                for filename, image_bytes in bytes_by_name.items():
                    if filename not in seen:
                        target.writestr(filename, image_bytes)
            temporary.replace(sb3_path)
        finally:
            if temporary.exists():
                temporary.unlink()


def add_game_models(sb3_path, by_model, restored_models):
    """Keep texture-only records explicit; do not count them as restored meshes."""
    models = {name: text for name, text, _report in restored_models}
    if len(models) != len(restored_models):
        raise ValueError('Duplicate restored geometry model')
    import re
    for name, textures in sorted(by_model.items()):
        if name not in models:
            models[name] = 'o Model'
        existing = set(re.findall(r'\bnewtex\s+(\S+)', models[name]))
        for texture_name, image in textures:
            if texture_name not in existing:
                models[name] += ' ' + _texture_tokens(texture_name, image)
                existing.add(texture_name)
    with zipfile.ZipFile(sb3_path) as source:
        project = json.loads(source.read('project.json'))
        stage = next(t for t in project['targets'] if t.get('isStage'))
        preloaded = next(v for v in stage['lists'].values() if v[0] == 'Pre-loaded')
        if preloaded[1]:
            raise ValueError('Input BASE must have an empty Pre-loaded list')
        for name, text in sorted(models.items()):
            preloaded[1].extend(('+' + name, text))
        payload = json.dumps(project, separators=(',', ':')).encode()
        temporary = sb3_path.with_name(sb3_path.name + '.models.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as out:
                for item in source.infolist():
                    _write_project(out, item, payload if item.filename == 'project.json' else source.read(item.filename))
            temporary.replace(sb3_path)
        finally:
            temporary.unlink(missing_ok=True)
