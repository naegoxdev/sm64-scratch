"""Rasterize native ROM IA8 transition masks into bounded Scratch costumes."""
import hashlib
from PIL import Image
from sm64_scratch.rom_codec import mio0, ROM_SHA1
from sm64_scratch.decoders.decode_intro import _render_transition_star

SIZE = (480, 360)
SEGMENT_ROM = 0x108A40
# US segment 2 offsets; 32x64 half tiles, mirrored exactly as screen_transition.c.
OFFSETS = {'star': 0x122B8, 'circle': 0x12AB8, 'bowser': 0x142B8}
MARIO_OFFSET = 0x132B8  # full 64x64 IA8 clamp texture


def build_game_transition_assets(rom):
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError('Transition textures require the supported US ROM')
    segment = mio0(rom, SEGMENT_ROM)
    tiles = {name: segment[offset:offset+2048] for name, offset in OFFSETS.items()}
    if any(len(tile) != 2048 for tile in tiles.values()):
        raise ValueError('Transition tile outside segment 2')
    alpha = {name: bytes((value & 15) * 17 for value in tile) for name, tile in tiles.items()}
    mario_tile = segment[MARIO_OFFSET:MARIO_OFFSET+4096]
    if len(mario_tile) != 4096:
        raise ValueError('Mario transition tile outside segment 2')
    mario_alpha = bytes((value & 15) * 17 for value in mario_tile)

    def clamp_texel(alpha_bytes, x, y):
        x = max(0, min(63, x)); y = max(0, min(63, y))
        return alpha_bytes[y * 64 + x]

    def clamp_sample(alpha_bytes, s_fixed, t_fixed):
        import math
        s, fs = divmod(s_fixed, 32); t, ft = divmod(t_fixed, 32)
        a = clamp_texel(alpha_bytes, s, t); b = clamp_texel(alpha_bytes, s + 1, t)
        c = clamp_texel(alpha_bytes, s, t + 1); d = clamp_texel(alpha_bytes, s + 1, t + 1)
        if fs == ft == 16:
            return (a + b + c + d + 2) // 4
        if fs + ft <= 32:
            return (a * 32 + (b-a) * fs + (c-a) * ft + 16) // 32
        return (d * 32 + (c-d) * (32-fs) + (b-d) * (32-ft) + 16) // 32

    def mario_mask(radius):
        import math
        width, height = SIZE
        pixels = bytearray([255]) * (width * height)
        if radius:
            s_coords=[]
            for x in range(width):
                dx=(x+.5)/1.5-160
                s_coords.append(math.floor((dx/radius+1)*31.5*32) if -radius <= dx < radius else None)
            for y in range(height):
                dy=(y+.5)/1.5-120
                if not -radius <= dy < radius:
                    continue
                t_fixed=math.floor((dy/radius+1)*31.5*32)
                for x,s_fixed in enumerate(s_coords):
                    if s_fixed is not None:
                        pixels[y*width+x]=clamp_sample(mario_alpha,s_fixed,t_fixed)
        out=Image.new('RGBA',SIZE,(0,0,0,0))
        out.putalpha(Image.frombytes('L',SIZE,bytes(pixels)))
        return out

    def mask(kind, radius, color=(0, 0, 0)):
        out = _render_transition_star(alpha[kind], radius)
        if color != (0, 0, 0):
            colored = Image.new('RGBA', SIZE, (*color, 0));colored.putalpha(out.getchannel('A'));out = colored
        return out
    circles = [('Circle %02d' % frame, mask('circle', round(320*(20-frame)/20))) for frame in range(21)]
    circles[0] = (circles[0][0], Image.new('RGBA', SIZE))
    # Painting entry fades white in C; the level opens from a white ROM star.
    stars = [('White fade', Image.new('RGBA', SIZE, (255, 255, 255, 255)))]
    stars += [('Star reveal %d' % frame, mask('star', round(320*min(frame,15)/15), (255, 255, 255))) for frame in range(17)]
    stars[-1] = (stars[-1][0], Image.new('RGBA', SIZE))
    death = [('Bowser %02d' % frame, mask('bowser', round(320*(47-frame)/47))) for frame in range(48)]
    death[0] = (death[0][0], Image.new('RGBA', SIZE))
    death[-1] = (death[-1][0], Image.new('RGBA', SIZE, (0, 0, 0, 255)))
    death += [('Death reveal %02d' % frame, mask('star', round(320*min(frame,15)/15))) for frame in range(17)]
    death[-1] = (death[-1][0], Image.new('RGBA', SIZE))
    # Star-exit course warp: native 32-frame Mario-head clamp wipe into black.
    mario_exit = [('Mario exit %02d' % frame, mario_mask(round(320 + (16-320) * frame / 31)))
                  for frame in range(32)]
    # Returning through a painting uses the normal black star reveal (16 ticks).
    star_return = [('Star return %02d' % frame, mask('star', round(320*min(frame,15)/15)))
                   for frame in range(17)]
    star_return[-1] = (star_return[-1][0], Image.new('RGBA', SIZE))
    assets = {'Door Transition': circles, 'Transitions': stars + mario_exit + star_return, 'Death Transition': death}
    hashes={name: hashlib.sha256(tile).hexdigest() for name,tile in tiles.items()}
    hashes['mario']=hashlib.sha256(mario_tile).hexdigest()
    return assets, dict(source='US ROM segment 2 IA8 masks; native mirror/clamp UV and three-point filtering',
                        tile_sha256=hashes,
                        screen_size=list(SIZE), door_close_seconds=20/30, door_open_seconds=16/30,
                        painting_white_fade=True, painting_open_seconds=16/30,
                        mario_exit_close_seconds=32/30, star_return_open_seconds=16/30,
                        death_close_seconds=48/30, death_open_seconds=16/30,
                        costume_count=sum(map(len, assets.values())), title_star_unchanged=True)
