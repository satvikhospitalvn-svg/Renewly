"""
Renewly API Client for Renewly Backend v1.5.

GUI-independent client for main.py integration.
Default development backend: http://127.0.0.1:8000

Security:
- Access tokens are kept in memory by default.
- Never hard-code passwords, JWT secrets, Telegram tokens, or API keys.
- HTTPS certificate verification is always enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse, urlunparse, urlencode
import json
import ssl
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 15.0
API_PREFIX = "/api"


class RenewlyAPIError(Exception):
    """Base Renewly API error."""


class RenewlyConnectionError(RenewlyAPIError):
    """Backend could not be reached."""


class RenewlyHTTPError(RenewlyAPIError):
    """Backend returned an unsuccessful HTTP status."""

    def __init__(self, status_code: int, message: str, *, detail: Any = None,
                 response_body: Any = None):
        self.status_code = status_code
        self.detail = detail
        self.response_body = response_body
        super().__init__(message)


class RenewlyAuthenticationError(RenewlyHTTPError):
    """Authentication or authorization failed."""


@dataclass(slots=True)
class RenewlyUser:
    id: int | None = None
    email: str | None = None
    name: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RenewlyUser":
        return cls(
            id=_safe_int(data.get("id")),
            email=_safe_str(data.get("email")),
            name=_safe_str(data.get("name")),
        )


@dataclass(slots=True)
class RenewlyPlan:
    id: int | None = None
    name: str = ""
    category: str | None = None
    amount: str | float | int | None = None
    currency: str = "INR"
    renewal_date: str | None = None
    status: str = "Active"
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RenewlyPlan":
        return cls(
            id=_safe_int(data.get("id")),
            name=str(data.get("name") or ""),
            category=_safe_str(data.get("category")),
            amount=data.get("amount"),
            currency=str(data.get("currency") or "INR"),
            renewal_date=_safe_str(data.get("renewal_date")),
            status=str(data.get("status") or "Active"),
            notes=_safe_str(data.get("notes")),
            created_at=_safe_str(data.get("created_at")),
            updated_at=_safe_str(data.get("updated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "amount": self.amount,
            "currency": self.currency,
            "renewal_date": self.renewal_date,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class LoginResult:
    access_token: str
    token_type: str = "bearer"


def _safe_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _safe_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _json_or_text(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_detail(payload: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get("detail", payload.get("message", payload.get("error", payload)))
    return payload


def _serialize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass

    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return text


class RenewlyAPI:
    """Synchronous, GUI-independent client for Renewly Backend v1.4."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = "Renewly-Desktop/1.0",
    ):
        self.base_url = self._normalize_base_url(base_url)
        self.timeout = max(1.0, float(timeout))
        self.user_agent = user_agent
        self._access_token: str | None = None
        self._token_type = "bearer"
        self._user: RenewlyUser | None = None

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        value = (base_url or "").strip() or DEFAULT_BASE_URL
        if not value.startswith(("http://", "https://")):
            value = "http://" + value

        parsed = urlparse(value)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("Invalid backend URL or embedded credentials.")

        cleaned = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path.rstrip("/"),
            "", "", ""
        ))
        return cleaned.rstrip("/")

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @property
    def current_user(self) -> RenewlyUser | None:
        return self._user

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access_token)

    def set_access_token(self, access_token: str | None, *, token_type: str = "bearer") -> None:
        token = (access_token or "").strip()
        self._access_token = token or None
        self._token_type = (token_type or "bearer").strip() or "bearer"

    def clear_session(self) -> None:
        self._access_token = None
        self._user = None
        self._token_type = "bearer"

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _ssl_context(self) -> ssl.SSLContext | None:
        if urlparse(self.base_url).scheme != "https":
            return None

        try:
            import truststore
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:
            pass

        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        form_body: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        timeout: float | None = None,
    ) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        data: bytes | None = None

        if json_body is not None:
            data = json.dumps(
                json_body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form_body is not None:
            data = urlencode({
                str(k): str(v)
                for k, v in form_body.items()
                if v is not None
            }).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        if authenticated and self._access_token:
            headers["Authorization"] = f"{self._token_type.capitalize()} {self._access_token}"

        request = urllib.request.Request(
            self._url(path),
            data=data,
            headers=headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout if timeout is None else timeout,
                context=self._ssl_context(),
            ) as response:
                return _json_or_text(response.read())

        except urllib.error.HTTPError as exc:
            payload = _json_or_text(exc.read())
            detail = _extract_detail(payload)

            if exc.code in (401, 403):
                if exc.code == 401:
                    self._access_token = None
                raise RenewlyAuthenticationError(
                    exc.code,
                    f"Backend authentication failed ({exc.code}).",
                    detail=detail,
                    response_body=payload,
                ) from None

            message = (
                f"Backend returned HTTP {exc.code}: {detail}"
                if detail else f"Backend returned HTTP {exc.code}."
            )
            raise RenewlyHTTPError(
                exc.code, message, detail=detail, response_body=payload
            ) from None

        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            raise RenewlyConnectionError(
                f"Could not connect to Renewly Backend at {self.base_url}: {reason}"
            ) from exc

    # -------------------- Health --------------------

    def health(self) -> dict[str, Any]:
        result = self._request("GET", "/", authenticated=False)
        return result if isinstance(result, dict) else {"response": result}

    def is_backend_reachable(self) -> bool:
        try:
            self.health()
            return True
        except RenewlyAPIError:
            return False

    def connection_test(self) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                "base_url": self.base_url,
                "authenticated": self.is_authenticated,
                "data": self.health(),
                "error": None,
            }
        except RenewlyAPIError as exc:
            return {
                "ok": False,
                "base_url": self.base_url,
                "authenticated": self.is_authenticated,
                "data": None,
                "error": str(exc),
            }

    # -------------------- Authentication --------------------

    def start_signup(self, email: str, name: str, password: str) -> dict[str, Any]:
        """Start email-verified account registration and request a six-digit OTP."""
        email = (email or "").strip().lower()
        name = (name or "").strip()
        if not email:
            raise ValueError("Email is required.")
        if not name:
            raise ValueError("Name is required.")
        if not password:
            raise ValueError("Password is required.")

        result = self._request(
            "POST",
            f"{API_PREFIX}/auth/signup/start",
            json_body={"email": email, "name": name, "password": password},
            authenticated=False,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected signup response.")
        return result

    def verify_signup(self, email: str, code: str) -> dict[str, Any]:
        """Verify the six-digit email OTP for a pending signup."""
        email = (email or "").strip().lower()
        code = (code or "").strip()
        if not email:
            raise ValueError("Email is required.")
        if len(code) != 6 or not code.isdigit():
            raise ValueError("Verification code must be exactly 6 digits.")

        result = self._request(
            "POST",
            f"{API_PREFIX}/auth/signup/verify",
            json_body={"email": email, "code": code},
            authenticated=False,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected verification response.")
        return result

    def complete_signup(self, email: str) -> dict[str, Any]:
        """Create the permanent user account after the email OTP is verified."""
        email = (email or "").strip().lower()
        if not email:
            raise ValueError("Email is required.")

        result = self._request(
            "POST",
            f"{API_PREFIX}/auth/signup/complete",
            json_body={"email": email},
            authenticated=False,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected signup completion response.")
        return result

    def register(self, email: str, name: str, password: str) -> RenewlyUser:
        email = (email or "").strip().lower()
        name = (name or "").strip()
        if not email:
            raise ValueError("Email is required.")
        if not name:
            raise ValueError("Name is required.")
        if not password:
            raise ValueError("Password is required.")

        result = self._request(
            "POST",
            f"{API_PREFIX}/auth/register",
            json_body={"email": email, "name": name, "password": password},
            authenticated=False,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected registration response.")

        self._user = RenewlyUser.from_dict(result)
        return self._user

    def login(self, email: str, password: str) -> LoginResult:
        email = (email or "").strip().lower()
        if not email:
            raise ValueError("Email is required.")
        if not password:
            raise ValueError("Password is required.")

        result = self._request(
            "POST",
            f"{API_PREFIX}/auth/login",
            form_body={"username": email, "password": password},
            authenticated=False,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected login response.")

        token = str(result.get("access_token") or "").strip()
        if not token:
            raise RenewlyAPIError("Login succeeded but no access token was returned.")

        token_type = str(result.get("token_type") or "bearer")
        self.set_access_token(token, token_type=token_type)
        return LoginResult(token, token_type)

    # -------------------- Plans --------------------

    @staticmethod
    def _plan_payload(
        *,
        name: str,
        category: str | None = None,
        amount: Any = None,
        currency: str = "INR",
        renewal_date: Any = None,
        status: str = "Active",
        notes: str | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Plan name is required.")

        return {
            "name": name,
            "category": category.strip() if isinstance(category, str) else category,
            "amount": amount,
            "currency": (currency or "INR").strip().upper(),
            "renewal_date": _serialize_date(renewal_date),
            "status": (status or "Active").strip(),
            "notes": notes,
        }

    def list_plans(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        search: str | None = None,
        status: str | None = None,
    ) -> list[RenewlyPlan]:
        params: dict[str, Any] = {
            "skip": max(0, int(skip)),
            "limit": max(1, min(int(limit), 200)),
        }
        if search and search.strip():
            params["search"] = search.strip()
        if status and status.strip():
            params["status"] = status.strip()

        query = urlencode(params)
        result = self._request(
            "GET",
            f"{API_PREFIX}/plans?{query}",
            authenticated=True,
        )

        if isinstance(result, dict) and isinstance(result.get("items"), list):
            result = result["items"]
        if not isinstance(result, list):
            raise RenewlyAPIError("Unexpected plans response.")

        return [RenewlyPlan.from_dict(x) for x in result if isinstance(x, dict)]

    def get_plan(self, plan_id: int) -> RenewlyPlan:
        plan_id = int(plan_id)
        if plan_id <= 0:
            raise ValueError("Invalid plan ID.")

        result = self._request(
            "GET", f"{API_PREFIX}/plans/{plan_id}", authenticated=True
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected plan response.")
        return RenewlyPlan.from_dict(result)

    def create_plan(self, **kwargs: Any) -> RenewlyPlan:
        result = self._request(
            "POST",
            f"{API_PREFIX}/plans",
            json_body=self._plan_payload(**kwargs),
            authenticated=True,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected create-plan response.")
        return RenewlyPlan.from_dict(result)

    def update_plan(self, plan_id: int, **kwargs: Any) -> RenewlyPlan:
        plan_id = int(plan_id)
        if plan_id <= 0:
            raise ValueError("Invalid plan ID.")

        result = self._request(
            "PUT",
            f"{API_PREFIX}/plans/{plan_id}",
            json_body=self._plan_payload(**kwargs),
            authenticated=True,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected update-plan response.")
        return RenewlyPlan.from_dict(result)

    def patch_plan(self, plan_id: int, **fields: Any) -> RenewlyPlan:
        plan_id = int(plan_id)
        if plan_id <= 0:
            raise ValueError("Invalid plan ID.")

        allowed = {
            "name", "category", "amount", "currency",
            "renewal_date", "status", "notes"
        }
        payload = {}
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported plan field: {key}")
            payload[key] = _serialize_date(value) if key == "renewal_date" else value

        if not payload:
            raise ValueError("At least one plan field is required.")

        result = self._request(
            "PATCH",
            f"{API_PREFIX}/plans/{plan_id}",
            json_body=payload,
            authenticated=True,
        )
        if not isinstance(result, dict):
            raise RenewlyAPIError("Unexpected patch-plan response.")
        return RenewlyPlan.from_dict(result)

    def delete_plan(self, plan_id: int) -> bool:
        plan_id = int(plan_id)
        if plan_id <= 0:
            raise ValueError("Invalid plan ID.")

        self._request(
            "DELETE", f"{API_PREFIX}/plans/{plan_id}", authenticated=True
        )
        return True

    def sync_new_local_plans(
        self,
        local_plans: list[Mapping[str, Any]],
    ) -> list[RenewlyPlan]:
        """
        Conservative helper: creates only local plans without a server/backend ID.
        It does not automatically modify or delete existing server plans.
        """
        created: list[RenewlyPlan] = []
        for local in local_plans:
            if local.get("server_id") or local.get("backend_id"):
                continue

            created.append(self.create_plan(
                name=str(local.get("name") or ""),
                category=_safe_str(local.get("category")),
                amount=local.get("amount"),
                currency=str(local.get("currency") or "INR"),
                renewal_date=(
                    local.get("renewal_date")
                    or local.get("next_renewal")
                    or local.get("due_date")
                ),
                status=str(local.get("status") or "Active"),
                notes=_safe_str(local.get("notes")),
            ))
        return created


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "RenewlyAPI",
    "RenewlyAPIError",
    "RenewlyAuthenticationError",
    "RenewlyConnectionError",
    "RenewlyHTTPError",
    "RenewlyPlan",
    "RenewlyUser",
    "LoginResult",
]
