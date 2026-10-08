"""Python port of src/wild_encounter.c.

src/wild_encounter.c is still what gets compiled into the ROM. This module
is a line-by-line port of it that reads the same encounter data
(src/data/wild_encounters.json, the file the C build generates
src/data/wild_encounters.h from) and makes the same sequence of random number
calls, so for the same game state it produces the same encounters as the game.

The game state that the C code reads from global variables (the save block,
event flags and vars, the player's party, the roamer, the RNG...) is passed in
as a GameState object. Functions that the C code calls in other files are
modeled to the extent that they affect encounters; see the GameState and
EncounterOutcome classes.

Example:

    from pokefirered.wild_encounter import GameState, WildEncounters, metatile_attributes, TILE_ENCOUNTER_LAND

    state = GameState(map="MAP_ROUTE1")
    state.rng.seed(0x1234)
    encounters = WildEncounters(state)
    grass = metatile_attributes(behavior=0x02, encounter_type=TILE_ENCOUNTER_LAND)
    for step in range(100):
        if encounters.try_standard_wild_encounter(grass):
            print(state.battle)
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict

from . import constants, repo_path
from .pokemon import (Pokemon, create_mon, create_mon_with_nature, get_unown_letter,
                      NUM_NATURES)
from .rng import Rng, iso_randomize2

# include/wild_encounter.h
LAND_WILD_COUNT = 12
WATER_WILD_COUNT = 5
ROCK_WILD_COUNT = 5
FISH_WILD_COUNT = 10
NUM_ALTERING_CAVE_TABLES = 9

MAX_ENCOUNTER_RATE = 1600
HEADER_NONE = 0xFFFF

# include/global.fieldmap.h
TILE_ENCOUNTER_NONE = 0
TILE_ENCOUNTER_LAND = 1
TILE_ENCOUNTER_WATER = 2

METATILE_ATTRIBUTE_BEHAVIOR = 0
METATILE_ATTRIBUTE_TERRAIN = 1
METATILE_ATTRIBUTE_2 = 2
METATILE_ATTRIBUTE_3 = 3
METATILE_ATTRIBUTE_ENCOUNTER_TYPE = 4
METATILE_ATTRIBUTE_5 = 5
METATILE_ATTRIBUTE_LAYER_TYPE = 6
METATILE_ATTRIBUTE_7 = 7
METATILE_ATTRIBUTE_COUNT = 8

# src/fieldmap.c
_METATILE_ATTR_MASKS = (0x000001ff, 0x00003e00, 0x0003c000, 0x00fc0000,
                        0x07000000, 0x18000000, 0x60000000, 0x80000000)
_METATILE_ATTR_SHIFTS = (0, 9, 14, 18, 24, 27, 29, 31)

PLAYER_AVATAR_FLAG_ON_FOOT = 1 << 0
PLAYER_AVATAR_FLAG_MACH_BIKE = 1 << 1
PLAYER_AVATAR_FLAG_ACRO_BIKE = 1 << 2
PLAYER_AVATAR_FLAG_SURFING = 1 << 3
PLAYER_AVATAR_FLAG_UNDERWATER = 1 << 4

# include/constants/items.h rods
OLD_ROD = 0
GOOD_ROD = 1
SUPER_ROD = 2

QL_STATE_PLAYBACK = 2

PARTY_SIZE = 6

WILD_AREA_LAND = 0
WILD_AREA_WATER = 1
WILD_AREA_ROCKS = 2
WILD_AREA_FISHING = 3

WILD_CHECK_REPEL = 0x1
WILD_CHECK_KEEN_EYE = 0x2

_TANOBY_CHAMBERS = (
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER",
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_LIPTOO_CHAMBER",
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_WEEPTH_CHAMBER",
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_DILFORD_CHAMBER",
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_SCUFIB_CHAMBER",
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_RIXY_CHAMBER",
    "MAP_SEVEN_ISLAND_TANOBY_RUINS_VIAPOIS_CHAMBER",
)

UNOWN_LETTER_SLOTS = (
    #  A   A   A   A   A   A   A   A   A   A   A   ?
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 27),
    #  C   C   C   D   D   D   H   H   H   U   U   O
    (2, 2, 2, 3, 3, 3, 7, 7, 7, 20, 20, 14),
    #  N   N   N   N   S   S   S   S   I   I   E   E
    (13, 13, 13, 13, 18, 18, 18, 18, 8, 8, 4, 4),
    #  P   P   L   L   J   J   R   R   R   Q   Q   Q
    (15, 15, 11, 11, 9, 9, 17, 17, 17, 16, 16, 16),
    #  Y   Y   T   T   G   G   G   F   F   F   K   K
    (24, 24, 19, 19, 6, 6, 6, 5, 5, 5, 10, 10),
    #  V   V   V   W   W   W   X   X   M   M   B   B
    (21, 21, 21, 22, 22, 22, 23, 23, 12, 12, 1, 1),
    #  Z   Z   Z   Z   Z   Z   Z   Z   Z   Z   Z   !
    (25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 26),
)


def map_group(map_value):
    return map_value >> 8


def map_num(map_value):
    return map_value & 0xFF


def extract_metatile_attribute(attributes, attribute_type):
    """ExtractMetatileAttribute() (src/fieldmap.c)"""
    if attribute_type >= METATILE_ATTRIBUTE_COUNT:  # METATILE_ATTRIBUTES_ALL
        return attributes
    return (attributes & _METATILE_ATTR_MASKS[attribute_type]) >> _METATILE_ATTR_SHIFTS[attribute_type]


def metatile_attributes(behavior=0, encounter_type=TILE_ENCOUNTER_NONE):
    """Builds a metatile attribute word with the given behavior and encounter type."""
    return ((behavior << _METATILE_ATTR_SHIFTS[METATILE_ATTRIBUTE_BEHAVIOR])
            & _METATILE_ATTR_MASKS[METATILE_ATTRIBUTE_BEHAVIOR]) | \
           ((encounter_type << _METATILE_ATTR_SHIFTS[METATILE_ATTRIBUTE_ENCOUNTER_TYPE])
            & _METATILE_ATTR_MASKS[METATILE_ATTRIBUTE_ENCOUNTER_TYPE])


def metatile_behavior_is_bridge(behavior):
    """MetatileBehavior_IsBridge() always returns FALSE in FireRed/LeafGreen."""
    return False


# ---------------------------------------------------------------------------
# Encounter data (src/data/wild_encounters.json)
# ---------------------------------------------------------------------------

@dataclass
class WildPokemon:
    min_level: int
    max_level: int
    species: str


@dataclass
class WildPokemonInfo:
    encounter_rate: int
    wild_pokemon: List[WildPokemon]


@dataclass
class WildPokemonHeader:
    map: str
    map_group: int
    map_num: int
    base_label: str
    land_mons_info: Optional[WildPokemonInfo] = None
    water_mons_info: Optional[WildPokemonInfo] = None
    rock_smash_mons_info: Optional[WildPokemonInfo] = None
    fishing_mons_info: Optional[WildPokemonInfo] = None


@dataclass
class EncounterTables:
    """gWildMonHeaders and the ENCOUNTER_CHANCE_* slot thresholds for one game version."""
    headers: List[WildPokemonHeader]
    # field type -> cumulative slot thresholds, e.g. "land_mons" -> [20, 40, ...]
    slot_chances: Dict[str, List[int]]
    # (field type, group) -> {slot index: cumulative threshold}, e.g. ("fishing_mons", "good_rod")
    group_slot_chances: Dict[tuple, Dict[int, int]]

    def chance(self, field_type, slot):
        return self.slot_chances[field_type][slot]

    def total(self, field_type):
        return self.slot_chances[field_type][-1]

    def group_chance(self, field_type, group, slot):
        return self.group_slot_chances[(field_type, group)][slot]

    def group_total(self, field_type, group):
        chances = self.group_slot_chances[(field_type, group)]
        return chances[max(chances)]


_FIELD_KEYS = ("land_mons", "water_mons", "rock_smash_mons", "fishing_mons")


def load_encounter_tables(game_version="FIRERED", path=None):
    """Builds the same tables that src/data/wild_encounters.json.txt generates for the C build."""
    if path is None:
        path = repo_path("src", "data", "wild_encounters.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    maps = constants.maps()
    excluded_tag = "LeafGreen" if game_version == "FIRERED" else "FireRed"

    headers = []
    slot_chances = {}
    group_slot_chances = {}

    for group in data["wild_encounter_groups"]:
        if group.get("for_maps"):
            for fld in group["fields"]:
                rates = fld["encounter_rates"]
                if "groups" not in fld:
                    total = 0
                    cumulative = []
                    for rate in rates:
                        total += rate
                        cumulative.append(total)
                    slot_chances[fld["type"]] = cumulative
                else:
                    for group_name, indices in fld["groups"].items():
                        total = 0
                        chances = {}
                        for index in indices:
                            total += rates[index]
                            chances[index] = total
                        group_slot_chances[(fld["type"], group_name)] = chances

        if group["label"] != "gWildMonHeaders":
            continue

        for i, encounter in enumerate(group["encounters"]):
            label = encounter["base_label"]
            if excluded_tag in label:
                continue
            if group.get("for_maps"):
                map_value = maps[encounter["map"]]
                header = WildPokemonHeader(encounter["map"], map_group(map_value), map_num(map_value), label)
            else:
                header = WildPokemonHeader("", 0, i + 1, label)
            for key in _FIELD_KEYS:
                if key in encounter:
                    info = WildPokemonInfo(
                        encounter[key]["encounter_rate"],
                        [WildPokemon(m["min_level"], m["max_level"], m["species"]) for m in encounter[key]["mons"]])
                    setattr(header, key + "_info", info)
            headers.append(header)

    # Terminator entry
    undefined = maps["MAP_UNDEFINED"]
    headers.append(WildPokemonHeader("MAP_UNDEFINED", map_group(undefined), map_num(undefined), ""))
    return EncounterTables(headers, slot_chances, group_slot_chances)


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

@dataclass
class Roamer:
    """The roaming legendary (struct Roamer and sRoamerLocation in src/roamer.c)."""
    species: str
    level: int
    map: str
    personality: int = 0
    ivs: int = 0
    active: bool = True


@dataclass
class EncounterOutcome:
    """What the C code hands over to the battle system."""
    kind: str  # "wild" or "roamer"
    mon: Pokemon

    def __str__(self):
        return "%s battle: %s" % (self.kind, self.mon.describe())


@dataclass
class GameState:
    """The global game state that src/wild_encounter.c reads and writes."""
    map: str = "MAP_ROUTE1"
    game_version: str = "FIRERED"
    rng: Rng = field(default_factory=Rng)
    flags: Set[str] = field(default_factory=set)
    vars: Dict[str, int] = field(default_factory=dict)
    party: List[Pokemon] = field(default_factory=lambda: [Pokemon("SPECIES_BULBASAUR", 5)])
    player_avatar_flags: int = PLAYER_AVATAR_FLAG_ON_FOOT
    player_trainer_id: int = 0
    player_dest_coords: tuple = (0, 0)
    # encounter type of the metatile at player_dest_coords (MapGridGetMetatileAttributeAt)
    metatile_encounter_type_at_dest: int = TILE_ENCOUNTER_NONE
    roamer: Optional[Roamer] = None
    in_union_room: bool = False
    quest_log_state: int = 0
    game_stats: Dict[str, int] = field(default_factory=dict)

    # Outputs
    battle: Optional[EncounterOutcome] = None
    special_var_result: int = 0
    scripts_started: List[str] = field(default_factory=list)

    # Accessors used by the port ---------------------------------------------

    @property
    def location(self):
        value = constants.maps()[self.map]
        return map_group(value), map_num(value)

    def flag_get(self, name):
        return name in self.flags

    def var_get(self, name):
        return self.vars.get(name, 0) & 0xFFFF

    def var_set(self, name, value):
        self.vars[name] = value & 0xFFFF

    def test_player_avatar_flags(self, flags):
        return (self.player_avatar_flags & flags) != 0

    def increment_game_stat(self, name):
        self.game_stats[name] = self.game_stats.get(name, 0) + 1


# ---------------------------------------------------------------------------
# src/wild_encounter.c
# ---------------------------------------------------------------------------

class WildEncounters:
    """The wild encounter system. Holds the C file's static variables
    (sWildEncounterData and sWildEncountersDisabled)."""

    def __init__(self, state, tables=None):
        self.state = state
        self.tables = tables if tables is not None else load_encounter_tables(state.game_version)
        # struct WildEncounterData sWildEncounterData
        self.rng_state = 0
        self.prev_metatile_behavior = 0
        self.encounter_rate_buff = 0
        self.steps_since_last_encounter = 0
        self.ability_effect = 0
        self.lead_mon_held_item = "ITEM_NONE"
        # bool8 sWildEncountersDisabled
        self.wild_encounters_disabled = False

    # -- helpers for C integer types -----------------------------------------

    @staticmethod
    def _u8(v):
        return v & 0xFF

    @staticmethod
    def _u16(v):
        return v & 0xFFFF

    def _random(self):
        return self.state.rng.random()

    def disable_wild_encounters(self, state):
        self.wild_encounters_disabled = bool(state)

    # -- slot selection ---------------------------------------------------------

    def _choose_wild_mon_index_land(self):
        t = self.tables
        rand = self._u8(self._random() % t.total("land_mons"))
        for slot in range(LAND_WILD_COUNT - 1):
            if rand < t.chance("land_mons", slot):
                return slot
        return LAND_WILD_COUNT - 1

    def _choose_wild_mon_index_water_rock(self):
        t = self.tables
        rand = self._u8(self._random() % t.total("water_mons"))
        for slot in range(WATER_WILD_COUNT - 1):
            if rand < t.chance("water_mons", slot):
                return slot
        return WATER_WILD_COUNT - 1

    def _choose_wild_mon_index_fishing(self, rod):
        t = self.tables
        wild_mon_index = 0
        modulus = max(max(t.group_total("fishing_mons", "old_rod"), t.group_total("fishing_mons", "good_rod")),
                      t.group_total("fishing_mons", "super_rod"))
        rand = self._u8(self._random() % modulus)

        if rod == OLD_ROD:
            if rand < t.group_chance("fishing_mons", "old_rod", 0):
                wild_mon_index = 0
            else:
                wild_mon_index = 1
        elif rod == GOOD_ROD:
            lower = 0
            for slot in (2, 3, 4):
                upper = t.group_chance("fishing_mons", "good_rod", slot)
                if lower <= rand < upper:
                    wild_mon_index = slot
                lower = upper
        elif rod == SUPER_ROD:
            lower = 0
            for slot in (5, 6, 7, 8, 9):
                upper = t.group_chance("fishing_mons", "super_rod", slot)
                if lower <= rand < upper:
                    wild_mon_index = slot
                lower = upper
        return wild_mon_index

    def _choose_wild_mon_level(self, info):
        if info.max_level >= info.min_level:
            lo = info.min_level
            hi = info.max_level
        else:
            lo = info.max_level
            hi = info.min_level
        mod = self._u8(hi - lo + 1)
        res = self._u8(self._random() % mod)
        return self._u8(lo + res)

    # -- map lookup -------------------------------------------------------------

    def get_current_map_wild_mon_header_id(self):
        state = self.state
        group, num = state.location
        headers = self.tables.headers
        undefined_group = map_group(constants.maps()["MAP_UNDEFINED"])
        altering_cave = constants.maps()["MAP_SIX_ISLAND_ALTERING_CAVE"]

        i = 0
        while True:
            if headers[i].map_group == undefined_group:
                break
            if headers[i].map_group == group and headers[i].map_num == num:
                if group == map_group(altering_cave) and num == map_num(altering_cave):
                    altering_cave_id = state.var_get("VAR_ALTERING_CAVE_WILD_SET")
                    if altering_cave_id >= NUM_ALTERING_CAVE_TABLES:
                        altering_cave_id = 0
                    i = self._u16(i + altering_cave_id)
                if not self._unlocked_tanoby_or_are_not_in_tanoby():
                    break
                return i
            i = self._u16(i + 1)
        return HEADER_NONE

    def _unlocked_tanoby_or_are_not_in_tanoby(self):
        state = self.state
        if state.flag_get("FLAG_SYS_UNLOCKED_TANOBY_RUINS"):
            return True
        group, num = state.location
        maps = constants.maps()
        if group != map_group(maps["MAP_SEVEN_ISLAND_TANOBY_RUINS_DILFORD_CHAMBER"]):
            return True
        if num not in [map_num(maps[name]) for name in _TANOBY_CHAMBERS]:
            return True
        return False

    # -- creating the wild Pokemon ----------------------------------------------

    def _generate_wild_mon(self, species, level, slot):
        """GenerateWildMon(): creates gEnemyParty[0]."""
        state = self.state
        if species != "SPECIES_UNOWN":
            nature = self._random() % NUM_NATURES
            mon = create_mon_with_nature(state.rng, species, level, nature, state.player_trainer_id)
        else:
            chamber = state.location[1] - map_num(constants.maps()["MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER"])
            chamber = ((chamber + 0x80) & 0xFF) - 0x80  # s8
            personality = self._generate_unown_personality_by_letter(UNOWN_LETTER_SLOTS[chamber][slot])
            mon = create_mon(state.rng, species, level, personality, state.player_trainer_id)
        self.enemy_mon = mon
        return mon

    def _generate_unown_personality_by_letter(self, letter):
        while True:
            # (Random() << 16) | Random(): the first call supplies the high half
            hi = self._random()
            lo = self._random()
            personality = (hi << 16) | lo
            if get_unown_letter(personality) == letter:
                return personality

    def _try_generate_wild_mon(self, info, area, flags):
        slot = 0
        if area == WILD_AREA_LAND:
            slot = self._choose_wild_mon_index_land()
        elif area == WILD_AREA_WATER:
            slot = self._choose_wild_mon_index_water_rock()
        elif area == WILD_AREA_ROCKS:
            slot = self._choose_wild_mon_index_water_rock()
        level = self._choose_wild_mon_level(info.wild_pokemon[slot])
        if flags == WILD_CHECK_REPEL and not self._is_wild_level_allowed_by_repel(level):
            return False
        self._generate_wild_mon(info.wild_pokemon[slot].species, level, slot)
        return True

    def _generate_fishing_encounter(self, info, rod):
        slot = self._choose_wild_mon_index_fishing(rod)
        level = self._choose_wild_mon_level(info.wild_pokemon[slot])
        self._generate_wild_mon(info.wild_pokemon[slot].species, level, slot)
        return info.wild_pokemon[slot].species

    # -- encounter rate -----------------------------------------------------------

    def _do_wild_encounter_rate_dice_roll(self, encounter_rate):
        return self._wild_encounter_random() % MAX_ENCOUNTER_RATE < encounter_rate

    def _do_wild_encounter_rate_test(self, encounter_rate, ignore_ability):
        rate = encounter_rate * 16
        if self.state.test_player_avatar_flags(PLAYER_AVATAR_FLAG_MACH_BIKE | PLAYER_AVATAR_FLAG_ACRO_BIKE):
            rate = rate * 80 // 100
        rate += self.encounter_rate_buff * 16 // 200
        rate = self._apply_flute_encounter_rate_mod(rate)
        rate = self._apply_cleanse_tag_encounter_rate_mod(rate)
        if not ignore_ability:
            if self.ability_effect == 1:
                rate //= 2
            elif self.ability_effect == 2:
                rate *= 2
        if rate > MAX_ENCOUNTER_RATE:
            rate = MAX_ENCOUNTER_RATE
        return self._do_wild_encounter_rate_dice_roll(self._u16(rate))

    def _get_ability_encounter_rate_mod_type(self):
        self.ability_effect = 0
        lead = self.state.party[0] if self.state.party else None
        if lead is not None and not lead.is_egg:
            ability = lead.ability
            if ability == "ABILITY_STENCH":
                self.ability_effect = 1
            elif ability == "ABILITY_ILLUMINATE":
                self.ability_effect = 2
        return self.ability_effect

    def _do_global_wild_encounter_dice_roll(self):
        return (self._random() % 100) < 60

    # -- roamer (src/roamer.c) ----------------------------------------------------

    def _is_roamer_at(self, group, num):
        roamer = self.state.roamer
        if roamer is None or not roamer.active:
            return False
        value = constants.maps()[roamer.map]
        return group == map_group(value) and num == map_num(value)

    def _try_start_roamer_encounter(self):
        group, num = self.state.location
        if self._is_roamer_at(group, num) and (self._random() % 4) == 0:
            roamer = self.state.roamer
            mon = Pokemon(species=roamer.species, level=roamer.level, personality=roamer.personality,
                          ot_id=self.state.player_trainer_id)
            ivs = roamer.ivs
            for i, key in enumerate(("hp", "atk", "def", "spe", "spa", "spd")):
                mon.ivs[key] = (ivs >> (5 * i)) & 31
            self.enemy_mon = mon
            return True
        return False

    def _start_wild_battle(self):
        self.state.battle = EncounterOutcome("wild", self.enemy_mon)

    def _start_roamer_battle(self):
        self.state.battle = EncounterOutcome("roamer", self.enemy_mon)

    # -- public functions -----------------------------------------------------------

    def standard_wild_encounter(self, curr_metatile_attrs, previous_metatile_behavior):
        """StandardWildEncounter()"""
        state = self.state
        if self.wild_encounters_disabled:
            return False

        header_id = self.get_current_map_wild_mon_header_id()
        if header_id == HEADER_NONE:
            return False
        header = self.tables.headers[header_id]
        encounter_type = extract_metatile_attribute(curr_metatile_attrs, METATILE_ATTRIBUTE_ENCOUNTER_TYPE)
        behavior = extract_metatile_attribute(curr_metatile_attrs, METATILE_ATTRIBUTE_BEHAVIOR)

        if encounter_type == TILE_ENCOUNTER_LAND:
            info = header.land_mons_info
            area = WILD_AREA_LAND
        elif encounter_type == TILE_ENCOUNTER_WATER or (
                state.test_player_avatar_flags(PLAYER_AVATAR_FLAG_SURFING)
                and metatile_behavior_is_bridge(behavior)):
            info = header.water_mons_info
            area = WILD_AREA_WATER
        else:
            return False

        if info is None:
            return False
        if previous_metatile_behavior != behavior and not self._do_global_wild_encounter_dice_roll():
            return False
        if not self._do_wild_encounter_rate_test(info.encounter_rate, False):
            self._add_to_wild_encounter_rate_buff(info.encounter_rate)
            return False

        if self._try_start_roamer_encounter():
            if not self._is_wild_level_allowed_by_repel(state.roamer.level):
                return False
            self._start_roamer_battle()
            return True

        # try a regular wild encounter
        if self._try_generate_wild_mon(info, area, WILD_CHECK_REPEL):
            self._start_wild_battle()
            return True
        self._add_to_wild_encounter_rate_buff(info.encounter_rate)
        return False

    def rock_smash_wild_encounter(self):
        """RockSmashWildEncounter(): the result goes to state.special_var_result."""
        state = self.state
        header_idx = self.get_current_map_wild_mon_header_id()
        if header_idx == HEADER_NONE:
            state.special_var_result = False
        elif self.tables.headers[header_idx].rock_smash_mons_info is None:
            state.special_var_result = False
        elif not self._do_wild_encounter_rate_test(
                self.tables.headers[header_idx].rock_smash_mons_info.encounter_rate, True):
            state.special_var_result = False
        elif self._try_generate_wild_mon(self.tables.headers[header_idx].rock_smash_mons_info,
                                         WILD_AREA_ROCKS, WILD_CHECK_REPEL):
            self._start_wild_battle()
            state.special_var_result = True
        else:
            state.special_var_result = False

    def sweet_scent_wild_encounter(self):
        """SweetScentWildEncounter()"""
        state = self.state
        header_id = self.get_current_map_wild_mon_header_id()
        if header_id == HEADER_NONE:
            return False
        header = self.tables.headers[header_id]
        encounter_type = state.metatile_encounter_type_at_dest

        if encounter_type == TILE_ENCOUNTER_LAND:
            info, area = header.land_mons_info, WILD_AREA_LAND
        elif encounter_type == TILE_ENCOUNTER_WATER:
            info, area = header.water_mons_info, WILD_AREA_WATER
        else:
            return False

        if self._try_start_roamer_encounter():
            self._start_roamer_battle()
            return True
        if info is None:
            return False
        self._try_generate_wild_mon(info, area, 0)
        self._start_wild_battle()
        return True

    def does_current_map_have_fishing_mons(self):
        header_idx = self.get_current_map_wild_mon_header_id()
        if header_idx == HEADER_NONE:
            return False
        return self.tables.headers[header_idx].fishing_mons_info is not None

    def fishing_wild_encounter(self, rod):
        """FishingWildEncounter(). Like the game, only call this if
        does_current_map_have_fishing_mons() returned True."""
        header = self.tables.headers[self.get_current_map_wild_mon_header_id()]
        self._generate_fishing_encounter(header.fishing_mons_info, rod)
        self.state.increment_game_stat("GAME_STAT_FISHING_CAPTURES")
        self._start_wild_battle()

    def get_local_wild_mon(self):
        """GetLocalWildMon(): returns (species, is_water_mon)."""
        header_id = self.get_current_map_wild_mon_header_id()
        if header_id == HEADER_NONE:
            return "SPECIES_NONE", False
        land = self.tables.headers[header_id].land_mons_info
        water = self.tables.headers[header_id].water_mons_info
        # Neither
        if land is None and water is None:
            return "SPECIES_NONE", False
        # Land Pokemon
        if land is not None and water is None:
            return land.wild_pokemon[self._choose_wild_mon_index_land()].species, False
        # Water Pokemon
        if land is None and water is not None:
            return water.wild_pokemon[self._choose_wild_mon_index_water_rock()].species, True
        # Either land or water Pokemon
        if (self._random() % 100) < 80:
            return land.wild_pokemon[self._choose_wild_mon_index_land()].species, False
        return water.wild_pokemon[self._choose_wild_mon_index_water_rock()].species, True

    def get_local_water_mon(self):
        header_id = self.get_current_map_wild_mon_header_id()
        if header_id != HEADER_NONE:
            water = self.tables.headers[header_id].water_mons_info
            if water is not None:
                return water.wild_pokemon[self._choose_wild_mon_index_water_rock()].species
        return "SPECIES_NONE"

    def update_repel_counter(self):
        state = self.state
        if state.in_union_room:
            return False
        if state.quest_log_state == QL_STATE_PLAYBACK:
            return False
        steps = state.var_get("VAR_REPEL_STEP_COUNT")
        if steps != 0:
            steps -= 1
            state.var_set("VAR_REPEL_STEP_COUNT", steps)
            if steps == 0:
                state.scripts_started.append("EventScript_RepelWoreOff")
                return True
        return False

    def _is_wild_level_allowed_by_repel(self, wild_level):
        state = self.state
        if not state.var_get("VAR_REPEL_STEP_COUNT"):
            return True
        for i in range(PARTY_SIZE):
            mon = state.party[i] if i < len(state.party) else None
            if mon is not None and mon.hp and not mon.is_egg:
                our_level = mon.level & 0xFF
                return not (wild_level < our_level)
        return False

    def _apply_flute_encounter_rate_mod(self, rate):
        mod = self._get_flute_encounter_rate_mod_type()
        if mod == 1:
            rate += rate // 2
        elif mod == 2:
            rate = rate // 2
        return rate

    def _get_flute_encounter_rate_mod_type(self):
        if self.state.flag_get("FLAG_SYS_WHITE_FLUTE_ACTIVE"):
            return 1
        if self.state.flag_get("FLAG_SYS_BLACK_FLUTE_ACTIVE"):
            return 2
        return 0

    def _apply_cleanse_tag_encounter_rate_mod(self, rate):
        if self._is_lead_mon_holding_cleanse_tag():
            rate = rate * 2 // 3
        return rate

    def _is_lead_mon_holding_cleanse_tag(self):
        return self.lead_mon_held_item == "ITEM_CLEANSE_TAG"

    def seed_wild_encounter_rng(self, seed):
        self.rng_state = seed & 0xFFFF
        self.reset_encounter_rate_modifiers()

    def _wild_encounter_random(self):
        self.rng_state = iso_randomize2(self.rng_state)
        return self.rng_state >> 16

    def _get_map_base_encounter_cooldown(self, encounter_type):
        header_idx = self.get_current_map_wild_mon_header_id()
        if header_idx == HEADER_NONE:
            return 0xFF
        header = self.tables.headers[header_idx]
        if encounter_type == TILE_ENCOUNTER_LAND:
            info = header.land_mons_info
        elif encounter_type == TILE_ENCOUNTER_WATER:
            info = header.water_mons_info
        else:
            return 0xFF
        if info is None:
            return 0xFF
        if info.encounter_rate >= 80:
            return 0
        if info.encounter_rate < 10:
            return 8
        return 8 - info.encounter_rate // 10

    def reset_encounter_rate_modifiers(self):
        self.encounter_rate_buff = 0
        self.steps_since_last_encounter = 0

    def _handle_wild_encounter_cooldown(self, curr_metatile_attrs):
        encounter_type = extract_metatile_attribute(curr_metatile_attrs, METATILE_ATTRIBUTE_ENCOUNTER_TYPE) & 0xFF
        if encounter_type == TILE_ENCOUNTER_NONE:
            return False
        min_steps = self._get_map_base_encounter_cooldown(encounter_type)
        if min_steps == 0xFF:
            return False
        min_steps *= 256
        enc_rate = 5 * 256
        mod = self._get_flute_encounter_rate_mod_type()
        if mod == 1:
            min_steps -= min_steps // 2
            enc_rate += enc_rate // 2
        elif mod == 2:
            min_steps *= 2
            enc_rate //= 2
        lead = self.state.party[0] if self.state.party else None
        self.lead_mon_held_item = lead.held_item if lead is not None else "ITEM_NONE"
        if self._is_lead_mon_holding_cleanse_tag():
            min_steps += min_steps // 3
            enc_rate -= enc_rate // 3
        ability_mod = self._get_ability_encounter_rate_mod_type()
        if ability_mod == 1:
            min_steps *= 2
            enc_rate //= 2
        elif ability_mod == 2:
            min_steps //= 2
            enc_rate *= 2
        min_steps //= 256
        enc_rate //= 256
        if self.steps_since_last_encounter >= min_steps:
            return True
        self.steps_since_last_encounter = self._u8(self.steps_since_last_encounter + 1)
        if (self._random() % 100) < enc_rate:
            return True
        return False

    def try_standard_wild_encounter(self, curr_metatile_attrs):
        """TryStandardWildEncounter(): called every time the player takes a step."""
        behavior = extract_metatile_attribute(curr_metatile_attrs, METATILE_ATTRIBUTE_BEHAVIOR) & 0xFFFF
        if not self._handle_wild_encounter_cooldown(curr_metatile_attrs):
            self.prev_metatile_behavior = behavior
            return False
        if self.standard_wild_encounter(curr_metatile_attrs, self.prev_metatile_behavior):
            self.encounter_rate_buff = 0
            self.steps_since_last_encounter = 0
            self.prev_metatile_behavior = behavior
            return True
        self.prev_metatile_behavior = behavior
        return False

    def _add_to_wild_encounter_rate_buff(self, encounter_rate):
        if self.state.var_get("VAR_REPEL_STEP_COUNT") == 0:
            self.encounter_rate_buff = self._u16(self.encounter_rate_buff + encounter_rate)
        else:
            self.encounter_rate_buff = 0

    @staticmethod
    def get_unown_letter_by_personality_lo_byte(personality):
        return get_unown_letter(personality)
