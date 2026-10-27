import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.ingestion.ocr import OcrError, OcrLine, OcrProvider, OcrResult
from app.ingestion.pdf import PdfValidationError, extract_pdf, validate_pdf
from app.ingestion.questions import extract_marks, segment
from app.render.fonts import fallback_font
from tests.conftest import run_jobs
from tests.pdfs import assignment_pdf, encrypted_pdf, plain_paragraphs_pdf, scanned_pdf


def _seg(data: bytes, ocr=None):
    ex = extract_pdf(data, ocr)
    return ex, segment(ex.lines, ex.pages, ex.figures)


def _pdf(pages: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        c.setFont(fallback_font(), 11)
        y = 780
        for t in lines:
            c.drawString(60, y, t)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


class ScriptedOcr(OcrProvider):
    name = "scripted"

    def __init__(self, lines=None, fail=False):
        self.lines, self.fail = lines or [], fail

    def ocr(self, png: bytes) -> OcrResult:
        if self.fail:
            raise OcrError("engine crashed")
        return OcrResult(
            [OcrLine(t, 0.1 + i * 0.05, 0.13 + i * 0.05, 0.1, c) for i, (t, c) in enumerate(self.lines)], self.name
        )


def test_text_pdf_questions_marks_sections_and_subparts():
    ex, seg = _seg(assignment_pdf())
    assert [p.has_text for p in ex.pages] == [True, True]
    qs = {q.number: q for q in seg.questions}
    assert list(qs) == ["1", "2(a)", "2(b)", "3", "5", "6"]
    assert qs["1"].marks == 3 and qs["2(a)"].marks == 2 and qs["2(b)"].marks == 4 and qs["6"].marks == 8
    assert qs["2(a)"].context.startswith("A ball of mass 2 kg")
    assert qs["1"].instructions == "Section A" and qs["6"].instructions == "Section B"
    assert "Instructions: Answer all questions" in seg.preamble
    assert "PHYS 101" not in seg.preamble  # running header removed
    assert qs["6"].pages == [1]


def test_numbering_gap_is_flagged_for_review():
    _, seg = _seg(assignment_pdf())
    q5 = next(q for q in seg.questions if q.number == "5")
    assert q5.needs_review and any("jumps from 3 to 5" in r for r in q5.reasons)


def test_tables_are_kept_as_markdown_and_flagged():
    _, seg = _seg(assignment_pdf())
    q3 = next(q for q in seg.questions if q.number == "3")
    assert "| Time (s) | Distance (m) |" in q3.text and "| 2 | 19.6 |" in q3.text
    assert any("table" in r for r in q3.reasons)


def test_figures_are_attached_to_their_question():
    ex, seg = _seg(assignment_pdf())
    assert len(ex.figures) == 1 and ex.figures[0].png.startswith(b"\x89PNG")
    q5 = next(q for q in seg.questions if q.number == "5")
    assert q5.has_visual and q5.figure_indexes == [0]


def test_math_symbols_are_preserved():
    _, seg = _seg(assignment_pdf())
    q2b = next(q for q in seg.questions if q.number == "2(b)")
    assert "√(2gh)" in q2b.text


def test_marks_syntaxes():
    assert extract_marks("Explain X. (5 marks)") == ("Explain X.", 5.0)
    assert extract_marks("Explain X [10 Marks]") == ("Explain X", 10.0)
    assert extract_marks("Explain X. [4]") == ("Explain X.", 4.0)
    assert extract_marks("Explain X 2.5 marks") == ("Explain X", 2.5)
    assert extract_marks("A mass of 5 kg") == ("A mass of 5 kg", None)


def test_question_label_letter_must_be_attached_lowercase():
    _, seg = _seg(_pdf([["Q1 Explain the evolution of HRIS. [10 Marks]", "Q2 Discuss payroll. [10 Marks]"]]))
    assert [(q.number, q.text) for q in seg.questions] == [
        ("1", "Explain the evolution of HRIS."),
        ("2", "Discuss payroll."),
    ]


def test_repeated_answer_sheet_template_is_not_mistaken_for_questions():
    header = [
        "Assignment No. 2",
        "Instructions:",
        "a. Attempt all questions in your own handwriting.",
        "b. Use the allocated pages only.",
    ]
    data = _pdf([[*header, "", "Q1 Explain HRIS. [10 Marks]"], [*header, "", "Q2 Discuss payroll. [10 Marks]"]])
    _, seg = _seg(data)
    assert [q.number for q in seg.questions] == ["1", "2"]
    assert "Attempt all questions" in seg.preamble
    assert all(q.answer_space and q.answer_space["fraction"] > 0.5 for q in seg.questions)
    assert seg.questions[1].answer_space["page"] == 1


def test_duplicate_numbers_are_flagged():
    _, seg = _seg(_pdf([["1. Define energy.", "2. Define work.", "2. Define power.", "3. Define force."]]))
    assert any("duplicate question number 2" in r for q in seg.questions for r in q.reasons)


def test_unnumbered_document_suggests_paragraphs_for_confirmation():
    _, seg = _seg(plain_paragraphs_pdf())
    assert seg.notes and all(q.needs_review for q in seg.questions)
    included = [q.text for q in seg.questions if not q.excluded]
    assert len(included) == 2 and all("article" in t.lower() or "evidence" in t.lower() for t in included)


def test_multi_page_question_continues_across_pages():
    data = _pdf(
        [
            ["Q1. Describe the process of", "photosynthesis in detail"],
            ["and explain its importance. (6 marks)", "Q2. State Ohm's law. (2 marks)"],
        ]
    )
    _, seg = _seg(data)
    q1 = seg.questions[0]
    assert q1.pages == [0, 1] and q1.marks == 6 and "importance" in q1.text


def test_scanned_page_without_ocr_is_flagged():
    ex, _seg_result = _seg(scanned_pdf(["Q1. What is energy?"]))
    assert ex.pages[0].ocr == "unavailable" and ex.pages[0].confidence == 0
    assert any("type the questions" in i for i in ex.pages[0].issues)


def test_scanned_page_with_ocr_extracts_and_flags_low_confidence():
    ocr = ScriptedOcr([("Q1. What is energy? (2 marks)", 0.92), ("Q2. Define momentum.", 0.55)])
    ex, seg = _seg(scanned_pdf(["x"]), ocr)
    assert ex.pages[0].ocr == "scripted"
    qs = {q.number: q for q in seg.questions}
    assert qs["1"].marks == 2
    assert any("low text confidence" in r for r in qs["2"].reasons)


def test_ocr_failure_is_reported_not_raised():
    ex, _ = _seg(scanned_pdf(["x"]), ScriptedOcr(fail=True))
    assert ex.pages[0].ocr == "failed" and any("OCR failed" in i for i in ex.pages[0].issues)


@pytest.mark.parametrize(
    ("data", "code"),
    [(b"hello world", "not_pdf"), (b"%PDF-1.7\n%garbage\n", "malformed"), (encrypted_pdf(), "encrypted")],
)
def test_bad_pdfs_are_rejected(data, code):
    with pytest.raises(PdfValidationError) as e:
        validate_pdf(data, 40)
    assert e.value.code == code


def test_page_limit_enforced():
    with pytest.raises(PdfValidationError) as e:
        validate_pdf(_pdf([["page"]] * 5), 3)
    assert e.value.code == "too_many_pages"


def test_upload_api_rejects_bad_pdfs_and_spoofed_types(auth_client):
    c = auth_client
    r = c.post("/api/v1/assignments", files={"file": ("a.pdf", encrypted_pdf())})
    assert r.status_code == 422 and r.json()["error"]["code"] == "pdf_encrypted"
    r = c.post("/api/v1/assignments", files={"file": ("a.pdf", b"\x89PNG\r\n\x1a\nnot a pdf")})
    assert r.status_code == 415


def test_upload_size_limit(auth_client, monkeypatch):
    from app import config

    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    config.get_settings.cache_clear()
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10)
    r = auth_client.post("/api/v1/assignments", files={"file": ("big.pdf", big)})
    assert r.status_code == 413


def test_question_editor_operations(auth_client):
    c = auth_client
    aid = c.post("/api/v1/assignments", files={"file": ("p.pdf", assignment_pdf())}).json()["id"]
    run_jobs()
    qs = c.get(f"/api/v1/assignments/{aid}/questions").json()
    ids = [q["id"] for q in qs]
    # merge 2(a)+2(b)
    merged = c.post(f"/api/v1/assignments/{aid}/questions/merge", json={"ids": ids[1:3]}).json()
    assert merged["marks"] == 6 and "Calculate" in merged["text"] and "speed" in merged["text"]
    # split it back
    at = merged["text"].index("Find")
    parts = c.post(f"/api/v1/questions/{merged['id']}/split", json={"at": at, "number": "2(b)"}).json()
    assert parts[1]["text"].startswith("Find") and parts[1]["number"] == "2(b)"
    # add, reorder, exclude, delete
    new = c.post(f"/api/v1/assignments/{aid}/questions", json={"number": "7", "text": "Extra question?"}).json()
    order = [q["id"] for q in c.get(f"/api/v1/assignments/{aid}/questions").json()]
    order.remove(new["id"])
    order.insert(0, new["id"])
    assert c.post(f"/api/v1/assignments/{aid}/questions/reorder", json={"ids": order}).json()[0]["id"] == new["id"]
    assert c.post(f"/api/v1/assignments/{aid}/questions/reorder", json={"ids": order[1:]}).status_code == 422
    assert c.patch(f"/api/v1/questions/{new['id']}", json={"excluded": True}).json()["excluded"] is True
    assert c.delete(f"/api/v1/questions/{new['id']}").status_code == 204
    positions = [q["position"] for q in c.get(f"/api/v1/assignments/{aid}/questions").json()]
    assert positions == list(range(len(positions)))
    # page images are served
    a = c.get(f"/api/v1/assignments/{aid}").json()
    doc = a["documents"][0]
    assert c.get(f"/api/v1/assignments/{aid}/documents/{doc['id']}/pages/1.png").content[:4] == b"\x89PNG"
    assert a["render_settings"]["paper"] in ("ruled", "source")
