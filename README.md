# Pokémon FireRed and LeafGreen

This is a decompilation of English Pokémon FireRed and LeafGreen.

It builds the following ROM images:

* [**pokefirered.gba**](https://datomatic.no-intro.org/?page=show_record&s=23&n=1616) `sha1: 41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc`
* [**pokeleafgreen.gba**](https://datomatic.no-intro.org/?page=show_record&s=23&n=1617) `sha1: 574fa542ffebb14be69902d1d36f1ec0a4afd71e`
* [**pokefirered_rev1.gba**](https://datomatic.no-intro.org/?page=show_record&s=23&n=1672) `sha1: dd5945db9b930750cb39d00c84da8571feebf417`
* [**pokeleafgreen_rev1.gba**](https://datomatic.no-intro.org/index.php?page=show_record&s=23&n=1668) `sha1: 7862c67bdecbe21d1d69ce082ce34327e1c6ed5e`
* [**pokefirered_switch.gba**](https://datomatic.no-intro.org/index.php?page=show_record&s=23&n=x550) `sha1: baa452d0b24629dd7782cfc07a8984085dde1311`
* [**pokeleafgreen_switch.gba**](https://datomatic.no-intro.org/index.php?page=show_record&s=23&n=x551) `sha1: 62b9fc77549dbc67032eb6cbd0ea6ad3b825690f`

To set up the repository, see [INSTALL.md](INSTALL.md).

## Building with Python

The build tools in `tools/` (graphics, audio, map and text converters) are Python scripts, so the only
external requirements are [uv](https://docs.astral.sh/uv/) (or Python 3.8+), `make`, an ARM binutils
toolchain (devkitARM or `arm-none-eabi-*`) and the agbcc compiler.

From the repository root:

```bash
uv run build.py --check            # report anything that is missing
uv run build.py --install-agbcc    # download and build agbcc into tools/agbcc (first time only)
uv run build.py                    # build pokefirered.gba and check it matches the original
uv run build.py --game leafgreen   # other versions: --rev 1, --rev switch
uv run build.py --clean            # remove build outputs
```

To play the game, open the built ROM (e.g. `pokefirered.gba`) in a GBA emulator such as
[mGBA](https://mgba.io/).

## Game code in Python

The wild encounter code is written in Python: [`src/wild_encounter.py`](src/wild_encounter.py). The build
translates it to C, which is compiled into the ROM, so editing it changes the game. It uses a
C-flavored dialect of Python described in [`python/gba/__init__.py`](python/gba/__init__.py).

```bash
uv run build.py                                         # build the ROM (includes the Python code)
uv run python/simulate_encounters.py --map MAP_ROUTE1   # run the same code on your PC
uv run python -m unittest discover -s python/tests -v   # check the PC and ROM versions agree
```

See [PYTHON_PORT.md](PYTHON_PORT.md) for how it works, a summary of everything that has been ported,
and a step-by-step example of changing the game in Python.

For contacts and other pret projects, see [pret.github.io](https://pret.github.io/).
