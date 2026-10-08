# Python port of gbagfx font.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# Conversion between PNG images and the game's 2bpp font formats.

from util import fatal_error, read_whole_file, write_whole_file

FONT_PALETTE = (
    (0x90, 0xC8, 0xFF),  # bg (saturated blue that contrasts well with the shadow color)
    (0x38, 0x38, 0x38),  # fg (dark grey)
    (0xD8, 0xD8, 0xD8),  # shadow (light grey)
    (0xFF, 0xFF, 0xFF),  # box (white)
)


def _get(buf, i):
    return buf[i] if i < len(buf) else 0


# Each layout function yields (fontOffset, pixelsOffset) pairs: the font data
# byte pair at fontOffset maps to the image byte pair at pixelsOffset (swapped).

def _half_width_latin_layout(num_rows):
    font_off = 0
    for row in range(num_rows):
        for column in range(32):
            for glyph_tile in range(2):
                pixels_x = column * 8
                for i in range(8):
                    pixels_y = row * 16 + glyph_tile * 8 + i
                    yield font_off, pixels_y * 64 + pixels_x // 4
                    font_off += 2


def _full_width_latin_layout(num_rows):
    font_off = 0
    for row in range(num_rows):
        for column in range(16):
            for glyph_tile in range(4):
                pixels_x = column * 16 + (glyph_tile & 1) * 8
                for i in range(8):
                    pixels_y = row * 16 + (glyph_tile >> 1) * 8 + i
                    yield font_off, pixels_y * 64 + pixels_x // 4
                    font_off += 2


def _halfwidth_japanese_layout(num_rows):
    for row in range(num_rows):
        for column in range(16):
            glyph_index = row * 16 + column
            for glyph_tile in range(2):
                pixels_x = column * 8
                font_off = 512 * (glyph_index >> 4) + 16 * (glyph_index & 0xF) + 256 * glyph_tile
                for i in range(8):
                    pixels_y = row * 16 + glyph_tile * 8 + i
                    yield font_off, pixels_y * 32 + pixels_x // 4
                    font_off += 2


def _fullwidth_japanese_layout(num_rows):
    for row in range(num_rows):
        for column in range(16):
            glyph_index = row * 16 + column
            for glyph_tile in range(4):
                pixels_x = column * 16 + (glyph_tile & 1) * 8
                font_off = (512 * (glyph_index >> 3) + 32 * (glyph_index & 7)
                            + 256 * (glyph_tile >> 1) + 16 * (glyph_tile & 1))
                for i in range(8):
                    pixels_y = row * 16 + (glyph_tile >> 1) * 8 + i
                    yield font_off, pixels_y * 64 + pixels_x // 4
                    font_off += 2


def _font_to_pixels(src, size, layout):
    dest = bytearray(size)
    for font_off, pix_off in layout:
        dest[pix_off] = _get(src, font_off + 1)
        dest[pix_off + 1] = _get(src, font_off)
    return bytes(dest)


def _pixels_to_font(src, size, layout):
    dest = bytearray(size)
    for font_off, pix_off in layout:
        dest[font_off] = _get(src, pix_off + 1)
        dest[font_off + 1] = _get(src, pix_off)
    return bytes(dest)


def _set_font_palette(image):
    image.has_palette = True
    image.palette.num_colors = 4
    colors = list(image.palette.colors)
    colors[:4] = FONT_PALETTE
    image.palette.colors = colors
    image.has_transparency = False


def _read_font(path, image, glyph_size, width, layout_fn, check_file_multiple=False):
    buffer = read_whole_file(path)
    file_size = len(buffer)
    if check_file_multiple and file_size % glyph_size != 0:
        fatal_error("The file size (%d) is not a multiple of %d.\n" % (file_size, glyph_size))
    num_glyphs = file_size // glyph_size
    if num_glyphs % 16 != 0:
        fatal_error("The number of glyphs (%d) is not a multiple of 16.\n" % num_glyphs)
    num_rows = num_glyphs // 16
    image.width = width
    image.height = num_rows * 16
    image.bit_depth = 2
    image.pixels = _font_to_pixels(buffer, max(file_size, image.height * width // 4), layout_fn(num_rows))
    _set_font_palette(image)


def _write_font(path, image, width, row_bytes, layout_fn):
    if image.width != width:
        fatal_error("The width of the font image (%d) is not %d.\n" % (image.width, width))
    if image.height % 16 != 0:
        fatal_error("The height of the font image (%d) is not a multiple of 16.\n" % image.height)
    num_rows = image.height // 16
    buffer_size = num_rows * 16 * row_bytes
    write_whole_file(path, _pixels_to_font(image.pixels, buffer_size, layout_fn(num_rows)))


def read_half_width_latin_font(path, image):
    _read_font(path, image, 32, 256, _half_width_latin_layout)


def write_half_width_latin_font(path, image):
    _write_font(path, image, 256, 64, _half_width_latin_layout)


def read_full_width_latin_font(path, image):
    _read_font(path, image, 64, 256, _full_width_latin_layout)


def write_full_width_latin_font(path, image):
    _write_font(path, image, 256, 64, _full_width_latin_layout)


def read_halfwidth_japanese_font(path, image):
    _read_font(path, image, 32, 128, _halfwidth_japanese_layout, check_file_multiple=True)


def write_halfwidth_japanese_font(path, image):
    _write_font(path, image, 128, 32, _halfwidth_japanese_layout)


def read_fullwidth_japanese_font(path, image):
    _read_font(path, image, 64, 256, _fullwidth_japanese_layout)


def write_fullwidth_japanese_font(path, image):
    _write_font(path, image, 256, 64, _fullwidth_japanese_layout)
