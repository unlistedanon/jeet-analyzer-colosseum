"""FastAPI application exposing bounded, read-only forensic investigations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4
import json
import os
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from jeet_analyzer.progress import safe_message

from .models import (
    BetaFeedbackRequest,
    BetaInvestigationRequest,
    BetaInviteRequest,
    ClusterAuditRequest,
    InvestigationMode,
    InvestigationView,
    SellerScanRequest,
    WalletAuditRequest,
)
from .beta_auth import ADMIN_COOKIE, BETA_COOKIE, issue_session, read_session, verify_admin, verify_invite
from .beta_config import BetaConfig
from .beta_service import BetaJobManager, beta_view
from .beta_store import BetaLimitError, BetaStorage, SQLiteBetaStore
from .sol_payments import PaymentConfig, SolPayments, payment_router
from .pump_feed import PumpRoundFeed
from .usage import UsageEvent, UsageStore
from .game_store import GameStore
from .service import EngineAdapter, InProcessCliEngineAdapter
from .store import InvestigationStore
from .search_public import ROBOTS, SITEMAP


def _cors_origins() -> list[str]:
    configured = os.environ.get("JEET_ANALYZER_CORS_ORIGINS", "")
    if configured.strip():
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return ["http://127.0.0.1:5173", "http://localhost:5173"]


def create_app(
    *,
    engine_adapter: EngineAdapter | None = None,
    store: InvestigationStore | None = None,
    receipt_root: Path | None = None,
    beta_config: BetaConfig | None = None,
    beta_storage: BetaStorage | None = None,
    payment_config: PaymentConfig | None = None,
    telegram_config=None,
    telegram_sender=None,
) -> FastAPI:
    adapter = engine_adapter or InProcessCliEngineAdapter()
    registry = store or InvestigationStore()
    outputs = receipt_root or Path(os.environ.get("JEET_ANALYZER_UI_RECEIPTS", "receipts/ui-investigations"))
    beta = beta_config or BetaConfig.from_env()
    beta_registry = beta_storage or SQLiteBetaStore(beta.database_path)
    beta_registry.initialize()
    usage = UsageStore(beta.database_path.with_suffix(".usage.sqlite3"), beta.session_secret)
    beta_registry.recover_interrupted()
    beta_jobs = BetaJobManager(config=beta, storage=beta_registry, engine=adapter)
    payments = SolPayments(beta, payment_config or PaymentConfig.from_env())
    beta_jobs.member_config = payments.effective_config
    game_store = GameStore(beta.database_path)
    game_store.initialize()
    pump_feed = PumpRoundFeed(on_round=game_store.add_round, on_jeet=game_store.settle_jeet)
    from .telegram.config import TelegramConfig
    telegram_settings = telegram_config or TelegramConfig.from_env()
    telegram_runtime = None
    if telegram_settings.enabled:
        from .telegram.runtime import TelegramRuntime
        telegram_runtime = TelegramRuntime(telegram_settings, beta, beta_jobs, payments, telegram_sender)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        try:
            if telegram_runtime:
                await telegram_runtime.start()
            pump_feed.start()
            yield
        finally:
            pump_feed.stop()
            try:
                if telegram_runtime:
                    await telegram_runtime.stop()
            finally:
                beta_jobs.shutdown()

    app = FastAPI(
        title="Jeet Analyzer API",
        version="1.0.0",
        description="Invite-gated, bounded, read-only transport for the existing Jeet Analyzer forensic engine.",
        lifespan=lifespan,
    )
    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type"],
        )
    app.state.engine_adapter = adapter
    app.state.investigation_store = registry
    app.state.receipt_root = outputs
    app.state.beta_config = beta
    app.state.beta_storage = beta_registry
    app.state.beta_jobs = beta_jobs
    app.state.sol_payments = payments
    app.state.pump_feed = pump_feed
    app.state.game_store = game_store

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if beta.configured and forwarded_proto == "http":
            return JSONResponse(
                {"detail": "HTTPS is required"},
                status_code=426,
                headers={"Cache-Control": "no-store"},
            )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 16_384:
                    return JSONResponse({"detail": "request body is too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "invalid content length"}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > 16_384:
                return JSONResponse({"detail": "request body is too large"}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'",
        )
        if beta.configured and forwarded_proto == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def beta_subject(request: Request) -> str:
        payload = read_session(beta, request.cookies.get(BETA_COOKIE), kind="beta")
        if payload is None:
            raise HTTPException(status_code=401, detail="beta access required")
        return str(payload["sub"])

    def admin_subject(request: Request) -> str:
        payload = read_session(beta, request.cookies.get(ADMIN_COOKIE), kind="admin")
        if payload is None:
            raise HTTPException(status_code=401, detail="admin access required")
        return str(payload["sub"])

    app.include_router(payment_router(payments, beta_subject))
    if telegram_runtime:
        app.state.telegram = telegram_runtime
        app.include_router(telegram_runtime.router(beta_subject, admin_subject))

    def legacy_local_only() -> None:
        if beta.enabled or beta.configured:
            raise HTTPException(status_code=404, detail="route is unavailable in hosted beta mode")

    def execute(identifier: str) -> None:
        record = registry.get(identifier)
        if record is None:
            return
        registry.mark_running(identifier)
        try:
            result = adapter.run(record.mode, record.request, outputs / identifier)
            registry.complete(identifier, result.result, result.artifact_paths)
        except Exception as exc:  # API boundary: redact provider URLs/credentials before transport.
            registry.fail(identifier, safe_message(exc))

    def submit(mode: InvestigationMode, request: SellerScanRequest | WalletAuditRequest | ClusterAuditRequest, tasks: BackgroundTasks) -> InvestigationView:
        record = registry.create(mode, request.model_dump())
        tasks.add_task(execute, record.id)
        return record.view()

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine": "jeet-analyzer-python",
            "capability": "read-only",
            "beta_enabled": beta.enabled,
        }

    app.add_api_route("/api/health", health, methods=["GET"], include_in_schema=False)

    @app.get("/health", include_in_schema=False)
    def container_health() -> dict[str, str]:
        return {"status": "ok", "capability": "read-only"}

    @app.get("/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        storage_ready = bool(getattr(beta_registry, "ready", lambda: True)())
        configured = beta.configured
        ready_state = storage_ready and (not beta.enabled or configured)
        return JSONResponse(
            {
                "status": "ready" if ready_state else "not_ready",
                "storage": "ready" if storage_ready else "unavailable",
                "beta_enabled": beta.enabled,
                "beta_configured": configured,
            },
            status_code=200 if ready_state else 503,
        )

    @app.post("/api/v1/investigations/seller-scan", response_model=InvestigationView, status_code=202)
    def seller_scan(request: SellerScanRequest, tasks: BackgroundTasks) -> InvestigationView:
        legacy_local_only()
        return submit(InvestigationMode.SELLER_SCAN, request, tasks)

    app.add_api_route("/api/scan", seller_scan, methods=["POST"], status_code=202, include_in_schema=False)

    @app.post("/api/v1/investigations/wallet-audit", response_model=InvestigationView, status_code=202)
    def wallet_audit(request: WalletAuditRequest, tasks: BackgroundTasks) -> InvestigationView:
        legacy_local_only()
        return submit(InvestigationMode.WALLET_AUDIT, request, tasks)

    app.add_api_route("/api/wallet", wallet_audit, methods=["POST"], status_code=202, include_in_schema=False)

    @app.post("/api/v1/investigations/cluster-audit", response_model=InvestigationView, status_code=202)
    def cluster_audit(request: ClusterAuditRequest, tasks: BackgroundTasks) -> InvestigationView:
        legacy_local_only()
        return submit(InvestigationMode.CLUSTER_AUDIT, request, tasks)

    app.add_api_route("/api/cluster-audit", cluster_audit, methods=["POST"], status_code=202, include_in_schema=False)

    @app.get("/api/v1/investigations/{identifier}", response_model=InvestigationView)
    def get_investigation(identifier: str) -> InvestigationView:
        legacy_local_only()
        record = registry.get(identifier)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        return record.view()

    @app.get("/api/v1/investigations/{identifier}/receipt")
    def get_receipt(identifier: str) -> JSONResponse:
        legacy_local_only()
        record = registry.get(identifier)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        if record.result is None:
            raise HTTPException(status_code=409, detail="receipt is not ready")
        return JSONResponse(record.result, headers={"Content-Disposition": f'attachment; filename="jeet-analyzer-{identifier}.json"'})

    @app.get("/api/v1/investigations/{identifier}/events")
    def get_events(identifier: str) -> JSONResponse:
        legacy_local_only()
        record = registry.get(identifier)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        path = record.artifact_paths.get("evidence_jsonl")
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="evidence JSONL is not available")
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return JSONResponse({"records": records})

    @app.get("/api/v1/investigations/{identifier}/artifacts/{artifact_name}")
    def download_artifact(identifier: str, artifact_name: str) -> FileResponse:
        legacy_local_only()
        record = registry.get(identifier)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        path = record.artifact_paths.get(artifact_name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        media_type = "application/x-ndjson" if path.suffix == ".jsonl" else "application/json" if path.suffix == ".json" else "text/csv"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/api/beta/session")
    def beta_session(request: Request, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        payload = read_session(beta, request.cookies.get(BETA_COOKIE), kind="beta")
        if payload is None and beta.public_access and beta.configured:
            # A private, signed browser identity preserves report and quota isolation.
            # Retain it for a year so anonymous membership survives short sessions.
            visitor_config = replace(beta, session_ttl_seconds=31_536_000)
            token = issue_session(visitor_config, subject=f"visitor-{uuid4().hex}", kind="beta")
            response.set_cookie(BETA_COOKIE, token, max_age=visitor_config.session_ttl_seconds,
                                httponly=True, secure=beta.cookie_secure, samesite="strict", path="/")
            payload = read_session(beta, token, kind="beta")
        limits = payments.effective_config(str(payload["sub"])) if payload else beta
        return {
            "authenticated": payload is not None,
            "public_access": beta.public_access,
            "beta_enabled": beta.enabled,
            "beta_configured": beta.configured,
            "code_id": payload.get("sub") if payload else None,
            "limits": {
                "runs_per_code_per_day": limits.max_runs_per_code_per_day,
                "estimated_credits_per_run": limits.max_estimated_credits_per_run,
                "global_daily_estimated_credits": beta.global_daily_credit_cap,
                "max_concurrent": beta.max_concurrent,
            },
        }

    @app.post("/api/beta/usage")
    def usage_event(body: UsageEvent, request: Request) -> dict[str, bool]:
        subject = beta_subject(request)
        if body.event == "result_viewed":
            record = beta_registry.get(body.investigation_id or "", code_id=subject)
            if record is None or record.get("result") is None:
                raise HTTPException(status_code=404, detail="result not available")
        elif body.investigation_id is not None:
            raise HTTPException(status_code=422, detail="this event does not accept an investigation ID")
        return {"stored": usage.record(subject, body.event)}

    @app.get("/api/game/active-round")
    def game_active_round() -> dict[str, Any]:
        """Public paper-game feed; never accepts writes or wallet actions."""
        return pump_feed.snapshot()

    def game_player(request: Request, response: Response) -> str:
        player_id = request.cookies.get("jeet_player")
        player = game_store.player(player_id)
        if not player_id:
            response.set_cookie("jeet_player", player["id"], max_age=60 * 60 * 24 * 365, httponly=True, secure=beta.cookie_secure, samesite="lax", path="/")
        return str(player["id"])

    @app.get("/api/game/state")
    def game_state(request: Request, response: Response) -> dict[str, Any]:
        return game_store.state(game_player(request, response))

    @app.post("/api/game/predict")
    def game_predict(body: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
        player_id = game_player(request, response)
        mint = body.get("mint")
        bucket = body.get("bucket")
        if not isinstance(mint, str) or not isinstance(bucket, str):
            raise HTTPException(status_code=422, detail="mint and bucket are required")
        try:
            player = game_store.predict(player_id, mint, bucket)
            return {"ok": True, "state": game_store.state(player_id), "player": player}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/beta/session")
    def beta_login(body: BetaInviteRequest, response: Response) -> dict[str, Any]:
        if not beta.configured:
            raise HTTPException(status_code=503, detail="beta access is not configured")
        identifier = verify_invite(body.access_code, beta.code_hashes)
        if identifier is None:
            raise HTTPException(status_code=401, detail="invalid or revoked beta access code")
        token = issue_session(beta, subject=identifier, kind="beta")
        limits = payments.effective_config(identifier)
        response.set_cookie(
            BETA_COOKIE,
            token,
            max_age=beta.session_ttl_seconds,
            httponly=True,
            secure=beta.cookie_secure,
            samesite="strict",
            path="/",
        )
        return {
            "authenticated": True,
            "beta_enabled": beta.enabled,
            "beta_configured": beta.configured,
            "code_id": identifier,
            "limits": {
                "runs_per_code_per_day": limits.max_runs_per_code_per_day,
                "estimated_credits_per_run": limits.max_estimated_credits_per_run,
                "global_daily_estimated_credits": beta.global_daily_credit_cap,
                "max_concurrent": beta.max_concurrent,
            },
        }

    @app.delete("/api/beta/session")
    def beta_logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(BETA_COOKIE, path="/")
        return {"authenticated": False}

    @app.post("/api/beta/investigations", status_code=202)
    def beta_investigation(body: BetaInvestigationRequest, request: Request) -> dict[str, Any]:
        code_id = beta_subject(request)
        if not beta.enabled:
            raise HTTPException(status_code=503, detail="Beta investigations are temporarily paused. Existing reports remain available.")
        try:
            record, duplicate = beta_jobs.submit(code_id=code_id, mint=body.mint, wallet=body.wallet)
        except BetaLimitError as exc:
            raise HTTPException(status_code=exc.status_code, detail={"kind": exc.kind, "message": str(exc)}) from exc
        usage.record(code_id, "scan_admitted")
        return beta_view(record, duplicate=duplicate, include_result=False)

    @app.get("/api/beta/investigations/{identifier}")
    def beta_investigation_status(identifier: str, request: Request) -> dict[str, Any]:
        code_id = beta_subject(request)
        record = beta_registry.get(identifier, code_id=code_id)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        return beta_view(record)

    @app.get("/api/beta/investigations/{identifier}/share")
    def beta_safe_share(identifier: str, request: Request, download: bool = False) -> JSONResponse:
        code_id = beta_subject(request)
        record = beta_registry.get(identifier, code_id=code_id)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        safe_result = record.get("safe_result")
        if safe_result is None:
            raise HTTPException(status_code=409, detail="public-safe report is not available")
        headers = {"Content-Disposition": f'attachment; filename="jeet-safe-report-{identifier}.json"'} if download else None
        return JSONResponse(safe_result, headers=headers)

    @app.post("/api/beta/investigations/{identifier}/feedback", status_code=201)
    def beta_feedback(identifier: str, body: BetaFeedbackRequest, request: Request) -> dict[str, Any]:
        code_id = beta_subject(request)
        if body.useful is None and not body.comment:
            raise HTTPException(status_code=422, detail="feedback must include a usefulness choice or comment")
        try:
            feedback_id = beta_registry.add_feedback(
                identifier=identifier,
                code_id=code_id,
                useful=body.useful,
                comment=body.comment,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="investigation not found") from exc
        usage.record(code_id, "feedback_submitted")
        return {"feedback_id": feedback_id, "stored": True}

    @app.get("/api/beta/admin/session")
    def admin_session(request: Request) -> dict[str, bool]:
        return {"authenticated": read_session(beta, request.cookies.get(ADMIN_COOKIE), kind="admin") is not None}

    @app.post("/api/beta/admin/session")
    def admin_login(body: BetaInviteRequest, response: Response) -> dict[str, bool]:
        if not beta.configured or not verify_admin(body.access_code, beta.admin_code_hash):
            raise HTTPException(status_code=401, detail="invalid admin credential")
        response.set_cookie(
            ADMIN_COOKIE,
            issue_session(beta, subject="beta-admin", kind="admin"),
            max_age=beta.session_ttl_seconds,
            httponly=True,
            secure=beta.cookie_secure,
            samesite="strict",
            path="/",
        )
        return {"authenticated": True}

    @app.delete("/api/beta/admin/session")
    def admin_logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(ADMIN_COOKIE, path="/")
        return {"authenticated": False}

    @app.get("/api/beta/admin/metrics")
    def admin_metrics(request: Request) -> dict[str, Any]:
        admin_subject(request)
        summary = beta_registry.admin_summary()
        summary["usage"] = usage.summary()
        if telegram_runtime:
            summary["telegram"] = telegram_runtime.store.summary()
        return summary

    if beta.frontend_dist.is_dir() and (beta.frontend_dist / "index.html").is_file():
        assets = beta.frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

        @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
        def public_robots() -> PlainTextResponse:
            return PlainTextResponse(ROBOTS, headers={"Cache-Control": "no-cache, max-age=0"})

        @app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
        def public_sitemap() -> Response:
            return Response(SITEMAP, media_type="application/xml", headers={"Cache-Control": "no-cache, max-age=0"})

        @app.api_route("/{frontend_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def frontend(frontend_path: str) -> Response:
            candidate = (beta.frontend_dist / frontend_path).resolve()
            root = beta.frontend_dist.resolve()
            if frontend_path == "index.html":
                return RedirectResponse("/", status_code=308)
            if frontend_path == "methodology":
                return RedirectResponse("/methodology/", status_code=308)
            if frontend_path == "methodology/":
                candidate = root / "methodology" / "index.html"
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
            if not frontend_path:
                return FileResponse(beta.frontend_dist / "index.html")
            return HTMLResponse(
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta name="robots" content="noindex"><title>Page not found | Jeet Analyzer</title>'
                '</head><body><main><h1>Page not found</h1><p>This is not a public Jeet Analyzer page.</p>'
                '<a href="/">Open Jeet Analyzer</a></main></body></html>', status_code=404,
            )

    return app


app = create_app()
