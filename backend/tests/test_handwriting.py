import cv2
import numpy as np
import pytest

from app.handwriting import template as T
from app.handwriting.charset import LOWER, template_cells
from app.handwriting.cursive import group_words, shape_plausible, slice_positions
from app.handwriting.glyphs import GlyphLibrary, outlines, slant_deg
from app.handwriting.preprocess import assess_quality, binarize, normalize_illumination, remove_ruling, to_gray
from app.handwriting.profile_builder import SampleInput, build_library
from app.handwriting.segment import TemplateNotFound, analyze_freeform, extract_template
from tests.conftest import run_jobs
from tests.synth import degrade, filled_template, freeform_sample, to_png


def test_template_has_every_character_and_valid_pdf():
    pdf = T.build_template_pdf()
    assert pdf.startswith(b"%PDF")
    cells = [c.char for p in range(T.page_count()) for c in T.cells_for_page(p)]
    assert cells == template_cells()
    assert set(LOWER) <= set(cells)


@pytest.mark.parametrize("rotate", [0, 6, -9, 90, 180, 271])
def test_template_extraction_survives_rotation_noise_and_shadow(rotate):
    img = degrade(filled_template(0), rotate=rotate, perspective=0.02, shadow=True, noise=6, seed=3)
    res = extract_template(img, "s")
    assert res.info["page"] == 0
    assert res.info["glyphs"] >= 115, res.issues
    chars = {g.char for g in res.glyphs}
    assert set("abcxyz") <= chars


def test_template_second_page_is_identified_by_its_code():
    res = extract_template(degrade(filled_template(1), rotate=180, seed=4), "s")
    assert res.info["page"] == 1
    assert {"0", "9", "?", "√"} <= {g.char for g in res.glyphs}


def test_missing_cells_are_reported_as_missing_glyphs():
    res = build_library([SampleInput("a", "template", filled_template(0, skip="qz"))])
    cov = res.library.coverage()
    assert "z" not in cov and cov["a"] == 3
    assert "z" in res.report["missing"]["lowercase"]
    # "q" is approximated by mirroring the user's own "p", and reported as such
    assert all(g.source == "derived" for g in res.library.variants("q"))
    assert "q (from p)" in res.report["derived_letters"]


def test_non_template_image_is_rejected_with_reason():
    blank = np.full((2000, 1500, 3), 250, np.uint8)
    cv2.putText(blank, "hello", (300, 1000), cv2.FONT_HERSHEY_SIMPLEX, 4, (20, 20, 20), 8)
    with pytest.raises(TemplateNotFound):
        extract_template(blank, "s")
    out = build_library([SampleInput("x", "template", blank)])
    assert out.samples[0].status == "rejected"
    assert any("corner squares" in i for i in out.samples[0].quality["issues"])


def test_low_resolution_sample_is_rejected():
    tiny = cv2.resize(filled_template(0), (300, 420))
    out = build_library([SampleInput("x", "template", tiny)])
    assert out.samples[0].status == "rejected"
    assert any("resolution" in i for i in out.samples[0].quality["issues"])
    assert out.ready is False


def test_blank_page_is_flagged_as_no_handwriting():
    img = np.full((1600, 1200, 3), 245, np.uint8)
    g = normalize_illumination(to_gray(img))
    q = assess_quality(img, g, binarize(g))
    assert not q.ok and any("no handwriting" in i for i in q.issues)


def test_mixed_case_and_digits_coverage(template_library):
    cov = template_library.coverage()
    assert all(c in cov for c in "aZ09")
    assert template_library.x_height_mm > 2 and template_library.cap_height_mm > template_library.x_height_mm


def test_slant_estimate_follows_shear():
    upright = np.zeros((120, 40), np.uint8)
    cv2.line(upright, (20, 5), (20, 115), 255, 6)
    slanted = np.zeros((120, 80), np.uint8)
    cv2.line(slanted, (20, 115), (60, 5), 255, 6)  # leans right ~20 degrees
    assert abs(slant_deg(upright)) <= 2
    assert 14 <= slant_deg(slanted) <= 26


def test_outlines_are_closed_polygons_in_mm(template_library):
    g = template_library.variants("o")[0]
    polys = outlines(g)
    assert len(polys) >= 2  # outer contour + counter (hole)
    xs = [x for p in polys for x, _ in p]
    assert min(xs) >= -0.5 and max(xs) <= g.width_mm + 0.5
    thick = outlines(g, 1.6)
    assert thick != polys


def test_library_roundtrip(template_library):
    lib2 = GlyphLibrary.from_bytes(template_library.to_bytes())
    assert lib2.coverage() == template_library.coverage()
    a, b = template_library.variants("g")[0], lib2.variants("g")[0]
    assert np.array_equal(a.bitmap, b.bitmap) and a.baseline_px == pytest.approx(b.baseline_px, abs=0.01)


def test_freeform_print_writing_with_transcript_yields_glyphs_and_metrics():
    text = "the quick brown fox\njumps over a lazy dog"
    img = freeform_sample(text, slant=10)
    res = analyze_freeform(img, "f", text)
    m = res.info["metrics"]
    assert m["lines"] == 2
    assert 5 <= m["slant_deg"] <= 15
    assert res.info["aligned_glyphs"] >= 25
    chars = {g.char for g in res.glyphs}
    assert {"q", "x", "j"} <= chars and len(chars) >= 18


def test_freeform_transcript_line_mismatch_is_reported():
    img = freeform_sample("one line here\nand another")
    res = analyze_freeform(img, "f", "only one line")
    assert any("lines" in i for i in res.issues)
    assert res.glyphs == []


def _ruled(img: np.ndarray, pitch: int = 60) -> np.ndarray:
    out = img.copy()
    for y in range(40, out.shape[0], pitch):
        cv2.line(out, (0, y), (out.shape[1], y + 6), (200, 170, 150), 2)  # slightly tilted pale rules
    return out


def test_ruled_lines_are_removed_but_strokes_kept():
    img = freeform_sample("hello world\nruled paper", x_height_mm=3.0)
    ruled = _ruled(img)
    g = normalize_illumination(to_gray(ruled))
    mask = binarize(g)
    cleaned, info = remove_ruling(ruled, mask)
    assert info["rules"] >= 5
    ref = binarize(normalize_illumination(to_gray(img)))
    # most handwriting ink survives; most rule ink is gone
    kept = ((cleaned > 0) & (ref > 0)).sum() / max(1, (ref > 0).sum())
    leftover = ((cleaned > 0) & (ref == 0)).sum() / max(1, ((mask > 0) & (ref == 0)).sum())
    assert kept > 0.85 and leftover < 0.25


def test_cursive_slicing_finds_thin_joins():
    # three blobs joined by thin bridges; cuts should land on the bridges
    cols = np.array([5] * 20 + [1] * 4 + [6] * 20 + [1] * 4 + [5] * 20)
    cuts = slice_positions(cols, "aaa")
    assert len(cuts) == 2
    assert 19 <= cuts[0] <= 25 and 43 <= cuts[1] <= 49


def test_word_grouping_uses_gaps_and_widths():
    spans = [(0, 30), (34, 60), (100, 140), (190, 200)]  # "ab" "cd" | "efg"? ...
    out = group_words(spans, ["abcd", "ef", "g"], xh=20)
    assert out == [(0, 2), (2, 3), (3, 4)]
    assert group_words(spans[:2], ["a", "b", "c"], xh=20) is None


def test_shape_plausibility_by_letter_class():
    assert shape_plausible("a", 1.0, 0.0, 0.9)
    assert not shape_plausible("a", 2.2, 0.0, 0.9)  # too tall for an x-height letter
    assert shape_plausible("h", 1.9, 0.0, 1.0)
    assert not shape_plausible("g", 1.0, 0.0, 1.0)  # a 'g' needs a descender
    assert not shape_plausible("e", 1.0, 0.0, 4.0)  # a whole word, not a letter


# ------------------------------------------------------------------------------------- API


def test_profile_persistence_preview_and_deletion(auth_client, tmp_env):
    c = auth_client
    pid = c.post("/api/v1/profiles", json={"name": "Neat"}).json()["id"]
    r = c.post(
        f"/api/v1/profiles/{pid}/samples",
        files={"file": ("a.png", to_png(filled_template(0)))},
        data={"kind": "template"},
    )
    sid = r.json()["id"]
    c.patch(f"/api/v1/profiles/{pid}/samples/{sid}", json={"rotate": 0})
    c.post(f"/api/v1/profiles/{pid}/process")
    run_jobs()
    p = c.get(f"/api/v1/profiles/{pid}").json()
    assert p["status"] in ("ready", "needs_samples") and p["metrics"]["glyph_count"] >= 100
    assert p["samples"][0]["has_processed"] is True
    assert c.get(f"/api/v1/profiles/{pid}/samples/{sid}/image?variant=processed").status_code == 200
    assert c.get(f"/api/v1/profiles/{pid}/glyphs.png").status_code == 200

    c.patch(f"/api/v1/profiles/{pid}", json={"style": {"handwriting_size_mm": 3.2, "ink_color": "#112233"}})
    assert c.get(f"/api/v1/profiles/{pid}").json()["style"]["ink_color"] == "#112233"
    prev = c.post(f"/api/v1/profiles/{pid}/preview", json={"text": "Hello ☃ world"})
    assert prev.status_code == 200 and "U+2603" in prev.headers["x-render-warnings"]

    storage_dir = tmp_env / "storage"
    assert any(storage_dir.rglob("original.png"))
    assert c.delete(f"/api/v1/profiles/{pid}").status_code == 204
    assert c.get(f"/api/v1/profiles/{pid}").status_code == 404
    assert not any(storage_dir.rglob("original.png"))
    assert not any(storage_dir.rglob("v*.json"))


def test_sample_upload_rejects_non_images(auth_client):
    c = auth_client
    pid = c.post("/api/v1/profiles", json={"name": "x"}).json()["id"]
    r = c.post(f"/api/v1/profiles/{pid}/samples", files={"file": ("evil.png", b"#!/bin/sh\necho hi")})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_sample_crop_can_be_cleared(auth_client):
    c = auth_client
    pid = c.post("/api/v1/profiles", json={"name": "x"}).json()["id"]
    sid = c.post(f"/api/v1/profiles/{pid}/samples", files={"file": ("a.png", to_png(filled_template(0)))}).json()["id"]
    assert c.patch(f"/api/v1/profiles/{pid}/samples/{sid}", json={"crop": [0.1, 0.1, 0.9, 0.9]}).json()["corrections"][
        "crop"
    ]
    assert (
        "crop" not in c.patch(f"/api/v1/profiles/{pid}/samples/{sid}", json={"clear_crop": True}).json()["corrections"]
    )


def test_letter_verification_is_cached_per_profile(tmp_env):
    import numpy as np

    from app.handwriting.glyphs import Glyph
    from app.models import HandwritingProfile
    from app.services.profiles import cached_verify

    p = HandwritingProfile(id="p1", user_id="u1", name="x")
    g = lambda ch, v: Glyph(ch, np.full((10, 8), v, np.uint8), 10.0, 10.0, "freeform")  # noqa: E731
    calls = []

    def verify(todo):
        calls.append(sum(len(v) for v in todo.values()))
        return {ch: [x for x in gs if x.bitmap[0, 0] == 255] for ch, gs in todo.items()}  # keep "good" ones

    first = cached_verify(p, {"a": [g("a", 255), g("a", 128)]}, verify, lambda *_: None)
    assert [x.bitmap[0, 0] for x in first["a"]] == [255] and calls == [2]
    # Same crops plus one new one: only the new crop is sent
    again = cached_verify(p, {"a": [g("a", 255), g("a", 128), g("a", 254)]}, verify, lambda *_: None)
    assert calls == [2, 1] and [x.bitmap[0, 0] for x in again["a"]] == [255]
    # Everything cached: no model call at all
    cached_verify(p, {"a": [g("a", 255)]}, verify, lambda *_: None)
    assert calls == [2, 1]


def test_filled_loops_are_dropped_when_an_open_variant_exists():
    import cv2
    import numpy as np

    from app.handwriting.glyphs import Glyph, GlyphLibrary
    from app.handwriting.profile_builder import _drop_filled, has_counter

    ring = np.zeros((40, 40), np.uint8)
    cv2.circle(ring, (20, 20), 14, 255, 4)
    blob = np.zeros((40, 40), np.uint8)
    cv2.circle(blob, (20, 20), 14, 255, -1)
    assert has_counter(Glyph("o", ring, 10, 38)) and not has_counter(Glyph("o", blob, 10, 38))
    lib = GlyphLibrary()
    lib.add(Glyph("o", ring, 10, 38))
    lib.add(Glyph("o", blob, 10, 38))
    lib.add(Glyph("e", blob.copy(), 10, 38))
    assert _drop_filled(lib) == ["e"]  # only filled variants: kept, and reported
    assert len(lib.variants("o")) == 1 and has_counter(lib.variants("o")[0])
    assert len(lib.variants("e")) == 1


def test_clean_glyph_removes_stray_specks_and_sits_letters_on_the_baseline():
    import numpy as np

    from app.handwriting.glyphs import Glyph
    from app.handwriting.profile_builder import clean_glyph

    bmp = np.zeros((60, 40), np.uint8)
    bmp[25:55, 5:35] = 255  # the letter body
    bmp[2:5, 2:5] = 255  # a neighbour's i-dot caught in the cut
    g = Glyph("n", bmp, 10.0, 50.0)  # baseline 5 px above the ink bottom
    assert clean_glyph(g)
    assert g.bitmap.shape[0] < 40  # the speck is gone and the bitmap re-cropped
    ink_bottom = np.nonzero((g.bitmap > 127).any(axis=1))[0].max() + 1
    assert g.baseline_px == ink_bottom
    i = Glyph("i", bmp.copy(), 10.0, 55.0)  # an i keeps its dot
    clean_glyph(i)
    assert i.bitmap.shape == (60, 40)
