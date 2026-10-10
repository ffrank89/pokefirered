"""gba: a C-flavored dialect of Python for writing GBA game code.

Files written in this dialect are ordinary Python syntax, but they follow C's
rules so that tools/py2c/py2c.py can translate them into C that the GBA
compiler (agbcc) turns into the ROM. The same file can also be run on a PC by
gba.runtime, which gives the code C's integer behavior.

Cheat sheet (Python dialect -> C):

    x: u8 = 5                       u8 x = 5;
    p: Ptr[WildPokemon]             struct WildPokemon *p;
    addr(x), p[0]                   &x, *p
    info.encounterRate              info->encounterRate   (when info is a pointer)
    a // b                          a / b    (C integer division)
    a and b, a or b, not a          a && b, a || b, !a
    p is None                       p == NULL
    for i in range(0, N):           for (i = 0; i < N; i++)
    for i in count(0):              for (i = 0; ; i++)
    while True: ... if done: break  do { ... } while (!done);   (break as the last statement)
    with switch(x) as case:         switch (x) {
        if case(A): ...                 case A: ...; break;
        elif case(B, C): ...            case B: case C: ...; break;
        else: ...                       default: ...; break; }
    @static def f(a: u8) -> u16:    static u16 f(u8 a) {...}
    class S(Struct): a: u8          struct S { u8 a; };
    N = define(16)                  #define N 16
    A, B = enum(2)                  enum { A, B };
    include("global.h")             #include "global.h"
    s: Static[Ewram[S]] = {}        static EWRAM_DATA struct S s = {};

Rules that come from C:
  * Every local variable must be declared with a type annotation, at the top
    of the block it is used in, before any other statement.
  * Numbers are fixed-size integers. Assigning 300 to a u8 stores 44.
  * There are no lists, dicts, strings or classes (other than Struct) in game
    code: the GBA only has what the C code gives it.
"""

import itertools

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CType:
    """Base class for C types used in annotations."""

    def c_name(self):
        raise NotImplementedError

    def unqualified(self):
        return self


class IntType(CType):
    def __init__(self, name, bits, signed):
        self.name = name
        self.bits = bits
        self.signed = signed
        self.mask = (1 << bits) - 1

    def c_name(self):
        return self.name

    def cast(self, value):
        value = int(value) & self.mask
        if self.signed and value >> (self.bits - 1):
            value -= 1 << self.bits
        return value

    def zero(self):
        return 0

    def __repr__(self):
        return self.name


u8 = IntType("u8", 8, False)
u16 = IntType("u16", 16, False)
u32 = IntType("u32", 32, False)
s8 = IntType("s8", 8, True)
s16 = IntType("s16", 16, True)
s32 = IntType("s32", 32, True)
bool8 = IntType("bool8", 8, False)
bool16 = IntType("bool16", 16, False)
bool32 = IntType("bool32", 32, False)
cint = IntType("int", 32, True)


class VoidType(CType):
    def c_name(self):
        return "void"


void = VoidType()


class PtrType(CType):
    def __init__(self, target):
        self.target = target

    def c_name(self):
        return self.target.c_name() + " *"

    def cast(self, value):
        return value

    def zero(self):
        return None

    def __repr__(self):
        return "Ptr[%r]" % (self.target,)


class ArrayType(CType):
    def __init__(self, element, length=None):
        self.element = element
        self.length = length  # an int, a constant name, or None for []

    def cast(self, value):
        return value

    def __repr__(self):
        return "Array[%r, %r]" % (self.element, self.length)


class QualifiedType(CType):
    """const / static / EWRAM_DATA wrappers."""

    def __init__(self, qualifier, inner):
        self.qualifier = qualifier
        self.inner = inner

    def unqualified(self):
        return self.inner.unqualified()

    def cast(self, value):
        return self.unqualified().cast(value)

    def zero(self):
        return self.unqualified().zero()

    def __repr__(self):
        return "%s[%r]" % (self.qualifier, self.inner)


class _TypeFactory:
    def __init__(self, make):
        self._make = make

    def __getitem__(self, args):
        return self._make(*args) if isinstance(args, tuple) else self._make(args)


Ptr = _TypeFactory(PtrType)
Array = _TypeFactory(ArrayType)
Const = _TypeFactory(lambda t: QualifiedType("Const", t))
Static = _TypeFactory(lambda t: QualifiedType("Static", t))
Ewram = _TypeFactory(lambda t: QualifiedType("Ewram", t))


class _StructMeta(type):
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        fields = {}
        for base in reversed(cls.__mro__[1:]):
            fields.update(getattr(base, "_fields", {}))
        fields.update(namespace.get("__annotations__", {}))
        cls._fields = fields
        return cls

    def c_name(cls):
        return "struct " + cls.__name__

    def unqualified(cls):
        return cls

    def cast(cls, value):
        return value

    def zero(cls):
        return cls()


class Struct(metaclass=_StructMeta):
    """A C struct. Fields are declared with annotations:

        class WildPokemon(Struct):
            minLevel: u8
            maxLevel: u8
            species: u16
    """

    def __init__(self, **values):
        for name, ctype in self._fields.items():
            object.__setattr__(self, name, _zero_value(ctype))
        for name, value in values.items():
            setattr(self, name, value)

    def __setattr__(self, name, value):
        ctype = self._fields.get(name)
        if ctype is not None:
            value = ctype.cast(value)
        object.__setattr__(self, name, value)

    def __getitem__(self, index):
        # p[0] on a pointer to a struct is the struct itself
        if index != 0:
            raise IndexError("pointer arithmetic on structs is not supported")
        return self

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join(
            "%s=%r" % (k, getattr(self, k)) for k in self._fields))


def _zero_value(ctype):
    if isinstance(ctype, ArrayType):
        length = ctype.length if isinstance(ctype.length, int) else 0
        return CArray(ctype.element, [_zero_value(ctype.element) for _ in range(length)])
    zero = getattr(ctype, "zero", None)
    return zero() if zero is not None else None


class CArray(list):
    """A fixed-type array: assigning to an element converts it like C would."""

    def __init__(self, element, values=()):
        super().__init__(values)
        self.element = element

    def __setitem__(self, index, value):
        super().__setitem__(index, self.element.cast(value))


class Cell:
    """A single variable whose address was taken (what a pointer to a scalar points at)."""

    def __init__(self, ctype, value=0):
        self.ctype = ctype
        self.value = ctype.cast(value)

    def __getitem__(self, index):
        assert index == 0
        return self.value

    def __setitem__(self, index, value):
        assert index == 0
        self.value = self.ctype.cast(value)


# ---------------------------------------------------------------------------
# Declarations and statements that have no direct Python equivalent
# ---------------------------------------------------------------------------

NULL = None


def include(header):
    """#include "header" (no effect when running on a PC)."""


def define(value):
    """NAME = define(value) becomes #define NAME value."""
    return value


def enum(count):
    """A, B, C = enum(3) becomes enum { A, B, C };"""
    return tuple(range(count))


def static(function):
    """Marks a function as static (private to its C file)."""
    return function


def addr(value):
    """&value. Taking the address of a local variable is handled by gba.runtime."""
    return value


def cast(ctype, value):
    """(type)value"""
    return ctype.cast(value)


class switch:
    """A C switch statement:

        with switch(area) as case:
            if case(WILD_AREA_LAND):
                ...
            elif case(WILD_AREA_WATER, WILD_AREA_ROCKS):
                ...
            else:
                ...
    """

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return lambda *values: self.value in values

    def __exit__(self, *exc):
        return False


count = itertools.count

__all__ = [
    "CType", "IntType", "PtrType", "ArrayType", "QualifiedType", "Struct", "CArray", "Cell",
    "u8", "u16", "u32", "s8", "s16", "s32", "bool8", "bool16", "bool32", "cint", "void",
    "Ptr", "Array", "Const", "Static", "Ewram", "NULL",
    "include", "define", "enum", "static", "addr", "cast", "switch", "count",
]
