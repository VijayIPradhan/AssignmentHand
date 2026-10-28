"""Complete workflow through the HTTP API: samples -> profile -> PDF -> questions -> answers ->
approval -> preview -> final PDF download."""

import io

from pypdf import PdfReader

from tests.conftest import run_jobs
from tests.pdfs import assignment_pdf
from tests.synth import degrade, filled_template, to_jpeg, to_png


def _profile(c) -> str:
    pid = c.post("/api/v1/profiles", json={"name": "My writing"}).json()["id"]
    for page, img in (
        (0, degrade(filled_template(0), rotate=4, shadow=True, noise=4, seed=2)),
        (1, filled_template(1)),
    ):
        data = to_jpeg(img) if page == 0 else to_png(img)
        r = c.post(
            f"/api/v1/profiles/{pid}/samples", files={"file": (f"sheet{page}.jpg", data)}, data={"kind": "template"}
        )
        assert r.status_code == 201, r.text
    assert c.post(f"/api/v1/profiles/{pid}/process").status_code == 202
    run_jobs()
    p = c.get(f"/api/v1/profiles/{pid}").json()
    assert p["status"] == "ready", p["quality_report"]
    assert p["metrics"]["glyph_count"] > 150
    return pid


def test_full_workflow(auth_client, fake_llm):
    c = auth_client
    pid = _profile(c)

    r = c.post(f"/api/v1/profiles/{pid}/preview", json={"text": "Quick test of my handwriting 123."})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"

    r = c.post("/api/v1/assignments", files={"file": ("physics.pdf", assignment_pdf())}, data={"subject": "Physics"})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    run_jobs()
    a = c.get(f"/api/v1/assignments/{aid}").json()
    assert a["status"] == "awaiting_review"
    numbers = [q["number"] for q in a["questions"]]
    assert numbers == ["1", "2(a)", "2(b)", "3", "5", "6"]

    # Correct the numbering gap and confirm
    q5 = next(q for q in a["questions"] if q["number"] == "5")
    assert c.patch(f"/api/v1/questions/{q5['id']}", json={"number": "4", "reviewed": True}).status_code == 200
    # Generating before confirming is refused
    assert c.post(f"/api/v1/assignments/{aid}/generate", json={}).status_code == 409
    assert c.post(f"/api/v1/assignments/{aid}/confirm-extraction").status_code == 200

    fake_llm.responses = [
        {"answer": f"Answer number {i} with $E = mc^2$ and a short explanation.", "flags": [], "confidence": "high"}
        for i in range(6)
    ]
    assert c.post(f"/api/v1/assignments/{aid}/generate", json={}).status_code == 202
    run_jobs()
    qs = c.get(f"/api/v1/assignments/{aid}/questions").json()
    assert all(q["answer"]["status"] == "generated" for q in qs)
    # The prompt carries the question as tagged, untrusted content
    assert "<question>" in fake_llm.calls[0]["user"]

    # Final export requires approval
    c.patch(f"/api/v1/assignments/{aid}", json={"profile_id": pid})
    assert c.post(f"/api/v1/assignments/{aid}/render", json={"preview": False}).status_code == 409

    # Edit one answer, approve all, preview, then final
    first = qs[0]
    r = c.put(
        f"/api/v1/questions/{first['id']}/answer", json={"content": "Kinetic energy is $\\frac{1}{2}mv^2$, in joules."}
    )
    assert r.json()["status"] == "edited"
    assert len(c.post(f"/api/v1/assignments/{aid}/answers/approve-all").json()) == 6

    c.put(f"/api/v1/assignments/{aid}/render-config", json={"paper": "ruled", "page_numbers": True})
    assert c.post(f"/api/v1/assignments/{aid}/render", json={"preview": True}).status_code == 202
    run_jobs()
    assert c.post(f"/api/v1/assignments/{aid}/render", json={"preview": False}).status_code == 202
    run_jobs()
    a = c.get(f"/api/v1/assignments/{aid}").json()
    assert a["status"] == "completed"
    final = [r for r in a["rendered"] if not r["is_preview"]][-1]
    assert final["page_count"] >= 1
    page = c.get(f"/api/v1/rendered/{final['id']}/pages/1.png")
    assert page.status_code == 200 and page.content[:4] == b"\x89PNG"
    pdf = c.get(f"/api/v1/rendered/{final['id']}/download")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf.content))
    assert len(reader.pages) == final["page_count"]
    assert "attachment" in pdf.headers["content-disposition"]
    assert pdf.headers["content-disposition"].endswith('_H.pdf"')  # no " - handwritten" suffix

    # Typed export and review copy
    for kind in ("digital", "review"):
        assert c.post(f"/api/v1/assignments/{aid}/export", json={"kind": kind}).status_code == 202
    run_jobs()
    kinds = {r["kind"] for r in c.get(f"/api/v1/assignments/{aid}").json()["rendered"]}
    assert {"handwritten", "digital", "review"} <= kinds

    # Dashboard reflects the work
    d = c.get("/api/v1/dashboard").json()
    assert d["assignments"][0]["id"] == aid and d["profiles"][0]["id"] == pid and d["exports"]
