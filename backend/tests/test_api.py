from io import BytesIO
from time import monotonic, sleep
from uuid import uuid4

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.lodestar.provider import Plan

PREFIX = "/api/v1"
CV_LINES = [
    "Avery Example",
    "avery@example.test",
    "Education",
    "BSc Mathematical Sciences — Example University",
    "2020 – 2023",
    "Projects",
    "Project: River Analytics",
    "Technologies: Python, PostgreSQL",
    "Contributions: Built a data validation pipeline.",
    "Technical Skills",
    "SQL, Python, SQL",
]
JOB = {
    "title": "Junior Data Engineer",
    "company": "Example Company",
    "description": (
        "Required skills:\n- Python\n- SQL\n- Microsoft SQL Server\n- Power BI\n"
        "- 3 years of professional Python experience\n"
        "Responsibilities:\n- Build data pipelines\n"
        "Preferred skills:\n- Azure\n"
        "Competencies:\n- Teamwork\n"
        "Benefits:\n- Learn Python on the job"
    ),
}


@pytest.fixture
def client():
    with TestClient(create_app(Settings(provider="local"))) as client:
        yield client


def pdf(lines=CV_LINES, *, password=False, image_only=False):
    with pymupdf.open() as doc:
        page = doc.new_page()
        if image_only:
            image = b"P6\n2 2\n255\n" + b"\xff\xff\xff" * 4
            page.insert_image(pymupdf.Rect(20, 20, 50, 50), stream=image)
        else:
            writer = pymupdf.TextWriter(page.rect)
            font = pymupdf.Font("helv")
            for index, line in enumerate(lines):
                writer.append((25, 35 + index * 16), line, font=font)
            writer.write_text(page)
        options = (
            {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "example"} if password else {}
        )
        return doc.tobytes(**options)


def docx(lines=CV_LINES):
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def submit(client, profile, job=None):
    response = client.post(PREFIX + "/profiles", json=profile)
    assert response.status_code == 201, response.text
    profile_id = response.json()["id"]
    payload = {"profile_id": profile_id}
    if job is not None:
        response = client.post(PREFIX + "/jobs", json=job)
        assert response.status_code == 201, response.text
        payload["job_id"] = response.json()["id"]
    response = client.post(PREFIX + "/runs", json=payload)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    return response.json()["id"], profile_id


def wait_for_terminal(client, run_id):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        response = client.get(f"{PREFIX}/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"completed", "failed"}:
            return run
        sleep(0.01)
    pytest.fail("Run did not reach a terminal state")


@pytest.mark.parametrize("extension, make_cv", [("pdf", pdf), ("docx", docx)])
def test_import_review_job_and_draft(client, extension, make_cv):
    response = client.post(PREFIX + "/imports", files={"file": (f"example.{extension}", make_cv())})
    assert response.status_code == 201, response.text
    result = response.json()
    profile = result["profile"]
    assert result["review_required"] is True
    assert "id" not in result
    assert profile["contact"]["full_name"] == "Avery Example"
    assert len(profile["technical_skills"]) == 2
    original = profile["education"][0]
    assert original["qualification"] == "BSc Mathematical Sciences"
    assert original["source_dates"] == "2020 – 2023"
    assert original["id"] in result["source_quotes"]

    profile["contact"]["location"] = "Example City"
    run_id, profile_id = submit(client, profile, JOB)
    run = wait_for_terminal(client, run_id)
    assert run["status"] == "completed", run
    job = client.get(f"{PREFIX}/jobs/{run['job_id']}").json()
    assert job["description"] == JOB["description"]
    assert job["seniority_level"] == "junior"
    assert job["required_technical_skills"][:2] == ["Python", "SQL"]
    assert job["professional_competencies"] == ["Teamwork"]
    endpoint = f"{PREFIX}/drafts/{run['draft_id']}"
    draft_response = client.get(endpoint)
    assert draft_response.headers["cache-control"] == "no-store"
    draft = draft_response.json()
    matches = {item["requirement"]: item["classification"] for item in draft["matches"]}
    assert matches["Python"] == "DIRECT_MATCH"
    assert matches["SQL"] == "DIRECT_MATCH"
    assert matches["Microsoft SQL Server"] == "TRANSFERABLE_MATCH"
    assert matches["Power BI"] == "MISSING"
    assert matches["3 years of professional Python experience"] == "NEEDS_CLARIFICATION"
    assert "Microsoft SQL Server" not in draft["ats_text"]
    assert "Example City" in draft["ats_text"]
    assert original["qualification"] in draft["ats_text"]
    assert original["source_dates"] in draft["ats_text"]

    profile["education"][0]["qualification"] = "Updated source"
    assert client.put(f"{PREFIX}/profiles/{profile_id}", json=profile).status_code == 200
    edited = client.patch(
        endpoint,
        json={
            "title": "Edited CV",
            "summary": None,
            "evidence_order": [original["id"]],
        },
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Edited CV"
    assert original["source_dates"] in edited.json()["ats_text"]
    assert "Updated source" not in edited.json()["ats_text"]
    assert "Professional Summary" not in edited.json()["ats_text"]
    assert len(edited.json()["sections"]) == 1
    assert client.patch(endpoint, json={"evidence_order": [str(uuid4())]}).status_code == 422


def test_manual_entry_without_job(client):
    run_id, _ = submit(
        client,
        {
            "contact": {"full_name": "Avery Example"},
            "experience": [
                {
                    "job_title": "Tutor",
                    "organisation": "Example University",
                    "start_date": "July 2022",
                    "end_date": "Nov 2023",
                    "responsibilities": ["Tutored first-year students."],
                }
            ],
            "education": [
                {"qualification": "Certificate in Computing", "completion_status": "Ongoing"}
            ],
        },
    )
    run = wait_for_terminal(client, run_id)
    assert run["status"] == "completed"
    draft = client.get(f"{PREFIX}/drafts/{run['draft_id']}").json()
    assert draft["matches"] == []
    assert "July 2022 – Nov 2023" in draft["ats_text"]
    assert "Certificate in Computing" in draft["ats_text"]
    assert "Ongoing" in draft["ats_text"]


@pytest.mark.parametrize(
    "extension, data, status, code",
    [
        ("pdf", b"", 422, "empty_document"),
        ("pdf", b"%PDF-1.7\nbroken", 422, "unreadable_document"),
        ("docx", b"PK\x03\x04broken", 422, "unreadable_document"),
        ("txt", b"Plain text", 415, "unsupported_file"),
        ("pdf", pdf(password=True), 422, "password_protected"),
        ("pdf", pdf(image_only=True), 422, "scanned_pdf"),
        ("docx", docx([]), 422, "no_selectable_text"),
        ("pdf", b"x" * (5 * 1024**2 + 1), 413, "file_too_large"),
    ],
)
def test_unreadable_imports(client, extension, data, status, code):
    response = client.post(PREFIX + "/imports", files={"file": (f"test.{extension}", data)})
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_validation_and_workspace_token():
    settings = Settings(provider="local", api_token="example-token")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.post(PREFIX + "/profiles", json={}).status_code == 401
        client.headers["Authorization"] = "Bearer example-token"
        response = client.post(PREFIX + "/profiles", json={"contact": {"phone": ["sensitive"]}})
        assert response.status_code == 422
        assert "sensitive" not in response.text
        assert response.json()["error"]["code"] == "validation_error"


class InvalidProvider:
    async def plan(self, context):
        assert "contact" not in context
        return Plan(evidence_order=[uuid4()], summary_evidence_ids=[])


def test_invalid_model_plan_creates_no_draft():
    with TestClient(create_app(Settings(provider="local"), provider=InvalidProvider())) as client:
        run_id, _ = submit(client, {"technical_skills": [{"name": "Python"}]})
        run = wait_for_terminal(client, run_id)
        assert run["status"] == "failed"
        assert run["error"]["code"] == "invalid_evidence"
        assert run["draft_id"] is None


def test_concurrent_runs_respect_worker_limit():
    import asyncio
    from threading import Event

    started = Event()
    release = Event()

    class HoldingProvider:
        async def plan(self, context):
            started.set()
            assert await asyncio.to_thread(release.wait, 3)
            order = [item["record"]["id"] for item in context["evidence"]]
            return Plan(evidence_order=order, summary_evidence_ids=order[:1])

    settings = Settings(provider="local", max_workers=1, max_pending=1)
    with TestClient(create_app(settings, provider=HoldingProvider())) as client:
        first_id, profile_id = submit(client, {"technical_skills": [{"name": "Python"}]})
        try:
            assert started.wait(2)
            second = client.post(PREFIX + "/runs", json={"profile_id": profile_id}).json()
            assert client.get(f"{PREFIX}/runs/{first_id}").json()["status"] == "processing"
            assert client.get(f"{PREFIX}/runs/{second['id']}").json()["status"] == "queued"
            saturated = client.post(PREFIX + "/runs", json={"profile_id": profile_id})
            assert saturated.status_code == 429
        finally:
            release.set()
        first = wait_for_terminal(client, first_id)
        second = wait_for_terminal(client, second["id"])
        assert first["status"] == second["status"] == "completed"
        assert first["draft_id"] != second["draft_id"]
