"""Integer constants from the game's C headers, importable by name.

    from gba.constants import *     # MAP_ROUTE1, SPECIES_PIDGEY, FLAG_..., TRUE, ...

Values are read from the headers in include/ (and from the map and encounter
JSON files for MAP_* and ENCOUNTER_CHANCE_*), so they always match the C build.
"""

import json

from pokefirered import constants as _c, repo_path as _repo_path

_HEADERS = [
    "include/gba/defines.h",
    "include/constants/global.h",
    "include/constants/species.h",
    "include/constants/opponents.h",
    "include/constants/flags.h",
    "include/constants/vars.h",
    "include/constants/items.h",
    "include/constants/abilities.h",
    "include/constants/pokemon.h",
    "include/constants/game_stat.h",
    "include/constants/quest_log.h",
    "include/constants/metatile_behaviors.h",
    "include/global.fieldmap.h",
    "include/wild_encounter.h",
    "include/pokemon.h",
]


def encounter_chances():
    """The ENCOUNTER_CHANCE_* macros that src/data/wild_encounters.json.txt generates."""
    with open(_repo_path("src", "data", "wild_encounters.json"), encoding="utf-8") as f:
        data = json.load(f)
    values = {}
    for group in data["wild_encounter_groups"]:
        if not group.get("for_maps"):
            continue
        for field in group["fields"]:
            kind = field["type"].upper()
            rates = field["encounter_rates"]
            if "groups" not in field:
                total = 0
                for slot, rate in enumerate(rates):
                    total += rate
                    values["ENCOUNTER_CHANCE_%s_SLOT_%d" % (kind, slot)] = total
                values["ENCOUNTER_CHANCE_%s_TOTAL" % kind] = total
            else:
                for name, indices in field["groups"].items():
                    total = 0
                    for index in indices:
                        total += rates[index]
                        values["ENCOUNTER_CHANCE_%s_%s_SLOT_%d" % (kind, name.upper(), index)] = total
                    values["ENCOUNTER_CHANCE_%s_%s_TOTAL" % (kind, name.upper())] = total
    return values


def all_constants():
    table = _c.Constants(*_HEADERS)
    values = {}
    for name in list(table._exprs):
        value = table.get(name)
        if value is not None:
            values[name] = value
    values.update(_c.maps())
    values.update(encounter_chances())
    return values


_values = all_constants()
globals().update(_values)
__all__ = sorted(_values)
