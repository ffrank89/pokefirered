#!/usr/bin/env python3
# Python port of gbafix.c
# DevkitPro GBA ROM fix utility (by Dark Fader / BlackThunder / WinterMute / Sierraffinity)
# Licensed under the GNU LGPL v2 or later, see COPYING.

import signal
import struct
import sys

VER = "1.07"

HEADER_SIZE = 0xC0

GOOD_LOGO = bytes([
    0x24, 0xFF, 0xAE, 0x51, 0x69, 0x9A, 0xA2, 0x21, 0x3D, 0x84, 0x82, 0x0A, 0x84, 0xE4, 0x09, 0xAD,
    0x11, 0x24, 0x8B, 0x98, 0xC0, 0x81, 0x7F, 0x21, 0xA3, 0x52, 0xBE, 0x19, 0x93, 0x09, 0xCE, 0x20,
    0x10, 0x46, 0x4A, 0x4A, 0xF8, 0x27, 0x31, 0xEC, 0x58, 0xC7, 0xE8, 0x33, 0x82, 0xE3, 0xCE, 0xBF,
    0x85, 0xF4, 0xDF, 0x94, 0xCE, 0x4B, 0x09, 0xC1, 0x94, 0x56, 0x8A, 0xC0, 0x13, 0x72, 0xA7, 0xFC,
    0x9F, 0x84, 0x4D, 0x73, 0xA3, 0xCA, 0x9A, 0x61, 0x58, 0x97, 0xA3, 0x27, 0xFC, 0x03, 0x98, 0x76,
    0x23, 0x1D, 0xC7, 0x61, 0x03, 0x04, 0xAE, 0x56, 0xBF, 0x38, 0x84, 0x00, 0x40, 0xA7, 0x0E, 0xFD,
    0xFF, 0x52, 0xFE, 0x03, 0x6F, 0x95, 0x30, 0xF1, 0x97, 0xFB, 0xC0, 0x85, 0x60, 0xD6, 0x80, 0x25,
    0xA9, 0x63, 0xBE, 0x03, 0x01, 0x4E, 0x38, 0xE2, 0xF9, 0xA2, 0x34, 0xFF, 0xBB, 0x3E, 0x03, 0x44,
    0x78, 0x00, 0x90, 0xCB, 0x88, 0x11, 0x3A, 0x94, 0x65, 0xC0, 0x7C, 0x63, 0x87, 0xF0, 0x3C, 0xAF,
    0xD6, 0x25, 0xE4, 0x8B, 0x38, 0x0A, 0xAC, 0x72, 0x21, 0xD4, 0xF8, 0x07,
])

# Header field offsets
OFF_LOGO = 0x04
OFF_TITLE = 0xA0
OFF_GAME_CODE = 0xAC
OFF_MAKER_CODE = 0xB0
OFF_FIXED = 0xB2
OFF_DEVICE_TYPE = 0xB4
OFF_GAME_VERSION = 0xBC
OFF_COMPLEMENT = 0xBD
OFF_CHECKSUM = 0xBE

SHT_PROGBITS = 1


def strtoul(s):
    # strtoul(s, NULL, 0)
    s = s.strip()
    neg = False
    if s[:1] in "+-":
        neg = s[0] == "-"
        s = s[1:]
    if s[:2].lower() == "0x":
        base, s = 16, s[2:]
    elif s[:1] == "0":
        base = 8
    else:
        base = 10
    digits = "0123456789abcdef"[:base]
    num = 0
    for ch in s.lower():
        if ch not in digits:
            break
        num = num * base + digits.index(ch)
    if neg:
        num = -num
    return num & 0xFFFFFFFFFFFFFFFF


def print_usage():
    print("GBA ROM fixer v" + VER + " by Dark Fader / BlackThunder / WinterMute / Sierraffinity ")
    print("Syntax: gbafix <rom.gba> [-p] [-t[title]] [-c<game_code>] [-m<maker_code>] [-r<version>] [-d<debug>] [--silent]")
    print()
    print("parameters:")
    print("    -p              Pad to next exact power of 2. No minimum size!")
    print("    -t[<title>]     Patch title. Stripped filename if none given.")
    print("    -c<game_code>   Patch game code (four characters)")
    print("    -m<maker_code>  Patch maker code (two characters)")
    print("    -r<version>     Patch game version (number)")
    print("    -d<debug>       Enable debugging handler and set debug entry point (0 or 1)")
    print("    --silent           Silence non-error output")


def main(argv):
    if len(argv) <= 1:
        print_usage()
        return 255

    argfile = None
    silent = False
    schedule_pad = False

    for arg in argv[1:]:
        if not arg.startswith("-"):
            argfile = arg
        if arg.startswith("--silen"):
            silent = True

    if argfile is None:
        sys.stderr.write("Filename needed!\n")
        return 255

    try:
        infile = open(argfile, "r+b")
    except OSError:
        sys.stderr.write("Error opening input file!\n")
        return 255

    with infile:
        sh_offset = 0
        header = bytearray(infile.read(HEADER_SIZE).ljust(HEADER_SIZE, b"\0"))

        # elf check
        if header[:4] == b"\x7fELF":
            e_entry = struct.unpack_from("<I", header, 0x18)[0]
            e_shoff = struct.unpack_from("<I", header, 0x20)[0]
            e_shnum = struct.unpack_from("<H", header, 0x30)[0]
            infile.seek(e_shoff)
            found = None
            for _ in range(e_shnum):
                sec = infile.read(40)
                _, sh_type, _, sh_addr, sh_offset_ = struct.unpack_from("<IIIII", sec, 0)
                if sh_type == SHT_PROGBITS and sh_addr == e_entry:
                    found = sh_offset_
                    break
            if found is None:
                sys.stderr.write("Error finding entry point!\n")
                return 1
            sh_offset = found
            infile.seek(sh_offset)
            header = bytearray(infile.read(HEADER_SIZE).ljust(HEADER_SIZE, b"\0"))

        # fix some data
        header[OFF_LOGO:OFF_LOGO + len(GOOD_LOGO)] = GOOD_LOGO
        header[OFF_FIXED] = 0x96
        header[OFF_DEVICE_TYPE] = 0x00

        for arg in argv[1:]:
            if not arg.startswith("-"):
                continue
            opt = arg[1:2]
            value = arg[2:]
            vbytes = value.encode("latin-1") + b"\0\0\0\0"
            if opt == "p":
                schedule_pad = True
            elif opt == "t":
                if value:
                    title = value.encode("latin-1")[:12]
                else:
                    s = argfile
                    begin = s
                    t = s.rfind("\\")
                    if t >= 0:
                        begin = s[t + 1:]
                    t = s.rfind("/")
                    if t >= 0:
                        begin = s[t + 1:]
                    t = begin.rfind(".")
                    if t >= 0:
                        begin = begin[:t]
                    title = begin.encode("latin-1")[:12]
                    if not silent:
                        print(begin)
                header[OFF_TITLE:OFF_TITLE + 12] = title.ljust(12, b"\0")
            elif opt == "c":
                header[OFF_GAME_CODE:OFF_GAME_CODE + 4] = vbytes[:4]
            elif opt == "m":
                header[OFF_MAKER_CODE:OFF_MAKER_CODE + 2] = vbytes[:2]
            elif opt == "v":
                pass
            elif opt == "r":
                if not value:
                    sys.stderr.write("Need value for %s\n" % arg)
                    continue
                header[OFF_GAME_VERSION] = strtoul(value) & 0xFF
            elif opt == "d":
                if not value:
                    sys.stderr.write("Need value for %s\n" % arg)
                    continue
                header[0x9C] = 0xA5
                header[OFF_DEVICE_TYPE] = ((strtoul(value) & 1) << 7) & 0xFF
            elif opt == "-":
                pass
            else:
                print("Invalid option: %s" % arg)

        # update complement check & total checksum
        header[OFF_COMPLEMENT] = 0
        header[OFF_CHECKSUM:OFF_CHECKSUM + 2] = b"\0\0"
        c = sum(header[0xA0:0xBD]) & 0xFF
        header[OFF_COMPLEMENT] = (-(0x19 + c)) & 0xFF

        if schedule_pad:
            if sh_offset != 0:
                sys.stderr.write("Warning: Cannot safely pad an ELF\n")
            else:
                infile.seek(0, 2)
                size = infile.tell()
                if size > 0 and size & (size - 1):
                    target = 1 << size.bit_length()
                    infile.write(b"\xff" * (target - size))

        infile.seek(sh_offset)
        infile.write(header)

    if not silent:
        print("ROM fixed!")
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
