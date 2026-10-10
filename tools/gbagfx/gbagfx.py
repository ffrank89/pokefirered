#!/usr/bin/env python3
# Python port of gbagfx main.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# Converts between PNG images and GBA graphics formats, and compresses data
# with the GBA BIOS LZ77, run-length and Huffman formats.
#
# Usage: gbagfx.py INPUT_PATH OUTPUT_PATH [options...]

import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from convert_png import read_png, read_png_palette, write_png  # noqa: E402
from font import (read_full_width_latin_font, read_fullwidth_japanese_font,  # noqa: E402
                  read_half_width_latin_font, read_halfwidth_japanese_font,
                  write_full_width_latin_font, write_fullwidth_japanese_font,
                  write_half_width_latin_font, write_halfwidth_japanese_font)
from gfx import (NUM_TILES_ERROR, NUM_TILES_IGNORE, NUM_TILES_WARN, Image,  # noqa: E402
                 Palette, read_gba_palette, read_plain_image, read_tile_image,
                 write_gba_palette, write_plain_image, write_tile_image)
from huff import huff_compress, huff_decompress  # noqa: E402
from jasc_pal import read_jasc_palette, write_jasc_palette  # noqa: E402
from lz import lz_compress, lz_decompress  # noqa: E402
from rl import rl_compress, rl_decompress  # noqa: E402
from util import (fatal_error, get_file_extension, get_file_extension_after_dot,  # noqa: E402
                  parse_int_arg, read_whole_file, read_whole_file_zero_padded,
                  write_whole_file)


def _next_arg(argv, i, missing_msg):
    if i + 1 >= len(argv):
        fatal_error(missing_msg)
    return argv[i + 1]


def _parse_positive(value, parse_msg, positive_msg):
    n = parse_int_arg(value)
    if n is None:
        fatal_error(parse_msg)
    if n < 1:
        fatal_error(positive_msg)
    return n


def handle_gba_to_png(input_path, output_path, argv):
    ext = get_file_extension_after_dot(input_path)
    palette_file_path = None
    bit_depth = ord(ext[0]) - ord("0")
    has_transparency = False
    width = 1
    metatile_width = 1
    metatile_height = 1
    tilemap_file_path = None
    is_affine_map = False
    is_tiled = True
    data_width = 1

    i = 3
    while i < len(argv):
        option = argv[i]
        if option == "-palette":
            palette_file_path = _next_arg(argv, i, 'No palette file path following "-palette".\n')
            i += 1
        elif option == "-object":
            has_transparency = True
        elif option == "-width":
            value = _next_arg(argv, i, 'No width following "-width".\n')
            i += 1
            width = _parse_positive(value, "Failed to parse width.\n", "Width must be positive.\n")
        elif option == "-mwidth":
            value = _next_arg(argv, i, 'No metatile width value following "-mwidth".\n')
            i += 1
            metatile_width = _parse_positive(value, "Failed to parse metatile width.\n",
                                             "metatile width must be positive.\n")
        elif option == "-mheight":
            value = _next_arg(argv, i, 'No metatile height value following "-mheight".\n')
            i += 1
            metatile_height = _parse_positive(value, "Failed to parse metatile height.\n",
                                              "metatile height must be positive.\n")
        elif option == "-tilemap":
            tilemap_file_path = _next_arg(argv, i, 'No tilemap value following "-tilemap".\n')
            i += 1
        elif option == "-affine":
            is_affine_map = True
        elif option == "-plain":
            is_tiled = False
        elif option == "-data_width":
            value = _next_arg(argv, i, 'No data width value following "-data_width".\n')
            i += 1
            data_width = _parse_positive(value, "Failed to parse data width.\n",
                                         "Data width must be positive.\n")
        else:
            fatal_error('Unrecognized option "%s".\n' % option)
        i += 1

    if metatile_width > width:
        width = metatile_width

    image = Image()
    image.bit_depth = bit_depth

    if palette_file_path is not None:
        if get_file_extension_after_dot(palette_file_path) == "gbapal":
            read_gba_palette(palette_file_path, image.palette)
        else:
            read_jasc_palette(palette_file_path, image.palette)
        image.has_palette = True
    else:
        image.has_palette = False

    if is_tiled:
        if tilemap_file_path is not None:
            image.tilemap = read_whole_file(tilemap_file_path)
            if is_affine_map and bit_depth != 8:
                fatal_error("affine maps are necessarily 8bpp\n")
            image.is_affine = is_affine_map
        read_tile_image(input_path, width, metatile_width, metatile_height, image, not image.has_palette)
    else:
        image.width = width
        read_plain_image(input_path, data_width, image, not image.has_palette)

    image.has_transparency = has_transparency
    write_png(output_path, image)


def handle_png_to_gba(input_path, output_path, argv):
    ext = get_file_extension_after_dot(output_path)
    num_tiles_mode = NUM_TILES_IGNORE
    num_tiles = 0
    bit_depth = ord(ext[0]) - ord("0")
    metatile_width = 1
    metatile_height = 1
    is_tiled = True
    data_width = 1

    i = 3
    while i < len(argv):
        option = argv[i]
        if option == "-num_tiles":
            value = _next_arg(argv, i, 'No number of tiles following "-num_tiles".\n')
            i += 1
            num_tiles = _parse_positive(value, "Failed to parse number of tiles.\n",
                                        "Number of tiles must be positive.\n")
        elif option == "-Wnum_tiles":
            num_tiles_mode = NUM_TILES_WARN
        elif option == "-Werror=num_tiles":
            num_tiles_mode = NUM_TILES_ERROR
        elif option == "-mwidth":
            value = _next_arg(argv, i, 'No metatile width value following "-mwidth".\n')
            i += 1
            metatile_width = _parse_positive(value, "Failed to parse metatile width.\n",
                                             "metatile width must be positive.\n")
        elif option == "-mheight":
            value = _next_arg(argv, i, 'No metatile height value following "-mheight".\n')
            i += 1
            metatile_height = _parse_positive(value, "Failed to parse metatile height.\n",
                                              "metatile height must be positive.\n")
        elif option == "-plain":
            is_tiled = False
        elif option == "-data_width":
            value = _next_arg(argv, i, 'No data width value following "-data_width".\n')
            i += 1
            data_width = _parse_positive(value, "Failed to parse data width.\n",
                                         "Data width must be positive.\n")
        else:
            fatal_error('Unrecognized option "%s".\n' % option)
        i += 1

    image = Image()
    image.bit_depth = bit_depth
    read_png(input_path, image)

    if is_tiled:
        write_tile_image(output_path, num_tiles_mode, num_tiles, metatile_width, metatile_height,
                         image, not image.has_palette)
    else:
        write_plain_image(output_path, data_width, image, not image.has_palette)


def handle_png_to_jasc_palette(input_path, output_path, argv):
    palette = Palette()
    read_png_palette(input_path, palette)
    write_jasc_palette(output_path, palette)


def handle_png_to_gba_palette(input_path, output_path, argv):
    palette = Palette()
    read_png_palette(input_path, palette)
    write_gba_palette(output_path, palette)


def handle_gba_to_jasc_palette(input_path, output_path, argv):
    palette = Palette()
    read_gba_palette(input_path, palette)
    write_jasc_palette(output_path, palette)


def handle_jasc_to_gba_palette(input_path, output_path, argv):
    num_colors = 0
    i = 3
    while i < len(argv):
        option = argv[i]
        if option == "-num_colors":
            value = _next_arg(argv, i, 'No number of colors following "-num_colors".\n')
            i += 1
            num_colors = _parse_positive(value, "Failed to parse number of colors.\n",
                                         "Number of colors must be positive.\n")
        else:
            fatal_error('Unrecognized option "%s".\n' % option)
        i += 1

    palette = Palette()
    read_jasc_palette(input_path, palette)
    if num_colors != 0:
        palette.num_colors = num_colors
    write_gba_palette(output_path, palette)


def _font_to_png(reader):
    def handler(input_path, output_path, argv):
        image = Image()
        reader(input_path, image)
        write_png(output_path, image)
    return handler


def _png_to_font(writer):
    def handler(input_path, output_path, argv):
        image = Image()
        image.bit_depth = 2
        read_png(input_path, image)
        writer(output_path, image)
    return handler


def handle_lz_compress(input_path, output_path, argv):
    overflow_size = 0
    min_distance = 2  # default, for compatibility with LZ77UnCompVram()

    i = 3
    while i < len(argv):
        option = argv[i]
        if option == "-overflow":
            value = _next_arg(argv, i, 'No size following "-overflow".\n')
            i += 1
            overflow_size = _parse_positive(value, "Failed to parse overflow size.\n",
                                            "Overflow size must be positive.\n")
        elif option == "-search":
            value = _next_arg(argv, i, 'No size following "-search".\n')
            i += 1
            min_distance = _parse_positive(value, "Failed to parse LZ min search distance.\n",
                                           "LZ min search distance must be positive.\n")
        else:
            fatal_error('Unrecognized option "%s".\n' % option)
        i += 1

    # The overflow option allows a quirk in some of Ruby/Sapphire's tilesets
    # to be reproduced. It works by appending a number of zeros to the data
    # before compressing it and then amending the LZ header's size field to
    # reflect the expected size. This will cause an overflow when decompressing
    # the data.
    buffer = read_whole_file_zero_padded(input_path, overflow_size)
    file_size = len(buffer) - overflow_size
    compressed = lz_compress(buffer, min_distance)
    compressed[1] = file_size & 0xFF
    compressed[2] = (file_size >> 8) & 0xFF
    compressed[3] = (file_size >> 16) & 0xFF
    write_whole_file(output_path, bytes(compressed))


def handle_lz_decompress(input_path, output_path, argv):
    write_whole_file(output_path, lz_decompress(read_whole_file(input_path)))


def handle_rl_compress(input_path, output_path, argv):
    write_whole_file(output_path, bytes(rl_compress(read_whole_file(input_path))))


def handle_rl_decompress(input_path, output_path, argv):
    write_whole_file(output_path, rl_decompress(read_whole_file(input_path)))


def handle_huff_compress(input_path, output_path, argv):
    bit_depth = 4
    i = 3
    while i < len(argv):
        option = argv[i]
        if option == "-depth":
            value = _next_arg(argv, i, 'No size following "-depth".\n')
            i += 1
            bit_depth = parse_int_arg(value)
            if bit_depth is None:
                fatal_error("Failed to parse bit depth.\n")
            if bit_depth != 4 and bit_depth != 8:
                fatal_error("GBA only supports bit depth of 4 or 8.\n")
        else:
            fatal_error('Unrecognized option "%s".\n' % option)
        i += 1
    write_whole_file(output_path, huff_compress(read_whole_file(input_path), bit_depth))


def handle_huff_decompress(input_path, output_path, argv):
    write_whole_file(output_path, huff_decompress(read_whole_file(input_path)))


HANDLERS = (
    ("1bpp", "png", handle_gba_to_png),
    ("4bpp", "png", handle_gba_to_png),
    ("8bpp", "png", handle_gba_to_png),
    ("png", "1bpp", handle_png_to_gba),
    ("png", "4bpp", handle_png_to_gba),
    ("png", "8bpp", handle_png_to_gba),
    ("png", "gbapal", handle_png_to_gba_palette),
    ("png", "pal", handle_png_to_jasc_palette),
    ("gbapal", "pal", handle_gba_to_jasc_palette),
    ("pal", "gbapal", handle_jasc_to_gba_palette),
    ("hwlatfont", "png", _font_to_png(read_half_width_latin_font)),
    ("png", "hwlatfont", _png_to_font(write_half_width_latin_font)),
    ("fwlatfont", "png", _font_to_png(read_full_width_latin_font)),
    ("png", "fwlatfont", _png_to_font(write_full_width_latin_font)),
    ("hwjpnfont", "png", _font_to_png(read_halfwidth_japanese_font)),
    ("png", "hwjpnfont", _png_to_font(write_halfwidth_japanese_font)),
    ("fwjpnfont", "png", _font_to_png(read_fullwidth_japanese_font)),
    ("png", "fwjpnfont", _png_to_font(write_fullwidth_japanese_font)),
    (None, "huff", handle_huff_compress),
    (None, "lz", handle_lz_compress),
    ("huff", None, handle_huff_decompress),
    ("lz", None, handle_lz_decompress),
    (None, "rl", handle_rl_compress),
    ("rl", None, handle_rl_decompress),
)


def main(argv):
    if len(argv) < 3:
        fatal_error("Usage: gbagfx INPUT_PATH OUTPUT_PATH [options...]\n")

    input_path = argv[1]
    output_path = argv[2]
    input_ext = get_file_extension_after_dot(input_path)
    output_ext = get_file_extension_after_dot(output_path)

    if input_ext is None:
        fatal_error('Input file "%s" has no extension.\n' % input_path)

    if output_ext is None:
        output_ext = get_file_extension(output_path)
        if output_ext.startswith("."):
            output_ext = output_ext[1:]
        if not output_ext:
            fatal_error('Output file "%s" has no extension.\n' % output_path)
        # Replace the input file's extension with the requested one.
        dot = input_path.find(".")
        output_path = input_path[:dot + 1] + output_ext

    for in_ext, out_ext, handler in HANDLERS:
        if (in_ext is None or in_ext == input_ext) and (out_ext is None or out_ext == output_ext):
            handler(input_path, output_path, argv)
            return 0

    fatal_error('Don\'t know how to convert "%s" to "%s".\n' % (argv[1], argv[2]))


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
