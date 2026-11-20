# TailorMyCV

TailorMyCV is a planned web application that helps job seekers build a CV for a specific role. Start by importing an existing CV or by entering your education, work experience, projects and skills. Paste a job description, then review a tailored draft that puts your most relevant experience first.

This is a backend and data engineering portfolio project. It follows the full journey from unstructured documents to validated records, concurrent background processing and versioned outputs.

**Project status:** Planning and implementation backlog. The features and architecture below describe the first release. Local setup instructions will be added with the integrated application.

## How it will work

1. **Import or create a profile.** Extract fields from a text-based PDF or DOCX, or enter them manually. Review and correct the profile before using it.
2. **Analyse the job description.** Identify skills, qualifications and responsibilities, then map them to evidence in the profile.
3. **Generate a draft.** Use AI-assisted wording and section ordering to highlight relevant evidence. Keep source dates, qualifications and achievements accurate, and surface requirements for which the profile has no supporting evidence.
4. **Review and export.** Edit the draft, save a version and export a text-readable CV with standard headings for applicant tracking systems.

## Engineering focus

| Area | What the project will demonstrate |
| --- | --- |
| Data ingestion and quality | Parse documents, normalise both input paths into one profile schema, validate fields and surface extraction errors. |
| Data modelling | Use a relational database with migrations and ownership checks to link profiles, job descriptions, tailoring runs and CV draft versions. |
| Backend APIs | Define clear contracts for imports, job submission, job status and editable drafts. Keep validation and persistence on the server. |
| Concurrency | Submit tailoring requests as background jobs so the API can return promptly. Limit worker concurrency, atomically claim jobs and keep retries safe through idempotency and transactional writes. |
| Reliability and verification | Track queued, processing, completed and failed states. Use synthetic fixtures to test simultaneous requests, repeated submissions and recovery after a worker interruption. |
| Privacy | Use synthetic CVs in repository examples and diagnostics. Log job IDs, status transitions and timings while keeping uploaded CV contents out of logs. |

## Planned architecture

The frontend sends profiles and job descriptions to the API. The API validates input and records a tailoring run in the database. A background worker claims the run, matches requirements against profile evidence and stores a draft with its final status. The frontend retrieves progress and lets the user review, edit and export the result.

A useful demo will submit two different jobs at the same time, repeat one submission and show that each distinct run produces its own result while the duplicate request returns the existing run.

## Roadmap

| Order | Deliverable |
| --- | --- |
| 1 | [Build the frontend with mock data](https://github.com/Nkanyezi016/TailorMyCV/issues/1) |
| 2 | [Build CV ingestion, job matching and draft APIs](https://github.com/Nkanyezi016/TailorMyCV/issues/2) |
| 3 | [Persist CV versions and process jobs concurrently](https://github.com/Nkanyezi016/TailorMyCV/issues/3) |
| 4 | [Connect the frontend, API and database end to end](https://github.com/Nkanyezi016/TailorMyCV/issues/4) |
