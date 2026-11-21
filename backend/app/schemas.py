from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Details = Annotated[list[Text], Field(max_length=40)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Record(Schema):
    id: UUID = Field(default_factory=uuid4)


class Contact(Schema):
    full_name: Label | None = None
    email: Label | None = None
    phone: Label | None = None
    location: Label | None = None
    linkedin_url: Label | None = None
    github_url: Label | None = None
    portfolio_url: Label | None = None


class Education(Record):
    qualification: Label | None = None
    institution: Label | None = None
    field_of_study: Label | None = None
    start_date: Label | None = None
    completion_date: Label | None = None
    source_dates: Label | None = None
    completion_status: Label | None = None
    academic_results: Label | None = None
    coursework: Details = Field(default_factory=list)
    achievements: Details = Field(default_factory=list)


class Experience(Record):
    organisation: Label | None = None
    job_title: Label | None = None
    employment_type: Label | None = None
    start_date: Label | None = None
    end_date: Label | None = None
    source_dates: Label | None = None
    responsibilities: Details = Field(default_factory=list)
    achievements: Details = Field(default_factory=list)
    technologies: Details = Field(default_factory=list)


class Project(Record):
    name: Label | None = None
    description: Text | None = None
    contributions: Details = Field(default_factory=list)
    technologies: Details = Field(default_factory=list)
    outcomes: Details = Field(default_factory=list)
    repository_url: Label | None = None
    start_date: Label | None = None
    completion_date: Label | None = None
    source_dates: Label | None = None


class ProfileItem(Record):
    name: Label
    details: Details = Field(default_factory=list)


class CandidateProfile(Schema):
    contact: Contact = Field(default_factory=Contact)
    education: list[Education] = Field(default_factory=list, max_length=25)
    experience: list[Experience] = Field(default_factory=list, max_length=25)
    projects: list[Project] = Field(default_factory=list, max_length=25)
    technical_skills: list[ProfileItem] = Field(default_factory=list, max_length=80)
    professional_skills: list[ProfileItem] = Field(default_factory=list, max_length=40)
    certifications: list[ProfileItem] = Field(default_factory=list, max_length=40)
    awards: list[ProfileItem] = Field(default_factory=list, max_length=30)
    hackathons: list[ProfileItem] = Field(default_factory=list, max_length=30)
    volunteer_experience: list[Experience] = Field(default_factory=list, max_length=25)
    languages: list[ProfileItem] = Field(default_factory=list, max_length=30)
    career_preferences: Details = Field(default_factory=list)

    def entries(self):
        for field in type(self).model_fields:
            if field not in {"contact", "career_preferences"}:
                for record in getattr(self, field):
                    yield field, record

    @model_validator(mode="after")
    def unique_record_ids(self):
        ids = [record.id for _, record in self.entries()]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate record IDs must be unique within a profile.")
        return self


class ImportResult(Schema):
    review_required: bool = True
    profile: CandidateProfile
    source_quotes: dict[UUID, list[str]]
    unmapped_text: list[str]
    warnings: list[str]


class ProfileResult(Record):
    profile: CandidateProfile


class RequirementKind(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    RESPONSIBILITY = "responsibility"
    COMPETENCY = "professional_competency"


class Requirement(Record):
    description: Text
    kind: RequirementKind


class JobInput(Schema):
    title: Label | None = None
    company: Label | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=16_000)]

    @field_validator("description")
    @classmethod
    def ensure_job_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a job description.")
        return value


class JobDescription(JobInput, Record):
    requirements: list[Requirement]
    required_qualifications: list[Text]
    required_technical_skills: list[Text]
    preferred_technical_skills: list[Text]
    responsibilities: list[Text]
    professional_competencies: list[Text]
    experience_requirements: list[Text]
    industry_terminology: list[str]
    seniority_level: str | None = None


class MatchKind(StrEnum):
    DIRECT = "DIRECT_MATCH"
    TRANSFERABLE = "TRANSFERABLE_MATCH"
    MISSING = "MISSING"
    CLARIFY = "NEEDS_CLARIFICATION"


class Match(Schema):
    requirement_id: UUID
    requirement: Text
    classification: MatchKind
    evidence_ids: list[UUID] = Field(default_factory=list)
    explanation: str


class RunInput(Schema):
    profile_id: UUID
    job_id: UUID | None = None


class RunStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorDetail(Schema):
    code: str
    message: str


class Run(Record, RunInput):
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime
    draft_id: UUID | None = None
    error: ErrorDetail | None = None


class DraftEntry(Schema):
    evidence_id: UUID
    fields: Education | Experience | Project | ProfileItem


class DraftSection(Schema):
    key: str
    heading: str
    entries: list[DraftEntry]


class Draft(Record):
    run_id: UUID
    title: Label
    contact: Contact
    summary: Text | None = None
    sections: list[DraftSection]
    matches: list[Match]
    ats_text: str


class DraftEdit(Schema):
    title: Label | None = None
    summary: Text | None = None
    evidence_order: list[UUID] | None = Field(default=None, max_length=350)
