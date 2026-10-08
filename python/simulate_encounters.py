#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Simulates wild encounters by running src/wild_encounter.py, the same code
that is compiled into the ROM.

Examples:
    python3 python/simulate_encounters.py --map MAP_ROUTE1
    python3 python/simulate_encounters.py --map MAP_VIRIDIAN_FOREST --steps 5000 --summary
    python3 python/simulate_encounters.py --map MAP_ROUTE4 --method super-rod --count 20
    python3 python/simulate_encounters.py --version LEAFGREEN --map MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER \\
        --flag FLAG_SYS_UNLOCKED_TANOBY_RUINS --summary
"""

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gba.constants as c  # noqa: E402
from gba.runtime import Game, _species_name  # noqa: E402


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
    parser.add_argument("--trainer-id", type=lambda s: int(s, 0), default=0x12345678)
    parser.add_argument("--lead", default="SPECIES_BULBASAUR", help="species of the lead party Pokemon")
    parser.add_argument("--lead-level", type=int, default=50)
    parser.add_argument("--lead-item", default="ITEM_NONE", help="e.g. ITEM_CLEANSE_TAG")
    parser.add_argument("--repel", type=int, default=0, help="remaining repel steps")
    parser.add_argument("--flag", action="append", default=[], help="set an event flag, e.g. FLAG_SYS_WHITE_FLUTE_ACTIVE")
    parser.add_argument("--altering-cave", type=int, default=0, help="VAR_ALTERING_CAVE_WILD_SET value")
    parser.add_argument("--summary", action="store_true", help="only print the species distribution")
    args = parser.parse_args()

    if not hasattr(c, args.map) or not args.map.startswith("MAP_"):
        parser.error("unknown map %s" % args.map)

    game = Game(args.version)
    game.set_map(args.map)
    game.set_party_mon(0, args.lead, args.lead_level, held_item=args.lead_item)
    game.gRngValue = args.seed
    game.player_trainer_id = args.trainer_id
    for flag in args.flag:
        game.flags.add(getattr(c, flag))
    game.VarSet(c.VAR_REPEL_STEP_COUNT, args.repel)
    game.VarSet(c.VAR_ALTERING_CAVE_WILD_SET, args.altering_cave)
    game.SeedWildEncounterRng(args.seed & 0xFFFF)

    found = []

    def record(label):
        if game.battle is not None:
            found.append(game.battle)
            if not args.summary:
                print("%-12s %s" % (label, game.battle))
            game.battle = None

    if args.method in ("walk", "surf", "bike"):
        if args.method == "surf":
            game.player_avatar_flags = c.PLAYER_AVATAR_FLAG_SURFING
            tile = game.metatile(c.MB_DEEP_WATER, c.TILE_ENCOUNTER_WATER)
        else:
            game.player_avatar_flags = c.PLAYER_AVATAR_FLAG_MACH_BIKE if args.method == "bike" else \
                c.PLAYER_AVATAR_FLAG_ON_FOOT
            tile = game.metatile(c.MB_TALL_GRASS, c.TILE_ENCOUNTER_LAND)
        for step in range(1, args.steps + 1):
            if game.UpdateRepelCounter() and not args.summary:
                print("step %-6d repel wore off" % step)
            game.TryStandardWildEncounter(tile)
            record("step %d" % step)
    elif args.method == "sweet-scent":
        game.dest_encounter_type = c.TILE_ENCOUNTER_LAND
        for i in range(args.count):
            game.SweetScentWildEncounter()
            record("scent %d" % (i + 1))
    elif args.method == "rock-smash":
        for i in range(args.count):
            game.RockSmashWildEncounter()
            record("rock %d" % (i + 1))
    else:
        if not game.DoesCurrentMapHaveFishingMons():
            parser.error("%s has no fishing encounters" % args.map)
        rod = {"old-rod": c.OLD_ROD, "good-rod": c.GOOD_ROD, "super-rod": c.SUPER_ROD}[args.method]
        for i in range(args.count):
            game.FishingWildEncounter(rod)
            record("cast %d" % (i + 1))

    print()
    if not found:
        print("No encounters (does %s have %s encounters in %s?)" % (args.map, args.method, args.version))
        return
    counts = collections.Counter(_species_name(b.mon.species) for b in found)
    print("%d encounters on %s (%s, %s):" % (len(found), args.map, args.version, args.method))
    for species, n in counts.most_common():
        print("  %-20s %5d  %5.1f%%" % (species[len("SPECIES_"):], n, 100.0 * n / len(found)))


if __name__ == "__main__":
    main()
