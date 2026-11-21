# TailorMyCV backend

The FastAPI service behind Lodestar handles CV import, profile review, job analysis, background tailoring runs and editable drafts. See the [API examples](docs/API.md) and the interactive schema at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Start locally

From the repository root, using Python 3.11 or newer:

~~~sh
cd backend
python -m venv .venv
. .venv/bin/activate  # PowerShell .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --no-access-log
~~~

Or build a container from the repository root:

~~~sh
docker build -t tailormycv-backend backend
docker run --rm -p 127.0.0.1:8000:8000 tailormycv-backend
~~~

## How Lodestar uses evidence

1. Import a text-based PDF or DOCX. Review the suggested profile, source quotes and any lines the parser could not confidently place. Submit the reviewed profile through the same API used for manual entry.
2. Optionally paste a job description. The analysis uses headings and wording to identify requirements. Matching marks direct support, transferable evidence, missing requirements and details that need clarification.
3. Submit a tailoring run. Lodestar ranks the confirmed evidence and selects entries for the summary. The application writes the summary using approved phrases and renders the full CV from that profile snapshot. It checks that evidence IDs exist before creating a draft.
4. Review the draft, update the title or summary, and reorder or omit records. The plain-text export uses standard section headings and keeps matches and gaps out of the CV body.

Lodestar uses local evidence ranking by default, so development and tests work without an API key. To use OpenAI, copy [the example settings](.env.example) to an ignored .env file and set LODESTAR_PROVIDER=openai and LODESTAR_OPENAI_API_KEY. The provider uses a configurable model, timeout and retry count. Direct contact fields and date metadata stay out of the request. Free-text profile fields can still contain details the candidate entered. The job description, career preferences and selected profile fields go to the provider when it is enabled. For Docker, load the settings with docker run --env-file backend/.env.

Runs and drafts live in one local process. Restarting clears the workspace. Bind to loopback for development. An optional LODESTAR_API_TOKEN enables a single-workspace bearer token on the data routes. PostgreSQL, multi-user ownership, durable workers and idempotency are tracked in [issue #3](https://github.com/Nkanyezi016/TailorMyCV/issues/3). The static frontend's local workflow is currently independent of the API. Wiring it up is tracked in [issue #4](https://github.com/Nkanyezi016/TailorMyCV/issues/4).

## Verify

~~~sh
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
~~~
