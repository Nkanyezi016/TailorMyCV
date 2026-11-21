from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.config import ApiError
from app.schemas import (
    CandidateProfile,
    Draft,
    DraftEdit,
    DraftEntry,
    DraftSection,
    JobDescription,
    MatchKind,
)
from app.services.lodestar.provider import Plan, Provider
from app.services.matching import evidence_fields, match_job, significant_terms

HEADINGS = {
    "experience": "Work Experience",
    "education": "Education",
    "projects": "Projects",
    "technical_skills": "Technical Skills",
    "professional_skills": "Professional Skills",
    "certifications": "Certifications",
    "awards": "Awards",
    "hackathons": "Hackathons",
    "volunteer_experience": "Volunteer Experience",
    "languages": "Languages",
}
SUMMARY_FIELDS = {
    "projects": ("name", "Projects include"),
    "education": ("qualification", "Education includes"),
    "technical_skills": ("name", "Technical skills include"),
    "professional_skills": ("name", "Professional skills include"),
    "certifications": ("name", "Certifications include"),
    "experience": ("job_title", "Work experience includes"),
    "volunteer_experience": ("job_title", "Volunteer experience includes"),
}
FIELD_LABELS = {
    "academic_results": "Academic results",
    "coursework": "Relevant coursework",
    "technologies": "Technologies",
    "employment_type": "Employment type",
    "completion_status": "Completion status",
}
LEADING_FIELDS = ("job_title", "organisation", "qualification", "institution", "name")


def local_plan(entries, matches, preferences) -> Plan:
    terms = {term for preference in preferences for term in significant_terms(preference)}
    relevance = (
        {
            evidence_id: len(
                terms.intersection(
                    word
                    for value in evidence_fields(section, record)
                    for word in significant_terms(value)
                )
            )
            for evidence_id, (section, record) in entries.items()
        }
        if terms
        else dict.fromkeys(entries, 0)
    )
    for match in matches:
        points = {MatchKind.DIRECT: 3, MatchKind.TRANSFERABLE: 1}.get(match.classification, 0)
        for evidence_id in match.evidence_ids:
            relevance[evidence_id] += points
    order = sorted(entries, key=relevance.get, reverse=True)
    summary = [
        evidence_id
        for evidence_id in order
        if entries[evidence_id][0] in SUMMARY_FIELDS
        and getattr(entries[evidence_id][1], SUMMARY_FIELDS[entries[evidence_id][0]][0])
    ][:3]
    return Plan(evidence_order=order, summary_evidence_ids=summary)


def validate_plan(plan: Plan, entries) -> None:
    available = set(entries)
    order = plan.evidence_order
    summary = plan.summary_evidence_ids
    if (
        len(order) != len(available)
        or set(order) != available
        or not set(summary) <= available
        or len(summary) != len(set(summary))
    ):
        raise ApiError(
            502,
            "invalid_evidence",
            "Lodestar selected invalid profile evidence. Submit a new run.",
        )


def summary_text(plan: Plan, entries) -> str | None:
    summary = {}
    for evidence_id in plan.summary_evidence_ids:
        section, record = entries[evidence_id]
        if section in SUMMARY_FIELDS:
            field, prefix = SUMMARY_FIELDS[section]
            value = getattr(record, field)
            if value:
                summary.setdefault(prefix, []).append(value.rstrip("."))
    sentences = []
    length = 0
    for prefix, values in summary.items():
        sentence = f"{prefix} {', '.join(dict.fromkeys(values))}."
        if length + len(sentence) + 1 <= 2000:
            sentences.append(sentence)
            length += len(sentence) + 1
    return " ".join(sentences) or None


def sections_for(order, entries) -> list[DraftSection]:
    sections = {}
    for evidence_id in order:
        section, fields = entries[evidence_id]
        sections.setdefault(section, []).append(DraftEntry(evidence_id=evidence_id, fields=fields))
    return [
        DraftSection(key=key, heading=HEADINGS[key], entries=records)
        for key, records in sections.items()
    ]


def format_record(entry: DraftEntry) -> list[str]:
    fields = entry.fields.model_dump(exclude={"id"}, exclude_none=True)
    heading = " | ".join(fields.pop(key) for key in LEADING_FIELDS if key in fields)
    lines = [heading] if heading else []
    original_dates = fields.pop("source_dates", None)
    start_date = fields.pop("start_date", None)
    end_date = fields.pop("end_date", None) or fields.pop("completion_date", None)
    dates = original_dates or " – ".join(value for value in (start_date, end_date) if value)
    if dates:
        lines.append(dates)
    for key, value in fields.items():
        if not value:
            continue
        if key in FIELD_LABELS:
            value = ", ".join(value) if isinstance(value, list) else value
            lines.append(f"{FIELD_LABELS[key]}: {value}")
        elif isinstance(value, list):
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(value)
    return lines


def with_ats_text(draft: Draft) -> Draft:
    contact = draft.contact.model_dump(exclude={"full_name"}, exclude_none=True)
    header = [draft.contact.full_name, " | ".join(contact.values())]
    parts = ["\n".join(value for value in header if value)]
    if draft.summary:
        parts.append(f"Professional Summary\n{draft.summary}")
    for section in draft.sections:
        entries = ["\n".join(format_record(entry)) for entry in section.entries]
        body = "\n\n".join(entry for entry in entries if entry)
        if body:
            parts.append(f"{section.heading}\n{body}")
    return draft.model_copy(update={"ats_text": "\n\n".join(part for part in parts if part)})


async def build_draft(
    run_id: UUID, profile: CandidateProfile, job: JobDescription | None, provider: Provider | None
) -> Draft:
    entries = {record.id: (section, record) for section, record in profile.entries()}
    matches = await run_in_threadpool(match_job, job, profile) if job else []
    plan = local_plan(entries, matches, profile.career_preferences if job is None else [])
    if provider and entries:
        # Contact information and exact dates are unnecessary for ranking.
        omit = {"start_date", "end_date", "completion_date", "source_dates", "repository_url"}
        plan = await provider.plan(
            {
                "job": job.model_dump(mode="json") if job else None,
                "career_preferences": profile.career_preferences,
                "evidence": [
                    {"section": section, "record": record.model_dump(mode="json", exclude=omit)}
                    for section, record in entries.values()
                ],
                "matches": [match.model_dump(mode="json") for match in matches],
            }
        )
    validate_plan(plan, entries)
    return with_ats_text(
        Draft(
            run_id=run_id,
            title=f"CV for {job.title}"[:500] if job and job.title else "My CV",
            contact=profile.contact,
            summary=summary_text(plan, entries),
            sections=sections_for(plan.evidence_order, entries),
            matches=matches,
            ats_text="",
        )
    )


def edit_draft(draft: Draft, edit: DraftEdit, profile: CandidateProfile) -> Draft:
    updates = edit.model_dump(exclude_unset=True, exclude={"evidence_order"})
    if updates.get("title") is None:
        updates.pop("title", None)
    if edit.evidence_order is not None:
        entries = {record.id: (section, record) for section, record in profile.entries()}
        order = edit.evidence_order
        if not set(order) <= set(entries) or len(order) != len(set(order)):
            raise ApiError(
                422, "invalid_evidence", "Use distinct evidence IDs from the run profile."
            )
        updates["sections"] = sections_for(order, entries)
    return with_ats_text(draft.model_copy(update=updates))
