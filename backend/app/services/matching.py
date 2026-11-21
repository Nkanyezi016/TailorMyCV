import re
from functools import lru_cache

from app.config import ApiError
from app.schemas import (
    CandidateProfile,
    JobDescription,
    JobInput,
    Match,
    MatchKind,
    Requirement,
    RequirementKind,
)

HEADINGS = {
    "requirements": RequirementKind.REQUIRED,
    "minimum requirements": RequirementKind.REQUIRED,
    "required qualifications": RequirementKind.REQUIRED,
    "qualifications": RequirementKind.REQUIRED,
    "required skills": RequirementKind.REQUIRED,
    "what you'll need": RequirementKind.REQUIRED,
    "preferred qualifications": RequirementKind.PREFERRED,
    "preferred skills": RequirementKind.PREFERRED,
    "nice to have": RequirementKind.PREFERRED,
    "responsibilities": RequirementKind.RESPONSIBILITY,
    "key responsibilities": RequirementKind.RESPONSIBILITY,
    "what you'll do": RequirementKind.RESPONSIBILITY,
    "competencies": RequirementKind.COMPETENCY,
    "soft skills": RequirementKind.COMPETENCY,
}
OTHER_HEADINGS = {"benefits", "about us", "about the role", "how to apply", "what we offer"}
QUALIFICATION = re.compile(
    r"\b(?:degree|bachelor|bsc|ba|beng|master|msc|diploma|phd|certification|certified|matric)\b",
    re.I,
)
DURATION = re.compile(
    r"\b(?:\d+\+?|one|two|three|four|five|six|several)\s+(?:years?|months?)\b", re.I
)
SQL_SERVER = re.compile(r"\bSQL Server\b", re.I)
WORK_CONTEXT = re.compile(r"\b(?:professional|commercial|production|industry)\b", re.I)
COMPETENCY = re.compile(r"\b(?:communication|teamwork|collaboration|attention to detail)\b", re.I)
SENIORITY = re.compile(r"\b(?:graduate|intern|junior|mid-level|senior|lead|principal)\b", re.I)
GLOSSARY = (
    "Python",
    "SQL",
    "PostgreSQL",
    "MySQL",
    "SQLite",
    "SQL Server",
    "T-SQL",
    "C++",
    "C#",
    "Java",
    "JavaScript",
    "TypeScript",
    "R",
    "Pandas",
    "Spark",
    "Airflow",
    "dbt",
    "Docker",
    "Kubernetes",
    "Power BI",
    "Tableau",
    "Excel",
    "AWS",
    "Azure",
    "GCP",
    "ETL",
    "ELT",
    "data pipelines",
    "relational databases",
    "data modelling",
    "data modeling",
    "machine learning",
    "statistics",
    "React",
    "FastAPI",
    "Git",
    "CI/CD",
    "REST",
    "Linux",
    "Snowflake",
    "BigQuery",
)
FAMILIES = (
    {
        "postgresql",
        "mysql",
        "sqlite",
        "sql server",
        "snowflake",
        "bigquery",
        "relational databases",
    },
    {"aws", "azure", "gcp"},
    {"power bi", "tableau"},
)
STOP_WORDS = set(
    "a an and or in of at on to for with ability knowledge familiarity experience "
    "required preferred minimum strong demonstrated hands using use be must have "
    "skill skills technical working work equivalent plus per as years year "
    "excellent proficiency proficient practical related background relevant".split()
)
NEGATION = re.compile(r"\b(?:no|not|without|never|unfamiliar|basic exposure)\b", re.I)


@lru_cache(maxsize=512)
def term_pattern(term: str):
    return re.compile(rf"(?<![\w+#-]){re.escape(term)}(?![\w+#-])", flags=re.I)


def has_term(text: str, term: str) -> bool:
    # Treat C++, C#, SQL, SQL Server and similar names as distinct tokens.
    return bool(term_pattern(term.casefold()).search(text))


def technology_terms(text: str) -> list[str]:
    return [term for term in GLOSSARY if covered(term, text)]


def analyse_job(job: JobInput) -> JobDescription:
    requirements = []
    seen = set()
    current = None
    skip = False
    for line in job.description.splitlines():
        line = re.sub(r"^\s*(?:[-*•–]+|\d+[.)])\s*", "", line).strip()
        if not line:
            continue
        label, colon, value = line.partition(":")
        heading = label.strip().casefold().replace("’", "'")
        if heading in HEADINGS:
            current = HEADINGS[heading]
            skip = False
            if not value.strip():
                continue
            line = value.strip()
        elif heading in OTHER_HEADINGS and (colon or heading == line.casefold()):
            current, skip = None, True
            continue
        if skip:
            continue
        if current and not colon:
            candidates = [line]
        else:
            candidates = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
        for candidate in candidates:
            if len(candidate) > 2000:
                raise ApiError(
                    422, "job_line_too_long", "Split long job requirements into shorter lines."
                )
            kind = current
            if re.search(
                r"\b(?:preferred|desirable|advantageous|nice to have|a plus)\b", candidate, re.I
            ):
                kind = RequirementKind.PREFERRED
            elif kind is None and COMPETENCY.search(candidate):
                kind = RequirementKind.COMPETENCY
            elif kind is None and re.match(
                r"^(?:build|design|develop|maintain|manage|support|analyse|analyze|monitor|lead|"
                r"collaborate|deliver|implement|create|own|ensure|write)\b",
                candidate,
                re.I,
            ):
                kind = RequirementKind.RESPONSIBILITY
            elif kind is None and (
                QUALIFICATION.search(candidate)
                or DURATION.search(candidate)
                or re.search(r"\b(?:required|must|proficiency|experience with)\b", candidate, re.I)
                or (len(candidate.split()) <= 8 and technology_terms(candidate))
            ):
                kind = RequirementKind.REQUIRED
            if kind and (kind, candidate.casefold()) not in seen:
                if len(requirements) >= 150:
                    raise ApiError(422, "too_many_requirements", "Submit up to 150 requirements.")
                requirements.append(Requirement(kind=kind, description=candidate))
                seen.add((kind, candidate.casefold()))
    seniority = SENIORITY.search(job.title or "") or SENIORITY.search(job.description)
    return JobDescription(
        **job.model_dump(),
        requirements=requirements,
        required_qualifications=[
            item.description
            for item in requirements
            if item.kind == RequirementKind.REQUIRED and QUALIFICATION.search(item.description)
        ],
        required_technical_skills=[
            item.description
            for item in requirements
            if item.kind == RequirementKind.REQUIRED and technology_terms(item.description)
        ],
        preferred_technical_skills=[
            item.description
            for item in requirements
            if item.kind == RequirementKind.PREFERRED and technology_terms(item.description)
        ],
        responsibilities=[
            item.description for item in requirements if item.kind == RequirementKind.RESPONSIBILITY
        ],
        professional_competencies=[
            item.description for item in requirements if item.kind == RequirementKind.COMPETENCY
        ],
        experience_requirements=[
            item.description
            for item in requirements
            if DURATION.search(item.description)
            or re.search(r"\bexperience\b", item.description, re.I)
        ],
        industry_terminology=technology_terms(job.description),
        seniority_level=seniority[0].casefold() if seniority else None,
    )


EVIDENCE_FIELDS = {
    "education": ("qualification", "field_of_study", "coursework", "achievements"),
    "experience": ("job_title", "technologies", "responsibilities", "achievements"),
    "projects": ("name", "technologies", "description", "contributions", "outcomes"),
    "volunteer_experience": ("job_title", "technologies", "responsibilities", "achievements"),
}


def evidence_fields(section, record):
    for field in EVIDENCE_FIELDS.get(section, ("name", "details")):
        value = getattr(record, field)
        for text in value if isinstance(value, list) else [value]:
            if text and not NEGATION.search(text):
                yield text


def significant_terms(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[\w+#-]+", text.casefold())
        if word not in STOP_WORDS and (not word.isdigit())
    ]


def covered(term: str, text: str) -> bool:
    if term.casefold() == "sql":
        text = SQL_SERVER.sub("", text)
    return has_term(text, term)


def score_requirement(requirement: Requirement, entries) -> Match:
    text = requirement.description
    terms = technology_terms(text)
    qualification = bool(QUALIFICATION.search(text))
    professional = bool(WORK_CONTEXT.search(text))
    duration = bool(DURATION.search(text))
    is_compound = not re.search(r"\bor\b", text, re.I)
    words = significant_terms(text)
    relevant = [
        (section, record.id, fields)
        for section, record, fields in entries
        if not qualification or section in {"education", "certifications"}
    ]
    hits = []
    partial_hits = []
    for section, record_id, fields in relevant:
        if professional and section != "experience":
            if any(covered(term, field) for term in (terms or words) for field in fields):
                partial_hits.append(record_id)
            continue
        if terms:
            found = [term for term in terms if any(covered(term, field) for field in fields)]
            if found:
                hits.append((record_id, found))
        elif words and any(all(covered(word, field) for word in words) for field in fields):
            hits.append((record_id, []))
        elif words and any(covered(word, field) for word in words for field in fields):
            partial_hits.append(record_id)

    def result(kind, ids, explanation):
        return Match(
            requirement_id=requirement.id,
            requirement=text,
            classification=kind,
            evidence_ids=list(dict.fromkeys(ids)),
            explanation=explanation,
        )

    hit_ids = [record_id for record_id, _ in hits]
    if duration and (hit_ids or partial_hits):
        return result(
            MatchKind.CLARIFY,
            hit_ids + partial_hits,
            "Related evidence is present. Confirm the duration and context of the experience.",
        )
    if terms and hit_ids:
        combined = {term for _, found in hits for term in found}
        remainder = [
            word
            for word in words
            if not any(has_term(term, word) for term in terms)
            and not (professional and WORK_CONTEXT.fullmatch(word))
            and not (word == "microsoft" and "SQL Server" in terms)
        ]
        complete = len(combined) == len(terms) or (
            not is_compound and len(terms) == 2 and not re.search(r"\band\b|[,;/]", text, re.I)
        )
        remaining_supported = not remainder or any(
            all(covered(word, field) for word in remainder)
            for _, record_id, fields in relevant
            if record_id in hit_ids
            for field in fields
        )
        if complete and remaining_supported and not qualification and not duration:
            return result(
                MatchKind.DIRECT, hit_ids, "The profile explicitly names the required skills."
            )
        return result(
            MatchKind.CLARIFY,
            hit_ids + partial_hits,
            "The profile covers part of this requirement. Confirm the remaining details.",
        )
    if hit_ids and not duration:
        return result(MatchKind.DIRECT, hit_ids, "The profile contains the stated requirement.")
    related = []
    alternatives = set()
    for term in map(str.casefold, terms):
        for family in FAMILIES:
            if term in family:
                alternatives.update(family - {term})
    for _, record_id, fields in relevant:
        if any(covered(item, field) for item in alternatives for field in fields):
            related.append(record_id)
    if related or (professional and partial_hits):
        return result(
            MatchKind.TRANSFERABLE,
            related + partial_hits,
            "The profile shows related evidence. The specific requirement still needs support.",
        )
    if partial_hits:
        return result(
            MatchKind.CLARIFY,
            partial_hits,
            "Some wording overlaps. Confirm whether the evidence meets this requirement.",
        )
    return result(
        MatchKind.MISSING, [], "No supporting evidence was found in the confirmed profile."
    )


def match_job(job: JobDescription, profile: CandidateProfile) -> list[Match]:
    entries = [
        (section, record, tuple(evidence_fields(section, record)))
        for section, record in profile.entries()
    ]
    return [score_requirement(requirement, entries) for requirement in job.requirements]
