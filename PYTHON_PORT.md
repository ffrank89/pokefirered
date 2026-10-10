# Writing game code in Python

## How it works

```
src/wild_encounter.py ──tools/py2c──▶ src/wild_encounter.c ──agbcc──▶ machine code ──▶ pokefirered.gba
        │
        └──gba.runtime──▶ runs on your PC (simulator and tests)
```

- `src/wild_encounter.py` is the source of the game's wild encounter code. The ROM gets its wild
  encounters from it, so **editing this file changes the game**.
- The build translates it to C with `tools/py2c/py2c.py`. The GBA's compiler (agbcc) then turns that
  C into machine code like every other C file. `src/wild_encounter.c` is a generated file (ignored by
  git); open it to see the C your Python became.
- The file is written in the **gba dialect** of Python: normal Python syntax with C's rules. Variables
  have fixed-size types like `u8` (0–255), there are pointers and structs, and there are no lists or
  dicts inside functions. The cheat sheet is at the top of [`python/gba/__init__.py`](python/gba/__init__.py).
- The same file also runs on your PC through `gba.runtime`, which gives it C's integer behaviour.
  `python/simulate_encounters.py` and the tests use it this way, so they always run the code that is
  in the game.
- **To start making changes**, read [PYTHON_RULES.md](PYTHON_RULES.md): the dialect rules explained for
  Python programmers, with a first change you can make in five minutes.

## What has been done

- **Scope.** The game is a decompilation of Pokémon FireRed/LeafGreen into C. Python can't run on a GBA,
  so Python reaches the ROM by being translated into C at build time.
- **All 11 build tools in `tools/` are Python**: `gbagfx`, `preproc`, `scaninc`, `mapjson`,
  `jsonproc` (including a port of the Inja template engine), `mid2agb`, `wav2agb`, `ramscrgen`,
  `gbafix`, `bin2c` and `rsfont`. Each one was checked byte-for-byte against the original C tool before
  it replaced it. That covered about 9,900 graphics conversions, all 347 songs, all 477 sound samples,
  all 425 maps, every source file and all JSON templates. The original C/C++ sources were removed (they
  remain in git history), and libpng is no longer needed.
- **`build.py` is a single entry point** for building. It checks the toolchain, can install the agbcc
  compiler, runs `make` and verifies the ROM's SHA1. It runs with `uv run build.py`.
- **The gba dialect and translator.** The `python/gba` package defines the dialect: C types (`u8`,
  `Ptr[...]`, `Struct`...), `switch`, `define`, `include` and so on. `tools/py2c/py2c.py` turns dialect
  files into C, keeping the comments, and reports dialect mistakes (such as an undeclared variable or
  `/` instead of `//`) with line numbers.
- **Wild encounters are written in Python.** `src/wild_encounter.c` was rewritten as
  `src/wild_encounter.py`, and the Makefile generates the C from it (`python_src_rules.mk`).
- **The generated C compiles to the same machine code as the original.** agbcc's assembly output for
  the original and the generated `wild_encounter.c` is identical, apart from internal label numbers.
  All six ROMs (FireRed and LeafGreen, rev 0, rev 1 and Switch) still match the original SHA1s.
- **`gba.runtime` runs dialect files on a PC.** It provides working versions of the game functions
  the code calls (`Random`, `CreateMon`, `VarGet`...) and the game state they use.
- **Tests check that the PC version behaves like the ROM version.** `python/tests` translates
  `src/wild_encounter.py` to C, compiles that C for your computer, and runs random scenarios through
  both. Across 647,508 calls, every result, random number and generated Pokémon matched.
- **`python/simulate_encounters.py`** runs the encounter code to show what you'd find on any map
  (walking, surfing, Sweet Scent, Rock Smash, each fishing rod).
- **Documentation:** `README.md` (building with `uv`), `python/README.md`, and this file.
- Everything is on the **`python-port`** branch.

## Example: making every wild Pokémon shiny

A Pokémon is shiny when its personality value, combined with the player's trainer ID, gives a "shiny
value" below 8:

```
shiny value = (high half of trainer ID) ^ (low half of trainer ID) ^ (high half of personality) ^ (low half of personality)
```

Normally that happens 1 time in 8,192. The personality is chosen in `GenerateWildMon` in
[`src/wild_encounter.py`](src/wild_encounter.py), which calls `CreateMonWithNature`. To force shininess,
we choose the personality ourselves. We pick its low half at random, then *calculate* a high half that
makes the shiny value small.

1. Add the header that declares `GetPlayerTrainerId`, next to the other includes:

   ```diff
    include("constants/items.h")
   +include("field_specials.h")
   ```

2. Change `GenerateWildMon`:

   ```diff
        ZeroEnemyPartyMons()
        if species != SPECIES_UNOWN:
   -        CreateMonWithNature(addr(gEnemyParty[0]), species, level, USE_RANDOM_IVS, Random() % NUM_NATURES)
   +        nature: u8 = Random() % NUM_NATURES
   +        otId: u32 = GetPlayerTrainerId()
   +        lo: u16
   +        hi: u16
   +
   +        # Build a shiny personality instead of waiting for one at random (1 in 8192):
   +        # pick the low half at random, then choose the high half so that the shiny value
   +        # (hi ^ lo ^ both halves of the trainer ID) is below SHINY_ODDS.
   +        # Repeat until the nature matches too (about 25 tries).
   +        while True:
   +            lo = Random()
   +            hi = (otId >> 16) ^ (otId & 0xFFFF) ^ lo ^ (Random() % SHINY_ODDS)
   +            personality = (hi << 16) | lo
   +            if personality % NUM_NATURES == nature:
   +                break
   +        CreateMon(addr(gEnemyParty[0]), species, level, USE_RANDOM_IVS, TRUE, personality, OT_ID_PLAYER_ID, 0)
   ```

   Note the dialect rules at work. `nature`, `otId`, `lo` and `hi` are declared with types at the top of
   their block, as C requires. `while True: ... if ...: break` is how the dialect writes C's
   `do { } while` loop. And because `hi` is a `u16`, only the low 16 bits of the calculation are kept,
   which is exactly what we want.

3. Unown pick their personality by letter in `GenerateUnownPersonalityByLetter`, so build it the same
   way there:

   ```diff
    def GenerateUnownPersonalityByLetter(letter: u8) -> u32:
        personality: u32
   +    otId: u32 = GetPlayerTrainerId()
   +    lo: u16
   +    hi: u16
   +    # Shiny, built the same way as in GenerateWildMon; repeat until the letter matches.
        while True:
   -        personality = (Random() << 16) | Random()
   +        lo = Random()
   +        hi = (otId >> 16) ^ (otId & 0xFFFF) ^ lo ^ (Random() % SHINY_ODDS)
   +        personality = (hi << 16) | lo
            if GetUnownLetterByPersonalityLoByte(personality) == letter:
                break
   ```

4. Build the ROM. Use `--no-compare`, because the ROM no longer matches the original game:

   ```bash
   uv run build.py --no-compare
   ```

   `src/wild_encounter.c` now contains the C version of your change:

   ```c
           do
           {
               lo = Random();
               hi = (otId >> 16) ^ (otId & 0xFFFF) ^ lo ^ (Random() % SHINY_ODDS);
               personality = (hi << 16) | lo;
           } while (personality % NUM_NATURES != nature);
           CreateMon(&gEnemyParty[0], species, level, USE_RANDOM_IVS, TRUE, personality, OT_ID_PLAYER_ID, 0);
   ```

   Load `pokefirered.gba` into an emulator such as [mGBA](https://mgba.io/) or OpenEmu, and every wild
   Pokémon will be shiny.

5. Try it on your PC as well. The simulator runs the same edited file:

   ```bash
   uv run python/simulate_encounters.py --map MAP_ROUTE1 --steps 120
   ```

   ```
   step 95      wild battle: RATTATA Lv3 CALM female, IVs 14/21/11/4/28/31, SHINY
   ```

   The tests still pass, because they check that the PC version and the ROM version of your code
   agree, not that the code matches the original game.

### Why not just reroll until it's shiny?

The first version of this example did exactly that: `while True: personality = Random32()` until the
personality happened to be shiny *and* have the right nature. It worked in the simulator, but **the
game froze on the first wild encounter.** The odds are 1 in 8,192 × 1 in 25, which is about 200,000
attempts. Counting in the simulator gave a median of about 330,000 random numbers per encounter, and
up to 1.2 million.

A PC does that in a blink. The GBA's processor runs at 16.8 MHz and has to do division in software, so
each attempt costs hundreds of cycles, and the loop took several seconds or more with the screen
frozen. The version above needs a median of about 41 random numbers.

The lesson is that **the simulator shows what your code does, not how fast it runs on a GBA.**
Avoid loops that wait for something rare to happen by chance; calculate the result instead.
[PYTHON_RULES.md](PYTHON_RULES.md) has this as rule 9.

## Things to know

- **Data vs. code.** Which species appear, their level ranges and the encounter rates live in
  `src/data/wild_encounters.json`, not in code. Edit that file for those changes.
- **Calling other parts of the game.** `src/wild_encounter.py` can call any C function or macro in the
  game, as long as it's declared in [`python/gba/game.py`](python/gba/game.py) (so the translator
  knows its type). To also use it in the simulator and tests, implement it in
  [`python/gba/runtime.py`](python/gba/runtime.py) and add a stand-in to
  `python/tests/c_harness/harness.c`. `GetPlayerTrainerId` and `GET_SHINY_VALUE` were added this way
  for the shiny example.
- **Only wild encounters are in Python so far.** The rest of the game is still C. Another C file can
  be ported the same way: write `src/<name>.py` in the dialect, delete `src/<name>.c`, and add
  `src/<name>.c` to `.gitignore`. Check the result by building the ROM and confirming the SHA1 still
  matches before changing behaviour.
- **What the dialect doesn't support yet.** Pointer arithmetic, `goto`, function pointers, unions and
  `#ifdef` aren't supported, because wild encounters don't use them. Other C files may need the
  translator extended.
