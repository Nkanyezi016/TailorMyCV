# TailorMyCV API

The data routes below use the /api/v1 prefix. Send JSON for write requests except uploads. If LODESTAR_API_TOKEN is configured, include an Authorization header with Bearer followed by the token. The examples abbreviate some response fields. IDs in the payloads are synthetic.

| Method and path | Success | Purpose |
| --- | --- | --- |
| GET /health | 200 | Check whether the process is running |
| POST /api/v1/imports | 201 | Extract a reviewable profile from a PDF or DOCX |
| POST /api/v1/profiles | 201 | Confirm an imported profile or save manual entry |
| GET /api/v1/profiles/{id} | 200 | Retrieve the confirmed profile |
| PUT /api/v1/profiles/{id} | 200 | Replace a profile after review or correction |
| POST /api/v1/jobs | 201 | Analyse a pasted job description |
| GET /api/v1/jobs/{id} | 200 | Retrieve the source posting and requirements |
| POST /api/v1/runs | 202 | Queue Lodestar for a confirmed profile |
| GET /api/v1/runs/{id} | 200 | Poll run status |
| GET /api/v1/drafts/{id} | 200 | Retrieve a draft, evidence matches and ATS-friendly text |
| PATCH /api/v1/drafts/{id} | 200 | Update the draft's title, summary or evidence ordering |

## Import and profile confirmation

Upload under the multipart field file. Files may be up to 5 MiB.

~~~sh
curl -F "file=@example.docx" http://127.0.0.1:8000/api/v1/imports
~~~

Example result:

~~~json
{
  "review_required": true,
  "profile": {
    "contact": {"full_name": "Avery Example"},
    "education": [
      {
        "id": "b21d967d-fbde-48e5-8839-2e50f3dce742",
        "qualification": "BSc Mathematical Sciences",
        "institution": "Example University",
        "source_dates": "2020 – 2023"
      }
    ]
  },
  "source_quotes": {
    "b21d967d-fbde-48e5-8839-2e50f3dce742": [
      "BSc Mathematical Sciences — Example University",
      "2020 – 2023"
    ]
  },
  "unmapped_text": [],
  "warnings": ["Check extracted names, dates and sections. Add any details that were missed."]
}
~~~

After reviewing, POST the profile object to /api/v1/profiles. For manual entry, send the same shape. Records can omit IDs on creation and the API will assign them. Send previously assigned IDs on corrections to preserve record identity. Dates are strings so year-only, month-and-year and source date ranges can be preserved.

~~~json
{
  "contact": {"full_name": "Avery Example", "location": "Example City"},
  "projects": [
    {"name": "River Analytics", "technologies": ["Python", "PostgreSQL"]}
  ],
  "technical_skills": [{"name": "SQL"}]
}
~~~

The response wraps the validated profile in an object with id and profile keys. Use that id as profile_id for runs. An import preview alone is ineligible for a run.

## Job submission and status

Optionally POST the following to /api/v1/jobs.

~~~json
{
  "title": "Junior Data Engineer",
  "company": "Example Company",
  "description": "Required skills:\n- SQL\n- Power BI\nPreferred skills:\n- PostgreSQL"
}
~~~

The result includes the original description, id and extracted requirements. Each requirement has an id, description and kind. The kinds are required, preferred, responsibility and professional_competency.

POST to /api/v1/runs with the profile and optional job IDs. Omit job_id for a general-purpose CV.

~~~json
{
  "profile_id": "b6bdfaa4-b571-46f6-8b33-b67ba85ed3d3",
  "job_id": "97093331-69f9-4cdb-a9a3-2db14039d098"
}
~~~

Example status from GET /api/v1/runs/{id}:

~~~json
{
  "id": "56ee0920-76d4-4263-a460-fc785bb1f744",
  "profile_id": "b6bdfaa4-b571-46f6-8b33-b67ba85ed3d3",
  "job_id": "97093331-69f9-4cdb-a9a3-2db14039d098",
  "status": "completed",
  "created_at": "2026-01-01T10:00:00Z",
  "draft_id": "962d835b-18a2-481a-bfa8-e0ac415fa8d8",
  "error": null
}
~~~

Poll while queued or processing. On completed, retrieve draft_id. A failed run includes error.code and error.message and has a null draft_id. To retry after resolving the reported problem, submit a new run.

## Read and edit a draft

A draft response includes title, contact, summary, ordered sections, matches and ats_text. Each section contains a key, heading and entries with evidence_id and fields. The fields retain the original record's structure.

~~~json
{
  "id": "962d835b-18a2-481a-bfa8-e0ac415fa8d8",
  "title": "CV for Junior Data Engineer",
  "matches": [
    {
      "requirement_id": "2ca172a8-6e2c-47b3-b694-7e10aa901ad5",
      "requirement": "Power BI",
      "classification": "MISSING",
      "evidence_ids": [],
      "explanation": "No supporting evidence was found in the confirmed profile."
    }
  ],
  "ats_text": "Avery Example\n\nTechnical Skills\nSQL"
}
~~~

Use matches for review. Render ats_text for plain text or the ordered sections for templates and themes. The current frontend holds contact details, education, work and projects in local browser objects. When connecting it, map those objects to CandidateProfile, submit the title/company/description to the job endpoint, then poll a run and use its draft. Keep layout and theme selection in the client.

Example PATCH body for a title change and evidence selection:

~~~json
{
  "title": "Data Engineering CV",
  "summary": null,
  "evidence_order": ["b21d967d-fbde-48e5-8839-2e50f3dce742"]
}
~~~

Omit fields you want to keep. A null summary clears it. A string summary is a candidate edit. Providing evidence_order selects and orders the included entries. Use only distinct IDs from the profile snapshot for that run. Updating a profile affects future runs. Re-run to generate from newly confirmed source data.

## Common errors

~~~json
{
  "error": {
    "code": "scanned_pdf",
    "message": "Upload a text-based PDF or the original DOCX."
  }
}
~~~

| HTTP status | Code | Action |
| --- | --- | --- |
| 401 | unauthorized | Supply the configured workspace token |
| 404 | not_found | Use a resource ID returned by this running service |
| 413 | file_too_large or request_too_large | Submit a smaller document or request |
| 415 | unsupported_file | Upload PDF or DOCX |
| 422 | password_protected | Remove the password and upload the document again |
| 422 | unreadable_document | Open and resave the file in the intended format |
| 422 | scanned_pdf or no_selectable_text | Use a text-based PDF or original DOCX |
| 422 | validation_error | Check the reported JSON fields |
| 422 | invalid_evidence | Select IDs from the draft's source profile |
| 429 | queue_full | Wait for a current run to finish |
| 507 | workspace_full | The local in-memory workspace reached its item limit |
