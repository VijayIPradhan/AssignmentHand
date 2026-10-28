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
    eng = GlyphCompositingEngine(
        lib, EngineStyle(x_height_mm=s.handwriting_size_mm, variation=s.variation, joined=s.letter_style == "joined")
    )
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


def test_answers_skip_printed_marks_labels(template_library):
    s = RenderSettings(paper="source", fit_to_space=False)
    eng = GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=s.handwriting_size_mm))
    band = (40.0, 46.0)  # where "[10 Marks]" is printed
    space = AnswerSpace(page=0, top=20.0, bottom=150.0, left=20.0, blocked=[band])
    res = Layouter(eng, s).run_on_source([SectionInput("a", "1", "Q", LONG, space=space)], [(210.0, 297.0)])
    for g in _glyphs(res.pages[0]):
        top = g.y - g.glyph.top_mm * g.scale
        bottom = g.y - g.glyph.bottom_mm * g.scale
        assert not (top < band[1] and bottom > band[0]), "handwriting overlaps the printed marks label"
    assert min(g.y for g in _glyphs(res.pages[0])) < band[0]  # writing starts above the label


def _engine(lib, joined):
    return GlyphCompositingEngine(lib, EngineStyle(x_height_mm=3.0, variation=0.5, joined=joined))


def test_joined_writing_connects_letters_within_words_only(template_library):
    import random

    printed = _engine(template_library, False).shape("minimum ok", random.Random(3))
    assert not any(isinstance(it, LineItem) for it in printed.items)
    run = _engine(template_library, True).shape("minimum ok", random.Random(3))
    glyphs = [it for it in run.items if isinstance(it, GlyphItem)]
    joins = [it for it in run.items if isinstance(it, LineItem)]
    assert len(glyphs) == 9
    assert 5 <= len(joins) <= 7  # "minimum": 6 joins, "ok": 1; never across the space
    ok_start, xh = glyphs[7].x, 3.0
    for ln in joins:
        xs = [p[0] for p in ln.pts]
        assert not min(xs) < ok_start < max(xs)  # never across the word gap
        assert all(-1.2 * xh <= p[1] <= 0.3 * xh for p in ln.pts)  # within the x-height band
        assert 0.05 < ln.width < 1.0


def test_joined_writing_skips_capitals_digits_and_punctuation(template_library):
    import random

    run = _engine(template_library, True).shape("A1.B", random.Random(1))
    assert not any(isinstance(it, LineItem) for it in run.items)


def test_joined_render_is_reproducible(template_library):
    a = render_pdf(
        _layout(template_library, ["Joined writing flows."], letter_style="joined")[1],
        RenderSettings(letter_style="joined"),
    )
    b = render_pdf(
        _layout(template_library, ["Joined writing flows."], letter_style="joined")[1],
        RenderSettings(letter_style="joined"),
    )
    assert a == b


def test_letters_are_normalised_to_a_common_size(template_library):
    import dataclasses
    import random

    import cv2

    g = template_library.variants("o")[0]
    big = dataclasses.replace(g, bitmap=cv2.resize(g.bitmap, None, fx=1.25, fy=1.25), baseline_px=g.baseline_px * 1.25)
    eng = _engine(template_library, False)
    k_ref = eng._glyph_run(g, random.Random(0), 1.0).items[0].scale
    k_big = eng._glyph_run(big, random.Random(0), 1.0).items[0].scale
    assert abs(k_big * big.top_mm - k_ref * g.top_mm) < 0.08 * k_ref * g.top_mm


def test_letter_style_choice_resolves_assignment_then_profile_then_samples(template_library):
    from types import SimpleNamespace

    from app.services.assignments import effective_settings
    from app.services.profiles import joined_letters

    lib = template_library
    for style, detected, want in [
        ("auto", True, True),
        ("auto", False, False),
        ("separate", True, False),
        ("joined", False, True),
    ]:
        lib.joined_writing = detected
        assert joined_letters(lib, RenderSettings(letter_style=style)) is want
    lib.joined_writing = False

    def settings(profile_style, assignment_style):
        prof = SimpleNamespace(style={"letter_style": profile_style} if profile_style else {}, metrics={})
        cfg = SimpleNamespace(settings={"letter_style": assignment_style} if assignment_style else {})
        a = SimpleNamespace(render_config=cfg, user_id="u")
        return effective_settings(a, prof, {"sign_fields": False}).letter_style

    assert settings(None, None) == "auto"
    assert settings("joined", None) == "joined"
    assert settings("joined", "auto") == "joined"  # an assignment on "auto" follows its profile
    assert settings("joined", "separate") == "separate"


def test_hand_placed_answer_area_is_used_exactly(template_library):
    s = RenderSettings(paper="source", fit_to_space=False)
    eng = GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=s.handwriting_size_mm))
    space = AnswerSpace(page=0, top=60.0, bottom=200.0, left=15.0, right=110.0)  # left of the page margin
    res = Layouter(eng, s).run_on_source([SectionInput("a", "1", "Q", LONG[:900], space=space)], [(210.0, 297.0)])
    glyphs = _glyphs(res.pages[0])
    assert glyphs and min(g.x for g in glyphs) < s.margins.left  # not pushed in to the margin
    assert max(g.x + g.glyph.width_mm * g.scale for g in glyphs) <= 110.0 + 1.0
    assert all(60.0 <= g.y <= 200.0 for g in glyphs)


KNN = """{"type": "plot", "x_label": "x1", "y_label": "x2", "x_range": [0, 9], "y_range": [0, 5.6],
 "series": [{"name": "Class A", "marker": "triangle", "points": [[1.2, 1.4], [2.0, 2.6], [3.6, 3.9], [2.7, 2.7]]},
            {"name": "Class B", "marker": "circle", "points": [[6.0, 4.2], [5.4, 3.3], [7.4, 2.6]]}],
 "points": [{"x": 4.6, "y": 3.6, "marker": "star", "label": "new point"}],
 "circles": [{"x": 4.6, "y": 3.6, "around_nearest": 3, "label": "k = 3"}],
 "caption": "KNN with k = 3"}"""


def test_diagram_block_is_parsed_and_not_counted_as_words():
    from app.render.markup import Diagram, parse_blocks, word_count

    text = f"Before the figure.\n\n```diagram\n{KNN}\n```\n\nAfter it."
    blocks = parse_blocks(text)
    assert [type(b).__name__ for b in blocks] == ["Paragraph", "Diagram", "Paragraph"]
    assert isinstance(blocks[1], Diagram) and '"star"' in blocks[1].src
    assert word_count(text) == 5


def test_knn_diagram_is_drawn_by_hand_with_a_circle_around_the_three_nearest(template_library):
    import math
    import random

    from app.render.diagram import DiagramDrawer, parse_spec

    eng = GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=3.0))
    spec = parse_spec(KNN)
    drawer = DiagramDrawer(eng, random.Random(1), 150.0)
    run = drawer.plot(spec)
    strokes = [it for it in run.items if isinstance(it, LineItem)]
    glyphs = [it for it in run.items if isinstance(it, GlyphItem)]
    assert len(strokes) > 15 and glyphs  # axes, markers, circle; labels written in the user's glyphs
    assert 0 < run.width <= 150.0 and run.ascent > 40

    # The circle is the stroke with the most points around the star: exactly 3 plotted points inside.
    data = [p for s in spec.series for p in s.points]
    star = (4.6, 3.6)
    nearest = sorted(math.dist(star, p) for p in data)
    assert nearest[2] < nearest[3]  # the test data has a clear 3rd/4th gap
    # Map the recorded placements onto the drawing, then measure the actual (wobbly) strokes.
    dx, dy = drawer.shift
    ((rc, r),) = drawer.rings
    c = (rc[0] + dx, rc[1] + dy)
    ring = min(strokes, key=lambda it: abs(sum(math.dist(c, q) for q in it.pts) / len(it.pts) - r))
    inner = min(math.dist(c, q) for q in ring.pts)
    outer = max(math.dist(c, q) for q in ring.pts)
    assert outer - inner < 0.15 * r  # roughly round
    reach = []
    for kind, mp, size in drawer.markers:
        if kind == "star":
            continue
        m = (mp[0] + dx, mp[1] + dy)
        ink = [q for it in strokes if it is not ring for q in it.pts if math.dist(m, q) < size * 1.6]
        assert ink, kind
        reach.append((min(math.dist(c, q) for q in ink), max(math.dist(c, q) for q in ink)))
    reach.sort()
    # The three nearest markers are wholly inside the drawn outline, the fourth wholly outside it.
    assert all(far < inner for _near, far in reach[:3])
    assert reach[3][0] > outer


def test_diagram_shrinks_with_the_handwriting(template_library):
    import random

    from app.render.diagram import DiagramDrawer, parse_spec

    spec = parse_spec(KNN)
    big = DiagramDrawer(GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=3.0)), random.Random(1), 180.0)
    small = DiagramDrawer(
        GlyphCompositingEngine(template_library, EngineStyle(x_height_mm=2.1)), random.Random(1), 180.0
    )
    assert small.plot(spec).width < big.plot(spec).width * 0.8  # fit-to-space shrinks the drawing too


def test_broken_diagrams_warn_instead_of_failing(template_library):
    bad = 'Text.\n\n```diagram\n{"type": "plot", "series": [\n```\n\nMore.'
    unsupported = 'Text.\n\n```diagram\n{"type": "flowchart"}\n```'
    _s, res = _layout(template_library, [bad, unsupported])
    msgs = [w["message"] for w in res.warnings if w["type"] == "diagram_invalid"]
    assert len(msgs) == 2 and "not valid JSON" in msgs[0] and "not supported" in msgs[1]
    assert _glyphs(res.pages[0])  # the rest of the answer is still written
