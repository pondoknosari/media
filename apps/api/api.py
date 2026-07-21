"""NinjaAPI instance for the Agent API.

Lives at ``/api/v1/`` (mounted in ``config/urls.py``). All routes are
behind ``ApiKeyAuth`` — there is no anonymous surface here, not even
``/me``: a bearer is required to learn anything about the workspace.

OpenAPI docs render at ``/api/v1/docs`` (Ninja's default).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError
from ninja.openapi.docs import Swagger

from apps.api.auth import ApiKeyAuth, McpAuth
from apps.api.routers.accounts import router as accounts_router
from apps.api.routers.analytics import router as analytics_router
from apps.api.routers.me import router as me_router
from apps.api.routers.media import router as media_router
from apps.api.routers.posts import router as posts_router
from apps.mcp.transport import router as mcp_router


class NoncedSwagger(Swagger):
    template_cdn = str(settings.BASE_DIR / "templates" / "ninja" / "swagger_cdn.html")


api = NinjaAPI(
    title="Media Pondok Nosari Agent API",
    version="1.0.0",
    description=(
        "Programmatic access for external AI agents. Authentication is via "
        "scoped bearer tokens; create one from the Organization -> API Keys "
        "page in the Media Pondok Nosari settings.\n\n"
        "Rate limits. Per-key write rate is 120/min, read 300/min, "
        "with a 1000/min aggregate cap per workspace. Limits are enforced "
        "as HTTP 429 with a JSON body that includes `tier`, `limit`, "
        "`remaining`, `retry_after`, and a `Retry-After` header. "
        "Headers `X-RateLimit-Limit` and `X-RateLimit-Remaining` are "
        "emitted only on 429 responses, not on every response.\n\n"
        "Per-platform daily caps. Posting against a connected account "
        "is also bounded by a per-SocialAccount 24-hour rolling cap "
        "(e.g. Instagram 25/day, LinkedIn 100/day). Over-quota requests "
        "return 429 with the same error body shape; computed `retry_after` "
        "tells you when the oldest counting row ages out.\n\n"
        "First comments. When a target account's "
        "`supports_first_comment` is `false` (TikTok, Pinterest, Bluesky, "
        "Google Business; LinkedIn Personal in OIDC mode), the "
        "`first_comment` field is silently dropped at publish time -- call "
        "GET /accounts/ or GET /me/ first to check before composing.\n\n"
        "Deleting posts. Posts cannot be deleted via the API in v1. "
        "Cancel a scheduled post with POST /posts/{id}/cancel; remove "
        "drafts from the workspace's drafts list in the web UI. Published "
        "posts are never deletable -- they remain as audit records."
    ),
    auth=ApiKeyAuth(),
    docs=NoncedSwagger(),
    urls_namespace="agent_api_v1",
)

api.add_router("/me", me_router)
api.add_router("/accounts", accounts_router)
api.add_router("/posts", posts_router)
api.add_router("/media", media_router)
api.add_router("/analytics", analytics_router)
api.add_router("/mcp", mcp_router, auth=McpAuth())


@api.exception_handler(HttpError)
def _http_error_handler(request: HttpRequest, exc: HttpError) -> HttpResponse:
    body, headers = (
        _parse_quota_message(exc.message)
        if exc.status_code == 429
        else (
            {"error": _slug_for(exc.status_code), "detail": exc.message},
            {},
        )
    )
    response = JsonResponse(body, status=exc.status_code)
    for k, v in headers.items():
        response[k] = v
    _audit_failed_request(request, status_code=exc.status_code)
    return response


@api.exception_handler(AuthenticationError)
def _authentication_error_handler(request: HttpRequest, exc: AuthenticationError) -> HttpResponse:
    response = JsonResponse(
        {"error": "unauthorized", "detail": "Authentication required."},
        status=401,
    )
    if (request.path or "").rstrip("/").endswith("/api/v1/mcp"):
        metadata_url = f"{settings.MCP_PUBLIC_BASE_URL}/.well-known/oauth-protected-resource/api/v1/mcp"
        response["WWW-Authenticate"] = f'Bearer resource_metadata="{metadata_url}"'
    return response


from django.http import Http404  # noqa: E402 -- keep with the handler


@api.exception_handler(Http404)
def _not_found_handler(request: HttpRequest, exc: Http404) -> HttpResponse:
    _audit_failed_request(request, status_code=404)
    return JsonResponse({"error": "not_found", "detail": "Not found."}, status=404)


from apps.media_library.quotas import StorageQuotaExceededError  # noqa: E402


@api.exception_handler(StorageQuotaExceededError)
def _storage_quota_handler(request: HttpRequest, exc: StorageQuotaExceededError) -> HttpResponse:
    remaining = max(exc.limit - exc.used, 0)
    detail = (
        f"Workspace storage limit reached. Used {exc.used} of {exc.limit} bytes; "
        f"this upload would exceed by {(exc.used + exc.attempted) - exc.limit} bytes."
    )
    body = {
        "error": "storage_quota_exceeded",
        "detail": detail,
        "used_bytes": exc.used,
        "limit_bytes": exc.limit,
        "attempted_bytes": exc.attempted,
    }
    response = JsonResponse(body, status=413)
    response["X-Storage-Used"] = str(exc.used)
    response["X-Storage-Limit"] = str(exc.limit)
    response["X-Storage-Remaining"] = str(remaining)
    _audit_failed_request(request, status_code=413)
    return response


def _audit_failed_request(request: HttpRequest, *, status_code: int) -> None:
    from apps.api.middleware import log_audit_entry
    path = request.path or ""
    action = _action_for_path(request.method or "GET", path, status_code=status_code)
    log_audit_entry(request, action=action, target_id=None, status_code=status_code)


def _action_for_path(method: str, path: str, *, status_code: int) -> str:
    prefixes = [
        ("/analytics/accounts/", f"analytics.read.account.{status_code}"),
        ("/analytics/posts/", f"analytics.read.post.{status_code}"),
        ("/posts/", None),
        ("/media/", None),
        ("/mcp", f"mcp.error.{status_code}"),
        ("/accounts", f"accounts.list.{status_code}"),
        ("/me", f"me.read.{status_code}"),
    ]
    for prefix, label in prefixes:
        if prefix in path or path.endswith(prefix.rstrip("/")):
            if label:
                return label
            if prefix == "/posts/":
                if path.endswith("/schedule"):
                    return f"post.schedule.{status_code}"
                if path.endswith("/cancel"):
                    return f"post.cancel.{status_code}"
                if method == "POST":
                    return f"post.create.{status_code}"
                if method == "GET":
                    return f"post.read.{status_code}"
                if method == "PATCH":
                    return f"post.update.{status_code}"
            if prefix == "/media/":
                if method == "POST":
                    return f"media.upload.{status_code}"
                if method == "GET":
                    return f"media.read.{status_code}"
    return f"unknown.{method.lower()}.{status_code}"


def _slug_for(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "unprocessable_entity",
        429: "rate_limited",
    }.get(status, "error")


def _parse_quota_message(msg: str) -> tuple[dict[str, Any], dict[str, str]]:
    parts: dict[str, Any] = {"error": "rate_limited"}
    for token in msg.split():
        if "=" in token:
            k, v = token.split("=", 1)
            parts[k] = int(v) if v.isdigit() else v
    headers: dict[str, str] = {}
    retry_after = parts.get("retry_after")
    if isinstance(retry_after, int):
        headers["Retry-After"] = str(retry_after)
    limit = parts.get("limit")
    if isinstance(limit, int):
        headers["X-RateLimit-Limit"] = str(limit)
    remaining = parts.get("remaining")
    if isinstance(remaining, int):
        headers["X-RateLimit-Remaining"] = str(remaining)
    return parts, headers
