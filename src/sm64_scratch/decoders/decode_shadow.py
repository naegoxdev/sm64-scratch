"""Reproduce the reference project's procedural player shadow material."""
import hashlib
import math
from PIL import Image
from sm64_scratch.decoders.decode_models import _texture_tokens


def build_shadow_model():
    # This is the project's authored smooth radial mask, not a substituted
    # native shadow image. Its RGB blue channel stores coverage for the
    # existing Scratch shadow shader; zero coverage uses the engine alpha key.
    size, radius, feather = 32, .5, .15
    pixels = []
    for y in range(size):
        for x in range(size):
            distance = math.hypot((x + .5) / size - .5, (y + .5) / size - .5)
            value = max(0, min(1, (radius - distance) / feather))
            coverage = round(255 * value * value * (3 - 2 * value))
            pixels.append((0, 0, coverage) if coverage else (254, 1, 254))
    image = Image.new('RGB', (size, size))
    image.putdata(pixels)
    expected = '1e3256ed44e7254070037f00f5ebe1ea029b26173d13ecbac1bbad37c4b6107a'
    if hashlib.sha256(image.tobytes()).hexdigest() != expected:
        raise ValueError('Procedural shadow differs from its reference hash')
    text = ' '.join((_texture_tokens('MarioShadowTex', image),
                     'newmtl MarioShadowMat nocull map_Kd MarioShadowTex',
                     'v -.5 .5 0 v .5 .5 0 v .5 -.5 0 v -.5 -.5 0',
                     'vt 0 0 vt 1 0 vt 1 1 vt 0 1',
                     'usemtl MarioShadowMat f 1/1/1 2/2/1 3/3/1 f 1/1/1 3/3/1 4/4/1'))
    return 'Mario Shadow', text, {
        'vertex_count': 4, 'model_faces': 2, 'texture_rgb_sha256': expected,
        'geometry_source': 'authored procedural shadow quad and smooth radial coverage',
        'provenance': 'project-authored material; no native ROM image is substituted'}
