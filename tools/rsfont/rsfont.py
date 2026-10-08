#!/usr/bin/env python3
# Python port of rsfont (main.c, font.c, convert_png.c, util.c)
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# Converts between PNG images and Ruby/Sapphire-style 1bpp/4bpp font data.
#
# Usage: rsfont.py INPUT_FILE OUTPUT_FILE NUM_GLYPHS LAYOUT_TYPE

import os
import signal
import sys

# The PNG reader/writer and helpers are shared with gbagfx.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gbagfx"))

from convert_png import (PNG_COLOR_TYPE_GRAY, PNG_COLOR_TYPE_PALETTE,  # noqa: E402
                         read_png_info, write_png)
from gfx import Image  # noqa: E402
from util import (fatal_error, get_file_extension_after_dot, parse_int_arg,  # noqa: E402
                  read_whole_file, write_whole_file)

FONT_PALETTE = (
    (0xFF, 0xFF, 0xFF),  # bg (white)
    (0x38, 0x38, 0x38),  # fg (dark grey)
    (0xD8, 0xD8, 0xD8),  # shadow (light grey)
)

FROM_4BPP_TABLE = (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 1)
TO_4BPP_TABLE = (0, 15, 14)


def _get(buf, i):
    return buf[i] if 0 <= i < len(buf) else 0


def _glyph_tiles(glyph, bpp, layout):
    """Returns a list of (fontOffset, yStart) for each 8x8 tile of a glyph."""
    if layout == 0:
        return [(glyph * 8 * bpp, (glyph // 16) * 8)]
    if layout == 1:
        tile1 = glyph * 16 * bpp
        tile2 = tile1 + 8 * bpp
    else:
        tile1 = (glyph // 16) * 1024 + (glyph % 16) * 32
        tile2 = tile1 + 512
    y = (glyph // 16) * 16
    return [(tile1, y), (tile2, y + 8)]


def convert_from_tiles(src, dest, num_glyphs, bpp, layout):
    for glyph in range(num_glyphs):
        x0 = (glyph % 16) * 8
        for offset, y0 in _glyph_tiles(glyph, bpp, layout):
            for i in range(8):
                row_off = (y0 + i) * 128 + x0
                if bpp == 1:
                    src_row = _get(src, offset + i)
                    for j in range(8):
                        dest[row_off + j] = (src_row >> (7 - j)) & 1
                else:
                    o = offset + i * 4
                    src_row = _get(src, o) | (_get(src, o + 1) << 8) | (_get(src, o + 2) << 16) | (_get(src, o + 3) << 24)
                    for j in range(8):
                        dest[row_off + j] = FROM_4BPP_TABLE[src_row & 0xF]
                        src_row >>= 4


def convert_to_tiles(src, dest, num_glyphs, bpp, layout):
    for glyph in range(num_glyphs):
        x0 = (glyph % 16) * 8
        for offset, y0 in _glyph_tiles(glyph, bpp, layout):
            for i in range(8):
                row_off = (y0 + i) * 128 + x0
                if bpp == 1:
                    dest_row = 0
                    for j in range(8):
                        color = _get(src, row_off + j)
                        if color > 1:
                            fatal_error("More than 2 colors in 1 BPP font.\n")
                        dest_row = ((dest_row << 1) | color) & 0xFF
                    dest[offset + i] = dest_row
                else:
                    dest_row = 0
                    for j in range(8):
                        color = _get(src, row_off + j)
                        if color > 2:
                            fatal_error("More than 3 colors in 4 BPP font.\n")
                        dest_row = (dest_row >> 4) | (TO_4BPP_TABLE[color] << 28)
                    o = offset + i * 4
                    dest[o:o + 4] = (dest_row & 0xFFFFFFFF).to_bytes(4, "little")


def calc_file_size(num_glyphs, bpp, layout):
    if layout == 2:
        # assume 4 BPP
        num_full_rows = num_glyphs // 16
        remainder = num_glyphs % 16
        remainder_size = 0
        if remainder != 0:
            remainder_size = 1024 - (16 - remainder) * 32
        return num_full_rows * 1024 + remainder_size
    tiles_per_glyph = 2 if layout > 0 else 1
    return num_glyphs * tiles_per_glyph * 8 * bpp


def read_font(path, image, num_glyphs, bpp, layout):
    buffer = read_whole_file(path)
    expected = calc_file_size(num_glyphs, bpp, layout)
    if len(buffer) != expected:
        fatal_error("The file size is %d but should be %d.\n" % (len(buffer), expected))

    num_rows = (num_glyphs + 15) // 16
    row_height = 16 if layout > 0 else 8
    image.width = 128
    image.height = num_rows * row_height
    image.bit_depth = 8
    pixels = bytearray(image.width * image.height)
    convert_from_tiles(buffer, pixels, num_glyphs, bpp, layout)
    image.pixels = bytes(pixels)

    image.has_palette = True
    image.palette.num_colors = 3
    colors = list(image.palette.colors)
    colors[:3] = FONT_PALETTE
    image.palette.colors = colors
    image.has_transparency = False


def write_font(path, image, num_glyphs, bpp, layout):
    if image.width != 128:
        fatal_error("The width of the font image (%d) is not 128.\n" % image.width)

    num_rows = (num_glyphs + 15) // 16
    row_height = 16 if layout > 0 else 8
    expected_height = num_rows * row_height
    if image.height < expected_height:
        fatal_error("The height of the font image (%d) is less than %d.\n" % (image.height, expected_height))

    file_size = calc_file_size(num_glyphs, bpp, layout)
    buffer = bytearray(file_size)
    convert_to_tiles(image.pixels, buffer, num_glyphs, bpp, layout)
    write_whole_file(path, bytes(buffer[:file_size]))


def read_png(path, image):
    info = read_png_info(path)
    if info.bit_depth != image.bit_depth:
        fatal_error('"%s" has a bit depth of %d, but the expected bit depth is %d.\n'
                    % (path, info.bit_depth, image.bit_depth))
    if info.color_type != PNG_COLOR_TYPE_GRAY and info.color_type != PNG_COLOR_TYPE_PALETTE:
        fatal_error('"%s" has an unsupported color type.\n' % path)
    image.has_palette = info.color_type == PNG_COLOR_TYPE_PALETTE
    image.width = info.width
    image.height = info.height
    image.pixels = b"".join(info.rows)


def extension_to_bpp(ext):
    if ext == "1bpp":
        return 1
    if ext == "4bpp":
        return 4
    return 0


def main(argv):
    if len(argv) < 5:
        fatal_error("Usage: rsfont INPUT_FILE OUTPUT_FILE NUM_GLYPHS LAYOUT_TYPE\n")

    input_path = argv[1]
    output_path = argv[2]
    input_ext = get_file_extension_after_dot(input_path)
    output_ext = get_file_extension_after_dot(output_path)

    if input_ext is None:
        fatal_error('Input file "%s" has no extension.\n' % input_path)
    if output_ext is None:
        fatal_error('Output file "%s" has no extension.\n' % output_path)

    num_glyphs = parse_int_arg(argv[3])
    if num_glyphs is None:
        fatal_error("Failed to parse number of glyphs.\n")

    layout = parse_int_arg(argv[4])
    if layout is None:
        fatal_error("Failed to parse layout type.\n")

    if layout < 0 or layout > 2:
        fatal_error("Layout type %d is invalid. Layout type must be 0, 1, or 2.\n" % layout)

    if input_ext == "png" and extension_to_bpp(output_ext) != 0:
        bpp = extension_to_bpp(output_ext)
        to_png = False
    elif extension_to_bpp(input_ext) != 0 and output_ext == "png":
        bpp = extension_to_bpp(input_ext)
        to_png = True
    else:
        fatal_error('Don\'t know how to convert "%s" to "%s".\n' % (input_path, output_path))

    if bpp == 1 and layout == 2:
        fatal_error("Layout type 2 is not supported with 1 BPP fonts.\n")

    image = Image()
    if to_png:
        read_font(input_path, image, num_glyphs, bpp, layout)
        write_png(output_path, image)
    else:
        image.bit_depth = 8
        read_png(input_path, image)
        write_font(output_path, image, num_glyphs, bpp, layout)
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
