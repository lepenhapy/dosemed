# main.py — app creation, router registration, startup/shutdown

import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi import HTTPException
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

# --- Logging ---
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(logging.FileHandler("dosemed.log", encoding="utf-8"))
except Exception:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=_log_handlers
)
logger = logging.getLogger("dosemed")

# database.py runs ALL migrations on import — must be first
from database import limiter  # noqa: E402

import cron  # noqa: E402  — registers scheduler jobs at import time

from routers import admin, alarmes, auth, bulas, estoque, farmacia, misc, orcamento, promocoes, publico, push  # noqa: E402

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    if cron._scheduler and not cron._scheduler.running:
        cron._scheduler.start()
        logger.info("Scheduler iniciado (vencimentos 08:00, alarmes a cada minuto)")
    elif not cron._scheduler:
        logger.warning("APScheduler não instalado — notificações automáticas desativadas")
    yield
    if cron._scheduler and cron._scheduler.running:
        cron._scheduler.shutdown(wait=False)


app = FastAPI(title="DoseMed API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
    response.headers["Content-Security-Policy"] = "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    logger.error(f"Erro não tratado em {request.url}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Erro interno. Tente novamente."})


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(estoque.router)
app.include_router(bulas.router)
app.include_router(alarmes.router)
app.include_router(farmacia.router)
app.include_router(admin.router)
app.include_router(orcamento.router)
app.include_router(push.router)
app.include_router(promocoes.router)
app.include_router(misc.router)
app.include_router(publico.router)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
