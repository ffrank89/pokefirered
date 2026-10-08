#!/usr/bin/env python3
# Python port of bin2c.c
# Copyright(c) 2015-2016 YamaArashi (MIT license, see LICENSE)

import signal
import sys


def fatal_error(msg):
    sys.stderr.write(msg)
    sys.exit(1)


def extract_data(buffer, offset, size):
    if size == 1:
        return buffer[offset]
    if size == 2:
        return buffer[offset] | (buffer[offset + 1] << 8)
    if size == 4:
        value = int.from_bytes(buffer[offset:offset + 4], "little")
        # C stores this in an int
        return value - (1 << 32) if value & 0x80000000 else value
    fatal_error("Invalid size passed to ExtractData.\n")


def c_hex_alt(value, pad):
    # Emulates printf("%#*x", pad, value)
    value &= 0xFFFFFFFF
    text = "0x%x" % value if value != 0 else "0"
    return text.rjust(pad)


def main(argv):
    if len(argv) < 3:
        fatal_error("Usage: bin2c INPUT_FILE VAR_NAME [OPTIONS...]\n")

    try:
        with open(argv[1], "rb") as f:
            buffer = f.read()
    except OSError:
        fatal_error('Failed to open "%s" for reading.\n' % argv[1])

    var_name = argv[2]
    col = 1
    pad = 0
    size = 1
    is_signed = False
    is_static = False
    is_decimal = False

    def atoi(s):
        s = s.strip()
        num = ""
        for i, ch in enumerate(s):
            if ch.isdigit() or (i == 0 and ch in "+-"):
                num += ch
            else:
                break
        try:
            return int(num)
        except ValueError:
            return 0

    i = 3
    while i < len(argv):
        arg = argv[i]
        if arg in ("-col", "-pad", "-size"):
            i += 1
            if i >= len(argv):
                fatal_error("Missing argument after '%s'.\n" % arg)
            value = atoi(argv[i])
            if arg == "-col":
                col = value
            elif arg == "-pad":
                pad = value
            else:
                size = value
                if size not in (1, 2, 4):
                    fatal_error("Size must be 1, 2, or 4.\n")
        elif arg == "-signed":
            is_signed = True
            is_decimal = True
        elif arg == "-static":
            is_static = True
        elif arg == "-decimal":
            is_decimal = True
        else:
            fatal_error("Unrecognized option '%s'.\n" % arg)
        i += 1

    file_size = len(buffer)
    if (file_size & (size - 1)) != 0:
        fatal_error("Size %d doesn't evenly divide file size %d.\n" % (size, file_size))

    out = ["// Generated file. Do not edit.\n\n"]
    if is_static:
        out.append("static ")
    out.append("const ")
    out.append("%s%d " % ("s" if is_signed else "u", 8 * size))
    out.append("%s[] =\n{" % var_name)

    count = file_size // size
    offset = 0
    for i in range(count):
        if i % col == 0:
            out.append("\n    ")
        data = extract_data(buffer, offset, size)
        offset += size
        if is_decimal:
            if is_signed:
                out.append(str(data).rjust(pad) + ", ")
            else:
                out.append(str(data & 0xFFFFFFFF).rjust(pad) + "u, ")
        else:
            out.append(c_hex_alt(data, pad) + "u, ")

    out.append("\n};\n")
    sys.stdout.write("".join(out))
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
