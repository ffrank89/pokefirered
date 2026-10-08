# Python support code

| Folder | What it is |
| --- | --- |
| [`gba/`](gba/__init__.py) | The **gba dialect** of Python used for game code (C types, structs, `switch`, ...). |
| [`gba/game.py`](gba/game.py) | Declarations of the game's C functions and variables that dialect code can use. |
| [`gba/runtime.py`](gba/runtime.py) | Runs dialect files on a PC with C's integer behavior, plus working versions of those game functions. |
| [`gba/constants.py`](gba/constants.py) | Every constant from the C headers (`MAP_*`, `SPECIES_*`, `FLAG_*`, ...), by name. |
| [`pokefirered/`](pokefirered/__init__.py) | Helpers for PC-side tools: reading C headers, species data. |
| [`simulate_encounters.py`](simulate_encounters.py) | Runs `src/wild_encounter.py` to simulate encounters on any map. |
| [`tests/`](tests/test_wild_encounter.py) | Checks that the PC version of the game code behaves exactly like the C generated for the ROM. |

The game code itself is in `src/*.py` (currently [`src/wild_encounter.py`](../src/wild_encounter.py)), and
the translator that turns it into C is [`tools/py2c/py2c.py`](../tools/py2c/py2c.py). See
[PYTHON_PORT.md](../PYTHON_PORT.md) for the full picture and an example.

```bash
uv run python/simulate_encounters.py --map MAP_ROUTE1
uv run python/simulate_encounters.py --map MAP_ROUTE4 --method super-rod --count 500 --summary
uv run python -m unittest discover -s python/tests -v
```

Running the game code from your own Python:

```python
import sys; sys.path.insert(0, "python")
import gba.constants as c
from gba.runtime import Game

game = Game("FIRERED")                 # loads src/wild_encounter.py
game.set_map("MAP_VIRIDIAN_FOREST")
game.set_party_mon(0, "SPECIES_BULBASAUR", 50)
game.gRngValue = 0x1234                # the game's random number generator state
tall_grass = game.metatile(c.MB_TALL_GRASS, c.TILE_ENCOUNTER_LAND)
for step in range(200):
    if game.TryStandardWildEncounter(tall_grass):    # the function from src/wild_encounter.py
        print(game.battle)
        game.battle = None
```
