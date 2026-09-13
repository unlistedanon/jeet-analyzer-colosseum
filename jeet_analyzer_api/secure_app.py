"""Hardened ASGI entrypoint for the public invite-only beta."""

from __future__ import annotations

from typing import Any

from .app import create_app
from .beta_config import BetaConfig
from .operator_dashboard import create_operator_router
from .security import HostedBetaSecurityMiddleware


def _mount_operator_routes_before_frontend(application, config: BetaConfig) -> None:
    """Mount admin API routes before the SPA catch-all route.

    ``create_app`` installs ``/{frontend_path:path}`` when a production frontend
    build exists. Routes appended after that catch-all would be shadowed by the
    SPA, so include the operator router normally, then move only those newly
    added route objects immediately ahead of the catch-all.
    """

    start = len(application.router.routes)
    application.include_router(create_operator_router(config))
    operator_routes = list(application.router.routes[start:])
    if not operator_routes:
        return
    del application.router.routes[start:]
    insert_at = next(
        (
            index
            for index, route in enumerate(application.router.routes)
            if getattr(route, "path", None) == "/{frontend_path:path}"
        ),
        len(application.router.routes),
    )
    application.router.routes[insert_at:insert_at] = operator_routes


def create_secure_app(*, beta_config: BetaConfig | None = None, **kwargs: Any):
    config = beta_config or BetaConfig.from_env()
    application = create_app(beta_config=config, **kwargs)
    _mount_operator_routes_before_frontend(application, config)
    application.add_middleware(HostedBetaSecurityMiddleware, config=config)
    return application


app = create_secure_app()
