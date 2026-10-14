#!/usr/bin/env python3
"""Rebuild the title head from a local compatible ROM.

Mesh coordinates, faces, animation samples and original skin weights are decoded
from the ROM. Small conversion recipes retain attachment transforms, material
calibration and the author's corrected Scratch grab mapping. See LEGAL_REVIEW.md
for their unresolved provenance; an assetless base is not legal clearance.
"""
from __future__ import annotations

import hashlib
import math
import struct
from collections import Counter

from PIL import Image

from sm64_scratch.rom_codec import ROM_SHA1
from sm64_scratch.decoders.decode_models import _fmt, _material_tokens, _texture_tokens


MODEL = "TitleIntro Mario Head"
OBJECT = "TitleHead"

# Goddard's dynlist arrays in the supported US ROM. Each vertex is s16[3]
# (6 bytes); each face is u16[4] = material ID plus three vertex indices.
COMPONENTS = (
    {"name": "Face", "vertices": 440, "faces": 877,
     "vertex_offset": 0x2745C0, "face_offset": 0x27501C,
     "palette": ((255, 255, 255), (225, 153, 104), (92, 0, 0),
                 (255, 255, 255), (255, 255, 255), (92, 0, 0),
                 (134, 0, 0), (255, 0, 0))},
    {"name": "RightEye", "vertices": 48, "faces": 82,
     "vertex_offset": 0x276FB0, "face_offset": 0x2770DC,
     "palette": ((0, 74, 255), (0, 147, 255), (0, 0, 0), (255, 255, 255))},
    {"name": "LeftEye", "vertices": 48, "faces": 82,
     "vertex_offset": 0x277618, "face_offset": 0x277744,
     "palette": ((0, 74, 255), (0, 147, 255), (0, 0, 0), (255, 255, 255))},
    {"name": "RightEyebrow", "vertices": 26, "faces": 36,
     "vertex_offset": 0x277C80, "face_offset": 0x277D28,
     "palette": ((0, 0, 0),)},
    {"name": "LeftEyebrow", "vertices": 26, "faces": 36,
     "vertex_offset": 0x277FD4, "face_offset": 0x27807C,
     "palette": ((0, 0, 0),)},
    {"name": "Mustache", "vertices": 56, "faces": 100,
     "vertex_offset": 0x278328, "face_offset": 0x278484,
     "palette": ((0, 0, 0),)},
)

class HeadDecodeError(ValueError):
    pass


def _read_component(rom: bytes, descriptor: dict):
    vertex_count = int(descriptor["vertices"])
    face_count = int(descriptor["faces"])
    vertex_offset = int(descriptor["vertex_offset"])
    face_offset = int(descriptor["face_offset"])
    vertex_end = vertex_offset + vertex_count * 6
    face_end = face_offset + face_count * 8
    if vertex_count < 1 or face_count < 1 or max(vertex_end, face_end) > len(rom):
        raise HeadDecodeError("Head geometry out of ROM bounds")

    vertices = [struct.unpack_from(">hhh", rom, vertex_offset + index * 6)
                for index in range(vertex_count)]
    faces = [struct.unpack_from(">HHHH", rom, face_offset + index * 8)
             for index in range(face_count)]
    palette = descriptor["palette"]
    if any(material >= len(palette) for material, *_indices in faces):
        raise HeadDecodeError("Head references an unknown ROM material")
    if any(index >= vertex_count for _material, *indices in faces for index in indices):
        raise HeadDecodeError("Head references a vertex outside ROM bounds")
    return vertices, faces


def _scratch_position(component_name: str, vertex):
    """Convert a Goddard component into the common title-head coordinates.

    The face, brows and moustache arrays are already in the head's display
    frame and use the same ``(x, -z, y)`` conversion as the rest of the
    Scratch renderer.  The eye shapes are different: they are attached nets
    in Goddard and their local axes are rotated before the attach offset is
    applied.  Keeping those offsets here mirrors the static zero-pose
    transforms from ``dynlist_mario_master.c``; the animation code can still
    deform the head vertices later.
    """
    x, y, z = vertex
    if component_name == "RightEye":
        # The eye is a child net of DYNOBJ_MARIO_RIGHT_EYE_JOINT_1.  Applying
        # only the attach offset (the previous implementation did that) puts
        # the whole iris behind the face.  Goddard first applies the joint
        # rotation (184.483, -178.885, 82.485) and then the mirrored eye-net
        # frame from SetRotation(90, 180, 0).  This is the exact static zero
        # pose; the runtime eye animator can subsequently adjust it.
        joint = _mat3_rotation((184.483, -178.885, 82.485))
        frame = ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                 (0.0, 0.0, -1.0))
        matrix = _mat3_mul(joint, frame)
        transformed = _row_vec_mat3((float(x), float(y), float(z)), matrix)
        return tuple(transformed[index] + (29.7, 3.0, 192.4)[index]
                     for index in range(3))
    if component_name == "LeftEye":
        # The left eye's source array already uses the left-net handedness;
        # its static joint is therefore applied without the extra mirror.
        joint = _mat3_rotation((-6.873, 0.206, -97.461))
        transformed = _row_vec_mat3((float(x), float(y), float(z)), joint)
        return tuple(transformed[index] + (-29.0, 2.0, 192.3)[index]
                     for index in range(3))
    if component_name == "RightEyebrow":
        # The eyebrow net is attached to the face at (20.8, 0, -8.5).
        return (x + 20.8, -z + 8.5, y)
    if component_name == "LeftEyebrow":
        # The left net uses the mirrored attach offset (-18.5, 0, -1).
        return (x - 18.5, -z + 1.0, y)
    if component_name == "Mustache":
        # The moustache net has the tiny static rotation from the dynlist.
        rotated = _row_vec_mat3((float(x), float(y), float(z)),
                                 _mat3_rotation((0.02, -0.002, 0.04)))
        return (rotated[0], -rotated[2], rotated[1])
    return (x, -z, y)


def _shade_bucket(points):
    """Quantise ROM-triangle lighting for the Scratch material format."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = points
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-9:
        return 2
    front = abs(nz) / length
    vertical = abs(ny) / length
    intensity = 0.42 + 0.48 * front + 0.10 * vertical
    return max(0, min(4, int(round(intensity * 4.0))))


def _shade_rgb(rgb, bucket):
    # Keep the ROM material bright while retaining five light bands.  The
    # title renderer is software based, so these bands are the visible
    # Lambert pass for the head; the narrower range avoids posterisation.
    factors = (0.82, 0.92, 1.0, 1.08, 1.16)
    factor = factors[max(0, min(len(factors) - 1, bucket))]
    return tuple(max(0, min(255, int(round(channel * factor)))) for channel in rgb)


def _light_palette(rgb, width=32, height=4096):
    """Build the vertical palette used by the title-head light pass.

    The renderer adds ``Rig.ShadeOffset`` to the texture start.  Title lighting
    combines four white-light steps with four red-light steps, so the offset
    spans 32 bands of 128 rows (0..3968).  Keeping the atlas local to each ROM
    material preserves that lookup range without embedding the private atlas.
    """
    # Fill complete scanlines in one operation.  The old per-pixel loop made
    # the intro builder spend several minutes in Pillow for the 21 material
    # atlases (21 × 32 × 4096 pixels) even though every scanline is uniform.
    raw = bytearray(width * height * 3)
    for band in range((height + 127) // 128):
        band = min(31, band)
        white_step, red_step = divmod(band, 4)
        white_factor = min(1.0, 0.50 + 0.125 * white_step)
        red_factor = min(1.35, white_factor + 0.18 * red_step)
        colour = bytes((
            max(0, min(255, int(round(rgb[0] * red_factor)))),
            max(0, min(255, int(round(rgb[1] * white_factor)))),
            max(0, min(255, int(round(rgb[2] * white_factor)))),
        ))
        row = colour * width
        first = band * 128 * width * 3
        last = min(height, (band + 1) * 128) * width * 3
        raw[first:last] = row * ((last - first) // (width * 3))
    return Image.frombytes("RGB", (width, height), bytes(raw))


# Scratch conversion mapping from the corrected v3 reference.
# This maps ROM-generated seam vertices; it contains no vertex coordinates.
# Provenance needs author confirmation before a license is applied.
_V3_AFFECTED_VERTEX = [1,
 2,
 3,
 4,
 5,
 6,
 7,
 8,
 9,
 10,
 11,
 12,
 13,
 14,
 15,
 16,
 17,
 18,
 19,
 20,
 21,
 22,
 23,
 24,
 25,
 26,
 27,
 28,
 29,
 30,
 31,
 32,
 33,
 34,
 35,
 36,
 37,
 38,
 41,
 43,
 44,
 45,
 46,
 47,
 49,
 50,
 51,
 52,
 53,
 54,
 55,
 56,
 57,
 60,
 61,
 62,
 63,
 64,
 65,
 66,
 67,
 68,
 69,
 70,
 71,
 72,
 73,
 74,
 75,
 77,
 79,
 80,
 84,
 85,
 86,
 87,
 88,
 89,
 90,
 91,
 92,
 93,
 94,
 95,
 96,
 97,
 98,
 99,
 100,
 101,
 102,
 103,
 104,
 105,
 106,
 107,
 108,
 109,
 110,
 111,
 112,
 113,
 114,
 115,
 116,
 117,
 118,
 119,
 121,
 122,
 123,
 124,
 126,
 127,
 135,
 136,
 137,
 138,
 139,
 140,
 141,
 142,
 143,
 144,
 146,
 147,
 148,
 149,
 150,
 151,
 154,
 158,
 160,
 161,
 162,
 164,
 168,
 170,
 171,
 172,
 173,
 174,
 175,
 177,
 178,
 179,
 180,
 181,
 182,
 183,
 184,
 185,
 186,
 187,
 188,
 189,
 190,
 191,
 192,
 193,
 194,
 195,
 196,
 197,
 202,
 203,
 207,
 208,
 209,
 210,
 211,
 212,
 213,
 214,
 215,
 216,
 217,
 218,
 219,
 220,
 221,
 222,
 223,
 224,
 225,
 226,
 227,
 228,
 229,
 230,
 231,
 232,
 233,
 234,
 235,
 236,
 237,
 238,
 239,
 240,
 241,
 242,
 243,
 244,
 245,
 246,
 247,
 248,
 249,
 250,
 251,
 252,
 253,
 254,
 255,
 256,
 257,
 258,
 259,
 260,
 261,
 262,
 263,
 264,
 265,
 266,
 268,
 269,
 272,
 280,
 281,
 282,
 283,
 284,
 285,
 286,
 287,
 288,
 289,
 290,
 291,
 292,
 293,
 294,
 295,
 296,
 297,
 298,
 299,
 300,
 301,
 302,
 303,
 304,
 305,
 306,
 307,
 308,
 309,
 310,
 311,
 312,
 313,
 314,
 315,
 316,
 317,
 318,
 320,
 322,
 351,
 352,
 353,
 354,
 361,
 362,
 367,
 379,
 380,
 381,
 384,
 406,
 407,
 408,
 409,
 410,
 414,
 415,
 416,
 417,
 418,
 419,
 421,
 422,
 423,
 424,
 426,
 427,
 428,
 429,
 430,
 431,
 432,
 433,
 436,
 438,
 440,
 441,
 442,
 443,
 444,
 445,
 447,
 448,
 449,
 450,
 451,
 454,
 455,
 456,
 457,
 458,
 459,
 461,
 462,
 463,
 464,
 465,
 466,
 467,
 468,
 469,
 470,
 471,
 472,
 473,
 474,
 475,
 484,
 485,
 486,
 487,
 488,
 489,
 490,
 491,
 494,
 496,
 498,
 499,
 501,
 502,
 504,
 511,
 512,
 513,
 514,
 515,
 516,
 517,
 520,
 521,
 522,
 523,
 524,
 525,
 526,
 527,
 528,
 530,
 531,
 532,
 533,
 534,
 535,
 536,
 537,
 538,
 539,
 540,
 541,
 543,
 544,
 545,
 546,
 547,
 692,
 693,
 694,
 695,
 696,
 697,
 698,
 699,
 700,
 701,
 702,
 703,
 704,
 705,
 706,
 707,
 708,
 709,
 710,
 712,
 713,
 714,
 715,
 716,
 718,
 719,
 720,
 721,
 722,
 723,
 724,
 725,
 727,
 728,
 729,
 731,
 732,
 733,
 734,
 735,
 736,
 737,
 738,
 739,
 740,
 741,
 742,
 743,
 744,
 745,
 746,
 747,
 748,
 749,
 750,
 751,
 752,
 753,
 754,
 755,
 756,
 757,
 758,
 759,
 760,
 761,
 762,
 763,
 764,
 765,
 766,
 767,
 768,
 769,
 770,
 771,
 772,
 773,
 774,
 775,
 776,
 777,
 778,
 779,
 780,
 781,
 782,
 783,
 784,
 785,
 786,
 787,
 788,
 789,
 790,
 791,
 792,
 793,
 794,
 795,
 796,
 797,
 798,
 799]




_V3_WEIGHT_ROWS = [(180, 1, 1.0),
 (173, 1, 0.967),
 (203, 1, 0.896),
 (193, 1, 0.87),
 (181, 1, 0.837),
 (171, 1, 0.831),
 (183, 1, 0.771),
 (190, 1, 0.745),
 (407, 1, 0.745),
 (178, 1, 0.736),
 (191, 1, 0.713),
 (410, 1, 0.713),
 (192, 1, 0.712),
 (172, 1, 0.69),
 (175, 1, 0.636),
 (179, 1, 0.63),
 (188, 1, 0.617),
 (408, 1, 0.617),
 (185, 1, 0.568),
 (182, 1, 0.562),
 (197, 1, 0.501),
 (187, 1, 0.49),
 (189, 1, 0.479),
 (406, 1, 0.479),
 (184, 1, 0.466),
 (280, 1, 0.43),
 (520, 1, 0.43),
 (177, 1, 0.429),
 (195, 1, 0.27),
 (174, 1, 0.264),
 (164, 1, 0.222),
 (379, 1, 0.222),
 (170, 1, 0.144),
 (168, 1, 0.083),
 (381, 1, 0.05),
 (127, 2, 1.0),
 (74, 2, 0.967),
 (70, 2, 0.896),
 (100, 2, 0.87),
 (99, 2, 0.837),
 (72, 2, 0.831),
 (71, 2, 0.771),
 (149, 2, 0.745),
 (353, 2, 0.745),
 (73, 2, 0.736),
 (148, 2, 0.713),
 (352, 2, 0.713),
 (60, 2, 0.712),
 (147, 2, 0.69),
 (151, 2, 0.636),
 (150, 2, 0.63),
 (146, 2, 0.617),
 (351, 2, 0.617),
 (96, 2, 0.568),
 (97, 2, 0.562),
 (56, 2, 0.501),
 (61, 2, 0.49),
 (57, 2, 0.479),
 (318, 2, 0.479),
 (118, 2, 0.466),
 (272, 2, 0.43),
 (440, 2, 0.43),
 (126, 2, 0.429),
 (55, 2, 0.27),
 (115, 2, 0.264),
 (119, 2, 0.222),
 (362, 2, 0.222),
 (95, 2, 0.144),
 (114, 2, 0.083),
 (361, 2, 0.05),
 (284, 3, 1.0),
 (469, 3, 1.0),
 (285, 3, 0.961),
 (295, 3, 0.961),
 (473, 3, 0.961),
 (527, 3, 0.961),
 (468, 3, 0.891),
 (528, 3, 0.891),
 (296, 3, 0.866),
 (297, 3, 0.866),
 (474, 3, 0.866),
 (526, 3, 0.866),
 (450, 3, 0.861),
 (484, 3, 0.861),
 (286, 3, 0.855),
 (544, 3, 0.855),
 (291, 3, 0.841),
 (294, 3, 0.841),
 (545, 3, 0.841),
 (547, 3, 0.841),
 (282, 3, 0.82),
 (289, 3, 0.82),
 (543, 3, 0.82),
 (546, 3, 0.82),
 (467, 3, 0.786),
 (487, 3, 0.786),
 (292, 3, 0.776),
 (540, 3, 0.776),
 (281, 3, 0.765),
 (287, 3, 0.765),
 (471, 3, 0.765),
 (524, 3, 0.765),
 (290, 3, 0.758),
 (293, 3, 0.758),
 (539, 3, 0.758),
 (541, 3, 0.758),
 (456, 3, 0.737),
 (525, 3, 0.737),
 (449, 3, 0.734),
 (530, 3, 0.734),
 (455, 3, 0.723),
 (523, 3, 0.723),
 (451, 3, 0.718),
 (532, 3, 0.718),
 (283, 3, 0.717),
 (288, 3, 0.717),
 (472, 3, 0.717),
 (513, 3, 0.717),
 (103, 3, 0.686),
 (488, 3, 0.686),
 (738, 3, 0.682),
 (712, 3, 0.682),
 (710, 3, 0.672),
 (724, 3, 0.669),
 (704, 3, 0.668),
 (466, 3, 0.667),
 (485, 3, 0.667),
 (736, 3, 0.664),
 (693, 3, 0.659),
 (742, 3, 0.659),
 (52, 3, 0.656),
 (245, 3, 0.656),
 (461, 3, 0.656),
 (517, 3, 0.656),
 (491, 3, 0.648),
 (692, 3, 0.647),
 (723, 3, 0.646),
 (703, 3, 0.646),
 (737, 3, 0.646),
 (722, 3, 0.637),
 (694, 3, 0.636),
 (732, 3, 0.635),
 (715, 3, 0.635),
 (705, 3, 0.632),
 (731, 3, 0.632),
 (716, 3, 0.632),
 (733, 3, 0.627),
 (695, 3, 0.627),
 (489, 3, 0.625),
 (515, 3, 0.625),
 (701, 3, 0.621),
 (734, 3, 0.619),
 (706, 3, 0.618),
 (728, 3, 0.614),
 (720, 3, 0.613),
 (721, 3, 0.613),
 (696, 3, 0.612),
 (702, 3, 0.61),
 (719, 3, 0.609),
 (714, 3, 0.607),
 (740, 3, 0.606),
 (490, 3, 0.593),
 (138, 3, 0.583),
 (51, 3, 0.578),
 (137, 3, 0.578),
 (475, 3, 0.569),
 (511, 3, 0.569),
 (741, 3, 0.568),
 (50, 3, 0.568),
 (240, 3, 0.568),
 (735, 3, 0.568),
 (708, 3, 0.567),
 (697, 3, 0.567),
 (739, 3, 0.566),
 (106, 3, 0.565),
 (317, 3, 0.565),
 (709, 3, 0.564),
 (713, 3, 0.563),
 (454, 3, 0.563),
 (516, 3, 0.563),
 (718, 3, 0.562),
 (457, 3, 0.562),
 (504, 3, 0.562),
 (443, 3, 0.561),
 (514, 3, 0.561),
 (743, 3, 0.56),
 (700, 3, 0.559),
 (108, 3, 0.557),
 (316, 3, 0.557),
 (470, 3, 0.555),
 (522, 3, 0.555),
 (242, 3, 0.555),
 (310, 3, 0.555),
 (241, 3, 0.548),
 (309, 3, 0.548),
 (448, 3, 0.538),
 (531, 3, 0.538),
 (14, 3, 0.534),
 (444, 3, 0.534),
 (465, 3, 0.531),
 (533, 3, 0.531),
 (246, 3, 0.531),
 (538, 3, 0.531),
 (107, 3, 0.528),
 (300, 3, 0.528),
 (727, 3, 0.519),
 (243, 3, 0.518),
 (301, 3, 0.518),
 (699, 3, 0.518),
 (725, 3, 0.514),
 (729, 3, 0.513),
 (707, 3, 0.513),
 (698, 3, 0.512),
 (458, 3, 0.511),
 (486, 3, 0.511),
 (13, 3, 0.501),
 (238, 3, 0.5),
 (109, 3, 0.499),
 (315, 3, 0.499),
 (239, 3, 0.488),
 (308, 3, 0.488),
 (442, 3, 0.486),
 (536, 3, 0.486),
 (140, 3, 0.465),
 (104, 3, 0.462),
 (139, 3, 0.462),
 (462, 3, 0.457),
 (512, 3, 0.457),
 (110, 3, 0.419),
 (314, 3, 0.419),
 (12, 3, 0.416),
 (237, 3, 0.41),
 (307, 3, 0.41),
 (222, 3, 0.409),
 (441, 3, 0.401),
 (535, 3, 0.401),
 (98, 3, 0.351),
 (161, 3, 0.351),
 (320, 3, 0.351),
 (414, 3, 0.351),
 (445, 3, 0.351),
 (502, 3, 0.351),
 (436, 3, 0.351),
 (534, 3, 0.351),
 (266, 3, 0.323),
 (313, 3, 0.323),
 (105, 3, 0.317),
 (299, 3, 0.317),
 (265, 3, 0.316),
 (306, 3, 0.316),
 (244, 3, 0.311),
 (302, 3, 0.311),
 (53, 3, 0.309),
 (62, 3, 0.307),
 (63, 3, 0.305),
 (141, 3, 0.305),
 (235, 3, 0.302),
 (64, 3, 0.3),
 (464, 3, 0.291),
 (501, 3, 0.291),
 (65, 3, 0.275),
 (260, 3, 0.275),
 (102, 3, 0.272),
 (162, 3, 0.272),
 (447, 3, 0.26),
 (537, 3, 0.26),
 (111, 3, 0.259),
 (312, 3, 0.259),
 (236, 3, 0.254),
 (305, 3, 0.254),
 (459, 3, 0.237),
 (521, 3, 0.237),
 (232, 3, 0.228),
 (303, 3, 0.228),
 (113, 3, 0.227),
 (298, 3, 0.227),
 (233, 3, 0.227),
 (101, 3, 0.227),
 (144, 3, 0.222),
 (24, 3, 0.22),
 (231, 3, 0.22),
 (234, 3, 0.214),
 (304, 3, 0.214),
 (112, 3, 0.213),
 (311, 3, 0.213),
 (79, 3, 0.211),
 (228, 3, 0.211),
 (47, 3, 0.209),
 (247, 3, 0.209),
 (463, 3, 0.205),
 (496, 3, 0.205),
 (23, 3, 0.188),
 (261, 3, 0.188),
 (31, 3, 0.185),
 (207, 3, 0.184),
 (49, 3, 0.159),
 (220, 3, 0.159),
 (154, 3, 0.151),
 (160, 3, 0.151),
 (322, 3, 0.151),
 (384, 3, 0.151),
 (26, 3, 0.05),
 (121, 4, 1.0),
 (27, 4, 0.838),
 (136, 4, 0.836),
 (28, 4, 0.818),
 (213, 4, 0.815),
 (32, 4, 0.792),
 (135, 4, 0.791),
 (124, 4, 0.671),
 (43, 4, 0.666),
 (212, 4, 0.664),
 (123, 4, 0.658),
 (269, 4, 0.656),
 (268, 4, 0.654),
 (30, 4, 0.649),
 (210, 4, 0.649),
 (122, 4, 0.637),
 (29, 4, 0.636),
 (211, 4, 0.636),
 (144, 4, 0.598),
 (26, 4, 0.587),
 (263, 4, 0.585),
 (31, 4, 0.578),
 (207, 4, 0.575),
 (25, 4, 0.53),
 (209, 4, 0.53),
 (20, 4, 0.485),
 (208, 4, 0.448),
 (69, 4, 0.426),
 (64, 4, 0.418),
 (21, 4, 0.4),
 (18, 4, 0.364),
 (214, 4, 0.364),
 (65, 4, 0.361),
 (260, 4, 0.36),
 (23, 4, 0.333),
 (261, 4, 0.333),
 (22, 4, 0.325),
 (230, 4, 0.325),
 (24, 4, 0.203),
 (231, 4, 0.203),
 (62, 4, 0.155),
 (63, 4, 0.05),
 (141, 4, 0.05),
 (88, 5, 1.0),
 (415, 5, 1.0),
 (90, 5, 0.949),
 (258, 5, 0.949),
 (264, 5, 0.913),
 (257, 5, 0.89),
 (432, 5, 0.89),
 (89, 5, 0.883),
 (417, 5, 0.883),
 (33, 5, 0.862),
 (255, 5, 0.861),
 (34, 5, 0.803),
 (259, 5, 0.803),
 (91, 5, 0.748),
 (256, 5, 0.748),
 (92, 5, 0.643),
 (80, 5, 0.586),
 (250, 5, 0.586),
 (10, 5, 0.557),
 (253, 5, 0.557),
 (16, 5, 0.511),
 (249, 5, 0.511),
 (93, 5, 0.487),
 (68, 5, 0.391),
 (254, 5, 0.391),
 (226, 5, 0.19),
 (9, 5, 0.184),
 (262, 5, 0.167),
 (431, 5, 0.167),
 (67, 5, 0.165),
 (422, 5, 0.165),
 (15, 5, 0.102),
 (224, 5, 0.102),
 (41, 5, 0.05),
 (202, 5, 0.05),
 (37, 6, 1.0),
 (11, 6, 0.967),
 (749, 6, 0.925),
 (750, 6, 0.891),
 (788, 6, 0.89),
 (759, 6, 0.861),
 (747, 6, 0.852),
 (3, 6, 0.851),
 (87, 6, 0.851),
 (423, 6, 0.851),
 (86, 6, 0.848),
 (75, 6, 0.842),
 (84, 6, 0.836),
 (789, 6, 0.824),
 (761, 6, 0.819),
 (748, 6, 0.819),
 (85, 6, 0.802),
 (783, 6, 0.779),
 (758, 6, 0.776),
 (760, 6, 0.757),
 (17, 6, 0.754),
 (781, 6, 0.73),
 (68, 6, 0.719),
 (10, 6, 0.703),
 (67, 6, 0.702),
 (422, 6, 0.702),
 (2, 6, 0.698),
 (44, 6, 0.698),
 (770, 6, 0.682),
 (768, 6, 0.671),
 (424, 6, 0.67),
 (782, 6, 0.662),
 (419, 6, 0.653),
 (36, 6, 0.632),
 (22, 6, 0.601),
 (9, 6, 0.596),
 (771, 6, 0.587),
 (780, 6, 0.578),
 (769, 6, 0.576),
 (19, 6, 0.571),
 (775, 6, 0.565),
 (21, 6, 0.557),
 (35, 6, 0.553),
 (38, 6, 0.546),
 (34, 6, 0.531),
 (773, 6, 0.526),
 (4, 6, 0.517),
 (45, 6, 0.517),
 (33, 6, 0.513),
 (798, 6, 0.509),
 (77, 6, 0.506),
 (16, 6, 0.479),
 (1, 6, 0.476),
 (418, 6, 0.476),
 (421, 6, 0.466),
 (46, 6, 0.445),
 (799, 6, 0.432),
 (778, 6, 0.426),
 (772, 6, 0.399),
 (774, 6, 0.36),
 (776, 6, 0.306),
 (794, 6, 0.233),
 (796, 6, 0.1),
 (218, 7, 1.0),
 (251, 7, 0.967),
 (763, 7, 0.925),
 (765, 7, 0.892),
 (757, 7, 0.89),
 (787, 7, 0.862),
 (762, 7, 0.852),
 (252, 7, 0.85),
 (6, 7, 0.848),
 (217, 7, 0.848),
 (427, 7, 0.848),
 (248, 7, 0.842),
 (215, 7, 0.836),
 (756, 7, 0.824),
 (786, 7, 0.82),
 (764, 7, 0.819),
 (223, 7, 0.802),
 (755, 7, 0.779),
 (767, 7, 0.777),
 (746, 7, 0.759),
 (229, 7, 0.754),
 (754, 7, 0.73),
 (254, 7, 0.72),
 (253, 7, 0.704),
 (262, 7, 0.699),
 (431, 7, 0.699),
 (7, 7, 0.698),
 (216, 7, 0.698),
 (766, 7, 0.683),
 (744, 7, 0.672),
 (428, 7, 0.672),
 (792, 7, 0.661),
 (430, 7, 0.653),
 (158, 7, 0.632),
 (230, 7, 0.602),
 (226, 7, 0.598),
 (785, 7, 0.588),
 (790, 7, 0.578),
 (784, 7, 0.577),
 (227, 7, 0.571),
 (752, 7, 0.567),
 (221, 7, 0.553),
 (219, 7, 0.547),
 (259, 7, 0.531),
 (745, 7, 0.527),
 (208, 7, 0.519),
 (8, 7, 0.517),
 (142, 7, 0.517),
 (255, 7, 0.513),
 (793, 7, 0.508),
 (225, 7, 0.506),
 (249, 7, 0.479),
 (5, 7, 0.477),
 (433, 7, 0.477),
 (429, 7, 0.466),
 (143, 7, 0.445),
 (791, 7, 0.432),
 (779, 7, 0.427),
 (751, 7, 0.401),
 (753, 7, 0.362),
 (777, 7, 0.307),
 (795, 7, 0.233),
 (797, 7, 0.1),
 (416, 5, 0.05),
 (426, 5, 0.05),
 (54, 2, 0.05),
 (194, 1, 0.05),
 (116, 2, 0.05),
 (186, 1, 0.05),
 (354, 2, 0.05),
 (409, 1, 0.05),
 (438, 2, 0.05),
 (499, 1, 0.05),
 (94, 5, 0.05),
 (117, 2, 0.05),
 (196, 1, 0.05),
 (367, 2, 0.05),
 (380, 1, 0.05),
 (494, 2, 0.05),
 (498, 1, 0.05),
 (66, 2, 0.05)]


# Legacy 32x512 head light-palette transfer, retained as calibration metadata
# for older diagnostic meshes.  The porter now emits the complete 32x4096
# atlas below, which is the range used by the corrected intro renderer.


# Primary skin nets.  The first rotation is the static net frame, the second
# is the static joint frame, and the last three values identify the ROM
# animation table for that joint.  The animation arrays contain tenths of a
# degree, just like the original GD_ANIM_ROT3S path.
# Skin command spans are address/count recipes, not embedded vertex/weight arrays.
_SKIN_COMMAND_SPANS = {'face': {'eyelid_r': {'offset': 2591912, 'count': 22}, 'eyelid_l': {'offset': 2592800, 'count': 22}, 'jaw_r': {'offset': 2593688, 'count': 40}, 'jaw_l': {'offset': 2594744, 'count': 30}, 'nose': {'offset': 2595824, 'count': 43}, 'ear_r': {'offset': 2597216, 'count': 29}, 'ear_l': {'offset': 2598272, 'count': 29}, 'lip_r': {'offset': 2599328, 'count': 29}, 'lip_l': {'offset': 2600384, 'count': 29}, 'cap': {'offset': 2601944, 'count': 90}, 'eye_l': {'offset': 2604992, 'count': 1}}, 'brows': {'right_r': {'offset': 2605712, 'count': 9}, 'right_l': {'offset': 2606288, 'count': 7}, 'right_m': {'offset': 2606816, 'count': 24}, 'left_l': {'offset': 2607920, 'count': 8}, 'left_r': {'offset': 2608472, 'count': 8}, 'left_m': {'offset': 2609024, 'count': 24}}, 'mustache': {'left': {'offset': 2610128, 'count': 28}, 'right': {'offset': 2611160, 'count': 28}}}


def _read_skin_groups(rom):
    result = {}
    for category, groups in _SKIN_COMMAND_SPANS.items():
        result[category] = {}
        for name, span in groups.items():
            weights = {}
            for index in range(span['count']):
                offset = span['offset'] + index * 24
                command, unused, vertex, percentage, y, z = struct.unpack_from('>IIIfff', rom, offset)
                if command != 32 or unused != 0 or y != 0 or z != 0 or not 0 <= percentage <= 100:
                    raise HeadDecodeError('Invalid ROM skin weight command at ' + hex(offset))
                weights[vertex] = percentage / 100.0
            result[category][name] = weights
    return result


_FACE_SKIN_JOINTS = {
    'eyelid_r': ((0.0, 90.0, 0.0), (116.3, 182.6, -70.2), (0.0, 0.0, 162.007), 0x29BE2C),
    'eyelid_l': ((0.0, 90.0, 0.0), (-116.3, 182.6, -70.2), (0.0, 0.0, 162.007), 0x29A6EC),
    'jaw_r': ((0.0, 90.0, 0.0), (0.0, -154.9, 118.5), (0.0, 0.0, -111.558), 0x298FAC),
    'jaw_l': ((0.0, 90.0, 0.0), (0.0, -154.9, 118.5), (0.0, 0.0, -31.153), 0x29786C),
    'nose': ((0.0, 90.0, 0.0), (0.0, 15.3, 295.1), (0.0, 0.0, -178.586), 0x29612C),
    'ear_r': ((-90.0, 0.0, 0.0), (294.7, 13.1, -82.1), (0.0, 0.0, 26.565), 0x294DD0),
    'ear_l': ((90.0, 180.0, 0.0), (-294.7, 13.1, -82.1), (180.0, 180.0, -153.435), 0x293A74),
    'lip_r': ((-90.0, 0.0, 0.0), (119.7, -161.4, 125.5), (-0.02, 0.0, -66.509), 0x292334),
    'lip_l': ((90.0, 180.0, 0.0), (-119.7, -161.4, 125.5), (0.02, 0.0, -66.509), 0x290BF4),
    'cap': ((0.0, 90.0, 0.0), (0.0, 233.8, -148.8), (0.0, 0.0, 153.932), 0x28F4B4),
    'eye_l': ((-90.0, 0.0, 0.0), (-29.0, 192.3, -2.0), (-6.873, 0.206, -97.461), 0x28C634),
}

# The eye, eyebrow and moustache shapes are separate runtime components.  The
# previous porter animated only the compact ``Face`` array, leaving these
# components frozen at their zero pose.  Goddard attaches each shape to a
# joint and applies the animation as a local rotation delta. The descriptors
# below retain attachment transforms and ROM offsets. Original skin weights
# and animation samples are read from the user's ROM at build time.
_COMPONENT_SKIN_GROUPS = {
    'RightEye': (
        ('eye_r', (184.483, -178.885, 82.485), 0x28DD74,
         {index: 1.0 for index in range(48)}, None, None),
    ),
    'LeftEye': (
        ('eye_l', (-6.873, 0.206, -97.461), 0x28C634,
         {index: 1.0 for index in range(48)}, None, None),
    ),
    'RightEyebrow': (
        ('right_r', (10.886, 0.0, 177.51), 0x28B2D8, 'brows.right_r', (-90.0, 90.0, -90.0), (231.3, 287.1, -51.4)),
        ('right_l', (-3.437, -0.069, -177.758), 0x289B98, 'brows.right_l', (-90.0, 90.0, -90.0), (23.0, 305.5, -44.6)),
        ('right_m', (2.865, 0.029, 182.377), 0x2870FC, 'brows.right_m', (-90.0, 90.0, -90.0), (123.1, 385.5, -46.4)),
    ),
    'LeftEyebrow': (
        ('left_l', (-6.876, 0.0, 177.51), 0x284660, 'brows.left_l', (0.0, 90.0, 0.0), (-233.6, 287.1, -51.4)),
        ('left_r', (-9.733, -0.388, -177.186), 0x282F20, 'brows.left_r', (0.0, 90.0, 0.0), (-25.3, 305.5, -44.6)),
        ('left_m', (-8.004, -0.61, 181.847), 0x2817E0, 'brows.left_m', (0.0, 90.0, 0.0), (-125.4, 385.5, -46.4)),
    ),
    'Mustache': (
        ('mustache_l', (-179.98, -164.53, -29.34), 0x2800A0, 'mustache.left', (0.0, 180.0, 0.0), (4.2, -86.5, 415.5)),
        ('mustache_r', (0.0, 15.47, 150.66), 0x27E960, 'mustache.right', (0.0, 0.0, 0.0), (-4.2, -86.5, 415.5)),
    ),
}

# ``DYNOBJ_MARIO_LEFT_JAW_JOINT`` is attached below
# ``DYNOBJ_MARIO_RIGHT_JAW_JOINT`` in dynlist_mario_master.c.  The offset is
# both the joint's attach offset and its initial position; move_animator keeps
# it when replacing the local rotation matrix.  Treating this as an
# independent net was the reason the mouth moved through the cheek.
_FACE_SKIN_ATTACH_OFFSETS = {
    'jaw_l': (141.0, 0.0, 0.0),
}
_FACE_SKIN_PARENTS = {
    'jaw_l': 'jaw_r',
}


def _mat3_mul(left, right):
    return tuple(tuple(sum(left[row][k] * right[k][col] for k in range(3))
                       for col in range(3)) for row in range(3))


def _mat3_rotation(angles):
    """Goddard's row-vector X/Y/Z rotations in its documented order."""
    result = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    for axis, angle in enumerate(angles):
        sine = math.sin(math.radians(angle))
        cosine = math.cos(math.radians(angle))
        if axis == 0:
            rotation = ((1.0, 0.0, 0.0),
                        (0.0, cosine, sine),
                        (0.0, -sine, cosine))
        elif axis == 1:
            rotation = ((cosine, 0.0, -sine),
                        (0.0, 1.0, 0.0),
                        (sine, 0.0, cosine))
        else:
            rotation = ((cosine, sine, 0.0),
                        (-sine, cosine, 0.0),
                        (0.0, 0.0, 1.0))
        result = _mat3_mul(result, rotation)
    return result


def _row_vec_mat3(vector, matrix):
    return tuple(sum(vector[k] * matrix[k][column] for k in range(3))
                 for column in range(3))


def _mat4_from_rt(rotation, translation):
    """Build Goddard's row-vector affine matrix."""
    return (tuple(rotation[0]) + (0.0,),
            tuple(rotation[1]) + (0.0,),
            tuple(rotation[2]) + (0.0,),
            (float(translation[0]), float(translation[1]),
             float(translation[2]), 1.0))


def _mat4_mul(left, right):
    return tuple(tuple(sum(left[row][k] * right[k][column]
                           for k in range(4))
                      for column in range(4))
                 for row in range(4))


def _mat4_inverse_rigid(matrix):
    """Inverse of a unit-scale Goddard affine matrix."""
    rotation = tuple(tuple(matrix[row][column] for column in range(3))
                     for row in range(3))
    # The rotation matrices are orthonormal; inverse(rotation) is transpose.
    inverse_rotation = tuple(tuple(rotation[column][row]
                                   for column in range(3))
                             for row in range(3))
    translation = tuple(matrix[3][axis] for axis in range(3))
    inverse_translation = tuple(-sum(translation[k] * inverse_rotation[k][axis]
                                     for k in range(3))
                                for axis in range(3))
    return _mat4_from_rt(inverse_rotation, inverse_translation)


def _row_vec_mat4(vector, matrix):
    return tuple(sum(vector[k] * matrix[k][column] for k in range(3))
                 + matrix[3][column] for column in range(3))


def _face_animation_tracks(rom: bytes, base_vertices, frames=820,
                           runtime_records=None, change_threshold=0.5):
    """Expand Goddard's weighted facial joints into Scratch sparse tracks.

    ``base_vertices`` stays in the Goddard coordinate system while the
    joints are evaluated.  The Scratch OBJ loader stores the face as
    ``(x, -z, y)``, so the sparse absolute samples are converted only when
    they are emitted.  Keeping the skin calculation in the source axes is
    important: the pivots and rotations in the dynlist are expressed there.
    """
    skin_groups = _read_skin_groups(rom)
    face_skin_weights = {name: tuple(weights.items())
                         for name, weights in skin_groups['face'].items()}
    animations = {}
    for name, (_net_angles, _offset, _static_angles, rom_offset) in _FACE_SKIN_JOINTS.items():
        rows = struct.unpack_from('>' + 'h' * (frames * 3), rom, rom_offset)
        animations[name] = [rows[index:index + 3]
                            for index in range(0, len(rows), 3)]

    component_vertices = {}
    component_animations = {}
    component_prepared = {}
    if runtime_records is not None:
        # Read the small non-face shape arrays once.  Their skin weights are
        # addressed in the component's own 0..N-1 index space.
        component_by_name = {item['name']: item for item in COMPONENTS}
        for component_name, groups in _COMPONENT_SKIN_GROUPS.items():
            descriptor = component_by_name[component_name]
            component_vertices[component_name], _ = _read_component(rom, descriptor)
            prepared_groups = []
            for group_name, static_angles, rom_offset, weights, net_angles, net_offset in groups:
                if isinstance(weights, str):
                    category, group = weights.split('.')
                    weights = skin_groups[category][group]
                rows = struct.unpack_from('>' + 'h' * (frames * 3), rom, rom_offset)
                component_animations[(component_name, group_name)] = [
                    rows[index:index + 3]
                    for index in range(0, len(rows), 3)
                ]
                static_local = _mat4_from_rt(_mat3_rotation(static_angles), (0.0, 0.0, 0.0))
                prepared_groups.append({
                    'name': group_name,
                    'static_local': static_local,
                    'inverse_static_local': _mat4_inverse_rigid(static_local),
                    'net_matrix': (_mat4_from_rt(_mat3_rotation(net_angles), net_offset)
                                   if net_angles is not None else None),
                    'weights': weights,
                })
            component_prepared[component_name] = prepared_groups

    prepared = []
    for name, (net_angles, offset, static_angles, _rom_offset) in _FACE_SKIN_JOINTS.items():
        net_matrix = _mat4_from_rt(_mat3_rotation(net_angles), offset)
        attach = _FACE_SKIN_ATTACH_OFFSETS.get(name, (0.0, 0.0, 0.0))
        static_local = _mat4_from_rt(_mat3_rotation(static_angles), attach)
        prepared.append({
            'name': name,
            'net_matrix': net_matrix,
            'static_local': static_local,
            'attach': attach,
            'locals_by_vertex': {
                vertex_index: weight
                for vertex_index, weight in face_skin_weights[name]
            },
        })

    # Resolve the static matrices once.  A parent is evaluated before its
    # child, matching transform_child_objects_recursive().
    prepared_by_name = {item['name']: item for item in prepared}
    static_world = {}
    for item in prepared:
        parent = _FACE_SKIN_PARENTS.get(item['name'])
        parent_world = static_world[parent] if parent else item['net_matrix']
        static_world[item['name']] = _mat4_mul(item['static_local'], parent_world)
        item['inverse_static_world'] = _mat4_inverse_rigid(static_world[item['name']])

    frame_values = []
    component_frame_values = []
    for frame_index in range(frames):
        coordinates = [list(map(float, point)) for point in base_vertices]
        current_world = {}
        for item in prepared:
            name = item['name']
            animated = animations[name][frame_index]
            local = _mat4_from_rt(
                _mat3_rotation(tuple(value / 10.0 for value in animated)),
                item['attach'],
            )
            parent = _FACE_SKIN_PARENTS.get(name)
            parent_world = current_world[parent] if parent else item['net_matrix']
            current_world[name] = _mat4_mul(local, parent_world)
            delta = _mat4_mul(item['inverse_static_world'], current_world[name])
            for vertex_index, weight in item['locals_by_vertex'].items():
                point = tuple(float(value) for value in base_vertices[vertex_index])
                transformed = _row_vec_mat4(point, delta)
                for axis in range(3):
                        coordinates[vertex_index][axis] += weight * (
                            transformed[axis] - base_vertices[vertex_index][axis])
        frame_values.append(coordinates)

        # Evaluate the separate eye/eyebrow/moustache shapes.  Goddard's
        # zero-pose mesh already includes each net's static frame in the ROM
        # coordinates used by this porter, so only the local animated delta
        # is applied here.  Converting through _scratch_position afterwards
        # keeps the output in the same (x, y, z) Scratch model axes as the
        # base runtime records.
        component_frame = {}
        for component_name, vertices in component_vertices.items():
            # Eyes are attached shape nets rather than weighted face seams.
            # Their animator replaces the joint rotation directly; preserve
            # the static eye-net frame and attach offset while using the
            # current ROM rotation for every eye vertex.
            if component_name in ('RightEye', 'LeftEye'):
                animated = component_animations[(component_name,
                                                 'eye_r' if component_name == 'RightEye' else 'eye_l')][frame_index]
                current_rotation = _mat3_rotation(tuple(value / 10.0 for value in animated))
                if component_name == 'RightEye':
                    eye_frame = ((-1.0, 0.0, 0.0),
                                 (0.0, 1.0, 0.0),
                                 (0.0, 0.0, -1.0))
                    current_matrix = _mat3_mul(current_rotation, eye_frame)
                    offset = (29.7, 3.0, 192.4)
                else:
                    current_matrix = current_rotation
                    offset = (-29.0, 2.0, 192.3)
                runtime_points = {}
                for index, source_point in enumerate(vertices):
                    transformed = _row_vec_mat3(tuple(float(value) for value in source_point),
                                                current_matrix)
                    point = tuple(transformed[axis] + offset[axis] for axis in range(3))
                    runtime_points[index] = (point[0], point[2], -point[1])
                component_frame[component_name] = runtime_points
                continue
            points = [list(map(float, point)) for point in vertices]
            for group in component_prepared[component_name]:
                animated = component_animations[(component_name, group['name'])][frame_index]
                current_local = _mat4_from_rt(
                    _mat3_rotation(tuple(value / 10.0 for value in animated)),
                    (0.0, 0.0, 0.0),
                )
                static_world = _mat4_mul(group['static_local'], group['net_matrix'])
                current_world = _mat4_mul(current_local, group['net_matrix'])
                delta = _mat4_mul(_mat4_inverse_rigid(static_world), current_world)
                for vertex_index, weight in group['weights'].items():
                    if vertex_index >= len(points):
                        continue
                    source = tuple(float(value) for value in vertices[vertex_index])
                    transformed = _row_vec_mat4(source, delta)
                    for axis in range(3):
                        points[vertex_index][axis] += weight * (
                            transformed[axis] - source[axis])
            runtime_points = {}
            for index, source_point in enumerate(points):
                point = _scratch_position(component_name, source_point)
                runtime_points[index] = (point[0], point[2], -point[1])
            component_frame[component_name] = runtime_points
        component_frame_values.append(component_frame)

    # Convert the final samples to the coordinate system used by the OBJ
    # parser.  The original head mesh is expanded by (position, material UV)
    # rather than keeping the compact ROM vertex array.  ``runtime_records``
    # carries that expansion so the sparse Scratch tracks address the same
    # vertex numbers as the model text.  Keeping this mapping here is what
    # makes a duplicated vertex receive the same facial movement as its other
    # material copy.
    if runtime_records is None:
        runtime_records = [
            ("Face", index, _scratch_position("Face", point), point)
            for index, point in enumerate(base_vertices)
        ]
    runtime_base = [record[3] for record in runtime_records]
    tracks = {}
    for axis, axis_name in enumerate('XYZ'):
        starts, counts, vertices, values = [], [], [], []
        previous_values = [runtime_base[index][axis]
                           for index in range(len(runtime_records))]
        for frame_index, coordinates in enumerate(frame_values):
            starts.append(len(values) + 1)
            count = 0
            for runtime_index, (component_name, source_index, _model_point, base_point) in enumerate(runtime_records):
                if component_name == "Face":
                    point = coordinates[source_index]
                    # ``coordinates`` is already in the runtime axes used by
                    # the Scratch controller.  The OBJ text is written as
                    # ``(x, -z, y)`` and the loader reverses that once when it
                    # fills vx/vy/vz; ``runtime_base`` therefore equals the
                    # Goddard point here.  Converting this array a second
                    # time swaps Y/Z and sends lip, nose and cap vertices
                    # hundreds of units away on the first animation frame.
                    runtime_point = point
                else:
                    runtime_point = component_frame_values[frame_index][component_name][source_index]
                value = runtime_point[axis]
                base_value = runtime_base[runtime_index][axis]
                # Goddard's exported face coordinates are integer deltas in
                # four-unit steps.  The reference Scratch lists preserve the
                # unquantised base coordinate and quantise only the animated
                # displacement (23 = 91 + round((21.437-91)/4)*4).
                if component_name == 'Face':
                    delta = value - base_value
                    value = base_value + int(round(delta / 4.0)) * 4
                # Face/FaceRev are incremental animation buffers.  A vertex
                # is emitted only when its quantised pose changes from the
                # preceding frame; frame zero establishes the initial pose.
                if frame_index > 0 and abs(value - previous_values[runtime_index]) >= change_threshold:
                    vertices.append(runtime_index + 1)
                    values.append(round(value, 3))
                    count += 1
                    # Compare against the value actually stored by Scratch.
                    # Sub-threshold motion must accumulate until emitted.
                    previous_values[runtime_index] = round(value, 3)
            counts.append(count)
        tracks[axis_name] = (starts, counts, vertices, values)
    return tracks


def _head_uv_group(component_name, material_id):
    """Return the atlas column used by the original Goddard head mesh."""
    if component_name == "Face":
        # The eight ROM face materials share five atlas columns: the white
        # entries (0, 3, 4), the dark-red entries (2, 5), and three cap/skin
        # colours.  The remaining components use the last eye/ink columns.
        return {0: 1, 1: 5, 2: 9, 3: 1, 4: 1, 5: 9, 6: 13, 7: 17}[material_id]
    if component_name in ("RightEye", "LeftEye"):
        return {1: 21, 2: 25, 3: 1}[material_id]
    return 25


# Scratch's title renderer selects eight white levels and four red levels
# in uniform 128-row bands, adding ``Rig.ShadeOffset`` to the V coordinate.
# The reference head uses a 32x4096 atlas. This small calibration recipe
# preserves its material and light bands; the local builder expands the rows.
# It includes material colour constants derived during development, so it
# must be included in the provenance review described in LEGAL_REVIEW.md.
_GODDARD_HEAD_ATLAS_RUNS = (
    (128, ('808080', '765136', '300000', '460000', '800000', '004D80', '000000', '808080')),
    (128, ('D48080', 'C55136', '510000', '760000', 'D40000', '004D80', '000000', 'D48080')),
    (256, ('FF8080', 'EC5136', '610000', '8D0000', 'FF0000', '004D80', '000000', 'FF8080')),
    (128, ('A4A4A4', '986846', '3E0000', '5B0000', 'A40000', '0063A4', '000000', 'A4A4A4')),
    (128, ('F9A4A4', 'E66846', '5F0000', '8A0000', 'F90000', '0063A4', '000000', 'F9A4A4')),
    (256, ('FFA4A4', 'EC6846', '610000', '8D0000', 'FF0000', '0063A4', '000000', 'FFA4A4')),
    (128, ('C8C8C8', 'B97F56', '4C0000', '6F0000', 'C80000', '0079C8', '000000', 'C8C8C8')),
    (384, ('FFC8C8', 'EC7F56', '610000', '8D0000', 'FF0000', '0079C8', '000000', 'FFC8C8')),
    (128, ('EDEDED', 'DB9665', '5A0000', '830000', 'ED0000', '008FED', '000000', 'EDEDED')),
    (384, ('FFEDED', 'EC9665', '610000', '8D0000', 'FF0000', '008FED', '000000', 'FFEDED')),
    (2048, ('FFFFFF', 'ECA26D', '610000', '8D0000', 'FF0000', '009AFF', '000000', 'FFFFFF')),
)


def _head_texture_atlas():
    """Build the 32x4096 Goddard light atlas used by the title head.

    Each row has eight four-pixel material bands. The renderer samples the
    centre of each 128-row shade band (64 + band * 128), avoiding precision
    errors at the boundary with the preceding light colour.
    """
    rows = []
    for height, colours in _GODDARD_HEAD_ATLAS_RUNS:
        scanline = bytearray()
        for colour in colours:
            scanline.extend(bytes.fromhex(colour) * 4)
        rows.extend(bytes(scanline) for _ in range(height))
    raw = b''.join(rows)
    if len(rows) != 4096 or len(raw) != 32 * 4096 * 3:
        raise HeadDecodeError('Goddard head atlas must be 32x4096 pixels')
    return Image.frombytes('RGB', (32, 4096), raw)

def _head_runtime_layout(rom: bytes):
    """Expand ROM head faces in the same stable order as the reference mesh.

    Goddard's OBJ exporter creates a vertex for each unique
    ``(ROM component/index, atlas column)`` pair and emits the reverse
    winding for every source triangle.  The compact ``440 + 48 + ...`` array
    therefore cannot be used directly by the Scratch animation lists:
    material seams duplicate several vertices and shift every later index.
    """
    vertex_map = {}
    records = []
    texcoords = []
    faces = []
    source_triangle_index = 0
    for component in COMPONENTS:
        name = component["name"]
        vertices, source_faces = _read_component(rom, component)
        for material_id, a, b, c in source_faces:
            column = _head_uv_group(name, material_id)
            # The Goddard exporter allocates the corners as (b, a, c), then
            # emits the first winding as (c, b, a) and its reverse.  This
            # seemingly unusual order is observable in BaseX/BaseY/BaseZ:
            # the first three entries are source vertices b, a, c.
            ordered = (b, a, c)
            refs = []
            for source_index in ordered:
                key = (name, source_index, column)
                if key not in vertex_map:
                    vertex_map[key] = len(records) + 1
                    point = _scratch_position(name, vertices[source_index])
                    # OBJ vertices are read by the Scratch runtime as
                    # (model.x, model.z, -model.y).  The controller buffers
                    # therefore use this inverse conversion, including the
                    # eyebrow and moustache attachment transforms above.
                    runtime_point = (float(point[0]), float(point[2]),
                                     float(-point[1]))
                    records.append((name, source_index, point, runtime_point))
                    texcoords.append((column / 32.0, 0.0))
                refs.append(vertex_map[key])
            material = "TitleHead_M0"
            texture = "TitleHead_T0"
            # Scratch's rasterizer keeps the reverse winding for this model.
            # Emitting the canonical winding and adding only a few reverse
            # copies made the mouth and nose depend on the culling state: some
            # triangles vanished as soon as a grabber moved them.  The clean
            # v3 project uses this reverse winding for every ROM triangle.
            reverse = (refs[2], refs[1], refs[0])
            faces.append((material, texture, reverse))
            source_triangle_index += 1
    if len(faces) != 1213:
        raise HeadDecodeError(f'Head mesh contains {len(faces)}triangles; expected 1213')
    return records, texcoords, faces


def _head_lighting_normals(runtime_records, layout_faces):
    """Rebuild unit surface normals in the actual renderer coordinate frame.

    The initial Scratch renderer effectively used face normals: its
    Object.Name lookup never selected the exported SmoothN tables.  Preserve
    that shading style while supplying explicit normals for this ROM mesh.
    The rebuilt mesh's emitted winding points outwards (the nose has +Z).
    Reversing it, as for the old export, produces inward lighting normals.
    The existing SmoothNX/Y/Z list names are retained for block compatibility.
    """
    if len(layout_faces) != 1213:
        raise HeadDecodeError('Normal table requires 1213 head triangles')
    points = [record[3] for record in runtime_records]
    faces = [tuple(index - 1 for index in face[2]) for face in layout_faces]

    def unit(vector):
        length = math.sqrt(sum(value * value for value in vector))
        return tuple(value / length for value in vector) if length else (0.0, 0.0, 0.0)

    result = []
    for indices in faces:
        a, b, c = (points[index] for index in indices)
        ab = tuple(b[axis] - a[axis] for axis in range(3))
        ac = tuple(c[axis] - a[axis] for axis in range(3))
        normal = unit((ab[1] * ac[2] - ab[2] * ac[1],
                       ab[2] * ac[0] - ab[0] * ac[2],
                       ab[0] * ac[1] - ab[1] * ac[0]))
        result.extend(round(value, 8) for value in normal)
    return result


def _grab_weight_payload(runtime_records):
    """Expand the reference's corrected Scratch grab mapping into runtime lists."""
    # The local v3 intro build contains the corrected grabber table.  It keeps
    # seam copies in the same order used by the Scratch drag solver and
    # includes the 18 vertices that were omitted by the old 506-entry table.
    # This conversion mapping differs from the ROM's original skin commands.
    # Its provenance needs author confirmation before a license is applied.
    if len(runtime_records) != 799 or len(_V3_WEIGHT_ROWS) != 524:
        raise HeadDecodeError('Corrected grab table does not match the head mesh')
    return ([row[0] for row in _V3_WEIGHT_ROWS],
            [row[1] for row in _V3_WEIGHT_ROWS],
            [row[2] for row in _V3_WEIGHT_ROWS])



def _grab_screen_tracks(rom: bytes, frames=820):
    """Project the seven grab points using the animated head root."""
    root_offset = 0x29D56C
    if root_offset + frames * 12 > len(rom):
        raise HeadDecodeError('Head root table out of ROM bounds')
    rows = struct.unpack_from('>' + 'h' * (frames * 6), rom, root_offset)
    base = [(-500.0, 0.0, -150.0), (500.0, 0.0, -150.0),
            (0.0, 700.0, 300.0), (0.0, 0.0, 600.0),
            (0.0, -300.0, 300.0), (250.0, -150.0, 300.0),
            (-250.0, -150.0, 300.0)]
    xs, ys = [], []
    for frame in range(frames):
        row = rows[frame * 6:frame * 6 + 6]
        rotation = _mat3_rotation(tuple(value / 10.0 for value in row[:3]))
        translation = tuple(float(value) for value in row[3:6])
        for point in base:
            world = _row_vec_mat3(point, rotation)
            world = tuple(world[axis] + translation[axis] for axis in range(3))
            depth = 2000.0 - world[2]
            if depth <= 1.0:
                xs.append(160.0)
                ys.append(120.0)
            else:
                xs.append(round(160.0 + world[0] * 256.0 / depth, 5))
                ys.append(round(120.0 - (world[1] - 200.0) * 256.0 / depth, 5))
    return xs, ys


def build_title_runtime_payload(rom: bytes):
    """Build the non-visual title-head tables from ROM geometry.

    The clean BASE deliberately has these lists empty.  The original Scratch
    project uses them as indexed working buffers while it drags the eyes,
    applies the face-light pass and advances the intro particles.  Supplying
    stable ROM-derived buffers here keeps those procedures total even before
    every optional visual effect has been ported.  Zero-filled effect tracks
    are intentional: they represent the effect's inactive state and do not
    import data from the private SB3.
    """
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise HeadDecodeError("Intro tables are verified only for the supported US ROM")
    runtime_records, _texcoords, _layout_faces = _head_runtime_layout(rom)
    points = [record[2] for record in runtime_records]
    runtime_base = [record[3] for record in runtime_records]

    # Keep a raw copy for Goddard's facial skin solver.  The runtime layout is
    # expanded by material seam, while the ROM animation weights address the
    # compact 440-vertex face array.
    raw_face_vertices, _ = _read_component(rom, COMPONENTS[0])
    # Scratch's OBJ loader converts model vertices back into Goddard's
    # internal axes before the controller reads these lists.  Keep the base
    # and sparse animation values in that internal (raw ROM) frame.
    xyz = [tuple(float(value) for value in point) for point in runtime_base]
    face_tracks = _face_animation_tracks(
        rom, raw_face_vertices, runtime_records=runtime_records)

    # Match the initial Scratch renderer's face shading, using outward unit
    # normals of the ROM-derived runtime mesh. No reference tables are copied.
    lighting_normals = _head_lighting_normals(runtime_records, _layout_faces)

    def channel(index):
        return [point[index] for point in xyz]

    # ``AffectedVertex`` is the controller's reset map, not a simple range.
    # It contains the skinned face copies followed by the ink meshes.  Four
    # jaw seam points and four eyebrow points are deliberately left out by
    # the original skin group; retaining that topology prevents a seam from
    # being reset from the wrong copy during a blink or mouth pose.
    skipped_face = {98, 104, 189, 358}
    skipped_brows = {
        ('RightEyebrow', 12), ('RightEyebrow', 17),
        ('LeftEyebrow', 1), ('LeftEyebrow', 9),
    }
    affected_ids = list(_V3_AFFECTED_VERTEX)
    affected_base = [tuple(round(value, 6) for value in xyz[index - 1])
                     for index in affected_ids]
    weight_ids, weight_grabs, weight_values = _grab_weight_payload(runtime_records)
    weight_base = [xyz[index - 1] for index in weight_ids]
    grab_track_x, grab_track_y = _grab_screen_tracks(rom)

    # The lengths mirror the fixed working buffers used by the Scratch
    # controller.  They are derived from the ROM head vertex count and the
    # documented frame/particle capacities rather than copied from an SB3.
    payload = {
        'TitleIntro.GrabOffsetX': [0] * 7,
        'TitleIntro.GrabOffsetY': [0] * 7,
        'TitleIntro.GrabOffsetZ': [0] * 7,
        'TitleIntro.GrabVelX': [0] * 7,
        'TitleIntro.GrabVelY': [0] * 7,
        'TitleIntro.GrabVelZ': [0] * 7,
        # Goddard's seven grabbers are fixed in the head frame.  They are
        # documented in shape_helper.c; they are not the first seven mesh
        # vertices (those belong to the cheek surface).
        'TitleIntro.GrabBaseX': [-500, 500, 0, 0, 0, 250, -250],
        'TitleIntro.GrabBaseY': [0, 0, 700, 0, -300, -150, -150],
        'TitleIntro.GrabBaseZ': [-150, -150, 300, 600, 300, 300, 300],
        'TitleIntro.GrabScreenX': [0] * 7,
        'TitleIntro.GrabScreenY': [0] * 7,
        'TitleIntro.AffectedVertex': affected_ids,
        'TitleIntro.BaseX': [point[0] for point in affected_base],
        'TitleIntro.BaseY': [point[1] for point in affected_base],
        'TitleIntro.BaseZ': [point[2] for point in affected_base],
        'TitleIntro.WeightVertex': weight_ids,
        'TitleIntro.WeightGrab': weight_grabs,
        'TitleIntro.Weight': weight_values,
        # The two eye nets are the second and third components in the ROM
        # model, immediately after the 440 face vertices.
        'TitleIntro.EyeVertex': [index + 1 for index, record in enumerate(runtime_records)
                                 if record[0] in ('RightEye', 'LeftEye')],
        'TitleIntro.EyeSide': ([1] * sum(record[0] == 'RightEye' for record in runtime_records)
                               + [2] * sum(record[0] == 'LeftEye' for record in runtime_records)),
        'TitleIntro.EyeBaseX': [point[0] for record, point in zip(runtime_records, xyz)
                                if record[0] in ('RightEye', 'LeftEye')],
        'TitleIntro.EyeBaseY': [point[1] for record, point in zip(runtime_records, xyz)
                                if record[0] in ('RightEye', 'LeftEye')],
        'TitleIntro.EyeBaseZ': [point[2] for record, point in zip(runtime_records, xyz)
                                if record[0] in ('RightEye', 'LeftEye')],
        'TitleIntro.RootRX': [0] * 820,
        'TitleIntro.RootRY': [0] * 820,
        'TitleIntro.RootRZ': [0] * 820,
        'TitleIntro.RootX': [0] * 820,
        'TitleIntro.RootY': [0] * 820,
        'TitleIntro.RootZ': [0] * 820,
        'TitleIntro.Background240': [0] * (240 * 180),
        'TitleIntro.GrabTrackScreenX': grab_track_x,
        'TitleIntro.GrabTrackScreenY': grab_track_y,
        'TitleIntro.SmoothNX': lighting_normals[0::3],
        'TitleIntro.SmoothNY': lighting_normals[1::3],
        'TitleIntro.SmoothNZ': lighting_normals[2::3],
        'TitleIntro.SilverStarX': [0] * 660,
        'TitleIntro.SilverStarY': [0] * 660,
        'TitleIntro.SilverStarSize': [0] * 660,
        'TitleIntro.SilverStarVisible': [0] * 660,
        'TitleIntro.RedStarX': [0] * 660,
        'TitleIntro.RedStarY': [0] * 660,
        'TitleIntro.RedStarSize': [0] * 660,
        'TitleIntro.RedStarVisible': [0] * 660,
    }
    # The face tracks are sparse absolute replacements, which is the format
    # consumed by the existing Scratch controller.  Reverse animation is
    # applied in place while moving from N+1 to N.  Its entry for N therefore
    # has the vertex set of forward frame N+1, but the coordinates of frame N.
    for axis in 'XYZ':
        starts, counts, vertices, values = face_tracks[axis]
        frame_maps = []
        cursor = 0
        for count in counts:
            frame_maps.append({
                int(vertices[cursor + index]): values[cursor + index]
                for index in range(count)
            })
            cursor += count
        # Reconstruct the complete forward pose, including unchanged values.
        # A sparse frame is a set of replacements, not a complete pose.
        # Falling back to the base when a vertex was absent in frame N
        # corrupted reverse playback (notably the yawn -> grabbed rest).
        current = [point['XYZ'.index(axis)] for point in xyz]
        poses = []
        for replacements in frame_maps:
            for vertex, value in replacements.items():
                current[vertex - 1] = value
            poses.append(current.copy())
        rev_starts, rev_counts, rev_vertices, rev_values = [], [], [], []
        for frame_index in range(len(frame_maps)):
            rev_starts.append(len(rev_values) + 1)
            if frame_index + 1 >= len(frame_maps):
                rev_counts.append(0)
                continue
            count = 0
            for runtime_vertex in frame_maps[frame_index + 1]:
                rev_vertices.append(runtime_vertex)
                rev_values.append(poses[frame_index][runtime_vertex - 1])
                count += 1
            rev_counts.append(count)
        payload[f'TitleIntro.Face{axis}Start'] = list(starts)
        payload[f'TitleIntro.Face{axis}Count'] = list(counts)
        payload[f'TitleIntro.Face{axis}Vertex'] = list(vertices)
        payload[f'TitleIntro.Face{axis}Value'] = list(values)
        payload[f'TitleIntro.FaceRev{axis}Start'] = rev_starts
        payload[f'TitleIntro.FaceRev{axis}Count'] = rev_counts
        payload[f'TitleIntro.FaceRev{axis}Vertex'] = rev_vertices
        payload[f'TitleIntro.FaceRev{axis}Value'] = rev_values
        for before, after in ((809, 750), (819, 69), (659, 661)):
            jump = f'Jump{before}to{after}'
            changed = [index + 1 for index, (a, b) in enumerate(
                zip(poses[before - 1], poses[after - 1])) if a != b]
            payload[f'TitleIntro.{jump}{axis}Vertex'] = changed
            payload[f'TitleIntro.{jump}{axis}Value'] = [
                poses[after - 1][index - 1] for index in changed]
    report = {
        'title_intro_head_rom_vertices': len(points),
        'title_intro_runtime_lists_rebuilt': len(payload),
        'title_intro_effect_tracks_initialized': True,
        'title_intro_face_animation_frames_from_rom': len(face_tracks['X'][0]),
        'title_intro_face_animation_sparse_values': {
            axis: len(face_tracks[axis][3]) for axis in 'XYZ'
        },
        'title_intro_face_reverse_source': 'complete_replayed_forward_pose',
        'title_intro_face_jumps_rebuilt_from_rom': True,
    }
    return payload, report


def build_head_model(rom: bytes):
    """Return model name, Scratch OBJ record, verification report, textures."""
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise HeadDecodeError("Head is verified only for the supported US ROM")

    runtime_records, texcoords, layout_faces = _head_runtime_layout(rom)
    all_vertices = [record[2] for record in runtime_records]
    all_faces = layout_faces
    texture_images_by_name = {"TitleHead_T0": _head_texture_atlas()}
    material_count = Counter()
    source_triangle_count = 0
    for component in COMPONENTS:
        vertices, faces = _read_component(rom, component)
        material_count["TitleHead_M0"] += len(faces)
        source_triangle_count += len(faces)

    tokens = ["o", OBJECT]
    # The source arrays use Goddard axes; the Scratch OBJ renderer uses the
    # same (x, -z, y) conversion as the reconstructed Mario body and title logo.
    tokens.extend("v " + " ".join(_fmt(value) for value in vertex)
                  for vertex in all_vertices)
    tokens.extend("vt " + " ".join(_fmt(value) for value in uv)
                  for uv in texcoords)
    tokens.append("vn 0.00000000 1.00000000 0.00000000")

    for texture, image in texture_images_by_name.items():
        tokens.extend(_texture_tokens(texture, image).split())
    seen_materials = set()
    for material, texture, _indices in all_faces:
        if material not in seen_materials:
            tokens.extend(_material_tokens(material, texture).split())
            seen_materials.add(material)

    current_material = None
    # The Goddard title head intentionally renders both sides of its
    # triangles.  The Scratch OBJ parser treats ``nocull`` as a model-level
    # directive; omitting it leaves the cap, eyes and mouth intermittently
    # invisible because their source winding is mixed.  The reference head
    # contains this directive immediately before the material stream.
    tokens.append("nocull")
    for material, _texture, indices in all_faces:
        refs = [f"{index}/{index}/1" for index in indices]
        if material != current_material:
            tokens.extend(("usemtl", material))
            current_material = material
        tokens.append("f " + " ".join(refs))

    report = {
        "model": MODEL,
        "rom_sha1": ROM_SHA1,
        "component_counts": {
            item["name"]: {"vertices": item["vertices"], "triangles": item["faces"]}
            for item in COMPONENTS
        },
        "vertex_count": len(all_vertices),
        "uv_count": len(all_vertices),
        "rom_triangles": source_triangle_count,
        "model_faces": len(all_faces),
        "material_triangles": dict(material_count),
        "texture_images": {
            name: {"size": list(image.size),
                   "rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest()}
            for name, image in texture_images_by_name.items()
        },
    }
    return MODEL, " ".join(tokens), report, list(texture_images_by_name.items())
