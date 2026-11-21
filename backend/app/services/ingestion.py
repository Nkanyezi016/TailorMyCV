import re
from io import BytesIO
from pathlib import PurePath
from threading import Lock
from zipfile import BadZipFile, ZipFile

import pymupdf
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import ValidationError

from app.config import ApiError
from app.schemas import CandidateProfile, Education, Experience, ImportResult, ProfileItem, Project

MAX_UPLOAD = 5 * 1024 * 1024
MAX_TEXT = 100_000
PDF_LOCK = Lock()
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
QUALIFICATION_NAME = re.compile(r"^(?:bsc|msc|bachelor|master|diploma|phd|certificate)\b", re.I)
HEADINGS = {
    "contact": "contact",
    "contact details": "contact",
    "education": "education",
    "education & training": "education",
    "academic background": "education",
    "qualifications": "education",
    "work experience": "experience",
    "professional experience": "experience",
    "employment history": "experience",
    "experience": "experience",
    "projects": "projects",
    "personal projects": "projects",
    "skills": "technical_skills",
    "technical skills": "technical_skills",
    "professional skills": "professional_skills",
    "soft skills": "professional_skills",
    "certifications": "certifications",
    "certificates": "certifications",
    "awards": "awards",
    "awards & leadership": "awards",
    "hackathons": "hackathons",
    "volunteer experience": "volunteer_experience",
    "volunteering": "volunteer_experience",
    "languages": "languages",
    "career preferences": "career_preferences",
    "summary": "other",
    "professional summary": "other",
    "references": "other",
}
TITLES = {
    "education": "qualification",
    "experience": "job_title",
    "volunteer_experience": "job_title",
    "projects": "name",
}
RECORD_TYPES = {
    "education": Education,
    "experience": Experience,
    "volunteer_experience": Experience,
    "projects": Project,
}
LABELS = {
    "degree": "qualification",
    "qualification": "qualification",
    "institution": "institution",
    "field of study": "field_of_study",
    "grade": "academic_results",
    "status": "completion_status",
    "coursework": "coursework",
    "job title": "job_title",
    "role": "job_title",
    "organisation": "organisation",
    "organization": "organisation",
    "employer": "organisation",
    "employment type": "employment_type",
    "project": "name",
    "description": "description",
    "start date": "start_date",
    "end date": "end_date",
    "completion date": "completion_date",
    "dates": "source_dates",
    "responsibilities": "responsibilities",
    "achievements": "achievements",
    "contributions": "contributions",
    "outcomes": "outcomes",
    "technologies": "technologies",
    "tools": "technologies",
    "repository": "repository_url",
}
LIST_FIELDS = {
    "coursework",
    "responsibilities",
    "achievements",
    "contributions",
    "outcomes",
    "technologies",
}
CONTACT_FIELDS = {
    "name": "full_name",
    "full name": "full_name",
    "email": "email",
    "phone": "phone",
    "mobile": "phone",
    "location": "location",
    "linkedin": "linkedin_url",
    "github": "github_url",
    "portfolio": "portfolio_url",
}


def pdf_text(data: bytes) -> str:
    with PDF_LOCK, pymupdf.open(stream=data, filetype="pdf") as document:
        if document.needs_pass or document.is_encrypted:
            raise ApiError(
                422, "password_protected", "Remove the PDF password and upload it again."
            )
        if len(document) > 50:
            raise ApiError(422, "too_many_pages", "Upload a CV with 50 pages or fewer.")
        pages = [page.get_text(sort=True) for page in document]
        if not any(page.strip() for page in pages) and any(page.get_images() for page in document):
            raise ApiError(422, "scanned_pdf", "Upload a text-based PDF or the original DOCX.")
        return "\n".join(pages)


def docx_text(data: bytes) -> str:
    with ZipFile(BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(member.file_size for member in members) > 30 * 1024**2:
            raise ApiError(422, "docx_too_complex", "Save a simplified DOCX and upload it again.")
        required = {"[Content_Types].xml", "word/document.xml"}
        if not required.issubset(archive.namelist()):
            raise BadZipFile("Missing DOCX parts")
        if any(member.flag_bits & 1 for member in members):
            raise ApiError(
                422, "password_protected", "Remove the DOCX password and upload it again."
            )
    document = Document(BytesIO(data))
    lines = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            lines.append(item.text)
        elif isinstance(item, Table):
            for row in item.rows:
                lines.extend(cell.text for cell in row.cells)
    return "\n".join(lines)


def import_cv(filename: str, data: bytes) -> ImportResult:
    extension = PurePath(filename).suffix.casefold()
    if extension not in {".pdf", ".docx"}:
        raise ApiError(415, "unsupported_file", "Upload a text-based PDF or DOCX.")
    if not data:
        raise ApiError(422, "empty_document", "Upload a CV with selectable text.")
    if len(data) > MAX_UPLOAD:
        raise ApiError(413, "file_too_large", "Upload a CV no larger than 5 MiB.")
    if extension == ".docx" and data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ApiError(422, "unreadable_document", "Save an unlocked copy in DOCX format.")
    try:
        if extension == ".pdf" and b"%PDF-" in data[:1024]:
            text = pdf_text(data)
        elif extension == ".docx" and data.startswith(b"PK\x03\x04"):
            text = docx_text(data)
        else:
            raise ValueError("File signature does not match its extension")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            422, "unreadable_document", "Open the CV and save a fresh PDF or DOCX, then try again."
        ) from exc
    if not text.strip():
        raise ApiError(
            422,
            "no_selectable_text",
            "Upload a CV with selectable text. For scanned CVs, use the original DOCX.",
        )
    if len(text) > MAX_TEXT:
        raise ApiError(422, "too_much_text", "Upload a shorter copy containing only the CV.")
    return parse_profile(text)


def contact_line(line: str, contact: dict) -> bool:
    label, colon, value = line.partition(":")
    key = CONTACT_FIELDS.get(label.casefold().strip()) if colon else None
    if key and value.strip():
        contact.setdefault(key, value.strip())
        return True
    email = EMAIL.search(line)
    if email:
        contact.setdefault("email", email[0])
        return email[0] == line
    for part, field in (("linkedin.com/", "linkedin_url"), ("github.com/", "github_url")):
        if part in line.casefold() and not any(char.isspace() for char in line):
            contact.setdefault(field, line)
            return True
    if re.fullmatch(r"\+?[\d ().-]{7,25}", line) and sum(ch.isdigit() for ch in line) >= 7:
        contact.setdefault("phone", line)
        return True
    return False


def parse_profile(text: str) -> ImportResult:
    data = {"contact": {}}
    pending = []
    unmapped = []
    section = "contact"
    current = None
    ambiguous = False
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        heading = HEADINGS.get(line.casefold().rstrip(":"))
        if heading:
            section, current, ambiguous = heading, None, False
            continue
        if section == "contact":
            if contact_line(line, data["contact"]):
                continue
            if index == 0 and re.fullmatch(r"[^\W\d_]+(?:[ .’'-]+[^\W\d_]+){1,5}", line):
                data["contact"].setdefault("full_name", line)
            else:
                unmapped.append(line)
            continue
        if section == "other":
            unmapped.append(line)
            continue
        if section == "career_preferences":
            data.setdefault(section, []).append(line)
            continue
        bullet = bool(re.match(r"^[-–•*]\s*", line))
        content = re.sub(r"^[-–•*]\s*", "", line).strip()
        if not content:
            continue
        if section not in TITLES:
            items = re.split(r"[,;|]", content) if section.endswith("skills") else [content]
            for item in items:
                if item.strip():
                    pending.append((section, {"name": item.strip()}, [line]))
            continue

        title_key = TITLES[section]
        label, colon, value = content.partition(":")
        field = LABELS.get(label.casefold().strip()) if colon and value.strip() else None
        allowed = RECORD_TYPES[section].model_fields
        if field not in allowed or field == "id":
            field = None
        parts = re.split(r"\s+[—–|]\s+", content, maxsplit=1)
        plain_title = not field and not colon and not bullet and not YEAR.search(content)
        explicit_title = len(parts) == 2 or (
            section == "education" and QUALIFICATION_NAME.search(content)
        )
        if field == title_key or (
            plain_title and ((current is None and not ambiguous) or explicit_title)
        ):
            record = {title_key: value.strip() if field else (parts[0] if parts else content)}
            if not field and len(parts) == 2:
                secondary = {
                    "education": "institution",
                    "projects": "description",
                }.get(section, "organisation")
                record[secondary] = parts[1]
            quotes = [line]
            current, ambiguous = (record, quotes), False
            pending.append((section, record, quotes))
            continue
        if current is None and field and not ambiguous:
            current = ({}, [])
            pending.append((section, *current))
        if current is None:
            unmapped.append(line)
            continue
        record, quotes = current
        if (
            not field
            and YEAR.search(content)
            and re.fullmatch(r"[\wÀ-ž\s./–—-]+", content)
            and len(content.split()) <= 6
            and "source_dates" not in record
        ):
            field, value = "source_dates", content
        if not field and bullet:
            field = {
                "education": "achievements",
                "projects": "contributions",
            }.get(section, "responsibilities")
            value = content
        if field in LIST_FIELDS:
            values = value.split(",") if field == "technologies" else [value]
            record.setdefault(field, []).extend(item.strip() for item in values if item.strip())
        elif field and field not in record:
            record[field] = value.strip()
        else:
            unmapped.append(line)
            if not field and not bullet:
                current, ambiguous = None, True
            continue
        quotes.append(line)

    sources = {}
    seen = set()
    for key, record, quotes in pending:
        schema = RECORD_TYPES.get(key, ProfileItem)
        try:
            parsed = schema.model_validate(record)
        except ValidationError:
            unmapped.extend(quotes)
            continue
        fingerprint = (key, parsed.model_dump_json(exclude={"id"}).casefold())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        items = data.setdefault(key, [])
        limit = next(
            item.max_length
            for item in CandidateProfile.model_fields[key].metadata
            if hasattr(item, "max_length")
        )
        if len(items) >= limit:
            unmapped.extend(quotes)
            continue
        items.append(parsed)
        sources[parsed.id] = quotes
    for key, value in list(data["contact"].items()):
        if len(value) > 500:
            unmapped.append(value)
            del data["contact"][key]
    if "career_preferences" in data:
        preferences = data["career_preferences"]
        data["career_preferences"] = [value for value in preferences[:40] if len(value) <= 2000]
        unmapped.extend(value for value in preferences[:40] if len(value) > 2000)
        unmapped.extend(preferences[40:])
    return ImportResult(
        profile=CandidateProfile.model_validate(data),
        source_quotes=sources,
        unmapped_text=unmapped,
        warnings=["Check extracted names, dates and sections. Add any details that were missed."],
    )
