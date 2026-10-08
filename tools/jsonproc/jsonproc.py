#!/usr/bin/env python3
# Python port of jsonproc (jsonproc.cpp)
#
# Converts JSON data to an output file using an Inja template.
# https://github.com/pantor/inja
#
# Usage: jsonproc.py <json-filepath> <template-filepath> <output-filepath>

import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inja  # noqa: E402
from inja import get_int, get_string, json_empty  # noqa: E402

custom_vars = {}


def fatal_error(msg):
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.exit(1)


def _isalnum_byte(b):
    return 48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122


def _to_bytes(s):
    return s.encode("utf-8", "surrogateescape")


def _from_bytes(b):
    return b.decode("utf-8", "surrogateescape")


def make_environment(json_filepath, template_filepath):
    env = inja.Environment()
    env.set_trim_blocks(True)

    # Add custom command callbacks.
    env.add_callback("doNotModifyHeader", 0, lambda args: (
        "//\n// DO NOT MODIFY THIS FILE! It is auto-generated from " + json_filepath
        + " and Inja template " + template_filepath + "\n//\n"))

    def contains(args):
        return get_string(args[1]) in get_string(args[0])

    def subtract(args):
        return inja._to_int32(get_int(args[0]) - get_int(args[1]))

    def set_var(args):
        custom_vars[get_string(args[0])] = get_string(args[1])
        return ""

    def set_var_int(args):
        custom_vars[get_string(args[0])] = str(get_int(args[1]))
        return ""

    def get_var(args):
        return custom_vars.setdefault(get_string(args[0]), "")

    def concat(args):
        return get_string(args[0]) + get_string(args[1])

    def remove_prefix(args):
        raw_value = _to_bytes(get_string(args[0]))
        prefix = _to_bytes(get_string(args[1]))
        if raw_value.find(prefix) != 0:
            return _from_bytes(raw_value)
        return _from_bytes(raw_value[len(prefix):])

    def remove_suffix(args):
        raw_value = _to_bytes(get_string(args[0]))
        suffix = _to_bytes(get_string(args[1]))
        i = raw_value.rfind(suffix)
        if i < 0:
            return _from_bytes(raw_value)
        return _from_bytes(raw_value[:i])

    def is_empty(args):
        # single argument is a json object
        return json_empty(args[0])

    def is_empty_string(args):
        return get_string(args[0]) == ""

    def clean_string(args):
        # Works on bytes like the C++ version, so each byte of a non-ASCII
        # UTF-8 character becomes an underscore.
        s = bytearray(_to_bytes(get_string(args[0])))
        for i in range(len(s)):
            if (i == 0 and 48 <= s[i] <= 57) or not _isalnum_byte(s[i]):
                s[i] = 0x5F
        return _from_bytes(bytes(s))

    env.add_callback("contains", 2, contains)
    env.add_callback("subtract", 2, subtract)
    env.add_callback("setVar", 2, set_var)
    env.add_callback("setVarInt", 2, set_var_int)
    env.add_callback("getVar", 1, get_var)
    env.add_callback("concat", 2, concat)
    env.add_callback("removePrefix", 2, remove_prefix)
    env.add_callback("removeSuffix", 2, remove_suffix)
    env.add_callback("isEmpty", 1, is_empty)
    env.add_callback("isEmptyString", 1, is_empty_string)
    env.add_callback("cleanString", 1, clean_string)
    return env


def main(argv):
    if len(argv) != 4:
        fatal_error("USAGE: jsonproc <json-filepath> <template-filepath> <output-filepath>\n")

    json_filepath = argv[1]
    template_filepath = argv[2]
    output_filepath = argv[3]

    custom_vars.clear()
    env = make_environment(json_filepath, template_filepath)

    try:
        env.write_with_json_file(template_filepath, json_filepath, output_filepath)
    except (inja.InjaError, inja.JsonError, ValueError, OverflowError, OSError,
            ZeroDivisionError, KeyError, IndexError, TypeError) as e:
        fatal_error("JSONPROC_ERROR: %s\n" % e)
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
