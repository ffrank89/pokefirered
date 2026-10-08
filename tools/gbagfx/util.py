# Python port of gbagfx util.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)

import sys


def fatal_error(msg):
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.exit(1)


def parse_number(s, radix=10):
    """Emulates ParseNumber() (strtol). Returns (value, rest) or (None, s)."""
    i = 0
    n = len(s)
    while i < n and s[i] in " \t\n\v\f\r":
        i += 1
    neg = False
    if i < n and s[i] in "+-":
        neg = s[i] == "-"
        i += 1
    digits_start = i
    value = 0
    while i < n and "0" <= s[i] <= "9":
        value = value * radix + ord(s[i]) - 48
        i += 1
    if i == digits_start:
        return None, s
    if neg:
        value = -value
    if value > 0x7FFFFFFF or value < -0x80000000:
        return None, s[i:]
    return value, s[i:]


def parse_int_arg(s):
    value, _ = parse_number(s)
    return value


def get_file_extension(path):
    """Emulates GetFileExtension(): returns the suffix starting at the last '.'
    (or the whole path if there is none)."""
    idx = path.rfind(".")
    if idx <= 0:
        return path
    return path[idx:]


def get_file_extension_after_dot(path):
    idx = path.rfind(".")
    if idx <= 0:
        return None
    ext = path[idx + 1:]
    if not ext:
        return None
    return ext


def read_whole_file(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        fatal_error('Failed to open "%s" for reading.\n' % path)


def read_whole_file_zero_padded(path, pad_amount):
    return read_whole_file(path) + bytes(pad_amount)


def write_whole_file(path, data):
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        fatal_error('Failed to open "%s" for writing.\n' % path)
