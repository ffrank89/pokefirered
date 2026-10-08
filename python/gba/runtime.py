"""Runs game code written in the gba dialect on a PC.

    from gba.runtime import Game

    game = Game("FIRERED")
    game.set_map("MAP_ROUTE1")
    game.TryStandardWildEncounter(game.metatile(MB_TALL_GRASS, TILE_ENCOUNTER_LAND))
    print(game.battle)

Game loads src/wild_encounter.py (or any other dialect file) and runs it as
Python, after a small rewrite that gives the code C's behavior:

  * values are converted to the declared type when assigned, passed to a
    function or returned (a u8 holding 300 becomes 44);
  * // and % round toward zero like C instead of toward negative infinity;
  * variables whose address is taken with addr() are stored in a Cell, so
    that the function they are passed to can write to them;
  * assigning to a global variable inside a function works like in C.

The functions and variables that the code uses from other parts of the game
(Random(), VarGet(), CreateMon(), gSaveBlock1Ptr, gWildMonHeaders...) are
provided by the Game object, which models the parts of the game state they
touch.
"""

import ast
import copy
import json
import os

import gba
import gba.constants
import gba.game as decl
from gba import ArrayType, CArray, Cell, PtrType, QualifiedType, Struct
from pokefirered import repo_path
from pokefirered import pokemon as mons

# ---------------------------------------------------------------------------
# C arithmetic helpers used by the rewritten code
# ---------------------------------------------------------------------------


def _cdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _cmod(a, b):
    return a - _cdiv(a, b) * b


def _cast(ctype, value):
    return ctype.cast(value)


def _zero(ctype):
    return gba._zero_value(ctype)


_GLOBAL_TYPES = {name: t for name, t in decl.EXTERNS.items()}


def _init(ctype, value):
    """Initial value of a global variable."""
    t = ctype.unqualified() if isinstance(ctype, QualifiedType) else ctype
    if isinstance(t, ArrayType):
        if isinstance(value, list):
            return CArray(t.element, [_init(t.element, v) for v in value])
        return value
    if isinstance(t, type) and issubclass(t, Struct):
        if isinstance(value, dict):
            return t(**value)
        return value
    return t.cast(value)


# ---------------------------------------------------------------------------
# Rewriting the dialect into Python with C behavior
# ---------------------------------------------------------------------------

def _call(name, *args):
    return ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=list(args), keywords=[])


def _load(name):
    return ast.Name(id=name, ctx=ast.Load())


class _FunctionRewriter(ast.NodeTransformer):
    def __init__(self, fn, module_globals):
        self.fn = fn
        self.module_globals = module_globals
        self.types = {}      # local name -> annotation AST
        self.boxed = set()   # locals whose address is taken
        self.assigned_globals = set()

        for arg in fn.args.args:
            self.types[arg.arg] = arg.annotation
        for node in ast.walk(fn):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.types[node.target.id] = node.annotation
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "addr"
                    and isinstance(node.args[0], ast.Name) and node.args[0].id in self.types):
                self.boxed.add(node.args[0].id)
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.For)):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in self.types:
                    self.assigned_globals.add(t.id)

    def rewrite(self):
        fn = self.fn
        fn.decorator_list = []
        prologue = []
        for arg in fn.args.args:
            name = arg.arg
            maker = "Cell" if name in self.boxed else "_cast"
            prologue.append(ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())],
                                       value=_call(maker, arg.annotation, _load(name))))
            arg.annotation = None
        if self.assigned_globals:
            prologue.insert(0, ast.Global(names=sorted(self.assigned_globals)))
        returns = fn.returns
        fn.returns = None
        self.return_type = returns
        body = [self.visit(stmt) for stmt in fn.body]
        fn.body = prologue + [s for s in body if s is not None] or [ast.Pass()]
        return fn

    # -- names ----------------------------------------------------------------------

    def visit_Name(self, node):
        if node.id in self.boxed:
            sub = ast.Subscript(value=_load(node.id), slice=ast.Constant(0), ctx=node.ctx)
            return ast.copy_location(sub, node)
        return node

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == "addr" and isinstance(node.args[0], ast.Name) \
                and node.args[0].id in self.boxed:
            return _load(node.args[0].id)
        self.generic_visit(node)
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.FloorDiv):
            return ast.copy_location(_call("_cdiv", node.left, node.right), node)
        if isinstance(node.op, ast.Mod):
            return ast.copy_location(_call("_cmod", node.left, node.right), node)
        return node

    # -- statements -------------------------------------------------------------------

    def _typed(self, name, value):
        annotation = self.types.get(name)
        if annotation is None:
            # a global variable: convert to the type it was declared with
            return _call("_cast_global", ast.Constant(name), value)
        return _call("_cast", annotation, value)

    def visit_AnnAssign(self, node):
        name = node.target.id
        if name in self.boxed:
            args = [node.annotation] + ([self.visit(node.value)] if node.value is not None else [])
            value = _call("Cell", *args)
        elif node.value is None:
            value = _call("_zero", node.annotation)
        else:
            value = _call("_cast", node.annotation, self.visit(node.value))
        return ast.copy_location(ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value), node)

    def visit_Assign(self, node):
        node.value = self.visit(node.value)
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id not in self.boxed:
            node.value = self._typed(target.id, node.value)
            return node
        node.targets = [self.visit(target)]
        return node

    def visit_AugAssign(self, node):
        value = self.visit(node.value)
        target = node.target
        load_target = copy.deepcopy(target)
        load_target.ctx = ast.Load()
        current = self.visit(load_target)
        if isinstance(node.op, ast.FloorDiv):
            combined = _call("_cdiv", current, value)
        elif isinstance(node.op, ast.Mod):
            combined = _call("_cmod", current, value)
        else:
            combined = ast.BinOp(left=current, op=node.op, right=value)
        if isinstance(target, ast.Name) and target.id not in self.boxed:
            combined = self._typed(target.id, combined)
            store = ast.Name(id=target.id, ctx=ast.Store())
        else:
            store = self.visit(target)
        return ast.copy_location(ast.Assign(targets=[store], value=combined), node)

    def visit_For(self, node):
        node.iter = self.visit(node.iter)
        node.body = [self.visit(s) for s in node.body]
        return node

    def visit_Return(self, node):
        if node.value is not None and self.return_type is not None and not (
                isinstance(self.return_type, ast.Constant) and self.return_type.value is None):
            node.value = _call("_cast", self.return_type, self.visit(node.value))
        return node


def lower(source, filename):
    """Rewrites a dialect file into Python code with C behavior."""
    tree = ast.parse(source, filename)
    body = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in ("gba", "gba.game"):
            continue  # the Game provides these names
        if isinstance(node, ast.FunctionDef):
            node = _FunctionRewriter(node, None).rewrite()
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value if node.value is not None else ast.Constant(None)
            if node.value is None:
                value = _call("_zero", node.annotation)
            else:
                value = _call("_init", node.annotation, value)
            body.append(ast.copy_location(ast.Assign(targets=[ast.Subscript(
                value=_load("_GLOBAL_TYPES"), slice=ast.Constant(node.target.id), ctx=ast.Store())],
                value=node.annotation), node))
            node = ast.copy_location(ast.Assign(targets=[ast.Name(id=node.target.id, ctx=ast.Store())],
                                                value=value), node)
        body.append(node)
    tree.body = body
    ast.fix_missing_locations(tree)
    return compile(tree, filename, "exec")


# ---------------------------------------------------------------------------
# The game state that the code reads and writes
# ---------------------------------------------------------------------------

class Pokemon(decl.Pokemon):
    """struct Pokemon, with the fields that GetMonData() is asked about."""

    def __init__(self, species=0, level=0, personality=0, ivs=(0, 0, 0, 0, 0, 0), hp=0, is_egg=False,
                 held_item=0, ability_num=0, ot_id=0):
        super().__init__()
        object.__setattr__(self, "data", {})
        self.set(species=species, level=level, personality=personality, ivs=list(ivs), hp=hp,
                 is_egg=is_egg, held_item=held_item, ability_num=ability_num, ot_id=ot_id)

    def set(self, **values):
        self.data.update(values)

    def __getattr__(self, name):
        try:
            return self.__dict__["data"][name]
        except KeyError:
            raise AttributeError(name)

    def copy(self):
        mon = Pokemon()
        mon.data.update(copy.deepcopy(self.data))
        return mon

    def describe(self):
        ivs = "/".join(str(v) for v in self.ivs)
        nature = mons.NATURE_NAMES[self.personality % 25]
        species = _species_name(self.species)
        gender = ""
        if species in mons.species_info():
            gender = " " + {mons.MON_MALE: "male", mons.MON_FEMALE: "female",
                            mons.MON_GENDERLESS: "genderless"}[mons.get_gender(species, self.personality)]
        text = "%s Lv%d %s%s, IVs %s" % (species[len("SPECIES_"):], self.level, nature, gender, ivs)
        if self.species == gba.constants.SPECIES_UNOWN:
            text += ", letter " + mons.UNOWN_LETTERS[mons.get_unown_letter(self.personality)]
        if mons.is_shiny(self.ot_id, self.personality):
            text += ", SHINY"
        return text


_SPECIES_NAMES = None


def _species_name(species_id):
    global _SPECIES_NAMES
    if _SPECIES_NAMES is None:
        _SPECIES_NAMES = {}
        for name in gba.constants.__all__:
            if name.startswith("SPECIES_"):
                _SPECIES_NAMES.setdefault(getattr(gba.constants, name), name)
    return _SPECIES_NAMES.get(species_id, "SPECIES_%d" % species_id)


class Battle:
    def __init__(self, kind, mon):
        self.kind = kind
        self.mon = mon

    def __str__(self):
        return "%s battle: %s" % (self.kind, self.mon.describe())


def build_wild_mon_headers(game_version):
    """gWildMonHeaders as generated from src/data/wild_encounters.json for one game version."""
    with open(repo_path("src", "data", "wild_encounters.json"), encoding="utf-8") as f:
        data = json.load(f)
    c = gba.constants
    excluded = "LeafGreen" if game_version == "FIRERED" else "FireRed"
    headers = []
    for group in data["wild_encounter_groups"]:
        if group["label"] != "gWildMonHeaders":
            continue
        for encounter in group["encounters"]:
            if excluded in encounter["base_label"]:
                continue
            map_value = getattr(c, encounter["map"])
            header = decl.WildPokemonHeader(mapGroup=map_value >> 8, mapNum=map_value & 0xFF)
            for key, field in (("land_mons", "landMonsInfo"), ("water_mons", "waterMonsInfo"),
                               ("rock_smash_mons", "rockSmashMonsInfo"), ("fishing_mons", "fishingMonsInfo")):
                if key in encounter:
                    mons_list = CArray(decl.WildPokemon, [
                        decl.WildPokemon(minLevel=m["min_level"], maxLevel=m["max_level"],
                                         species=getattr(c, m["species"]))
                        for m in encounter[key]["mons"]])
                    setattr(header, field, decl.WildPokemonInfo(encounterRate=encounter[key]["encounter_rate"],
                                                                wildPokemon=mons_list))
            headers.append(header)
    undefined = c.MAP_UNDEFINED
    headers.append(decl.WildPokemonHeader(mapGroup=undefined >> 8, mapNum=undefined & 0xFF))
    return CArray(decl.WildPokemonHeader, headers)


_CODE_CACHE = {}


class Game:
    """The game state plus the dialect file(s) running on top of it."""

    def __init__(self, game_version="FIRERED", sources=("src/wild_encounter.py",)):
        c = gba.constants
        self.version = game_version
        self.c = c

        # Global variables from other files
        self.gRngValue = 0
        self.flags = set()
        self.vars = {}
        self.player_avatar_flags = c.PLAYER_AVATAR_FLAG_ON_FOOT
        self.player_trainer_id = 0
        self.dest_encounter_type = c.TILE_ENCOUNTER_NONE
        self.in_union_room = False
        self.roamer_location = (0, 0)
        self.game_stats = {}
        self.scripts_started = []
        self.battle = None

        save = decl.SaveBlock1()
        self.party = [Pokemon() for _ in range(6)]
        self.enemy_party = [Pokemon() for _ in range(6)]

        namespace = {"__name__": "game", "__builtins__": __builtins__}
        namespace.update({name: getattr(gba, name) for name in gba.__all__})
        namespace.update({name: getattr(c, name) for name in c.__all__})
        global_types = dict(_GLOBAL_TYPES)

        def cast_global(name, value):
            ctype = global_types.get(name)
            return ctype.cast(value) if ctype is not None else value

        namespace.update({"_cdiv": _cdiv, "_cmod": _cmod, "_cast": _cast, "_zero": _zero, "_init": _init,
                          "Cell": Cell, "_cast_global": cast_global, "_GLOBAL_TYPES": global_types})
        namespace.update({name: getattr(decl, name) for name in dir(decl) if not name.startswith("_")
                          and name not in decl.FUNCTIONS and name not in decl.EXTERNS})
        # Externs
        namespace.update({
            "gSaveBlock1Ptr": save,
            "gPlayerParty": self.party,
            "gEnemyParty": self.enemy_party,
            "gSpecialVar_Result": 0,
            "gQuestLogState": 0,
            "gWildMonHeaders": build_wild_mon_headers(game_version),
            "EventScript_RepelWoreOff": "EventScript_RepelWoreOff",
        })
        # Functions
        for name in decl.FUNCTIONS:
            namespace[name] = getattr(self, name)
        self.namespace = namespace

        for source in sources:
            path = repo_path(source)
            key = (path, os.path.getmtime(path))
            if key not in _CODE_CACHE:
                with open(path, encoding="utf-8") as f:
                    _CODE_CACHE[key] = lower(f.read(), path)
            exec(_CODE_CACHE[key], namespace)

    # -- access to the code's functions and variables ---------------------------------------

    def __getattr__(self, name):
        namespace = self.__dict__.get("namespace")
        if namespace is not None and name in namespace:
            return namespace[name]
        raise AttributeError(name)

    def set_global(self, name, value):
        self.namespace[name] = value

    # -- convenience setup ----------------------------------------------------------------

    def set_map(self, map_name):
        value = getattr(self.c, map_name)
        location = self.namespace["gSaveBlock1Ptr"].location
        location.mapGroup = value >> 8
        location.mapNum = value & 0xFF

    def set_party_mon(self, slot, species, level, hp=20, is_egg=False, held_item="ITEM_NONE", ability_num=0):
        self.party[slot].set(species=getattr(self.c, species) if isinstance(species, str) else species,
                             level=level, hp=hp, is_egg=is_egg,
                             held_item=getattr(self.c, held_item) if isinstance(held_item, str) else held_item,
                             ability_num=ability_num)

    def set_roamer(self, species, level, map_name, ivs=0, personality=0, active=True):
        roamer = self.namespace["gSaveBlock1Ptr"].roamer
        roamer.species = getattr(self.c, species) if isinstance(species, str) else species
        roamer.level = level
        roamer.ivs = ivs
        roamer.personality = personality
        roamer.active = int(active)
        value = getattr(self.c, map_name)
        self.roamer_location = (value >> 8, value & 0xFF)

    def metatile(self, behavior, encounter_type):
        """A metatile attribute word with the given behavior and encounter type."""
        return (behavior & 0x1FF) | ((encounter_type & 7) << 24)

    # -- random.h ---------------------------------------------------------------------------

    def Random(self):
        self.gRngValue = (1103515245 * self.gRngValue + 24691) & 0xFFFFFFFF
        return self.gRngValue >> 16

    def Random32(self):
        lo = self.Random()
        return lo | (self.Random() << 16)

    @staticmethod
    def ISO_RANDOMIZE2(value):
        return (1103515245 * value + 12345) & 0xFFFFFFFF

    # -- event_data.h -----------------------------------------------------------------------

    def VarGet(self, var_id):
        return self.vars.get(var_id, 0) & 0xFFFF

    def VarSet(self, var_id, value):
        self.vars[var_id] = value & 0xFFFF
        return 1

    def FlagGet(self, flag_id):
        return int(flag_id in self.flags)

    # -- fieldmap.h, field_player_avatar.h, metatile_behavior.h -----------------------------

    _ATTR_MASKS = (0x000001ff, 0x00003e00, 0x0003c000, 0x00fc0000, 0x07000000, 0x18000000, 0x60000000, 0x80000000)
    _ATTR_SHIFTS = (0, 9, 14, 18, 24, 27, 29, 31)

    def ExtractMetatileAttribute(self, attributes, attribute_type):
        if attribute_type >= len(self._ATTR_MASKS):
            return attributes
        return (attributes & self._ATTR_MASKS[attribute_type]) >> self._ATTR_SHIFTS[attribute_type]

    def MapGridGetMetatileAttributeAt(self, x, y, attribute_type):
        if attribute_type == self.c.METATILE_ATTRIBUTE_ENCOUNTER_TYPE:
            return self.dest_encounter_type
        return 0

    def PlayerGetDestCoords(self, x, y):
        x[0] = 0
        y[0] = 0

    def TestPlayerAvatarFlags(self, flags):
        return int((self.player_avatar_flags & flags) != 0)

    def MetatileBehavior_IsBridge(self, behavior):
        return 0

    # -- pokemon.h (the parts of CreateBoxMon that use the RNG) -----------------------------

    def ZeroEnemyPartyMons(self):
        for mon in self.enemy_party:
            mon.data.clear()
            mon.set(species=0, level=0, personality=0, ivs=[0] * 6, hp=0, is_egg=False, held_item=0,
                    ability_num=0, ot_id=0)

    def CreateMon(self, mon, species, level, fixed_iv, has_fixed_personality, fixed_personality, ot_id_type,
                  fixed_ot_id):
        personality = fixed_personality if has_fixed_personality else self.Random32()
        value = self.Random()
        ivs = [value & 31, (value >> 5) & 31, (value >> 10) & 31]
        value = self.Random()
        ivs += [value & 31, (value >> 5) & 31, (value >> 10) & 31]
        info = mons.species_info().get(_species_name(species))
        ability_num = personality & 1 if info and info.abilities[1] != "ABILITY_NONE" else 0
        mon.set(species=species, level=level, personality=personality & 0xFFFFFFFF, ivs=ivs,
                ot_id=self.player_trainer_id, ability_num=ability_num, hp=1, is_egg=False, held_item=0)

    def CreateMonWithNature(self, mon, species, level, fixed_iv, nature):
        while True:
            personality = self.Random32()
            if personality % 25 == nature:
                break
        self.CreateMon(mon, species, level, fixed_iv, 1, personality, 0, 0)

    def GetMonData(self, mon, field):
        c = self.c
        if field == c.MON_DATA_HP:
            return mon.hp
        if field in (c.MON_DATA_IS_EGG, c.MON_DATA_SANITY_IS_EGG):
            return int(mon.is_egg)
        if field == c.MON_DATA_LEVEL:
            return mon.level
        if field == c.MON_DATA_HELD_ITEM:
            return mon.held_item
        if field == c.MON_DATA_SPECIES:
            return mon.species
        if field == c.MON_DATA_PERSONALITY:
            return mon.personality
        raise NotImplementedError("GetMonData field %d" % field)

    def GetMonAbility(self, mon):
        if "ability" in mon.data:  # set directly (used by tests)
            return mon.ability
        info = mons.species_info().get(_species_name(mon.species))
        if info is None:
            return 0
        name = info.abilities[1] if mon.ability_num else info.abilities[0]
        return getattr(self.c, name)

    # -- roamer.c, battle_setup.c ----------------------------------------------------------

    def TryStartRoamerEncounter(self):
        save = self.namespace["gSaveBlock1Ptr"]
        roamer = save.roamer
        here = (save.location.mapGroup, save.location.mapNum)
        group, num = self.roamer_location
        if roamer.active and here == (gba.s8.cast(group), gba.s8.cast(num)) and (self.Random() % 4) == 0:
            self.ZeroEnemyPartyMons()
            ivs = [(roamer.ivs >> (5 * i)) & 31 for i in range(6)]
            self.enemy_party[0].set(species=roamer.species, level=roamer.level, personality=roamer.personality,
                                    ivs=ivs, ot_id=self.player_trainer_id)
            return 1
        return 0

    def StartWildBattle(self):
        self.battle = Battle("wild", self.enemy_party[0].copy())

    def StartRoamerBattle(self):
        self.battle = Battle("roamer", self.enemy_party[0].copy())

    # -- overworld.h, link.h, script.h ------------------------------------------------------

    def IncrementGameStat(self, index):
        self.game_stats[index] = self.game_stats.get(index, 0) + 1

    def InUnionRoom(self):
        return int(self.in_union_room)

    def ScriptContext_SetupScript(self, script):
        self.scripts_started.append(script)

    # -- macros --------------------------------------------------------------------------------

    @staticmethod
    def MAP_GROUP(value):
        return value >> 8

    @staticmethod
    def MAP_NUM(value):
        return value & 0xFF

    @staticmethod
    def GET_UNOWN_LETTER(personality):
        return mons.get_unown_letter(personality)

    @staticmethod
    def GET_SHINY_VALUE(ot_id, personality):
        return (ot_id >> 16) ^ (ot_id & 0xFFFF) ^ (personality >> 16) ^ (personality & 0xFFFF)

    # -- field_specials.h --------------------------------------------------------------------

    def GetPlayerTrainerId(self):
        return self.player_trainer_id & 0xFFFFFFFF

    @staticmethod
    def max(a, b):
        return a if a >= b else b

    @staticmethod
    def min(a, b):
        return a if a < b else b
