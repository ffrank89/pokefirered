#!/usr/bin/env python3
# Python port of ramscrgen (main.cpp, sym_file.cpp, elf.cpp)
# Copyright(c) 2016 YamaArashi (MIT license, see LICENSE)
#
# Generates linker script fragments from .txt symbol files.

import signal
import struct
import sys

K_MAX_PATH = 256
ULONG_MAX = 0xFFFFFFFFFFFFFFFF


def fatal_error(msg):
    sys.stderr.write(msg)
    sys.exit(1)


def is_ascii_alpha(c):
    return (65 <= c <= 90) or (97 <= c <= 122)


def is_ascii_digit(c):
    return 48 <= c <= 57


def is_identifier_starting_char(c):
    return is_ascii_alpha(c) or c == 0x5F


def is_identifier_char(c):
    return is_ascii_alpha(c) or is_ascii_digit(c) or c == 0x5F


def is_ascii_printable(c):
    return 0x20 <= c <= 0x7E


def convert_digit(c, radix):
    if 48 <= c <= 57:
        digit = c - 48
    elif 65 <= c <= 70:
        digit = 10 + c - 65
    elif 97 <= c <= 102:
        digit = 10 + c - 97
    else:
        return -1
    return digit if digit < radix else -1


# ---------------------------------------------------------------------------
# elf.cpp
# ---------------------------------------------------------------------------

def get_common_symbols(source_path, path):
    if path[0] == "*":
        fatal_error('error: library common syms are unsupported (filename: "%s")\n' % path)

    elf_path = source_path + "/" + path
    try:
        with open(elf_path, "rb") as f:
            data = f.read()
    except OSError:
        fatal_error('error: failed to open "%s" for reading\n' % path)

    def u8(off):
        if off >= len(data):
            fatal_error('error: unexpected EOF when reading ELF file "%s"\n' % elf_path)
        return data[off]

    def u16(off):
        if off + 2 > len(data):
            fatal_error('error: unexpected EOF when reading ELF file "%s"\n' % elf_path)
        return struct.unpack_from("<H", data, off)[0]

    def u32(off):
        if off + 4 > len(data):
            fatal_error('error: unexpected EOF when reading ELF file "%s"\n' % elf_path)
        return struct.unpack_from("<I", data, off)[0]

    def read_string(off):
        end = data.find(b"\0", off)
        if end < 0:
            fatal_error('error: unexpected EOF when reading ELF file "%s"\n' % elf_path)
        return data[off:end].decode("latin-1")

    if len(data) < 4:
        fatal_error('error: failed to read ELF magic from "%s"\n' % elf_path)
    if data[:4] != b"\x7fELF":
        fatal_error('error: ELF magic did not match in "%s"\n' % elf_path)
    if u8(4) != 1:
        fatal_error('error: "%s" not 32-bit ELF\n' % elf_path)
    if u8(5) != 1:
        fatal_error('error: "%s" not little-endian ELF\n' % elf_path)

    sh_off = u32(0x20)
    sh_entsize = u16(0x2E)
    sh_count = u16(0x30)
    shstrtab_index = u16(0x32)

    symtab_offset = 0
    strtab_offset = 0
    symbol_count = 0
    pseudo_common_index = 0

    shstrtab_offset = u32(sh_off + sh_entsize * shstrtab_index + 0x10)

    for i in range(sh_count):
        hdr = sh_off + sh_entsize * i
        name = read_string(shstrtab_offset + u32(hdr))
        if name == ".symtab":
            if symtab_offset:
                fatal_error('error: mutiple .symtab sections found in "%s"\n' % elf_path)
            symtab_offset = u32(hdr + 0x10)
            symbol_count = u32(hdr + 0x14) // 16
        elif name == ".strtab":
            if strtab_offset:
                fatal_error('error: mutiple .strtab sections found in "%s"\n' % elf_path)
            strtab_offset = u32(hdr + 0x10)
        elif name == "common_data":
            if pseudo_common_index:
                fatal_error('error: mutiple common_data sections found in "%s"\n' % elf_path)
            pseudo_common_index = i

    if not symtab_offset:
        fatal_error('error: couldn\'t find .symtab section in "%s"\n' % elf_path)
    if not strtab_offset:
        fatal_error('error: couldn\'t find .strtab section in "%s"\n' % elf_path)

    common_symbols = []
    if pseudo_common_index:
        syms = []
        for i in range(symbol_count):
            off = symtab_offset + i * 16
            name_offset = u32(off)
            size = u32(off + 8)
            section_index = u16(off + 14)
            if section_index == pseudo_common_index:
                syms.append((name_offset, size))
        for name_offset, size in syms:
            name = read_string(strtab_offset + name_offset)
            if name == "$d" or name == "":
                continue
            common_symbols.append((name, size))

    return common_symbols


# ---------------------------------------------------------------------------
# sym_file.cpp
# ---------------------------------------------------------------------------

DIRECTIVE_INCLUDE = 0
DIRECTIVE_SPACE = 1
DIRECTIVE_ALIGN = 2
DIRECTIVE_UNKNOWN = 3


class SymFile:
    def __init__(self, filename):
        self.filename = filename
        try:
            with open(filename, "rb") as f:
                raw = f.read()
        except OSError:
            fatal_error('Failed to open "%s" for reading.\n' % filename)
        self.size = len(raw)
        # Extra zero bytes act as the C string terminator and allow lookahead.
        self.buf = bytearray(raw) + b"\0\0"
        self.pos = 0
        self.line_num = 1
        self.line_start = 0
        self.in_lang_conditional = False
        self.remove_comments()

    def remove_comments(self):
        b = self.buf
        pos = 0
        string_char = 0
        while True:
            if b[pos] == 0:
                return
            if string_char != 0:
                if b[pos] == 0x5C and b[pos + 1] == string_char:
                    pos += 2
                else:
                    if b[pos] == string_char:
                        string_char = 0
                    pos += 1
            elif b[pos] == 0x40 and (pos == 0 or b[pos - 1] != 0x5C):
                while b[pos] != 0x0A and b[pos] != 0:
                    b[pos] = 0x20
                    pos += 1
            elif b[pos] == 0x2F and b[pos + 1] == 0x2A:
                b[pos] = 0x20
                b[pos + 1] = 0x20
                pos += 2
                comment_string_char = 0
                while True:
                    if b[pos] == 0:
                        return
                    if comment_string_char != 0:
                        if b[pos] == 0x5C and b[pos + 1] == comment_string_char:
                            b[pos] = 0x20
                            b[pos + 1] = 0x20
                            pos += 2
                        else:
                            if b[pos] == comment_string_char:
                                comment_string_char = 0
                            if b[pos] != 0x0A:
                                b[pos] = 0x20
                            pos += 1
                    else:
                        if b[pos] == 0x2A and b[pos + 1] == 0x2F:
                            b[pos] = 0x20
                            b[pos + 1] = 0x20
                            pos += 2
                            break
                        if b[pos] == 0x22 or b[pos] == 0x27:
                            comment_string_char = b[pos]
                        if b[pos] != 0x0A:
                            b[pos] = 0x20
                        pos += 1
            else:
                if b[pos] == 0x22 or b[pos] == 0x27:
                    string_char = b[pos]
                pos += 1

    def check_for_directive(self, name):
        name = name.encode()
        length = len(name)
        i = 0
        while i < length and self.pos + i < self.size:
            if name[i] != self.buf[self.pos + i]:
                return False
            i += 1
        if i < length:
            return False
        self.pos += length
        return True

    def get_directive(self):
        self.skip_whitespace()
        if self.check_for_directive(".include"):
            return DIRECTIVE_INCLUDE
        if self.check_for_directive(".space"):
            return DIRECTIVE_SPACE
        if self.check_for_directive(".align"):
            return DIRECTIVE_ALIGN
        return DIRECTIVE_UNKNOWN

    def get_label(self, require_colon=True):
        b = self.buf
        start = self.pos
        pos = self.pos
        if is_identifier_starting_char(b[pos]):
            pos += 1
            while is_identifier_char(b[pos]):
                pos += 1
        if require_colon:
            if b[pos] == 0x3A:
                if pos != start:
                    self.pos = pos + 1
            else:
                pos = start
        else:
            self.pos = pos
        return b[start:pos].decode("latin-1")

    def skip_whitespace(self):
        while self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1

    def read_path(self):
        self.skip_whitespace()
        b = self.buf
        if b[self.pos] != 0x22:
            self.raise_error("expected file path")
        self.pos += 1
        length = 0
        start = self.pos
        while b[self.pos] != 0x22:
            c = b[self.pos]
            self.pos += 1
            if c == 0:
                if self.pos >= self.size:
                    self.raise_error("unexpected EOF in include string")
                else:
                    self.raise_error("unexpected null character in include string")
            if not is_ascii_printable(c):
                self.raise_error("unexpected character '\\x%02X' in include string" % c)
            if c == 0x5C:
                self.raise_error("unexpected escape '\\%c' in include string" % chr(b[self.pos]))
            length += 1
            if length > K_MAX_PATH:
                self.raise_error("path is too long")
        self.pos += 1
        return b[start:start + length].decode("latin-1")

    def read_integer(self):
        self.skip_whitespace()
        b = self.buf
        if not is_ascii_digit(b[self.pos]):
            return None
        start = self.pos
        radix = 10
        if b[self.pos] == 0x30 and b[self.pos + 1] == 0x78:
            radix = 16
            self.pos += 2
        n = 0
        while True:
            digit = convert_digit(b[self.pos], radix)
            if digit == -1:
                break
            n = n * radix + digit
            if n > ULONG_MAX:
                self.pos += 1
                while convert_digit(b[self.pos], radix) != -1:
                    self.pos += 1
                self.raise_error("integer is too large (%s)" % b[start:self.pos].decode("latin-1"))
            self.pos += 1
        return n

    def expect_empty_rest_of_line(self):
        self.skip_whitespace()
        c = self.buf[self.pos]
        if c == 0:
            if self.pos >= self.size:
                self.raise_warning("file doesn't end with newline")
            else:
                self.raise_error("unexpected null character")
        elif c == 0x0A:
            self.pos += 1
            self.line_start = self.pos
            self.line_num += 1
        elif c == 0x0D:
            self.raise_error("only Unix-style LF newlines are supported")
        else:
            self.raise_error("junk at end of line")

    def skip_line(self):
        while self.buf[self.pos] != 0 and self.buf[self.pos] != 0x0A:
            self.pos += 1
        if self.buf[self.pos] == 0x0A:
            self.pos += 1

    def is_at_end(self):
        return self.pos >= self.size

    def handle_lang_conditional(self, lang):
        if self.buf[self.pos] != 0x23:
            return
        self.pos += 1
        if self.check_for_directive("begin"):
            if self.in_lang_conditional:
                self.raise_error("already inside language conditional")
            self.skip_whitespace()
            label = self.get_label(False)
            if len(label) == 0:
                self.raise_error("no language name after #begin")
            self.expect_empty_rest_of_line()
            if lang == label:
                self.in_lang_conditional = True
            else:
                while not self.is_at_end() and self.buf[self.pos] != 0x23:
                    self.skip_line()
                if self.buf[self.pos] != 0x23:
                    self.raise_error("unterminated language conditional")
                self.pos += 1
                if not self.check_for_directive("end"):
                    self.raise_error("expected #end")
                self.expect_empty_rest_of_line()
        elif self.check_for_directive("end"):
            if not self.in_lang_conditional:
                self.raise_error("not inside language conditional")
            self.in_lang_conditional = False
            self.expect_empty_rest_of_line()
        else:
            self.raise_error("unknown # directive")

    def _report(self, kind, msg):
        sys.stdout.flush()
        sys.stderr.write("%s:%d: %s: %s\n" % (self.filename, self.line_num, kind, msg))

    def raise_error(self, msg):
        self._report("error", msg)
        sys.exit(1)

    def raise_warning(self, msg):
        self._report("warning", msg)


# ---------------------------------------------------------------------------
# main.cpp
# ---------------------------------------------------------------------------

def handle_common_include(out, filename, source_path):
    for name, size in get_common_symbols(source_path, filename):
        alignment = 4
        if size > 4:
            alignment = 8
        if size > 8:
            alignment = 16
        out.append(". = ALIGN(%d);\n" % alignment)
        out.append("%s = .;\n" % name)
        out.append(". += 0x%X;\n" % size)


def convert_sym_file(filename, section_name, lang, common, source_path, lib_source_path):
    sym_file = SymFile(filename)
    out = []

    def flush():
        sys.stdout.write("".join(out))
        out.clear()

    try:
        while not sym_file.is_at_end():
            sym_file.handle_lang_conditional(lang)
            directive = sym_file.get_directive()
            if directive == DIRECTIVE_INCLUDE:
                inc_filename = sym_file.read_path()
                sym_file.expect_empty_rest_of_line()
                out.append(". = ALIGN(4);\n")
                if common:
                    handle_common_include(out, inc_filename,
                                          lib_source_path if inc_filename[0] == "*" else source_path)
                else:
                    out.append("%s(%s);\n" % (inc_filename, section_name))
            elif directive == DIRECTIVE_SPACE:
                length = sym_file.read_integer()
                if length is None:
                    sym_file.raise_error("expected integer after .space directive")
                sym_file.expect_empty_rest_of_line()
                out.append(". += 0x%X;\n" % length)
            elif directive == DIRECTIVE_ALIGN:
                amount = sym_file.read_integer()
                if amount is None:
                    sym_file.raise_error("expected integer after .align directive")
                if amount > 4:
                    sym_file.raise_error("max alignment amount is 4")
                amount = 1 << amount
                sym_file.expect_empty_rest_of_line()
                out.append(". = ALIGN(%d);\n" % amount)
            else:
                label = sym_file.get_label()
                if len(label) != 0:
                    out.append("%s = .;\n" % label)
                sym_file.expect_empty_rest_of_line()
    finally:
        flush()


def main(argv):
    if len(argv) < 4:
        sys.stderr.write("Usage: %s SECTION_NAME SYM_FILE LANG [-c SRC_PATH,COMMON_SYM_PATH]" % argv[0])
        return 1

    common = False
    section_name = argv[1]
    sym_file_name = argv[2]
    lang = argv[3]
    source_path = ""
    lib_source_path = ""

    if len(argv) > 4:
        if argv[4] != "-c":
            fatal_error('error: unrecognized argument "%s"\n' % argv[4])
        if len(argv) < 6:
            fatal_error('error: missing SRC_PATH,COMMON_SYM_PATH after "-c"\n')
        common = True
        paths = argv[5]
        comma = paths.find(",")
        if comma < 0:
            fatal_error('error: missing comma in argument after "-c"\n')
        source_path = paths[:comma]
        common_sym_path = paths[comma + 1:]
        comma = common_sym_path.find(",")
        if comma < 0:
            lib_source_path = "tools/agbcc/lib"
        else:
            lib_source_path = common_sym_path[comma + 1:]

    convert_sym_file(sym_file_name, section_name, lang, common, source_path, lib_source_path)
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
