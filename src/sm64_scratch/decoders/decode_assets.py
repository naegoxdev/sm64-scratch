"""Decode verified texture/costume recipes without an extracted asset directory."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_rgba16, decode_ia16, decode_ia8, source_sha


def normalize5(im):
    return im.convert('RGB').point([round((v >> 3) * 255 / 31) for v in range(256)] * 3)


def tint(im, gains):
    if len(gains) != 3:
        raise ValueError('RGB gain must have three channels')
    return Image.merge('RGB', [c.point([min(255, max(0, round(k * gain))) for k in range(256)])
                              for c, gain in zip(im.convert('RGB').split(), gains)])


def transform(im, rec):
    if rec.get('transpose') is not None:
        im = im.transpose(Image.Transpose(rec['transpose']))
    if rec['kind'] == 'v6_confirmed':
        method = rec['method']
        if method == 'quant5_round':
            return normalize5(im)
        if method == 'quant5_round_then_tint':
            return tint(normalize5(im), rec['gain_v5'])
        if method == 'quant5_floor_then_tint':
            table = [((v >> 3) << 3) | ((v >> 3) >> 2) for v in range(256)]
            return tint(im.convert('RGB').point(table * 3), rec['gain_v5'])
        if method == 'rgb_then_tint':
            return tint(im, rec['gain_v5'])
        raise ValueError('Unknown calibrated method: ' + method)
    if rec['kind'] in ('sparse_lut_verified_reference', 'confirmed_local_lut'):
        if rec['source_mode'] == 'quant5_round':
            im = normalize5(im)
        elif rec['source_mode'] != 'rgb':
            raise ValueError('Unknown lookup source mode')
        output = []
        for channel, mapping in zip(im.convert('RGB').split(), rec['lut']):
            missing = set(channel.tobytes()) - {int(k) for k in mapping}
            if missing:
                raise ValueError('Lookup table does not cover source values: ' + str(sorted(missing)))
            table = list(range(256))
            for key, value in mapping.items():
                table[int(key)] = int(value)
            output.append(channel.point(table))
        if len(output) != 3:
            raise ValueError('Lookup table must contain three channels')
        converted = Image.merge('RGB', output)
        if rec.get('alpha0_to_key'):
            threshold = rec.get('alpha_key_threshold', 0)
            converted.putdata([color if alpha > threshold else (254, 1, 254)
                               for color, alpha in zip(converted.getdata(), im.convert('RGBA').getchannel('A').getdata())])
        return converted
    raise ValueError('Unknown recipe kind: ' + rec['kind'])


def make_image(src, method, output_size=None):
    rgba = src.convert('RGBA')
    rgb = rgba.convert('RGB')
    if method == 'ia16_shadow_on_grass':
        result = Image.new('RGB', rgba.size)
        result.putdata([(254, 1, 254) if a <= 46 else
                        tuple(round(channel * (1 - a / 255)) for channel in (20, 180, 10))
                        for a in rgba.getchannel('A').getdata()])
        return result
    if method in ('ia16_alpha_threshold_46_to_FE01FE', 'ia16_tint_yellow'):
        if rgb.getchannel('R').tobytes() != rgb.getchannel('G').tobytes() or rgb.getchannel('R').tobytes() != rgb.getchannel('B').tobytes():
            raise ValueError('IA16 conversion expects grayscale intensity')
        pixels = [((254, 1, 254) if a <= 46 else (0, 0, 0))
                  if method == 'ia16_alpha_threshold_46_to_FE01FE' else (r, r, 0)
                  for r, g, b, a in rgba.getdata()]
        image = Image.new('RGB', rgba.size)
        image.putdata(pixels)
        return image
    if method.startswith('quant5_round'):
        rgb = normalize5(rgb)
    elif not method.startswith(('rgb', 'alpha0_')):
        raise ValueError('Unknown image transform: ' + method)
    if 'alpha0_to_FE01FE' in method:
        keyed = Image.new('RGB', rgba.size)
        keyed.putdata([color if a else (254, 1, 254) for color, a in zip(rgb.getdata(), rgba.getchannel('A').getdata())])
        rgb = keyed
    if 'edge_xy' in method:
        if output_size is None or output_size[0] < rgb.width or output_size[1] < rgb.height:
            raise ValueError('Edge extension requires a compatible output size')
        atlas = Image.new('RGB', output_size)
        atlas.paste(rgb, (0, 0))
        w, h = output_size
        if w > rgb.width:
            atlas.paste(rgb.crop((rgb.width - 1, 0, rgb.width, rgb.height)).resize((w - rgb.width, rgb.height), Image.Resampling.NEAREST), (rgb.width, 0))
        if h > rgb.height:
            atlas.paste(atlas.crop((0, rgb.height - 1, w, rgb.height)).resize((w, h - rgb.height), Image.Resampling.NEAREST), (0, rgb.height))
        return atlas
    return rgb


def decode_verified_assets(rom, layout, texture_manifest, costume_manifest):
    """Fail before packaging if any selected source or destination hash differs."""
    recipes = texture_manifest['recipes']
    costumes = costume_manifest['costumes']
    sources, segments = {}, {}
    paths = {row['source'] for row in recipes + costumes}
    for path in sorted(paths):
        row = layout['sources'][path]
        choices, selected = row['choices'], row.get('selected')
        if selected is None:
            if choices and all(c['validation'] == 'exact_png_rgba_sha256' for c in choices):
                selected = 0
            else:
                raise ValueError('Ambiguous ROM source requires review: ' + path)
        if not isinstance(selected, int) or not 0 <= selected < len(choices):
            raise ValueError('Invalid selected ROM source: ' + path)
        choice = choices[selected]
        codec = choice['codec']
        if codec == 'raw':
            data = rom
        elif codec == 'MIO0':
            start = choice['rom_offset']
            if start not in segments:
                segments[start] = mio0(rom, start)
            data = segments[start]
        else:
            raise ValueError('Unsupported compression: ' + codec)
        fmt = choice['pixel_format']
        offset, w, h = choice['decoded_offset'], choice['width'], choice['height']
        if w <= 0 or h <= 0 or offset < 0 or offset + w * h * (1 if fmt == 'ia8' else 2) > len(data):
            raise ValueError('Image is outside its ROM region: ' + path)
        if fmt == 'rgba16':
            image = decode_rgba16(data, offset, w, h, choice['decode_mode'])
        elif fmt == 'ia16':
            image = decode_ia16(data, offset, w, h)
        elif fmt == 'ia8':
            image = decode_ia8(data, offset, w, h)
        else:
            raise ValueError('Unsupported pixel format: ' + fmt)
        validation = choice['validation']
        if validation == 'exact_png_rgba_sha256':
            if source_sha(image) != choice['source_rgba_sha256']:
                raise ValueError('ROM source RGBA hash differs: ' + path)
        elif validation != 'opaque_pixels_only':
            raise ValueError('Unsupported source validation: ' + validation)
        sources[path] = image
    by_model, by_sprite, hashes = defaultdict(list), defaultdict(list), {}
    for row in recipes:
        image = sources[row['source']]
        if image.size != (row.get('source_width', row['width']), row.get('source_height', row['height'])):
            raise ValueError('Texture source size differs: ' + row['texture'])
        converted = (make_image(image, row['method'], (row['width'], row['height']))
                     if row['kind'] == 'exact_png_transform' else transform(image, row)).convert('RGB')
        digest = hashlib.sha256(converted.tobytes()).hexdigest()
        if converted.size != (row['width'], row['height']) or digest != row['expected_rgb_sha256']:
            raise ValueError('Texture recipe hash/size differs: ' + row['model'] + '/' + row['texture'])
        key = (row['model'], row['texture'])
        if key in hashes:
            raise ValueError('Duplicate texture recipe: ' + str(key))
        hashes[key] = digest
        by_model[row['model']].append((row['texture'], converted))
    for row in costumes:
        image = sources[row['source']].convert('RGBA')
        if image.size != (row['source_width'], row['source_height']):
            raise ValueError('Costume source size differs: ' + row['costume'])
        if row['method'] == 'quant5_round_keep_alpha':
            image = Image.merge('RGBA', (*normalize5(image).split(), image.getchannel('A')))
        elif row['method'] != 'copy_rgba':
            raise ValueError('Unknown costume transform: ' + row['method'])
        if image.size != (row['width'], row['height']) or hashlib.sha256(image.tobytes()).hexdigest() != row['expected_rgba_sha256']:
            raise ValueError('Costume recipe hash/size differs: ' + row['costume'])
        by_sprite[row['sprite']].append((row['costume'], image))
    return dict(by_model), dict(by_sprite), hashes, {'verified_textures': len(hashes), 'verified_costumes': len(costumes), 'decoded_sources': len(sources)}
