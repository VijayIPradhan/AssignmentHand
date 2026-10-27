"""Writing on the original answer sheet: space-aware answer length and the fit-check loop."""

import io

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.render.fonts import fallback_font
from tests.conftest import run_jobs
from tests.synth import filled_template, to_png


def _answer_sheet_pdf() -> bytes:
    """One question per page with the rest of the page left blank for the answer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for n in (1, 2):
        c.setFont(fallback_font(), 11)
        c.drawString(60, 800, "Assignment 1 - answer in the space provided")
        c.drawString(60, 760, f"Q{n} Explain one idea in detail. [5 Marks]")
        c.drawString(60, 330, "Signature of the Student")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_long_answer_is_regenerated_to_fit_the_space(auth_client, fake_llm):
    c = auth_client
    pid = c.post("/api/v1/profiles", json={"name": "p"}).json()["id"]
    for page in (0, 1):
        c.post(f"/api/v1/profiles/{pid}/samples", files={"file": ("s.png", to_png(filled_template(page)))})
    c.post(f"/api/v1/profiles/{pid}/process")
    aid = c.post("/api/v1/assignments", files={"file": ("sheet.pdf", _answer_sheet_pdf())}).json()["id"]
    run_jobs()
    a = c.get(f"/api/v1/assignments/{aid}").json()
    assert a["render_settings"]["paper"] == "source"  # answer space detected and suggested
    q1 = a["questions"][0]
    c.patch(f"/api/v1/assignments/{aid}", json={"profile_id": pid})
    c.post(f"/api/v1/assignments/{aid}/confirm-extraction")

    caps = c.get(f"/api/v1/assignments/{aid}/estimate").json()["space_limited_words"]
    assert set(caps) == {q["id"] for q in a["questions"]} and all(20 < v < 400 for v in caps.values())

    long = " ".join(["Energy changes form but the total stays constant."] * 120)
    fake_llm.responses = [
        {"answer": long, "flags": [], "confidence": "high"},
        {"answer": "A short answer that fits easily.", "flags": [], "confidence": "high"},
    ]
    c.post(f"/api/v1/assignments/{aid}/generate", json={"question_ids": [q1["id"]]})
    run_jobs()
    ans = c.get(f"/api/v1/assignments/{aid}/questions").json()[0]["answer"]
    assert ans["content"] == "A short answer that fits easily."
    assert len(fake_llm.calls) == 2 and "Hard limit" in fake_llm.calls[1]["user"]
    assert ans["input_tokens"] == 200  # both attempts are accounted for

    c.put(f"/api/v1/questions/{a['questions'][1]['id']}/answer", json={"content": "Second answer."})
    c.post(f"/api/v1/assignments/{aid}/answers/approve-all")
    c.post(f"/api/v1/assignments/{aid}/render", json={"preview": False})
    run_jobs()
    final = [r for r in c.get(f"/api/v1/assignments/{aid}").json()["rendered"] if not r["is_preview"]][-1]
    assert final["page_count"] == 2  # same pages as the source; nothing spilled over
    assert not [w for w in final["warnings"] if w["type"] == "exceeds_space"]
    pdf = c.get(f"/api/v1/rendered/{final['id']}/download").content
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Explain one idea in detail" in text  # the original page content is preserved underneath
