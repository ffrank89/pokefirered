# Python port of gbagfx rl.c
# Copyright (c) 2016 YamaArashi (MIT license, see LICENSE)
#
# GBA BIOS-compatible run-length compression.

from util import fatal_error


def rl_decompress(src):
    src_size = len(src)
    if src_size < 4:
        fatal_error("Fatal error while decompressing RL file.\n")

    dest_size = (src[3] << 16) | (src[2] << 8) | src[1]
    dest = bytearray()
    src_pos = 4

    while True:
        if src_pos >= src_size:
            fatal_error("Fatal error while decompressing RL file.\n")
        flags = src[src_pos]
        src_pos += 1
        if flags & 0x80:
            length = (flags & 0x7F) + 3
            data = src[src_pos] if src_pos < src_size else 0
            src_pos += 1
            if len(dest) + length > dest_size:
                fatal_error("Fatal error while decompressing RL file.\n")
            dest.extend(bytes((data,)) * length)
        else:
            length = (flags & 0x7F) + 1
            if len(dest) + length > dest_size:
                fatal_error("Fatal error while decompressing RL file.\n")
            chunk = src[src_pos:src_pos + length]
            dest.extend(chunk.ljust(length, b"\0"))
            src_pos += length
        if len(dest) == dest_size:
            return bytes(dest)


def rl_compress(src):
    src_size = len(src)
    if src_size <= 0:
        fatal_error("Fatal error while compressing RL file.\n")

    dest = bytearray((0x30, src_size & 0xFF, (src_size >> 8) & 0xFF, (src_size >> 16) & 0xFF))
    src_pos = 0

    while True:
        compress = False
        uncompressed_start = src_pos
        uncompressed_length = 0

        while src_pos < src_size and uncompressed_length < 0x7F + 1:
            compress = (src_pos + 2 < src_size
                        and src[src_pos] == src[src_pos + 1]
                        and src[src_pos] == src[src_pos + 2])
            if compress:
                break
            src_pos += 1
            uncompressed_length += 1

        if uncompressed_length > 0:
            dest.append(uncompressed_length - 1)
            dest.extend(src[uncompressed_start:uncompressed_start + uncompressed_length])

        if compress:
            data = src[src_pos]
            compressed_length = 0
            while (compressed_length < 0x7F + 3
                   and src_pos + compressed_length < src_size
                   and src[src_pos + compressed_length] == data):
                compressed_length += 1
            dest.append(0x80 | (compressed_length - 3))
            dest.append(data)
            src_pos += compressed_length

        if src_pos == src_size:
            remainder = len(dest) % 4
            if remainder != 0:
                dest.extend(bytes(4 - remainder))
            return dest
