import json
import logging
import time
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.agent import router as agent_router
from app.api.memory import router as memory_router
from app.api.menu import router as menu_router
from app.api.records import router as records_router
from app.api.vision import router as vision_router
from app.agent.graph import close_agent_runtime
from app.core.authz import normalize_capabilities, reset_auth_context, set_auth_context
from app.core.config import get_settings
from app.core.security import decode_token
from app.models.db import close_pool, init_pool
from app.tools.mcp_server import create_mcp_server

logger = logging.getLogger(__name__)


def _is_mcp_service_token(token: str, settings) -> bool:
    candidates = {f"{settings.jwt_secret}:mcp"}
    if settings.mcp_service_token:
        candidates.add(settings.mcp_service_token)
    return token in candidates


class JWTMiddleware(BaseHTTPMiddleware):
    EXCLUDED = {
        "/bobo/auth/login",
        "/bobo/auth/register",
        "/bobo/auth/refresh",
        "/bobo/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        request_id = request.headers.get("X-Request-Id") or uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        auth_context_token = None

        def _attach_metadata(response):
            response.headers["X-Request-Id"] = request_id
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": getattr(response, "status_code", None),
                        "duration_ms": duration_ms,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return response

        path = request.url.path
        root_path = request.scope.get("root_path", "")
        mcp_request = path.startswith("/mcp") or str(root_path).startswith("/mcp")
        if request.method == "OPTIONS" or any(path.startswith(p) for p in self.EXCLUDED):
            return _attach_metadata(await call_next(request))

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _attach_metadata(JSONResponse(status_code=401, content={"detail": "missing bearer token"}))

        token = auth_header.replace("Bearer ", "", 1)
        if mcp_request and _is_mcp_service_token(token, settings):
            request.state.user_id = "mcp-service"
            request.state.auth_source = "mcp_service_token"
            request.state.auth_capabilities = ("*",)
            auth_context_token = set_auth_context(
                {
                    "user_id": "mcp-service",
                    "auth_source": "mcp_service_token",
                    "source": request.headers.get("X-Bobo-Source", "mcp"),
                    "capabilities": ("*",),
                }
            )
            try:
                return _attach_metadata(await call_next(request))
            finally:
                if auth_context_token is not None:
                    reset_auth_context(auth_context_token)

        try:
            payload = decode_token(token, expected_token_type="access")
            user_id = payload.get("sub")
            if not user_id:
                raise JWTError("missing subject")
            request.state.user_id = str(user_id)
            request.state.auth_source = "user_bearer"
            request.state.auth_capabilities = normalize_capabilities(payload.get("caps"))
            auth_context_token = set_auth_context(
                {
                    "user_id": str(user_id),
                    "auth_source": "user_bearer",
                    "source": request.headers.get("X-Bobo-Source", "http"),
                    "capabilities": request.state.auth_capabilities,
                }
            )
        except JWTError:
            return _attach_metadata(JSONResponse(status_code=401, content={"detail": "invalid token"}))

        try:
            return _attach_metadata(await call_next(request))
        finally:
            if auth_context_token is not None:
                reset_auth_context(auth_context_token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    mcp_container = getattr(app.state, "mcp_container", None)
    async with AsyncExitStack() as stack:
        if mcp_container and mcp_container.http_app is not None:
            lifespan_context = getattr(getattr(mcp_container.http_app, "router", None), "lifespan_context", None) or getattr(mcp_container.http_app, "lifespan", None)
            if callable(lifespan_context):
                await stack.enter_async_context(lifespan_context(mcp_container.http_app))

        init_pool()
        logger.info(json.dumps({"event": "app_startup", "app_name": settings.app_name}, ensure_ascii=False))
        yield
        await close_agent_runtime()
        close_pool()
        logger.info(json.dumps({"event": "app_shutdown", "app_name": settings.app_name}, ensure_ascii=False))


def create_app() -> FastAPI:
    settings = get_settings()
    is_production = settings.env.lower() in {"prod", "production"}
    mcp_container = create_mcp_server()
    app = FastAPI(
        title=settings.app_name,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.mcp_container = mcp_container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.cors_prod_origin],
        allow_origin_regex=r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(JWTMiddleware)

    @app.get("/bobo/health")
    def health() -> dict:
        return {"ok": True}

    app.include_router(auth_router)
    app.include_router(records_router)
    app.include_router(vision_router)
    app.include_router(menu_router)
    app.include_router(agent_router)
    app.include_router(memory_router)

    if mcp_container and mcp_container.http_app is not None:
        app.mount("/mcp", mcp_container.http_app)

    return app


app = create_app()
