"""Tests for the Python port of src/wild_encounter.c.

The main test compiles the real src/wild_encounter.c for the host machine
(tests/c_harness/harness.c), runs thousands of randomly generated scenarios
through both the C code and the Python port, and checks that every result,
every random number and every created Pokemon is identical.

Run from the repository root:

    python3 -m unittest discover -s python/tests -v
"""

import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pokefirered import constants, repo_path  # noqa: E402
from pokefirered.pokemon import Pokemon, species_info  # noqa: E402
from pokefirered.rng import Rng  # noqa: E402
from pokefirered.wild_encounter import (  # noqa: E402
    GameState, Roamer, WildEncounters, load_encounter_tables, metatile_attributes,
    map_group, map_num, TILE_ENCOUNTER_LAND, TILE_ENCOUNTER_NONE, TILE_ENCOUNTER_WATER,
    PLAYER_AVATAR_FLAG_ACRO_BIKE, PLAYER_AVATAR_FLAG_MACH_BIKE, PLAYER_AVATAR_FLAG_ON_FOOT,
    PLAYER_AVATAR_FLAG_SURFING)

GENERATED_HEADER = repo_path("src", "data", "wild_encounters.h")


def ensure_generated_header():
    """Generates src/data/wild_encounters.h the same way the Makefile does."""
    if not os.path.exists(GENERATED_HEADER):
        subprocess.run([sys.executable, repo_path("tools", "jsonproc", "jsonproc.py"),
                        "src/data/wild_encounters.json", "src/data/wild_encounters.json.txt",
                        "src/data/wild_encounters.h"], cwd=repo_path(), check=True)


# ---------------------------------------------------------------------------
# Python implementation of the harness command protocol
# ---------------------------------------------------------------------------

class PythonHarness:
    def __init__(self, game_version):
        self.tables = load_encounter_tables(game_version)
        self.state = GameState(game_version=game_version, party=[Pokemon("SPECIES_NONE", 0, hp=0)
                                                                  for _ in range(6)])
        self.state.rng = Rng(0)
        self.enc = WildEncounters(self.state, self.tables)
        self.map_names = {v: k for k, v in constants.maps().items()}
        self.state.map = "MAP_UNDEFINED"
        self.flag_names = {}
        for name in ("FLAG_SYS_WHITE_FLUTE_ACTIVE", "FLAG_SYS_BLACK_FLUTE_ACTIVE", "FLAG_SYS_UNLOCKED_TANOBY_RUINS"):
            self.flag_names[constants.flags()[name]] = name
        self.var_names = {constants.vars()[n]: n for n in ("VAR_REPEL_STEP_COUNT", "VAR_ALTERING_CAVE_WILD_SET")}
        self.item_names = {}
        for name in constants.items().names("ITEM_"):
            value = constants.items().get(name)
            if value is not None:
                self.item_names.setdefault(value, name)
        self.species_names = {}
        for name in constants.species().names("SPECIES_"):
            value = constants.species().get(name)
            if value is not None:
                self.species_names.setdefault(value, name)
        self.abilities = {}
        self.fishing = 0
        self.scripts = 0

    def run(self, lines):
        out = []
        for line in lines:
            parts = line.split()
            cmd, args = parts[0], [int(x) for x in parts[1:]]
            self.state.battle = None
            s = self.state
            if cmd == "rng":
                s.rng.value = args[0]
            elif cmd == "map":
                s.map = self.map_names.get(args[0] << 8 | args[1], s.map)
                self._raw_location = (args[0], args[1])
            elif cmd == "flag":
                name = self.flag_names[args[0]]
                if args[1]:
                    s.flags.add(name)
                else:
                    s.flags.discard(name)
            elif cmd == "var":
                s.var_set(self.var_names[args[0]], args[1])
            elif cmd == "avatar":
                s.player_avatar_flags = args[0]
            elif cmd == "tid":
                s.player_trainer_id = args[0]
            elif cmd == "dest":
                s.metatile_encounter_type_at_dest = args[0]
            elif cmd == "union":
                s.in_union_room = bool(args[0])
            elif cmd == "questlog":
                s.quest_log_state = args[0]
            elif cmd == "party":
                slot, hp, egg, level, held, ability = args
                mon = PartyMonWithAbility("SPECIES_BULBASAUR", level, hp=hp, is_egg=bool(egg),
                                          held_item=self.item_names.get(held, "ITEM_NONE"))
                mon.fixed_ability = constants.name_of(constants.abilities(), ability, "ABILITY_")
                s.party[slot] = mon
            elif cmd == "roamer":
                active, group, num, species, level, ivs, personality = args
                s.roamer = Roamer(self.species_names[species], level, self.map_names[group << 8 | num],
                                  personality, ivs, bool(active))
            else:
                result = self._call(cmd, args)
                out.append(self._format(cmd, result))
        return out

    def _call(self, cmd, args):
        enc = self.enc
        s = self.state
        if cmd == "try_standard":
            return int(enc.try_standard_wild_encounter(args[0]))
        if cmd == "standard":
            return int(enc.standard_wild_encounter(args[0], args[1]))
        if cmd == "sweet_scent":
            return int(enc.sweet_scent_wild_encounter())
        if cmd == "rock_smash":
            enc.rock_smash_wild_encounter()
            return int(s.special_var_result)
        if cmd == "has_fishing":
            return int(enc.does_current_map_have_fishing_mons())
        if cmd == "fishing":
            enc.fishing_wild_encounter(args[0])
            return 0
        if cmd == "local_wild":
            species, is_water = enc.get_local_wild_mon()
            return constants.species()[species] * 2 + int(is_water)
        if cmd == "local_water":
            return constants.species()[enc.get_local_water_mon()]
        if cmd == "repel":
            return int(enc.update_repel_counter())
        if cmd == "seed_wild":
            enc.seed_wild_encounter_rng(args[0])
            return 0
        if cmd == "disable":
            enc.disable_wild_encounters(args[0])
            return 0
        if cmd == "reset_mods":
            enc.reset_encounter_rate_modifiers()
            return 0
        raise ValueError(cmd)

    def _format(self, cmd, result):
        enc = self.enc
        s = self.state
        held = constants.items().get(enc.lead_mon_held_item, 0)
        text = "%s -> %d | rng=%08X wrng=%08X prev=%d buff=%d steps=%d ability=%d held=%d | " % (
            cmd, result, s.rng.value, enc.rng_state, enc.prev_metatile_behavior, enc.encounter_rate_buff,
            enc.steps_since_last_encounter, enc.ability_effect, held)
        if s.battle is not None:
            mon = s.battle.mon
            ivs = "/".join(str(mon.ivs[k]) for k in ("hp", "atk", "def", "spe", "spa", "spd"))
            text += "%s %d L%d %08X %s" % (s.battle.kind, mon.species_id, mon.level, mon.personality, ivs)
        else:
            text += "none"
        text += " | special=%d scripts=%d fishing=%d repel=%d" % (
            int(s.special_var_result), len(s.scripts_started), s.game_stats.get("GAME_STAT_FISHING_CAPTURES", 0),
            s.var_get("VAR_REPEL_STEP_COUNT"))
        return text


class PartyMonWithAbility(Pokemon):
    """A party Pokemon whose ability is given directly (the harness passes ability IDs)."""
    fixed_ability = None

    @property
    def ability(self):
        return self.fixed_ability


# ---------------------------------------------------------------------------
# Scenario generation
# ---------------------------------------------------------------------------

def make_scenario(rnd, game_version, length):
    tables = load_encounter_tables(game_version)
    maps = constants.maps()
    flags = constants.flags()
    variables = constants.vars()
    items = constants.items()
    abilities = constants.abilities()

    encounter_maps = sorted({h.map for h in tables.headers if h.map and h.map != "MAP_UNDEFINED"})
    other_maps = ["MAP_PALLET_TOWN", "MAP_PALLET_TOWN_PLAYERS_HOUSE_1F", "MAP_SIX_ISLAND_ALTERING_CAVE",
                  "MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER", "MAP_SEVEN_ISLAND_TANOBY_RUINS_VIAPOIS_CHAMBER"]

    lines = ["rng %d" % rnd.getrandbits(32), "tid %d" % rnd.getrandbits(32)]

    def set_map():
        name = rnd.choice(encounter_maps + other_maps)
        value = maps[name]
        lines.append("map %d %d" % (map_group(value), map_num(value)))
        return name

    def set_party():
        for slot in range(6):
            present = slot == 0 or rnd.random() < 0.5
            hp = rnd.choice([0, 1, 30]) if present else 0
            if slot == 0:
                hp = rnd.choice([0, 25, 25, 25])
            egg = int(rnd.random() < 0.1)
            level = rnd.randint(1, 100)
            held = rnd.choice(["ITEM_NONE", "ITEM_NONE", "ITEM_CLEANSE_TAG", "ITEM_POTION"])
            ability = rnd.choice(["ABILITY_NONE", "ABILITY_STENCH", "ABILITY_ILLUMINATE", "ABILITY_OVERGROW"])
            lines.append("party %d %d %d %d %d %d" % (slot, hp, egg, level, items[held], abilities[ability]))

    def set_roamer(current_map):
        species = rnd.choice(["SPECIES_RAIKOU", "SPECIES_ENTEI", "SPECIES_SUICUNE"])
        where = current_map if rnd.random() < 0.6 else rnd.choice(encounter_maps)
        value = maps[where]
        lines.append("roamer %d %d %d %d %d %d %d" % (
            int(rnd.random() < 0.8), map_group(value), map_num(value), constants.species()[species],
            50, rnd.getrandbits(30), rnd.getrandbits(32)))

    current_map = set_map()
    set_party()
    set_roamer(current_map)
    lines.append("seed_wild %d" % rnd.getrandbits(16))

    behaviors = [0, 2, 0x10, 0x11, 0x13, 0x15]
    for _ in range(length):
        r = rnd.random()
        if r < 0.02:
            current_map = set_map()
        elif r < 0.03:
            set_party()
        elif r < 0.035:
            set_roamer(current_map)
        elif r < 0.05:
            name = rnd.choice(["FLAG_SYS_WHITE_FLUTE_ACTIVE", "FLAG_SYS_BLACK_FLUTE_ACTIVE",
                               "FLAG_SYS_UNLOCKED_TANOBY_RUINS"])
            lines.append("flag %d %d" % (flags[name], int(rnd.random() < 0.5)))
        elif r < 0.06:
            lines.append("var %d %d" % (variables["VAR_REPEL_STEP_COUNT"], rnd.choice([0, 0, 1, 2, 5, 100])))
        elif r < 0.065:
            lines.append("var %d %d" % (variables["VAR_ALTERING_CAVE_WILD_SET"], rnd.randint(0, 10)))
        elif r < 0.075:
            lines.append("avatar %d" % rnd.choice([PLAYER_AVATAR_FLAG_ON_FOOT, PLAYER_AVATAR_FLAG_MACH_BIKE,
                                                   PLAYER_AVATAR_FLAG_ACRO_BIKE, PLAYER_AVATAR_FLAG_SURFING]))
        elif r < 0.08:
            lines.append("dest %d" % rnd.choice([TILE_ENCOUNTER_NONE, TILE_ENCOUNTER_LAND, TILE_ENCOUNTER_WATER]))
        elif r < 0.082:
            lines.append("union %d" % int(rnd.random() < 0.3))
        elif r < 0.084:
            lines.append("questlog %d" % rnd.choice([0, 0, 2]))
        elif r < 0.75:
            encounter_type = rnd.choice([TILE_ENCOUNTER_LAND, TILE_ENCOUNTER_LAND, TILE_ENCOUNTER_WATER,
                                         TILE_ENCOUNTER_NONE])
            attrs = metatile_attributes(rnd.choice(behaviors), encounter_type) | (rnd.getrandbits(32) & 0xF8FFFE00)
            lines.append("try_standard %d" % attrs)
        elif r < 0.78:
            lines.append("standard %d %d" % (metatile_attributes(rnd.choice(behaviors), rnd.randint(0, 3)),
                                              rnd.choice(behaviors)))
        elif r < 0.81:
            lines.append("sweet_scent")
        elif r < 0.84:
            lines.append("rock_smash")
        elif r < 0.87:
            lines.append("has_fishing")
        elif r < 0.90:
            if any(h.fishing_mons_info for h in tables.headers if h.map == current_map):
                lines.append("fishing %d" % rnd.randint(0, 2))
        elif r < 0.93:
            lines.append("local_wild")
        elif r < 0.95:
            lines.append("local_water")
        elif r < 0.98:
            lines.append("repel")
        elif r < 0.99:
            lines.append("disable %d" % int(rnd.random() < 0.3))
        else:
            lines.append(rnd.choice(["reset_mods", "seed_wild %d" % rnd.getrandbits(16)]))
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def find_c_compiler():
    for cc in (os.environ.get("CC"), "cc", "clang", "gcc"):
        if cc and shutil.which(cc):
            return cc
    return None


class TestAgainstC(unittest.TestCase):
    """Runs the original C code and the Python port side by side."""

    @classmethod
    def setUpClass(cls):
        cls.cc = find_c_compiler()
        if cls.cc is None:
            raise unittest.SkipTest("no C compiler found")
        ensure_generated_header()
        cls.tmpdir = tempfile.mkdtemp()
        cls.harness = {}
        for version in ("FIRERED", "LEAFGREEN"):
            exe = os.path.join(cls.tmpdir, "harness_" + version)
            cmd = [cls.cc, "-w", "-O1", "-iquote", repo_path("include"), "-I", repo_path("include"),
                   "-D" + version, "-DREVISION=0", "-DENGLISH", "-DMODERN=1",
                   "-o", exe, repo_path("python", "tests", "c_harness", "harness.c")]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise unittest.SkipTest("could not build the C harness:\n" + result.stderr[-2000:])
            cls.harness[version] = exe

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def run_c(self, version, lines):
        result = subprocess.run([self.harness[version]], input="\n".join(lines) + "\n",
                                capture_output=True, text=True, check=True)
        return result.stdout.splitlines()

    def compare(self, version, seed, length):
        rnd = random.Random(seed)
        lines = make_scenario(rnd, version, length)
        c_out = self.run_c(version, lines)
        py_out = PythonHarness(version).run(lines)
        self.assertEqual(len(c_out), len(py_out))
        for i, (c_line, py_line) in enumerate(zip(c_out, py_out)):
            if c_line != py_line:
                self.fail("scenario %s/%d differs at output %d:\n  C:      %s\n  Python: %s"
                          % (version, seed, i, c_line, py_line))
        return len(c_out)

    def test_firered_scenarios(self):
        total = sum(self.compare("FIRERED", seed, 400) for seed in range(40))
        self.assertGreater(total, 10000)

    def test_leafgreen_scenarios(self):
        total = sum(self.compare("LEAFGREEN", seed, 400) for seed in range(1000, 1040))
        self.assertGreater(total, 10000)

    def test_battles_happen(self):
        """Make sure the scenarios actually exercise wild and roamer battles."""
        out = []
        for seed in range(40):
            out += PythonHarness("FIRERED").run(make_scenario(random.Random(seed), "FIRERED", 400))
        for kind in ("try_standard", "sweet_scent", "rock_smash", "fishing"):
            self.assertTrue(any(line.startswith(kind) and "| wild " in line for line in out), kind)
        self.assertTrue(any("| roamer " in line for line in out))


class TestEncounterData(unittest.TestCase):
    """Checks the Python tables against the header that the C build compiles."""

    @classmethod
    def setUpClass(cls):
        ensure_generated_header()
        with open(GENERATED_HEADER, encoding="utf-8") as f:
            cls.header = f.read()

    def test_slot_chances_match_defines(self):
        defines = dict(re.findall(r"#define (ENCOUNTER_CHANCE_\w+) (.+)", self.header))

        def evaluate(name):
            expr = defines[name].strip()
            total = 0
            for term in expr.strip("()").split("+"):
                term = term.strip()
                total += evaluate(term) if term.startswith("ENCOUNTER_CHANCE_") else int(term)
            return total

        tables = load_encounter_tables("FIRERED")
        for field_type, chances in tables.slot_chances.items():
            for slot, chance in enumerate(chances):
                self.assertEqual(evaluate("ENCOUNTER_CHANCE_%s_SLOT_%d" % (field_type.upper(), slot)), chance)
            self.assertEqual(evaluate("ENCOUNTER_CHANCE_%s_TOTAL" % field_type.upper()), tables.total(field_type))
        for (field_type, group), chances in tables.group_slot_chances.items():
            for slot, chance in chances.items():
                self.assertEqual(evaluate("ENCOUNTER_CHANCE_%s_%s_SLOT_%d" % (field_type.upper(), group.upper(), slot)),
                                 chance)

    def test_mon_lists_match_header(self):
        arrays = {}
        for label, body in re.findall(r"const struct WildPokemon (\w+)\[\] =\s*\{(.*?)\};", self.header, re.S):
            arrays[label] = re.findall(r"\{ (\d+), (\d+), (SPECIES_\w+) \}", body)
        for version in ("FIRERED", "LEAFGREEN"):
            for header in load_encounter_tables(version).headers[:-1]:
                for key, suffix in (("land_mons_info", "LandMons"), ("water_mons_info", "WaterMons"),
                                    ("rock_smash_mons_info", "RockSmashMons"), ("fishing_mons_info", "FishingMons")):
                    info = getattr(header, key)
                    if info is None:
                        self.assertNotIn(header.base_label + "_" + suffix, arrays)
                        continue
                    expected = [(str(m.min_level), str(m.max_level), m.species) for m in info.wild_pokemon]
                    self.assertEqual(arrays[header.base_label + "_" + suffix], expected)

    def test_species_data_available(self):
        tables = load_encounter_tables("FIRERED")
        for header in tables.headers[:-1]:
            for info in (header.land_mons_info, header.water_mons_info,
                         header.rock_smash_mons_info, header.fishing_mons_info):
                if info:
                    for mon in info.wild_pokemon:
                        self.assertIn(mon.species, species_info())


class TestRng(unittest.TestCase):
    def test_random_sequence(self):
        rng = Rng(0)
        # gRngValue = 1103515245 * gRngValue + 24691; the well-known Gen 3 sequence from seed 0
        self.assertEqual([rng.random() for _ in range(3)], [0x0000, 0xE97E, 0x5271])


if __name__ == "__main__":
    unittest.main()
