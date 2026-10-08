"""Reads integer constants (#define NAME value) from the project's C headers."""

import json
import re

from . import repo_path

_DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*(?://.*)?$")
_TOKEN_RE = re.compile(r"0[xX][0-9a-fA-F]+|\d+|[A-Za-z_]\w*|<<|>>|[-+*/%|&~^()]")


class Constants:
    """A lazily evaluated table of the object-like #defines in a set of headers."""

    def __init__(self, *headers):
        self._exprs = {}
        self._values = {}
        for header in headers:
            self.add_header(header)

    def add_header(self, header):
        with open(repo_path(header), encoding="utf-8") as f:
            for line in f:
                m = _DEFINE_RE.match(line)
                if m:
                    self._exprs[m.group(1)] = m.group(2)

    def names(self, prefix=""):
        return [name for name in self._exprs if name.startswith(prefix)]

    def __contains__(self, name):
        return name in self._exprs or name in self._values

    def __getitem__(self, name):
        if name in self._values:
            return self._values[name]
        if name not in self._exprs:
            raise KeyError(name)
        value = self._evaluate(self._exprs[name], {name})
        self._values[name] = value
        return value

    def get(self, name, default=None):
        try:
            return self[name]
        except (KeyError, ValueError):
            return default

    def _evaluate(self, expr, seen):
        parts = []
        for token in _TOKEN_RE.findall(expr):
            if token[0].isdigit():
                parts.append(str(int(token, 0)))
            elif token[0].isalpha() or token[0] == "_":
                if token in seen or token not in self._exprs:
                    raise ValueError("cannot evaluate %r in %r" % (token, expr))
                if token not in self._values:
                    self._values[token] = self._evaluate(self._exprs[token], seen | {token})
                parts.append(str(self._values[token]))
            elif token == "/":
                parts.append("//")
            else:
                parts.append(token)
        try:
            return int(eval(" ".join(parts), {"__builtins__": {}}, {}))  # noqa: S307 - digits/operators only
        except SyntaxError:
            raise ValueError("cannot evaluate %r" % expr)


_cache = {}


def _cached(key, factory):
    if key not in _cache:
        _cache[key] = factory()
    return _cache[key]


def species():
    return _cached("species", lambda: Constants("include/constants/species.h"))


def flags():
    return _cached("flags", lambda: Constants("include/constants/opponents.h", "include/constants/flags.h"))


def vars():
    return _cached("vars", lambda: Constants("include/constants/vars.h"))


def abilities():
    return _cached("abilities", lambda: Constants("include/constants/abilities.h"))


def items():
    return _cached("items", lambda: Constants("include/constants/items.h"))


def pokemon():
    return _cached("pokemon", lambda: Constants("include/constants/pokemon.h"))


def _load_maps():
    """MAP_* constant -> value, computed the same way tools/mapjson does it
    for include/constants/map_groups.h (map number | group << 8)."""
    with open(repo_path("data", "maps", "map_groups.json"), encoding="utf-8") as f:
        groups = json.load(f)
    values = {"MAP_UNDEFINED": 0xFF | (0xFF << 8)}
    for group_num, group in enumerate(groups["group_order"]):
        for map_num, map_name in enumerate(groups[group]):
            with open(repo_path("data", "maps", map_name, "map.json"), encoding="utf-8") as f:
                map_id = json.load(f)["id"]
            values[map_id] = map_num | (group_num << 8)
    return values


def maps():
    return _cached("maps", _load_maps)


def name_of(table, value, prefix):
    """Reverse lookup of a constant name (first match with the given prefix)."""
    key = ("reverse", id(table), prefix)
    if key not in _cache:
        reverse = {}
        names = table.names(prefix) if isinstance(table, Constants) else [n for n in table if n.startswith(prefix)]
        for name in names:
            try:
                reverse.setdefault(table[name], name)
            except (KeyError, ValueError):
                pass
        _cache[key] = reverse
    return _cache[key].get(value)
