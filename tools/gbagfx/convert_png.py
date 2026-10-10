# Python port of gbagfx convert_png.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# A small self-contained PNG reader/writer (no third-party dependencies).
# The writer follows libpng's default behavior (filter selection, zlib
# settings and IDAT chunking) so that its output matches the C tool.

import struct
import sys
import zlib

from util import fatal_error

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

PNG_COLOR_TYPE_GRAY = 0
PNG_COLOR_TYPE_RGB = 2
PNG_COLOR_TYPE_PALETTE = 3
PNG_COLOR_TYPE_GRAY_ALPHA = 4
PNG_COLOR_TYPE_RGB_ALPHA = 6

_CHANNELS = {
    PNG_COLOR_TYPE_GRAY: 1,
    PNG_COLOR_TYPE_RGB: 3,
    PNG_COLOR_TYPE_PALETTE: 1,
    PNG_COLOR_TYPE_GRAY_ALPHA: 2,
    PNG_COLOR_TYPE_RGB_ALPHA: 4,
}

# Adam7 passes: (x start, y start, x step, y step)
_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


class PngInfo:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.bit_depth = 0
        self.color_type = 0
        self.interlace = 0
        self.palette = None  # list of (r, g, b)
        self.rows = None     # list of bytes, one per row (packed pixels)

    @property
    def rowbytes(self):
        return (self.width * self.bit_depth * _CHANNELS[self.color_type] + 7) // 8


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data, offset, height, rowbytes, bpp, path):
    rows = []
    prev = bytearray(rowbytes)
    pos = offset
    for _ in range(height):
        if pos + 1 + rowbytes > len(data):
            fatal_error('Error reading from "%s".\n' % path)
        ftype = data[pos]
        line = bytearray(data[pos + 1:pos + 1 + rowbytes])
        pos += 1 + rowbytes
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, rowbytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(rowbytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(rowbytes):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(rowbytes):
                if i >= bpp:
                    a = line[i - bpp]
                    c = prev[i - bpp]
                else:
                    a = 0
                    c = 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            fatal_error('Error reading from "%s".\n' % path)
        rows.append(bytes(line))
        prev = line
    return rows, pos


def _get_pixel(row, x, bits):
    if bits == 8:
        return row[x]
    per_byte = 8 // bits
    byte = row[x // per_byte]
    shift = 8 - bits * (x % per_byte + 1)
    return (byte >> shift) & ((1 << bits) - 1)


def _deinterlace(data, info, path):
    bits_per_pixel = info.bit_depth * _CHANNELS[info.color_type]
    bpp = max(1, bits_per_pixel // 8)
    rowbytes = info.rowbytes
    out = [bytearray(rowbytes) for _ in range(info.height)]
    pos = 0
    for x0, y0, dx, dy in _ADAM7:
        pw = (info.width - x0 + dx - 1) // dx
        ph = (info.height - y0 + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        prb = (pw * bits_per_pixel + 7) // 8
        rows, pos = _unfilter(data, pos, ph, prb, bpp, path)
        for py, row in enumerate(rows):
            y = y0 + py * dy
            dest = out[y]
            for px in range(pw):
                x = x0 + px * dx
                if bits_per_pixel >= 8:
                    n = bits_per_pixel // 8
                    dest[x * n:(x + 1) * n] = row[px * n:(px + 1) * n]
                else:
                    value = _get_pixel(row, px, bits_per_pixel)
                    per_byte = 8 // bits_per_pixel
                    shift = 8 - bits_per_pixel * (x % per_byte + 1)
                    dest[x // per_byte] |= value << shift
    return [bytes(r) for r in out]


def read_png_info(path, read_pixels=True):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        fatal_error('Failed to open "%s" for reading.\n' % path)

    if len(data) < 8:
        fatal_error('Failed to read PNG signature from "%s".\n' % path)
    if data[:8] != PNG_SIGNATURE:
        fatal_error('"%s" does not have a valid PNG signature.\n' % path)

    info = PngInfo()
    idat = []
    pos = 8
    seen_ihdr = False
    while pos + 8 <= len(data):
        length, ctype = struct.unpack_from(">I4s", data, pos)
        chunk = data[pos + 8:pos + 8 + length]
        crc_ok = len(data) >= pos + 12 + length and \
            struct.unpack_from(">I", data, pos + 8 + length)[0] == (zlib.crc32(data[pos + 4:pos + 8 + length]) & 0xFFFFFFFF)
        critical = not (ctype[0] & 0x20)
        if not crc_ok and critical:
            fatal_error('Failed to init I/O for reading "%s".\n' % path)
        pos += 12 + length
        if ctype == b"IHDR":
            (info.width, info.height, info.bit_depth, info.color_type,
             _, _, info.interlace) = struct.unpack(">IIBBBBB", chunk[:13])
            seen_ihdr = True
        elif ctype == b"PLTE":
            info.palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk) - len(chunk) % 3, 3)]
        elif ctype == b"IDAT":
            idat.append(chunk)
        elif ctype == b"IEND":
            break

    if not seen_ihdr or info.color_type not in _CHANNELS:
        fatal_error('Failed to init I/O for reading "%s".\n' % path)

    if read_pixels:
        try:
            raw = zlib.decompress(b"".join(idat))
        except zlib.error:
            fatal_error('Error reading from "%s".\n' % path)
        if info.interlace:
            info.rows = _deinterlace(raw, info, path)
        else:
            bpp = max(1, (info.bit_depth * _CHANNELS[info.color_type]) // 8)
            info.rows, _ = _unfilter(raw, 0, info.height, info.rowbytes, bpp, path)
    return info


def convert_bit_depth(src, src_bit_depth, dest_bit_depth, num_pixels):
    src_size = ((num_pixels * src_bit_depth + 7) & ~7) // 8
    dest_size = ((num_pixels * dest_bit_depth + 7) & ~7) // 8
    output = bytearray(dest_size + 1)
    dest = 0
    dest_bit = 8 - dest_bit_depth
    src_mask = (1 << src_bit_depth) - 1
    dest_mask = (1 << dest_bit_depth) - 1
    src = bytes(src) + bytes(max(0, src_size - len(src)))
    for i in range(src_size):
        src_byte = src[i]
        j = 8 - src_bit_depth
        while j >= 0:
            pixel = ((src_byte >> j) & src_mask) & dest_mask
            output[dest] |= (pixel << dest_bit) & 0xFF
            dest_bit -= dest_bit_depth
            if dest_bit < 0:
                dest += 1
                dest_bit = 8 - dest_bit_depth
            j -= src_bit_depth
    return output[:dest_size]


def read_png(path, image):
    info = read_png_info(path)

    if info.color_type != PNG_COLOR_TYPE_GRAY and info.color_type != PNG_COLOR_TYPE_PALETTE:
        fatal_error('"%s" has an unsupported color type.\n' % path)

    image.has_palette = info.color_type == PNG_COLOR_TYPE_PALETTE
    image.width = info.width
    image.height = info.height
    image.pixels = b"".join(info.rows)

    if info.bit_depth != image.bit_depth and image.tilemap is None:
        if info.bit_depth not in (1, 2, 4, 8):
            fatal_error("Bit depth of image must be 1, 2, 4, or 8.\n")
        image.pixels = convert_bit_depth(image.pixels, info.bit_depth, image.bit_depth,
                                         image.width * image.height)


def read_png_palette(path, palette):
    info = read_png_info(path, read_pixels=False)

    if info.color_type != PNG_COLOR_TYPE_PALETTE:
        fatal_error('The image "%s" does not contain a palette.\n' % path)
    if info.palette is None:
        fatal_error('Failed to retrieve palette from "%s".\n' % path)
    if len(info.palette) > 256:
        fatal_error("Images with more than 256 colors are not supported.\n")

    palette.num_colors = len(info.palette)
    for i, color in enumerate(info.palette):
        palette.colors[i] = color


# ---------------------------------------------------------------------------
# PNG writing (mirrors libpng 1.6 defaults)
# ---------------------------------------------------------------------------

PNG_ZBUF_SIZE = 8192


def _chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def _abs_sum(row):
    total = 0
    for v in row:
        total += v if v < 128 else 256 - v
    return total


def _filter_row(row, prev, bpp):
    """libpng's png_write_find_filter() heuristic with all filters enabled."""
    n = len(row)
    best_type = 0
    best = row
    mins = _abs_sum(row)

    sub = bytearray(n)
    for i in range(n):
        sub[i] = (row[i] - (row[i - bpp] if i >= bpp else 0)) & 0xFF
    s = _abs_sum(sub)
    if s < mins:
        mins, best, best_type = s, sub, 1

    up = bytearray(n)
    for i in range(n):
        up[i] = (row[i] - prev[i]) & 0xFF
    s = _abs_sum(up)
    if s < mins:
        mins, best, best_type = s, up, 2

    avg = bytearray(n)
    for i in range(n):
        left = row[i - bpp] if i >= bpp else 0
        avg[i] = (row[i] - ((left + prev[i]) >> 1)) & 0xFF
    s = _abs_sum(avg)
    if s < mins:
        mins, best, best_type = s, avg, 3

    paeth = bytearray(n)
    for i in range(n):
        if i >= bpp:
            a = row[i - bpp]
            c = prev[i - bpp]
        else:
            a = c = 0
        paeth[i] = (row[i] - _paeth(a, prev[i], c)) & 0xFF
    s = _abs_sum(paeth)
    if s < mins:
        mins, best, best_type = s, paeth, 4

    return best_type, bytes(best)


def _optimize_cmf(data, data_size):
    if data_size <= 16384:
        z_cmf = data[0]
        if (z_cmf & 0x0F) == 8 and (z_cmf & 0xF0) <= 0x70:
            z_cinfo = z_cmf >> 4
            half_z_window_size = 1 << (z_cinfo + 7)
            if data_size <= half_z_window_size:
                while True:
                    half_z_window_size >>= 1
                    z_cinfo -= 1
                    if not (z_cinfo > 0 and data_size <= half_z_window_size):
                        break
                z_cmf = (z_cmf & 0x0F) | (z_cinfo << 4)
                data[0] = z_cmf
                tmp = data[1] & 0xE0
                tmp += 0x1F - ((z_cmf << 8) + tmp) % 0x1F
                data[1] = tmp


def write_png_data(width, height, bit_depth, color_type, rows, palette=None, trns=None):
    channels = _CHANNELS[color_type]
    rowbytes = (width * bit_depth * channels + 7) // 8
    bpp = max(1, (bit_depth * channels) // 8)
    use_filters = not (color_type == PNG_COLOR_TYPE_PALETTE or bit_depth < 8)

    raw = bytearray()
    prev = bytes(rowbytes)
    for y in range(height):
        row = bytes(rows[y][:rowbytes]).ljust(rowbytes, b"\0")
        if use_filters:
            ftype, filtered = _filter_row(row, prev, bpp)
        else:
            ftype, filtered = 0, row
        raw.append(ftype)
        raw += filtered
        prev = row

    data_size = (rowbytes + 1) * height
    window_bits = 15
    if data_size <= 16384:
        half_window_size = 1 << (window_bits - 1)
        while data_size + 262 <= half_window_size:
            half_window_size >>= 1
            window_bits -= 1
    if window_bits == 8:
        window_bits = 9
    strategy = zlib.Z_FILTERED if use_filters else zlib.Z_DEFAULT_STRATEGY
    comp = zlib.compressobj(6, zlib.DEFLATED, window_bits, 8, strategy)
    compressed = bytearray(comp.compress(bytes(raw)) + comp.flush())
    _optimize_cmf(compressed, data_size)

    out = bytearray(PNG_SIGNATURE)
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0))
    if palette is not None:
        out += _chunk(b"PLTE", b"".join(bytes(c) for c in palette))
    if trns is not None:
        out += _chunk(b"tRNS", bytes(trns))
    for i in range(0, len(compressed), PNG_ZBUF_SIZE):
        out += _chunk(b"IDAT", bytes(compressed[i:i + PNG_ZBUF_SIZE]))
    out += _chunk(b"IEND", b"")
    return bytes(out)


def _max_palette_index(rows, width, bit_depth):
    highest = 0
    for row in rows:
        for x in range(width):
            value = _get_pixel(row, x, bit_depth) if (x * bit_depth) // 8 < len(row) else 0
            if value > highest:
                highest = value
    return highest


def write_png(path, image):
    try:
        fp = open(path, "wb")
    except OSError:
        fatal_error('Failed to open "%s" for writing.\n' % path)
    rowbytes = (image.width * image.bit_depth + 7) // 8
    rows = [image.pixels[i * rowbytes:(i + 1) * rowbytes] for i in range(image.height)]
    if image.has_palette:
        color_type = PNG_COLOR_TYPE_PALETTE
        if image.palette.num_colors > (1 << image.bit_depth):
            # libpng's png_set_PLTE() rejects this
            fp.close()
            sys.stderr.write("libpng error: Invalid palette length\n")
            fatal_error('Error writing header for "%s".\n' % path)
        palette = [image.palette.colors[i] for i in range(image.palette.num_colors)]
        trns = b"\0" if image.has_transparency else None
    else:
        color_type = PNG_COLOR_TYPE_GRAY
        palette = None
        trns = None
    data = write_png_data(image.width, image.height, image.bit_depth, color_type, rows, palette, trns)
    if palette is not None and _max_palette_index(rows, image.width, image.bit_depth) >= len(palette):
        # libpng's png_write_end() raises this after the image data has been
        # written, so the file is left without its IEND chunk.
        with fp:
            fp.write(data[:-12])
        sys.stderr.write("libpng error: Wrote palette index exceeding num_palette\n")
        fatal_error('Error ending write of "%s".\n' % path)
    with fp:
        fp.write(data)
