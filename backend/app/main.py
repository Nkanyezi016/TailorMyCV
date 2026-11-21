import asyncio
import logging
from collections import defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from secrets import compare_digest
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import ApiError, Settings
from app.schemas import (
    CandidateProfile,
    Draft,
    DraftEdit,
    ErrorDetail,
    ImportResult,
    JobDescription,
    JobInput,
    ProfileResult,
    Run,
    RunInput,
    RunStatus,
)
from app.services.ingestion import MAX_UPLOAD, import_cv
from app.services.lodestar.agent import build_draft, edit_draft
from app.services.lodestar.provider import OpenAIProvider, Provider
from app.services.matching import analyse_job

log = logging.getLogger("lodestar")
MAX_RESOURCES = 200
MAX_JSON_BODY = 512 * 1024


def error_response(status: int, code: str, message: str, headers=None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


class LimitRequestBody:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        is_import = scope["path"].rstrip("/") == "/api/v1/imports"
        limit = MAX_UPLOAD + 64 * 1024 if is_import else MAX_JSON_BODY
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                error = error_response(
                    413, "request_too_large", "Submit a smaller document or request body."
                )
                await error(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        chunks.clear()
        delivered = False

        async def replay():
            nonlocal delivered, body
            if delivered:
                return await receive()
            delivered = True
            message = {"type": "http.request", "body": body, "more_body": False}
            body = b""
            return message

        await self.app(scope, replay, send)


def lookup(records, key: UUID):
    record = records.get(key)
    if record is None:
        raise ApiError(404, "not_found", "This resource does not exist.")
    return record


class Workspace:
    def __init__(self, settings: Settings, provider: Provider | None):
        self.settings = settings
        self.provider = provider
        self.profiles: dict[UUID, ProfileResult] = {}
        self.jobs: dict[UUID, JobDescription] = {}
        self.runs: dict[UUID, Run] = {}
        self.drafts: dict[UUID, Draft] = {}
        self.snapshots: dict[UUID, CandidateProfile] = {}
        self.tasks: set[asyncio.Task] = set()
        self.workers = asyncio.Semaphore(settings.max_workers)
        self.created = defaultdict(int)

    def reserve(self, resource):
        if self.created[resource] >= MAX_RESOURCES:
            raise ApiError(507, "workspace_full", "The local workspace has reached its capacity.")
        self.created[resource] += 1

    def submit(self, body: RunInput) -> Run:
        profile = lookup(self.profiles, body.profile_id).profile
        job = lookup(self.jobs, body.job_id) if body.job_id else None
        if len(self.tasks) >= self.settings.max_pending + self.settings.max_workers:
            raise ApiError(
                429, "queue_full", "Wait for a current run to finish before trying again."
            )
        self.reserve("run")
        run = Run(**body.model_dump(), created_at=datetime.now(UTC))
        self.runs[run.id] = run
        task = asyncio.create_task(self.process(run, profile, job))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run

    async def process(self, run: Run, profile: CandidateProfile, job: JobDescription | None):
        async with self.workers:
            self.update(run, RunStatus.PROCESSING)
            try:
                draft = await build_draft(run.id, profile, job, self.provider)
            except ApiError as exc:
                self.update(
                    run,
                    RunStatus.FAILED,
                    error=ErrorDetail(
                        code=exc.code,
                        message=exc.message,
                    ),
                )
            except Exception:
                self.update(
                    run,
                    RunStatus.FAILED,
                    error=ErrorDetail(
                        code="generation_failed",
                        message="Lodestar could not generate a draft.",
                    ),
                )
            else:
                self.drafts[draft.id] = draft
                self.snapshots[draft.id] = profile
                self.update(run, RunStatus.COMPLETED, draft_id=draft.id)

    def update(self, run: Run, status: RunStatus, **fields):
        self.runs[run.id] = run.model_copy(update={"status": status, **fields})
        log.info("lodestar_run id=%s status=%s", run.id, status)


def create_app(settings: Settings | None = None, *, provider: Provider | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            active_provider = provider
            if active_provider is None and settings.provider == "openai":
                client = await stack.enter_async_context(httpx.AsyncClient())
                active_provider = OpenAIProvider(settings, client)
            workspace = app.state.workspace = Workspace(settings, active_provider)
            try:
                yield
            finally:
                tasks = tuple(workspace.tasks)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="TailorMyCV API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(LimitRequestBody)

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error(_request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc: RequestValidationError):
        paths = sorted({".".join(map(str, error["loc"])) for error in exc.errors()})
        # Do not echo field values, even in validation errors.
        fields = ", ".join(paths)[:300]
        return error_response(422, "validation_error", f"Check these fields: {fields}.")

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return error_response(exc.status_code, "invalid_request", "The request could not be read.")

    async def workspace_for(request: Request) -> Workspace:
        token = settings.api_token.get_secret_value() if settings.api_token else ""
        if token:
            presented = request.headers.get("authorization", "")
            if not compare_digest(presented.encode(), f"Bearer {token}".encode()):
                raise ApiError(401, "unauthorized", "Supply the workspace bearer token.")
        return request.app.state.workspace

    Store = Annotated[Workspace, Depends(workspace_for)]

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/v1/imports", status_code=201)
    async def imports(store: Store, file: Annotated[UploadFile, File()]) -> ImportResult:
        try:
            data = await file.read(MAX_UPLOAD + 1)
            return await run_in_threadpool(import_cv, file.filename or "", data)
        finally:
            await file.close()

    @app.post("/api/v1/profiles", status_code=201)
    async def create_profile(profile: CandidateProfile, store: Store) -> ProfileResult:
        store.reserve("profile")
        record = ProfileResult(profile=profile)
        store.profiles[record.id] = record
        return record

    @app.get("/api/v1/profiles/{profile_id}")
    async def get_profile(profile_id: UUID, store: Store) -> ProfileResult:
        return lookup(store.profiles, profile_id)

    @app.put("/api/v1/profiles/{profile_id}")
    async def replace_profile(
        profile_id: UUID, profile: CandidateProfile, store: Store
    ) -> ProfileResult:
        lookup(store.profiles, profile_id)
        record = ProfileResult(id=profile_id, profile=profile)
        store.profiles[profile_id] = record
        return record

    @app.post("/api/v1/jobs", status_code=201)
    async def create_job(body: JobInput, store: Store) -> JobDescription:
        job = await run_in_threadpool(analyse_job, body)
        store.reserve("job")
        store.jobs[job.id] = job
        return job

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: UUID, store: Store) -> JobDescription:
        return lookup(store.jobs, job_id)

    @app.post("/api/v1/runs", status_code=202)
    async def create_run(body: RunInput, store: Store) -> Run:
        return store.submit(body)

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(run_id: UUID, store: Store) -> Run:
        return lookup(store.runs, run_id)

    @app.get("/api/v1/drafts/{draft_id}")
    async def get_draft(draft_id: UUID, store: Store) -> Draft:
        return lookup(store.drafts, draft_id)

    @app.patch("/api/v1/drafts/{draft_id}")
    async def patch_draft(draft_id: UUID, body: DraftEdit, store: Store) -> Draft:
        draft = lookup(store.drafts, draft_id)
        updated = edit_draft(draft, body, store.snapshots[draft_id])
        store.drafts[draft_id] = updated
        return updated

    return app


app = create_app()
