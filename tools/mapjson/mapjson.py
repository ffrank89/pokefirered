#!/usr/bin/env python3
# Python port of mapjson (mapjson.cpp)
#
# Generates assembly and C header files from the map, map group and layout
# JSON files in data/maps and data/layouts.
#
# Usage:
#   mapjson.py map <game-version> <map_file> <layouts_file> <output_dir>
#   mapjson.py groups <game-version> <groups_file> <output_asm_dir> <output_c_dir>
#   mapjson.py layouts <game-version> <layouts_file> <output_asm_dir> <output_c_dir>
#   mapjson.py event_constants <game-version> <map_file> [additional_map_files] <output_ids_file>

import json
import signal
import sys

INT_MAX = 0x7FFFFFFF

version = ""
sep = ""


def fatal_error(msg):
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# JSON helpers emulating json11 semantics
# ---------------------------------------------------------------------------

class JsonError(Exception):
    pass


def _reject_constant(name):
    raise JsonError("invalid literal %s" % name)


def parse_json(text):
    """Parses like json11: short integers are ints, everything else numeric is a double.
    Returns (value, error). A null result is treated as a parse failure by callers."""
    try:
        value = json.loads(text,
                           parse_int=lambda s: int(s) if len(s) <= 9 else float(s),
                           parse_constant=_reject_constant)
        return value, ""
    except (ValueError, JsonError) as e:
        return None, str(e)


def get(data, field):
    if isinstance(data, dict):
        return data.get(field)
    return None


def array_items(value):
    return value if isinstance(value, list) else []


def has_key(value, field):
    return isinstance(value, dict) and field in value


def json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def json_equal(a, b):
    ta, tb = json_type(a), json_type(b)
    if ta != tb:
        return False
    if ta == "array":
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if ta == "object":
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return a == b


def int_value(value):
    # static_cast<int>(double) truncates toward zero
    return int(value)


def json_to_string(data, field="", silent=False):
    value = get(data, field) if field else data
    t = json_type(value)
    output = ""
    if t == "string":
        output = value
    elif t == "number":
        output = str(int_value(value))
    elif t == "bool":
        output = "TRUE" if value else "FALSE"
    elif t == "null":
        output = ""
    else:
        if not silent:
            s = ("Value for '" + field + "'") if field else "JSON field"
            fatal_error("%s is unexpected type; expected string, number, or bool.\n" % s)

    if not silent and not output:
        s = ("Value for '" + field + "'") if field else "JSON field"
        fatal_error("%s cannot be empty.\n" % s)

    return output


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def read_text_file(filepath):
    try:
        with open(filepath, "rb") as f:
            return f.read().decode("utf-8", "surrogateescape")
    except OSError:
        fatal_error("Cannot open file %s for reading.\n" % filepath)


def write_text_file(filepath, text):
    try:
        with open(filepath, "wb") as f:
            f.write(text.encode("utf-8", "surrogateescape"))
    except OSError:
        fatal_error("Cannot open file %s for writing.\n" % filepath)


def get_generated_warning(filename, is_asm):
    comment = "@" if is_asm else "//"
    return "%s\n%s DO NOT MODIFY THIS FILE! It is auto-generated from %s\n%s\n\n" % (
        comment, comment, filename, comment)


def get_include_guard_start(name):
    return "#ifndef GUARD_%s_H\n#define GUARD_%s_H\n\n" % (name, name)


def get_include_guard_end(name):
    return "#endif // GUARD_%s_H\n" % name


# ---------------------------------------------------------------------------
# Map files
# ---------------------------------------------------------------------------

def generate_map_header_text(map_data, layouts_data):
    map_layout_id = json_to_string(map_data, "layout")

    matched = [layout for layout in array_items(get(layouts_data, "layouts"))
               if map_layout_id == json_to_string(layout, "id", True)]

    if len(matched) != 1:
        fatal_error("Failed to find matching layout for %s.\n" % map_layout_id)

    layout = matched[0]
    text = []

    map_name = json_to_string(map_data, "name")
    text.append(get_generated_warning("data/maps/" + map_name + "/map.json", True))

    text.append("%s:\n\t.4byte %s\n" % (map_name, json_to_string(layout, "name")))

    if has_key(map_data, "shared_events_map"):
        text.append("\t.4byte %s_MapEvents\n" % json_to_string(map_data, "shared_events_map"))
    else:
        text.append("\t.4byte %s_MapEvents\n" % map_name)

    if has_key(map_data, "shared_scripts_map"):
        text.append("\t.4byte %s_MapScripts\n" % json_to_string(map_data, "shared_scripts_map"))
    else:
        text.append("\t.4byte %s_MapScripts\n" % map_name)

    if (has_key(map_data, "connections")
            and len(array_items(get(map_data, "connections"))) > 0
            and json_to_string(map_data, "connections_no_include", True) != "TRUE"):
        text.append("\t.4byte %s_MapConnections\n" % map_name)
    else:
        text.append("\t.4byte NULL\n")

    text.append("\t.2byte %s\n" % json_to_string(map_data, "music"))
    text.append("\t.2byte %s\n" % json_to_string(layout, "id"))
    text.append("\t.byte %s\n" % json_to_string(map_data, "region_map_section"))
    text.append("\t.byte %s\n" % json_to_string(map_data, "requires_flash"))
    text.append("\t.byte %s\n" % json_to_string(map_data, "weather"))
    text.append("\t.byte %s\n" % json_to_string(map_data, "map_type"))

    if version != "firered":
        text.append("\t.2byte 0\n")

    if version == "ruby":
        text.append("\t.byte %s\n" % json_to_string(map_data, "show_map_name"))
    elif version in ("emerald", "firered"):
        text.append("\tmap_header_flags allow_cycling=%s, " % json_to_string(map_data, "allow_cycling"))
        text.append("allow_escaping=%s, " % json_to_string(map_data, "allow_escaping"))
        text.append("allow_running=%s, " % json_to_string(map_data, "allow_running"))
        text.append("show_map_name=%s\n" % json_to_string(map_data, "show_map_name"))

    if version == "firered":
        text.append("\t.byte %s\n" % json_to_string(map_data, "floor_number"))

    text.append("\t.byte %s\n\n" % json_to_string(map_data, "battle_scene"))
    return "".join(text)


def generate_map_connections_text(map_data):
    if get(map_data, "connections") is None:
        return "\n"

    map_name = json_to_string(map_data, "name")
    text = [get_generated_warning("data/maps/" + map_name + "/map.json", True)]
    text.append("%s_MapConnectionsList:\n" % map_name)

    connections = array_items(get(map_data, "connections"))
    for connection in connections:
        text.append("\tconnection %s, %s, %s\n" % (
            json_to_string(connection, "direction"),
            json_to_string(connection, "offset"),
            json_to_string(connection, "map")))

    text.append("\n%s_MapConnections:\n" % map_name)
    text.append("\t.4byte %d\n" % len(connections))
    text.append("\t.4byte %s_MapConnectionsList\n\n" % map_name)
    return "".join(text)


def generate_map_events_text(map_data):
    if has_key(map_data, "shared_events_map"):
        return "\n"

    map_name = json_to_string(map_data, "name")
    text = [get_generated_warning("data/maps/" + map_name + "/map.json", True)]
    text.append("\t.align 2\n\n")

    object_events = array_items(get(map_data, "object_events"))
    if object_events:
        objects_label = map_name + "_ObjectEvents"
        text.append(objects_label + ":\n")
        for i, obj_event in enumerate(object_events):
            event_type = json_to_string(obj_event, "type", True)
            # If no type field is present, assume it's a regular object event.
            if event_type == "" or event_type == "object":
                fields = ("graphics_id", "x", "y", "elevation", "movement_type", "movement_range_x",
                          "movement_range_y", "trainer_type", "trainer_sight_or_berry_tree_id",
                          "script", "flag")
                text.append("\tobject_event %d, %s\n" % (
                    i + 1, ", ".join(json_to_string(obj_event, f) for f in fields)))
            elif event_type == "clone":
                fields = ("graphics_id", "x", "y", "target_local_id", "target_map")
                text.append("\tclone_event %d, %s\n" % (
                    i + 1, ", ".join(json_to_string(obj_event, f) for f in fields)))
            else:
                fatal_error("Unknown object event type '%s'. Expected 'object' or 'clone'.\n" % event_type)
        text.append("\n")
    else:
        objects_label = "NULL"

    warp_events = array_items(get(map_data, "warp_events"))
    if warp_events:
        warps_label = map_name + "_MapWarps"
        text.append(warps_label + ":\n")
        for warp_event in warp_events:
            fields = ("x", "y", "elevation", "dest_warp_id", "dest_map")
            text.append("\twarp_def %s\n" % ", ".join(json_to_string(warp_event, f) for f in fields))
        text.append("\n")
    else:
        warps_label = "NULL"

    coord_events = array_items(get(map_data, "coord_events"))
    if coord_events:
        coords_label = map_name + "_MapCoordEvents"
        text.append(coords_label + ":\n")
        for coord_event in coord_events:
            event_type = json_to_string(coord_event, "type")
            if event_type == "trigger":
                fields = ("x", "y", "elevation", "var", "var_value", "script")
                text.append("\tcoord_event %s\n" % ", ".join(json_to_string(coord_event, f) for f in fields))
            elif event_type == "weather":
                fields = ("x", "y", "elevation", "weather")
                text.append("\tcoord_weather_event %s\n" % ", ".join(json_to_string(coord_event, f) for f in fields))
            else:
                fatal_error("Unknown coord event type '%s'. Expected 'trigger' or 'weather'.\n" % event_type)
        text.append("\n")
    else:
        coords_label = "NULL"

    bg_events = array_items(get(map_data, "bg_events"))
    if bg_events:
        bgs_label = map_name + "_MapBGEvents"
        text.append(bgs_label + ":\n")
        for bg_event in bg_events:
            event_type = json_to_string(bg_event, "type")
            if event_type == "sign":
                fields = ("x", "y", "elevation", "player_facing_dir", "script")
                text.append("\tbg_sign_event %s\n" % ", ".join(json_to_string(bg_event, f) for f in fields))
            elif event_type == "hidden_item":
                fields = ["x", "y", "elevation", "item", "flag"]
                if version == "firered":
                    fields += ["quantity", "underfoot"]
                text.append("\tbg_hidden_item_event %s\n" % ", ".join(json_to_string(bg_event, f) for f in fields))
            elif event_type == "secret_base":
                fields = ("x", "y", "elevation", "secret_base_id")
                text.append("\tbg_secret_base_event %s\n" % ", ".join(json_to_string(bg_event, f) for f in fields))
            else:
                fatal_error("Unknown bg event type '%s'. Expected 'sign', 'hidden_item', or 'secret_base'.\n"
                            % event_type)
        text.append("\n")
    else:
        bgs_label = "NULL"

    text.append("%s_MapEvents::\n" % map_name)
    text.append("\tmap_events %s, %s, %s, %s\n\n" % (objects_label, warps_label, coords_label, bgs_label))
    return "".join(text)


def strip_trailing_separator(filename):
    if filename and filename[-1] in "/\\":
        filename = filename[:-1]
    return filename


def infer_separator(filename):
    global sep
    dir_pos = max(filename.rfind("/"), filename.rfind("\\"))
    sep = filename[dir_pos] if dir_pos >= 0 else "/"


def file_parent(filename):
    dir_pos = max(filename.rfind("/"), filename.rfind("\\"))
    return filename[:dir_pos + 1]


def parse_or_die(text):
    data, err = parse_json(text)
    if data is None:
        fatal_error("%s\n" % err)
    return data


def process_map(map_filepath, layouts_filepath, output_dir):
    map_data = parse_or_die(read_text_file(map_filepath))
    layouts_data = parse_or_die(read_text_file(layouts_filepath))

    header_text = generate_map_header_text(map_data, layouts_data)
    events_text = generate_map_events_text(map_data)
    connections_text = generate_map_connections_text(map_data)

    out_dir = strip_trailing_separator(output_dir) + sep
    write_text_file(out_dir + "header.inc", header_text)
    write_text_file(out_dir + "events.inc", events_text)
    write_text_file(out_dir + "connections.inc", connections_text)


def process_event_constants(map_filepaths, output_ids_file):
    warning = get_generated_warning("data/maps/*/map.json", False)
    guard_name = "CONSTANTS_MAP_EVENT_IDS"
    text = [get_include_guard_start(guard_name), warning]

    for filepath in map_filepaths:
        map_data, err = parse_json(read_text_file(filepath))
        if map_data is None:
            fatal_error("Failed to read '%s' while generating map event constants: %s\n" % (filepath, err))

        map_id = json_to_string(map_data, "id")

        ids = []
        for i, obj_event in enumerate(array_items(get(map_data, "object_events"))):
            if has_key(obj_event, "local_id"):
                ids.append("#define %s %d\n" % (json_to_string(obj_event, "local_id"), i + 1))
        for i, warp_event in enumerate(array_items(get(map_data, "warp_events"))):
            if has_key(warp_event, "warp_id"):
                ids.append("#define %s %d\n" % (json_to_string(warp_event, "warp_id"), i))
        # Only output if we found any IDs
        if ids:
            text.append("// %s\n%s\n" % (map_id, "".join(ids)))

    text.append(get_include_guard_end(guard_name))
    write_text_file(output_ids_file, "".join(text))


# ---------------------------------------------------------------------------
# Map groups
# ---------------------------------------------------------------------------

def generate_groups_text(groups_data):
    text = [get_generated_warning("data/maps/map_groups.json", True)]
    group_order = array_items(get(groups_data, "group_order"))

    for key in group_order:
        group = json_to_string(key)
        text.append(group + "::\n")
        for map_name in array_items(get(groups_data, group)):
            text.append("\t.4byte %s\n" % json_to_string(map_name))
        text.append("\n")

    text.append("\t.align 2\ngMapGroups::\n")
    for group in group_order:
        text.append("\t.4byte %s\n" % json_to_string(group))
    text.append("\n")
    return "".join(text)


def _all_map_names(groups_data):
    names = []
    for group in array_items(get(groups_data, "group_order")):
        names.extend(array_items(get(groups_data, json_to_string(group))))
    return names


def generate_connections_text(groups_data, include_path):
    map_names = _all_map_names(groups_data)
    include_order = array_items(get(groups_data, "connections_include_order"))

    if include_order:
        def order_key(name):
            for i, entry in enumerate(include_order):
                if json_equal(entry, name):
                    return i
            return INT_MAX
        map_names.sort(key=order_key)

    text = [get_generated_warning("data/maps/map_groups.json", True)]
    for map_name in map_names:
        text.append('\t.include "%s/%s/connections.inc"\n' % (include_path, json_to_string(map_name)))
    return "".join(text)


def generate_headers_text(groups_data, include_path):
    text = [get_generated_warning("data/maps/map_groups.json", True)]
    for map_name in _all_map_names(groups_data):
        text.append('\t.include "%s/%s/header.inc"\n' % (include_path, json_to_string(map_name)))
    return "".join(text)


def generate_events_text(groups_data, include_path):
    names = [json_to_string(m) for m in _all_map_names(groups_data)]
    text = [get_generated_warning(include_path + "/map_groups.json", True)]
    for map_name in names:
        text.append('\t.include "%s/%s/events.inc"\n' % (include_path, map_name))
    return "".join(text)


def generate_map_constants_text(groups_filepath, groups_data):
    file_dir = file_parent(groups_filepath) + sep
    guard_name = "CONSTANTS_MAP_GROUPS"
    text = [get_include_guard_start(guard_name), get_generated_warning("data/maps/map_groups.json", False)]

    group_num = 0
    for group in array_items(get(groups_data, "group_order")):
        group_name = json_to_string(group)
        text.append("// %s\n" % group_name)
        map_ids = []
        max_length = 0

        for map_name in array_items(get(groups_data, group_name)):
            map_filepath = file_dir + json_to_string(map_name) + sep + "map.json"
            map_data, err = parse_json(read_text_file(map_filepath))
            if map_data is None:
                fatal_error("%s: %s\n" % (map_filepath, err))
            map_id = json_to_string(map_data, "id", True)
            map_ids.append(map_id)
            max_length = max(max_length, len(map_id.encode("utf-8", "surrogateescape")))

        for map_id_num, map_id in enumerate(map_ids):
            padding = " " * (max_length - len(map_id.encode("utf-8", "surrogateescape")) + 1)
            text.append("#define %s%s(%d | (%d << 8))\n" % (map_id, padding, map_id_num, group_num))
        text.append("\n")
        group_num += 1

    text.append("#define MAP_GROUPS_COUNT %d\n\n" % group_num)
    text.append(get_include_guard_end(guard_name))
    return "".join(text)


def process_groups(groups_filepath, output_asm, output_c):
    output_asm = strip_trailing_separator(output_asm)
    output_c = strip_trailing_separator(output_c)

    groups_data = parse_or_die(read_text_file(groups_filepath))

    groups_text = generate_groups_text(groups_data)
    connections_text = generate_connections_text(groups_data, output_asm)
    headers_text = generate_headers_text(groups_data, output_asm)
    events_text = generate_events_text(groups_data, output_asm)
    map_header_text = generate_map_constants_text(groups_filepath, groups_data)

    write_text_file(output_asm + sep + "groups.inc", groups_text)
    write_text_file(output_asm + sep + "connections.inc", connections_text)
    write_text_file(output_asm + sep + "headers.inc", headers_text)
    write_text_file(output_asm + sep + "events.inc", events_text)
    write_text_file(output_c + sep + "map_groups.h", map_header_text)


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def generate_layout_headers_text(layouts_data):
    text = [get_generated_warning("data/layouts/layouts.json", True)]

    for layout in array_items(get(layouts_data, "layouts")):
        if json_equal(layout, {}):
            continue
        layout_name = json_to_string(layout, "name")
        border_label = layout_name + "_Border"
        blockdata_label = layout_name + "_Blockdata"
        text.append("%s::\n" % border_label)
        text.append('\t.incbin "%s"\n\n' % json_to_string(layout, "border_filepath"))
        text.append("%s::\n" % blockdata_label)
        text.append('\t.incbin "%s"\n\n' % json_to_string(layout, "blockdata_filepath"))
        text.append("\t.align 2\n")
        text.append("%s::\n" % layout_name)
        text.append("\t.4byte %s\n" % json_to_string(layout, "width"))
        text.append("\t.4byte %s\n" % json_to_string(layout, "height"))
        text.append("\t.4byte %s\n" % border_label)
        text.append("\t.4byte %s\n" % blockdata_label)
        text.append("\t.4byte %s\n" % json_to_string(layout, "primary_tileset"))
        text.append("\t.4byte %s\n" % json_to_string(layout, "secondary_tileset"))
        if version == "firered":
            text.append("\t.byte %s\n" % json_to_string(layout, "border_width"))
            text.append("\t.byte %s\n" % json_to_string(layout, "border_height"))
            text.append("\t.2byte 0\n")
        text.append("\n")

    return "".join(text)


def generate_layouts_table_text(layouts_data):
    text = [get_generated_warning("data/layouts/layouts.json", True)]
    text.append("\t.align 2\n%s::\n" % json_to_string(layouts_data, "layouts_table_label"))
    for layout in array_items(get(layouts_data, "layouts")):
        layout_name = json_to_string(layout, "name", True)
        if not layout_name:
            layout_name = "NULL"
        text.append("\t.4byte %s\n" % layout_name)
    return "".join(text)


def generate_layouts_constants_text(layouts_data):
    guard_name = "CONSTANTS_LAYOUTS"
    text = [get_include_guard_start(guard_name), get_generated_warning("data/layouts/layouts.json", False)]
    for i, layout in enumerate(array_items(get(layouts_data, "layouts")), start=1):
        if not json_equal(layout, {}):
            text.append("#define %s %d\n" % (json_to_string(layout, "id"), i))
    text.append(get_include_guard_end(guard_name))
    return "".join(text)


def process_layouts(layouts_filepath, output_asm, output_c):
    output_asm = strip_trailing_separator(output_asm) + sep
    output_c = strip_trailing_separator(output_c) + sep

    layouts_data = parse_or_die(read_text_file(layouts_filepath))

    layout_headers_text = generate_layout_headers_text(layouts_data)
    layouts_table_text = generate_layouts_table_text(layouts_data)
    layouts_constants_text = generate_layouts_constants_text(layouts_data)

    write_text_file(output_asm + "layouts.inc", layout_headers_text)
    write_text_file(output_asm + "layouts_table.inc", layouts_table_text)
    write_text_file(output_c + "layouts.h", layouts_constants_text)


def main(argv):
    global version

    if len(argv) < 3:
        fatal_error("USAGE: mapjson <mode> <game-version> [options]\n")

    version = argv[2]
    if version not in ("emerald", "ruby", "firered"):
        fatal_error("ERROR: <game-version> must be 'emerald', 'firered', or 'ruby'.\n")

    mode = argv[1]
    if mode == "map":
        if len(argv) != 6:
            fatal_error("USAGE: mapjson map <game-version> <map_file> <layouts_file> <output_dir>\n")
        infer_separator(argv[3])
        process_map(argv[3], argv[4], argv[5])
    elif mode == "groups":
        if len(argv) != 6:
            fatal_error("USAGE: mapjson groups <game-version> <groups_file> <output_asm_dir> <output_c_dir>\n")
        infer_separator(argv[3])
        process_groups(argv[3], argv[4], argv[5])
    elif mode == "layouts":
        if len(argv) != 6:
            fatal_error("USAGE: mapjson layouts <game-version> <layouts_file> <output_asm_dir> <output_c_dir>\n")
        infer_separator(argv[3])
        process_layouts(argv[3], argv[4], argv[5])
    elif mode == "event_constants":
        if len(argv) < 5:
            fatal_error("USAGE: mapjson event_constants <game-version> <map_file> [additional_map_files] "
                        "<output_ids_file>")
        infer_separator(argv[3])
        process_event_constants(argv[3:-1], argv[-1])
    else:
        fatal_error("ERROR: <mode> must be 'layouts', 'map', 'event_constants', or 'groups'.\n")
    return 0


if __name__ == "__main__":
    # Exit quietly if the reading end of a pipe goes away, like a C program would.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
