import io

from PIL import Image, ImageDraw

from tests.conftest import run_jobs
from tests.pdfs import assignment_pdf


def _signature_png() -> bytes:
    img = Image.new("L", (600, 200), 255)
    ImageDraw.Draw(img).line([(40, 150), (160, 50), (260, 140), (380, 60), (540, 130)], fill=20, width=9)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_pages_are_signed_automatically_once_a_signature_exists(auth_client):
    c = auth_client
    aid = c.post("/api/v1/assignments", files={"file": ("p.pdf", assignment_pdf())}).json()["id"]
    run_jobs()
    assert c.get(f"/api/v1/assignments/{aid}").json()["render_settings"]["sign_fields"] is False

    r = c.post("/api/v1/signature", files={"file": ("sig.png", _signature_png(), "image/png")})
    assert r.status_code in (200, 201), r.text
    assert c.get(f"/api/v1/assignments/{aid}").json()["render_settings"]["sign_fields"] is True

    # an explicit choice for the assignment wins over the automatic default
    c.put(f"/api/v1/assignments/{aid}/render-config", json={"sign_fields": False})
    assert c.get(f"/api/v1/assignments/{aid}").json()["render_settings"]["sign_fields"] is False
