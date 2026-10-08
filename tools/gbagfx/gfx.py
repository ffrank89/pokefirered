# Python port of gbagfx gfx.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# Conversion between linear (PNG-style) pixel buffers and GBA tile data.

import sys

from util import fatal_error, read_whole_file, write_whole_file

NUM_TILES_IGNORE = 0
NUM_TILES_WARN = 1
NUM_TILES_ERROR = 2


class Palette:
    def __init__(self):
        self.colors = [(0, 0, 0)] * 256
        self.num_colors = 0


class Image:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.bit_depth = 0
        self.pixels = b""
        self.has_palette = False
        self.palette = Palette()
        self.has_transparency = False
        self.tilemap = None  # raw tilemap bytes, if any
        self.is_affine = False


def _upconvert(x):
    return (x * 255) // 31


def _tile_positions(num_tiles, metatiles_wide, metatile_width, metatile_height):
    """Yields (tileX, tileY) in tile units, following AdvanceMetatilePosition()."""
    sub_x = sub_y = meta_x = meta_y = 0
    for _ in range(num_tiles):
        yield meta_x * metatile_width + sub_x, meta_y * metatile_height + sub_y
        sub_x += 1
        if sub_x == metatile_width:
            sub_x = 0
            sub_y += 1
            if sub_y == metatile_height:
                sub_y = 0
                meta_x += 1
                if meta_x == metatiles_wide:
                    meta_x = 0
                    meta_y += 1


_REVERSE_BITS = bytes(int("{:08b}".format(i)[::-1], 2) for i in range(256))
_INVERT_NIBBLES = bytes(((15 - (i >> 4)) << 4) | (15 - (i & 0xF)) for i in range(256))
_SWAP_NIBBLES = bytes(((i & 0xF) << 4) | (i >> 4) for i in range(256))
_INVERT_BYTES = bytes(255 - i for i in range(256))


def _convert_from_tiles(src, dest, num_tiles, metatiles_wide, metatile_width, metatile_height,
                        bit_depth, invert_colors):
    pitch = metatiles_wide * metatile_width * bit_depth
    tile_row = bit_depth  # bytes per row of one tile
    src_pos = 0
    for tile_x, tile_y in _tile_positions(num_tiles, metatiles_wide, metatile_width, metatile_height):
        for j in range(8):
            dest_y = tile_y * 8 + j
            dest_off = dest_y * pitch + tile_x * tile_row
            chunk = src[src_pos:src_pos + tile_row]
            if len(chunk) < tile_row:
                chunk = chunk.ljust(tile_row, b"\0")
            src_pos += tile_row
            if bit_depth == 1:
                b = _REVERSE_BITS[chunk[0]]
                if invert_colors:
                    b ^= 0xFF
                dest[dest_off] = b
            elif bit_depth == 4:
                chunk = chunk.translate(_SWAP_NIBBLES)
                if invert_colors:
                    chunk = chunk.translate(_INVERT_NIBBLES)
                dest[dest_off:dest_off + 4] = chunk
            else:
                if invert_colors:
                    chunk = chunk.translate(_INVERT_BYTES)
                dest[dest_off:dest_off + 8] = chunk


def _convert_to_tiles(src, num_tiles, metatiles_wide, metatile_width, metatile_height,
                      bit_depth, invert_colors):
    pitch = metatiles_wide * metatile_width * bit_depth
    tile_row = bit_depth
    out = bytearray()
    src_len = len(src)
    for tile_x, tile_y in _tile_positions(num_tiles, metatiles_wide, metatile_width, metatile_height):
        base = tile_y * 8 * pitch + tile_x * tile_row
        for j in range(8):
            off = base + j * pitch
            chunk = src[off:off + tile_row]
            if len(chunk) < tile_row:
                chunk = bytes(chunk).ljust(tile_row, b"\0")
                if off > src_len:
                    chunk = bytes(tile_row)
            out += chunk
    if bit_depth == 1:
        out = out.translate(_REVERSE_BITS)
        if invert_colors:
            out = out.translate(_INVERT_BYTES)
    elif bit_depth == 4:
        out = out.translate(_SWAP_NIBBLES)
        if invert_colors:
            out = out.translate(_INVERT_NIBBLES)
    else:
        if invert_colors:
            out = out.translate(_INVERT_BYTES)
    return out


def _copy_plain_pixels(src, size, data_width, invert_colors):
    out = bytearray()
    if data_width == 0:
        return out
    src = bytes(src)
    padded = src + bytes(max(0, size + data_width - len(src)))
    for i in range(0, size, data_width):
        group = padded[i:i + data_width][::-1]
        out += group
    if invert_colors:
        out = out.translate(_INVERT_BYTES)
    return out


def _vflip_tile(tile, bit_depth):
    row = bit_depth
    rows = [tile[i * row:(i + 1) * row] for i in range(8)]
    return b"".join(reversed(rows))


def _hflip_tile(tile, bit_depth):
    if bit_depth == 1:
        return bytes(tile).translate(_REVERSE_BITS)
    if bit_depth == 4:
        out = bytearray()
        for i in range(8):
            out += bytes(tile[4 * i:4 * i + 4])[::-1].translate(_SWAP_NIBBLES)
        return bytes(out)
    out = bytearray()
    for i in range(8):
        out += bytes(tile[8 * i:8 * i + 8])[::-1]
    return bytes(out)


def _decode_tilemap(tiles, tilemap, is_affine, tile_size, out_tile_size, bit_depth):
    map_tile_size = 1 if is_affine else 2
    num_tiles = len(tilemap) // map_tile_size
    tiles = bytes(tiles)

    def get_tile(index):
        chunk = tiles[index * tile_size:(index + 1) * tile_size]
        return chunk.ljust(tile_size, b"\0")

    decoded = bytearray()
    if is_affine:
        for i in range(num_tiles):
            decoded += get_tile(tilemap[i])
        return decoded, num_tiles

    effective_bit_depth = bit_depth if tile_size == out_tile_size else 8
    for i in range(num_tiles):
        entry = tilemap[2 * i] | (tilemap[2 * i + 1] << 8)
        index = entry & 0x3FF
        hflip = (entry >> 10) & 1
        vflip = (entry >> 11) & 1
        palno = (entry >> 12) & 0xF
        in_tile = get_tile(index)
        if tile_size == out_tile_size:
            out_tile = in_tile
        else:
            out_tile = bytearray(64)
            for j in range(64):
                shift = (j & 1) * 4
                out_tile[j] = (in_tile[j // 2] & (0xF << shift)) >> shift
            out_tile = bytes(out_tile)
        if hflip:
            out_tile = _hflip_tile(out_tile, effective_bit_depth)
        if vflip:
            out_tile = _vflip_tile(out_tile, effective_bit_depth)
        if bit_depth == 4 and effective_bit_depth == 8:
            out_tile = bytes((b & 0xF) | (((15 - palno) << 4) & 0xFF) for b in out_tile)
        decoded += out_tile
    return decoded, num_tiles


def read_tile_image(path, tiles_width, metatile_width, metatile_height, image, invert_colors):
    tile_size = image.bit_depth * 8
    buffer = read_whole_file(path)
    num_tiles = len(buffer) // tile_size

    if image.tilemap is not None:
        out_tile_size = 64 if (image.bit_depth == 4 and image.palette.num_colors > 16) else tile_size
        buffer, num_tiles = _decode_tilemap(buffer, image.tilemap, image.is_affine, tile_size,
                                            out_tile_size, image.bit_depth)
        if out_tile_size == 64:
            tile_size = 64
            image.bit_depth = 8

    tiles_height = (num_tiles + tiles_width - 1) // tiles_width

    if tiles_width % metatile_width != 0:
        fatal_error("The width in tiles (%d) isn't a multiple of the specified metatile width (%d)\n"
                    % (tiles_width, metatile_width))
    if tiles_height % metatile_height != 0:
        fatal_error("The height in tiles (%d) isn't a multiple of the specified metatile height (%d)\n"
                    % (tiles_height, metatile_height))

    image.width = tiles_width * 8
    image.height = tiles_height * 8
    pixels = bytearray(tiles_width * tiles_height * tile_size)
    metatiles_wide = tiles_width // metatile_width

    _convert_from_tiles(buffer, pixels, num_tiles, metatiles_wide, metatile_width, metatile_height,
                        image.bit_depth, invert_colors)
    image.pixels = bytes(pixels)


def write_tile_image(path, num_tiles_mode, num_tiles, metatile_width, metatile_height, image, invert_colors):
    tile_size = image.bit_depth * 8

    if image.width % 8 != 0:
        fatal_error("The width in pixels (%d) isn't a multiple of 8.\n" % image.width)
    if image.height % 8 != 0:
        fatal_error("The height in pixels (%d) isn't a multiple of 8.\n" % image.height)

    tiles_width = image.width // 8
    tiles_height = image.height // 8

    if tiles_width % metatile_width != 0:
        fatal_error("The width in tiles (%d) isn't a multiple of the specified metatile width (%d)\n"
                    % (tiles_width, metatile_width))
    if tiles_height % metatile_height != 0:
        fatal_error("The height in tiles (%d) isn't a multiple of the specified metatile height (%d)\n"
                    % (tiles_height, metatile_height))

    max_num_tiles = tiles_width * tiles_height

    if num_tiles == 0:
        num_tiles = max_num_tiles
    elif num_tiles > max_num_tiles:
        fatal_error("The specified number of tiles (%d) is greater than the maximum possible value (%d).\n"
                    % (num_tiles, max_num_tiles))

    buffer_size = num_tiles * tile_size
    max_buffer_size = max_num_tiles * tile_size
    metatiles_wide = tiles_width // metatile_width

    buffer = _convert_to_tiles(image.pixels, max_num_tiles, metatiles_wide, metatile_width, metatile_height,
                               image.bit_depth, invert_colors)

    zero_padded = True
    if buffer_size < max_buffer_size:
        tail = buffer[buffer_size:max_buffer_size]
        stripped = tail.lstrip(b"\0")
        if stripped:
            i = buffer_size + (len(tail) - len(stripped))
            if num_tiles_mode == NUM_TILES_WARN:
                sys.stderr.write("Ignoring -num_tiles %d because tile %d contains non-transparent pixels.\n"
                                 % (num_tiles, 1 + i // tile_size))
                zero_padded = False
            elif num_tiles_mode == NUM_TILES_ERROR:
                fatal_error("Tile %d contains non-transparent pixels.\n" % (1 + i // tile_size))

    write_whole_file(path, bytes(buffer[:buffer_size if zero_padded else max_buffer_size]))


def read_plain_image(path, data_width, image, invert_colors):
    buffer = read_whole_file(path)
    file_size = len(buffer)

    if file_size % data_width != 0:
        fatal_error("The image data size (%d) isn't a multiple of the specified data width %d.\n"
                    % (file_size, data_width))

    pixels_per_byte = 8 // image.bit_depth
    if image.width % pixels_per_byte != 0:
        fatal_error("The width in pixels (%d) isn't a multiple of %d.\n" % (image.width, pixels_per_byte))

    num_pixels = file_size * pixels_per_byte
    image.height = (num_pixels + image.width - 1) // image.width
    total = image.width * image.height * image.bit_depth // 8
    pixels = _copy_plain_pixels(buffer, file_size, data_width, invert_colors)
    image.pixels = bytes(pixels).ljust(total, b"\0")


def write_plain_image(path, data_width, image, invert_colors):
    buffer_size = image.width * image.height * image.bit_depth // 8

    if buffer_size % data_width != 0:
        fatal_error("The image data size (%d) isn't a multiple of the specified data width %d.\n"
                    % (buffer_size, data_width))

    pixels_per_byte = 8 // image.bit_depth
    if image.width % pixels_per_byte != 0:
        fatal_error("The width in pixels (%d) isn't a multiple of %d.\n" % (image.width, pixels_per_byte))

    buffer = _copy_plain_pixels(image.pixels, buffer_size, data_width, invert_colors)
    write_whole_file(path, bytes(buffer[:buffer_size]))


def read_gba_palette(path, palette):
    data = read_whole_file(path)
    file_size = len(data)

    if file_size % 2 != 0:
        fatal_error("The file size (%d) is not a multiple of 2.\n" % file_size)

    palette.num_colors = file_size // 2
    colors = []
    for i in range(palette.num_colors):
        entry = data[i * 2] | (data[i * 2 + 1] << 8)
        colors.append((_upconvert(entry & 0x1F), _upconvert((entry >> 5) & 0x1F),
                       _upconvert((entry >> 10) & 0x1F)))
    palette.colors = colors + [(0, 0, 0)] * (256 - len(colors)) if len(colors) <= 256 else colors
    # png can only accept 16 or 256 colors, so fill the remainder with black
    if palette.num_colors > 16:
        palette.num_colors = 256


def write_gba_palette(path, palette):
    out = bytearray()
    for i in range(palette.num_colors):
        red, green, blue = palette.colors[i]
        entry = ((blue // 8) << 10) | ((green // 8) << 5) | (red // 8)
        out.append(entry & 0xFF)
        out.append(entry >> 8)
    write_whole_file(path, bytes(out))
