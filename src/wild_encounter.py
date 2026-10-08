"""Wild Pokémon encounters: grass, water, Rock Smash, fishing, Sweet Scent and repels.

This file is the source of src/wild_encounter.c. The build translates it to C
with tools/py2c/py2c.py, and agbcc compiles that C into the ROM, so changes made
here change the game. It is written in the gba dialect of Python: Python syntax
with C's rules (see python/gba/__init__.py). gba.runtime can also run it on a
PC, which is what python/simulate_encounters.py and the tests use.
"""

from gba import *  # noqa: F401,F403
from gba.game import *  # noqa: F401,F403

include("global.h")
include("random.h")
include("wild_encounter.h")
include("event_data.h")
include("fieldmap.h")
include("random.h")
include("roamer.h")
include("field_player_avatar.h")
include("battle_setup.h")
include("overworld.h")
include("metatile_behavior.h")
include("event_scripts.h")
include("script.h")
include("link.h")
include("quest_log.h")
include("constants/maps.h")
include("constants/abilities.h")
include("constants/items.h")

MAX_ENCOUNTER_RATE = define(1600)

HEADER_NONE = define(0xFFFF)


class WildEncounterData(Struct):
    rngState: u32
    prevMetatileBehavior: u16
    encounterRateBuff: u16
    stepsSinceLastEncounter: u8
    abilityEffect: u8
    leadMonHeldItem: u16


sWildEncounterData: Static[Ewram[WildEncounterData]] = {}
sWildEncountersDisabled: Static[Ewram[bool8]] = FALSE

include("data/wild_encounters.h")

sUnownLetterSlots: Static[Const[Array[Array[u8, LAND_WILD_COUNT]]]] = [
    #  A   A   A   A   A   A   A   A   A   A   A   ?
    [ 0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0, 27],
    #  C   C   C   D   D   D   H   H   H   U   U   O
    [ 2,  2,  2,  3,  3,  3,  7,  7,  7, 20, 20, 14],
    #  N   N   N   N   S   S   S   S   I   I   E   E
    [13, 13, 13, 13, 18, 18, 18, 18,  8,  8,  4,  4],
    #  P   P   L   L   J   J   R   R   R   Q   Q   Q
    [15, 15, 11, 11,  9,  9, 17, 17, 17, 16, 16, 16],
    #  Y   Y   T   T   G   G   G   F   F   F   K   K
    [24, 24, 19, 19,  6,  6,  6,  5,  5,  5, 10, 10],
    #  V   V   V   W   W   W   X   X   M   M   B   B
    [21, 21, 21, 22, 22, 22, 23, 23, 12, 12,  1,  1],
    #  Z   Z   Z   Z   Z   Z   Z   Z   Z   Z   Z   !
    [25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 26],
]


def DisableWildEncounters(state: bool8) -> None:
    sWildEncountersDisabled = state


@static
def ChooseWildMonIndex_Land() -> u8:
    rand: u8 = Random() % ENCOUNTER_CHANCE_LAND_MONS_TOTAL

    if rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_0:
        return 0
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_0 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_1:
        return 1
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_1 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_2:
        return 2
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_2 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_3:
        return 3
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_3 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_4:
        return 4
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_4 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_5:
        return 5
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_5 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_6:
        return 6
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_6 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_7:
        return 7
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_7 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_8:
        return 8
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_8 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_9:
        return 9
    elif rand >= ENCOUNTER_CHANCE_LAND_MONS_SLOT_9 and rand < ENCOUNTER_CHANCE_LAND_MONS_SLOT_10:
        return 10
    else:
        return 11


@static
def ChooseWildMonIndex_WaterRock() -> u8:
    rand: u8 = Random() % ENCOUNTER_CHANCE_WATER_MONS_TOTAL

    if rand < ENCOUNTER_CHANCE_WATER_MONS_SLOT_0:
        return 0
    elif rand >= ENCOUNTER_CHANCE_WATER_MONS_SLOT_0 and rand < ENCOUNTER_CHANCE_WATER_MONS_SLOT_1:
        return 1
    elif rand >= ENCOUNTER_CHANCE_WATER_MONS_SLOT_1 and rand < ENCOUNTER_CHANCE_WATER_MONS_SLOT_2:
        return 2
    elif rand >= ENCOUNTER_CHANCE_WATER_MONS_SLOT_2 and rand < ENCOUNTER_CHANCE_WATER_MONS_SLOT_3:
        return 3
    else:
        return 4


@static
def ChooseWildMonIndex_Fishing(rod: u8) -> u8:
    wildMonIndex: u8 = 0
    rand: u8 = Random() % max(max(ENCOUNTER_CHANCE_FISHING_MONS_OLD_ROD_TOTAL, ENCOUNTER_CHANCE_FISHING_MONS_GOOD_ROD_TOTAL),
                              ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_TOTAL)

    with switch(rod) as case:
        if case(OLD_ROD):
            if rand < ENCOUNTER_CHANCE_FISHING_MONS_OLD_ROD_SLOT_0:
                wildMonIndex = 0
            else:
                wildMonIndex = 1
        elif case(GOOD_ROD):
            if rand < ENCOUNTER_CHANCE_FISHING_MONS_GOOD_ROD_SLOT_2:
                wildMonIndex = 2
            if rand >= ENCOUNTER_CHANCE_FISHING_MONS_GOOD_ROD_SLOT_2 and rand < ENCOUNTER_CHANCE_FISHING_MONS_GOOD_ROD_SLOT_3:
                wildMonIndex = 3
            if rand >= ENCOUNTER_CHANCE_FISHING_MONS_GOOD_ROD_SLOT_3 and rand < ENCOUNTER_CHANCE_FISHING_MONS_GOOD_ROD_SLOT_4:
                wildMonIndex = 4
        elif case(SUPER_ROD):
            if rand < ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_5:
                wildMonIndex = 5
            if rand >= ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_5 and rand < ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_6:
                wildMonIndex = 6
            if rand >= ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_6 and rand < ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_7:
                wildMonIndex = 7
            if rand >= ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_7 and rand < ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_8:
                wildMonIndex = 8
            if rand >= ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_8 and rand < ENCOUNTER_CHANCE_FISHING_MONS_SUPER_ROD_SLOT_9:
                wildMonIndex = 9
    return wildMonIndex


@static
def ChooseWildMonLevel(info: Ptr[Const[WildPokemon]]) -> u8:
    lo: u8
    hi: u8
    mod: u8
    res: u8
    if info.maxLevel >= info.minLevel:
        lo = info.minLevel
        hi = info.maxLevel
    else:
        lo = info.maxLevel
        hi = info.minLevel
    mod = hi - lo + 1
    res = Random() % mod
    return lo + res


@static
def GetCurrentMapWildMonHeaderId() -> u16:
    i: u16

    for i in count(0):
        wildHeader: Ptr[Const[WildPokemonHeader]] = addr(gWildMonHeaders[i])
        if wildHeader.mapGroup == MAP_GROUP(MAP_UNDEFINED):
            break

        if (gWildMonHeaders[i].mapGroup == gSaveBlock1Ptr.location.mapGroup
                and gWildMonHeaders[i].mapNum == gSaveBlock1Ptr.location.mapNum):
            if (gSaveBlock1Ptr.location.mapGroup == MAP_GROUP(MAP_SIX_ISLAND_ALTERING_CAVE)
                    and gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SIX_ISLAND_ALTERING_CAVE)):
                alteringCaveId: u16 = VarGet(VAR_ALTERING_CAVE_WILD_SET)
                if alteringCaveId >= NUM_ALTERING_CAVE_TABLES:
                    alteringCaveId = 0

                i += alteringCaveId

            if not UnlockedTanobyOrAreNotInTanoby():
                break
            return i

    return HEADER_NONE


@static
def UnlockedTanobyOrAreNotInTanoby() -> bool8:
    if FlagGet(FLAG_SYS_UNLOCKED_TANOBY_RUINS):
        return TRUE
    if gSaveBlock1Ptr.location.mapGroup != MAP_GROUP(MAP_SEVEN_ISLAND_TANOBY_RUINS_DILFORD_CHAMBER):
        return TRUE
    if not (gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER)
            or gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_LIPTOO_CHAMBER)
            or gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_WEEPTH_CHAMBER)
            or gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_DILFORD_CHAMBER)
            or gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_SCUFIB_CHAMBER)
            or gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_RIXY_CHAMBER)
            or gSaveBlock1Ptr.location.mapNum == MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_VIAPOIS_CHAMBER)):
        return TRUE
    return FALSE


@static
def GenerateWildMon(species: u16, level: u8, slot: u8) -> None:
    personality: u32
    chamber: s8
    ZeroEnemyPartyMons()
    if species != SPECIES_UNOWN:
        CreateMonWithNature(addr(gEnemyParty[0]), species, level, USE_RANDOM_IVS, Random() % NUM_NATURES)
    else:
        chamber = gSaveBlock1Ptr.location.mapNum - MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER)
        personality = GenerateUnownPersonalityByLetter(sUnownLetterSlots[chamber][slot])
        CreateMon(addr(gEnemyParty[0]), species, level, USE_RANDOM_IVS, TRUE, personality, FALSE, 0)


@static
def GenerateUnownPersonalityByLetter(letter: u8) -> u32:
    personality: u32
    while True:
        personality = (Random() << 16) | Random()
        if GetUnownLetterByPersonalityLoByte(personality) == letter:
            break
    return personality


def GetUnownLetterByPersonalityLoByte(personality: u32) -> u8:
    return GET_UNOWN_LETTER(personality)


WILD_AREA_LAND, WILD_AREA_WATER, WILD_AREA_ROCKS, WILD_AREA_FISHING = enum(4)

WILD_CHECK_REPEL = define(0x1)
WILD_CHECK_KEEN_EYE = define(0x2)


@static
def TryGenerateWildMon(info: Ptr[Const[WildPokemonInfo]], area: u8, flags: u8) -> bool8:
    slot: u8 = 0
    level: u8
    with switch(area) as case:
        if case(WILD_AREA_LAND):
            slot = ChooseWildMonIndex_Land()
        elif case(WILD_AREA_WATER):
            slot = ChooseWildMonIndex_WaterRock()
        elif case(WILD_AREA_ROCKS):
            slot = ChooseWildMonIndex_WaterRock()
    level = ChooseWildMonLevel(addr(info.wildPokemon[slot]))
    if flags == WILD_CHECK_REPEL and not IsWildLevelAllowedByRepel(level):
        return FALSE
    GenerateWildMon(info.wildPokemon[slot].species, level, slot)
    return TRUE


@static
def GenerateFishingEncounter(info: Ptr[Const[WildPokemonInfo]], rod: u8) -> u16:
    slot: u8 = ChooseWildMonIndex_Fishing(rod)
    level: u8 = ChooseWildMonLevel(addr(info.wildPokemon[slot]))
    GenerateWildMon(info.wildPokemon[slot].species, level, slot)
    return info.wildPokemon[slot].species


@static
def DoWildEncounterRateDiceRoll(encounterRate: u16) -> bool8:
    if WildEncounterRandom() % MAX_ENCOUNTER_RATE < encounterRate:
        return TRUE
    return FALSE


@static
def DoWildEncounterRateTest(encounterRate: u32, ignoreAbility: bool8) -> bool8:
    encounterRate *= 16
    if TestPlayerAvatarFlags(PLAYER_AVATAR_FLAG_MACH_BIKE | PLAYER_AVATAR_FLAG_ACRO_BIKE):
        encounterRate = encounterRate * 80 // 100
    encounterRate += sWildEncounterData.encounterRateBuff * 16 // 200
    ApplyFluteEncounterRateMod(addr(encounterRate))
    ApplyCleanseTagEncounterRateMod(addr(encounterRate))
    if not ignoreAbility:
        with switch(sWildEncounterData.abilityEffect) as case:
            if case(1):
                encounterRate //= 2
            elif case(2):
                encounterRate *= 2
    if encounterRate > MAX_ENCOUNTER_RATE:
        encounterRate = MAX_ENCOUNTER_RATE
    return DoWildEncounterRateDiceRoll(encounterRate)


@static
def GetAbilityEncounterRateModType() -> u8:
    sWildEncounterData.abilityEffect = 0
    if not GetMonData(addr(gPlayerParty[0]), MON_DATA_SANITY_IS_EGG):
        ability: u8 = GetMonAbility(addr(gPlayerParty[0]))
        if ability == ABILITY_STENCH:
            sWildEncounterData.abilityEffect = 1
        elif ability == ABILITY_ILLUMINATE:
            sWildEncounterData.abilityEffect = 2
    return sWildEncounterData.abilityEffect


@static
def DoGlobalWildEncounterDiceRoll() -> bool8:
    if (Random() % 100) >= 60:
        return FALSE
    return TRUE


def StandardWildEncounter(currMetatileAttrs: u32, previousMetatileBehavior: u16) -> bool8:
    headerId: u16
    roamer: Ptr[Roamer]

    if sWildEncountersDisabled == TRUE:
        return FALSE

    headerId = GetCurrentMapWildMonHeaderId()
    if headerId != HEADER_NONE:
        if ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_ENCOUNTER_TYPE) == TILE_ENCOUNTER_LAND:
            if gWildMonHeaders[headerId].landMonsInfo is None:
                return FALSE
            elif (previousMetatileBehavior != ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_BEHAVIOR)
                    and not DoGlobalWildEncounterDiceRoll()):
                return FALSE
            if DoWildEncounterRateTest(gWildMonHeaders[headerId].landMonsInfo.encounterRate, FALSE) != TRUE:
                AddToWildEncounterRateBuff(gWildMonHeaders[headerId].landMonsInfo.encounterRate)
                return FALSE

            elif TryStartRoamerEncounter() == TRUE:
                roamer = addr(gSaveBlock1Ptr.roamer)
                if not IsWildLevelAllowedByRepel(roamer.level):
                    return FALSE

                StartRoamerBattle()
                return TRUE
            else:
                # try a regular wild land encounter
                if TryGenerateWildMon(gWildMonHeaders[headerId].landMonsInfo, WILD_AREA_LAND, WILD_CHECK_REPEL) == TRUE:
                    StartWildBattle()
                    return TRUE
                else:
                    AddToWildEncounterRateBuff(gWildMonHeaders[headerId].landMonsInfo.encounterRate)
        elif (ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_ENCOUNTER_TYPE) == TILE_ENCOUNTER_WATER
                or (TestPlayerAvatarFlags(PLAYER_AVATAR_FLAG_SURFING)
                    and MetatileBehavior_IsBridge(ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_BEHAVIOR)) == TRUE)):
            if gWildMonHeaders[headerId].waterMonsInfo is None:
                return FALSE
            elif (previousMetatileBehavior != ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_BEHAVIOR)
                    and not DoGlobalWildEncounterDiceRoll()):
                return FALSE
            elif DoWildEncounterRateTest(gWildMonHeaders[headerId].waterMonsInfo.encounterRate, FALSE) != TRUE:
                AddToWildEncounterRateBuff(gWildMonHeaders[headerId].waterMonsInfo.encounterRate)
                return FALSE

            if TryStartRoamerEncounter() == TRUE:
                roamer = addr(gSaveBlock1Ptr.roamer)
                if not IsWildLevelAllowedByRepel(roamer.level):
                    return FALSE

                StartRoamerBattle()
                return TRUE
            else:  # try a regular surfing encounter
                if TryGenerateWildMon(gWildMonHeaders[headerId].waterMonsInfo, WILD_AREA_WATER, WILD_CHECK_REPEL) == TRUE:
                    StartWildBattle()
                    return TRUE
                else:
                    AddToWildEncounterRateBuff(gWildMonHeaders[headerId].waterMonsInfo.encounterRate)

    return FALSE


def RockSmashWildEncounter() -> None:
    headerIdx: u16 = GetCurrentMapWildMonHeaderId()
    if headerIdx == HEADER_NONE:
        gSpecialVar_Result = FALSE
    elif gWildMonHeaders[headerIdx].rockSmashMonsInfo is None:
        gSpecialVar_Result = FALSE
    elif DoWildEncounterRateTest(gWildMonHeaders[headerIdx].rockSmashMonsInfo.encounterRate, TRUE) != TRUE:
        gSpecialVar_Result = FALSE
    elif TryGenerateWildMon(gWildMonHeaders[headerIdx].rockSmashMonsInfo, WILD_AREA_ROCKS, WILD_CHECK_REPEL) == TRUE:
        StartWildBattle()
        gSpecialVar_Result = TRUE
    else:
        gSpecialVar_Result = FALSE


def SweetScentWildEncounter() -> bool8:
    x: s16
    y: s16
    headerId: u16

    PlayerGetDestCoords(addr(x), addr(y))
    headerId = GetCurrentMapWildMonHeaderId()
    if headerId != HEADER_NONE:
        if MapGridGetMetatileAttributeAt(x, y, METATILE_ATTRIBUTE_ENCOUNTER_TYPE) == TILE_ENCOUNTER_LAND:
            if TryStartRoamerEncounter() == TRUE:
                StartRoamerBattle()
                return TRUE

            if gWildMonHeaders[headerId].landMonsInfo is None:
                return FALSE

            TryGenerateWildMon(gWildMonHeaders[headerId].landMonsInfo, WILD_AREA_LAND, 0)

            StartWildBattle()
            return TRUE
        elif MapGridGetMetatileAttributeAt(x, y, METATILE_ATTRIBUTE_ENCOUNTER_TYPE) == TILE_ENCOUNTER_WATER:
            if TryStartRoamerEncounter() == TRUE:
                StartRoamerBattle()
                return TRUE

            if gWildMonHeaders[headerId].waterMonsInfo is None:
                return FALSE

            TryGenerateWildMon(gWildMonHeaders[headerId].waterMonsInfo, WILD_AREA_WATER, 0)
            StartWildBattle()
            return TRUE

    return FALSE


def DoesCurrentMapHaveFishingMons() -> bool8:
    headerIdx: u16 = GetCurrentMapWildMonHeaderId()
    if headerIdx == HEADER_NONE:
        return FALSE
    if gWildMonHeaders[headerIdx].fishingMonsInfo is None:
        return FALSE
    return TRUE


def FishingWildEncounter(rod: u8) -> None:
    GenerateFishingEncounter(gWildMonHeaders[GetCurrentMapWildMonHeaderId()].fishingMonsInfo, rod)
    IncrementGameStat(GAME_STAT_FISHING_CAPTURES)
    StartWildBattle()


def GetLocalWildMon(isWaterMon: Ptr[bool8]) -> u16:
    headerId: u16
    landMonsInfo: Ptr[Const[WildPokemonInfo]]
    waterMonsInfo: Ptr[Const[WildPokemonInfo]]

    isWaterMon[0] = FALSE
    headerId = GetCurrentMapWildMonHeaderId()
    if headerId == HEADER_NONE:
        return SPECIES_NONE
    landMonsInfo = gWildMonHeaders[headerId].landMonsInfo
    waterMonsInfo = gWildMonHeaders[headerId].waterMonsInfo
    # Neither
    if landMonsInfo is None and waterMonsInfo is None:
        return SPECIES_NONE
    # Land Pokemon
    elif landMonsInfo is not None and waterMonsInfo is None:
        return landMonsInfo.wildPokemon[ChooseWildMonIndex_Land()].species
    # Water Pokemon
    elif landMonsInfo is None and waterMonsInfo is not None:
        isWaterMon[0] = TRUE
        return waterMonsInfo.wildPokemon[ChooseWildMonIndex_WaterRock()].species
    # Either land or water Pokemon
    if (Random() % 100) < 80:
        return landMonsInfo.wildPokemon[ChooseWildMonIndex_Land()].species
    else:
        isWaterMon[0] = TRUE
        return waterMonsInfo.wildPokemon[ChooseWildMonIndex_WaterRock()].species


def GetLocalWaterMon() -> u16:
    headerId: u16 = GetCurrentMapWildMonHeaderId()

    if headerId != HEADER_NONE:
        waterMonsInfo: Ptr[Const[WildPokemonInfo]] = gWildMonHeaders[headerId].waterMonsInfo

        if waterMonsInfo:
            return waterMonsInfo.wildPokemon[ChooseWildMonIndex_WaterRock()].species
    return SPECIES_NONE


def UpdateRepelCounter() -> bool8:
    steps: u16

    if InUnionRoom() == TRUE:
        return FALSE

    if gQuestLogState == QL_STATE_PLAYBACK:
        return FALSE

    steps = VarGet(VAR_REPEL_STEP_COUNT)

    if steps != 0:
        steps -= 1
        VarSet(VAR_REPEL_STEP_COUNT, steps)
        if steps == 0:
            ScriptContext_SetupScript(EventScript_RepelWoreOff)
            return TRUE
    return FALSE


@static
def IsWildLevelAllowedByRepel(wildLevel: u8) -> bool8:
    i: u8

    if not VarGet(VAR_REPEL_STEP_COUNT):
        return TRUE

    for i in range(0, PARTY_SIZE):
        if GetMonData(addr(gPlayerParty[i]), MON_DATA_HP) and not GetMonData(addr(gPlayerParty[i]), MON_DATA_IS_EGG):
            ourLevel: u8 = GetMonData(addr(gPlayerParty[i]), MON_DATA_LEVEL)

            if wildLevel < ourLevel:
                return FALSE
            else:
                return TRUE

    return FALSE


@static
def ApplyFluteEncounterRateMod(encounterRate: Ptr[u32]) -> None:
    with switch(GetFluteEncounterRateModType()) as case:
        if case(1):
            encounterRate[0] += encounterRate[0] // 2
        elif case(2):
            encounterRate[0] = encounterRate[0] // 2


@static
def GetFluteEncounterRateModType() -> u8:
    if FlagGet(FLAG_SYS_WHITE_FLUTE_ACTIVE) == TRUE:
        return 1
    elif FlagGet(FLAG_SYS_BLACK_FLUTE_ACTIVE) == TRUE:
        return 2
    else:
        return 0


@static
def ApplyCleanseTagEncounterRateMod(encounterRate: Ptr[u32]) -> None:
    if IsLeadMonHoldingCleanseTag():
        encounterRate[0] = encounterRate[0] * 2 // 3


@static
def IsLeadMonHoldingCleanseTag() -> bool8:
    if sWildEncounterData.leadMonHeldItem == ITEM_CLEANSE_TAG:
        return TRUE
    else:
        return FALSE


def SeedWildEncounterRng(seed: u16) -> None:
    sWildEncounterData.rngState = seed
    ResetEncounterRateModifiers()


@static
def WildEncounterRandom() -> u16:
    sWildEncounterData.rngState = ISO_RANDOMIZE2(sWildEncounterData.rngState)
    return sWildEncounterData.rngState >> 16


@static
def GetMapBaseEncounterCooldown(encounterType: u8) -> u8:
    headerIdx: u16 = GetCurrentMapWildMonHeaderId()
    if headerIdx == HEADER_NONE:
        return 0xFF
    if encounterType == TILE_ENCOUNTER_LAND:
        if gWildMonHeaders[headerIdx].landMonsInfo is None:
            return 0xFF
        if gWildMonHeaders[headerIdx].landMonsInfo.encounterRate >= 80:
            return 0
        if gWildMonHeaders[headerIdx].landMonsInfo.encounterRate < 10:
            return 8
        return 8 - (gWildMonHeaders[headerIdx].landMonsInfo.encounterRate // 10)
    if encounterType == TILE_ENCOUNTER_WATER:
        if gWildMonHeaders[headerIdx].waterMonsInfo is None:
            return 0xFF
        if gWildMonHeaders[headerIdx].waterMonsInfo.encounterRate >= 80:
            return 0
        if gWildMonHeaders[headerIdx].waterMonsInfo.encounterRate < 10:
            return 8
        return 8 - (gWildMonHeaders[headerIdx].waterMonsInfo.encounterRate // 10)
    return 0xFF


def ResetEncounterRateModifiers() -> None:
    sWildEncounterData.encounterRateBuff = 0
    sWildEncounterData.stepsSinceLastEncounter = 0


@static
def HandleWildEncounterCooldown(currMetatileAttrs: u32) -> bool8:
    encounterType: u8 = ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_ENCOUNTER_TYPE)
    minSteps: u32
    encRate: u32
    if encounterType == TILE_ENCOUNTER_NONE:
        return FALSE
    minSteps = GetMapBaseEncounterCooldown(encounterType)
    if minSteps == 0xFF:
        return FALSE
    minSteps *= 256
    encRate = 5 * 256
    with switch(GetFluteEncounterRateModType()) as case:
        if case(1):
            minSteps -= minSteps // 2
            encRate += encRate // 2
        elif case(2):
            minSteps *= 2
            encRate //= 2
    sWildEncounterData.leadMonHeldItem = GetMonData(addr(gPlayerParty[0]), MON_DATA_HELD_ITEM)
    if IsLeadMonHoldingCleanseTag() == TRUE:
        minSteps += minSteps // 3
        encRate -= encRate // 3
    with switch(GetAbilityEncounterRateModType()) as case:
        if case(1):
            minSteps *= 2
            encRate //= 2
        elif case(2):
            minSteps //= 2
            encRate *= 2
    minSteps //= 256
    encRate //= 256
    if sWildEncounterData.stepsSinceLastEncounter >= minSteps:
        return TRUE
    sWildEncounterData.stepsSinceLastEncounter += 1
    if (Random() % 100) < encRate:
        return TRUE
    return FALSE


def TryStandardWildEncounter(currMetatileAttrs: u32) -> bool8:
    if not HandleWildEncounterCooldown(currMetatileAttrs):
        sWildEncounterData.prevMetatileBehavior = ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_BEHAVIOR)
        return FALSE
    elif StandardWildEncounter(currMetatileAttrs, sWildEncounterData.prevMetatileBehavior) == TRUE:
        sWildEncounterData.encounterRateBuff = 0
        sWildEncounterData.stepsSinceLastEncounter = 0
        sWildEncounterData.prevMetatileBehavior = ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_BEHAVIOR)
        return TRUE
    else:
        sWildEncounterData.prevMetatileBehavior = ExtractMetatileAttribute(currMetatileAttrs, METATILE_ATTRIBUTE_BEHAVIOR)
        return FALSE


@static
def AddToWildEncounterRateBuff(encounterRate: u8) -> None:
    if VarGet(VAR_REPEL_STEP_COUNT) == 0:
        sWildEncounterData.encounterRateBuff += encounterRate
    else:
        sWildEncounterData.encounterRateBuff = 0
