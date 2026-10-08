#!/usr/bin/env python3
"""py2c: translates game code written in the gba dialect of Python into C.

    python3 tools/py2c/py2c.py src/wild_encounter.py src/wild_encounter.c

The dialect is described in python/gba/__init__.py. This translator reads the
Python file with Python's own parser (the ast module), works out the C type of
every expression it needs to (for example, to know that info.encounterRate
must become info->encounterRate because info is a pointer), and writes the
equivalent C, keeping the comments.

Code that the dialect does not allow (for example a list inside a function, or
a variable that was never declared) is reported as an error with its line
number, the same way a C compiler would complain.
"""

import ast
import io
import os
import signal
import sys
import tokenize

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))

import gba  # noqa: E402
from gba import ArrayType, IntType, PtrType, QualifiedType, VoidType  # noqa: E402
import gba.game  # noqa: E402

INDENT = "    "


class TranslateError(Exception):
    def __init__(self, node, message):
        self.lineno = getattr(node, "lineno", 0)
        super().__init__(message)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class StructInfo:
    """A struct type: either one defined in the translated file or one from gba.game."""

    def __init__(self, name, fields):
        self.name = name
        self.fields = fields  # name -> type

    def c_name(self):
        return "struct " + self.name

    def unqualified(self):
        return self


def _from_runtime_type(t, structs):
    """Converts the type objects in gba / gba.game into translator types."""
    if isinstance(t, type) and issubclass(t, gba.Struct):
        if t.__name__ not in structs:
            info = StructInfo(t.__name__, {})
            structs[t.__name__] = info
            info.fields = {k: _from_runtime_type(v, structs) for k, v in t._fields.items()}
        return structs[t.__name__]
    if isinstance(t, PtrType):
        return PtrType(_from_runtime_type(t.target, structs))
    if isinstance(t, ArrayType):
        return ArrayType(_from_runtime_type(t.element, structs), t.length)
    if isinstance(t, QualifiedType):
        return QualifiedType(t.qualifier, _from_runtime_type(t.inner, structs))
    return t


def unqualify(t):
    while isinstance(t, QualifiedType):
        t = t.inner
    return t


def declare(t, declarator, length_text=None):
    """Builds a C declaration such as 'static const u8 name[][LAND_WILD_COUNT]'."""
    if isinstance(t, QualifiedType):
        prefix = {"Static": "static ", "Ewram": "EWRAM_DATA ", "Const": "const "}[t.qualifier]
        return prefix + declare(t.inner, declarator, length_text)
    if isinstance(t, PtrType):
        return declare(t.target, "*" + declarator)
    if isinstance(t, ArrayType):
        if declarator.startswith("*"):
            declarator = "(" + declarator + ")"
        length = "" if t.length is None else str(t.length)
        return declare(t.element, declarator + "[" + length + "]")
    return t.c_name() + (" " + declarator if declarator else "")


# ---------------------------------------------------------------------------
# C operator precedence
# ---------------------------------------------------------------------------

PREC_POSTFIX, PREC_UNARY, PREC_MUL, PREC_ADD, PREC_SHIFT, PREC_REL, PREC_EQ, PREC_BITAND, PREC_BITXOR, \
    PREC_BITOR, PREC_AND, PREC_OR, PREC_COND = 16, 15, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3

BINOPS = {
    ast.Mult: ("*", PREC_MUL), ast.FloorDiv: ("/", PREC_MUL), ast.Mod: ("%", PREC_MUL),
    ast.Add: ("+", PREC_ADD), ast.Sub: ("-", PREC_ADD),
    ast.LShift: ("<<", PREC_SHIFT), ast.RShift: (">>", PREC_SHIFT),
    ast.BitAnd: ("&", PREC_BITAND), ast.BitXor: ("^", PREC_BITXOR), ast.BitOr: ("|", PREC_BITOR),
}

COMPARE_OPS = {
    ast.Eq: ("==", PREC_EQ), ast.NotEq: ("!=", PREC_EQ),
    ast.Lt: ("<", PREC_REL), ast.LtE: ("<=", PREC_REL), ast.Gt: (">", PREC_REL), ast.GtE: (">=", PREC_REL),
}

NEGATED_COMPARE = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
                   ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.Is: ast.IsNot, ast.IsNot: ast.Is}

AUG_OPS = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.FloorDiv: "/=", ast.Mod: "%=",
           ast.LShift: "<<=", ast.RShift: ">>=", ast.BitAnd: "&=", ast.BitOr: "|=", ast.BitXor: "^="}

BITWISE_PRECS = (PREC_BITAND, PREC_BITXOR, PREC_BITOR)


# ---------------------------------------------------------------------------
# The translator
# ---------------------------------------------------------------------------

class Translator:
    def __init__(self, source, filename):
        self.source = source
        self.filename = filename
        self.lines = source.splitlines()
        self.tree = ast.parse(source, filename)
        self.out = []
        self.indent = 0

        self.structs = {}
        self.globals = {}         # name -> type (externs and file-level variables)
        self.functions = {}       # name -> return type
        self.defines = set()
        self.locals = None        # name -> type, inside a function

        self._load_game_declarations()
        self._read_comments()

    # -- setup ------------------------------------------------------------------

    def _load_game_declarations(self):
        for name in dir(gba.game):
            value = getattr(gba.game, name)
            if isinstance(value, type) and issubclass(value, gba.Struct) and value is not gba.Struct:
                _from_runtime_type(value, self.structs)
        for name, t in gba.game.EXTERNS.items():
            self.globals[name] = _from_runtime_type(t, self.structs)
        for name, t in gba.game.FUNCTIONS.items():
            self.functions[name] = t

    def _read_comments(self):
        """line number -> (comment text, is the comment alone on its line)"""
        self.comments = {}
        self.raw_comments = {}
        tokens = tokenize.generate_tokens(io.StringIO(self.source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                line = tok.start[0]
                alone = self.lines[line - 1][:tok.start[1]].strip() == ""
                raw = tok.string[1:]
                text = raw[1:] if raw.startswith(" ") else raw
                if "noqa" in text and not alone:
                    continue
                self.comments[line] = (text, alone)
                self.raw_comments[line] = raw
        self.comment_cursor = 0

    def error(self, node, message):
        raise TranslateError(node, message)

    # -- output helpers -----------------------------------------------------------

    def emit(self, text=""):
        self.out.append((INDENT * self.indent + text) if text else "")

    def flush_comments(self, upto_line):
        """Emits comments that appear before source line upto_line."""
        for line in sorted(self.comments):
            if self.comment_cursor < line < upto_line:
                self.emit("// " + self.comments[line][0] if self.comments[line][0] else "//")
        self.comment_cursor = max(self.comment_cursor, upto_line - 1)

    def trailing_comment(self, node):
        line = getattr(node, "end_lineno", None) or getattr(node, "lineno", 0)
        entry = self.comments.get(line)
        if entry and not entry[1] and line > self.comment_cursor:
            self.comment_cursor = line
            return " // " + entry[0]
        return ""

    def blank_line_before(self, node, previous_end):
        if previous_end is None:
            return False
        for index in range(previous_end, node.lineno - 1):
            if self.lines[index].strip() == "":
                return True
        return False

    def segment(self, node):
        return ast.get_source_segment(self.source, node)

    # -- types ------------------------------------------------------------------

    def eval_type(self, node):
        """Evaluates a type annotation."""
        if node is None:
            return gba.void
        if isinstance(node, ast.Constant) and node.value is None:
            return gba.void
        if isinstance(node, ast.Name):
            base = getattr(gba, node.id, None)
            if isinstance(base, (IntType, VoidType)):
                return base
            if node.id in self.structs:
                return self.structs[node.id]
            self.error(node, "unknown type '%s'" % node.id)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            kind = node.value.id
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if kind == "Ptr":
                return PtrType(self.eval_type(args[0]))
            if kind in ("Const", "Static", "Ewram"):
                return QualifiedType(kind, self.eval_type(args[0]))
            if kind == "Array":
                length = None
                if len(args) > 1:
                    length = self.expr(args[1])
                return ArrayType(self.eval_type(args[0]), length)
        self.error(node, "invalid type annotation '%s'" % self.segment(node))

    def type_of(self, node):
        """The C type of an expression, or None for plain integers / unknown."""
        if isinstance(node, ast.Name):
            if self.locals is not None and node.id in self.locals:
                return self.locals[node.id]
            return self.globals.get(node.id)
        if isinstance(node, ast.Attribute):
            base = unqualify(self.type_of(node.value))
            if isinstance(base, PtrType):
                base = unqualify(base.target)
            if isinstance(base, StructInfo):
                if node.attr not in base.fields:
                    self.error(node, "struct %s has no field '%s'" % (base.name, node.attr))
                return base.fields[node.attr]
            return None
        if isinstance(node, ast.Subscript):
            base = unqualify(self.type_of(node.value))
            if isinstance(base, PtrType):
                return base.target
            if isinstance(base, ArrayType):
                return base.element
            return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "addr":
                return PtrType(self.type_of(node.args[0]))
            if node.func.id == "cast":
                return self.eval_type(node.args[0])
            return self.functions.get(node.func.id)
        return None

    # -- expressions --------------------------------------------------------------

    def expr(self, node, parent_prec=0, right=False, parent_op=None):
        text, prec, op = self._expr(node)
        if self._needs_parens(prec, op, parent_prec, right, parent_op):
            return "(" + text + ")"
        return text

    @staticmethod
    def _needs_parens(prec, op, parent_prec, right, parent_op):
        if parent_prec == 0 or prec >= PREC_POSTFIX:
            return False
        if prec < parent_prec or (prec == parent_prec and right):
            return True
        # Cases that gcc's -Wparentheses warns about
        if parent_op == "||" and op == "&&":
            return True
        if parent_prec == PREC_SHIFT and prec in (PREC_ADD,):
            return True
        if parent_prec in BITWISE_PRECS and op != parent_op and prec < PREC_UNARY:
            return True
        if parent_prec in (PREC_REL, PREC_EQ) and prec in (PREC_REL, PREC_EQ):
            return True
        return False

    def _expr(self, node):
        """Returns (text, precedence, operator)."""
        if isinstance(node, ast.Constant):
            if node.value is None:
                return "NULL", PREC_POSTFIX, None
            if isinstance(node.value, bool):
                self.error(node, "use TRUE and FALSE instead of True and False")
            if isinstance(node.value, int):
                return self.segment(node), PREC_POSTFIX, None
            if isinstance(node.value, str):
                return '"%s"' % node.value.replace("\\", "\\\\").replace('"', '\\"'), PREC_POSTFIX, None
            self.error(node, "floating point numbers are not available on the GBA")

        if isinstance(node, ast.Name):
            if node.id in ("True", "False"):
                self.error(node, "use TRUE and FALSE instead of True and False")
            return node.id, PREC_POSTFIX, None

        if isinstance(node, ast.Attribute):
            base_type = unqualify(self.type_of(node.value))
            if base_type is None and isinstance(node.value, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
                self.error(node, "cannot tell what '%s' is; declare its type" % self.segment(node.value))
            arrow = "->" if isinstance(base_type, PtrType) else "."
            self.type_of(node)  # checks that the field exists
            return self.expr(node.value, PREC_POSTFIX) + arrow + node.attr, PREC_POSTFIX, None

        if isinstance(node, ast.Subscript):
            base_type = unqualify(self.type_of(node.value))
            index = node.slice
            if isinstance(base_type, PtrType) and isinstance(index, ast.Constant) and index.value == 0:
                return "*" + self.expr(node.value, PREC_UNARY), PREC_UNARY, "*"
            return "%s[%s]" % (self.expr(node.value, PREC_POSTFIX), self.expr(index)), PREC_POSTFIX, None

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                self.error(node, "only plain function calls are supported")
            if node.keywords:
                self.error(node, "keyword arguments are not supported")
            name = node.func.id
            if name == "addr":
                return "&" + self.expr(node.args[0], PREC_UNARY), PREC_UNARY, "&"
            if name == "cast":
                t = self.eval_type(node.args[0])
                return "(%s)%s" % (declare(t, ""), self.expr(node.args[1], PREC_UNARY)), PREC_UNARY, "cast"
            if name in ("range", "count", "switch", "enum", "define", "include"):
                self.error(node, "%s() can't be used here" % name)
            args = ", ".join(self.expr(a) for a in node.args)
            return "%s(%s)" % (name, args), PREC_POSTFIX, None

        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                self.error(node, "use // for integer division (there are no floats on the GBA)")
            if type(node.op) not in BINOPS:
                self.error(node, "operator not supported in C: %s" % type(node.op).__name__)
            op, prec = BINOPS[type(node.op)]
            left = self.expr(node.left, prec, False, op)
            right = self.expr(node.right, prec, True, op)
            return "%s %s %s" % (left, op, right), prec, op

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return "!" + self.expr(node.operand, PREC_UNARY), PREC_UNARY, "!"
            op = {ast.USub: "-", ast.UAdd: "+", ast.Invert: "~"}[type(node.op)]
            return op + self.expr(node.operand, PREC_UNARY), PREC_UNARY, op

        if isinstance(node, ast.BoolOp):
            op, prec = ("&&", PREC_AND) if isinstance(node.op, ast.And) else ("||", PREC_OR)
            parts = [self.expr(v, prec, i > 0, op) for i, v in enumerate(node.values)]
            return (" %s " % op).join(parts), prec, op

        if isinstance(node, ast.Compare):
            if len(node.ops) == 1:
                return self._compare(node.left, node.ops[0], node.comparators[0])
            # a < b < c  ->  a < b && b < c
            parts = []
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                text, prec, _ = self._compare(left, op, right)
                parts.append(text if prec > PREC_AND else "(" + text + ")")
                left = right
            return " && ".join(parts), PREC_AND, "&&"

        if isinstance(node, ast.IfExp):
            return "%s ? %s : %s" % (self.expr(node.test, PREC_COND + 1), self.expr(node.body, PREC_COND + 1),
                                     self.expr(node.orelse, PREC_COND)), PREC_COND, "?"

        if isinstance(node, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
            self.error(node, "lists, dicts, tuples and sets only exist as initializers of global arrays")

        self.error(node, "unsupported expression: %s" % type(node).__name__)

    def _compare(self, left, op, right):
        if isinstance(op, (ast.Is, ast.IsNot)):
            if not (isinstance(right, ast.Constant) and right.value is None):
                self.error(right, "'is' can only be used to compare with None (NULL)")
            text = "==" if isinstance(op, ast.Is) else "!="
            prec = PREC_EQ
        elif type(op) in COMPARE_OPS:
            text, prec = COMPARE_OPS[type(op)]
        else:
            self.error(left, "comparison not supported in C: %s" % type(op).__name__)
        return "%s %s %s" % (self.expr(left, prec, False, text), text, self.expr(right, prec, True, text)), prec, text

    def negate(self, node):
        """!(node), simplified where possible."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.expr(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in NEGATED_COMPARE:
            flipped = ast.Compare(left=node.left, ops=[NEGATED_COMPARE[type(node.ops[0])]()],
                                  comparators=node.comparators)
            ast.copy_location(flipped, node)
            return self.expr(flipped)
        return "!" + self.expr(node, PREC_UNARY)

    # -- statements -----------------------------------------------------------------

    def block(self, statements, braces=True):
        if braces:
            self.emit("{")
        self.indent += 1
        self._statements(statements)
        self.indent -= 1
        if braces:
            self.emit("}")

    def body(self, statements):
        """An if/else/loop body: braces are left out for a single simple statement, like the C code base."""
        simple = len(statements) == 1 and isinstance(statements[0], (
            ast.Return, ast.Assign, ast.AugAssign, ast.Expr, ast.Break, ast.Continue))
        if simple and not self._has_comments_in(statements[0]):
            self.indent += 1
            self._statements(statements)
            self.indent -= 1
        else:
            self.block(statements)

    def _has_comments_in(self, node):
        return any(self.comment_cursor < line < node.lineno and alone
                   for line, (_, alone) in self.comments.items())

    def _statements(self, statements):
        declarations_allowed = True
        previous_end = None
        for stmt in statements:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue  # docstring
            if isinstance(stmt, ast.Global):
                continue
            is_declaration = isinstance(stmt, ast.AnnAssign)
            if is_declaration and not declarations_allowed:
                self.error(stmt, "C requires variables to be declared at the start of a block, "
                                 "before any other statement")
            if not is_declaration:
                declarations_allowed = False
            if self.blank_line_before(stmt, previous_end):
                self.emit()
            self.flush_comments(stmt.lineno)
            self.statement(stmt)
            previous_end = stmt.end_lineno

    def statement(self, stmt):
        if isinstance(stmt, ast.AnnAssign):
            if not isinstance(stmt.target, ast.Name):
                self.error(stmt, "only plain variables can be declared")
            name = stmt.target.id
            t = self.eval_type(stmt.annotation)
            if name in self.locals and name not in self.params:
                self.error(stmt, "'%s' is already declared" % name)
            self.locals[name] = t
            init = " = " + self.expr(stmt.value) if stmt.value is not None else ""
            self.emit(declare(t, name) + init + ";" + self.trailing_comment(stmt))
            return

        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or isinstance(stmt.targets[0], ast.Tuple):
                self.error(stmt, "assign one variable at a time")
            self.check_assignable(stmt.targets[0])
            self.emit("%s = %s;%s" % (self.expr(stmt.targets[0]), self.expr(stmt.value), self.trailing_comment(stmt)))
            return

        if isinstance(stmt, ast.AugAssign):
            self.check_assignable(stmt.target)
            target = self.expr(stmt.target)
            if isinstance(stmt.value, ast.Constant) and stmt.value.value == 1 and isinstance(stmt.op, (ast.Add, ast.Sub)):
                self.emit(target + ("++;" if isinstance(stmt.op, ast.Add) else "--;") + self.trailing_comment(stmt))
                return
            if type(stmt.op) not in AUG_OPS:
                self.error(stmt, "use //= for integer division" if isinstance(stmt.op, ast.Div)
                           else "operator not supported in C")
            self.emit("%s %s %s;%s" % (target, AUG_OPS[type(stmt.op)], self.expr(stmt.value), self.trailing_comment(stmt)))
            return

        if isinstance(stmt, ast.Expr):
            if not isinstance(stmt.value, ast.Call):
                self.error(stmt, "this statement has no effect")
            self.emit(self.expr(stmt.value) + ";" + self.trailing_comment(stmt))
            return

        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                self.emit("return;")
            else:
                self.emit("return %s;%s" % (self.expr(stmt.value), self.trailing_comment(stmt)))
            return

        if isinstance(stmt, ast.Pass):
            self.emit(";")
            return
        if isinstance(stmt, ast.Break):
            if self.in_switch_case:
                self.error(stmt, "'break' inside a switch case is ambiguous; restructure the code")
            self.emit("break;")
            return
        if isinstance(stmt, ast.Continue):
            if self.in_do_while:
                self.error(stmt, "'continue' would skip the loop condition of a do-while loop")
            self.emit("continue;")
            return

        if isinstance(stmt, ast.If):
            self.if_statement(stmt, "if")
            return

        if isinstance(stmt, ast.For):
            self.for_statement(stmt)
            return

        if isinstance(stmt, ast.While):
            self.while_statement(stmt)
            return

        if isinstance(stmt, ast.With):
            self.switch_statement(stmt)
            return

        self.error(stmt, "statement not supported: %s" % type(stmt).__name__)

    def check_assignable(self, target):
        if isinstance(target, ast.Name):
            if target.id not in self.locals and target.id not in self.globals:
                self.error(target, "'%s' is not declared (give it a type, e.g. %s: u8 = ...)" % (target.id, target.id))
        elif not isinstance(target, (ast.Attribute, ast.Subscript)):
            self.error(target, "can't assign to this")

    def if_statement(self, stmt, keyword):
        self.emit("%s (%s)%s" % (keyword, self.expr(stmt.test), self.trailing_comment(stmt.test)))
        self.body(stmt.body)
        orelse = stmt.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If) and self._is_elif(orelse[0]):
            self.flush_comments(orelse[0].lineno)
            self.if_statement(orelse[0], "else if")
        elif orelse:
            self.emit("else")
            self.body(orelse)

    def _is_elif(self, node):
        return self.lines[node.lineno - 1].lstrip().startswith("elif")

    def for_statement(self, stmt):
        if stmt.orelse:
            self.error(stmt, "for/else is not supported")
        if not isinstance(stmt.target, ast.Name):
            self.error(stmt, "the loop variable must be a plain variable")
        var = stmt.target.id
        self.check_assignable(stmt.target)
        it = stmt.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id in ("range", "count")):
            self.error(stmt, "loops must use range(start, stop[, step]) or count(start)")
        args = it.args
        if it.func.id == "count":
            start = self.expr(args[0]) if args else "0"
            header = "for (%s = %s; ; %s++)" % (var, start, var)
        else:
            if len(args) == 1:
                start, stop, step = "0", args[0], None
            else:
                start, stop = self.expr(args[0]), args[1]
                step = args[2] if len(args) > 2 else None
            if step is None or (isinstance(step, ast.Constant) and step.value == 1):
                increment = var + "++"
                cmp = "<"
            elif isinstance(step, ast.Constant) and step.value == -1:
                increment = var + "--"
                cmp = ">"
            else:
                increment = "%s += %s" % (var, self.expr(step))
                cmp = "<"
            header = "for (%s = %s; %s %s %s; %s)" % (var, start, var, cmp, self.expr(stop, PREC_REL, True), increment)
        self.emit(header)
        self.loop_body(stmt.body)

    def while_statement(self, stmt):
        if stmt.orelse:
            self.error(stmt, "while/else is not supported")
        is_forever = isinstance(stmt.test, ast.Constant) and stmt.test.value is True
        last = stmt.body[-1]
        if (is_forever and isinstance(last, ast.If) and not last.orelse and len(last.body) == 1
                and isinstance(last.body[0], ast.Break)):
            # while True: ...; if done: break   ->   do { ... } while (!done);
            self.emit("do")
            saved = self.in_do_while
            self.in_do_while = True
            self.block(stmt.body[:-1])
            self.in_do_while = saved
            self.out[-1] = self.out[-1] + " while (%s);" % self.negate(last.test)
            return
        self.emit("for (;;)" if is_forever else "while (%s)" % self.expr(stmt.test))
        self.loop_body(stmt.body)

    def loop_body(self, statements):
        saved = (self.in_do_while, self.in_switch_case)
        self.in_do_while = False
        self.in_switch_case = False
        self.body(statements)
        self.in_do_while, self.in_switch_case = saved

    def switch_statement(self, stmt):
        if (len(stmt.items) != 1 or not isinstance(stmt.items[0].context_expr, ast.Call)
                or not isinstance(stmt.items[0].context_expr.func, ast.Name)
                or stmt.items[0].context_expr.func.id != "switch"
                or not isinstance(stmt.items[0].optional_vars, ast.Name)):
            self.error(stmt, "'with' is only used for switch: with switch(value) as case:")
        case_name = stmt.items[0].optional_vars.id
        value = stmt.items[0].context_expr.args[0]
        if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.If):
            self.error(stmt, "a switch must contain one if/elif/else chain of case(...) tests")
        self.emit("switch (%s)" % self.expr(value))
        self.emit("{")
        branch = stmt.body[0]
        while True:
            test = branch.test
            if not (isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == case_name):
                self.error(test, "each branch of a switch must test %s(...)" % case_name)
            self.flush_comments(branch.lineno)
            for label in test.args:
                self.emit("case %s:" % self.expr(label))
            self.case_body(branch.body)
            if len(branch.orelse) == 1 and isinstance(branch.orelse[0], ast.If) and self._is_elif(branch.orelse[0]):
                branch = branch.orelse[0]
                continue
            if branch.orelse:
                self.emit("default:")
                self.case_body(branch.orelse)
            break
        self.emit("}")

    def case_body(self, statements):
        saved = self.in_switch_case
        self.in_switch_case = True
        self.indent += 1
        self._statements(statements)
        if not isinstance(statements[-1], ast.Return):
            self.emit("break;")
        self.indent -= 1
        self.in_switch_case = saved

    # -- top level ----------------------------------------------------------------------

    def translate(self):
        body = self.tree.body
        self.emit("// Generated from %s by tools/py2c/py2c.py." % self.filename)
        self.emit("// Do not edit this file; edit %s instead." % self.filename)

        docstring = ast.get_docstring(self.tree)
        if docstring:
            self.emit("//")
            for line in docstring.splitlines():
                self.emit(("// " + line) if line else "//")
        self.emit()

        static_functions = [n for n in body if isinstance(n, ast.FunctionDef) and self._is_static(n)]
        for node in body:
            if isinstance(node, ast.FunctionDef):
                self.functions[node.name] = self.eval_type(node.returns)
        prototypes_done = False

        previous = None
        for node in body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                previous = node
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.comment_cursor = max(self.comment_cursor, node.end_lineno)
                previous = node
                continue
            if isinstance(node, ast.FunctionDef) and not prototypes_done:
                prototypes_done = True
                if static_functions:
                    self.emit()
                    for fn in static_functions:
                        self.emit(self.function_signature(fn) + ";")
                    self.emit()
                    previous = None
            if previous is not None and self.blank_line_before(node, previous.end_lineno):
                self.emit()
            self.flush_comments(node.lineno)
            self.top_level(node)
            previous = node
        self.flush_comments(len(self.lines) + 1)
        while self.out and self.out[-1] == "":
            self.out.pop()
        return "\n".join(self.out) + "\n"

    def _is_static(self, fn):
        return any(isinstance(d, ast.Name) and d.id == "static" for d in fn.decorator_list)

    def top_level(self, node):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "include":
            self.emit('#include "%s"' % node.value.args[0].value)
            return

        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            func = node.value.func.id
            if func == "define":
                name = node.targets[0].id
                self.defines.add(name)
                self.emit("#define %s %s" % (name, self.expr(node.value.args[0])))
                return
            if func == "enum":
                names = [e.id for e in node.targets[0].elts]
                self.emit("enum")
                self.emit("{")
                for name in names:
                    self.emit(INDENT + name + ",")
                    self.defines.add(name)
                self.emit("};")
                return

        if isinstance(node, ast.ClassDef):
            self.struct_definition(node)
            return

        if isinstance(node, ast.AnnAssign):
            name = node.target.id
            t = self.eval_type(node.annotation)
            self.globals[name] = t
            text = declare(t, name)
            if node.value is not None:
                text += " = " + self.initializer(node.value)
            self.emit(text + ";")
            return

        if isinstance(node, ast.FunctionDef):
            self.function_definition(node)
            return

        self.error(node, "not allowed at the top level of a game file: %s" % self.segment(node).splitlines()[0])

    def initializer(self, node):
        if isinstance(node, ast.Dict) and not node.keys:
            return "{}"
        if isinstance(node, ast.List):
            if all(isinstance(e, ast.List) for e in node.elts):
                lines = ["{"]
                for element in node.elts:
                    for line in sorted(self.comments):
                        if self.comment_cursor < line < element.lineno:
                            lines.append("  //" + self.raw_comments[line])
                    self.comment_cursor = max(self.comment_cursor, element.end_lineno)
                    text = self.segment(element)
                    lines.append(INDENT + "{" + text[1:-1] + "},")
                lines.append("}")
                return "\n".join(lines)
            return "{" + ", ".join(self.initializer(e) for e in node.elts) + "}"
        return self.expr(node)

    def struct_definition(self, node):
        if not (len(node.bases) == 1 and isinstance(node.bases[0], ast.Name) and node.bases[0].id == "Struct"):
            self.error(node, "classes are only used for structs: class Name(Struct):")
        info = StructInfo(node.name, {})
        self.structs[node.name] = info
        self.emit("struct " + node.name)
        self.emit("{")
        for item in node.body:
            if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                continue
            if not isinstance(item, ast.AnnAssign) or item.value is not None:
                self.error(item, "a struct can only contain field declarations (name: type)")
            t = self.eval_type(item.annotation)
            info.fields[item.target.id] = t
            self.emit(INDENT + declare(t, item.target.id) + ";")
        self.emit("};")

    def function_signature(self, fn):
        args = fn.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.defaults:
            self.error(fn, "functions can only have plain typed parameters")
        params = []
        for arg in args.args:
            if arg.annotation is None:
                self.error(arg, "parameter '%s' needs a type" % arg.arg)
            params.append(declare(self.eval_type(arg.annotation), arg.arg))
        returns = self.eval_type(fn.returns)
        prefix = "static " if self._is_static(fn) else ""
        return "%s%s(%s)" % (prefix, declare(returns, fn.name), ", ".join(params) or "void")

    def function_definition(self, fn):
        for d in fn.decorator_list:
            if not (isinstance(d, ast.Name) and d.id == "static"):
                self.error(d, "only @static is allowed on functions")
        self.locals = {}
        self.params = set()
        for arg in fn.args.args:
            self.locals[arg.arg] = self.eval_type(arg.annotation)
            self.params.add(arg.arg)
        self.in_do_while = False
        self.in_switch_case = False
        self.emit(self.function_signature(fn))
        self.block(fn.body)
        self.locals = None


def translate_file(path, display_name=None):
    with open(path, encoding="utf-8") as f:
        source = f.read()
    translator = Translator(source, display_name or path)
    return translator.translate()


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: py2c.py INPUT.py OUTPUT.c\n")
        return 1
    try:
        text = translate_file(argv[1])
    except TranslateError as e:
        sys.stderr.write("%s:%d: error: %s\n" % (argv[1], e.lineno, e))
        return 1
    except SyntaxError as e:
        sys.stderr.write("%s:%d: error: %s\n" % (argv[1], e.lineno or 0, e.msg))
        return 1
    with open(argv[2], "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv))
