# Python port of gbagfx lz.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# GBA BIOS-compatible LZ77 compression. The compressor produces exactly the
# same output as the original brute force C implementation, but uses a hash
# table of 3-byte prefixes to find candidate matches quickly.

import sys

from util import fatal_error


def lz_decompress(src):
    src_size = len(src)
    if src_size < 4:
        fatal_error("Fatal error while decompressing LZ file.\n")

    dest_size = (src[3] << 16) | (src[2] << 8) | src[1]
    dest = bytearray(dest_size)
    src_pos = 4
    dest_pos = 0

    while True:
        if src_pos >= src_size:
            fatal_error("Fatal error while decompressing LZ file.\n")
        flags = src[src_pos]
        src_pos += 1
        for _ in range(8):
            if flags & 0x80:
                if src_pos + 1 >= src_size:
                    fatal_error("Fatal error while decompressing LZ file.\n")
                block_size = (src[src_pos] >> 4) + 3
                block_distance = (((src[src_pos] & 0xF) << 8) | src[src_pos + 1]) + 1
                src_pos += 2
                block_pos = dest_pos - block_distance
                # Some Ruby/Sapphire tilesets overflow.
                if dest_pos + block_size > dest_size:
                    block_size = dest_size - dest_pos
                    sys.stderr.write("Destination buffer overflow.\n")
                if block_pos < 0:
                    fatal_error("Fatal error while decompressing LZ file.\n")
                for j in range(block_size):
                    dest[dest_pos] = dest[block_pos + j]
                    dest_pos += 1
            else:
                if src_pos >= src_size or dest_pos >= dest_size:
                    fatal_error("Fatal error while decompressing LZ file.\n")
                dest[dest_pos] = src[src_pos]
                dest_pos += 1
                src_pos += 1
            if dest_pos == dest_size:
                return bytes(dest)
            flags = (flags << 1) & 0xFF


def lz_compress(src, min_distance):
    src_size = len(src)
    if src_size <= 0:
        fatal_error("Fatal error while compressing LZ file.\n")

    src = bytes(src)
    dest = bytearray((0x10, src_size & 0xFF, (src_size >> 8) & 0xFF, (src_size >> 16) & 0xFF))

    table = {}
    inserted = 0
    src_pos = 0

    while True:
        flags_pos = len(dest)
        dest.append(0)
        flags = 0

        for i in range(8):
            # Make every earlier position available as a match candidate.
            while inserted < src_pos:
                if inserted + 2 < src_size:
                    key = src[inserted:inserted + 3]
                    lst = table.get(key)
                    if lst is None:
                        table[key] = [inserted]
                    else:
                        lst.append(inserted)
                inserted += 1

            best_size = 0
            best_distance = 0

            if src_pos + 2 < src_size:
                candidates = table.get(src[src_pos:src_pos + 3])
                if candidates:
                    max_len = min(18, src_size - src_pos)
                    lowest = src_pos - 0x1000
                    highest = src_pos - min_distance
                    target = src[src_pos:src_pos + max_len]
                    # Smallest distance first, which means the latest position first.
                    for idx in range(len(candidates) - 1, -1, -1):
                        q = candidates[idx]
                        if q > highest:
                            continue
                        if q < lowest:
                            break
                        if src[q:q + max_len] == target:
                            length = max_len
                        else:
                            length = 3
                            while src[q + length] == src[src_pos + length]:
                                length += 1
                        if length > best_size:
                            best_size = length
                            best_distance = src_pos - q
                            if length == max_len:
                                break

            if best_size >= 3:
                flags |= 0x80 >> i
                src_pos += best_size
                best_size -= 3
                best_distance -= 1
                dest.append(((best_size << 4) | (best_distance >> 8)) & 0xFF)
                dest.append(best_distance & 0xFF)
            else:
                dest.append(src[src_pos])
                src_pos += 1

            if src_pos == src_size:
                dest[flags_pos] = flags
                remainder = len(dest) % 4
                if remainder != 0:
                    dest.extend(bytes(4 - remainder))
                return dest

        dest[flags_pos] = flags
