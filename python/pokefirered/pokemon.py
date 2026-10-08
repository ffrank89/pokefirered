"""The parts of src/pokemon.c that wild encounters depend on.

Creating a Pokemon consumes values from the RNG (personality and IVs), so the
order of Random() calls here mirrors CreateMonWithNature() and CreateBoxMon()
exactly. Stats, moves and the rest of the Pokemon structure are not modeled.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, Optional

from . import constants, repo_path

NUM_NATURES = 25
NUM_UNOWN_FORMS = 28
MAX_IV_MASK = 31
SHINY_ODDS = 8

MON_MALE = 0x00
MON_FEMALE = 0xFE
MON_GENDERLESS = 0xFF

NATURE_NAMES = [
    "HARDY", "LONELY", "BRAVE", "ADAMANT", "NAUGHTY", "BOLD", "DOCILE", "RELAXED", "IMPISH",
    "LAX", "TIMID", "HASTY", "SERIOUS", "JOLLY", "NAIVE", "MODEST", "MILD", "QUIET", "BASHFUL",
    "RASH", "CALM", "GENTLE", "SASSY", "CAREFUL", "QUIRKY",
]

UNOWN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ!?"


@dataclass
class SpeciesInfo:
    gender_ratio: int
    abilities: tuple


_species_info = None


def _parse_gender_ratio(text):
    text = text.strip()
    m = re.match(r"PERCENT_FEMALE\(([\d.]+)\)", text)
    if m:
        # min(254, ((percent * 255) / 100)) stored in a u8
        return int(min(254, float(m.group(1)) * 255 / 100))
    return {"MON_MALE": MON_MALE, "MON_FEMALE": MON_FEMALE, "MON_GENDERLESS": MON_GENDERLESS}[text]


def species_info():
    """SPECIES_* name -> SpeciesInfo, parsed from src/data/pokemon/species_info.h."""
    global _species_info
    if _species_info is None:
        with open(repo_path("src", "data", "pokemon", "species_info.h"), encoding="utf-8") as f:
            text = f.read()
        info = {}
        for m in re.finditer(r"\[(SPECIES_\w+)\] =\s*\n\s*\{(.*?)\n    \},", text, re.S):
            body = m.group(2)
            gender = re.search(r"\.genderRatio\s*=\s*([^,\n]+(?:\([^)]*\))?)", body)
            abilities = re.search(r"\.abilities\s*=\s*\{\s*(\w+)\s*,\s*(\w+)\s*\}", body)
            if gender and abilities:
                info[m.group(1)] = SpeciesInfo(_parse_gender_ratio(gender.group(1)),
                                               (abilities.group(1), abilities.group(2)))
        _species_info = info
    return _species_info


def get_nature_from_personality(personality):
    return personality % NUM_NATURES


def get_unown_letter(personality):
    """GET_UNOWN_LETTER (include/pokemon.h)"""
    return (((personality & 0x03000000) >> 18)
            | ((personality & 0x00030000) >> 12)
            | ((personality & 0x00000300) >> 6)
            | ((personality & 0x00000003) >> 0)) % NUM_UNOWN_FORMS


def get_gender(species, personality):
    """GetGenderFromSpeciesAndPersonality()"""
    ratio = species_info()[species].gender_ratio
    if ratio in (MON_MALE, MON_FEMALE, MON_GENDERLESS):
        return ratio
    return MON_FEMALE if ratio > (personality & 0xFF) else MON_MALE


def get_ability(species, ability_num):
    """GetAbilityBySpecies()"""
    abilities = species_info()[species].abilities
    return abilities[1] if ability_num else abilities[0]


def is_shiny(ot_id, personality):
    shiny_value = ((ot_id >> 16) ^ (ot_id & 0xFFFF) ^ (personality >> 16) ^ (personality & 0xFFFF))
    return shiny_value < SHINY_ODDS


@dataclass
class Pokemon:
    """The fields of a created Pokemon that are relevant to encounters."""
    species: str
    level: int
    personality: int = 0
    ivs: Dict[str, int] = field(default_factory=dict)
    ot_id: int = 0
    hp: int = 1
    is_egg: bool = False
    held_item: str = "ITEM_NONE"
    ability_num: Optional[int] = None

    @property
    def species_id(self):
        return constants.species()[self.species]

    @property
    def nature(self):
        return NATURE_NAMES[get_nature_from_personality(self.personality)]

    @property
    def ability(self):
        num = self.ability_num if self.ability_num is not None else 0
        return get_ability(self.species, num)

    @property
    def gender(self):
        g = get_gender(self.species, self.personality)
        return {MON_MALE: "male", MON_FEMALE: "female", MON_GENDERLESS: "genderless"}[g]

    @property
    def shiny(self):
        return is_shiny(self.ot_id, self.personality)

    @property
    def unown_letter(self):
        if self.species != "SPECIES_UNOWN":
            return None
        return UNOWN_LETTERS[get_unown_letter(self.personality)]

    def describe(self):
        text = "%s Lv%d %s %s, IVs %s" % (
            self.species[len("SPECIES_"):], self.level, self.nature, self.gender,
            "/".join(str(self.ivs[k]) for k in ("hp", "atk", "def", "spe", "spa", "spd")))
        if self.unown_letter:
            text += ", letter " + self.unown_letter
        if self.shiny:
            text += ", SHINY"
        return text


def create_mon(rng, species, level, personality, ot_id):
    """CreateMon(mon, species, level, USE_RANDOM_IVS, TRUE, personality, OT_ID_PLAYER_ID, 0)"""
    mon = Pokemon(species=species, level=level, personality=personality & 0xFFFFFFFF, ot_id=ot_id)
    value = rng.random()
    mon.ivs["hp"] = value & MAX_IV_MASK
    mon.ivs["atk"] = (value >> 5) & MAX_IV_MASK
    mon.ivs["def"] = (value >> 10) & MAX_IV_MASK
    value = rng.random()
    mon.ivs["spe"] = value & MAX_IV_MASK
    mon.ivs["spa"] = (value >> 5) & MAX_IV_MASK
    mon.ivs["spd"] = (value >> 10) & MAX_IV_MASK
    if species_info()[species].abilities[1] != "ABILITY_NONE":
        mon.ability_num = personality & 1
    return mon


def create_mon_with_nature(rng, species, level, nature, ot_id):
    """CreateMonWithNature(mon, species, level, USE_RANDOM_IVS, nature)"""
    while True:
        personality = rng.random32()
        if get_nature_from_personality(personality) == nature:
            break
    return create_mon(rng, species, level, personality, ot_id)
