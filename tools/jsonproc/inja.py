# Python port of the parts of Inja 3.3 (https://github.com/pantor/inja) used by
# jsonproc, together with the nlohmann::json value semantics that Inja relies on.
#
# Inja is Copyright (c) 2018-2021 Lars Berscheid, licensed under the MIT License.
#
# The lexer, parser and renderer follow the C++ implementation closely so that
# templates render byte-for-byte identically, including its less obvious
# behaviors (sorted object keys, how loop data is saved and restored, the
# truthiness rules of nlohmann::json, and so on).
#
# JSON values are represented with plain Python objects:
#   null -> None, boolean -> bool, integer -> int, float -> float,
#   string -> str, array -> list, object -> dict (iterated in sorted key order).

import copy
import json
import math

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InjaError(Exception):
    def __init__(self, kind, message, location=None):
        if location is None:
            text = "[inja.exception.%s] %s" % (kind, message)
        else:
            text = "[inja.exception.%s] (at %d:%d) %s" % (kind, location[0], location[1], message)
        super().__init__(text)


class JsonError(Exception):
    """Mirrors the nlohmann::json exceptions."""


def get_source_location(content, pos):
    sliced = content[:pos]
    last_newline = sliced.rfind(b"\n")
    if last_newline < 0:
        return 1, len(sliced) + 1
    return sliced.count(b"\n") + 1, len(sliced) - last_newline


# ---------------------------------------------------------------------------
# nlohmann::json helpers
# ---------------------------------------------------------------------------

def type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_float(value):
    return isinstance(value, float)


def json_empty(value):
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def json_size(value):
    if value is None:
        return 0
    if isinstance(value, (list, dict)):
        return len(value)
    return 1


def json_clear(value):
    """Returns the value that basic_json::clear() would leave behind."""
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0
    if isinstance(value, float):
        return 0.0
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    return {}


_TYPE_ORDER = {"null": 0, "object": 3, "array": 4, "string": 5, "boolean": 1, "number": 2}


def json_equal(a, b):
    ta, tb = type_name(a), type_name(b)
    if ta != tb:
        return False
    if ta == "array":
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if ta == "object":
        if sorted(a.keys()) != sorted(b.keys()):
            return False
        return all(json_equal(a[k], b[k]) for k in a)
    return a == b


def json_less(a, b):
    ta, tb = type_name(a), type_name(b)
    if ta == tb:
        if ta == "array":
            for x, y in zip(a, b):
                if json_less(x, y):
                    return True
                if json_less(y, x):
                    return False
            return len(a) < len(b)
        if ta == "object":
            ia = [(k, a[k]) for k in sorted(a)]
            ib = [(k, b[k]) for k in sorted(b)]
            for (ka, va), (kb, vb) in zip(ia, ib):
                if ka != kb:
                    return ka.encode("utf-8", "surrogateescape") < kb.encode("utf-8", "surrogateescape")
                if json_less(va, vb):
                    return True
                if json_less(vb, va):
                    return False
            return len(ia) < len(ib)
        if ta == "null":
            return False
        if ta == "string":
            return a.encode("utf-8", "surrogateescape") < b.encode("utf-8", "surrogateescape")
        return a < b
    return _TYPE_ORDER[ta] < _TYPE_ORDER[tb]


def sorted_keys(obj):
    return sorted(obj.keys(), key=lambda k: k.encode("utf-8", "surrogateescape"))


def get_string(value):
    if not isinstance(value, str):
        raise JsonError("[json.exception.type_error.302] type must be string, but is %s" % type_name(value))
    return value


def _to_int32(value):
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def get_int(value):
    """basic_json::get<int>()"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return _to_int32(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return -0x80000000
        return _to_int32(int(value))
    raise JsonError("[json.exception.type_error.302] type must be number, but is %s" % type_name(value))


def get_double(value):
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    raise JsonError("[json.exception.type_error.302] type must be number, but is %s" % type_name(value))


def _escape_string(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _format_float(x):
    if math.isnan(x) or math.isinf(x):
        return "null"
    if x == 0:
        return "-0.0" if math.copysign(1.0, x) < 0 else "0.0"

    sign = "-" if x < 0 else ""
    # Shortest round-trip digits and decimal exponent.
    mantissa, _, exp_part = repr(abs(x)).partition("e")
    exponent = int(exp_part) if exp_part else 0
    int_part, _, frac_part = mantissa.partition(".")
    digits = (int_part + frac_part).lstrip("0")
    decimal_exponent = exponent - len(frac_part)
    stripped = digits.rstrip("0")
    decimal_exponent += len(digits) - len(stripped)
    digits = stripped or "0"

    k = len(digits)
    n = k + decimal_exponent
    k_min_exp, k_max_exp = -4, 15

    if k <= n <= k_max_exp:
        return sign + digits + "0" * (n - k) + ".0"
    if 0 < n <= k_max_exp:
        return sign + digits[:n] + "." + digits[n:]
    if k_min_exp < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    body = digits if k == 1 else digits[0] + "." + digits[1:]
    esign = "-" if e < 0 else "+"
    e = abs(e)
    return sign + body + "e" + esign + ("%02d" % e if e < 100 else "%d" % e)


def dump(value):
    """basic_json::dump() with default settings (compact, no ASCII escaping)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, list):
        return "[" + ",".join(dump(v) for v in value) + "]"
    return "{" + ",".join(_escape_string(k) + ":" + dump(value[k]) for k in sorted_keys(value)) + "}"


def _reject_constant(name):
    raise JsonError("[json.exception.parse_error.101] parse error: invalid literal '%s'" % name)


def parse_json(text):
    """json::parse() for template literals and data files."""
    if isinstance(text, bytes):
        if text.startswith(b"\xef\xbb\xbf"):
            text = text[3:]
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as e:
            raise JsonError("[json.exception.parse_error.101] parse error: invalid UTF-8 (%s)" % e)
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError as e:
        raise JsonError("[json.exception.parse_error.101] parse error at %s" % e)


# JSON pointers ---------------------------------------------------------------

def _pointer_tokens(ptr):
    if ptr == "":
        return []
    if not ptr.startswith("/"):
        raise JsonError("[json.exception.parse_error.107] parse error at byte 1: JSON pointer must be empty "
                        "or begin with '/' - was: '%s'" % ptr)
    tokens = []
    for token in ptr.split("/")[1:]:
        i = token.find("~")
        while i >= 0:
            if i == len(token) - 1 or token[i + 1] not in "01":
                raise JsonError("[json.exception.parse_error.108] parse error: escape character '~' "
                                "must be followed with '0' or '1'")
            i = token.find("~", i + 2)
        tokens.append(token.replace("~1", "/").replace("~0", "~"))
    return tokens


def _array_index(token):
    if not token or not token.isdigit() or not token.isascii():
        return None
    if len(token) > 1 and token[0] == "0":
        return None
    return int(token)


def pointer_contains(value, tokens):
    for token in tokens:
        if isinstance(value, dict):
            if token not in value:
                return False
            value = value[token]
        elif isinstance(value, list):
            if len(token) == 1 and not ("0" <= token <= "9"):
                return False
            if len(token) > 1:
                if not ("1" <= token[0] <= "9"):
                    return False
                if not all("0" <= c <= "9" for c in token[1:]):
                    return False
            idx = int(token)
            if idx >= len(value):
                return False
            value = value[idx]
        else:
            return False
    return True


def pointer_get(value, tokens):
    for token in tokens:
        if isinstance(value, dict):
            value = value[token]
        else:
            value = value[int(token)]
    return value


class Ref:
    """A mutable reference to a slot holding a JSON value (parent container + key)."""
    __slots__ = ("container", "key")

    def __init__(self, container, key):
        self.container = container
        self.key = key

    def get(self):
        return self.container[self.key]

    def set(self, value):
        self.container[self.key] = value


def pointer_ref_create(root_holder, tokens):
    """operator[](json_pointer) on a non-const json: creates missing elements.
    root_holder is a one-element list holding the root value."""
    ref = Ref(root_holder, 0)
    for token in tokens:
        current = ref.get()
        if current is None:
            # null values are converted to arrays (numeric token) or objects
            if token == "-" or (token and all("0" <= c <= "9" for c in token)):
                current = []
            else:
                current = {}
            ref.set(current)
        if isinstance(current, dict):
            if token not in current:
                current[token] = None
            ref = Ref(current, token)
        elif isinstance(current, list):
            if token == "-":
                current.append(None)
                ref = Ref(current, len(current) - 1)
            else:
                idx = _array_index(token)
                if idx is None:
                    raise JsonError("[json.exception.parse_error.109] parse error: array index '%s' "
                                    "is not a number" % token)
                while len(current) <= idx:
                    current.append(None)
                ref = Ref(current, idx)
        else:
            raise JsonError("[json.exception.type_error.313] invalid value to unflatten")
    return ref


def convert_dot_to_ptr(name):
    result = ""
    while True:
        part, sep, rest = name.partition(".")
        result += "/" + part
        name = rest
        if not sep or not name:
            break
    return result


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

class Kind:
    Text = "Text"
    ExpressionOpen = "ExpressionOpen"
    ExpressionClose = "ExpressionClose"
    LineStatementOpen = "LineStatementOpen"
    LineStatementClose = "LineStatementClose"
    StatementOpen = "StatementOpen"
    StatementClose = "StatementClose"
    CommentOpen = "CommentOpen"
    CommentClose = "CommentClose"
    Id = "Id"
    Number = "Number"
    String = "String"
    Plus = "Plus"
    Minus = "Minus"
    Times = "Times"
    Slash = "Slash"
    Percent = "Percent"
    Power = "Power"
    Comma = "Comma"
    Dot = "Dot"
    Colon = "Colon"
    LeftParen = "LeftParen"
    RightParen = "RightParen"
    LeftBracket = "LeftBracket"
    RightBracket = "RightBracket"
    LeftBrace = "LeftBrace"
    RightBrace = "RightBrace"
    Equal = "Equal"
    NotEqual = "NotEqual"
    GreaterThan = "GreaterThan"
    GreaterEqual = "GreaterEqual"
    LessThan = "LessThan"
    LessEqual = "LessEqual"
    Unknown = "Unknown"
    Eof = "Eof"


class Token:
    __slots__ = ("kind", "start", "end", "content")

    def __init__(self, kind, content, start, end):
        self.kind = kind
        self.content = content
        self.start = start
        self.end = end

    @property
    def text(self):
        return self.content[self.start:self.end]

    def describe(self):
        if self.kind == Kind.Text:
            return "<text>"
        if self.kind == Kind.LineStatementClose:
            return "<eol>"
        if self.kind == Kind.Eof:
            return "<eof>"
        return self.text.decode("utf-8", "surrogateescape")


class LexerConfig:
    def __init__(self):
        self.statement_open = b"{%"
        self.statement_open_no_lstrip = b"{%+"
        self.statement_open_force_lstrip = b"{%-"
        self.statement_close = b"%}"
        self.statement_close_force_rstrip = b"-%}"
        self.line_statement = b"##"
        self.expression_open = b"{{"
        self.expression_open_force_lstrip = b"{{-"
        self.expression_close = b"}}"
        self.expression_close_force_rstrip = b"-}}"
        self.comment_open = b"{#"
        self.comment_open_force_lstrip = b"{#-"
        self.comment_close = b"#}"
        self.comment_close_force_rstrip = b"-#}"
        self.open_chars = b"#{"
        self.trim_blocks = False
        self.lstrip_blocks = False


def _isalpha(c):
    return 65 <= c <= 90 or 97 <= c <= 122


def _isdigit(c):
    return 48 <= c <= 57


def _isalnum(c):
    return _isalpha(c) or _isdigit(c)


class Lexer:
    # states
    TEXT, EXPRESSION_START, EXPRESSION_START_FORCE_LSTRIP, EXPRESSION_BODY, LINE_START, LINE_BODY, \
        STATEMENT_START, STATEMENT_START_NO_LSTRIP, STATEMENT_START_FORCE_LSTRIP, STATEMENT_BODY, \
        COMMENT_START, COMMENT_START_FORCE_LSTRIP, COMMENT_BODY = range(13)

    MINUS_OPERATOR, MINUS_NUMBER = range(2)

    def __init__(self, config):
        self.config = config
        self.state = Lexer.TEXT
        self.minus_state = Lexer.MINUS_NUMBER
        self.content = b""
        self.base = 0
        self.end = 0
        self.tok_start = 0
        self.pos = 0

    def current_position(self):
        return get_source_location(self.content[self.base:], self.tok_start - self.base)

    def start(self, content):
        self.content = content
        self.base = 0
        self.end = len(content)
        self.tok_start = 0
        self.pos = 0
        self.state = Lexer.TEXT
        self.minus_state = Lexer.MINUS_NUMBER
        # Consume byte order mark (BOM) for UTF-8
        if content.startswith(b"\xef\xbb\xbf"):
            self.base = 3
            self.tok_start = 3
            self.pos = 3

    def make_token(self, kind):
        start = min(self.tok_start, self.end)
        end = min(max(start, self.pos), self.end)
        return Token(kind, self.content, start, end)

    def _starts_with(self, at, prefix):
        return self.content.startswith(prefix, at) if at <= self.end else False

    def scan_body(self, close, close_kind, close_trim=b"", trim=False):
        c = self.content
        while True:
            if self.tok_start >= self.end:
                return self.make_token(Kind.Eof)
            ch = c[self.tok_start]
            if ch in (0x20, 0x09, 0x0D):
                self.tok_start += 1
                continue

            if close_trim and self._starts_with(self.tok_start, close_trim):
                self.state = Lexer.TEXT
                self.pos = self.tok_start + len(close_trim)
                tok = self.make_token(close_kind)
                self.skip_whitespaces_and_newlines()
                return tok

            if self._starts_with(self.tok_start, close):
                self.state = Lexer.TEXT
                self.pos = self.tok_start + len(close)
                tok = self.make_token(close_kind)
                if trim:
                    self.skip_whitespaces_and_first_newline()
                return tok

            if ch == 0x0A:
                self.tok_start += 1
                continue
            break

        self.pos = self.tok_start + 1
        if _isalpha(ch):
            self.minus_state = Lexer.MINUS_OPERATOR
            return self.scan_id()

        current_minus_state = self.minus_state
        if self.minus_state == Lexer.MINUS_OPERATOR:
            self.minus_state = Lexer.MINUS_NUMBER

        simple = {
            0x2B: Kind.Plus, 0x2A: Kind.Times, 0x2F: Kind.Slash, 0x5E: Kind.Power, 0x25: Kind.Percent,
            0x2E: Kind.Dot, 0x2C: Kind.Comma, 0x3A: Kind.Colon, 0x28: Kind.LeftParen,
            0x5B: Kind.LeftBracket, 0x7B: Kind.LeftBrace,
        }
        if ch in simple:
            return self.make_token(simple[ch])
        if ch == 0x2D:  # '-'
            if current_minus_state == Lexer.MINUS_OPERATOR:
                return self.make_token(Kind.Minus)
            return self.scan_number()
        if ch == 0x29:
            self.minus_state = Lexer.MINUS_OPERATOR
            return self.make_token(Kind.RightParen)
        if ch == 0x5D:
            self.minus_state = Lexer.MINUS_OPERATOR
            return self.make_token(Kind.RightBracket)
        if ch == 0x7D:
            self.minus_state = Lexer.MINUS_OPERATOR
            return self.make_token(Kind.RightBrace)
        nxt = c[self.pos] if self.pos < self.end else None
        if ch == 0x3E:
            if nxt == 0x3D:
                self.pos += 1
                return self.make_token(Kind.GreaterEqual)
            return self.make_token(Kind.GreaterThan)
        if ch == 0x3C:
            if nxt == 0x3D:
                self.pos += 1
                return self.make_token(Kind.LessEqual)
            return self.make_token(Kind.LessThan)
        if ch == 0x3D:
            if nxt == 0x3D:
                self.pos += 1
                return self.make_token(Kind.Equal)
            return self.make_token(Kind.Unknown)
        if ch == 0x21:
            if nxt == 0x3D:
                self.pos += 1
                return self.make_token(Kind.NotEqual)
            return self.make_token(Kind.Unknown)
        if ch == 0x22:
            return self.scan_string()
        if _isdigit(ch):
            self.minus_state = Lexer.MINUS_OPERATOR
            return self.scan_number()
        if ch in (0x5F, 0x40, 0x24):
            self.minus_state = Lexer.MINUS_OPERATOR
            return self.scan_id()
        return self.make_token(Kind.Unknown)

    def scan_id(self):
        c = self.content
        while self.pos < self.end:
            ch = c[self.pos]
            if not _isalnum(ch) and ch not in (0x2E, 0x2F, 0x5F, 0x2D):
                break
            self.pos += 1
        return self.make_token(Kind.Id)

    def scan_number(self):
        c = self.content
        while self.pos < self.end:
            ch = c[self.pos]
            prev = c[self.pos - 1] if self.pos > 0 else None
            at_start = self.pos == 0
            if not (_isdigit(ch) or ch in (0x2E, 0x65, 0x45)
                    or (ch == 0x2B and (at_start or prev in (0x65, 0x45)))
                    or (ch == 0x2D and (at_start or prev in (0x65, 0x45)))):
                break
            self.pos += 1
        return self.make_token(Kind.Number)

    def scan_string(self):
        c = self.content
        escape = False
        quote = c[self.tok_start]
        while self.pos < self.end:
            ch = c[self.pos]
            self.pos += 1
            if ch == 0x5C:
                escape = True
            elif not escape and ch == quote:
                break
            else:
                escape = False
        return self.make_token(Kind.String)

    def skip_whitespaces_and_newlines(self):
        c = self.content
        while self.pos < self.end and c[self.pos] in (0x20, 0x09, 0x0A, 0x0D):
            self.pos += 1

    def skip_whitespaces_and_first_newline(self):
        c = self.content
        while self.pos < self.end and c[self.pos] in (0x20, 0x09):
            self.pos += 1
        if self.pos < self.end:
            ch = c[self.pos]
            if ch == 0x0A:
                self.pos += 1
            elif ch == 0x0D:
                self.pos += 1
                if self.pos < self.end and c[self.pos] == 0x0A:
                    self.pos += 1

    def clear_final_line_if_whitespace(self, start, end):
        c = self.content
        result_end = end
        while result_end > start:
            ch = c[result_end - 1]
            if ch in (0x20, 0x09):
                result_end -= 1
            elif ch in (0x0A, 0x0D):
                break
            else:
                return end
        return result_end

    def scan(self):
        cfg = self.config
        c = self.content
        self.tok_start = self.pos

        while True:
            if self.tok_start >= self.end:
                return self.make_token(Kind.Eof)

            state = self.state
            if state == Lexer.TEXT:
                # fast-scan to first open character
                open_start = -1
                for i in range(self.pos, self.end):
                    if c[i] in cfg.open_chars:
                        open_start = i
                        break
                if open_start < 0:
                    self.pos = self.end
                    return self.make_token(Kind.Text)
                self.pos = open_start

                must_lstrip = False
                if self._starts_with(self.pos, cfg.expression_open):
                    if self._starts_with(self.pos, cfg.expression_open_force_lstrip):
                        self.state = Lexer.EXPRESSION_START_FORCE_LSTRIP
                        must_lstrip = True
                    else:
                        self.state = Lexer.EXPRESSION_START
                elif self._starts_with(self.pos, cfg.statement_open):
                    if self._starts_with(self.pos, cfg.statement_open_no_lstrip):
                        self.state = Lexer.STATEMENT_START_NO_LSTRIP
                    elif self._starts_with(self.pos, cfg.statement_open_force_lstrip):
                        self.state = Lexer.STATEMENT_START_FORCE_LSTRIP
                        must_lstrip = True
                    else:
                        self.state = Lexer.STATEMENT_START
                        must_lstrip = cfg.lstrip_blocks
                elif self._starts_with(self.pos, cfg.comment_open):
                    if self._starts_with(self.pos, cfg.comment_open_force_lstrip):
                        self.state = Lexer.COMMENT_START_FORCE_LSTRIP
                        must_lstrip = True
                    else:
                        self.state = Lexer.COMMENT_START
                        must_lstrip = cfg.lstrip_blocks
                elif (self.pos == self.base or c[self.pos - 1] == 0x0A) and self._starts_with(self.pos, cfg.line_statement):
                    self.state = Lexer.LINE_START
                else:
                    self.pos += 1  # wasn't actually an opening sequence
                    continue

                text_start = min(self.tok_start, self.end)
                text_end = min(max(text_start, self.pos), self.end)
                if must_lstrip:
                    text_end = self.clear_final_line_if_whitespace(text_start, text_end)
                if text_end == text_start:
                    continue  # don't generate empty token
                return Token(Kind.Text, c, text_start, text_end)

            if state == Lexer.EXPRESSION_START:
                self.state = Lexer.EXPRESSION_BODY
                self.pos += len(cfg.expression_open)
                return self.make_token(Kind.ExpressionOpen)
            if state == Lexer.EXPRESSION_START_FORCE_LSTRIP:
                self.state = Lexer.EXPRESSION_BODY
                self.pos += len(cfg.expression_open_force_lstrip)
                return self.make_token(Kind.ExpressionOpen)
            if state == Lexer.LINE_START:
                self.state = Lexer.LINE_BODY
                self.pos += len(cfg.line_statement)
                return self.make_token(Kind.LineStatementOpen)
            if state == Lexer.STATEMENT_START:
                self.state = Lexer.STATEMENT_BODY
                self.pos += len(cfg.statement_open)
                return self.make_token(Kind.StatementOpen)
            if state == Lexer.STATEMENT_START_NO_LSTRIP:
                self.state = Lexer.STATEMENT_BODY
                self.pos += len(cfg.statement_open_no_lstrip)
                return self.make_token(Kind.StatementOpen)
            if state == Lexer.STATEMENT_START_FORCE_LSTRIP:
                self.state = Lexer.STATEMENT_BODY
                self.pos += len(cfg.statement_open_force_lstrip)
                return self.make_token(Kind.StatementOpen)
            if state == Lexer.COMMENT_START:
                self.state = Lexer.COMMENT_BODY
                self.pos += len(cfg.comment_open)
                return self.make_token(Kind.CommentOpen)
            if state == Lexer.COMMENT_START_FORCE_LSTRIP:
                self.state = Lexer.COMMENT_BODY
                self.pos += len(cfg.comment_open_force_lstrip)
                return self.make_token(Kind.CommentOpen)
            if state == Lexer.EXPRESSION_BODY:
                return self.scan_body(cfg.expression_close, Kind.ExpressionClose, cfg.expression_close_force_rstrip)
            if state == Lexer.LINE_BODY:
                return self.scan_body(b"\n", Kind.LineStatementClose)
            if state == Lexer.STATEMENT_BODY:
                return self.scan_body(cfg.statement_close, Kind.StatementClose,
                                      cfg.statement_close_force_rstrip, cfg.trim_blocks)
            # COMMENT_BODY
            end = c.find(cfg.comment_close, self.pos, self.end)
            if end < 0:
                self.pos = self.end
                return self.make_token(Kind.Eof)
            must_rstrip = c.startswith(cfg.comment_close_force_rstrip, end - 1)
            self.state = Lexer.TEXT
            self.pos = end + len(cfg.comment_close)
            tok = self.make_token(Kind.CommentClose)
            if must_rstrip or cfg.trim_blocks:
                self.skip_whitespaces_and_first_newline()
            return tok


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

class Op:
    Not, And, Or, In, Equal, NotEqual, Greater, GreaterEqual, Less, LessEqual, Add, Subtract, \
        Multiplication, Division, Power, Modulo, AtId, At, Default, DivisibleBy, Even, Exists, \
        ExistsInObject, First, Float, Int, IsArray, IsBoolean, IsFloat, IsInteger, IsNumber, IsObject, \
        IsString, Last, Length, Lower, Max, Min, Odd, Range, Round, Sort, Upper, Super, Join, Callback, \
        ParenLeft, ParenRight, NoneOp = range(49)


class BlockNode:
    def __init__(self):
        self.nodes = []
        self.pos = 0


class TextNode:
    def __init__(self, pos, length):
        self.pos = pos
        self.length = length


class LiteralNode:
    def __init__(self, data_text, pos):
        self.pos = pos
        self.value = parse_json(data_text)


class DataNode:
    def __init__(self, name, pos):
        self.pos = pos
        self.name = name
        self.ptr = _pointer_tokens(convert_dot_to_ptr(name))


LEFT, RIGHT = range(2)

_OP_INFO = {
    Op.Not: (1, 4, LEFT),
    Op.And: (2, 1, LEFT),
    Op.Or: (2, 1, LEFT),
    Op.In: (2, 2, LEFT),
    Op.Equal: (2, 2, LEFT),
    Op.NotEqual: (2, 2, LEFT),
    Op.Greater: (2, 2, LEFT),
    Op.GreaterEqual: (2, 2, LEFT),
    Op.Less: (2, 2, LEFT),
    Op.LessEqual: (2, 2, LEFT),
    Op.Add: (2, 3, LEFT),
    Op.Subtract: (2, 3, LEFT),
    Op.Multiplication: (2, 4, LEFT),
    Op.Division: (2, 4, LEFT),
    Op.Power: (2, 5, RIGHT),
    Op.Modulo: (2, 4, LEFT),
    Op.AtId: (2, 8, LEFT),
}


class FunctionNode:
    def __init__(self, pos, name=None, operation=None):
        self.pos = pos
        self.arguments = []
        self.callback = None
        if operation is None:
            self.name = name
            self.operation = Op.Callback
            self.precedence = 8
            self.associativity = LEFT
            self.number_args = 1
        else:
            self.name = ""
            self.operation = operation
            self.number_args, self.precedence, self.associativity = _OP_INFO.get(operation, (1, 1, LEFT))


class ExpressionListNode:
    def __init__(self, pos=0):
        self.pos = pos
        self.root = None


class ForArrayStatementNode:
    def __init__(self, value, parent, pos):
        self.value = value
        self.parent = parent
        self.pos = pos
        self.condition = ExpressionListNode()
        self.body = BlockNode()


class ForObjectStatementNode:
    def __init__(self, key, value, parent, pos):
        self.key = key
        self.value = value
        self.parent = parent
        self.pos = pos
        self.condition = ExpressionListNode()
        self.body = BlockNode()


class IfStatementNode:
    def __init__(self, parent, pos, is_nested=False):
        self.parent = parent
        self.pos = pos
        self.is_nested = is_nested
        self.has_false_statement = False
        self.condition = ExpressionListNode()
        self.true_statement = BlockNode()
        self.false_statement = BlockNode()


class IncludeStatementNode:
    def __init__(self, file, pos):
        self.file = file
        self.pos = pos


class ExtendsStatementNode:
    def __init__(self, file, pos):
        self.file = file
        self.pos = pos


class BlockStatementNode:
    def __init__(self, parent, name, pos):
        self.parent = parent
        self.name = name
        self.pos = pos
        self.block = BlockNode()


class SetStatementNode:
    def __init__(self, key, pos):
        self.key = key
        self.pos = pos
        self.expression = ExpressionListNode()


class Template:
    def __init__(self, content=b""):
        self.root = BlockNode()
        self.content = content
        self.block_storage = {}


# ---------------------------------------------------------------------------
# Function storage
# ---------------------------------------------------------------------------

VARIADIC = -1

_BUILTINS = {
    ("at", 2): Op.At,
    ("default", 2): Op.Default,
    ("divisibleBy", 2): Op.DivisibleBy,
    ("even", 1): Op.Even,
    ("exists", 1): Op.Exists,
    ("existsIn", 2): Op.ExistsInObject,
    ("first", 1): Op.First,
    ("float", 1): Op.Float,
    ("int", 1): Op.Int,
    ("isArray", 1): Op.IsArray,
    ("isBoolean", 1): Op.IsBoolean,
    ("isFloat", 1): Op.IsFloat,
    ("isInteger", 1): Op.IsInteger,
    ("isNumber", 1): Op.IsNumber,
    ("isObject", 1): Op.IsObject,
    ("isString", 1): Op.IsString,
    ("last", 1): Op.Last,
    ("length", 1): Op.Length,
    ("lower", 1): Op.Lower,
    ("max", 1): Op.Max,
    ("min", 1): Op.Min,
    ("odd", 1): Op.Odd,
    ("range", 1): Op.Range,
    ("round", 2): Op.Round,
    ("sort", 1): Op.Sort,
    ("upper", 1): Op.Upper,
    ("super", 0): Op.Super,
    ("super", 1): Op.Super,
    ("join", 2): Op.Join,
}


class FunctionStorage:
    def __init__(self):
        self.storage = {key: (op, None) for key, op in _BUILTINS.items()}

    def add_callback(self, name, num_args, callback):
        # std::map::emplace does not overwrite an existing entry
        self.storage.setdefault((name, num_args), (Op.Callback, callback))

    def find_function(self, name, num_args):
        found = self.storage.get((name, num_args))
        if found is not None:
            return found
        if num_args > 0:
            found = self.storage.get((name, VARIADIC))
            if found is not None:
                return found
        return (Op.NoneOp, None)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, lexer_config, template_storage, function_storage, search_included_templates_in_files=True):
        self.lexer_config = lexer_config
        self.lexer = Lexer(lexer_config)
        self.template_storage = template_storage
        self.function_storage = function_storage
        self.search_included_templates_in_files = search_included_templates_in_files

        self.tok = None
        self.peek_tok = None
        self.have_peek_tok = False

        self.current_paren_level = 0
        self.current_bracket_level = 0
        self.current_brace_level = 0

        self.literal_start = 0
        self.current_block = None
        self.current_expression_list = None
        self.function_stack = []
        self.arguments = []
        self.operator_stack = []
        self.if_statement_stack = []
        self.for_statement_stack = []
        self.block_statement_stack = []

    def throw_parser_error(self, message):
        raise InjaError("parser_error", message, self.lexer.current_position())

    def get_next_token(self):
        if self.have_peek_tok:
            self.tok = self.peek_tok
            self.have_peek_tok = False
        else:
            self.tok = self.lexer.scan()

    def get_peek_token(self):
        if not self.have_peek_tok:
            self.peek_tok = self.lexer.scan()
            self.have_peek_tok = True

    def add_literal(self, content):
        data_text = content[self.literal_start:self.tok.end]
        try:
            node = LiteralNode(data_text, self.literal_start)
        except JsonError:
            raise
        self.arguments.append(node)

    def add_operator(self):
        function = self.operator_stack.pop()
        for _ in range(function.number_args):
            if not self.arguments:
                # Undefined behavior in the C++ version
                self.throw_parser_error("malformed expression")
            function.arguments.insert(0, self.arguments.pop())
        self.arguments.append(function)

    def add_to_template_storage(self, path, template_name):
        if template_name in self.template_storage:
            return template_name
        if self.search_included_templates_in_files:
            name = path + template_name
            if name.startswith("./"):
                name = name[2:]
            if name not in self.template_storage:
                try:
                    with open(name, "rb") as f:
                        text = f.read()
                except OSError:
                    raise InjaError("file_error", "failed accessing file at '%s'" % name)
                include_template = Template(text)
                self.template_storage[name] = include_template
                self.parse_into_template(include_template, name)
            return name
        return template_name

    def parse_filename(self, tok):
        if tok.kind != Kind.String:
            self.throw_parser_error("expected string, got '%s'" % tok.describe())
        if tok.end - tok.start < 2:
            self.throw_parser_error("expected filename, got '%s'" % tok.describe())
        return tok.content[tok.start + 1:tok.end - 1].decode("utf-8", "surrogateescape")

    def parse_expression(self, tmpl, closing):
        content = tmpl.content
        while self.tok.kind != closing and self.tok.kind != Kind.Eof:
            kind = self.tok.kind
            if kind == Kind.String or kind == Kind.Number:
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    self.literal_start = self.tok.start
                    self.add_literal(content)
            elif kind == Kind.LeftBracket:
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    self.literal_start = self.tok.start
                self.current_bracket_level += 1
            elif kind == Kind.LeftBrace:
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    self.literal_start = self.tok.start
                self.current_brace_level += 1
            elif kind == Kind.RightBracket:
                if self.current_bracket_level == 0:
                    self.throw_parser_error("unexpected ']'")
                self.current_bracket_level -= 1
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    self.add_literal(content)
            elif kind == Kind.RightBrace:
                if self.current_brace_level == 0:
                    self.throw_parser_error("unexpected '}'")
                self.current_brace_level -= 1
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    self.add_literal(content)
            elif kind == Kind.Id:
                self.get_peek_token()
                text = self.tok.text
                if text in (b"true", b"false", b"null"):
                    if self.current_brace_level == 0 and self.current_bracket_level == 0:
                        self.literal_start = self.tok.start
                        self.add_literal(content)
                elif text in (b"and", b"or", b"in", b"not"):
                    self.parse_operator()
                elif self.peek_tok.kind == Kind.LeftParen:
                    node = FunctionNode(self.tok.start, name=text.decode("utf-8", "surrogateescape"))
                    self.operator_stack.append(node)
                    self.function_stack.append((node, self.current_paren_level))
                else:
                    self.arguments.append(DataNode(text.decode("utf-8", "surrogateescape"), self.tok.start))
            elif kind in (Kind.Equal, Kind.NotEqual, Kind.GreaterThan, Kind.GreaterEqual, Kind.LessThan,
                          Kind.LessEqual, Kind.Plus, Kind.Minus, Kind.Times, Kind.Slash, Kind.Power,
                          Kind.Percent, Kind.Dot):
                self.parse_operator()
            elif kind == Kind.Comma:
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    if not self.function_stack:
                        self.throw_parser_error("unexpected ','")
                    self.function_stack[-1][0].number_args += 1
            elif kind == Kind.Colon:
                if self.current_brace_level == 0 and self.current_bracket_level == 0:
                    self.throw_parser_error("unexpected ':'")
            elif kind == Kind.LeftParen:
                self.current_paren_level += 1
                self.operator_stack.append(FunctionNode(self.tok.start, operation=Op.ParenLeft))
                self.get_peek_token()
                if self.peek_tok.kind == Kind.RightParen:
                    if self.function_stack and self.function_stack[-1][1] == self.current_paren_level - 1:
                        self.function_stack[-1][0].number_args = 0
            elif kind == Kind.RightParen:
                self.current_paren_level -= 1
                while self.operator_stack and self.operator_stack[-1].operation != Op.ParenLeft:
                    self.add_operator()
                if self.operator_stack and self.operator_stack[-1].operation == Op.ParenLeft:
                    self.operator_stack.pop()
                if self.function_stack and self.function_stack[-1][1] == self.current_paren_level:
                    func = self.function_stack[-1][0]
                    operation, callback = self.function_storage.find_function(func.name, func.number_args)
                    if operation == Op.NoneOp:
                        self.throw_parser_error("unknown function " + func.name)
                    func.operation = operation
                    if operation == Op.Callback:
                        func.callback = callback
                    if not self.operator_stack:
                        self.throw_parser_error("internal error at function " + func.name)
                    self.add_operator()
                    self.function_stack.pop()

            self.get_next_token()

        while self.operator_stack:
            self.add_operator()

        if len(self.arguments) == 1:
            self.current_expression_list.root = self.arguments[0]
            self.arguments = []
        elif len(self.arguments) > 1:
            self.throw_parser_error("malformed expression")
        return True

    def parse_operator(self):
        kind = self.tok.kind
        if kind == Kind.Id:
            text = self.tok.text
            operation = {b"and": Op.And, b"or": Op.Or, b"in": Op.In, b"not": Op.Not}.get(text)
            if operation is None:
                self.throw_parser_error("unknown operator in parser.")
        else:
            operation = {
                Kind.Equal: Op.Equal, Kind.NotEqual: Op.NotEqual, Kind.GreaterThan: Op.Greater,
                Kind.GreaterEqual: Op.GreaterEqual, Kind.LessThan: Op.Less, Kind.LessEqual: Op.LessEqual,
                Kind.Plus: Op.Add, Kind.Minus: Op.Subtract, Kind.Times: Op.Multiplication,
                Kind.Slash: Op.Division, Kind.Power: Op.Power, Kind.Percent: Op.Modulo, Kind.Dot: Op.AtId,
            }.get(kind)
            if operation is None:
                self.throw_parser_error("unknown operator in parser.")
        function_node = FunctionNode(self.tok.start, operation=operation)
        while (self.operator_stack
               and ((self.operator_stack[-1].precedence > function_node.precedence)
                    or (self.operator_stack[-1].precedence == function_node.precedence
                        and function_node.associativity == LEFT))
               and self.operator_stack[-1].operation != Op.ParenLeft):
            self.add_operator()
        self.operator_stack.append(function_node)

    def parse_statement(self, tmpl, closing, path):
        if self.tok.kind != Kind.Id:
            return False
        text = self.tok.text

        if text == b"if":
            self.get_next_token()
            node = IfStatementNode(self.current_block, self.tok.start)
            self.current_block.nodes.append(node)
            self.if_statement_stack.append(node)
            self.current_block = node.true_statement
            self.current_expression_list = node.condition
            if not self.parse_expression(tmpl, closing):
                return False
        elif text == b"else":
            if not self.if_statement_stack:
                self.throw_parser_error("else without matching if")
            if_statement_data = self.if_statement_stack[-1]
            self.get_next_token()
            if_statement_data.has_false_statement = True
            self.current_block = if_statement_data.false_statement
            # Chained else if
            if self.tok.kind == Kind.Id and self.tok.text == b"if":
                self.get_next_token()
                node = IfStatementNode(self.current_block, self.tok.start, is_nested=True)
                self.current_block.nodes.append(node)
                self.if_statement_stack.append(node)
                self.current_block = node.true_statement
                self.current_expression_list = node.condition
                if not self.parse_expression(tmpl, closing):
                    return False
        elif text == b"endif":
            if not self.if_statement_stack:
                self.throw_parser_error("endif without matching if")
            # Nested if statements
            while self.if_statement_stack[-1].is_nested:
                self.if_statement_stack.pop()
            if_statement_data = self.if_statement_stack[-1]
            self.get_next_token()
            self.current_block = if_statement_data.parent
            self.if_statement_stack.pop()
        elif text == b"block":
            self.get_next_token()
            if self.tok.kind != Kind.Id:
                self.throw_parser_error("expected block name, got '%s'" % self.tok.describe())
            block_name = self.tok.text.decode("utf-8", "surrogateescape")
            node = BlockStatementNode(self.current_block, block_name, self.tok.start)
            self.current_block.nodes.append(node)
            self.block_statement_stack.append(node)
            self.current_block = node.block
            if block_name in tmpl.block_storage:
                self.throw_parser_error("block with the name '%s' does already exist" % block_name)
            tmpl.block_storage[block_name] = node
            self.get_next_token()
        elif text == b"endblock":
            if not self.block_statement_stack:
                self.throw_parser_error("endblock without matching block")
            block_statement_data = self.block_statement_stack[-1]
            self.get_next_token()
            self.current_block = block_statement_data.parent
            self.block_statement_stack.pop()
        elif text == b"for":
            self.get_next_token()
            # options: for a in arr; for a, b in obj
            if self.tok.kind != Kind.Id:
                self.throw_parser_error("expected id, got '%s'" % self.tok.describe())
            value_token = self.tok
            self.get_next_token()

            if self.tok.kind == Kind.Comma:
                self.get_next_token()
                if self.tok.kind != Kind.Id:
                    self.throw_parser_error("expected id, got '%s'" % self.tok.describe())
                key_token = value_token
                value_token = self.tok
                self.get_next_token()
                node = ForObjectStatementNode(key_token.text.decode("utf-8", "surrogateescape"),
                                              value_token.text.decode("utf-8", "surrogateescape"),
                                              self.current_block, self.tok.start)
            else:
                node = ForArrayStatementNode(value_token.text.decode("utf-8", "surrogateescape"),
                                             self.current_block, self.tok.start)

            self.current_block.nodes.append(node)
            self.for_statement_stack.append(node)
            self.current_block = node.body
            self.current_expression_list = node.condition

            if self.tok.kind != Kind.Id or self.tok.text != b"in":
                self.throw_parser_error("expected 'in', got '%s'" % self.tok.describe())
            self.get_next_token()
            if not self.parse_expression(tmpl, closing):
                return False
        elif text == b"endfor":
            if not self.for_statement_stack:
                self.throw_parser_error("endfor without matching for")
            for_statement_data = self.for_statement_stack[-1]
            self.get_next_token()
            self.current_block = for_statement_data.parent
            self.for_statement_stack.pop()
        elif text == b"include":
            self.get_next_token()
            template_name = self.parse_filename(self.tok)
            template_name = self.add_to_template_storage(path, template_name)
            self.current_block.nodes.append(IncludeStatementNode(template_name, self.tok.start))
            self.get_next_token()
        elif text == b"extends":
            self.get_next_token()
            template_name = self.parse_filename(self.tok)
            template_name = self.add_to_template_storage(path, template_name)
            self.current_block.nodes.append(ExtendsStatementNode(template_name, self.tok.start))
            self.get_next_token()
        elif text == b"set":
            self.get_next_token()
            if self.tok.kind != Kind.Id:
                self.throw_parser_error("expected variable name, got '%s'" % self.tok.describe())
            key = self.tok.text.decode("utf-8", "surrogateescape")
            self.get_next_token()
            node = SetStatementNode(key, self.tok.start)
            self.current_block.nodes.append(node)
            self.current_expression_list = node.expression
            if self.tok.text != b"=":
                self.throw_parser_error("expected '=', got '%s'" % self.tok.describe())
            self.get_next_token()
            if not self.parse_expression(tmpl, closing):
                return False
        else:
            return False
        return True

    def parse_into(self, tmpl, path):
        self.lexer.start(tmpl.content)
        self.current_block = tmpl.root

        while True:
            self.get_next_token()
            kind = self.tok.kind
            if kind == Kind.Eof:
                if self.if_statement_stack:
                    self.throw_parser_error("unmatched if")
                if self.for_statement_stack:
                    self.throw_parser_error("unmatched for")
                return
            if kind == Kind.Text:
                self.current_block.nodes.append(TextNode(self.tok.start, self.tok.end - self.tok.start))
            elif kind == Kind.StatementOpen:
                self.get_next_token()
                if not self.parse_statement(tmpl, Kind.StatementClose, path):
                    self.throw_parser_error("expected statement, got '%s'" % self.tok.describe())
                if self.tok.kind != Kind.StatementClose:
                    self.throw_parser_error("expected statement close, got '%s'" % self.tok.describe())
            elif kind == Kind.LineStatementOpen:
                self.get_next_token()
                if not self.parse_statement(tmpl, Kind.LineStatementClose, path):
                    self.throw_parser_error("expected statement, got '%s'" % self.tok.describe())
                if self.tok.kind != Kind.LineStatementClose and self.tok.kind != Kind.Eof:
                    self.throw_parser_error("expected line statement close, got '%s'" % self.tok.describe())
            elif kind == Kind.ExpressionOpen:
                self.get_next_token()
                node = ExpressionListNode(self.tok.start)
                self.current_block.nodes.append(node)
                self.current_expression_list = node
                if not self.parse_expression(tmpl, Kind.ExpressionClose):
                    self.throw_parser_error("expected expression, got '%s'" % self.tok.describe())
                if self.tok.kind != Kind.ExpressionClose:
                    self.throw_parser_error("expected expression close, got '%s'" % self.tok.describe())
            elif kind == Kind.CommentOpen:
                self.get_next_token()
                if self.tok.kind != Kind.CommentClose:
                    self.throw_parser_error("expected comment close, got '%s'" % self.tok.describe())
            else:
                self.throw_parser_error("unexpected token '%s'" % self.tok.describe())

    def parse_into_template(self, tmpl, filename):
        idx = max(filename.rfind("/"), filename.rfind("\\"))
        path = filename[:idx + 1]
        sub_parser = Parser(self.lexer_config, self.template_storage, self.function_storage,
                            self.search_included_templates_in_files)
        sub_parser.parse_into(tmpl, path)

    @staticmethod
    def load_file(filename):
        try:
            with open(filename, "rb") as f:
                return f.read()
        except OSError:
            raise InjaError("file_error", "failed accessing file at '%s'" % filename)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

_NOT_FOUND = object()


def truthy(data):
    if isinstance(data, bool):
        return data
    if is_number(data):
        return data != 0
    if data is None:
        return False
    return not json_empty(data)


def _ascii_upper(s):
    return "".join(chr(ord(c) - 32) if "a" <= c <= "z" else c for c in s)


def _ascii_lower(s):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)


class Renderer:
    def __init__(self, template_storage, function_storage, throw_at_missing_includes=True):
        self.template_storage = template_storage
        self.function_storage = function_storage
        self.throw_at_missing_includes = throw_at_missing_includes

        self.current_template = None
        self.current_level = 0
        self.template_stack = []
        self.block_statement_stack = []

        self.data_input = None
        self.output = None

        # additional_data["loop"] is created by the C++ member initializer
        self.additional = [{"loop": None}]
        self.data_eval_stack = []
        self.not_found_stack = []
        self.break_rendering = False

    # helpers -------------------------------------------------------------

    @property
    def additional_data(self):
        return self.additional[0]

    def loop_data(self):
        return self.additional[0]["loop"]

    def set_loop_data(self, value):
        self.additional[0]["loop"] = value

    def throw_renderer_error(self, message, node):
        loc = get_source_location(self.current_template.content, node.pos)
        raise InjaError("render_error", message, loc)

    def make_result(self, result):
        self.data_eval_stack.append(result)

    def print_data(self, value):
        if isinstance(value, str):
            self.output.append(value.encode("utf-8", "surrogateescape"))
        elif is_integer(value):
            self.output.append(str(value).encode())
        elif value is None:
            pass
        else:
            self.output.append(dump(value).encode("utf-8", "surrogateescape"))

    def eval_expression_list(self, expression_list):
        if expression_list.root is None:
            self.throw_renderer_error("empty expression", expression_list)

        self.accept(expression_list.root)

        if not self.data_eval_stack:
            self.throw_renderer_error("empty expression", expression_list)
        elif len(self.data_eval_stack) != 1:
            self.throw_renderer_error("malformed expression", expression_list)

        result = self.data_eval_stack.pop()
        if result is _NOT_FOUND:
            if not self.not_found_stack:
                self.throw_renderer_error("expression could not be evaluated", expression_list)
            node = self.not_found_stack.pop()
            self.throw_renderer_error("variable '%s' not found" % node.name, node)
        return copy.deepcopy(result)

    def get_arguments(self, node, n, n_start=0, throw_not_found=True):
        if len(node.arguments) < n_start + n:
            self.throw_renderer_error("function needs %d variables, but has only found %d"
                                      % (n_start + n, len(node.arguments)), node)
        for i in range(n_start, n_start + n):
            self.accept(node.arguments[i])
        if len(self.data_eval_stack) < n:
            self.throw_renderer_error("function needs %d variables, but has only found %d"
                                      % (n, len(self.data_eval_stack)), node)
        result = [None] * n
        for i in range(n):
            value = self.data_eval_stack.pop()
            result[n - i - 1] = value
            if value is _NOT_FOUND:
                data_node = self.not_found_stack.pop()
                if throw_not_found:
                    self.throw_renderer_error("variable '%s' not found" % data_node.name, data_node)
        return result

    def get_argument_vector(self, node):
        n = len(node.arguments)
        for a in node.arguments:
            self.accept(a)
        if len(self.data_eval_stack) < n:
            self.throw_renderer_error("function needs %d variables, but has only found %d"
                                      % (n, len(self.data_eval_stack)), node)
        result = [None] * n
        for i in range(n):
            value = self.data_eval_stack.pop()
            result[n - i - 1] = value
            if value is _NOT_FOUND:
                data_node = self.not_found_stack.pop()
                self.throw_renderer_error("variable '%s' not found" % data_node.name, data_node)
        return result

    # visitors ------------------------------------------------------------

    def accept(self, node):
        getattr(self, "visit_" + type(node).__name__)(node)

    def visit_BlockNode(self, node):
        for n in node.nodes:
            self.accept(n)
            if self.break_rendering:
                break

    def visit_TextNode(self, node):
        self.output.append(self.current_template.content[node.pos:node.pos + node.length])

    def visit_LiteralNode(self, node):
        self.data_eval_stack.append(node.value)

    def visit_DataNode(self, node):
        if pointer_contains(self.additional_data, node.ptr):
            self.data_eval_stack.append(pointer_get(self.additional_data, node.ptr))
        elif pointer_contains(self.data_input, node.ptr):
            self.data_eval_stack.append(pointer_get(self.data_input, node.ptr))
        else:
            # Try to evaluate as a no-argument callback
            operation, callback = self.function_storage.find_function(node.name, 0)
            if operation == Op.Callback:
                self.data_eval_stack.append(callback([]))
            else:
                self.data_eval_stack.append(_NOT_FOUND)
                self.not_found_stack.append(node)

    def visit_FunctionNode(self, node):
        op = node.operation
        if op == Op.Not:
            args = self.get_arguments(node, 1)
            self.make_result(not truthy(args[0]))
        elif op == Op.And:
            self.make_result(truthy(self.get_arguments(node, 1, 0)[0])
                             and truthy(self.get_arguments(node, 1, 1)[0]))
        elif op == Op.Or:
            self.make_result(truthy(self.get_arguments(node, 1, 0)[0])
                             or truthy(self.get_arguments(node, 1, 1)[0]))
        elif op == Op.In:
            args = self.get_arguments(node, 2)
            container = args[1]
            if isinstance(container, dict):
                items = [container[k] for k in sorted_keys(container)]
            elif isinstance(container, list):
                items = container
            elif container is None:
                items = []
            else:
                items = [container]
            self.make_result(any(json_equal(item, args[0]) for item in items))
        elif op == Op.Equal:
            args = self.get_arguments(node, 2)
            self.make_result(json_equal(args[0], args[1]))
        elif op == Op.NotEqual:
            args = self.get_arguments(node, 2)
            self.make_result(not json_equal(args[0], args[1]))
        elif op == Op.Greater:
            args = self.get_arguments(node, 2)
            self.make_result(json_less(args[1], args[0]))
        elif op == Op.GreaterEqual:
            args = self.get_arguments(node, 2)
            self.make_result(not json_less(args[0], args[1]))
        elif op == Op.Less:
            args = self.get_arguments(node, 2)
            self.make_result(json_less(args[0], args[1]))
        elif op == Op.LessEqual:
            args = self.get_arguments(node, 2)
            self.make_result(not json_less(args[1], args[0]))
        elif op == Op.Add:
            a, b = self.get_arguments(node, 2)
            if isinstance(a, str) and isinstance(b, str):
                self.make_result(a + b)
            elif is_integer(a) and is_integer(b):
                self.make_result(_to_int32(get_int(a) + get_int(b)))
            else:
                self.make_result(get_double(a) + get_double(b))
        elif op == Op.Subtract:
            a, b = self.get_arguments(node, 2)
            if is_integer(a) and is_integer(b):
                self.make_result(_to_int32(get_int(a) - get_int(b)))
            else:
                self.make_result(get_double(a) - get_double(b))
        elif op == Op.Multiplication:
            a, b = self.get_arguments(node, 2)
            if is_integer(a) and is_integer(b):
                self.make_result(_to_int32(get_int(a) * get_int(b)))
            else:
                self.make_result(get_double(a) * get_double(b))
        elif op == Op.Division:
            a, b = self.get_arguments(node, 2)
            if get_double(b) == 0:
                self.throw_renderer_error("division by zero", node)
            self.make_result(get_double(a) / get_double(b))
        elif op == Op.Power:
            a, b = self.get_arguments(node, 2)
            if is_integer(a) and get_int(b) >= 0:
                self.make_result(_to_int32(int(math.pow(get_int(a), get_int(b)))))
            else:
                self.make_result(math.pow(get_double(a), get_int(b)))
        elif op == Op.Modulo:
            a, b = self.get_arguments(node, 2)
            x, y = get_int(a), get_int(b)
            if y == 0:
                raise ZeroDivisionError("integer modulo by zero")
            self.make_result(int(math.fmod(x, y)))
        elif op == Op.AtId:
            container = self.get_arguments(node, 1, 0, False)[0]
            self.accept(node.arguments[1])
            if not self.not_found_stack:
                self.throw_renderer_error("could not find element with given name", node)
            id_node = self.not_found_stack.pop()
            self.data_eval_stack.pop()
            self.data_eval_stack.append(self._at_key(container, id_node.name))
        elif op == Op.At:
            a, b = self.get_arguments(node, 2)
            if isinstance(a, dict):
                self.data_eval_stack.append(self._at_key(a, get_string(b)))
            else:
                self.data_eval_stack.append(self._at_index(a, get_int(b)))
        elif op == Op.Default:
            test_arg = self.get_arguments(node, 1, 0, False)[0]
            if test_arg is not _NOT_FOUND:
                self.data_eval_stack.append(test_arg)
            else:
                self.data_eval_stack.append(self.get_arguments(node, 1, 1)[0])
        elif op == Op.DivisibleBy:
            a, b = self.get_arguments(node, 2)
            divisor = get_int(b)
            self.make_result(divisor != 0 and int(math.fmod(get_int(a), divisor)) == 0)
        elif op == Op.Even:
            self.make_result(int(math.fmod(get_int(self.get_arguments(node, 1)[0]), 2)) == 0)
        elif op == Op.Exists:
            name = get_string(self.get_arguments(node, 1)[0])
            self.make_result(pointer_contains(self.data_input, _pointer_tokens(convert_dot_to_ptr(name))))
        elif op == Op.ExistsInObject:
            a, b = self.get_arguments(node, 2)
            name = get_string(b)
            self.make_result(isinstance(a, dict) and name in a)
        elif op == Op.First:
            value = self.get_arguments(node, 1)[0]
            self.data_eval_stack.append(self._front_back(value, front=True))
        elif op == Op.Float:
            text = get_string(self.get_arguments(node, 1)[0])
            self.make_result(float(_strtod_prefix(text)))
        elif op == Op.Int:
            text = get_string(self.get_arguments(node, 1)[0])
            self.make_result(_stoi(text))
        elif op == Op.Last:
            value = self.get_arguments(node, 1)[0]
            self.data_eval_stack.append(self._front_back(value, front=False))
        elif op == Op.Length:
            val = self.get_arguments(node, 1)[0]
            if isinstance(val, str):
                self.make_result(len(val.encode("utf-8", "surrogateescape")))
            else:
                self.make_result(json_size(val))
        elif op == Op.Lower:
            self.make_result(_ascii_lower(get_string(self.get_arguments(node, 1)[0])))
        elif op == Op.Upper:
            self.make_result(_ascii_upper(get_string(self.get_arguments(node, 1)[0])))
        elif op in (Op.Max, Op.Min):
            value = self.get_arguments(node, 1)[0]
            items = self._iter_values(value)
            if not items:
                raise JsonError("cannot take %s of an empty container" % ("max" if op == Op.Max else "min"))
            best = items[0]
            for item in items[1:]:
                if op == Op.Max and json_less(best, item):
                    best = item
                elif op == Op.Min and json_less(item, best):
                    best = item
            self.data_eval_stack.append(best)
        elif op == Op.Odd:
            self.make_result(int(math.fmod(get_int(self.get_arguments(node, 1)[0]), 2)) != 0)
        elif op == Op.Range:
            self.make_result(list(range(max(0, get_int(self.get_arguments(node, 1)[0])))))
        elif op == Op.Round:
            a, b = self.get_arguments(node, 2)
            precision = get_int(b)
            value = get_double(a) * math.pow(10.0, precision)
            rounded = math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
            result = rounded / math.pow(10.0, precision)
            self.make_result(int(result) if precision == 0 else result)
        elif op == Op.Sort:
            value = self.get_arguments(node, 1)[0]
            if not isinstance(value, list):
                raise JsonError("[json.exception.type_error.302] type must be array, but is %s" % type_name(value))
            import functools
            result = sorted(copy.deepcopy(value),
                            key=functools.cmp_to_key(lambda x, y: -1 if json_less(x, y) else (1 if json_less(y, x) else 0)))
            self.data_eval_stack.append(result)
        elif op == Op.IsBoolean:
            self.make_result(isinstance(self.get_arguments(node, 1)[0], bool))
        elif op == Op.IsNumber:
            self.make_result(is_number(self.get_arguments(node, 1)[0]))
        elif op == Op.IsInteger:
            self.make_result(is_integer(self.get_arguments(node, 1)[0]))
        elif op == Op.IsFloat:
            self.make_result(is_float(self.get_arguments(node, 1)[0]))
        elif op == Op.IsObject:
            self.make_result(isinstance(self.get_arguments(node, 1)[0], dict))
        elif op == Op.IsArray:
            self.make_result(isinstance(self.get_arguments(node, 1)[0], list))
        elif op == Op.IsString:
            self.make_result(isinstance(self.get_arguments(node, 1)[0], str))
        elif op == Op.Callback:
            args = self.get_argument_vector(node)
            self.make_result(node.callback(args))
        elif op == Op.Super:
            args = self.get_argument_vector(node)
            old_level = self.current_level
            level_diff = get_int(args[0]) if len(args) == 1 else 1
            level = self.current_level + level_diff
            if not self.block_statement_stack:
                self.throw_renderer_error("super() call is not within a block", node)
            if level < 1 or level > len(self.template_stack) - 1:
                self.throw_renderer_error("level of super() call does not match parent templates "
                                          "(between 1 and %d)" % (len(self.template_stack) - 1), node)
            current_block_statement = self.block_statement_stack[-1]
            new_template = self.template_stack[level]
            old_template = self.current_template
            block = new_template.block_storage.get(current_block_statement.name)
            if block is not None:
                self.current_template = new_template
                self.current_level = level
                self.accept(block.block)
                self.current_level = old_level
                self.current_template = old_template
            else:
                self.throw_renderer_error("could not find block with name '%s'" % current_block_statement.name, node)
            self.make_result(None)
        elif op == Op.Join:
            a, b = self.get_arguments(node, 2)
            separator = get_string(b)
            parts = []
            for value in self._iter_values(a):
                parts.append(value if isinstance(value, str) else dump(value))
            self.make_result(separator.join(parts))

    @staticmethod
    def _iter_values(value):
        if isinstance(value, dict):
            return [value[k] for k in sorted_keys(value)]
        if isinstance(value, list):
            return list(value)
        if value is None:
            return []
        return [value]

    @staticmethod
    def _at_key(container, key):
        if not isinstance(container, dict):
            raise JsonError("[json.exception.type_error.304] cannot use at() with %s" % type_name(container))
        if key not in container:
            raise JsonError("[json.exception.out_of_range.403] key '%s' not found" % key)
        return container[key]

    @staticmethod
    def _at_index(container, index):
        if not isinstance(container, list):
            raise JsonError("[json.exception.type_error.304] cannot use at() with %s" % type_name(container))
        if index < 0 or index >= len(container):
            raise JsonError("[json.exception.out_of_range.401] array index %d is out of range" % index)
        return container[index]

    @staticmethod
    def _front_back(value, front):
        if isinstance(value, (list, dict)):
            items = Renderer._iter_values(value)
            if not items:
                raise JsonError("[json.exception.invalid_iterator.214] cannot get value")
            return items[0] if front else items[-1]
        if value is None:
            raise JsonError("[json.exception.invalid_iterator.214] cannot get value")
        return value

    def visit_ExpressionListNode(self, node):
        self.print_data(self.eval_expression_list(node))

    def visit_ForArrayStatementNode(self, node):
        result = self.eval_expression_list(node.condition)
        if not isinstance(result, list):
            self.throw_renderer_error("object must be an array", node)

        if not json_empty(self.loop_data()):
            tmp = copy.deepcopy(self.loop_data())
            self.loop_data()["parent"] = tmp

        loop = self._ensure_loop_object()
        loop["is_first"] = True
        loop["is_last"] = len(result) <= 1
        for index, item in enumerate(result):
            self.additional_data[node.value] = copy.deepcopy(item)
            loop = self._ensure_loop_object()
            loop["index"] = index
            loop["index1"] = index + 1
            if index == 1:
                loop["is_first"] = False
            if index == len(result) - 1:
                loop["is_last"] = True
            self.accept(node.body)

        self.additional_data[node.value] = json_clear(self.additional_data.get(node.value))
        self._restore_parent_loop()

    def visit_ForObjectStatementNode(self, node):
        result = self.eval_expression_list(node.condition)
        if not isinstance(result, dict):
            self.throw_renderer_error("object must be an object", node)

        if not json_empty(self.loop_data()):
            # The C++ code moves the loop data into its own "parent" member.
            parent = self.loop_data()
            self.set_loop_data({"parent": parent})

        size = len(result)
        loop = self._ensure_loop_object()
        loop["is_first"] = True
        loop["is_last"] = size <= 1
        for index, key in enumerate(sorted_keys(result)):
            self.additional_data[node.key] = key
            self.additional_data[node.value] = copy.deepcopy(result[key])
            loop = self._ensure_loop_object()
            loop["index"] = index
            loop["index1"] = index + 1
            if index == 1:
                loop["is_first"] = False
            if index == size - 1:
                loop["is_last"] = True
            self.accept(node.body)

        self.additional_data[node.key] = json_clear(self.additional_data.get(node.key))
        self.additional_data[node.value] = json_clear(self.additional_data.get(node.value))
        self._restore_parent_loop()

    def _ensure_loop_object(self):
        loop = self.loop_data()
        if loop is None:
            loop = {}
            self.set_loop_data(loop)
        if not isinstance(loop, dict):
            raise JsonError("[json.exception.type_error.305] cannot use operator[] with a string argument with %s"
                            % type_name(loop))
        return loop

    def _restore_parent_loop(self):
        loop = self._ensure_loop_object()
        if "parent" not in loop:
            loop["parent"] = None
        if not json_empty(loop["parent"]):
            self.set_loop_data(copy.deepcopy(loop["parent"]))

    def visit_IfStatementNode(self, node):
        result = self.eval_expression_list(node.condition)
        if truthy(result):
            self.accept(node.true_statement)
        elif node.has_false_statement:
            self.accept(node.false_statement)

    def visit_IncludeStatementNode(self, node):
        sub_renderer = Renderer(self.template_storage, self.function_storage, self.throw_at_missing_includes)
        included = self.template_storage.get(node.file)
        if included is not None:
            sub_renderer.render_to(self.output, included, self.data_input, self.additional_data)
        elif self.throw_at_missing_includes:
            self.throw_renderer_error("include '%s' not found" % node.file, node)

    def visit_ExtendsStatementNode(self, node):
        included = self.template_storage.get(node.file)
        if included is not None:
            self.render_to(self.output, included, self.data_input, self.additional_data)
            self.break_rendering = True
        elif self.throw_at_missing_includes:
            self.throw_renderer_error("extends '%s' not found" % node.file, node)

    def visit_BlockStatementNode(self, node):
        old_level = self.current_level
        self.current_level = 0
        self.current_template = self.template_stack[0]
        block = self.current_template.block_storage.get(node.name)
        if block is not None:
            self.block_statement_stack.append(node)
            self.accept(block.block)
            self.block_statement_stack.pop()
        self.current_level = old_level
        self.current_template = self.template_stack[-1]

    def visit_SetStatementNode(self, node):
        ptr = "/" + node.key.replace(".", "/")
        value = self.eval_expression_list(node.expression)
        ref = pointer_ref_create(self.additional, _pointer_tokens(ptr))
        ref.set(value)

    def render_to(self, output, tmpl, data, loop_data=None):
        self.output = output
        self.current_template = tmpl
        self.data_input = data
        if loop_data is not None:
            self.additional = [copy.deepcopy(loop_data)]
            if not isinstance(self.additional[0], dict):
                self.additional = [{}]
            self.additional[0].setdefault("loop", None)

        self.template_stack.append(self.current_template)
        self.accept(self.current_template.root)


def _strtod_prefix(text):
    import re
    m = re.match(r"\s*[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?|inf(inity)?|nan)", text, re.I)
    if not m:
        raise ValueError("stod")
    return m.group(0)


def _stoi(text):
    import re
    m = re.match(r"\s*([+-]?\d+)", text)
    if not m:
        raise ValueError("stoi")
    value = int(m.group(1))
    if value > 0x7FFFFFFF or value < -0x80000000:
        raise OverflowError("stoi")
    return value


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

class Environment:
    def __init__(self, input_path="", output_path=None):
        self.input_path = input_path
        self.output_path = input_path if output_path is None else output_path
        self.lexer_config = LexerConfig()
        self.function_storage = FunctionStorage()
        self.template_storage = {}
        self.search_included_templates_in_files = True
        self.throw_at_missing_includes = True

    def set_trim_blocks(self, trim_blocks):
        self.lexer_config.trim_blocks = trim_blocks

    def set_lstrip_blocks(self, lstrip_blocks):
        self.lexer_config.lstrip_blocks = lstrip_blocks

    def add_callback(self, name, num_args, callback):
        self.function_storage.add_callback(name, num_args, callback)

    def _parser(self):
        return Parser(self.lexer_config, self.template_storage, self.function_storage,
                      self.search_included_templates_in_files)

    def parse(self, text):
        parser = self._parser()
        tmpl = Template(text)
        parser.parse_into(tmpl, "./")
        return tmpl

    def parse_template(self, filename):
        parser = self._parser()
        path = self.input_path + filename
        tmpl = Template(Parser.load_file(path))
        parser.parse_into_template(tmpl, path)
        return tmpl

    def render(self, tmpl, data):
        output = []
        Renderer(self.template_storage, self.function_storage, self.throw_at_missing_includes) \
            .render_to(output, tmpl, data)
        return b"".join(output)

    def render_file(self, filename, data):
        return self.render(self.parse_template(filename), data)

    def load_json(self, filename):
        path = self.input_path + filename
        try:
            with open(path, "rb") as f:
                text = f.read()
        except OSError:
            raise InjaError("file_error", "failed accessing file at '%s'" % path)
        return parse_json(text)

    def write(self, filename, data, filename_out):
        # std::ofstream truncates the output before the template is rendered.
        with open(self.output_path + filename_out, "wb") as f:
            f.write(self.render_file(filename, data))

    def write_with_json_file(self, filename, filename_data, filename_out):
        data = self.load_json(filename_data)
        self.write(filename, data, filename_out)
