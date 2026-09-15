import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from atlas.auth import Identity, authenticate
from atlas.config import settings
from atlas.db import pool, transaction
from atlas.telemetry import logger, setup, tracer

redis = Redis.from_url(settings.redis_url, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup()
    await pool.open(wait=True)
    yield
    await pool.close()
    await redis.aclose()


app = FastAPI(title="Atlas Knowledge API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    with tracer.start_as_current_span(f"{request.method} request") as span:
        span.set_attribute("request.id", request_id)
        response = await call_next(request)
        span.set_attribute("http.response.status_code", response.status_code)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    logger.info(
        "request",
        extra={
            "fields": {
                "request_id": request_id,
                "method": request.method,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        },
    )
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": "atlas"}


@app.get("/ready")
async def ready():
    try:
        async with transaction() as conn:
            await conn.execute("SELECT 1")
        await redis.ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.get("/api/me")
async def me(identity: Identity = Depends(authenticate)):
    return {"tenant_id": str(identity.tenant_id), "name": identity.name, "scopes": identity.scopes}
