#!/usr/bin/env python3
# Python port of scaninc (scaninc.cpp, source_file.cpp, c_file.cpp, asm_file.cpp)
# Copyright(c) 2015-2017 YamaArashi (MIT license, see LICENSE)
#
# Scans C and assembly files for #include, .include, .incbin and INCBIN_*
# directives and emits make dependency rules.

import os
import signal
import sys

SCANINC_MAX_PATH = 255
USAGE = "Usage: scaninc [-I INCLUDE_PATH] [-M DEPENDENCY_OUT_PATH] FILE_PATH\n"


def fatal_error(msg):
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.exit(1)


def read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        fatal_error('Failed to open "%s" for reading.\n' % path)


def can_open_file(path):
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


class CFile:
    INCBIN_IDENTS = (b"INCBIN_S8", b"INCBIN_U8", b"INCBIN_S16", b"INCBIN_U16", b"INCBIN_S32", b"INCBIN_U32")

    def __init__(self, path):
        self.path = path
        data = read_file(path)
        self.size = len(data)
        self.buf = data + b"\0" * 8
        self.pos = 0
        self.line_num = 1
        self.incbins = set()
        self.includes = set()

    def error(self, msg):
        fatal_error("%s:%d %s" % (self.path, self.line_num, msg))

    def find_incbins(self):
        b = self.buf
        string_char = 0
        while self.pos < self.size:
            if string_char:
                c = b[self.pos]
                if c == string_char:
                    self.pos += 1
                    string_char = 0
                elif c == 0x5C and b[self.pos + 1] == string_char:
                    self.pos += 2
                else:
                    if c == 0x0A:
                        self.line_num += 1
                    self.pos += 1
            else:
                self.skip_whitespace()
                self.check_include()
                self.check_incbin()
                if self.pos >= self.size:
                    break
                c = b[self.pos]
                self.pos += 1
                if c == 0x0A:
                    self.line_num += 1
                elif c == 0x22 or c == 0x27:
                    string_char = c
                elif c == 0:
                    self.error("unexpected null character")

    def consume_horizontal_whitespace(self):
        if self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1
            return True
        return False

    def consume_newline(self):
        b = self.buf
        if b[self.pos] == 0x0A:
            self.pos += 1
            self.line_num += 1
            return True
        if b[self.pos] == 0x0D and b[self.pos + 1] == 0x0A:
            self.pos += 2
            self.line_num += 1
            return True
        return False

    def consume_comment(self):
        b = self.buf
        if b[self.pos] == 0x2F and b[self.pos + 1] == 0x2A:
            self.pos += 2
            while b[self.pos] != 0x2A or b[self.pos + 1] != 0x2F:
                if b[self.pos] == 0:
                    return False
                if not self.consume_newline():
                    self.pos += 1
            self.pos += 2
            return True
        if b[self.pos] == 0x2F and b[self.pos + 1] == 0x2F:
            self.pos += 2
            while not self.consume_newline():
                if b[self.pos] == 0:
                    return False
                self.pos += 1
            return True
        return False

    def skip_whitespace(self):
        while self.consume_horizontal_whitespace() or self.consume_newline() or self.consume_comment():
            pass

    def check_identifier(self, ident):
        end = min(self.pos + len(ident), self.size)
        return self.buf[self.pos:end] == ident

    def check_include(self):
        if self.buf[self.pos] != 0x23:
            return
        ident = b"#include"
        if not self.check_identifier(ident):
            return
        self.pos += len(ident)
        self.consume_horizontal_whitespace()
        path = self.read_path()
        if path:
            self.includes.add(path)

    def check_incbin(self):
        if self.buf[self.pos:self.pos + 7] != b"INCBIN_":
            return
        ident = None
        for candidate in self.INCBIN_IDENTS:
            if self.check_identifier(candidate):
                ident = candidate
                break
        if ident is None:
            return
        old_pos = self.pos
        old_line_num = self.line_num
        self.pos += len(ident)
        self.skip_whitespace()
        if self.buf[self.pos] != 0x28:
            self.pos = old_pos
            self.line_num = old_line_num
            return
        self.pos += 1
        while True:
            self.skip_whitespace()
            path = self.read_path()
            self.skip_whitespace()
            self.incbins.add(path)
            if self.buf[self.pos] != 0x2C:
                break
            self.pos += 1
        if self.buf[self.pos] != 0x29:
            self.error("expected ')'")
        self.pos += 1

    def read_path(self):
        b = self.buf
        if b[self.pos] != 0x22:
            if b[self.pos] == 0x3C:
                return ""
            self.error("expected '\"' or '<'")
        self.pos += 1
        start = self.pos
        while b[self.pos] != 0x22:
            c = b[self.pos]
            if c == 0:
                if self.pos >= self.size:
                    self.error("unexpected EOF in path string")
                else:
                    self.error("unexpected null character in path string")
            if c == 0x0D or c == 0x0A:
                self.error("unexpected end of line character in path string")
            if c == 0x5C:
                self.error("unexpected escape in path string")
            self.pos += 1
        self.pos += 1
        return b[start:self.pos - 1].decode("utf-8", "surrogateescape")


INC_NONE = 0
INC_INCLUDE = 1
INC_INCBIN = 2


class AsmFile:
    def __init__(self, path):
        self.path = path
        self.buf = read_file(path)
        self.size = len(self.buf)
        self.pos = 0
        self.line_num = 1

    def error(self, msg):
        fatal_error("%s:%d %s" % (self.path, self.line_num, msg))

    def get_char(self):
        if self.pos >= self.size:
            return -1
        c = self.buf[self.pos]
        self.pos += 1
        if c == 0x0D:
            if self.pos < self.size:
                nxt = self.buf[self.pos]
                self.pos += 1
                if nxt == 0x0A:
                    self.line_num += 1
                    return 0x0A
            self.error("CR line endings are not supported\n")
        if c == 0x0A:
            self.line_num += 1
        # The C++ version stores bytes in a signed char, so 0xFF reads as EOF.
        if c == 0xFF:
            return -1
        return c

    def peek_char(self):
        if self.pos >= self.size:
            return -1
        return self.buf[self.pos]

    def skip_tabs_and_spaces(self):
        while self.pos < self.size and self.buf[self.pos] in (0x09, 0x20):
            self.pos += 1

    def match_inc_directive(self, name):
        n = name.encode()
        if self.buf[self.pos:self.pos + len(n)] != n:
            return None
        self.pos += len(n)
        self.skip_tabs_and_spaces()
        if self.get_char() != 0x22:
            self.error('no path after ".%s" directive\n' % name)
        return self.read_path()

    def read_until_inc_directive(self):
        while True:
            self.skip_tabs_and_spaces()
            directive = INC_NONE
            path = None
            c = self.peek_char()
            if c == 0x2E or c == 0x23:
                self.pos += 1
                path = self.match_inc_directive("incbin")
                if path is not None:
                    directive = INC_INCBIN
                else:
                    path = self.match_inc_directive("include")
                    if path is not None:
                        directive = INC_INCLUDE
            while True:
                c = self.get_char()
                if c == -1:
                    return directive, path
                if c == 0x3B:
                    self.skip_end_of_line_comment()
                    break
                elif c == 0x2F and self.peek_char() == 0x2A:
                    self.pos += 1
                    self.skip_multi_line_comment()
                elif c == 0x22:
                    self.skip_string()
                elif c == 0x0A:
                    break
            if directive != INC_NONE:
                return directive, path

    def read_path(self):
        length = 0
        start = self.pos
        while True:
            c = self.get_char()
            if c == 0x22:
                break
            if c == -1:
                self.error("unexpected EOF in include string\n")
            if c == 0:
                self.error("unexpected NUL character in include string\n")
            if c == 0x0A:
                self.error("unexpected end of line character in include string\n")
            if c == 0x5C:
                self.error("unexpected escape in include string\n")
            length += 1
            if length > SCANINC_MAX_PATH:
                self.error("path is too long")
        return self.buf[start:start + length].decode("utf-8", "surrogateescape")

    def skip_end_of_line_comment(self):
        while True:
            c = self.get_char()
            if c == -1 or c == 0x0A:
                return

    def skip_multi_line_comment(self):
        while True:
            c = self.get_char()
            if c == 0x2A:
                if self.peek_char() == 0x2F:
                    self.pos += 1
                    return
            elif c == -1:
                return

    def skip_string(self):
        while True:
            c = self.get_char()
            if c == 0x22:
                break
            if c == -1:
                self.error("unexpected EOF in string\n")
            if c == 0x5C:
                self.get_char()


TYPE_C, TYPE_HEADER, TYPE_ASM, TYPE_INC = range(4)


def get_file_type(path):
    pos = path.rfind(".")
    if pos < 0:
        fatal_error('no file extension in path "%s"\n' % path)
    ext = path[pos + 1:]
    if ext == "c":
        return TYPE_C
    if ext == "s":
        return TYPE_ASM
    if ext == "h":
        return TYPE_HEADER
    if ext == "inc":
        return TYPE_INC
    fatal_error('Unrecognized extension "%s"\n' % ext)


def get_dir(path):
    slash = path.rfind("/")
    return path[:slash + 1] if slash >= 0 else ""


class SourceFile:
    def __init__(self, path):
        self.file_type = get_file_type(path)
        self.src_dir = get_dir(path)
        if self.file_type in (TYPE_C, TYPE_HEADER):
            f = CFile(path)
            f.find_incbins()
            self.incbins = f.incbins
            self.includes = f.includes
        else:
            f = AsmFile(path)
            self.incbins = set()
            self.includes = set()
            while True:
                kind, p = f.read_until_inc_directive()
                if kind == INC_NONE:
                    break
                if kind == INC_INCLUDE:
                    self.includes.add(p)
                else:
                    self.incbins.add(p)


def sort_key(s):
    # std::set<std::string> orders by raw bytes
    return s.encode("utf-8", "surrogateescape")


def main(argv):
    args = argv[1:]
    include_dirs = []
    make_format = False
    make_outfile = ""

    while len(args) > 1:
        arg = args[0]
        if arg[:2] == "-I":
            include_dir = arg[2:]
            if not include_dir:
                args = args[1:]
                include_dir = args[0]
            if include_dir and not include_dir.endswith("/"):
                include_dir += "/"
            include_dirs.append(include_dir)
        elif arg[:2] == "-M":
            make_format = True
            args = args[1:]
            make_outfile = args[0]
        else:
            fatal_error(USAGE)
        args = args[1:]

    if len(args) != 1:
        fatal_error(USAGE)

    files_to_process = [args[0]]
    dependencies = set()
    dependencies_includes = set()

    exists_cache = {}

    def cached_can_open(p):
        r = exists_cache.get(p)
        if r is None:
            r = exists_cache[p] = can_open_file(p)
        return r

    while files_to_process:
        file_path = files_to_process.pop(0)
        f = SourceFile(file_path)
        include_dirs.append(f.src_dir)
        dependencies.update(f.incbins)
        for include in sorted(f.includes, key=sort_key):
            exists = False
            path = ""
            for include_dir in include_dirs:
                path = include_dir + include
                if cached_can_open(path):
                    exists = True
                    break
            if not exists and f.file_type in (TYPE_ASM, TYPE_INC):
                path = include
                if cached_can_open(path):
                    exists = True
            if not exists:
                continue
            dependencies_includes.add(path)
            if path not in dependencies:
                dependencies.add(path)
                files_to_process.append(path)
        include_dirs.pop()

    deps_sorted = sorted(dependencies, key=sort_key)

    if not make_format:
        sys.stdout.write("".join(p + "\n" for p in deps_sorted))
        sys.stdout.write("\n")
    else:
        ext_pos = make_outfile.rfind(".")
        object_file = make_outfile[:ext_pos + 1] + "o"
        out = [object_file + ":"]
        out.extend(" " + p for p in deps_sorted)
        out.append("\n")
        out.append(make_outfile + ":")
        out.extend(" " + p for p in sorted(dependencies_includes, key=sort_key))
        out.append("\n")
        out.extend(p + ":\n" for p in deps_sorted)
        with open(make_outfile, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.write("".join(out))
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
