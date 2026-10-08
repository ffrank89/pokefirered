# Python ports of game systems

The C code in `src/` is what gets compiled into the ROM, so it stays as it is. This folder holds Python
ports of individual game systems. They read the same data files as the C build and reproduce the game's
behavior exactly (including every random number), which makes them useful for tools, research and
simulations without a GBA toolchain.

## Wild encounters

[`pokefirered/wild_encounter.py`](pokefirered/wild_encounter.py) is a line-by-line port of
[`src/wild_encounter.c`](../src/wild_encounter.c), together with the pieces of other files it depends on:
the RNG ([`rng.py`](pokefirered/rng.py), from `src/random.c`) and Pokémon creation
([`pokemon.py`](pokefirered/pokemon.py), the RNG-consuming parts of `src/pokemon.c`).

Encounter tables come from `src/data/wild_encounters.json`, the same file the C build turns into
`src/data/wild_encounters.h`, so editing the JSON changes both.

```python
import sys; sys.path.insert(0, "python")
from pokefirered.wild_encounter import GameState, WildEncounters, metatile_attributes, TILE_ENCOUNTER_LAND

state = GameState(map="MAP_VIRIDIAN_FOREST")
state.rng.value = 0x1234
encounters = WildEncounters(state)
tall_grass = metatile_attributes(behavior=0x02, encounter_type=TILE_ENCOUNTER_LAND)
for step in range(200):
    if encounters.try_standard_wild_encounter(tall_grass):
        print(state.battle)   # wild battle: CATERPIE Lv4 HASTY male, IVs 9/16/24/26/19/13
```

The game state that the C code reads from globals (current map, flags and vars, party, roamer, player
avatar, RNG...) is a `GameState`; results such as the started battle or `gSpecialVar_Result` are written
back to it.

### Simulator

```bash
python3 python/simulate_encounters.py --map MAP_ROUTE1
python3 python/simulate_encounters.py --map MAP_ROUTE4 --method super-rod --count 500 --summary
python3 python/simulate_encounters.py --map MAP_SAFARI_ZONE_CENTER --version LEAFGREEN --steps 5000 --summary
python3 python/simulate_encounters.py --help
```

### Tests

```bash
python3 -m unittest discover -s python/tests -v
```

`tests/test_wild_encounter.py` compiles the real `src/wild_encounter.c` for your computer
(`tests/c_harness/harness.c`, needs a C compiler such as clang or gcc), runs thousands of random scenarios
through both versions for FireRed and LeafGreen, and fails if any result, RNG state or generated Pokémon
differs. It also checks the Python encounter tables against the generated C header.
