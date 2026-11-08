"""Local authenticated HTTP adapter. Start with uvicorn src.api:create_app --factory."""
from __future__ import annotations

from contextlib import asynccontextmanager, closing, contextmanager
import hmac
import os
from pathlib import Path
import re
import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from src.case_queue import queue_ids
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.batch_scan import ROOT, validate_request
from src.case_application import CaseApplication, MissingCase, projection
from src.review_workflow import TransitionError


@contextmanager
def single_process(path):
    """OS lease survives thread boundaries and is released after process death.

    Fail startup for a second server using this same store. Not a distributed lock.
    """
    stream = Path(str(path) + '.lock').open('a+b')
    try:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            if not stream.read(1):
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


class Submission(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account_id: int = Field(ge=1, le=2**63-1)
    market: Literal[1] = 1
    as_of_period: str

    @field_validator('market', mode='before')
    @classmethod
    def strict_market(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('Only integer market 1 is supported')
        return value

    @field_validator('as_of_period')
    @classmethod
    def month(cls, value):
        validate_request(dict(schema_version=1, account_ids=[1], market=1, as_of_period=value))
        return value

    def scan_request(self):
        return dict(schema_version=1, account_ids=[self.account_id], market=self.market,
                    as_of_period=self.as_of_period)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0)
    package_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['approved', 'rejected']
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('A reason is required')
        return value.strip()


def create_app(*, store_path=None, scores_path=None, source_root=ROOT, token=None,
               start_worker=True):
    """Factory supports isolated tests; configuration is evaluated at startup."""
    configured_token = token if token is not None else os.environ.get('COPILOT_API_TOKEN', '')
    if len(configured_token) < 32 or not configured_token.isascii() or any(c.isspace() for c in configured_token):
        raise ValueError('Set COPILOT_API_TOKEN to a random ASCII token of at least 32 characters')
    path = Path(store_path or os.environ.get('COPILOT_CASE_DB', 'var/api/cases.db')).resolve()
    scores = Path(scores_path or os.environ.get('COPILOT_SCORES_DB', 'data/scores.db')).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path == scores:
        raise ValueError('Case store must be separate from the score database')

    @asynccontextmanager
    async def lifespan(app):
        with single_process(path):
            application = CaseApplication(path, scores, source_root)
            app.state.application = application
            if start_worker:
                application.start()
            try:
                yield
            finally:
                application.close()

    bearer = HTTPBearer(auto_error=False)

    def analyst(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if (credentials is None or credentials.scheme.lower() != 'bearer'
                or not hmac.compare_digest(credentials.credentials.encode(), configured_token.encode())):
            raise HTTPException(401, 'Authentication required', headers={'WWW-Authenticate': 'Bearer'})
        return 'demo-analyst'  # Local single-analyst token; never take identity from JSON.

    app = FastAPI(title='Behavioral Score Trajectory Copilot', version='m3.2',
                  lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def bounded_body(request: Request, call_next):
        # Bound streamed bodies too; Content-Length alone can be absent or false.
        if request.method in ('POST', 'PUT', 'PATCH'):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return JSONResponse({'detail': 'Request body too large'}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Do not echo payload values or private analyst reasons in errors.
        return JSONResponse({'detail': 'Invalid request fields'}, status_code=422)

    @app.exception_handler(TransitionError)
    async def conflict(request, exc):
        return JSONResponse({'detail': 'Case state or package conflict; reload the case'}, status_code=409)

    @app.exception_handler(MissingCase)
    async def missing(request, exc):
        return JSONResponse({'detail': 'Case not found'}, status_code=404)

    @app.exception_handler(sqlite3.Error)
    async def storage(request, exc):
        return JSONResponse({'detail': 'Storage unavailable'}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({'detail': 'Operation unavailable'}, status_code=500)

    def application(request: Request):
        return request.app.state.application

    def case_id(value: str):
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except ValueError:
            raise HTTPException(404, 'Case not found') from None
        return value

    @app.get('/health/live')
    def live():
        return {'status': 'alive'}

    @app.get('/health/ready')
    def ready(service=Depends(application)):
        if service.failure or not service.thread or not service.thread.is_alive():
            raise HTTPException(503, 'Worker unavailable')
        with closing(service.store.connect()) as db:
            db.execute('SELECT 1').fetchone()
        uri = service.scores_path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute('SELECT 1 FROM scores LIMIT 1').fetchone()
        return {'status': 'ready', 'mode': 'offline', 'live_notes': False}

    @app.get('/openapi.json', dependencies=[Depends(analyst)])
    def schema():
        return app.openapi()

    @app.post('/cases', status_code=202)
    def submit(body: Submission, response: Response, service=Depends(application),
               actor=Depends(analyst), idempotency_key: str = Header(min_length=1, max_length=80)):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', idempotency_key):
            raise HTTPException(422, 'Invalid idempotency key')
        try:
            case = service.submit(body.scan_request(), actor + ':' + idempotency_key)
        except (ValueError, OSError) as exc:
            if isinstance(exc, TransitionError):
                raise
            raise HTTPException(503, 'Source snapshot unavailable') from None
        response.headers['Location'] = '/cases/' + case['case_id']
        return projection(case)

    @app.get('/cases', dependencies=[Depends(analyst)])
    def cases(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
              service=Depends(application),
              workflow_status: Literal['queued', 'running', 'awaiting_review', 'held',
                                       'failed', 'completed'] | None = Query(None)):
        ids, has_more = queue_ids(service.store, limit, offset, workflow_status)
        return {'items': [projection(service.store.read_owned(i), detail=False) for i in ids],
                'limit': limit, 'offset': offset, 'has_more': has_more}

    @app.get('/cases/{identifier}', dependencies=[Depends(analyst)])
    def read(identifier: str, service=Depends(application)):
        return projection(service.store.read_owned(case_id(identifier)))

    @app.get('/cases/{identifier}/evidence', dependencies=[Depends(analyst)])
    def evidence(identifier: str, service=Depends(application)):
        case = service.store.read_owned(case_id(identifier))
        if case['analyst_decision'] != 'approved':
            raise HTTPException(409, 'Approved package required')
        return projection(case)

    @app.post('/cases/{identifier}/decisions')
    def decide(identifier: str, body: Decision, actor=Depends(analyst), service=Depends(application)):
        identifier = case_id(identifier)
        payload = dict(body.model_dump(), case_id=identifier, reviewer=actor)
        return projection(service.decide(identifier, payload))

    # The shell contains no private data. Case reads remain bearer-authenticated.
    ui = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'

    @app.get('/', include_in_schema=False)
    def frontend():
        if not (ui / 'index.html').is_file():
            raise HTTPException(503, 'Build frontend with npm ci and npm run build in frontend')
        return FileResponse(ui / 'index.html')

    app.mount('/ui', StaticFiles(directory=ui, check_dir=False), name='ui')
    return app
