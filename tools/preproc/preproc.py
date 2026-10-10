#!/usr/bin/env python3
# Python port of preproc (preproc.cpp, asm_file.cpp, c_file.cpp, charmap.cpp,
# string_parser.cpp, utf8.cpp, io.cpp)
# Copyright(c) 2016 YamaArashi (MIT license, see LICENSE)
#
# Converts string literals in C and assembly files into the game's character
# encoding using charmap.txt, and expands INCBIN_* macros in C files.

import re
import signal
import sys

K_MAX_PATH = 256
K_MAX_STRING_LENGTH = 1024
K_MAX_CHARMAP_SEQUENCE_LENGTH = 16

# include/characters.h
CHAR_SPACE = 0x00
CHAR_NEWLINE = 0xFE
EOS = 0xFF

BRAILLE_CHAR_SPACE = 0x00
BRAILLE_CHAR_NUMBER = 0x3A

_BRAILLE_LETTERS = {
    "A": 0x01, "B": 0x05, "C": 0x03, "D": 0x0B, "E": 0x09, "F": 0x07, "G": 0x0F,
    "H": 0x0D, "I": 0x06, "J": 0x0E, "K": 0x11, "L": 0x15, "M": 0x13, "N": 0x1B,
    "O": 0x19, "P": 0x17, "Q": 0x1F, "R": 0x1D, "S": 0x16, "T": 0x1E, "U": 0x31,
    "V": 0x35, "W": 0x2E, "X": 0x33, "Y": 0x3B, "Z": 0x39,
}
BRAILLE_ENCODING = {}
for _k, _v in _BRAILLE_LETTERS.items():
    BRAILLE_ENCODING[ord(_k)] = _v
    BRAILLE_ENCODING[ord(_k.lower())] = _v
for _digit, _letter in zip("1234567890", "ABCDEFGHIJ"):
    BRAILLE_ENCODING[ord(_digit)] = _BRAILLE_LETTERS[_letter]
BRAILLE_ENCODING.update({
    ord(" "): BRAILLE_CHAR_SPACE,
    ord(","): 0x04,
    ord("."): 0x2C,
    ord("?"): 0x34,
    ord("!"): 0x1C,
    ord(":"): 0x0C,
    ord(";"): 0x14,
    ord("-"): 0x30,
    ord("/"): 0x12,
    ord("("): 0x3C,
    ord(")"): 0x3C,
    ord("'"): 0x10,
    ord("#"): BRAILLE_CHAR_NUMBER,
    ord("$"): EOS,
})


# Output that has been produced but not yet written to stdout.
_pending_out = []


def flush_output():
    if _pending_out:
        sys.stdout.buffer.write(b"".join(_pending_out))
        _pending_out.clear()
    sys.stdout.flush()


def fatal_error(msg):
    flush_output()
    sys.stderr.write(msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# char_util.h
# ---------------------------------------------------------------------------

def is_ascii(c):
    return c < 128


def is_ascii_alpha(c):
    return (65 <= c <= 90) or (97 <= c <= 122)


def is_ascii_digit(c):
    return 48 <= c <= 57


def is_ascii_hex_digit(c):
    return (48 <= c <= 57) or (97 <= c <= 102) or (65 <= c <= 70)


def is_ascii_printable(c):
    return 0x20 <= c <= 0x7E


def is_identifier_starting_char(c):
    return is_ascii_alpha(c) or c == 0x5F


def is_identifier_char(c):
    return is_ascii_alpha(c) or is_ascii_digit(c) or c == 0x5F


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


def c_char(c):
    # printf("%c") of a byte
    return chr(c)


# ---------------------------------------------------------------------------
# utf8.cpp
# ---------------------------------------------------------------------------

_BYTE_TYPE_TABLE = (
    [0] * 128
    + [1] * 16 + [9] * 16
    + [7] * 32
    + [8, 8] + [2] * 30
    + [0xA, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3, 0x4, 0x3, 0x3]
    + [0xB, 0x6, 0x6, 0x6, 0x5, 0x8, 0x8, 0x8, 0x8, 0x8, 0x8, 0x8, 0x8, 0x8, 0x8, 0x8]
)

_S = [i * 12 for i in range(9)]
s0, s1, s2, s3, s4, s5, s6, s7, s8 = _S

_TRANSITION_TABLE = [
    s0, s1, s2, s3, s5, s8, s7, s1, s1, s1, s4, s6,
    s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1,
    s1, s0, s1, s1, s1, s1, s1, s0, s1, s0, s1, s1,
    s1, s2, s1, s1, s1, s1, s1, s2, s1, s2, s1, s1,
    s1, s1, s1, s1, s1, s1, s1, s2, s1, s1, s1, s1,
    s1, s2, s1, s1, s1, s1, s1, s1, s1, s2, s1, s1,
    s1, s1, s1, s1, s1, s1, s1, s3, s1, s3, s1, s1,
    s1, s3, s1, s1, s1, s1, s1, s3, s1, s3, s1, s1,
    s1, s3, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1,
]


def decode_utf8(buf, pos):
    """Returns (code, encoding_length). code is -1 on invalid encoding."""
    state = s0
    start = pos
    code = 0
    while True:
        byte = buf[pos] if pos < len(buf) else 0
        pos += 1
        t = _BYTE_TYPE_TABLE[byte]
        if state == s0:
            code = (0xFF >> t) & byte
        else:
            code = (code << 6) | (byte & 0x3F)
        state = _TRANSITION_TABLE[state + t]
        if state == s1:
            return -1, pos - start
        if state == s0:
            return code, pos - start


# ---------------------------------------------------------------------------
# io.cpp
# ---------------------------------------------------------------------------

def read_file_to_buffer(filename, is_stdin):
    if is_stdin:
        data = sys.stdin.buffer.read()
    else:
        try:
            with open(filename, "rb") as f:
                data = f.read()
        except OSError:
            fatal_error('Failed to open "%s" for reading.\n' % filename)
    return data


# ---------------------------------------------------------------------------
# charmap.cpp
# ---------------------------------------------------------------------------

LHS_CHAR, LHS_ESCAPE, LHS_CONSTANT, LHS_NONE = range(4)


class CharmapReader:
    def __init__(self, filename):
        self.filename = filename
        try:
            with open(filename, "rb") as f:
                raw = f.read()
        except OSError:
            fatal_error('Failed to open "%s" for reading.\n' % filename)
        self.size = len(raw)
        self.buf = bytearray(raw) + b"\0" * 8
        self.pos = 0
        self.line_num = 1
        self.remove_comments()

    def raise_error(self, msg):
        sys.stdout.flush()
        sys.stderr.write("%s:%d: error: %s\n" % (self.filename, self.line_num, msg))
        sys.exit(1)

    def remove_comments(self):
        b = self.buf
        pos = 0
        in_string = False
        while True:
            if b[pos] == 0:
                return
            if in_string:
                if b[pos] == 0x5C and b[pos + 1] == 0x27:
                    pos += 2
                else:
                    if b[pos] == 0x27:
                        in_string = False
                    pos += 1
            elif b[pos] == 0x40:
                while b[pos] != 0x0A and b[pos] != 0:
                    b[pos] = 0x20
                    pos += 1
            else:
                if b[pos] == 0x27:
                    in_string = True
                pos += 1

    def skip_whitespace(self):
        while self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1

    def read_lhs(self):
        b = self.buf
        while True:
            self.skip_whitespace()
            if b[self.pos] == 0x0A:
                self.pos += 1
                self.line_num += 1
            else:
                break

        c = b[self.pos]
        if c == 0x27:
            self.pos += 1
            is_escape = b[self.pos] == 0x5C
            if is_escape:
                self.pos += 1
            c = b[self.pos]
            if c == 0:
                if self.pos >= self.size:
                    self.raise_error("unexpected EOF in UTF-8 character literal")
                else:
                    self.raise_error("unexpected null character in UTF-8 character literal")
            if is_ascii(c) and not is_ascii_printable(c):
                self.raise_error("unexpected character U+%X in UTF-8 character literal" % c)
            code, enc_len = decode_utf8(b, self.pos)
            if code == -1:
                self.raise_error("invalid encoding in UTF-8 character literal")
            self.pos += enc_len
            if b[self.pos] != 0x27:
                self.raise_error("unterminated character literal")
            self.pos += 1
            if is_escape:
                if code >= 128:
                    self.raise_error("escapes using non-ASCII characters are invalid")
                if code == 0x27:
                    return LHS_CHAR, None, code
                if code == 0x5C or code == 0x22:
                    self.raise_error("cannot escape double quote")
                return LHS_ESCAPE, None, code
            if code == 0x27:
                self.raise_error("empty character literal")
            return LHS_CHAR, None, code
        if is_identifier_starting_char(c):
            start = self.pos
            while is_identifier_char(b[self.pos]):
                self.pos += 1
            return LHS_CONSTANT, bytes(b[start:self.pos]), 0
        if c == 0x0D:
            self.raise_error("only Unix-style LF newlines are supported")
        if c == 0:
            if self.pos < self.size:
                self.raise_error("unexpected null character")
            return LHS_NONE, None, 0
        self.raise_error("junk at start of line")

    def expect_equals_sign(self):
        self.skip_whitespace()
        if self.buf[self.pos] != 0x3D:
            self.raise_error("expected equals sign")
        self.pos += 1

    def read_sequence(self):
        b = self.buf
        self.skip_whitespace()
        start = self.pos
        length = 0
        while is_ascii_hex_digit(b[self.pos]) and is_ascii_hex_digit(b[self.pos + 1]):
            self.pos += 2
            length += 1
            if length > K_MAX_CHARMAP_SEQUENCE_LENGTH:
                self.raise_error("byte sequence too long (max is %d bytes)" % K_MAX_CHARMAP_SEQUENCE_LENGTH)
            self.skip_whitespace()
        if is_ascii_hex_digit(b[self.pos]):
            self.raise_error("each byte must have 2 hex digits")
        if length == 0:
            self.raise_error("expected byte sequence")
        self.pos = start
        seq = bytearray()
        for _ in range(length):
            seq.append(int(b[self.pos:self.pos + 2].decode(), 16))
            self.pos += 2
            self.skip_whitespace()
        return bytes(seq)

    def expect_empty_rest_of_line(self):
        self.skip_whitespace()
        c = self.buf[self.pos]
        if c == 0:
            if self.pos < self.size:
                self.raise_error("unexpected null character")
        elif c == 0x0A:
            self.pos += 1
            self.line_num += 1
        elif c == 0x0D:
            self.raise_error("only Unix-style LF newlines are supported")
        else:
            self.raise_error("junk at end of line")


class Charmap:
    def __init__(self, filename):
        self.chars = {}
        self.escapes = [b""] * 128
        self.constants = {}
        reader = CharmapReader(filename)
        while True:
            kind, name, code = reader.read_lhs()
            if kind == LHS_NONE:
                return
            reader.expect_equals_sign()
            sequence = reader.read_sequence()
            if kind == LHS_CHAR:
                if code in self.chars:
                    reader.raise_error("redefining char")
                self.chars[code] = sequence
            elif kind == LHS_ESCAPE:
                if self.escapes[code]:
                    reader.raise_error("redefining escape")
                self.escapes[code] = sequence
            else:
                if name in self.constants:
                    reader.raise_error("redefining constant")
                self.constants[name] = sequence
            reader.expect_empty_rest_of_line()

    def char(self, code):
        return self.chars.get(code, b"")

    def escape(self, code):
        return self.escapes[code]

    def constant(self, name):
        return self.constants.get(name, b"")


g_charmap = None


# ---------------------------------------------------------------------------
# string_parser.cpp
# ---------------------------------------------------------------------------

class StringParserError(Exception):
    pass


class StringParser:
    def __init__(self, buf, size):
        self.buf = buf
        self.size = size
        self.pos = 0

    def raise_error(self, msg):
        raise StringParserError(msg)

    def read_char_or_escape(self):
        b = self.buf
        is_escape = b[self.pos] == 0x5C
        if is_escape:
            self.pos += 1
            if b[self.pos] == 0x22:
                seq = g_charmap.char(0x22)
                if not seq:
                    self.raise_error("no mapping exists for double quote")
                return seq
            if b[self.pos] == 0x5C:
                seq = g_charmap.char(0x5C)
                if not seq:
                    self.raise_error("no mapping exists for backslash")
                return seq
        c = b[self.pos]
        if c == 0:
            if self.pos >= self.size:
                self.raise_error("unexpected EOF in UTF-8 string")
            else:
                self.raise_error("unexpected null character in UTF-8 string")
        if is_ascii(c) and not is_ascii_printable(c):
            self.raise_error("unexpected character U+%X in UTF-8 string" % c)
        code, enc_len = decode_utf8(b, self.pos)
        self.pos += enc_len
        if code == -1:
            self.raise_error("invalid encoding in UTF-8 string")
        if is_escape and code >= 128:
            self.raise_error("escapes using non-ASCII characters are invalid")
        seq = g_charmap.escape(code) if is_escape else g_charmap.char(code)
        if not seq:
            if is_escape:
                self.raise_error("unknown escape '\\%s'" % chr(code))
            else:
                self.raise_error("unknown character U+%X" % code)
        return seq

    def read_bracketed_constants(self):
        b = self.buf
        total = bytearray()
        self.pos += 1
        while b[self.pos] != 0x7D:
            self.skip_whitespace()
            c = b[self.pos]
            if is_identifier_starting_char(c):
                start = self.pos
                self.pos += 1
                while is_identifier_char(b[self.pos]):
                    self.pos += 1
                name = bytes(b[start:self.pos])
                seq = g_charmap.constant(name)
                if not seq:
                    self.raise_error("unknown constant '%s'" % name.decode("latin-1"))
                total += seq
            elif is_ascii_digit(c):
                value, size = self.read_integer()
                total += (value & 0xFFFFFFFF).to_bytes(4, "little")[:size]
            elif c == 0:
                if self.pos >= self.size:
                    self.raise_error("unexpected EOF after left curly bracket")
                else:
                    self.raise_error("unexpected null character within curly brackets")
            else:
                if is_ascii_printable(c):
                    self.raise_error("unexpected character '%s' within curly brackets" % chr(c))
                else:
                    self.raise_error("unexpected character '\\x%02X' within curly brackets" % c)
        self.pos += 1
        return bytes(total)

    def parse_string(self, src_pos, dest):
        """Appends the converted string to dest. Returns the number of source bytes consumed."""
        b = self.buf
        self.pos = src_pos
        if b[self.pos] != 0x22:
            self.raise_error("expected UTF-8 string literal")
        start = self.pos
        self.pos += 1
        while b[self.pos] != 0x22:
            if b[self.pos] == 0x7B:
                seq = self.read_bracketed_constants()
            else:
                seq = self.read_char_or_escape()
            for byte in seq:
                if len(dest) == K_MAX_STRING_LENGTH:
                    self.raise_error("mapped string longer than %d bytes" % K_MAX_STRING_LENGTH)
                dest.append(byte)
        self.pos += 1
        return self.pos - start

    def skip_rest_of_integer(self, radix):
        while convert_digit(self.buf[self.pos], radix) != -1:
            self.pos += 1

    def read_decimal(self):
        b = self.buf
        n = 0
        start = self.pos
        while True:
            digit = convert_digit(b[self.pos], 10)
            if digit == -1:
                break
            n = n * 10 + digit
            if n >= 0xFFFFFFFF:
                self.skip_rest_of_integer(10)
                self.raise_error('integer literal "%s" is too large' % b[start:self.pos].decode("latin-1"))
            self.pos += 1
        if b[self.pos] == 0x48:  # 'H'
            if n >= 0x10000:
                self.raise_error("%d is too large to be a halfword" % n)
            size = 2
            self.pos += 1
        elif b[self.pos] == 0x57:  # 'W'
            size = 4
            self.pos += 1
        else:
            if n >= 0x10000:
                size = 4
            elif n >= 0x100:
                size = 2
            else:
                size = 1
        return n, size

    def read_hex(self):
        b = self.buf
        n = 0
        start = self.pos
        while True:
            digit = convert_digit(b[self.pos], 16)
            if digit == -1:
                break
            n = n * 16 + digit
            if n >= 0xFFFFFFFF:
                self.skip_rest_of_integer(16)
                self.raise_error('integer literal "%s" is too large' % b[start:self.pos].decode("latin-1"))
            self.pos += 1
        length = self.pos - start
        size = {2: 1, 4: 2, 8: 4}.get(length)
        if size is None:
            self.raise_error('hex integer literal "0x%s" doesn\'t have length of 2, 4, or 8 digits'
                             % b[start:self.pos].decode("latin-1"))
        return n, size

    def read_integer(self):
        b = self.buf
        if not is_ascii_digit(b[self.pos]):
            self.raise_error("expected integer")
        if b[self.pos] == 0x30 and b[self.pos + 1] == 0x78:
            self.pos += 2
            return self.read_hex()
        return self.read_decimal()

    def skip_whitespace(self):
        while self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1


# ---------------------------------------------------------------------------
# asm_file.cpp
# ---------------------------------------------------------------------------

DIR_INCLUDE, DIR_STRING, DIR_BRAILLE, DIR_ENUM, DIR_UNKNOWN = range(5)


class AsmFile:
    def __init__(self, filename, is_stdin, do_enum, out):
        self.filename = filename
        raw = read_file_to_buffer(filename, is_stdin)
        self.size = len(raw)
        self.buf = bytearray(raw) + b"\0" * 8
        self.do_enum = do_enum
        self.out = out
        self.pos = 0
        self.line_num = 1
        self.line_start = 0
        self.remove_comments()

    def remove_comments(self):
        b = self.buf
        pos = 0
        string_char = 0
        # Fast path: jump between interesting characters.
        interesting = re.compile(rb'[\x00@/"\']')
        while True:
            if string_char != 0:
                c = b[pos]
                if c == 0:
                    return
                if c == 0x5C and b[pos + 1] == string_char:
                    pos += 2
                else:
                    if c == string_char:
                        string_char = 0
                    pos += 1
                continue
            m = interesting.search(b, pos)
            pos = m.start()
            c = b[pos]
            if c == 0:
                return
            if c == 0x40 and (pos == 0 or b[pos - 1] != 0x5C):
                end = pos
                while b[end] != 0x0A and b[end] != 0:
                    end += 1
                b[pos:end] = b" " * (end - pos)
                pos = end
            elif c == 0x2F and b[pos + 1] == 0x2A:
                b[pos] = 0x20
                b[pos + 1] = 0x20
                pos += 2
                while True:
                    if b[pos] == 0:
                        return
                    if b[pos] == 0x2A and b[pos + 1] == 0x2F:
                        b[pos] = 0x20
                        b[pos + 1] = 0x20
                        pos += 2
                        break
                    if b[pos] != 0x0A:
                        b[pos] = 0x20
                    pos += 1
            else:
                if c == 0x22 or c == 0x27:
                    string_char = c
                pos += 1

    def report(self, kind, msg):
        flush_output()
        sys.stderr.write("%s:%d: %s: %s\n" % (self.filename, self.line_num, kind, msg))

    def raise_error(self, msg):
        self.report("error", msg)
        sys.exit(1)

    def raise_warning(self, msg):
        self.report("warning", msg)

    def check_for_directive(self, name):
        n = len(name)
        if self.pos + n > self.size:
            return False
        if self.buf[self.pos:self.pos + n] != name:
            return False
        self.pos += n
        return True

    def get_directive(self):
        self.skip_whitespace()
        if self.check_for_directive(b".include"):
            return DIR_INCLUDE
        if self.check_for_directive(b".string"):
            return DIR_STRING
        if self.check_for_directive(b".braille"):
            return DIR_BRAILLE
        if self.check_for_directive(b"enum"):
            return DIR_ENUM
        return DIR_UNKNOWN

    def get_global_label(self):
        b = self.buf
        start = self.pos
        pos = self.pos
        if is_identifier_starting_char(b[pos]):
            pos += 1
            while is_identifier_char(b[pos]):
                pos += 1
        if b[pos] == 0x3A and b[pos + 1] == 0x3A:
            self.pos = pos + 2
            self.expect_empty_rest_of_line()
            return bytes(b[start:pos])
        return b""

    def skip_whitespace(self):
        while self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1

    def read_path(self):
        b = self.buf
        self.skip_whitespace()
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
                self.raise_error("unexpected escape '\\%s' in include string" % chr(b[self.pos]))
            length += 1
            if length > K_MAX_PATH:
                self.raise_error("path is too long")
        self.pos += 1
        self.expect_empty_rest_of_line()
        return b[start:start + length].decode("latin-1")

    def read_string(self):
        self.skip_whitespace()
        s = bytearray()
        parser = StringParser(self.buf, self.size)
        try:
            self.pos += parser.parse_string(self.pos, s)
        except StringParserError as e:
            self.raise_error(str(e))
        self.skip_whitespace()
        if self.consume_comma():
            self.skip_whitespace()
            pad_length = self.read_pad_length()
            while len(s) < pad_length:
                s.append(CHAR_SPACE)
        self.expect_empty_rest_of_line()
        return s

    def verify_string_length(self, length):
        if length == K_MAX_STRING_LENGTH:
            self.raise_error("mapped string longer than %d bytes" % K_MAX_STRING_LENGTH)

    def read_braille(self):
        b = self.buf
        self.skip_whitespace()
        s = bytearray()
        if b[self.pos] != 0x22:
            self.raise_error("expected braille string literal")
        self.pos += 1
        in_number = False
        while b[self.pos] != 0x22:
            if b[self.pos] == 0x5C and b[self.pos + 1] == 0x6E:
                self.verify_string_length(len(s))
                s.append(CHAR_NEWLINE)
                self.pos += 2
            else:
                c = b[self.pos]
                if c not in BRAILLE_ENCODING:
                    if is_ascii_printable(c):
                        self.raise_error("character '%s' not valid in braille string" % chr(c))
                    else:
                        # printf("%02X") of a signed char
                        self.raise_error("character '\\x%02X' not valid in braille string"
                                         % (c if c < 0x80 else (c - 256) & 0xFFFFFFFF))
                if not in_number and 48 <= c <= 57:
                    in_number = True
                    self.verify_string_length(len(s))
                    s.append(BRAILLE_CHAR_NUMBER)
                elif in_number and BRAILLE_ENCODING[c] == BRAILLE_CHAR_SPACE:
                    in_number = False
                self.verify_string_length(len(s))
                s.append(BRAILLE_ENCODING[c])
                self.pos += 1
        self.pos += 1
        self.expect_empty_rest_of_line()
        return s

    def consume_comma(self):
        if self.buf[self.pos] == 0x2C:
            self.pos += 1
            return True
        return False

    def read_pad_length(self):
        b = self.buf
        if not is_ascii_digit(b[self.pos]):
            self.raise_error("expected integer")
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
            if n > K_MAX_STRING_LENGTH:
                self.raise_error("pad length greater than maximum length (%d)" % K_MAX_STRING_LENGTH)
            self.pos += 1
        return n

    def output_line(self):
        b = self.buf
        end = b.find(b"\n", self.pos, self.size)
        nul = b.find(b"\0", self.pos)
        if end < 0 or (0 <= nul < end):
            self.pos = nul
            if self.pos >= self.size:
                self.raise_warning("file doesn't end with newline")
                line = b[self.line_start:self.pos]
                self.out.append(bytes(line) + b"\n")
            else:
                self.raise_error("unexpected null character")
        else:
            self.pos = end
            self.out.append(bytes(b[self.line_start:end + 1]))
            self.pos += 1
            self.line_start = self.pos
            self.line_num += 1

    def parse_enum(self):
        if not self.do_enum:
            return False
        b = self.buf
        out = self.out
        fallback = self.pos
        current_header_line = self.skip_whitespace_and_eol()
        self.read_identifier()
        current_header_line += self.skip_whitespace_and_eol()
        enum_base = b"0"
        enum_counter = 0
        symbol_count = 0

        if b[self.pos] != 0x7B:
            self.pos = fallback - 4
            return False

        header_filename, delta = self.find_last_line_number()
        current_header_line += delta
        self.pos += 1
        while True:
            current_header_line += self.skip_whitespace_and_eol()
            ident = self.read_identifier()
            if ident:
                out.append(b'# %d "%s"\n' % (current_header_line, header_filename))
                current_header_line += self.skip_whitespace_and_eol()
                if b[self.pos] == 0x3D:
                    self.pos += 1
                    self.skip_whitespace()
                    base = bytearray()
                    while True:
                        if self.pos == self.size:
                            self.raise_error("unexpected EOF")
                        if b[self.pos] == 0x2C:
                            break
                        if b[self.pos] == 0x0A:
                            current_header_line += 1
                            base.append(0x20)
                        else:
                            base.append(b[self.pos])
                        self.pos += 1
                    enum_base = bytes(base)
                    enum_counter = 0
                out.append(b".equiv %s, (%s) + %d\n" % (ident, enum_base, enum_counter))
                enum_counter += 1
                symbol_count += 1
            elif symbol_count == 0:
                self.raise_error("%s:%d: empty enum is invalid"
                                 % (header_filename.decode("latin-1"), current_header_line))

            if b[self.pos] != 0x2C:
                current_header_line += self.skip_whitespace_and_eol()
                c1 = b[self.pos]
                self.pos += 1
                ok = False
                if c1 == 0x7D:
                    c2 = b[self.pos]
                    self.pos += 1
                    ok = c2 == 0x3B
                if ok:
                    self.expect_empty_rest_of_line()
                    break
                self.raise_error("unterminated enum from included file %s:%d"
                                 % (header_filename.decode("latin-1"), current_header_line))
            self.pos += 1
        return True

    def expect_empty_rest_of_line(self):
        self.skip_whitespace()
        b = self.buf
        c = b[self.pos]
        if c == 0:
            if self.pos >= self.size:
                self.raise_warning("file doesn't end with newline")
            else:
                self.raise_error("unexpected null character")
        elif c == 0x0A:
            self.pos += 1
            self.line_start = self.pos
            self.line_num += 1
        elif c == 0x0D and b[self.pos + 1] == 0x0A:
            self.pos += 2
            self.line_start = self.pos
            self.line_num += 1
        else:
            self.raise_error("junk at end of line")

    def is_at_end(self):
        return self.pos >= self.size

    def output_location(self):
        self.out.append(b'# %d "%s"\n' % (self.line_num, self.filename.encode("utf-8", "surrogateescape")))

    def skip_whitespace_and_eol(self):
        newlines = 0
        b = self.buf
        while b[self.pos] in (0x09, 0x20, 0x0A):
            if b[self.pos] == 0x0A:
                newlines += 1
            self.pos += 1
        return newlines

    def find_last_line_number(self):
        b = self.buf
        pos = self.pos
        linebreaks = 0
        while pos >= 0 and b[pos] != 0x23:
            if b[pos] == 0x0A:
                linebreaks += 1
            pos -= 1
        if pos < 0:
            self.raise_error("line indicator for header file not found before `enum`")
        pos += 1
        while b[pos] in (0x20, 0x09):
            pos += 1
        if not is_ascii_digit(b[pos]):
            self.raise_error("malformatted line indicator found before `enum`, expected line number")
        n = 0
        while True:
            digit = convert_digit(b[pos], 10)
            pos += 1
            if digit == -1:
                break
            n = (10 * n + digit) & 0xFFFFFFFF
        while b[pos] in (0x20, 0x09):
            pos += 1
        c = b[pos]
        pos += 1
        if c != 0x22:
            self.raise_error("malformatted line indicator found before `enum`, expected filename")
        filename = bytearray()
        while b[pos] != 0x22:
            c = b[pos]
            pos += 1
            if c == 0:
                if pos >= self.size:
                    self.raise_error("unexpected EOF in line indicator")
                else:
                    self.raise_error("unexpected null character in line indicator")
            if not is_ascii_printable(c):
                self.raise_error("unexpected character '\\x%02X' in line indicator" % c)
            if c == 0x5C:
                self.raise_error("unexpected escape '\\%s' in line indicator" % chr(b[pos]))
            filename.append(c)
        return bytes(filename), n + linebreaks - 1

    def read_identifier(self):
        b = self.buf
        start = self.pos
        if not is_identifier_starting_char(b[self.pos]):
            return b""
        self.pos += 1
        while is_identifier_char(b[self.pos]):
            self.pos += 1
        return bytes(b[start:self.pos])


def print_asm_bytes(out, s):
    if len(s) > 0:
        out.append(b"\t.byte " + b", ".join(b"0x%02X" % c for c in s) + b"\n")


def preproc_asm_file(filename, is_stdin, do_enum):
    out = _pending_out
    stack = [AsmFile(filename, is_stdin, do_enum, out)]
    out.append(b'# 1 "%s"\n' % filename.encode("utf-8", "surrogateescape"))

    while True:
        while stack[-1].is_at_end():
            stack.pop()
            if not stack:
                flush_output()
                return
            stack[-1].output_location()

        top = stack[-1]
        directive = top.get_directive()

        if directive == DIR_INCLUDE:
            path = top.read_path()
            stack.append(AsmFile(path, False, do_enum, out))
            stack[-1].output_location()
        elif directive == DIR_STRING:
            print_asm_bytes(out, top.read_string())
        elif directive == DIR_BRAILLE:
            print_asm_bytes(out, top.read_braille())
        elif directive == DIR_ENUM:
            if not top.parse_enum():
                top.output_line()
        else:
            label = top.get_global_label()
            if label:
                out.append(b"%s: ; .global %s\n" % (label, label))
            else:
                top.output_line()


# ---------------------------------------------------------------------------
# c_file.cpp
# ---------------------------------------------------------------------------

_INCBIN_IDENTS = (b"INCBIN_S8", b"INCBIN_U8", b"INCBIN_S16", b"INCBIN_U16", b"INCBIN_S32", b"INCBIN_U32")
_C_INTERESTING = re.compile(rb"[_I\"']")


class CFile:
    def __init__(self, filename, is_stdin):
        if is_stdin:
            self.filename = "<stdin>/" + filename
        else:
            self.filename = filename
        raw = read_file_to_buffer(filename, is_stdin)
        self.size = len(raw)
        self.buf = raw + b"\0" * 8
        self.pos = 0
        self.line_num = 1
        self.out = _pending_out

    def report(self, kind, msg):
        flush_output()
        sys.stderr.write("%s:%d: %s: %s\n" % (self.filename, self.line_num, kind, msg))

    def raise_error(self, msg):
        self.report("error", msg)
        sys.exit(1)

    def preproc(self):
        b = self.buf
        size = self.size
        out = self.out
        string_char = 0
        while self.pos < size:
            if string_char:
                # Copy string contents up to the next quote or backslash.
                pos = self.pos
                q = b.find(string_char, pos, size)
                bs = b.find(b"\\", pos, size)
                if q < 0 and bs < 0:
                    end = size
                elif q < 0:
                    end = bs
                elif bs < 0:
                    end = q
                else:
                    end = min(q, bs)
                if end > pos:
                    chunk = b[pos:end]
                    self.line_num += chunk.count(b"\n")
                    out.append(chunk)
                    self.pos = end
                    continue
                c = b[pos]
                if c == string_char[0]:
                    out.append(string_char)
                    self.pos += 1
                    string_char = 0
                elif c == 0x5C and b[pos + 1] == string_char[0]:
                    out.append(b"\\" + string_char)
                    self.pos += 2
                else:
                    out.append(b[pos:pos + 1])
                    self.pos += 1
            else:
                # Copy plain text up to the next character that could start
                # a string conversion, an incbin, or a literal.
                pos = self.pos
                m = _C_INTERESTING.search(b, pos, size)
                end = m.start() if m else size
                if end > pos:
                    chunk = b[pos:end]
                    self.line_num += chunk.count(b"\n")
                    out.append(chunk)
                    self.pos = end
                    if self.pos >= size:
                        break
                self.try_convert_string()
                self.try_convert_incbin()
                if self.pos >= size:
                    break
                c = b[self.pos]
                self.pos += 1
                out.append(bytes((c,)))
                if c == 0x0A:
                    self.line_num += 1
                elif c == 0x22:
                    string_char = b'"'
                elif c == 0x27:
                    string_char = b"'"
        flush_output()

    def consume_horizontal_whitespace(self):
        if self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1
            return True
        return False

    def consume_newline(self):
        b = self.buf
        if b[self.pos] == 0x0D and b[self.pos + 1] == 0x0A:
            self.pos += 2
            self.line_num += 1
            self.out.append(b"\n")
            return True
        if b[self.pos] == 0x0A:
            self.pos += 1
            self.line_num += 1
            self.out.append(b"\n")
            return True
        return False

    def skip_whitespace(self):
        while self.consume_horizontal_whitespace() or self.consume_newline():
            pass

    def try_convert_string(self):
        b = self.buf
        old_pos = self.pos
        old_line_num = self.line_num
        no_terminator = False

        if b[self.pos] != 0x5F or (self.pos > 0 and is_identifier_char(b[self.pos - 1])):
            return

        self.pos += 1
        if b[self.pos] == 0x5F:
            no_terminator = True
            self.pos += 1

        self.skip_whitespace()

        if b[self.pos] != 0x28:
            self.pos = old_pos
            self.line_num = old_line_num
            return

        self.pos += 1
        self.skip_whitespace()
        self.out.append(b"{ ")

        while True:
            self.skip_whitespace()
            c = b[self.pos]
            if c == 0x22:
                s = bytearray()
                parser = StringParser(b, self.size)
                try:
                    self.pos += parser.parse_string(self.pos, s)
                except StringParserError as e:
                    self.raise_error(str(e))
                self.out.append(b"".join(b"0x%02X, " % x for x in s))
            elif c == 0x29:
                self.pos += 1
                break
            else:
                if self.pos >= self.size:
                    self.raise_error("unexpected EOF")
                if is_ascii_printable(c):
                    self.raise_error("unexpected character '%s'" % chr(c))
                else:
                    self.raise_error("unexpected character '\\x%02X'"
                                     % (c if c < 0x80 else (c - 256) & 0xFFFFFFFF))

        self.out.append(b" }" if no_terminator else b"0xFF }")

    def check_identifier(self, ident):
        end = min(self.pos + len(ident), self.size)
        return self.buf[self.pos:end] == ident

    def read_whole_file(self, path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            self.raise_error('Failed to open "%s" for reading.\n' % path)

    def try_convert_incbin(self):
        b = self.buf
        if b[self.pos] != 0x49:
            return
        incbin_type = -1
        for i, ident in enumerate(_INCBIN_IDENTS):
            if self.check_identifier(ident):
                incbin_type = i
                break
        if incbin_type == -1:
            return

        size = 1 << (incbin_type // 2)
        is_signed = (incbin_type % 2) == 0

        old_pos = self.pos
        old_line_num = self.line_num
        self.pos += len(_INCBIN_IDENTS[incbin_type])
        self.skip_whitespace()

        if b[self.pos] != 0x28:
            self.pos = old_pos
            self.line_num = old_line_num
            return

        self.pos += 1
        self.out.append(b"{")

        while True:
            self.skip_whitespace()
            if b[self.pos] != 0x22:
                self.raise_error("expected double quote")
            self.pos += 1
            start = self.pos
            while b[self.pos] != 0x22:
                c = b[self.pos]
                if c == 0:
                    if self.pos >= self.size:
                        self.raise_error("unexpected EOF in path string")
                    else:
                        self.raise_error("unexpected null character in path string")
                if c == 0x0D or c == 0x0A:
                    self.raise_error("unexpected end of line character in path string")
                if c == 0x5C:
                    self.raise_error("unexpected escape in path string")
                self.pos += 1
            path = b[start:self.pos].decode("utf-8", "surrogateescape")
            self.pos += 1

            data = self.read_whole_file(path)
            file_size = len(data)
            if file_size % size != 0:
                self.raise_error("Size %d doesn't evenly divide file size %d.\n" % (size, file_size))

            # ExtractData returns the unsigned value for 8 and 16 bit data,
            # even for the signed INCBIN variants.
            if size == 1:
                values = data
            elif size == 2:
                values = memoryview(data).cast("H") if sys.byteorder == "little" else \
                    [data[i] | (data[i + 1] << 8) for i in range(0, file_size, 2)]
            else:
                values = [int.from_bytes(data[i:i + 4], "little") for i in range(0, file_size, 4)]

            if is_signed:
                if size == 4:
                    self.out.append(b"".join(
                        b"%d," % (v - (1 << 32) if v & 0x80000000 else v) for v in values))
                else:
                    self.out.append(b"".join(b"%d," % v for v in values))
            else:
                self.out.append(b"".join(b"%uu," % v for v in values))

            self.skip_whitespace()
            if b[self.pos] != 0x2C:
                break
            self.pos += 1

        if b[self.pos] != 0x29:
            self.raise_error("expected ')'")
        self.pos += 1
        self.out.append(b"}")


def preproc_c_file(filename, is_stdin):
    CFile(filename, is_stdin).preproc()


# ---------------------------------------------------------------------------
# preproc.cpp
# ---------------------------------------------------------------------------

def get_file_extension(filename):
    idx = filename.rfind(".")
    if idx <= 0:
        return None
    ext = filename[idx + 1:]
    if not ext:
        return None
    return ext


def usage_and_exit(program):
    sys.stderr.write("Usage: %s [-i] [-e] SRC_FILE CHARMAP_FILE\nwhere -i denotes if input is from stdin\n"
                     "      -e enables enum handling\n" % program)
    sys.exit(1)


def main(argv):
    global g_charmap

    is_stdin = False
    do_enum = False

    # getopt(argc, argv, "ie") with GNU-style argument permutation
    positional = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            positional.extend(argv[i + 1:])
            break
        if arg.startswith("-") and len(arg) > 1:
            for ch in arg[1:]:
                if ch == "i":
                    is_stdin = True
                elif ch == "e":
                    do_enum = True
                else:
                    sys.stderr.write("%s: invalid option -- '%s'\n" % (argv[0], ch))
                    usage_and_exit(argv[0])
        else:
            positional.append(arg)
        i += 1

    if len(positional) != 2:
        usage_and_exit(argv[0])

    source, charmap = positional
    g_charmap = Charmap(charmap)

    extension = get_file_extension(source)
    if extension is None:
        fatal_error('"%s" has no file extension.\n' % argv[1])

    if extension == "s":
        preproc_asm_file(source, is_stdin, do_enum)
    elif extension in ("c", "i"):
        if do_enum:
            fatal_error("-e is invalid for C sources\n")
        preproc_c_file(source, is_stdin)
    else:
        fatal_error('"%s" has an unknown file extension of "%s".\n' % (argv[1], extension))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
