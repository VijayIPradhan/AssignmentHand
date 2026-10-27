"""Parse answer text (a small Markdown subset with $LaTeX$ math) into renderable blocks.

Supported: paragraphs, ``## subheadings``, ``-``/``*``/``1.``/``a)`` lists, pipe tables,
``**underline**``, inline ``$math$``, display ``$$math$$``, and ``[[figure:N]]`` placeholders.
"""

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------------
# Inline + block model
# --------------------------------------------------------------------------------------------


@dataclass
class Text:
    text: str
    underline: bool = False


@dataclass
class Math:
    src: str


Inline = Text | Math


@dataclass
class Paragraph:
    inlines: list[Inline]


@dataclass
class Heading:
    inlines: list[Inline]


@dataclass
class ListItem:
    marker: str
    inlines: list[Inline]
    level: int = 0


@dataclass
class Table:
    rows: list[list[list[Inline]]]
    header: bool = True


@dataclass
class DisplayMath:
    src: str


@dataclass
class Figure:
    index: int


Block = Paragraph | Heading | ListItem | Table | DisplayMath | Figure

_LIST_RE = re.compile(r"^(\s*)([-*•]|\d{1,3}[.)]|[a-zA-Z][.)]|\([a-zA-Z0-9]{1,4}\)|[ivxIVX]{1,5}[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_FIGURE_RE = re.compile(r"^\s*\[\[figure:(\d+)\]\]\s*$", re.I)


def parse_inlines(s: str) -> list[Inline]:
    out: list[Inline] = []
    i, buf, underline = 0, [], False

    def flush():
        if buf:
            out.append(Text("".join(buf), underline))
            buf.clear()

    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in "$*_\\":
            buf.append(s[i + 1])
            i += 2
            continue
        if c == "$":
            j = i + 1
            while j < len(s) and not (s[j] == "$" and s[j - 1] != "\\"):
                j += 1
            if j < len(s) and j > i + 1:
                flush()
                out.append(Math(s[i + 1 : j]))
                i = j + 1
                continue
        if s.startswith("**", i) or s.startswith("__", i):
            flush()
            underline = not underline
            i += 2
            continue
        if c == "`":
            i += 1
            continue
        if c == "*" and i + 1 < len(s) and s[i + 1] != " ":
            # *emphasis*: handwriting has no italics, drop the markers
            j = s.find("*", i + 1)
            if j > i + 1:
                buf.append(s[i + 1 : j])
                i = j + 1
                continue
        buf.append(c)
        i += 1
    flush()
    return [x for x in out if not (isinstance(x, Text) and x.text == "")]


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_blocks(text: str) -> list[Block]:
    text = text.replace("\r\n", "\n").replace("\t", "    ")
    lines = text.split("\n")
    blocks: list[Block] = []
    para: list[str] = []

    def flush_para():
        if para:
            joined = " ".join(p.strip() for p in para).strip()
            if joined:
                blocks.append(Paragraph(parse_inlines(joined)))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        if stripped.startswith("$$"):
            flush_para()
            body = stripped[2:]
            if body.endswith("$$") and len(body) >= 2:
                blocks.append(DisplayMath(body[:-2].strip()))
                i += 1
                continue
            acc = [body]
            i += 1
            while i < len(lines) and "$$" not in lines[i]:
                acc.append(lines[i])
                i += 1
            if i < len(lines):
                acc.append(lines[i].split("$$")[0])
                i += 1
            blocks.append(DisplayMath(" ".join(a.strip() for a in acc).strip()))
            continue
        if m := _FIGURE_RE.match(stripped):
            flush_para()
            blocks.append(Figure(int(m.group(1))))
            i += 1
            continue
        if stripped.startswith("#"):
            flush_para()
            blocks.append(Heading(parse_inlines(stripped.lstrip("#").strip())))
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            rows = [_split_row(stripped)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            ncol = max(len(r) for r in rows)
            rows = [r + [""] * (ncol - len(r)) for r in rows]
            blocks.append(Table([[parse_inlines(c) for c in r] for r in rows], header=True))
            continue
        if m := _LIST_RE.match(line):
            flush_para()
            indent, marker, rest = m.groups()
            if marker in "-*•":
                marker = "-"
            level = min(3, len(indent) // 2)
            body = [rest]
            i += 1
            while i < len(lines) and lines[i].strip() and not _LIST_RE.match(lines[i]) and lines[i].startswith(" "):
                body.append(lines[i].strip())
                i += 1
            blocks.append(ListItem(marker, parse_inlines(" ".join(body)), level))
            continue
        para.append(line)
        i += 1
    flush_para()
    return blocks


def plain_text(inlines: list[Inline]) -> str:
    return "".join(x.text if isinstance(x, Text) else f"${x.src}$" for x in inlines)


def word_count(text: str) -> int:
    stripped = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", " x ", text, flags=re.S)
    stripped = re.sub(r"[|#*_`>\[\]]", " ", stripped)
    return len(re.findall(r"[\w'’\-]+", stripped))


# --------------------------------------------------------------------------------------------
# LaTeX subset -> math node tree
# --------------------------------------------------------------------------------------------


@dataclass
class MSym:
    ch: str
    op: bool = False  # binary operator / relation: gets extra space either side


@dataclass
class MTextRun:
    text: str


@dataclass
class MRow:
    items: list["MNode"] = field(default_factory=list)


@dataclass
class MFrac:
    num: "MNode"
    den: "MNode"
    bar: bool = True


@dataclass
class MScript:
    base: "MNode"
    sup: "MNode | None" = None
    sub: "MNode | None" = None


@dataclass
class MSqrt:
    body: "MNode"
    index: "MNode | None" = None


@dataclass
class MOver:
    body: "MNode"
    kind: str  # bar | vec | hat | dot


@dataclass
class MSpace:
    em: float


MNode = MSym | MTextRun | MRow | MFrac | MScript | MSqrt | MOver | MSpace

SYMBOLS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "infty": "∞",
    "partial": "∂",
    "nabla": "∇",
    "sum": "Σ",
    "prod": "Π",
    "int": "∫",
    "oint": "∮",
    "degree": "°",
    "circ": "°",
    "angle": "∠",
    "therefore": "∴",
    "because": "∵",
    "forall": "∀",
    "exists": "∃",
    "emptyset": "∅",
    "ldots": "...",
    "cdots": "...",
    "dots": "...",
    "prime": "'",
    "hbar": "ħ",
    "ell": "l",
    "%": "%",
    "$": "$",
    "{": "{",
    "}": "}",
    "_": "_",
    "&": "&",
    "#": "#",
    "lbrace": "{",
    "rbrace": "}",
    "langle": "⟨",
    "rangle": "⟩",
    "vert": "|",
    "mid": "|",
}
OPERATORS = {
    "times": "×",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "cdot": "·",
    "ast": "*",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "~",
    "simeq": "≈",
    "propto": "∝",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "cup": "∪",
    "cap": "∩",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "Leftrightarrow": "⇔",
    "iff": "⇔",
    "implies": "⇒",
    "gets": "←",
    "ll": "≪",
    "gg": "≫",
    "perp": "⊥",
    "parallel": "∥",
}
FUNCTIONS = {
    "sin",
    "cos",
    "tan",
    "sec",
    "csc",
    "cot",
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "log",
    "ln",
    "exp",
    "lim",
    "max",
    "min",
    "det",
    "gcd",
    "mod",
    "deg",
    "arg",
    "dim",
}
TEXT_CMDS = {
    "text",
    "mathrm",
    "textrm",
    "mathbf",
    "textbf",
    "mathit",
    "textit",
    "operatorname",
    "mathsf",
    "mathbb",
    "mathcal",
    "boldsymbol",
}
SPACES = {",": 0.17, ":": 0.22, ";": 0.28, "!": -0.1, "quad": 1.0, "qquad": 2.0, " ": 0.25}
IGNORED = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "displaystyle", "limits", "nolimits"}
OP_CHARS = set("+-=<>×÷±≤≥≠≈→·")


class MathParser:
    def __init__(self, src: str):
        self.toks = self._tokenize(src)
        self.i = 0
        self.unsupported: list[str] = []

    @staticmethod
    def _tokenize(s: str) -> list[str]:
        toks, i = [], 0
        while i < len(s):
            c = s[i]
            if c == "\\":
                j = i + 1
                if j < len(s) and s[j].isalpha():
                    while j < len(s) and s[j].isalpha():
                        j += 1
                    toks.append(s[i:j])
                    i = j
                else:
                    toks.append(s[i : j + 1])
                    i = j + 1
            elif c.isspace():
                i += 1
            else:
                toks.append(c)
                i += 1
        return toks

    def _peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> str | None:
        t = self._peek()
        self.i += 1
        return t

    def parse(self) -> MRow:
        return self._row(stop=None)

    def _row(self, stop: str | None) -> MRow:
        row = MRow()
        while (t := self._peek()) is not None and t != stop:
            if t == "}" and stop is None:
                self.i += 1
                continue
            node = self._atom()
            if node is None:
                continue
            while self._peek() in ("^", "_"):
                kind = self._next()
                arg = self._atom() or MRow()
                if not isinstance(node, MScript):
                    node = MScript(node)
                if kind == "^":
                    node.sup = arg
                else:
                    node.sub = arg
            if self._peek() == "'" and not isinstance(node, MScript):
                self.i += 1
                node = MScript(node, sup=MSym("'"))
            row.items.append(node)
        if stop is not None and self._peek() == stop:
            self.i += 1
        return row

    def _group(self) -> MNode:
        if self._peek() == "{":
            self.i += 1
            return self._row(stop="}")
        return self._atom() or MRow()

    def _raw_group(self) -> str:
        if self._peek() != "{":
            t = self._next()
            return t or ""
        self.i += 1
        depth, out = 1, []
        while (t := self._next()) is not None:
            if t == "{":
                depth += 1
            elif t == "}":
                depth -= 1
                if depth == 0:
                    break
            out.append(t[1:] if t.startswith("\\") and len(t) == 2 and not t[1].isalpha() else t)
        return "".join(out)

    def _atom(self) -> MNode | None:
        t = self._next()
        if t is None:
            return None
        if t == "{":
            return self._row(stop="}")
        if t.startswith("\\"):
            name = t[1:]
            if name in IGNORED:
                return None
            if name in ("frac", "dfrac", "tfrac", "cfrac"):
                return MFrac(self._group(), self._group())
            if name == "binom":
                return MRow([MSym("("), MFrac(self._group(), self._group(), bar=False), MSym(")")])
            if name == "sqrt":
                index = None
                if self._peek() == "[":
                    self.i += 1
                    index = self._row(stop="]")
                return MSqrt(self._group(), index)
            if name in ("overline", "bar"):
                return MOver(self._group(), "bar")
            if name in ("vec", "overrightarrow"):
                return MOver(self._group(), "vec")
            if name in ("hat", "widehat"):
                return MOver(self._group(), "hat")
            if name == "dot":
                return MOver(self._group(), "dot")
            if name in TEXT_CMDS:
                txt = self._raw_group()
                if name in ("mathbb", "mathcal"):
                    self.unsupported.append(f"\\{name} (drawn as plain letters)")
                return MTextRun(txt)
            if name in FUNCTIONS:
                return MTextRun(name)
            if name in SPACES:
                return MSpace(SPACES[name])
            if name in OPERATORS:
                return MSym(OPERATORS[name], op=True)
            if name in SYMBOLS:
                return MSym(SYMBOLS[name])
            if name == "begin":
                env = self._raw_group()
                self.unsupported.append(f"\\begin{{{env}}}")
                body = []
                while (tok := self._next()) is not None and tok != "\\end":
                    body.append(tok)
                self._raw_group()
                return MTextRun("".join(body))
            self.unsupported.append(t)
            return MTextRun(t)
        if t == "^" or t == "_":
            return MRow()
        if t == "-":
            return MSym("-", op=True)
        return MSym(t, op=t in OP_CHARS)


def parse_math(src: str) -> tuple[MRow, list[str]]:
    p = MathParser(src)
    tree = p.parse()
    return tree, p.unsupported
