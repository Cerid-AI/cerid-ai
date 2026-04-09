"""Internal-only router registrations and shutdown hooks.

This file exists only in cerid-ai-internal. The bootstrap block at
the bottom of main.py calls bootstrap_internal() at startup.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("ai-companion")


def bootstrap_internal(app) -> None:
    """Register internal-only routers, settings, and taxonomy extensions.

    Called from the bootstrap block at the bottom of main.py (internal repo only).
    Must run BEFORE the app starts serving requests.
    """
    # 1. Extend settings with trading/boardroom config
    from config.settings_internal import extend_settings
    extend_settings()

    # 2. Extend taxonomy with trading/boardroom domains
    from config.taxonomy_internal import extend_taxonomy
    extend_taxonomy()

    # 3. Register internal-only routers
    from app.routers import alerts, migration, ws_sync

    app.include_router(alerts.router)
    app.include_router(alerts.router, prefix="/api/v1")
    app.include_router(migration.router)
    app.include_router(migration.router, prefix="/api/v1")
    app.include_router(ws_sync.router)

    # Trading proxy (conditional)
    from config.settings import CERID_TRADING_ENABLED
    if CERID_TRADING_ENABLED:
        from app.routers import trading_proxy
        app.include_router(trading_proxy.router)
        logger.info("Trading proxy router registered")

    # Eval harness (conditional)
    if os.getenv("CERID_EVAL_ENABLED", "").lower() in ("1", "true", "yes"):
        from app.routers import eval as eval_router
        app.include_router(eval_router.router)
        logger.info("Eval harness router registered")

    # Billing (pro/enterprise tier only)
    if os.getenv("CERID_TIER", "community") in ("pro", "enterprise"):
        from routers import billing
        app.include_router(billing.router)
        app.include_router(billing.router, prefix="/api/v1")
        logger.info("Billing router registered (tier=%s)", os.getenv("CERID_TIER"))

    # 4. Register shutdown hooks via the public callback list
    from app.main import _shutdown_hooks
    from config.settings import CERID_TRADING_ENABLED
    if CERID_TRADING_ENABLED:
        async def _close_trading_proxy() -> None:
            try:
                from app.routers.trading_proxy import close_trading_proxy_client
                await close_trading_proxy_client()
            except Exception as exc:
                logger.warning("Trading proxy shutdown failed: %s", exc)
        _shutdown_hooks.append(_close_trading_proxy)
