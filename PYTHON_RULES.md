# Changing the game in Python: the rules

[`src/wild_encounter.py`](src/wild_encounter.py) is the code that decides which wild Pokémon you meet,
and how often. When you build the ROM, it's translated into C and compiled into the game, so **editing
it changes the game.**

It looks like Python and is Python, but it follows C's rules, because it has to become C that a
Game Boy Advance can run. This guide explains those rules. If you know basic Python (variables,
`if`, loops, functions) you know enough to start.

## Your first change in 5 minutes

Let's make every wild Rattata a Pikachu.

1. Open `src/wild_encounter.py` and find `GenerateWildMon`. Every wild Pokémon is created there.
   Add the two lines marked `+` (without the `+`):

   ```diff
    @static
    def GenerateWildMon(species: u16, level: u8, slot: u8) -> None:
        personality: u32
        chamber: s8
        ZeroEnemyPartyMons()
   +    if species == SPECIES_RATTATA:
   +        species = SPECIES_PIKACHU
        if species != SPECIES_UNOWN:
   ```

2. Try it on your computer first. This runs your edited file, no ROM needed:

   ```bash
   uv run python/simulate_encounters.py --map MAP_ROUTE1 --steps 300 --summary
   ```

   ```
   32 encounters on MAP_ROUTE1 (FIRERED, walk):
     PIDGEY                  20   62.5%
     PIKACHU                 12   37.5%
   ```

3. Build the ROM. `--no-compare` skips the check that the ROM is identical to the original game,
   because now it isn't:

   ```bash
   uv run build.py --no-compare
   ```

4. Open `pokefirered.gba` in a GBA emulator such as [mGBA](https://mgba.io/) and walk into the grass on
   Route 1.

5. Curious what your Python became? Open `src/wild_encounter.c` (it's regenerated on every build):

   ```c
       ZeroEnemyPartyMons();
       if (species == SPECIES_RATTATA)
           species = SPECIES_PIKACHU;
   ```

To undo your changes, use `git checkout src/wild_encounter.py`. Then `uv run build.py` shows
`OK (SHA1 matches the original game)` again.

## The rules

### 1. Every variable has a type, and you declare it first

In normal Python a variable can hold anything. On the GBA every variable is a fixed-size number, so you
say what size when you create it, using a type annotation:

```python
level: u8 = 5          # u8: whole numbers 0 to 255
personality: u32       # declared without a value yet
```

Declarations go **at the top of their block** (the start of a function, or the start of an `if`/loop
body), before any other statement:

```python
def Example() -> u8:
    total: u16 = 0                 # OK: first thing in the function
    if total == 0:
        bonus: u8 = 5              # OK: first thing in this if-block
        total = total + bonus
    count: u8 = 3                  # ERROR: comes after other statements
```

After a variable is declared, assign to it normally (`total = 7`) without repeating the type. Assigning
to a name you never declared is an error.

| Type | Holds | Notes |
| --- | --- | --- |
| `u8`, `u16`, `u32` | 0 to 255 / 65,535 / 4,294,967,295 | "u" = unsigned (no negatives) |
| `s8`, `s16`, `s32` | -128 to 127 / -32,768 to 32,767 / about ±2.1 billion | "s" = signed |
| `bool8`, `bool32` | `TRUE` or `FALSE` | stored as 1 and 0 |
| `Ptr[T]` | a pointer to a `T` | see rule 6 |

### 2. Numbers wrap around, and there are no decimals

A `u8` can't hold 300. It keeps only the bits that fit and becomes 44. Going below zero wraps the other
way: a `u8` holding 0 minus 1 becomes 255. Think of a car's odometer rolling over.

- **No decimal numbers.** `2.5` is an error; the GBA has no floating-point hardware.
- **Use `//` for division**, not `/`. It rounds toward zero like C, so `-7 // 2` is `-3` here (in normal
  Python it would be `-4`).
- `**` (power) isn't available; multiply instead.
- To scale by a fraction, multiply first, then divide: `rate * 80 // 100` is 80% of `rate`.

### 3. Use `TRUE`, `FALSE` and `None`

| Write | Not | Means |
| --- | --- | --- |
| `TRUE` / `FALSE` | `True` / `False` | yes / no (the C versions) |
| `x is None` / `x is not None` | | "x points to nothing" (C's `NULL`) |

`and`, `or` and `not` work as usual. So do `==`, `!=`, `<`, `<=`, `>`, `>=`, `+`, `-`, `*`, `%`,
and the bit operators `&`, `|`, `^`, `~`, `<<`, `>>`.

### 4. Functions say what goes in and what comes out

Every parameter has a type, and so does the return value. Use `-> None` when nothing is returned:

```python
@static
def DoubleIt(value: u8) -> u16:
    return value * 2
```

- `@static` means the function is private to this file. Leave it off functions that other parts of
  the game call (like `StandardWildEncounter`); those are declared in `include/wild_encounter.h`.
- No default values, `*args` or keyword arguments.
- You can call the other functions in this file, and the game functions listed in
  [`python/gba/game.py`](python/gba/game.py) (`Random()`, `VarGet()`, `FlagGet()`, `CreateMon()`, ...).

### 5. Loops and branches

`if`/`elif`/`else` and `while` work as usual. `for` loops must use `range`:

```python
i: u8
for i in range(0, PARTY_SIZE):      # i = 0, 1, ..., 5   (declare i first)
    ...
```

There are two forms Python doesn't have:

**"Do this, then check":** a `while True:` loop whose **last** line is `if ...: break`. It becomes C's
`do { ... } while` loop.

```python
while True:
    personality = Random32()
    if personality % NUM_NATURES == nature:
        break
```

**Choosing between fixed values** (`switch`): write a `with switch(...) as case:` block containing one
`if`/`elif`/`else` chain:

```python
with switch(rod) as case:
    if case(OLD_ROD):
        ...
    elif case(GOOD_ROD, SUPER_ROD):    # either value
        ...
    else:                              # anything else
        ...
```

Don't use `break` inside a `case`, or `continue` inside a do-while loop. Their meaning differs between
Python and C, so the translator stops you.

### 6. Pointers: when a value lives somewhere else

Some values are passed around as a location instead of a copy. These are pointers, like a reference to
a list in Python. You'll mostly see them for structs:

```python
info: Ptr[Const[WildPokemonInfo]] = gWildMonHeaders[headerId].landMonsInfo
rate: u8 = info.encounterRate      # just use a dot, as in Python
```

- `Ptr[X]` is "pointer to an X", and `Const[X]` means "you may read it but not change it".
- `addr(x)` gives a pointer to `x`. Use it to let another function fill in your variable, as with
  `PlayerGetDestCoords(addr(x), addr(y))`.
- `p[0]` reads or writes the value a pointer points at.
- A pointer that points at nothing is `None`.

### 7. Constants and global variables

- **Game constants are available by name:** `SPECIES_PIKACHU`, `MAP_ROUTE1`, `ITEM_CLEANSE_TAG`,
  `FLAG_...`, `VAR_...`. They come from the C headers in `include/`, so search there (or in
  `include/constants/`) to find one.
- **Your own constants:** `MY_BONUS = define(5)`. Write them at the top level of the file, not inside
  a function.
- **Global variables** (like `sWildEncounterData` or `gSaveBlock1Ptr`) can be read and changed from
  any function, without Python's `global` keyword.

### 8. What you can't use

Inside game code there are **no** lists, dicts, sets, tuples or strings. There are also no classes
(other than `Struct`), floats, `import`s, `try`/`except`, `lambda`, generators or `print()`. The GBA
only has what the C code gives it. If you need a table of values, look at how `sUnownLetterSlots` is
declared at the top level.

## Changing data instead of code

You don't need code to change *which* Pokémon live where. The species, level ranges and encounter rates
for every map are in [`src/data/wild_encounters.json`](src/data/wild_encounters.json). Edit that file,
rebuild, and both the ROM and the simulator pick up the change. Use `wild_encounter.py` for changes to
how encounters *work*.

## Ideas to try

| Change | Where | What to write |
| --- | --- | --- |
| Wild Pokémon are 5 levels higher | end of `ChooseWildMonLevel` | `return lo + res + 5` |
| Swap one species for another | start of `GenerateWildMon` | `if species == SPECIES_RATTATA: species = SPECIES_PIKACHU` |
| Every wild Pokémon is shiny | `GenerateWildMon` | see [PYTHON_PORT.md](PYTHON_PORT.md#example-making-every-wild-pokémon-shiny) |
| More encounters per step | `DoWildEncounterRateTest` | change `encounterRate *= 16` to `*= 32` |

The first two were tried while writing this guide. On Route 1, levels 2–5 became 7–10, and every
Rattata became a Pikachu.

## When something goes wrong

**The translator found a rule problem.** It names the line in your `.py` file:

```
src/wild_encounter.py:219: error: 'bonus' is not declared (give it a type, e.g. bonus: u8 = ...)
```

| Message | Fix |
| --- | --- |
| `'x' is not declared` | Add a type the first time: `x: u8 = ...` (rule 1) |
| `declared at the start of a block` | Move the declaration above the other statements (rule 1) |
| `use // for integer division` | Replace `/` with `//` (rule 2) |
| `use TRUE and FALSE` | Replace `True`/`False` (rule 3) |
| `cannot tell what 'x' is; declare its type` | Give `x` a type so the translator knows whether it's a pointer |
| `lists, dicts, tuples and sets only exist as initializers...` | Rule 8 |

**The C compiler complained.** Its message points at `src/wild_encounter.c`. Open that file at the line
given, find the matching code in your `.py`, and fix it there. Never edit the `.c` file: your changes
would be overwritten on the next build.

**You want to call a game function that isn't available yet.** If it exists in the game's C code
(search `include/`), add it to `FUNCTIONS` in [`python/gba/game.py`](python/gba/game.py) with its
return type, and add its header with `include("...")`. The ROM build then works. To also use it in the
simulator and tests, add a Python version in [`python/gba/runtime.py`](python/gba/runtime.py) and a
stand-in in `python/tests/c_harness/harness.c`.

**Checking your work:**

```bash
uv run python -m unittest discover -s python/tests -v
```

The tests check that your code behaves the same on your PC and in the ROM. They stay green after
gameplay changes; a failure means the simulator would no longer show what the game does.
