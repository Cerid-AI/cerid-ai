# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Embeddable chat widget endpoints.

Serves the widget HTML page (iframe mode), the JavaScript bundle (script tag
mode), and a default configuration endpoint.

Gated by ``CERID_WIDGET_ENABLED`` env var (default ``true``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["Widget"])

_WIDGET_ENABLED = os.getenv("CERID_WIDGET_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Path to the built widget JS bundle.  In production this is expected at
# ``packages/widget/dist/cerid-widget.js`` relative to the repo root.  The
# Docker image copies this to ``/app/static/cerid-widget.js``.
_STATIC_DIR = Path(os.getenv(
    "CERID_WIDGET_STATIC_DIR",
    str(Path(__file__).resolve().parents[3] / "packages" / "widget" / "dist"),
))

# ---------------------------------------------------------------------------
# Guard: 404 if widget is disabled
# ---------------------------------------------------------------------------


def _check_enabled() -> None:
    if not _WIDGET_ENABLED:
        raise HTTPException(status_code=404, detail="Widget is disabled")


# ---------------------------------------------------------------------------
# GET /widget/config — default widget configuration
# ---------------------------------------------------------------------------


@router.get(
    "/widget/config",
    response_class=JSONResponse,
    summary="Widget default configuration",
    description="Returns the default widget configuration. Clients can override values.",
)
async def widget_config(request: Request) -> dict[str, Any]:
    """Return default widget config, including the API URL derived from the request."""
    _check_enabled()

    # Derive the API URL from the incoming request so it works in any
    # deployment without manual configuration.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8888"))
    api_url = f"{scheme}://{host}"

    return {
        "apiUrl": api_url,
        "clientId": "widget-embed",
        "position": "bottom-right",
        "theme": "auto",
        "title": "Cerid AI",
        "placeholder": "Ask anything...",
    }


# ---------------------------------------------------------------------------
# GET /widget.js — serve the JavaScript bundle
# ---------------------------------------------------------------------------


@router.get(
    "/widget.js",
    summary="Widget JavaScript bundle",
    description="Serves the built widget JavaScript bundle for script-tag embedding.",
)
async def widget_script() -> Response:
    """Serve the widget JavaScript bundle."""
    _check_enabled()

    js_path = _STATIC_DIR / "cerid-widget.js"
    if not js_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Widget JS bundle not found. Run 'npm run build' in "
                "packages/widget/ to generate it."
            ),
        )

    content = js_path.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ---------------------------------------------------------------------------
# GET /widget.html — standalone HTML page (iframe mode)
# ---------------------------------------------------------------------------

_WIDGET_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body, #cerid-widget-root {{
    width: 100%; height: 100%; overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Helvetica Neue", Arial, sans-serif;
  }}
  body {{
    background: {bg_color};
    color: {text_color};
  }}
  /* Full-page panel overrides */
  #cerid-widget-root .cerid-panel {{
    width: 100%; height: 100%; max-width: none; max-height: none;
    border-radius: 0; border: none; margin: 0; box-shadow: none;
    animation: none;
  }}
</style>

<!-- Inline widget styles (for iframe mode we embed them directly) -->
<style>{widget_css}</style>

</head>
<body>
<div id="cerid-widget-root"></div>

<script>
  // Signal to the widget that we are in full-page (iframe) mode.
  window.__CERID_WIDGET_FULLPAGE__ = true;

  // Configuration from query params or defaults.
  (function() {{
    var params = new URLSearchParams(window.location.search);
    window.ceridChatConfig = {{
      apiUrl: params.get("apiUrl") || "{api_url}",
      clientId: params.get("clientId") || "widget-iframe",
      apiKey: params.get("apiKey") || "",
      theme: params.get("theme") || "auto",
      title: params.get("title") || "{title}",
      placeholder: params.get("placeholder") || "Ask anything...",
      initialMessage: params.get("initialMessage") || "{initial_message}",
    }};
  }})();
</script>

<!-- Load the widget bundle.  In production this points to the built JS.
     During development it loads directly from the Vite dev server. -->
<script src="{widget_js_url}" defer></script>

</body>
</html>
"""


@router.get(
    "/widget.html",
    response_class=HTMLResponse,
    summary="Widget standalone HTML page",
    description=(
        "Self-contained HTML page that renders the Cerid AI chat widget. "
        "Designed for embedding via iframe."
    ),
)
async def widget_page(request: Request) -> HTMLResponse:
    """Serve the widget as a standalone HTML page for iframe embedding."""
    _check_enabled()

    # Derive the API URL from the incoming request
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8888"))
    api_url = f"{scheme}://{host}"

    # Try to load the widget CSS for inline embedding
    widget_css = ""
    css_path = _STATIC_DIR / "styles.css"
    if css_path.is_file():
        widget_css = css_path.read_text(encoding="utf-8")
    else:
        # Fallback: try the source CSS
        src_css = Path(__file__).resolve().parents[3] / "packages" / "widget" / "src" / "styles.css"
        if src_css.is_file():
            widget_css = src_css.read_text(encoding="utf-8")

    # Query param overrides
    params = request.query_params
    title = params.get("title", "Cerid AI")
    theme = params.get("theme", "auto")
    initial_message = params.get("initialMessage", "")

    # Theme-based colors for the page background
    is_dark = theme == "dark"
    bg_color = "#1a1a2e" if is_dark else "#ffffff"
    text_color = "#e5e7eb" if is_dark else "#1a1a2e"

    html = _WIDGET_HTML_TEMPLATE.format(
        title=_escape_html(title),
        bg_color=bg_color,
        text_color=text_color,
        widget_css=widget_css,
        api_url=api_url,
        initial_message=_escape_html(initial_message),
        widget_js_url=f"{api_url}/widget.js",
    )

    return HTMLResponse(
        content=html,
        headers={
            "X-Frame-Options": "ALLOWALL",
            "Content-Security-Policy": (
                "frame-ancestors *; "
                f"script-src 'self' 'unsafe-inline' {api_url}; "
                "style-src 'self' 'unsafe-inline';"
            ),
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape_html(text: str) -> str:
    """Minimal HTML escaping for template interpolation."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
