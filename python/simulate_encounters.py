#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Simulates wild encounters using the Python port of src/wild_encounter.c.

Examples:
    python3 python/simulate_encounters.py --map MAP_ROUTE1
    python3 python/simulate_encounters.py --map MAP_VIRIDIAN_FOREST --steps 5000 --summary
    python3 python/simulate_encounters.py --map MAP_ROUTE4 --method super-rod --count 20
    python3 python/simulate_encounters.py --version LEAFGREEN --map MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER \
        --flag FLAG_SYS_UNLOCKED_TANOBY_RUINS --summary
"""

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pokefirered import constants  # noqa: E402
from pokefirered.pokemon import Pokemon  # noqa: E402
from pokefirered.wild_encounter import (  # noqa: E402
    GOOD_ROD, OLD_ROD, PLAYER_AVATAR_FLAG_MACH_BIKE, PLAYER_AVATAR_FLAG_ON_FOOT, PLAYER_AVATAR_FLAG_SURFING,
    SUPER_ROD, TILE_ENCOUNTER_LAND, TILE_ENCOUNTER_WATER, GameState, WildEncounters, metatile_attributes)

# Metatile behaviors (include/constants/metatile_behaviors.h)
MB_TALL_GRASS = 0x02
MB_DEEP_WATER = 0x12


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", default="MAP_ROUTE1", help="map constant, e.g. MAP_ROUTE1")
    parser.add_argument("--version", default="FIRERED", choices=["FIRERED", "LEAFGREEN"])
    parser.add_argument("--method", default="walk",
                        choices=["walk", "surf", "bike", "sweet-scent", "rock-smash", "old-rod", "good-rod",
                                 "super-rod"])
    parser.add_argument("--steps", type=int, default=1000, help="steps to take when walking/surfing/biking")
    parser.add_argument("--count", type=int, default=10, help="attempts for the other methods")
    parser.add_argument("--seed", type=lambda s: int(s, 0), default=0x5EED, help="initial RNG value")
    parser.add_argument("--lead", default="SPECIES_BULBASAUR", help="species of the lead party Pokemon")
    parser.add_argument("--lead-level", type=int, default=50)
    parser.add_argument("--lead-item", default="ITEM_NONE", help="e.g. ITEM_CLEANSE_TAG")
    parser.add_argument("--repel", type=int, default=0, help="remaining repel steps")
    parser.add_argument("--flag", action="append", default=[], help="set an event flag, e.g. FLAG_SYS_WHITE_FLUTE_ACTIVE")
    parser.add_argument("--altering-cave", type=int, default=0, help="VAR_ALTERING_CAVE_WILD_SET value")
    parser.add_argument("--summary", action="store_true", help="only print the species distribution")
    args = parser.parse_args()

    if args.map not in constants.maps():
        parser.error("unknown map %s" % args.map)

    state = GameState(map=args.map, game_version=args.version,
                      party=[Pokemon(args.lead, args.lead_level, held_item=args.lead_item)])
    state.rng.value = args.seed
    state.flags.update(args.flag)
    state.vars["VAR_REPEL_STEP_COUNT"] = args.repel
    state.vars["VAR_ALTERING_CAVE_WILD_SET"] = args.altering_cave
    encounters = WildEncounters(state)
    encounters.seed_wild_encounter_rng(args.seed & 0xFFFF)

    found = []

    def record(label):
        if state.battle is not None:
            found.append(state.battle)
            if not args.summary:
                print("%-12s %s" % (label, state.battle))
            state.battle = None

    if args.method in ("walk", "surf", "bike"):
        if args.method == "surf":
            state.player_avatar_flags = PLAYER_AVATAR_FLAG_SURFING
            tile = metatile_attributes(MB_DEEP_WATER, TILE_ENCOUNTER_WATER)
        else:
            state.player_avatar_flags = PLAYER_AVATAR_FLAG_MACH_BIKE if args.method == "bike" else \
                PLAYER_AVATAR_FLAG_ON_FOOT
            tile = metatile_attributes(MB_TALL_GRASS, TILE_ENCOUNTER_LAND)
        for step in range(1, args.steps + 1):
            if encounters.update_repel_counter() and not args.summary:
                print("step %-6d repel wore off" % step)
            encounters.try_standard_wild_encounter(tile)
            record("step %d" % step)
    elif args.method == "sweet-scent":
        state.metatile_encounter_type_at_dest = TILE_ENCOUNTER_LAND
        for i in range(args.count):
            encounters.sweet_scent_wild_encounter()
            record("scent %d" % (i + 1))
    elif args.method == "rock-smash":
        for i in range(args.count):
            encounters.rock_smash_wild_encounter()
            record("rock %d" % (i + 1))
    else:
        if not encounters.does_current_map_have_fishing_mons():
            parser.error("%s has no fishing encounters" % args.map)
        rod = {"old-rod": OLD_ROD, "good-rod": GOOD_ROD, "super-rod": SUPER_ROD}[args.method]
        for i in range(args.count):
            encounters.fishing_wild_encounter(rod)
            record("cast %d" % (i + 1))

    print()
    if not found:
        print("No encounters (does %s have %s encounters in %s?)" % (args.map, args.method, args.version))
        return
    counts = collections.Counter(b.mon.species for b in found)
    print("%d encounters on %s (%s, %s):" % (len(found), args.map, args.version, args.method))
    for species, n in counts.most_common():
        print("  %-20s %5d  %5.1f%%" % (species[len("SPECIES_"):], n, 100.0 * n / len(found)))


if __name__ == "__main__":
    main()
