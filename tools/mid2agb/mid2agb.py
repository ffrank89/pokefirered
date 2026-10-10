#!/usr/bin/env python3
# Python port of mid2agb (main.cpp, midi.cpp, agb.cpp, tables.cpp, error.cpp)
# Copyright(c) 2016 YamaArashi (MIT license, see LICENSE)
#
# Converts a standard MIDI file into an assembly file for the GBA's
# MusicPlayer2000 (m4a) sound engine.
#
# Usage: mid2agb.py input_file [output_file] [options]

import math
import signal
import struct
import sys

# ---------------------------------------------------------------------------
# tables.cpp
# ---------------------------------------------------------------------------

NOTE_DURATION_LUT = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    24, 24, 24, 24, 28, 28, 30, 30, 32, 32, 32, 32, 36, 36, 36, 36, 40, 40, 42, 42, 44, 44, 44, 44,
    48, 48, 48, 48, 52, 52, 54, 54, 56, 56, 56, 56, 60, 60, 60, 60, 64, 64, 66, 66, 68, 68, 68, 68,
    72, 72, 72, 72, 76, 76, 78, 78, 80, 80, 80, 80, 84, 84, 84, 84, 88, 88, 90, 90, 92, 92, 92, 92,
    96,
]

NOTE_VELOCITY_LUT = [0] + [((i + 3) // 4) * 4 for i in range(1, 125)] + [127, 127, 127]

NOTE_TABLE = ["Cn%01u ", "Cs%01u ", "Dn%01u ", "Ds%01u ", "En%01u ", "Fn%01u ",
              "Fs%01u ", "Gn%01u ", "Gs%01u ", "An%01u ", "As%01u ", "Bn%01u "]

MINUS_NOTE_TABLE = ["CnM%01u", "CsM%01u", "DnM%01u", "DsM%01u", "EnM%01u", "FnM%01u",
                    "FsM%01u", "GnM%01u", "GsM%01u", "AnM%01u", "AsM%01u", "BnM%01u"]

# The C++ version reads g_noteDurationLUT[-1] when scoring tied notes, which is
# whatever happens to precede the table in memory. Any value outside the
# table's range behaves identically; this is the value from the macOS build.
_LUT_BEFORE_START = 1385600662


def duration_lut(index):
    if index < 0:
        return _LUT_BEFORE_START
    return NOTE_DURATION_LUT[index]


def u32(v):
    return v & 0xFFFFFFFF


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def c_div(a, b):
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def fmt_u(v):
    return str(u32(v))


def fmt_plus_d(v):
    v = s32(v)
    return "%+d" % v


# ---------------------------------------------------------------------------
# error.cpp
# ---------------------------------------------------------------------------

class MidiError(Exception):
    pass


def raise_error(msg):
    raise MidiError(msg)


# ---------------------------------------------------------------------------
# midi.h
# ---------------------------------------------------------------------------

class EventType:
    EndOfTie = 0x01
    Label = 0x11
    LoopEnd = 0x12
    LoopEndBegin = 0x13
    LoopBegin = 0x14
    OriginalTimeSignature = 0x15
    WholeNoteMark = 0x16
    Pattern = 0x17
    TimeSignature = 0x18
    Tempo = 0x19
    InstrumentChange = 0x21
    Controller = 0x22
    PitchBend = 0x23
    KeyShift = 0x31
    Note = 0x40
    TimeSplit = 0xFE
    EndOfTrack = 0xFF


class Event:
    __slots__ = ("time", "type", "note", "param1", "param2")

    def __init__(self, time=0, type=0, note=0, param1=0, param2=0):
        self.time = time
        self.type = type
        self.note = note
        self.param1 = param1
        self.param2 = param2

    def copy(self):
        return Event(self.time, self.type, self.note, self.param1, self.param2)

    def __eq__(self, other):
        return (self.time == other.time and self.type == other.type and self.note == other.note
                and self.param1 == other.param1 and self.param2 == other.param2)

    def __ne__(self, other):
        return not self == other

    # Field setters with C integer widths
    def set_note(self, v):
        self.note = v & 0xFF

    def set_param1(self, v):
        self.param1 = v & 0xFF

    def set_param2(self, v):
        self.param2 = s32(v)


def is_pattern_boundary(event_type):
    return event_type == EventType.EndOfTrack or event_type <= 0x17


def event_compare(event1, event2):
    if event1.time < event2.time:
        return True
    if event1.time > event2.time:
        return False

    event1_type = event1.type
    event2_type = event2.type

    if event1.type == EventType.Note:
        event1_type += event1.note
    if event2.type == EventType.Note:
        event2_type += event2.note

    if event1_type < event2_type:
        return True
    if event1_type > event2_type:
        return False

    if event1.type == EventType.EndOfTie:
        if event1.note < event2.note:
            return True
        if event1.note > event2.note:
            return False

    return False


def event_sort_key(event):
    type_key = event.type + (event.note if event.type == EventType.Note else 0)
    tie_key = event.note if event.type == EventType.EndOfTie else 0
    return (event.time, type_key, tie_key)


# ---------------------------------------------------------------------------
# The converter
# ---------------------------------------------------------------------------

CATEGORY_CONTROL, CATEGORY_SYSEX, CATEGORY_META, CATEGORY_INVALID = range(4)


class Mid2Agb:
    def __init__(self):
        # main.cpp options
        self.asm_label = ""
        self.master_volume = 127
        self.voice_group = 0
        self.priority = 0
        self.reverb = -1
        self.clocks_per_beat = 1
        self.exact_gate_time = False
        self.compression_enabled = True

        # midi.cpp state
        self.data = b""
        self.pos = 0
        self.midi_format = 0
        self.midi_track_count = 0
        self.midi_time_div = 0
        self.midi_chan = 0
        self.initial_wait = 0
        self.track_data_start = 0
        self.seq_events = []
        self.track_events = []
        self.absolute_time = 0
        self.block_count = 0
        self.min_note = 0
        self.max_note = 0
        self.running_status = 0

        # agb.cpp state
        self.out = []
        self.agb_track = 0
        self.last_op_name = ""
        self.block_num = 0
        self.keep_last_op_name = False
        self.last_note = 0
        self.last_velocity = 0
        self.note_changed = False
        self.velocity_changed = False
        self.in_pattern = False
        self.extended_command = 0
        self.memacc_op = 0
        self.memacc_param1 = 0
        self.memacc_param2 = 0

    def write(self, text):
        self.out.append(text)

    # -- file reading ------------------------------------------------------

    def seek(self, offset):
        if offset < 0:
            raise_error("failed to seek to %d" % offset)
        self.pos = offset

    def skip(self, offset):
        if self.pos + offset < 0:
            raise_error("failed to skip %d bytes" % offset)
        self.pos += offset

    def read_signature(self):
        if self.pos + 4 > len(self.data):
            raise_error("failed to read signature")
        sig = self.data[self.pos:self.pos + 4]
        self.pos += 4
        return sig

    def read_int8(self):
        if self.pos >= len(self.data):
            raise_error("unexpected EOF")
        c = self.data[self.pos]
        self.pos += 1
        return c

    def read_int16(self):
        return (self.read_int8() << 8) | self.read_int8()

    def read_int24(self):
        return (self.read_int8() << 16) | (self.read_int8() << 8) | self.read_int8()

    def read_int32(self):
        val = self.read_int8() << 24
        val |= self.read_int8() << 16
        val |= self.read_int8() << 8
        val |= self.read_int8()
        return val

    def read_vlq(self):
        val = 0
        while True:
            c = self.read_int8()
            val = u32(val << 7) | (c & 0x7F)
            if not (c & 0x80):
                return val

    def read_midi_file_header(self):
        self.seek(0)
        if self.read_signature() != b"MThd":
            raise_error('MIDI file header signature didn\'t match "MThd"')
        if self.read_int32() != 6:
            raise_error("MIDI file header length isn't 6")
        midi_format = self.read_int16()
        if midi_format >= 2:
            raise_error("unsupported MIDI format (%u)" % midi_format)
        self.midi_format = midi_format
        self.midi_track_count = self.read_int16()
        time_div = self.read_int16()
        self.midi_time_div = time_div - 0x10000 if time_div & 0x8000 else time_div
        if self.midi_time_div < 0:
            raise_error("unsupported MIDI time division (%d)" % self.midi_time_div)

    def read_midi_track_header(self, offset):
        self.seek(offset)
        if self.read_signature() != b"MTrk":
            raise_error('MIDI track header signature didn\'t match "MTrk"')
        size = self.read_int32()
        self.track_data_start = self.pos
        return size + 8

    def start_track(self):
        self.seek(self.track_data_start)
        self.absolute_time = 0
        self.running_status = 0

    def skip_event_data(self):
        self.skip(self.read_vlq())

    def determine_event_category(self):
        type_chan = self.read_int8()
        size = 0
        if type_chan < 0x80:
            # If data byte was found, use the running status.
            self.pos -= 1
            type_chan = self.running_status

        if type_chan == 0xFF:
            category = CATEGORY_META
            self.running_status = 0
        elif type_chan >= 0xF0:
            category = CATEGORY_SYSEX
            self.running_status = 0
        elif type_chan >= 0x80:
            category = CATEGORY_CONTROL
            size = 1 if (type_chan >> 4) in (0xC, 0xD) else 2
            self.running_status = type_chan
        else:
            category = CATEGORY_INVALID
        return category, type_chan, size

    def make_block_event(self, event, event_type):
        event.type = event_type
        event.set_param1(self.block_count)
        self.block_count += 1
        event.param2 = 0

    def read_event_text(self):
        length = self.read_vlq()
        if length <= 2:
            if length == 0 or self.pos + length > len(self.data):
                raise_error("failed to read event text")
            text = self.data[self.pos:self.pos + length]
            self.pos += length
            return text
        self.skip(length)
        return b""

    def read_seq_event(self, event):
        self.absolute_time = s32(self.absolute_time + self.read_vlq())
        event.time = self.absolute_time

        category, type_chan, size = self.determine_event_category()

        if category == CATEGORY_CONTROL:
            self.skip(size)
            return False
        if category == CATEGORY_SYSEX:
            self.skip_event_data()
            return False
        if category == CATEGORY_INVALID:
            raise_error("invalid event")

        meta_event_type = self.read_int8()

        if 1 <= meta_event_type <= 7:
            text = self.read_event_text()
            if text == b"[":
                self.make_block_event(event, EventType.LoopBegin)
            elif text == b"][":
                self.make_block_event(event, EventType.LoopEndBegin)
            elif text == b"]":
                self.make_block_event(event, EventType.LoopEnd)
            elif text == b":":
                self.make_block_event(event, EventType.Label)
            else:
                return False
        elif meta_event_type == 0x2F:  # end of track
            self.skip_event_data()
            event.type = EventType.EndOfTrack
            event.param1 = 0
            event.param2 = 0
        elif meta_event_type == 0x51:  # tempo
            if self.read_vlq() != 3:
                raise_error("invalid tempo size")
            event.type = EventType.Tempo
            event.param1 = 0
            event.set_param2(self.read_int24())
        elif meta_event_type == 0x58:  # time signature
            if self.read_vlq() != 4:
                raise_error("invalid time signature size")
            numerator = self.read_int8()
            denominator_exponent = self.read_int8()
            if denominator_exponent >= 16:
                raise_error("invalid time signature denominator")
            self.skip(2)  # ignore other values
            clock_ticks = 96 * numerator * self.clocks_per_beat
            denominator = 1 << denominator_exponent
            time_sig = c_div(clock_ticks, denominator)
            if time_sig <= 0 or time_sig >= 0x10000:
                raise_error("invalid time signature")
            event.type = EventType.TimeSignature
            event.param1 = 0
            event.set_param2(time_sig)
        else:
            self.skip_event_data()
            return False
        return True

    def read_seq_events(self):
        self.start_track()
        while True:
            event = Event()
            if self.read_seq_event(event):
                self.seq_events.append(event)
                if event.type == EventType.EndOfTrack:
                    return

    def check_note_end(self, event):
        event.set_param2(event.param2 + self.read_vlq())

        category, type_chan, size = self.determine_event_category()

        if category == CATEGORY_CONTROL:
            chan = type_chan & 0xF
            if chan != self.midi_chan:
                self.skip(size)
                return False
            status = type_chan & 0xF0
            if status == 0x80:  # note off
                note = self.read_int8()
                self.read_int8()  # ignore velocity
                if note == event.note:
                    return True
            elif status == 0x90:  # note on
                note = self.read_int8()
                velocity = self.read_int8()
                if velocity == 0 and note == event.note:
                    return True
            else:
                self.skip(size)
            return False

        if category == CATEGORY_SYSEX:
            self.skip_event_data()
            return False

        if category == CATEGORY_META:
            meta_event_type = self.read_int8()
            self.skip_event_data()
            if meta_event_type == 0x2F:
                raise_error("note doesn't end")
            return False

        raise_error("invalid event")

    def find_note_end(self, event):
        # Save the current file position and running status
        # which get modified by check_note_end.
        start_pos = self.pos
        saved_running_status = self.running_status
        event.param2 = 0
        while not self.check_note_end(event):
            pass
        self.seek(start_pos)
        self.running_status = saved_running_status

    def read_track_event(self, event):
        self.absolute_time = s32(self.absolute_time + self.read_vlq())
        event.time = self.absolute_time

        category, type_chan, size = self.determine_event_category()

        if category == CATEGORY_CONTROL:
            chan = type_chan & 0xF
            if chan != self.midi_chan:
                self.skip(size)
                return False

            status = type_chan & 0xF0
            if status == 0x90:  # note on
                note = self.read_int8()
                velocity = self.read_int8()
                if velocity != 0:
                    event.type = EventType.Note
                    event.set_note(note)
                    event.set_param1(velocity)
                    self.find_note_end(event)
                    if event.param2 > 0:
                        if note < self.min_note:
                            self.min_note = note
                        if note > self.max_note:
                            self.max_note = note
            elif status == 0xB0:  # controller event
                event.type = EventType.Controller
                event.set_param1(self.read_int8())  # controller index
                event.set_param2(self.read_int8())  # value
            elif status == 0xC0:  # instrument change
                event.type = EventType.InstrumentChange
                event.set_param1(self.read_int8())  # instrument
                event.param2 = 0
            elif status == 0xE0:  # pitch bend
                event.type = EventType.PitchBend
                event.set_param1(self.read_int8())
                event.set_param2(self.read_int8())
            else:
                self.skip(size)
                return False
            return True

        if category == CATEGORY_SYSEX:
            self.skip_event_data()
            return False

        if category == CATEGORY_META:
            meta_event_type = self.read_int8()
            self.skip_event_data()
            if meta_event_type == 0x2F:
                event.type = EventType.EndOfTrack
                event.param1 = 0
                event.param2 = 0
                return True
            return False

        raise_error("invalid event")

    def read_track_events(self):
        self.start_track()
        self.track_events = []
        self.min_note = 0xFF
        self.max_note = 0
        while True:
            event = Event()
            if self.read_track_event(event):
                self.track_events.append(event)
                if event.type == EventType.EndOfTrack:
                    return

    def merge_events(self):
        events = []
        t = 0
        s = 0
        track = self.track_events
        seq = self.seq_events

        while track[t].type != EventType.EndOfTrack and seq[s].type != EventType.EndOfTrack:
            if event_compare(track[t], seq[s]):
                events.append(track[t].copy())
                t += 1
            else:
                events.append(seq[s].copy())
                s += 1

        while track[t].type != EventType.EndOfTrack:
            events.append(track[t].copy())
            t += 1

        while seq[s].type != EventType.EndOfTrack:
            events.append(seq[s].copy())
            s += 1

        # Push the EndOfTrack event with the larger time.
        if event_compare(track[t], seq[s]):
            events.append(seq[s].copy())
        else:
            events.append(track[t].copy())
        return events

    def convert_times(self, events):
        cpb = self.clocks_per_beat
        div = self.midi_time_div
        for event in events:
            event.time = s32(c_div(s32(24 * cpb * event.time), div))
            if event.type == EventType.Note:
                event.set_param1(NOTE_VELOCITY_LUT[event.param1])
                duration = u32(c_div(s32(24 * cpb * event.param2), div))
                if duration == 0:
                    duration = 1
                if not self.exact_gate_time and duration < 96:
                    duration = NOTE_DURATION_LUT[duration]
                event.set_param2(duration)

    def insert_timing_events(self, in_events):
        out_events = []
        timing_event = Event(0, EventType.TimeSignature, 0, 0, 96 * self.clocks_per_beat)

        for event in in_events:
            while event_compare(timing_event, event):
                out_events.append(timing_event.copy())
                timing_event.time = s32(timing_event.time + timing_event.param2)

            if event.type == EventType.TimeSignature:
                if self.agb_track == 1 and event.param2 != timing_event.param2:
                    original = event.copy()
                    original.type = EventType.OriginalTimeSignature
                    out_events.append(original)
                timing_event.param2 = event.param2
                timing_event.time = s32(event.time + timing_event.param2)

            out_events.append(event.copy())
        return out_events

    @staticmethod
    def split_time(in_events):
        out_events = []
        time = 0
        for event in in_events:
            diff = s32(event.time - time)
            if diff > 96:
                whole_note_count = c_div(diff - 1, 96)
                diff -= 96 * whole_note_count
                for _ in range(whole_note_count):
                    time += 96
                    out_events.append(Event(time, EventType.TimeSplit))

            lut_value = NOTE_DURATION_LUT[diff]
            if lut_value != diff:
                out_events.append(Event(time + lut_value, EventType.TimeSplit))

            time = event.time
            out_events.append(event.copy())
        return out_events

    @staticmethod
    def create_ties(in_events):
        out_events = []
        for event in in_events:
            if event.type == EventType.Note and event.param2 > 96:
                tie = event.copy()
                tie.param2 = -1
                out_events.append(tie)
                out_events.append(Event(s32(event.time + event.param2), EventType.EndOfTie, event.note))
            else:
                out_events.append(event.copy())
        return out_events

    def calculate_waits(self, events):
        self.initial_wait = events[0].time
        whole_note_count = 0
        i = 0
        while i < len(events) and events[i].type != EventType.EndOfTrack:
            events[i].time = s32(events[i + 1].time - events[i].time)
            if events[i].type == EventType.TimeSignature:
                events[i].type = EventType.WholeNoteMark
                events[i].param2 = whole_note_count
                whole_note_count += 1
            i += 1

    @staticmethod
    def calculate_compression_score(events, index):
        score = 0
        last_param1 = events[index].param1
        last_velocity = 0x80
        last_type = events[index].type
        last_duration = -0x80000000
        last_note = 0x40

        if events[index].time > 0:
            score += 1

        i = index + 1
        while not is_pattern_boundary(events[i].type):
            ev = events[i]
            if ev.type == EventType.Note:
                val = 0
                if ev.note != last_note:
                    val += 1
                    last_note = ev.note
                if ev.param1 != last_velocity:
                    val += 1
                    last_velocity = ev.param1
                duration = ev.param2
                if duration_lut(duration) != last_duration:
                    val += 1
                    last_duration = duration_lut(duration)
                if duration != last_duration:
                    val += 1
                if val == 0:
                    val = 1
                score += val
            else:
                last_duration = -0x80000000
                if ev.type == last_type:
                    if ((last_type != EventType.Controller and last_type != 0x25
                         and last_type != EventType.EndOfTie) or ev.param1 == last_param1):
                        score += 1
                    else:
                        score += 2
                else:
                    score += 2

            last_param1 = ev.param1
            last_type = ev.type

            if ev.time:
                score += 1
            i += 1
        return score

    @staticmethod
    def is_compression_match(events, index1, index2):
        e1 = events[index1]
        e2 = events[index2]
        if e1.type != e2.type or e1.note != e2.note or e1.param1 != e2.param1 or e1.time != e2.time:
            return False

        index1 += 1
        index2 += 1
        while True:
            if events[index1] != events[index2]:
                return False
            index1 += 1
            index2 += 1
            if is_pattern_boundary(events[index1].type):
                break
        return is_pattern_boundary(events[index2].type)

    def compress_whole_note(self, events, index):
        j = index + 1
        while events[j].type != EventType.EndOfTrack:
            while events[j].type != EventType.WholeNoteMark:
                j += 1
                if events[j].type == EventType.EndOfTrack:
                    return
            if self.is_compression_match(events, index, j):
                events[j].type = EventType.Pattern
                events[j].param2 = events[index].param2 & 0x7FFFFFFF
                events[index].param2 = s32(events[index].param2 | 0x80000000)
            j += 1

    def compress(self, events):
        i = 0
        while events[i].type != EventType.EndOfTrack:
            while events[i].type != EventType.WholeNoteMark:
                i += 1
                if events[i].type == EventType.EndOfTrack:
                    return
            if self.calculate_compression_score(events, i) >= 6:
                self.compress_whole_note(events, i)
            i += 1

    def read_midi_tracks(self):
        track_header_start = 14

        self.read_midi_track_header(track_header_start)
        self.read_seq_events()

        self.agb_track = 1

        for _ in range(self.midi_track_count):
            track_header_start += self.read_midi_track_header(track_header_start)

            for chan in range(16):
                self.midi_chan = chan
                self.read_track_events()

                if self.min_note != 0xFF:
                    events = self.merge_events()

                    # We don't need TEMPO in anything but track 1.
                    if self.agb_track == 1:
                        self.seq_events = [e for e in self.seq_events if e.type != EventType.Tempo]

                    self.convert_times(events)
                    events = self.insert_timing_events(events)
                    events = self.create_ties(events)
                    events.sort(key=event_sort_key)  # stable, like std::stable_sort
                    events = self.split_time(events)
                    self.calculate_waits(events)

                    if self.compression_enabled:
                        self.compress(events)

                    self.print_agb_track(events)
                    self.agb_track += 1

    # -- agb.cpp -----------------------------------------------------------

    def print_agb_header(self):
        label = self.asm_label
        self.write('\t.include "MPlayDef.s"\n\n')
        self.write("\t.equ\t%s_grp, voicegroup%03u\n" % (label, u32(self.voice_group)))
        self.write("\t.equ\t%s_pri, %s\n" % (label, fmt_u(self.priority)))
        if self.reverb >= 0:
            self.write("\t.equ\t%s_rev, reverb_set+%s\n" % (label, fmt_u(self.reverb)))
        else:
            self.write("\t.equ\t%s_rev, 0\n" % label)
        self.write("\t.equ\t%s_mvl, %s\n" % (label, fmt_u(self.master_volume)))
        self.write("\t.equ\t%s_key, %u\n" % (label, 0))
        self.write("\t.equ\t%s_tbs, %u\n" % (label, self.clocks_per_beat))
        self.write("\t.equ\t%s_exg, %u\n" % (label, int(self.exact_gate_time)))
        self.write("\t.equ\t%s_cmp, %u\n" % (label, int(self.compression_enabled)))
        self.write("\n\t.section .rodata\n")
        self.write("\t.global\t%s\n" % label)
        self.write("\t.align\t2\n")

    def reset_track_vars(self):
        self.last_velocity = -1
        self.last_note = -1
        self.velocity_changed = False
        self.note_changed = False
        self.keep_last_op_name = False
        self.last_op_name = ""
        self.in_pattern = False

    def print_wait(self, wait):
        if wait > 0:
            self.write("\t.byte\tW%02d\n" % wait)
            self.velocity_changed = True
            self.note_changed = True
            self.keep_last_op_name = True

    def print_op(self, wait, name, text=None):
        """text is the already formatted operand string, or None for no operands."""
        self.write("\t.byte\t\t")
        if text is not None:
            if not self.compression_enabled or self.last_op_name != name:
                self.write("%s, " % name)
                self.last_op_name = name
            else:
                self.write("        ")
            self.write(text)
        else:
            self.write(name)
            self.last_op_name = name
        self.write("\n")
        self.print_wait(wait)

    def print_byte(self, text):
        self.write("\t.byte\t%s\n" % text)
        self.velocity_changed = True
        self.note_changed = True
        self.keep_last_op_name = True

    def print_word(self, text):
        self.write("\t .word\t%s\n" % text)

    @staticmethod
    def note_name(note):
        if note >= 24:
            return NOTE_TABLE[note % 12] % (note // 12 - 2)
        return MINUS_NOTE_TABLE[note % 12] % u32(-(note // 12) + 2)

    def print_note(self, event):
        note = event.note
        velocity = NOTE_VELOCITY_LUT[event.param1]
        duration = -1

        if event.param2 != -1:
            duration = NOTE_DURATION_LUT[event.param2]

        gate_time_param = 0
        if self.exact_gate_time and duration != -1:
            gate_time_param = event.param2 - duration

        gtp_buf = (", gtp%u" % gate_time_param) if gate_time_param > 0 else ""
        op_name = "TIE   " if duration == -1 else ("N%02u   " % duration)[:15]

        note_changed = True
        velocity_changed = True

        if self.compression_enabled:
            note_changed = note != self.last_note
            velocity_changed = velocity != self.last_velocity

        if self.keep_last_op_name:
            self.keep_last_op_name = False
        else:
            self.last_op_name = ""

        if note_changed or velocity_changed or gate_time_param > 0:
            self.last_note = note
            note_buf = self.note_name(note)[:15]

            if velocity_changed or gate_time_param > 0:
                self.last_velocity = velocity
                velocity_buf = (", v%03u" % velocity)[:15]
            else:
                velocity_buf = ""

            self.print_op(event.time, op_name, note_buf + velocity_buf + gtp_buf)
        else:
            self.print_op(event.time, op_name, None)

        self.note_changed = note_changed
        self.velocity_changed = velocity_changed

    def print_end_of_tie_op(self, event):
        note = event.note
        note_changed = note != self.last_note

        if not note_changed or not self.note_changed:
            self.last_op_name = ""

        if not note_changed and self.compression_enabled:
            self.print_op(event.time, "EOT   ", None)
        else:
            self.last_note = note
            self.print_op(event.time, "EOT   ", self.note_name(note))

        self.note_changed = note_changed

    def print_seq_loop_label(self, event):
        self.block_num = event.param1 + 1
        self.write("%s_%u_B%u:\n" % (self.asm_label, self.agb_track, self.block_num))
        self.print_wait(event.time)
        self.reset_track_vars()

    def print_mem_acc(self, event):
        label_word = "%s_%u_L%s" % (self.asm_label, self.agb_track, fmt_u(self.memacc_param2))
        p1 = "0x%02X" % u32(self.memacc_param1)
        dec = fmt_u(event.param2)
        hexv = "0x%02X" % u32(event.param2)
        ops = {
            0x00: ("mem_set", dec, False),
            0x01: ("mem_add", dec, False),
            0x02: ("mem_sub", dec, False),
            0x03: ("mem_mem_set", hexv, False),
            0x04: ("mem_mem_add", hexv, False),
            0x05: ("mem_mem_sub", hexv, False),
            0x06: ("mem_beq", dec, True),
            0x07: ("mem_bne", dec, True),
            0x08: ("mem_bhi", dec, True),
            0x09: ("mem_bhs", dec, True),
            0x0A: ("mem_bls", dec, True),
            0x0B: ("mem_blo", dec, True),
            0x0C: ("mem_mem_beq", hexv, True),
            0x0D: ("mem_mem_bne", hexv, True),
            0x0E: ("mem_mem_bhi", hexv, True),
            0x0F: ("mem_mem_bhs", hexv, True),
            0x10: ("mem_mem_bls", hexv, True),
            0x11: ("mem_mem_blo", hexv, True),
        }
        op = ops.get(self.memacc_op)
        if op is not None:
            name, value, has_label = op
            self.print_byte("MEMACC, %s, %s, %s" % (name, p1, value))
            if has_label:
                self.print_word(label_word)
        self.print_wait(event.time)

    def print_extended_op(self, event):
        # TODO: support for other extended commands
        if self.extended_command == 0x08:
            self.print_op(event.time, "XCMD  ", "xIECV , %s" % fmt_u(event.param2))
        elif self.extended_command == 0x09:
            self.print_op(event.time, "XCMD  ", "xIECL , %s" % fmt_u(event.param2))
        else:
            self.print_wait(event.time)

    def print_controller_op(self, event):
        p1 = event.param1
        p2 = event.param2
        if p1 == 0x01:
            self.print_op(event.time, "MOD   ", fmt_u(p2))
        elif p1 == 0x07:
            self.print_op(event.time, "VOL   ", "%s*%s_mvl/mxv" % (fmt_u(p2), self.asm_label))
        elif p1 == 0x0A:
            self.print_op(event.time, "PAN   ", "c_v" + fmt_plus_d(p2 - 64))
        elif p1 in (0x0C, 0x10):
            self.print_mem_acc(event)
        elif p1 == 0x0D:
            self.memacc_op = p2
            self.print_wait(event.time)
        elif p1 == 0x0E:
            self.memacc_param1 = p2
            self.print_wait(event.time)
        elif p1 == 0x0F:
            self.memacc_param2 = p2
            self.print_wait(event.time)
        elif p1 == 0x11:
            self.write("%s_%u_L%s:\n" % (self.asm_label, self.agb_track, fmt_u(p2)))
            self.print_wait(event.time)
            self.reset_track_vars()
        elif p1 == 0x14:
            self.print_op(event.time, "BENDR ", fmt_u(p2))
        elif p1 == 0x15:
            self.print_op(event.time, "LFOS  ", fmt_u(p2))
        elif p1 == 0x16:
            self.print_op(event.time, "MODT  ", fmt_u(p2))
        elif p1 == 0x18:
            self.print_op(event.time, "TUNE  ", "c_v" + fmt_plus_d(p2 - 64))
        elif p1 == 0x1A:
            self.print_op(event.time, "LFODL ", fmt_u(p2))
        elif p1 in (0x1D, 0x1F):
            self.print_extended_op(event)
        elif p1 == 0x1E:
            self.extended_command = p2
            # TODO: loop op
        elif p1 in (0x21, 0x27):
            self.print_byte("PRIO  , %s" % fmt_u(p2))
            self.print_wait(event.time)
        else:
            self.print_wait(event.time)

    def print_agb_track(self, events):
        label = self.asm_label
        self.write("\n@**************** Track %u (Midi-Chn.%u) ****************@\n\n"
                   % (self.agb_track, self.midi_chan + 1))
        self.write("%s_%u:\n" % (label, self.agb_track))

        whole_note_count = 0
        loop_end_block_num = 0

        self.reset_track_vars()

        found_vol_before_note = False
        for event in events:
            if event.type == EventType.Note:
                break
            if event.type == EventType.Controller and event.param1 == 0x07:
                found_vol_before_note = True
                break

        if not found_vol_before_note:
            self.print_byte("\tVOL   , 127*%s_mvl/mxv" % label)

        self.print_wait(self.initial_wait)
        self.print_byte("KEYSH , %s_key%+d" % (label, 0))

        i = 0
        while events[i].type != EventType.EndOfTrack:
            event = events[i]

            if is_pattern_boundary(event.type):
                if self.in_pattern:
                    self.print_byte("PEND")
                self.in_pattern = False

            if event.type in (EventType.WholeNoteMark, EventType.Pattern):
                self.write("@ %03d   ----------------------------------------\n" % whole_note_count)
                whole_note_count += 1

            t = event.type
            if t == EventType.Note:
                self.print_note(event)
            elif t == EventType.EndOfTie:
                self.print_end_of_tie_op(event)
            elif t == EventType.Label:
                self.print_seq_loop_label(event)
            elif t == EventType.LoopEnd:
                self.print_byte("GOTO")
                self.print_word("%s_%u_B%u" % (label, self.agb_track, loop_end_block_num))
                self.print_seq_loop_label(event)
            elif t == EventType.LoopEndBegin:
                self.print_byte("GOTO")
                self.print_word("%s_%u_B%u" % (label, self.agb_track, loop_end_block_num))
                self.print_seq_loop_label(event)
                loop_end_block_num = self.block_num
            elif t == EventType.LoopBegin:
                self.print_seq_loop_label(event)
                loop_end_block_num = self.block_num
            elif t == EventType.WholeNoteMark:
                if event.param2 & 0x80000000:
                    self.write("%s_%u_%03u:\n" % (label, self.agb_track, event.param2 & 0x7FFFFFFF))
                    self.reset_track_vars()
                    self.in_pattern = True
                self.print_wait(event.time)
            elif t == EventType.Pattern:
                self.print_byte("PATT")
                self.print_word("%s_%u_%03u" % (label, self.agb_track, u32(event.param2)))
                while not is_pattern_boundary(events[i + 1].type):
                    i += 1
                self.reset_track_vars()
            elif t == EventType.Tempo:
                quotient = _to_float32(60000000.0 / float(event.param2))
                rounded = math.floor(quotient + 0.5) if quotient >= 0 else math.ceil(quotient - 0.5)
                self.print_byte("TEMPO , %s*%s_tbs/2" % (fmt_u(int(rounded)), label))
                self.print_wait(event.time)
            elif t == EventType.InstrumentChange:
                self.print_op(event.time, "VOICE ", "%u" % event.param1)
            elif t == EventType.PitchBend:
                self.print_op(event.time, "BEND  ", "c_v" + fmt_plus_d(event.param2 - 64))
            elif t == EventType.Controller:
                self.print_controller_op(event)
            else:
                self.print_wait(event.time)
            i += 1

        self.print_byte("FINE")

    def print_agb_footer(self):
        label = self.asm_label
        track_count = self.agb_track - 1
        self.write("\n@******************************************************@\n")
        self.write("\t.align\t2\n")
        self.write("\n%s:\n" % label)
        self.write("\t.byte\t%s\t@ NumTrks\n" % fmt_u(track_count))
        self.write("\t.byte\t%u\t@ NumBlks\n" % 0)
        self.write("\t.byte\t%s_pri\t@ Priority\n" % label)
        self.write("\t.byte\t%s_rev\t@ Reverb.\n" % label)
        self.write("\n")
        self.write("\t.word\t%s_grp\n" % label)
        self.write("\n")
        for i in range(1, track_count + 1):
            self.write("\t.word\t%s_%u\n" % (label, i))
        self.write("\n\t.end\n")


def _to_float32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


# ---------------------------------------------------------------------------
# main.cpp
# ---------------------------------------------------------------------------

USAGE = (
    "Usage: MID2AGB name [options]\n"
    "\n"
    "    input_file  filename(.mid) of MIDI file\n"
    "   output_file  filename(.s) for AGB file (default:input_file)\n"
    "\n"
    "options  -L???  label for assembler (default:output_file)\n"
    "         -V???  master volume (default:127)\n"
    "         -G???  voice group number (default:0)\n"
    "         -P???  priority (default:0)\n"
    "         -R???  reverb (default:off)\n"
    "            -X  48 clocks/beat (default:24 clocks/beat)\n"
    "            -E  exact gate-time\n"
    "            -N  no compression\n"
)


def print_usage():
    sys.stdout.write(USAGE)
    sys.exit(1)


def strip_extension(s):
    pos = s.rfind(".")
    if pos > 0:
        s = s[:pos]
    return s


def get_extension(s):
    pos = s.rfind(".")
    if pos > 0:
        return s[pos + 1:]
    return ""


def base_name(s):
    pos_after_slash = max(s.rfind("/"), s.rfind("\\"))
    pos_after_slash = 0 if pos_after_slash < 0 else pos_after_slash + 1
    dot_pos = s.find(".", pos_after_slash)
    if dot_pos > pos_after_slash:
        s = s[pos_after_slash:dot_pos]
    return s


def stoi(text):
    import re
    m = re.match(r"\s*([+-]?\d+)", text)
    if not m:
        sys.stderr.write("libc++abi: terminating due to uncaught exception of type std::invalid_argument: stoi: no conversion\n")
        sys.exit(134)
    value = int(m.group(1))
    if value > 0x7FFFFFFF or value < -0x80000000:
        sys.stderr.write("libc++abi: terminating due to uncaught exception of type std::out_of_range: stoi: out of range\n")
        sys.exit(134)
    return value


def main(argv):
    conv = Mid2Agb()
    input_filename = ""
    output_filename = ""

    i = 1
    argc = len(argv)
    while i < argc:
        option = argv[i]
        if option.startswith("-") and len(option) > 1:
            letter = option[1].upper()

            def get_argument():
                nonlocal i
                if len(option.encode()) >= 3:
                    return option[2:]
                if i + 1 < argc:
                    i += 1
                    return argv[i]
                return None

            if letter == "E":
                conv.exact_gate_time = True
            elif letter in ("G", "L", "P", "R", "V"):
                arg = get_argument()
                if arg is None:
                    print_usage()
                if letter == "G":
                    conv.voice_group = stoi(arg)
                elif letter == "L":
                    conv.asm_label = arg
                elif letter == "P":
                    conv.priority = stoi(arg)
                elif letter == "R":
                    conv.reverb = stoi(arg)
                else:
                    conv.master_volume = stoi(arg)
            elif letter == "N":
                conv.compression_enabled = False
            elif letter == "X":
                conv.clocks_per_beat = 2
            else:
                print_usage()
        else:
            if not input_filename:
                input_filename = option
            elif not output_filename:
                output_filename = option
            else:
                print_usage()
        i += 1

    try:
        if not input_filename:
            print_usage()

        if get_extension(input_filename) != "mid":
            raise_error('input filename extension is not "mid"')

        if not output_filename:
            output_filename = strip_extension(input_filename) + ".s"

        if get_extension(output_filename) != "s":
            raise_error('output filename extension is not "s"')

        if not conv.asm_label:
            conv.asm_label = base_name(output_filename)

        try:
            with open(input_filename, "rb") as f:
                conv.data = f.read()
        except OSError:
            raise_error('failed to open "%s" for reading' % input_filename)

        try:
            out_file = open(output_filename, "w", newline="\n", encoding="latin-1")
        except OSError:
            raise_error('failed to open "%s" for writing' % output_filename)

        with out_file:
            try:
                conv.read_midi_file_header()
                conv.print_agb_header()
                conv.read_midi_tracks()
                conv.print_agb_footer()
            finally:
                # Like the C++ version, whatever was generated before an error
                # still ends up in the output file.
                out_file.write("".join(conv.out))
    except MidiError as e:
        sys.stderr.write("error: %s\n" % e)
        sys.exit(1)
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
