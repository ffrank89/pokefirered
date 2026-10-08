# Python port: summary

## What has been done

- **Scope.** The game's C code in `src/` still builds the ROM, because a GBA ROM has to be compiled
  from C (or assembly). The Python work replaces the project's build tooling and adds Python versions of
  game systems that run alongside the C code.
- **All 11 build tools in `tools/` are now Python**: `gbagfx`, `preproc`, `scaninc`, `mapjson`,
  `jsonproc` (including a port of the Inja template engine), `mid2agb`, `wav2agb`, `ramscrgen`,
  `gbafix`, `bin2c` and `rsfont`. Their C/C++ sources and `build_tools.sh` were removed (they remain in
  git history).
- **Every tool was checked against the original C binary** before the switch. The checks covered about
  9,900 graphics conversions, all 347 songs, all 477 sound samples, all 425 maps, every C and assembly
  file through `preproc` and `scaninc`, and all JSON templates. All outputs were byte-identical.
  The one exception is the order of `#include` lines in `data/maps/connections.inc`, which the C tool
  left to the C++ standard library's unstable sort. The affected includes all point at empty files, so
  the ROM is unaffected.
- **The Makefile runs the Python tools** through `$(PYTHON)` (default `python3`), and `make tools` now
  just checks the Python version. libpng is no longer required.
- **All six ROMs still match the originals.** Built with the Python tools, `pokefirered.gba` and
  `pokeleafgreen.gba` match the original SHA1s in rev 0, rev 1 and the Switch releases.
- **`build.py` is a single entry point** for building. It checks the toolchain, can download and
  build agbcc (`--install-agbcc`), runs `make` and verifies the ROM's SHA1. It runs with
  `uv run build.py` or `python3 build.py`.
- **Wild encounters were ported to Python** in `python/pokefirered/`:
  - `wild_encounter.py` is a line-by-line port of `src/wild_encounter.c`, covering grass and water
    steps, Sweet Scent, Rock Smash, fishing, repels, flutes, Cleanse Tag, Stench and Illuminate,
    roamers, Altering Cave and Tanoby Unown.
  - `rng.py` ports the game's random number generator (`src/random.c`).
  - `pokemon.py` ports the parts of `src/pokemon.c` that use random numbers when a wild Pokémon is
    created (personality, nature and IVs), plus gender, ability, shininess and Unown letter.
  - `constants.py` reads map, species, flag, var, item and ability values from the C headers and JSON.
  - Encounter tables are read from `src/data/wild_encounters.json`, the same file the C build uses, so
    a data edit changes both.
- **The port is checked against the real C code.** `python/tests` compiles the original
  `wild_encounter.c` for the host machine with small stand-ins for the rest of the game, then runs random
  scenarios through both versions for FireRed and LeafGreen. Across 647,272 calls, every return
  value, random number, encounter-state field and generated Pokémon matched. The tests also check the
  Python tables against the generated C header.
- **`python/simulate_encounters.py`** simulates walking, surfing, biking, Sweet Scent, Rock Smash and
  each fishing rod on any map, and prints each encounter or a species distribution.
- **Documentation** covers `uv` usage in `README.md`, the Python package in `python/README.md`, and a
  note in `INSTALL.md` that libpng is no longer needed.
- **Supporting changes:** `.gitignore` ignores `__pycache__/`, and the VS Code launch and task configs
  are kept in `.vscode/`.
- Everything is committed on the **`python-port`** branch.

## Example: making every wild Pokémon shiny

A Pokémon is shiny when its personality value combines with the player's trainer ID to give a "shiny
value" below 8, which happens about 1 time in 8,192. Personality is rolled when the wild Pokémon is
created, so the change goes in `_generate_wild_mon` in
[`python/pokefirered/wild_encounter.py`](python/pokefirered/wild_encounter.py): keep rolling until the
personality is shiny.

1. Import the two helpers this needs:

   ```diff
    from .pokemon import (Pokemon, create_mon, create_mon_with_nature, get_unown_letter,
   -                      NUM_NATURES)
   +                      get_nature_from_personality, is_shiny, NUM_NATURES)
   ```

2. In `_generate_wild_mon`, keep rolling a personality until it has the chosen nature **and** is shiny:

   ```diff
            if species != "SPECIES_UNOWN":
                nature = self._random() % NUM_NATURES
   -            mon = create_mon_with_nature(state.rng, species, level, nature, state.player_trainer_id)
   +            # Keep rolling personalities until one has the chosen nature AND is shiny.
   +            while True:
   +                personality = state.rng.random32()
   +                if (get_nature_from_personality(personality) == nature
   +                        and is_shiny(state.player_trainer_id, personality)):
   +                    break
   +            mon = create_mon(state.rng, species, level, personality, state.player_trainer_id)
   ```

3. For Unown, which pick their personality by letter, add the same condition in
   `_generate_unown_personality_by_letter`:

   ```diff
   -            if get_unown_letter(personality) == letter:
   +            if get_unown_letter(personality) == letter and is_shiny(self.state.player_trainer_id, personality):
                    return personality
   ```

Try it:

```bash
uv run python/simulate_encounters.py --map MAP_ROUTE1 --steps 150
```

```
step 115     wild battle: PIDGEY Lv5 DOCILE female, IVs 27/6/24/15/14/28, SHINY
step 122     wild battle: PIDGEY Lv3 RASH female, IVs 14/22/9/28/6/5, SHINY
step 130     wild battle: RATTATA Lv2 TIMID female, IVs 20/14/31/20/23/16, SHINY
```

### What this does and doesn't change

- **It changes the Python simulation, not the game.** The ROM is still built from
  `src/wild_encounter.c`, so encounters in an emulator are unaffected.
- **Two tests will fail.** The C-comparison tests in `python/tests` check that the Python behaves
  exactly like the C code, and after this change it deliberately doesn't. Use that as a reminder to
  make the same change in C, then the tests pass again. Changing only the Python is fine for a
  "what if" experiment, but then the comparison tests are expected to fail.
- **To change the game itself**, make the equivalent edit in `GenerateWildMon` in
  `src/wild_encounter.c` (and add `#include "field_specials.h"` for `GetPlayerTrainerId`):

  ```diff
       if (species != SPECIES_UNOWN)
       {
  -        CreateMonWithNature(&gEnemyParty[0], species, level, USE_RANDOM_IVS, Random() % NUM_NATURES);
  +        u8 nature = Random() % NUM_NATURES;
  +        u32 otId = GetPlayerTrainerId();
  +        do
  +        {
  +            personality = Random32();
  +        } while (personality % NUM_NATURES != nature || GET_SHINY_VALUE(otId, personality) >= SHINY_ODDS);
  +        CreateMon(&gEnemyParty[0], species, level, USE_RANDOM_IVS, TRUE, personality, OT_ID_PLAYER_ID, 0);
       }
  ```

  Then rebuild with `uv run build.py --no-compare`. The ROM will no longer match the original game, so
  skip the SHA1 check. This C change was test-compiled with agbcc.

Two kinds of change work differently:

- **Data changes**, such as which species appear, their level ranges, slot odds and encounter rates,
  go in `src/data/wild_encounters.json`. Both the Python port and the ROM read that file, so one edit
  updates both and the tests keep passing.
- **Logic changes**, such as the shiny example, are code. Try them quickly in Python with the
  simulator, then mirror them in `src/wild_encounter.c` when you want them in the game.
