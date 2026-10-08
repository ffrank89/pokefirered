"""Declarations of the parts of the game's C code that dialect files use.

These mirror declarations in include/*.h. They tell tools/py2c/py2c.py the
types of things defined in other C files (for example that gSaveBlock1Ptr is a
pointer, so gSaveBlock1Ptr.location becomes gSaveBlock1Ptr->location), and
they give editors something to auto-complete.

Nothing here is translated into C. When a dialect file runs on a PC,
gba.runtime replaces these placeholders with working implementations.

Constants from the C headers (MAP_*, SPECIES_*, FLAG_*, ITEM_*, ...) are
available by name too; see gba.constants.
"""

from gba import Array, Const, Ptr, Struct, bool8, bool32, s8, s16, u8, u16, u32, void

# ---------------------------------------------------------------------------
# Structs (include/global.h, include/wild_encounter.h, include/pokemon.h)
# ---------------------------------------------------------------------------


class WildPokemon(Struct):
    minLevel: u8
    maxLevel: u8
    species: u16


class WildPokemonInfo(Struct):
    encounterRate: u8
    wildPokemon: Ptr[Const[WildPokemon]]


class WildPokemonHeader(Struct):
    mapGroup: u8
    mapNum: u8
    landMonsInfo: Ptr[Const[WildPokemonInfo]]
    waterMonsInfo: Ptr[Const[WildPokemonInfo]]
    rockSmashMonsInfo: Ptr[Const[WildPokemonInfo]]
    fishingMonsInfo: Ptr[Const[WildPokemonInfo]]


class WarpData(Struct):
    mapGroup: s8
    mapNum: s8
    warpId: s8
    x: s16
    y: s16


class Roamer(Struct):
    ivs: u32
    personality: u32
    species: u16
    hp: u16
    level: u8
    status: u8
    active: bool8


class SaveBlock1(Struct):
    location: WarpData
    roamer: Roamer


class Pokemon(Struct):
    """struct Pokemon. Its contents are only accessed through GetMonData()."""


# ---------------------------------------------------------------------------
# Global variables defined in other files
# ---------------------------------------------------------------------------

EXTERNS = {
    "gSaveBlock1Ptr": Ptr[SaveBlock1],
    "gPlayerParty": Array[Pokemon, 6],
    "gEnemyParty": Array[Pokemon, 6],
    "gSpecialVar_Result": u16,
    "gQuestLogState": u8,
    "gRngValue": u32,
    "gWildMonHeaders": Array[Const[WildPokemonHeader]],
    "EventScript_RepelWoreOff": Array[Const[u8]],
}

gSaveBlock1Ptr = None
gPlayerParty = None
gEnemyParty = None
gSpecialVar_Result = 0
gQuestLogState = 0
gRngValue = 0
gWildMonHeaders = None
EventScript_RepelWoreOff = None

# ---------------------------------------------------------------------------
# Functions and function-like macros defined in other files
# ---------------------------------------------------------------------------


def _placeholder(name):
    def function(*args):
        raise RuntimeError("%s() only works when the file is run through gba.runtime" % name)
    function.__name__ = name
    return function


# name -> return type
FUNCTIONS = {
    # random.h
    "Random": u16, "Random32": u32, "ISO_RANDOMIZE2": u32,
    # event_data.h
    "VarGet": u16, "VarSet": bool8, "FlagGet": bool8,
    # fieldmap.h, field_player_avatar.h, metatile_behavior.h
    "ExtractMetatileAttribute": u32, "MapGridGetMetatileAttributeAt": u32, "PlayerGetDestCoords": void,
    "TestPlayerAvatarFlags": bool8, "MetatileBehavior_IsBridge": bool8,
    # pokemon.h
    "ZeroEnemyPartyMons": void, "CreateMon": void, "CreateMonWithNature": void, "GetMonData": u32,
    "GetMonAbility": u8,
    # roamer.h, battle_setup.h
    "TryStartRoamerEncounter": u8, "StartRoamerBattle": void, "StartWildBattle": void,
    # overworld.h, link.h, script.h
    "IncrementGameStat": void, "InUnionRoom": bool32, "ScriptContext_SetupScript": void,
    # macros
    "MAP_GROUP": u16, "MAP_NUM": u16, "GET_UNOWN_LETTER": u32, "GET_SHINY_VALUE": u32, "max": u32, "min": u32,
    # field_specials.h
    "GetPlayerTrainerId": u32,
}

for _name in FUNCTIONS:
    globals()[_name] = _placeholder(_name)
del _name

# Constants from the C headers.
from gba.constants import *  # noqa: E402,F401,F403
