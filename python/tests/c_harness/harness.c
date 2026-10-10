// Test harness that runs src/wild_encounter.c (the C generated from
// src/wild_encounter.py) on the host machine.
//
// The C file is #included directly so that its static data
// (sWildEncounterData) can be printed after every call. The functions it
// calls in other parts of the game are replaced with small stand-ins below;
// the ones that consume random numbers (CreateMon, CreateMonWithNature,
// TryStartRoamerEncounter) copy the logic of the real implementations in
// src/pokemon.c and src/roamer.c.
//
// Commands are read from stdin, one per line, and every "call" prints a line
// describing the result and the resulting state. tests/test_wild_encounter.py
// runs src/wild_encounter.py with the same commands and compares the output.

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifndef WILD_ENCOUNTER_C
#define WILD_ENCOUNTER_C "../../../src/wild_encounter.c"
#endif
#include WILD_ENCOUNTER_C

// ---------------------------------------------------------------------------
// Globals the C file uses
// ---------------------------------------------------------------------------

static struct SaveBlock1 sSaveBlock1;
struct SaveBlock1 *gSaveBlock1Ptr = &sSaveBlock1;
struct Pokemon gPlayerParty[PARTY_SIZE];
struct Pokemon gEnemyParty[PARTY_SIZE];
u8 gQuestLogState;
u16 gSpecialVar_Result;
const u8 EventScript_RepelWoreOff[] = {0};

u32 gRngValue;

static u8 sFlags[0x1000];
static u16 sVars[0x400];
static u8 sAvatarFlags;
static u32 sTrainerId;
static u8 sDestEncounterType;
static bool32 sInUnionRoom;
static u32 sFishingCaptures;
static u32 sScriptsStarted;

struct HarnessPartyMon
{
    u16 hp;
    bool8 isEgg;
    u8 level;
    u16 heldItem;
    u8 ability;
};
static struct HarnessPartyMon sParty[PARTY_SIZE];

// The roamer lives in gSaveBlock1Ptr->roamer like in the game; its location
// is kept separately like sRoamerLocation in src/roamer.c.
#define ROAMER (&gSaveBlock1Ptr->roamer)
static u8 sRoamerLocation[2];

// The Pokemon most recently created in gEnemyParty[0]
static struct
{
    u16 species;
    u8 level;
    u32 personality;
    u8 ivs[6];
} sEnemy;

static const char *sBattle;

// ---------------------------------------------------------------------------
// Stand-ins for functions in other files
// ---------------------------------------------------------------------------

u16 Random(void)
{
    gRngValue = ISO_RANDOMIZE1(gRngValue);
    return gRngValue >> 16;
}

u16 VarGet(u16 id)
{
    return sVars[id - 0x4000];
}

bool8 VarSet(u16 id, u16 value)
{
    sVars[id - 0x4000] = value;
    return TRUE;
}

bool8 FlagGet(u16 id)
{
    return sFlags[id];
}

static const u32 sAttrMasks[METATILE_ATTRIBUTE_COUNT] = {
    0x000001ff, 0x00003e00, 0x0003c000, 0x00fc0000, 0x07000000, 0x18000000, 0x60000000, 0x80000000
};
static const u8 sAttrShifts[METATILE_ATTRIBUTE_COUNT] = {0, 9, 14, 18, 24, 27, 29, 31};

u32 ExtractMetatileAttribute(u32 attributes, u8 attributeType)
{
    if (attributeType >= METATILE_ATTRIBUTE_COUNT)
        return attributes;
    return (attributes & sAttrMasks[attributeType]) >> sAttrShifts[attributeType];
}

u32 MapGridGetMetatileAttributeAt(s16 x, s16 y, u8 attributeType)
{
    if (attributeType == METATILE_ATTRIBUTE_ENCOUNTER_TYPE)
        return sDestEncounterType;
    return 0;
}

void PlayerGetDestCoords(s16 *x, s16 *y)
{
    *x = 0;
    *y = 0;
}

bool8 TestPlayerAvatarFlags(u8 flags)
{
    return (sAvatarFlags & flags) != 0;
}

bool8 MetatileBehavior_IsBridge(u8 metatileBehavior)
{
    return FALSE;
}

u32 GetPlayerTrainerId(void)
{
    return sTrainerId;
}

bool32 InUnionRoom(void)
{
    return sInUnionRoom;
}

void IncrementGameStat(u8 index)
{
    if (index == GAME_STAT_FISHING_CAPTURES)
        sFishingCaptures++;
}

void ScriptContext_SetupScript(const u8 *ptr)
{
    sScriptsStarted++;
}

void StartWildBattle(void)
{
    sBattle = "wild";
}

void StartRoamerBattle(void)
{
    sBattle = "roamer";
}

void ZeroEnemyPartyMons(void)
{
    memset(&sEnemy, 0, sizeof(sEnemy));
}

u32 GetMonData2(struct Pokemon *mon, s32 field)
{
    struct HarnessPartyMon *p = &sParty[mon - gPlayerParty];
    switch (field)
    {
    case MON_DATA_HP:
        return p->hp;
    case MON_DATA_IS_EGG:
    case MON_DATA_SANITY_IS_EGG:
        return p->isEgg;
    case MON_DATA_LEVEL:
        return p->level;
    case MON_DATA_HELD_ITEM:
        return p->heldItem;
    }
    return 0;
}

u8 GetMonAbility(struct Pokemon *mon)
{
    return sParty[mon - gPlayerParty].ability;
}

// From CreateBoxMon() in src/pokemon.c: the parts that use the RNG.
void CreateMon(struct Pokemon *mon, u16 species, u8 level, u8 fixedIV, u8 hasFixedPersonality, u32 fixedPersonality, u8 otIdType, u32 fixedOtId)
{
    u32 personality;
    u32 value;

    if (hasFixedPersonality)
        personality = fixedPersonality;
    else
        personality = Random32();

    sEnemy.species = species;
    sEnemy.level = level;
    sEnemy.personality = personality;

    value = Random();
    sEnemy.ivs[0] = value & MAX_IV_MASK;
    sEnemy.ivs[1] = (value & (MAX_IV_MASK << 5)) >> 5;
    sEnemy.ivs[2] = (value & (MAX_IV_MASK << 10)) >> 10;
    value = Random();
    sEnemy.ivs[3] = value & MAX_IV_MASK;
    sEnemy.ivs[4] = (value & (MAX_IV_MASK << 5)) >> 5;
    sEnemy.ivs[5] = (value & (MAX_IV_MASK << 10)) >> 10;
}

// From src/pokemon.c
void CreateMonWithNature(struct Pokemon *mon, u16 species, u8 level, u8 fixedIV, u8 nature)
{
    u32 personality;

    do
    {
        personality = Random32();
    }
    while (nature != personality % NUM_NATURES);

    CreateMon(mon, species, level, fixedIV, TRUE, personality, OT_ID_PLAYER_ID, 0);
}

// From src/roamer.c
u8 TryStartRoamerEncounter(void)
{
    if (ROAMER->active && gSaveBlock1Ptr->location.mapGroup == sRoamerLocation[0]
     && gSaveBlock1Ptr->location.mapNum == sRoamerLocation[1] && (Random() % 4) == 0)
    {
        int i;
        ZeroEnemyPartyMons();
        sEnemy.species = ROAMER->species;
        sEnemy.level = ROAMER->level;
        sEnemy.personality = ROAMER->personality;
        for (i = 0; i < 6; i++)
            sEnemy.ivs[i] = (ROAMER->ivs >> (5 * i)) & 31;
        return TRUE;
    }
    return FALSE;
}

// ---------------------------------------------------------------------------
// Command loop
// ---------------------------------------------------------------------------

static void PrintState(const char *call, long result)
{
    printf("%s -> %ld | rng=%08X wrng=%08X prev=%u buff=%u steps=%u ability=%u held=%u | ",
           call, result, gRngValue, sWildEncounterData.rngState, sWildEncounterData.prevMetatileBehavior,
           sWildEncounterData.encounterRateBuff, sWildEncounterData.stepsSinceLastEncounter,
           sWildEncounterData.abilityEffect, sWildEncounterData.leadMonHeldItem);
    if (sBattle)
        printf("%s %u L%u %08X %u/%u/%u/%u/%u/%u", sBattle, sEnemy.species, sEnemy.level, sEnemy.personality,
               sEnemy.ivs[0], sEnemy.ivs[1], sEnemy.ivs[2], sEnemy.ivs[3], sEnemy.ivs[4], sEnemy.ivs[5]);
    else
        printf("none");
    printf(" | special=%u scripts=%u fishing=%u repel=%u\n", gSpecialVar_Result, sScriptsStarted,
           sFishingCaptures, VarGet(VAR_REPEL_STEP_COUNT));
}

int main(void)
{
    char line[256];
    char cmd[64];

    while (fgets(line, sizeof(line), stdin))
    {
        unsigned long a = 0, b = 0, c = 0, d = 0, e = 0, f = 0, g = 0;
        if (sscanf(line, "%63s %lu %lu %lu %lu %lu %lu %lu", cmd, &a, &b, &c, &d, &e, &f, &g) < 1)
            continue;

        sBattle = NULL;

        if (!strcmp(cmd, "rng"))
            gRngValue = a;
        else if (!strcmp(cmd, "map"))
        {
            gSaveBlock1Ptr->location.mapGroup = a;
            gSaveBlock1Ptr->location.mapNum = b;
        }
        else if (!strcmp(cmd, "flag"))
            sFlags[a] = b;
        else if (!strcmp(cmd, "var"))
            VarSet(a, b);
        else if (!strcmp(cmd, "avatar"))
            sAvatarFlags = a;
        else if (!strcmp(cmd, "tid"))
            sTrainerId = a;
        else if (!strcmp(cmd, "dest"))
            sDestEncounterType = a;
        else if (!strcmp(cmd, "union"))
            sInUnionRoom = a;
        else if (!strcmp(cmd, "questlog"))
            gQuestLogState = a;
        else if (!strcmp(cmd, "party"))
        {
            // party <slot> <hp> <isEgg> <level> <heldItem> <ability>
            sParty[a].hp = b;
            sParty[a].isEgg = c;
            sParty[a].level = d;
            sParty[a].heldItem = e;
            sParty[a].ability = f;
        }
        else if (!strcmp(cmd, "roamer"))
        {
            // roamer <active> <group> <num> <species> <level> <ivs> <personality>
            ROAMER->active = a;
            sRoamerLocation[0] = b;
            sRoamerLocation[1] = c;
            ROAMER->species = d;
            ROAMER->level = e;
            ROAMER->ivs = f;
            ROAMER->personality = g;
        }
        else if (!strcmp(cmd, "try_standard"))
            PrintState(cmd, TryStandardWildEncounter(a));
        else if (!strcmp(cmd, "standard"))
            PrintState(cmd, StandardWildEncounter(a, b));
        else if (!strcmp(cmd, "sweet_scent"))
            PrintState(cmd, SweetScentWildEncounter());
        else if (!strcmp(cmd, "rock_smash"))
        {
            RockSmashWildEncounter();
            PrintState(cmd, gSpecialVar_Result);
        }
        else if (!strcmp(cmd, "has_fishing"))
            PrintState(cmd, DoesCurrentMapHaveFishingMons());
        else if (!strcmp(cmd, "fishing"))
        {
            FishingWildEncounter(a);
            PrintState(cmd, 0);
        }
        else if (!strcmp(cmd, "local_wild"))
        {
            bool8 isWater;
            u16 species = GetLocalWildMon(&isWater);
            PrintState(cmd, species * 2 + isWater);
        }
        else if (!strcmp(cmd, "local_water"))
            PrintState(cmd, GetLocalWaterMon());
        else if (!strcmp(cmd, "repel"))
            PrintState(cmd, UpdateRepelCounter());
        else if (!strcmp(cmd, "seed_wild"))
        {
            SeedWildEncounterRng(a);
            PrintState(cmd, 0);
        }
        else if (!strcmp(cmd, "disable"))
        {
            DisableWildEncounters(a);
            PrintState(cmd, 0);
        }
        else if (!strcmp(cmd, "reset_mods"))
        {
            ResetEncounterRateModifiers();
            PrintState(cmd, 0);
        }
        else
        {
            fprintf(stderr, "unknown command: %s\n", cmd);
            return 1;
        }
    }
    return 0;
}
