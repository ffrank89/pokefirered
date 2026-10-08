"""Pokemon data helpers for PC-side tools: species info from
src/data/pokemon/species_info.h and the formulas the game uses to derive
nature, gender, Unown letter and shininess from a personality value."""

import re
from dataclasses import dataclass

from . import repo_path

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
