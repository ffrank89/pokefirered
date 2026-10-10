# Python port of gbagfx jasc_pal.c
# Copyright (c) 2015 YamaArashi (MIT license, see LICENSE)
#
# Reads and writes JASC-PAL (Paint Shop Pro) palette files.

from util import fatal_error, parse_number

MAX_LINE_LENGTH = 11


class _LineReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def getc(self):
        if self.pos >= len(self.data):
            return -1
        c = self.data[self.pos]
        self.pos += 1
        return c

    def read_line(self):
        line = bytearray()
        while True:
            c = self.getc()
            if c == 0x0D:
                c = self.getc()
                if c != 0x0A:
                    fatal_error("CR line endings aren't supported.\n")
                return line.decode("latin-1")
            if c == 0x0A:
                return line.decode("latin-1")
            if c == -1:
                fatal_error("Unexpected EOF. No LF or CRLF at end of file.\n")
            if c == 0:
                fatal_error("NUL character in file.\n")
            if len(line) == MAX_LINE_LENGTH:
                fatal_error('The line "%s" is too long.\n' % line.decode("latin-1"))
            line.append(c)


def read_jasc_palette(path, palette):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        fatal_error('Failed to open JASC-PAL file "%s" for reading.\n' % path)

    reader = _LineReader(data)

    if reader.read_line() != "JASC-PAL":
        fatal_error("Invalid JASC-PAL signature.\n")

    if reader.read_line() != "0100":
        fatal_error("Unsuported JASC-PAL version.\n")

    num_colors, _ = parse_number(reader.read_line())
    if num_colors is None:
        fatal_error("Failed to parse number of colors.\n")
    if num_colors < 1 or num_colors > 256:
        fatal_error("%d is an invalid number of colors. The number of colors must be in the range [1, 256].\n"
                    % num_colors)
    palette.num_colors = num_colors

    colors = list(palette.colors)
    for i in range(num_colors):
        s = reader.read_line()

        red, s = parse_number(s)
        if red is None:
            fatal_error("Failed to parse red color component.\n")
        if not s.startswith(" "):
            fatal_error("Expected a space after red color component.\n")
        s = s[1:]
        if not s or s[0] < "0" or s[0] > "9":
            fatal_error("Expected only a space between red and green color components.\n")

        green, s = parse_number(s)
        if green is None:
            fatal_error("Failed to parse green color component.\n")
        if not s.startswith(" "):
            fatal_error("Expected a space after green color component.\n")
        s = s[1:]
        if not s or s[0] < "0" or s[0] > "9":
            fatal_error("Expected only a space between green and blue color components.\n")

        blue, s = parse_number(s)
        if blue is None:
            fatal_error("Failed to parse blue color component.\n")
        if s:
            fatal_error("Garbage after blue color component.\n")

        if red < 0 or red > 255:
            fatal_error("Red color component (%d) is outside the range [0, 255].\n" % red)
        if green < 0 or green > 255:
            fatal_error("Green color component (%d) is outside the range [0, 255].\n" % green)
        if blue < 0 or blue > 255:
            fatal_error("Blue color component (%d) is outside the range [0, 255].\n" % blue)

        colors[i] = (red, green, blue)

    palette.colors = colors

    if reader.getc() != -1:
        fatal_error("Garbage after color data.\n")


def write_jasc_palette(path, palette):
    lines = ["JASC-PAL\r\n", "0100\r\n", "%d\r\n" % palette.num_colors]
    for i in range(palette.num_colors):
        lines.append("%d %d %d\r\n" % tuple(palette.colors[i]))
    with open(path, "wb") as f:
        f.write("".join(lines).encode("latin-1"))
