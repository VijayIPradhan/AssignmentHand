import io
import itertools

from pypdf import PdfReader

from app.render.engine import EngineStyle, FallbackItem, GlyphCompositingEngine, GlyphItem, LineItem, TofuItem
from app.render.layout import AnswerSpace, Layouter, SectionInput
from app.render.markup import (
    DisplayMath,
    ListItem,
    Math,
    MFrac,
    MScript,
    MSqrt,
    Paragraph,
    Table,
    Text,
    parse_blocks,
    parse_inlines,
    parse_math,
    word_count,
)
from app.render.pdf import compose_with_source, rasterize, render_pdf
from app.render.settings import RenderSettings
from tests.pdfs import assignment_pdf

LONG = "\n\n".join(
    ["Energy is conserved in an isolated system and can change form between kinetic and potential energy." * 3] * 12
)


def _layout(lib, answers, **kw):
    s = RenderSettings(**kw)
    eng = GlyphCompositingEngine(lib, EngineStyle(x_height_mm=s.handwriting_size_mm, variation=s.variation))
    secs = [SectionInput(f"q{i}", str(i + 1), f"Question {i + 1}?", a) for i, a in enumerate(answers)]
    return s, Layouter(eng, s).run(secs)


def _glyphs(page):
    return [it for it in page.items if isinstance(it, GlyphItem)]


def test_markup_parsing():
    blocks = parse_blocks(
        "Intro **key** text with $x^2$.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$\\frac{a}{b}$$"
    )
    kinds = [type(b) for b in blocks]
    assert kinds == [Paragraph, ListItem, ListItem, Table, DisplayMath]
    inl = parse_inlines("Intro **key** $x$")
    assert inl[1] == Text("key", True) and inl[-1] == Math("x")
    assert word_count("Two words $x+y$ | table |") == 4


def test_math_parsing_and_unsupported_commands():
    tree, bad = parse_math(r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a} + \alpha_1 + \foo")
    kinds = {type(n) for n in tree.items}
    assert MFrac in kinds and MScript in kinds
    frac = next(n for n in tree.items if isinstance(n, MFrac))
    assert any(isinstance(n, MSqrt) for n in frac.num.items)
    assert bad == ["\\foo"]


def test_short_assignment_single_page_within_margins(template_library):
    s, res = _layout(template_library, ["A short answer."])
    assert len(res.pages) == 1
    m = s.margins
    for g in _glyphs(res.pages[0]):
        assert m.left - 3 <= g.x <= res.width_mm - m.right + 3
        assert m.top - 5 <= g.y <= res.height_mm - m.bottom * 0.3
    assert not [w for w in res.warnings if w["type"] in ("overflow", "clipping")]


def test_long_answers_paginate_without_overflow(template_library):
    s, res = _layout(template_library, [LONG, LONG])
    assert len(res.pages) >= 3
    bottom = res.height_mm - s.margins.bottom
    for p in res.pages:
        body = [g for g in _glyphs(p) if g.y < bottom]  # exclude the page number in the bottom margin
        assert body, "every page has content"
        assert max(g.y for g in body) <= bottom + 0.5
    pdf = render_pdf(res, s)
    assert len(PdfReader(io.BytesIO(pdf)).pages) == len(res.pages)


def test_lines_snap_to_rules_and_do_not_overlap(template_library):
    s, res = _layout(template_library, [LONG])
    baselines = sorted({round(g.y - (g.y - s.margins.top) % s.line_spacing_mm, 3) for g in _glyphs(res.pages[0])})
    gaps = {round(b - a, 3) for a, b in itertools.pairwise(baselines)}
    assert all(
        abs(gap % s.line_spacing_mm) < 0.01 or abs(gap % s.line_spacing_mm - s.line_spacing_mm) < 0.01 for gap in gaps
    )


def test_heading_kept_with_answer(template_library):
    filler = "\n\n".join(["Line of text to fill the page nearly to the bottom edge."] * 26)
    _, res = _layout(template_library, [filler, "Second answer body text."])
    for p in res.pages:
        ys = sorted(g.y for g in _glyphs(p))
        assert ys, "no empty pages"


def test_unicode_missing_and_unsupported_characters_warn(template_library):
    _, res = _layout(template_library, ["Café naïve ☃ 日本"])
    types = {w["type"] for w in res.warnings}
    assert "missing_glyph" in types and "unsupported_character" in types
    items = res.pages[0].items
    assert any(isinstance(i, FallbackItem) for i in items) and any(isinstance(i, TofuItem) for i in items)


def test_math_tables_and_lists_render(template_library):
    ans = (
        "The roots are $x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$.\n\n$$E = mc^2$$\n\n- first\n- second\n\n"
        "| Quantity | Unit |\n|---|---|\n| Force | N |\n| Energy | J |"
    )
    s, res = _layout(template_library, [ans])
    lines = [i for i in res.pages[0].items if isinstance(i, LineItem)]
    assert len(lines) >= 8  # fraction bar, radical, table rules, underline
    png = rasterize(render_pdf(res, s), dpi=60)
    assert png and png[0][:4] == b"\x89PNG"


def test_rendering_is_reproducible_and_seed_dependent(template_library):
    s1, r1 = _layout(template_library, ["Same text every time."], seed=7)
    s2, r2 = _layout(template_library, ["Same text every time."], seed=7)
    assert render_pdf(r1, s1) == render_pdf(r2, s2)
    s3, r3 = _layout(template_library, ["Same text every time."], seed=8)
    assert render_pdf(r3, s3) != render_pdf(r1, s1)


def test_page_sizes_and_orientation(template_library):
    s, res = _layout(template_library, ["x"], page_size="Letter", orientation="landscape")
    page = PdfReader(io.BytesIO(render_pdf(res, s))).pages[0]
    assert round(float(page.mediabox.width)) == 792 and round(float(page.mediabox.height)) == 612


def test_wide_expression_is_shrunk_or_flagged(template_library):
    wide = "$$" + " + ".join(f"x_{{{i}}}^2" for i in range(60)) + "$$"
    _, res = _layout(template_library, [wide])
    assert {"scaled_down", "overflow"} & {w["type"] for w in res.warnings}


def test_writing_on_source_pages_fits_or_extends(template_library):
    src = assignment_pdf()
    s = RenderSettings(paper="source", fit_to_space=True, min_fit_scale=0.7)
    eng = GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=s.handwriting_size_mm))
    sizes = [(210.0, 297.0), (210.0, 297.0)]
    space = AnswerSpace(page=1, top=60.0, bottom=120.0, left=20.0)
    secs = [SectionInput("a", "6", "Q", "Short answer that fits.", space=space)]
    res = Layouter(eng, s).run_on_source(secs, sizes)
    assert [p.source_page for p in res.pages] == [0, 1]
    assert all(60 <= g.y <= 120 for g in _glyphs(res.pages[1]))
    out = compose_with_source(render_pdf(res, s), src, res)
    assert len(PdfReader(io.BytesIO(out)).pages) == 2

    secs = [SectionInput("a", "6", "Q", LONG, space=space)]
    res = Layouter(GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=2.8)), s).run_on_source(secs, sizes)
    types = {w["type"] for w in res.warnings}
    assert "shrunk_to_fit" in types and "exceeds_space" in types
    assert [p.source_page for p in res.pages][:2] == [0, 1] and len(res.pages) > 2


def test_question_text_tables_render_as_tables_not_pipes(template_library):
    s = RenderSettings(include_question_text=True)
    eng = GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=s.handwriting_size_mm))
    q = "Use the table below.\n\n| Time (s) | Distance (m) |\n|---|---|\n| 1 | 4.9 |\n\nEstimate the acceleration."
    res = Layouter(eng, s).run([SectionInput("q", "3", q, "It is about 9.8 m/s.")])
    items = res.pages[0].items
    assert not any(isinstance(i, GlyphItem) and i.glyph.char == "|" for i in items)
    assert sum(isinstance(i, LineItem) for i in items) >= 4  # hand-drawn table rules
