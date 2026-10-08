#!/usr/bin/env python3
# Python port of wav2agb (wav2agb.cpp, converter.cpp, wav_file.cpp)
# Copyright (c) 2019 ipatix (see README.md for the list of local modifications)
#
# Converts .wav files to GBA compatible .s or .bin files for use with the
# mp2k/m4a sound driver.
#
# Usage: wav2agb.py [options] <input.wav> [<output>]

import math
import os
import signal
import struct
import sys

WAV_INVALID_VAL = 0xFFFFFFFF
UINT32_MAX = 0xFFFFFFFF
INT_MAX = 0x7FFFFFFF


class WavError(Exception):
    """Equivalent of the std::exceptions thrown by the C++ version."""


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if hi < v else v)


def quantize(sample):
    """clamp(static_cast<int>(floor(ds * 128.0)), -128, 127)"""
    return _clamp(int(math.floor(sample * 128.0)), -128, 127)


# ---------------------------------------------------------------------------
# wav_file.cpp
# ---------------------------------------------------------------------------

FMT_U8, FMT_S16, FMT_S24, FMT_S32, FMT_F32, FMT_F64 = range(6)
_FMT_SIZES = {FMT_U8: 1, FMT_S16: 2, FMT_S24: 3, FMT_S32: 4, FMT_F32: 4, FMT_F64: 8}


class WavChunk:
    def __init__(self, chunk_id, data):
        self.id = chunk_id
        self.data = bytearray(data)


def _arr_u16(arr, pos):
    if pos + 2 > len(arr):
        raise WavError("vector")
    return arr[pos] | (arr[pos + 1] << 8)


def _arr_u32(arr, pos):
    if pos + 4 > len(arr):
        raise WavError("vector")
    return arr[pos] | (arr[pos + 1] << 8) | (arr[pos + 2] << 16) | (arr[pos + 3] << 24)


class WavFile:
    def __init__(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise WavError("failed to open file: %s, reason: %s" % (path, e.strerror))

        self.loop_start = 0
        self.loop_end = UINT32_MAX
        self.loop_enabled = False
        self.tuning = 0.0
        self.midi_key = 60
        self.sample_rate = 0
        self.num_samples = 0
        self.agb_pitch = 0
        self.agb_loop_end = 0
        self.chunks = []
        self.fmt = None
        self.samples = []

        length = len(data)

        def read(pos, n):
            if pos + n > length:
                raise WavError("basic_ios::clear: unspecified iostream_category error")
            return data[pos:pos + n]

        if read(0, 4) != b"RIFF":
            raise WavError("RIFF ID invalid")
        main_chunk_len = struct.unpack("<I", read(4, 4))[0]
        if ((main_chunk_len + 8) & 0xFFFFFFFF) != length:
            raise WavError("RIFF chunk len (=%d) doesn't match file len (=%d)" % (main_chunk_len, length))
        if read(8, 4) != b"WAVE":
            raise WavError("WAVE ID invalid")

        data_chunk_found = False
        fmt_chunk_found = False
        data_chunk_pos = 0
        data_chunk_end_pos = 0

        cur_pos = 12
        while cur_pos + 8 <= length:
            chunk_id = read(cur_pos, 4)
            chunk_len = struct.unpack("<I", read(cur_pos + 4, 4))[0]
            if cur_pos + 8 + chunk_len > length:
                raise WavError("ERROR: chunk goes beyond end of file: offset=%d" % cur_pos)

            chunk_data = read(cur_pos + 8, chunk_len)
            self.chunks.append(WavChunk(chunk_id, chunk_data))

            if chunk_id == b"fmt ":
                fmt_chunk_found = True
                fmt_tag = _arr_u16(chunk_data, 0)
                num_channels = _arr_u16(chunk_data, 2)
                if num_channels != 1:
                    raise WavError("ERROR: input file is NOT mono")
                self.sample_rate = _arr_u32(chunk_data, 4)
                block_align = _arr_u16(chunk_data, 12)
                bits_per_sample = _arr_u16(chunk_data, 14)
                if fmt_tag == 1:
                    combos = {(1, 8): FMT_U8, (2, 16): FMT_S16, (3, 24): FMT_S24, (4, 32): FMT_S32}
                    if (block_align, bits_per_sample) not in combos:
                        raise WavError("ERROR: unsupported integer format combination")
                    self.fmt = combos[(block_align, bits_per_sample)]
                elif fmt_tag == 3:
                    combos = {(4, 32): FMT_F32, (8, 64): FMT_F64}
                    if (block_align, bits_per_sample) not in combos:
                        raise WavError("ERROR: unsupported float format combination")
                    self.fmt = combos[(block_align, bits_per_sample)]
                else:
                    raise WavError("ERROR: unsupported format code: %d" % fmt_tag)
            elif chunk_id == b"data":
                data_chunk_found = True
                data_chunk_pos = cur_pos + 8
                data_chunk_end_pos = data_chunk_pos + chunk_len
            elif chunk_id == b"smpl":
                midi_unity_note = _arr_u32(chunk_data, 12)
                self.midi_key = min(midi_unity_note, 127)
                midi_pitch_fraction = _arr_u32(chunk_data, 16)
                # the values below convert the uint32_t range to 0.0 to 100.0 range
                self.tuning = float(midi_pitch_fraction) / (4294967296.0 * 100.0)
                num_loops = _arr_u32(chunk_data, 28)
                if num_loops > 1:
                    raise WavError("ERROR: too many loops in smpl chunk")
                if num_loops == 1:
                    loop_type = _arr_u32(chunk_data, 36 + 4)
                    if loop_type != 0:
                        raise WavError("ERROR: loop type not supported: %d" % loop_type)
                    self.loop_start = _arr_u32(chunk_data, 36 + 8)
                    # sampler chunks tell the last sample to be played (so including
                    # rather than excluding), thus +1
                    self.loop_end = (_arr_u32(chunk_data, 36 + 12) + 1) & 0xFFFFFFFF
                    self.loop_enabled = True
            elif chunk_id == b"agbp":
                # Custom chunk: exact GBA pitch value (sample_rate * 1024)
                if chunk_len >= 4:
                    self.agb_pitch = _arr_u32(chunk_data, 0)
            elif chunk_id == b"agbl":
                # Custom chunk: exact loop end override (handles off-by-one from original game)
                if chunk_len >= 4:
                    self.agb_loop_end = _arr_u32(chunk_data, 0)

            cur_pos += 8 + chunk_len
            # If chunk size is odd, skip the pad byte
            if chunk_len % 2 == 1:
                cur_pos += 1

        if not fmt_chunk_found:
            raise WavError("ERROR: fmt chunk not found")
        if not data_chunk_found:
            raise WavError("ERROR: data chunk not found")

        size = _FMT_SIZES[self.fmt]
        self.num_samples = ((data_chunk_end_pos - data_chunk_pos) & 0xFFFFFFFF) // size
        self.loop_end = min(self.loop_end, self.num_samples)
        self.samples = self._decode(data[data_chunk_pos:data_chunk_pos + self.num_samples * size])

    def _decode(self, raw):
        n = self.num_samples
        fmt = self.fmt
        if fmt == FMT_U8:
            return [(float(b) - 128.0) / 128.0 for b in raw]
        if fmt == FMT_S16:
            return [float(v) / 32768.0 for v in struct.unpack("<%dh" % n, raw)]
        if fmt == FMT_S24:
            out = []
            for i in range(n):
                v = raw[3 * i] | (raw[3 * i + 1] << 8) | (raw[3 * i + 2] << 16)
                if v & 0x800000:
                    v -= 0x1000000
                out.append(float(v) / 8388608.0)
            return out
        if fmt == FMT_S32:
            return [float(v) / 2147483648.0 for v in struct.unpack("<%di" % n, raw)]
        if fmt == FMT_F32:
            return list(struct.unpack("<%df" % n, raw))
        return list(struct.unpack("<%dd" % n, raw))

    def read_data(self, location, length):
        out = self.samples[location:location + length]
        if len(out) < length:
            out = out + [0.0] * (length - len(out))
        return out


def write_wav_with_agbl_chunk(output_path, chunks, loop_end_value):
    value = (loop_end_value & 0xFFFFFFFF).to_bytes(4, "little")
    has_agbl = False
    for chunk in chunks:
        if chunk.id == b"agbl":
            has_agbl = True
            chunk.data = bytearray(value)
            break

    if not has_agbl:
        for i, chunk in enumerate(chunks):
            if chunk.id == b"data":
                chunks.insert(i, WavChunk(b"agbl", value))
                break

    total_chunk_size = 0
    for chunk in chunks:
        total_chunk_size += 8 + len(chunk.data)
        if len(chunk.data) % 2 == 1:
            total_chunk_size += 1
    riff_size = (4 + total_chunk_size) & 0xFFFFFFFF

    out = bytearray(b"RIFF" + riff_size.to_bytes(4, "little") + b"WAVE")
    for chunk in chunks:
        out += chunk.id[:4]
        out += (len(chunk.data) & 0xFFFFFFFF).to_bytes(4, "little")
        out += chunk.data
        if len(chunk.data) % 2 == 1:
            out.append(0)

    try:
        with open(output_path, "wb") as f:
            f.write(out)
    except OSError:
        raise WavError("Failed to open output file: " + output_path)


# ---------------------------------------------------------------------------
# converter.cpp
# ---------------------------------------------------------------------------

DPCM_BLK_SIZE = 0x40
DPCM_LOOKUP_TABLE = [0, 1, 4, 9, 16, 25, 36, 49, -64, -49, -36, -25, -16, -9, -4, -1]
DPCM_INDEX_TABLE = list(range(16))


def _build_fast_lookup_table():
    # (candidate indices, run length), indexed by (sample - previous level + 255)
    runs = [
        ([8], 192),
        ([8, 9], 14),
        ([9, 10], 13),
        ([10, 11], 11),
        ([11, 12], 9),
        ([12, 13], 7),
        ([13, 14], 5),
        ([14, 15], 3),
        ([0, 15], 1),
        ([0, 1, 15], 1),
        ([1, 0], 1),
        ([1, 2], 2),
        ([2, 1], 1),
        ([2, 3], 4),
        ([3, 2], 1),
        ([3, 4], 6),
        ([4, 3], 1),
        ([4, 5], 8),
        ([5, 4], 1),
        ([5, 6], 10),
        ([6, 5], 1),
        ([6, 7], 12),
        ([7], 207),
    ]
    table = []
    for candidates, count in runs:
        table.extend([candidates] * count)
    assert len(table) == 511
    return table


DPCM_FAST_LOOKUP_TABLE = _build_fast_lookup_table()


class Settings:
    def __init__(self):
        self.loop_start = None
        self.loop_end = None
        self.tune = None
        self.key = None
        self.rate = None
        self.dpcm_verbose = False
        self.dpcm_lookahead_fast = False
        self.dpcm_include_padding = True
        self.dpcm_enc_lookahead = 3


def _dpcm_lookahead(levels, offset, lookahead, prev_level, fast):
    """Returns (minimumError, minimumErrorIndex). levels holds the quantized samples."""
    if lookahead == 0:
        return 0, None

    minimum_error = INT_MAX
    minimum_error_index = len(DPCM_LOOKUP_TABLE)
    s = levels[offset]
    candidates = DPCM_FAST_LOOKUP_TABLE[s - prev_level + 255] if fast else DPCM_INDEX_TABLE

    for i in candidates:
        new_level = prev_level + DPCM_LOOKUP_TABLE[i]
        error_estimation = (s - new_level) * (s - new_level)
        if error_estimation >= minimum_error:
            continue
        rec_minimum_error, _ = _dpcm_lookahead(levels, offset + 1, lookahead - 1, new_level, fast)
        error = error_estimation + rec_minimum_error
        if error < minimum_error and -128 <= new_level <= 127:
            minimum_error = error
            minimum_error_index = i

    return minimum_error, minimum_error_index


def _calculate_snr(uncompressed, decompressed):
    sum_son = 0
    sum_mum = 0
    for u, d in zip(uncompressed, decompressed):
        s = quantize(u) + 128
        sum_son += s * s
        sub = d + 128 - s
        sum_mum += sub * sub
    sum_son = ((sum_son + 0x80000000) & 0xFFFFFFFF) - 0x80000000
    sum_mum = ((sum_mum + 0x80000000) & 0xFFFFFFFF) - 0x80000000
    if sum_mum == 0:
        return 100.0
    return 10 * math.log10(float(sum_son) / sum_mum)


def _convert_dpcm_impl(wf, settings, write_initial_sample, write_compressed_data):
    import time
    start_time = time.perf_counter()
    uncompressed_data = []
    decompressed_data = []
    lookahead_setting = settings.dpcm_enc_lookahead
    fast = settings.dpcm_lookahead_fast

    for i in range(0, wf.loop_end, DPCM_BLK_SIZE):
        samples_in_block = min(DPCM_BLK_SIZE, wf.loop_end - i)
        ds = wf.read_data(i, samples_in_block) + [0.0] * (DPCM_BLK_SIZE - samples_in_block)
        if settings.dpcm_verbose:
            uncompressed_data.extend(ds)
        levels = [quantize(x) for x in ds]

        s = levels[0]
        write_initial_sample(s)
        if settings.dpcm_verbose:
            decompressed_data.append(s)

        inner = 1
        samples_to_process = DPCM_BLK_SIZE if settings.dpcm_include_padding else samples_in_block
        out_data = 0
        first = True

        while True:
            if not first:
                if inner >= samples_to_process:
                    break
                read_len = min(lookahead_setting, DPCM_BLK_SIZE - inner)
                _, index = _dpcm_lookahead(levels, inner, read_len, s, fast)
                out_data = (index & 0xF) << 4
                s += DPCM_LOOKUP_TABLE[index]
                if settings.dpcm_verbose:
                    decompressed_data.append(s)
                inner += 1
            first = False
            # initial_loop_enter:
            if inner >= samples_to_process:
                break
            read_len = min(lookahead_setting, DPCM_BLK_SIZE - inner)
            _, index = _dpcm_lookahead(levels, inner, read_len, s, fast)
            out_data |= index & 0xF
            s += DPCM_LOOKUP_TABLE[index]
            inner += 1
            if settings.dpcm_verbose:
                decompressed_data.append(s)
            write_compressed_data(out_data)
            if not inner < DPCM_BLK_SIZE:
                break

    if settings.dpcm_verbose:
        duration = time.perf_counter() - start_time
        sys.stdout.write("SNR: %.2fdB, run time: %.2fs\n"
                         % (_calculate_snr(uncompressed_data, decompressed_data), duration))


class AsmWriter:
    def __init__(self):
        self.parts = []
        self.block_pos = 0

    def out(self, text):
        # vsnprintf into a 256 byte buffer
        self.parts.append(text.encode("latin-1", "replace")[:255].decode("latin-1"))

    def data_write(self, data, hexadecimal):
        if self.block_pos == 0:
            self.out(("\n    .byte   0x%02X" % (data & 0xFFFFFFFF)) if hexadecimal else ("\n    .byte   %4d" % data))
        else:
            self.out((", 0x%02X" % (data & 0xFFFFFFFF)) if hexadecimal else (", %4d" % data))
        self.block_pos = (self.block_pos + 1) % 16


def convert(wav_file_str, out_file_str, sym, compress, binary, settings):
    wf = WavFile(wav_file_str)

    # check command line overrides
    if settings.loop_start is not None:
        wf.loop_start = min(settings.loop_start, wf.loop_end)
        wf.loop_enabled = True
    if settings.loop_end is not None:
        wf.loop_end = min(settings.loop_end, wf.loop_end)
    if settings.tune is not None:
        wf.tuning = settings.tune
    if settings.key is not None:
        wf.midi_key = settings.key
    if settings.rate is not None:
        wf.sample_rate = settings.rate

    fmt = 1 if compress else 0

    if wf.midi_key == 60 and wf.tuning == 0.0:
        pitch = float(wf.sample_rate)
    else:
        pitch = wf.sample_rate * math.pow(2.0, (60.0 - wf.midi_key) / 12.0 + wf.tuning / 1200.0)

    if wf.agb_pitch != 0:
        pitch_value = wf.agb_pitch
    else:
        pitch_value = int(pitch * 1024.0) & 0xFFFFFFFF

    loop_end = wf.loop_end
    if wf.agb_loop_end != 0:
        loop_end = wf.agb_loop_end

    if binary:
        bin_data = bytearray()
        flags = fmt
        if wf.loop_enabled:
            flags |= 0x40000000
        bin_data += struct.pack("<IIII", flags, pitch_value, wf.loop_start & 0xFFFFFFFF, loop_end & 0xFFFFFFFF)

        if not compress:
            for x in wf.read_data(0, wf.loop_end):
                bin_data.append(quantize(x) & 0xFF)
            # Align to 4 bytes.
            while len(bin_data) % 4 != 0:
                bin_data.append(0)
        else:
            _convert_dpcm_impl(wf, settings,
                               lambda s: bin_data.append(s & 0xFF),
                               lambda d: bin_data.append(d & 0xFF))

        try:
            with open(out_file_str, "wb") as f:
                f.write(bin_data)
        except OSError as e:
            sys.stderr.write("ofstream: %s\n" % e.strerror)
            raise WavError("unable to open output file")
    else:
        w = AsmWriter()
        w.out("    .section .rodata\n")
        w.out("    .global %s\n" % sym)
        w.out("    .align  2\n\n%s:\n\n" % sym)
        w.out("    .byte   0x%X, 0x0, 0x0, 0x%X\n" % (fmt, 0x40 if wf.loop_enabled else 0x0))
        w.out("    .word   0x%08X  @ Mid-C ~%f\n" % (pitch_value, pitch))
        w.out("    .word   %u, %u\n" % (wf.loop_start & 0xFFFFFFFF, loop_end & 0xFFFFFFFF))

        if not compress:
            loop_sample = 0
            for i, x in enumerate(wf.read_data(0, wf.loop_end)):
                s = quantize(x)
                if wf.loop_enabled and i == wf.loop_start:
                    loop_sample = s
                w.data_write(s, False)
            w.data_write(loop_sample, False)
        else:
            _convert_dpcm_impl(wf, settings,
                               lambda s: w.data_write(s, False),
                               lambda d: w.data_write(d, True))

        w.out("\n\n    .end\n")
        try:
            with open(out_file_str, "w", newline="\n", encoding="latin-1") as f:
                f.write("".join(w.parts))
        except OSError as e:
            sys.stderr.write("ofstream: %s\n" % e.strerror)
            raise WavError("unable to open output file")


# ---------------------------------------------------------------------------
# wav2agb.cpp
# ---------------------------------------------------------------------------

def usage():
    sys.stderr.write(
        "wav2agb\n"
        "\n"
        "Usage: wav2agb [options] <input.wav> [<output>]\n"
        "\n"
        "Options:\n"
        "-s, --symbol <sym>       | symbol name for wave header (default: file name)\n"
        "-l, --lookahead <amount> | DPCM compression lookahead 1..8 (default: 3)\n"
        "-c, --compress           | compress output with DPCM\n"
        "-f, --fast-compress      | compress output with DPCM fast\n"
        "--no-pad                 | omit trailing padding in compressed output\n"
        "-b, --binary             | output raw binary instead of assembly\n"
        "--loop-start <pos>       | override loop start (integer)\n"
        "--loop-end <pos>         | override loop end (integer)\n"
        "--tune <cents>           | override tuning (float)\n"
        "--key <key>              | override midi key (int)\n"
        "--rate <rate>            | override base samplerate (int)\n"
        "--set-agbl <loop-end>    | adds the custom agbl chunk to the given input .wav file\n")
    sys.exit(1)


def die(msg):
    sys.stderr.write(msg)
    sys.exit(1)


def fix_str(s):
    # replaces all characters that are not alphanumerical
    out = []
    for i, ch in enumerate(s):
        if "a" <= ch <= "z" or "A" <= ch <= "Z" or ("0" <= ch <= "9" and i > 0):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


PATH_SEPARATORS = ["/", "\\"] if os.name == "nt" else ["/"]


def filename_without_ext(s):
    last_sep = 0
    for sep in PATH_SEPARATORS:
        pos = s.rfind(sep)
        if pos >= 0:
            last_sep = max(pos, last_sep)
    dot = s.rfind(".")
    if dot < 0:
        return s
    if dot > last_sep:
        return s[:dot]
    return s


def filename_without_dir(s):
    last_sep = 0
    found = False
    for sep in PATH_SEPARATORS:
        pos = s.rfind(sep)
        if pos >= 0:
            last_sep = max(pos, last_sep)
            found = True
    if s and found:
        return s[last_sep + 1:]
    return s


def _parse_c_number(text, kind):
    import re
    if kind == "float":
        m = re.match(r"\s*[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?|inf(inity)?|nan)", text, re.I)
        if not m:
            raise WavError("stod: no conversion")
        return float(m.group(0))
    m = re.match(r"\s*([+-]?\d+)", text)
    if not m:
        raise WavError("%s: no conversion" % kind)
    value = int(m.group(1))
    if kind == "stoi":
        if value > 0x7FFFFFFF or value < -0x80000000:
            raise WavError("stoi: out of range")
        return value
    # stoul accepts a leading '-' and negates in unsigned arithmetic
    if abs(value) > 0xFFFFFFFFFFFFFFFF:
        raise WavError("stoul: out of range")
    return value & 0xFFFFFFFFFFFFFFFF


def main(argv):
    settings = Settings()
    compress = False
    binary = False
    sym = ""
    input_file = None
    output_file = None
    set_agbl = False
    agbl_value = 0

    try:
        if len(argv) == 1:
            usage()

        i = 1
        argc = len(argv)
        while i < argc:
            st = argv[i]
            if st in ("-s", "--symbol"):
                i += 1
                if i >= argc:
                    die("-s: missing symbol name\n")
                sym = fix_str(argv[i])
            elif st in ("-c", "--compress"):
                compress = True
            elif st in ("-f", "--compress-fast"):
                compress = True
                settings.dpcm_lookahead_fast = True
            elif st == "--no-pad":
                settings.dpcm_include_padding = False
            elif st in ("-b", "--binary"):
                binary = True
            elif st == "--verbose":
                settings.dpcm_verbose = True
            elif st in ("-l", "--lookahead"):
                i += 1
                if i >= argc:
                    die("-l: missing parameter")
                settings.dpcm_enc_lookahead = _clamp(_parse_c_number(argv[i], "stoul"), 1, 8)
            elif st == "--version":
                sys.stdout.write("wav2agb v1.1 (c) 2019 ipatix\n")
                sys.exit(0)
            elif st == "--loop-start":
                i += 1
                if i >= argc:
                    die("--loop-start: missing parameter")
                settings.loop_start = _parse_c_number(argv[i], "stoul") & 0xFFFFFFFF
            elif st == "--loop-end":
                i += 1
                if i >= argc:
                    die("--loop-end: missing parameter")
                settings.loop_end = _parse_c_number(argv[i], "stoul") & 0xFFFFFFFF
            elif st == "--tune":
                i += 1
                if i >= argc:
                    die("--tune: missing parameter")
                settings.tune = _parse_c_number(argv[i], "float")
            elif st == "--key":
                i += 1
                if i >= argc:
                    die("--key: missing parameter")
                settings.key = _clamp(_parse_c_number(argv[i], "stoi"), 0, 127)
            elif st == "--rate":
                i += 1
                if i >= argc:
                    die("--rate: missing parameter")
                settings.rate = _parse_c_number(argv[i], "stoul") & 0xFFFFFFFF
            elif st == "--set-agbl":
                i += 1
                if i >= argc:
                    die("--set-agbl: missing parameter")
                agbl_value = _parse_c_number(argv[i], "stoi")
                set_agbl = True
            else:
                if st == "--":
                    i += 1
                    if i >= argc:
                        die("--: missing file name\n")
                if input_file is None:
                    input_file = argv[i]
                    if not input_file:
                        die("empty input file name\n")
                elif output_file is None:
                    output_file = argv[i]
                    if not output_file:
                        die("empty output file name\n")
                else:
                    die("Too many files specified\n")
            i += 1

        if input_file is None:
            die("No input file specified\n")

        if output_file is None:
            # create output file name if none is provided
            if set_agbl:
                output_file = input_file
            elif binary:
                output_file = filename_without_ext(input_file) + ".bin"
            else:
                output_file = filename_without_ext(input_file) + ".s"

        if not sym:
            sym = fix_str(filename_without_dir(filename_without_ext(output_file)))

        if set_agbl:
            wav = WavFile(input_file)
            if agbl_value < 0:
                # Negative value: offset from end of samples
                calculated = wav.num_samples + agbl_value
                if calculated < 0:
                    die("--set-agbl: negative offset %d exceeds total samples %u\n" % (agbl_value, wav.num_samples))
                loop_end_value = calculated & 0xFFFFFFFF
            else:
                loop_end_value = agbl_value & 0xFFFFFFFF
            write_wav_with_agbl_chunk(output_file, wav.chunks, loop_end_value)
            return 0

        convert(input_file, output_file, sym, compress, binary, settings)
        return 0
    except WavError as e:
        sys.stderr.write("std lib error:\n%s\n" % e)
    return 1


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
