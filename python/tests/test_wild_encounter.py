"""Tests for src/wild_encounter.py, the Python source of the wild encounter code.

The ROM is built from the C that tools/py2c/py2c.py generates from
src/wild_encounter.py, and the simulator runs src/wild_encounter.py itself
through gba.runtime. The main test checks that those two agree exactly: it
translates the file to C, compiles that C for this computer
(tests/c_harness/harness.c), runs thousands of random scenarios through both,
and compares every result, every random number and every created Pokemon.

(That the generated C matches the original game is checked by building the
ROM: `python3 build.py` compares its SHA1 with the original.)

Run from the repository root:

    python3 -m unittest discover -s python/tests -v
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gba  # noqa: E402
import gba.constants as c  # noqa: E402
from gba.runtime import Game, build_wild_mon_headers, _cdiv, _cmod  # noqa: E402
from pokefirered import repo_path  # noqa: E402

sys.path.insert(0, repo_path("tools", "py2c"))
import py2c  # noqa: E402

GENERATED_HEADER = repo_path("src", "data", "wild_encounters.h")
SOURCE = repo_path("src", "wild_encounter.py")


def ensure_generated_header():
    """Generates src/data/wild_encounters.h the same way the Makefile does."""
    if not os.path.exists(GENERATED_HEADER):
        subprocess.run([sys.executable, repo_path("tools", "jsonproc", "jsonproc.py"),
                        "src/data/wild_encounters.json", "src/data/wild_encounters.json.txt",
                        "src/data/wild_encounters.h"], cwd=repo_path(), check=True)


def metatile(behavior, encounter_type):
    return (behavior & 0x1FF) | ((encounter_type & 7) << 24)


# ---------------------------------------------------------------------------
# Running harness commands with gba.runtime
# ---------------------------------------------------------------------------

class PythonHarness:
    def __init__(self, game_version):
        self.game = Game(game_version)

    def run(self, lines):
        g = self.game
        ns = g.namespace
        out = []
        for line in lines:
            parts = line.split()
            cmd, args = parts[0], [int(x) for x in parts[1:]]
            g.battle = None
            if cmd == "rng":
                g.gRngValue = args[0]
            elif cmd == "map":
                ns["gSaveBlock1Ptr"].location.mapGroup = args[0]
                ns["gSaveBlock1Ptr"].location.mapNum = args[1]
            elif cmd == "flag":
                if args[1]:
                    g.flags.add(args[0])
                else:
                    g.flags.discard(args[0])
            elif cmd == "var":
                g.VarSet(args[0], args[1])
            elif cmd == "avatar":
                g.player_avatar_flags = args[0]
            elif cmd == "tid":
                g.player_trainer_id = args[0]
            elif cmd == "dest":
                g.dest_encounter_type = args[0]
            elif cmd == "union":
                g.in_union_room = bool(args[0])
            elif cmd == "questlog":
                ns["gQuestLogState"] = args[0]
            elif cmd == "party":
                slot, hp, egg, level, held, ability = args
                g.party[slot].set(hp=hp, is_egg=bool(egg), level=level, held_item=held, ability=ability)
            elif cmd == "roamer":
                active, group, num, species, level, ivs, personality = args
                roamer = ns["gSaveBlock1Ptr"].roamer
                roamer.active = active
                roamer.species = species
                roamer.level = level
                roamer.ivs = ivs
                roamer.personality = personality
                g.roamer_location = (group, num)
            else:
                out.append(self._format(cmd, self._call(cmd, args)))
        return out

    def _call(self, cmd, args):
        g = self.game
        if cmd == "try_standard":
            return g.TryStandardWildEncounter(args[0])
        if cmd == "standard":
            return g.StandardWildEncounter(args[0], args[1])
        if cmd == "sweet_scent":
            return g.SweetScentWildEncounter()
        if cmd == "rock_smash":
            g.RockSmashWildEncounter()
            return g.namespace["gSpecialVar_Result"]
        if cmd == "has_fishing":
            return g.DoesCurrentMapHaveFishingMons()
        if cmd == "fishing":
            g.FishingWildEncounter(args[0])
            return 0
        if cmd == "local_wild":
            is_water = gba.Cell(gba.bool8)
            species = g.GetLocalWildMon(is_water)
            return species * 2 + is_water.value
        if cmd == "local_water":
            return g.GetLocalWaterMon()
        if cmd == "repel":
            return g.UpdateRepelCounter()
        if cmd == "seed_wild":
            g.SeedWildEncounterRng(args[0])
            return 0
        if cmd == "disable":
            g.DisableWildEncounters(args[0])
            return 0
        if cmd == "reset_mods":
            g.ResetEncounterRateModifiers()
            return 0
        raise ValueError(cmd)

    def _format(self, cmd, result):
        g = self.game
        data = g.namespace["sWildEncounterData"]
        text = "%s -> %d | rng=%08X wrng=%08X prev=%d buff=%d steps=%d ability=%d held=%d | " % (
            cmd, int(result), g.gRngValue, data.rngState, data.prevMetatileBehavior, data.encounterRateBuff,
            data.stepsSinceLastEncounter, data.abilityEffect, data.leadMonHeldItem)
        if g.battle is not None:
            mon = g.battle.mon
            text += "%s %d L%d %08X %s" % (g.battle.kind, mon.species, mon.level, mon.personality,
                                           "/".join(str(v) for v in mon.ivs))
        else:
            text += "none"
        text += " | special=%d scripts=%d fishing=%d repel=%d" % (
            g.namespace["gSpecialVar_Result"], len(g.scripts_started),
            g.game_stats.get(c.GAME_STAT_FISHING_CAPTURES, 0), g.VarGet(c.VAR_REPEL_STEP_COUNT))
        return text


# ---------------------------------------------------------------------------
# Scenario generation
# ---------------------------------------------------------------------------

def make_scenario(rnd, game_version, length):
    headers = build_wild_mon_headers(game_version)[:-1]
    encounter_maps = sorted({(h.mapGroup, h.mapNum) for h in headers})
    fishing_maps = {(h.mapGroup, h.mapNum) for h in headers if h.fishingMonsInfo is not None}
    other_maps = [c.MAP_PALLET_TOWN, c.MAP_PALLET_TOWN_PLAYERS_HOUSE_1F, c.MAP_SIX_ISLAND_ALTERING_CAVE,
                  c.MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER, c.MAP_SEVEN_ISLAND_TANOBY_RUINS_VIAPOIS_CHAMBER]
    all_maps = encounter_maps + [(m >> 8, m & 0xFF) for m in other_maps]

    lines = ["rng %d" % rnd.getrandbits(32), "tid %d" % rnd.getrandbits(32)]

    def set_map():
        where = rnd.choice(all_maps)
        lines.append("map %d %d" % where)
        return where

    def set_party():
        for slot in range(6):
            present = slot == 0 or rnd.random() < 0.5
            hp = rnd.choice([0, 1, 30]) if present else 0
            if slot == 0:
                hp = rnd.choice([0, 25, 25, 25])
            held = rnd.choice([c.ITEM_NONE, c.ITEM_NONE, c.ITEM_CLEANSE_TAG, c.ITEM_POTION])
            ability = rnd.choice([c.ABILITY_NONE, c.ABILITY_STENCH, c.ABILITY_ILLUMINATE, c.ABILITY_OVERGROW])
            lines.append("party %d %d %d %d %d %d" % (slot, hp, int(rnd.random() < 0.1), rnd.randint(1, 100),
                                                     held, ability))

    def set_roamer(current):
        species = rnd.choice([c.SPECIES_RAIKOU, c.SPECIES_ENTEI, c.SPECIES_SUICUNE])
        where = current if rnd.random() < 0.6 else rnd.choice(encounter_maps)
        lines.append("roamer %d %d %d %d %d %d %d" % (int(rnd.random() < 0.8), where[0], where[1], species, 50,
                                                      rnd.getrandbits(30), rnd.getrandbits(32)))

    current = set_map()
    set_party()
    set_roamer(current)
    lines.append("seed_wild %d" % rnd.getrandbits(16))

    behaviors = [0, 2, 0x10, 0x11, 0x13, 0x15]
    tiles = [c.TILE_ENCOUNTER_LAND, c.TILE_ENCOUNTER_LAND, c.TILE_ENCOUNTER_WATER, c.TILE_ENCOUNTER_NONE]
    for _ in range(length):
        r = rnd.random()
        if r < 0.02:
            current = set_map()
        elif r < 0.03:
            set_party()
        elif r < 0.035:
            set_roamer(current)
        elif r < 0.05:
            flag = rnd.choice([c.FLAG_SYS_WHITE_FLUTE_ACTIVE, c.FLAG_SYS_BLACK_FLUTE_ACTIVE,
                               c.FLAG_SYS_UNLOCKED_TANOBY_RUINS])
            lines.append("flag %d %d" % (flag, int(rnd.random() < 0.5)))
        elif r < 0.06:
            lines.append("var %d %d" % (c.VAR_REPEL_STEP_COUNT, rnd.choice([0, 0, 1, 2, 5, 100])))
        elif r < 0.065:
            lines.append("var %d %d" % (c.VAR_ALTERING_CAVE_WILD_SET, rnd.randint(0, 10)))
        elif r < 0.075:
            lines.append("avatar %d" % rnd.choice([c.PLAYER_AVATAR_FLAG_ON_FOOT, c.PLAYER_AVATAR_FLAG_MACH_BIKE,
                                                   c.PLAYER_AVATAR_FLAG_ACRO_BIKE, c.PLAYER_AVATAR_FLAG_SURFING]))
        elif r < 0.08:
            lines.append("dest %d" % rnd.choice([c.TILE_ENCOUNTER_NONE, c.TILE_ENCOUNTER_LAND, c.TILE_ENCOUNTER_WATER]))
        elif r < 0.082:
            lines.append("union %d" % int(rnd.random() < 0.3))
        elif r < 0.084:
            lines.append("questlog %d" % rnd.choice([0, 0, 2]))
        elif r < 0.75:
            attrs = metatile(rnd.choice(behaviors), rnd.choice(tiles)) | (rnd.getrandbits(32) & 0xF8FFFE00)
            lines.append("try_standard %d" % attrs)
        elif r < 0.78:
            lines.append("standard %d %d" % (metatile(rnd.choice(behaviors), rnd.randint(0, 3)), rnd.choice(behaviors)))
        elif r < 0.81:
            lines.append("sweet_scent")
        elif r < 0.84:
            lines.append("rock_smash")
        elif r < 0.87:
            lines.append("has_fishing")
        elif r < 0.90:
            if current in fishing_maps:
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


class TestPythonMatchesGeneratedC(unittest.TestCase):
    """Runs the Python source and the C generated from it side by side."""

    @classmethod
    def setUpClass(cls):
        cls.cc = find_c_compiler()
        if cls.cc is None:
            raise unittest.SkipTest("no C compiler found")
        ensure_generated_header()
        cls.tmpdir = tempfile.mkdtemp()
        generated = os.path.join(cls.tmpdir, "wild_encounter.c")
        with open(generated, "w") as f:
            f.write(py2c.translate_file(SOURCE, "src/wild_encounter.py"))
        cls.harness = {}
        for version in ("FIRERED", "LEAFGREEN"):
            exe = os.path.join(cls.tmpdir, "harness_" + version)
            cmd = [cls.cc, "-w", "-O1", "-iquote", repo_path("src"), "-iquote", repo_path("include"),
                   "-I", repo_path("include"), "-D" + version, "-DREVISION=0", "-DENGLISH", "-DMODERN=1",
                   '-DWILD_ENCOUNTER_C="%s"' % generated,
                   "-o", exe, repo_path("python", "tests", "c_harness", "harness.c")]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # A function that src/wild_encounter.py calls may be missing from
                # tests/c_harness/harness.c: add a stand-in for it there.
                raise AssertionError("could not build the C harness:\n" + result.stderr[-3000:])
            cls.harness[version] = exe

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def compare(self, version, seed, length):
        lines = make_scenario(random.Random(seed), version, length)
        c_out = subprocess.run([self.harness[version]], input="\n".join(lines) + "\n",
                               capture_output=True, text=True, check=True).stdout.splitlines()
        py_out = PythonHarness(version).run(lines)
        self.assertEqual(len(c_out), len(py_out))
        for i, (c_line, py_line) in enumerate(zip(c_out, py_out)):
            if c_line != py_line:
                self.fail("scenario %s/%d differs at output %d:\n  C:      %s\n  Python: %s"
                          % (version, seed, i, c_line, py_line))
        return len(c_out)

    def test_firered_scenarios(self):
        self.assertGreater(sum(self.compare("FIRERED", seed, 400) for seed in range(40)), 10000)

    def test_leafgreen_scenarios(self):
        self.assertGreater(sum(self.compare("LEAFGREEN", seed, 400) for seed in range(1000, 1040)), 10000)

    def test_battles_happen(self):
        """Make sure the scenarios actually exercise every kind of encounter."""
        out = []
        for seed in range(40):
            out += PythonHarness("FIRERED").run(make_scenario(random.Random(seed), "FIRERED", 400))
        for kind in ("try_standard", "sweet_scent", "rock_smash", "fishing"):
            self.assertTrue(any(line.startswith(kind) and "| wild " in line for line in out), kind)
        self.assertTrue(any("| roamer " in line for line in out))
        self.assertTrue(any("| wild %d " % c.SPECIES_UNOWN in line for line in out))


class TestTranslator(unittest.TestCase):
    def translate(self, body):
        return py2c.Translator("from gba import *\n" + body, "test.py").translate()

    def test_errors_are_reported(self):
        cases = {
            "def f() -> u8:\n    x = 5\n    return x\n": "not declared",
            "def f() -> u8:\n    return 7 / 2\n": "integer division",
            "def f() -> None:\n    g()\n    x: u8 = 1\n": "start of a block",
            "def f() -> u8:\n    return True\n": "TRUE and FALSE",
        }
        for body, message in cases.items():
            with self.assertRaises(py2c.TranslateError) as ctx:
                self.translate(body)
            self.assertIn(message, str(ctx.exception))

    def test_examples(self):
        text = self.translate(
            "@static\n"
            "def f(p: Ptr[u32], n: u8) -> u16:\n"
            "    i: u8\n"
            "    for i in range(0, n):\n"
            "        p[0] += i // 2\n"
            "    while True:\n"
            "        n -= 1\n"
            "        if n == 0:\n"
            "            break\n"
            "    return (n << 2) | 1\n")
        self.assertIn("static u16 f(u32 *p, u8 n)", text)
        self.assertIn("for (i = 0; i < n; i++)", text)
        self.assertIn("*p += i / 2;", text)
        self.assertIn("} while (n != 0);", text)
        self.assertIn("return (n << 2) | 1;", text)


class TestRuntime(unittest.TestCase):
    def test_random_sequence(self):
        game = Game("FIRERED")
        # gRngValue = 1103515245 * gRngValue + 24691; the well-known Gen 3 sequence from seed 0
        self.assertEqual([game.Random() for _ in range(3)], [0x0000, 0xE97E, 0x5271])

    def test_integer_types(self):
        self.assertEqual(gba.u8.cast(300), 44)
        self.assertEqual(gba.s8.cast(200), -56)
        self.assertEqual((_cdiv(-7, 2), _cmod(-7, 2)), (-3, -1))


if __name__ == "__main__":
    unittest.main()
