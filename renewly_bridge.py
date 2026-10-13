"""
renewly_bridge.py - connects Renewly's HTML/CSS/JS frontend to main.py.

How it fits together
--------------------
* main.py stays the ENGINE. MainWindow is still created (hidden) and keeps doing
  everything it does today: renewals.json / settings.json / recycle bin, cloud sync,
  reminder timer, Windows tray notifications, Telegram alerts, update checker, app lock.
* This module starts a tiny HTTP server on 127.0.0.1 that
    - serves the frontend files (index.html, index.css, renderer.js, ...), and
    - exposes a small JSON API (/bridge/...) that reads/writes MainWindow's own data
      through MainWindow's own methods (save_data, refresh_all, recycle, ...).
* The frontend is shown inside main.py in a QWebEngineView window (WebShellWindow),
  and the same address can be opened in Chrome/Edge, which is needed for browser
  Web Push (QtWebEngine has no push service).
* Everything the HTML pages do not (yet) have - the full Settings window, Profile/Account,
  Reset Password, Logout, Notification center, Recycle Bin - is opened from the web UI by
  calling MainWindow's OWN methods, so no existing main.py feature is lost.
* Web Push is sent by the Renewly Cloud backend. Browsers subscribe on the backend's own
  /web/ portal (MainWindow.open_web_push_portal); the bridge only forwards "send test push".

Security
--------
* Binds to 127.0.0.1 only; the Host header must be 127.0.0.1:<port> or localhost:<port>
  (blocks DNS-rebinding).
* Every /bridge/* call needs a random per-launch token (X-Renewly-Token). It is handed
  to the page in the URL once and never stored on disk.
* No CORS headers are sent, and JSON bodies must use Content-Type: application/json.
* Only whitelisted frontend files are served; no directory listing or path traversal.

Public entry point: install_web_frontend(main_window) -> WebShellWindow
"""

from __future__ import annotations

import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# A FIXED port matters: browser push subscriptions belong to the page's origin, so a
# port that changed on every launch would invalidate the subscription each time.
PREFERRED_PORT = int(os.environ.get("RENEWLY_BRIDGE_PORT", "47821"))

# The same route main.py's WebPushTestWorker already uses.
PUSH_TEST_PATH = "/api/push/test"

# Web-UI button name -> MainWindow method that opens the ORIGINAL desktop window.
# Whitelist only: the web page can never call an arbitrary method.
DESKTOP_ACTIONS = {
    "settings": "show_settings",            # full application settings (reminders, security, cloud, backups, ...)
    "profile": "show_profile",              # Profile / Account control centre
    "password": "reset_password",
    "logout": "logout_account",
    "notifications": "show_notifications",  # notification centre
    "recycle": "show_recycle",
    "export": "open_selective_export",
    "web-push-portal": "open_web_push_portal",  # opens {backend}/web/ in the browser to subscribe
}

MAX_BODY_BYTES = 256 * 1024
GUI_CALL_TIMEOUT = 20.0

FREQUENCIES = ("weekly", "monthly", "quarterly", "half-yearly", "yearly", "custom")
# Frontend "frequency"  ->  main.py "billing_cycle"
BILLING_FROM_FREQ = {
    "weekly": "Custom",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "half-yearly": "Half-Yearly",
    "yearly": "Yearly",
    "custom": "Custom",
}
FREQ_FROM_BILLING = {
    "Monthly": "monthly",
    "Quarterly": "quarterly",
    "Half-Yearly": "half-yearly",
    "Yearly": "yearly",
    "Custom": "custom",
}

STATIC_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".ico", ".woff", ".woff2", ".ttf"}
STATIC_NAMES = {"index.css", "webgpu-glass.js", "bridge.js"}


class BridgeError(Exception):
    """An error that is safe to show to the frontend."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------------------
# Pure data mapping (no Qt, easy to test)
# --------------------------------------------------------------------------------------

def _parse_any_date(value: Any) -> str:
    """Return YYYY-MM-DD for a dd-mm-yyyy or ISO date, else ''."""
    text = str(value or "").strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _safe_int(value: Any, default: int) -> int:
    try:
        return max(0, min(3650, int(float(str(value).strip()))))
    except (TypeError, ValueError):
        return default


def _to_local_date(iso_value: str) -> str:
    return datetime.strptime(iso_value, "%Y-%m-%d").strftime("%d-%m-%Y")


def _currency_code(label: Any, fallback: str = "INR") -> str:
    """'INR (₹)' -> 'INR'."""
    match = re.match(r"\s*([A-Za-z]{3})\b", str(label or ""))
    return match.group(1).upper() if match else fallback


def plan_key(plan: dict) -> str:
    """Identifier the frontend uses for a main.py plan."""
    return str(plan.get("web_id") or plan.get("id") or "")


def plan_to_frontend(plan: dict, default_currency: str = "INR") -> dict:
    cycle = str(plan.get("billing_cycle") or "Monthly")
    stored = plan.get("web_frequency")
    # Prefer the exact frontend value, but only while it still agrees with the billing
    # cycle (the cycle may have been changed in the classic UI or by cloud sync).
    if stored in BILLING_FROM_FREQ and BILLING_FROM_FREQ[stored] == cycle:
        frequency = stored
    else:
        frequency = FREQ_FROM_BILLING.get(cycle, "monthly")
    try:
        amount = float(str(plan.get("amount", "0") or "0").replace(",", ""))
    except ValueError:
        amount = 0.0
    if not math.isfinite(amount):
        amount = 0.0
    return {
        "id": plan_key(plan),
        "name": str(plan.get("name") or "Unnamed Plan"),
        "amount": round(amount, 2),
        "currency": str(plan.get("currency") or default_currency).upper(),
        "frequency": frequency,
        "renewalDate": _parse_any_date(plan.get("renewal_date")),
        "notes": str(plan.get("notes") or ""),
        # Fields of the classic Add/Edit Plan dialog.
        "category": str(plan.get("category") or "📦 Other"),
        "icon": str(plan.get("icon_emoji") or "📦"),
        "reminderDays": _safe_int(plan.get("reminder_days"), 3),
        "paymentMethod": str(plan.get("payment_method") or ""),
        "website": str(plan.get("website") or ""),
        "accountEmail": str(plan.get("account_email") or ""),
        "autoRenew": bool(plan.get("auto_renew", False)),
        "paymentStatus": "PAID" if str(plan.get("payment_status") or "").upper() == "PAID" else "PENDING PAYMENT",
        "lastPaymentDate": _parse_any_date(plan.get("last_payment_date")),
    }


def validate_frontend_plan(data: Any, expected_id: str) -> dict:
    if not isinstance(data, dict):
        raise BridgeError("Plan must be a JSON object.")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise BridgeError("Plan name is required (max 80 characters).")
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        raise BridgeError("Amount must be a number.") from None
    if not math.isfinite(amount) or amount <= 0 or amount > 1e9:
        raise BridgeError("Amount must be greater than zero.")
    currency = str(data.get("currency") or "INR").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise BridgeError("Currency must be a 3-letter code.")
    frequency = str(data.get("frequency") or "monthly")
    if frequency not in FREQUENCIES:
        raise BridgeError("Unsupported billing frequency.")
    renewal = _parse_any_date(data.get("renewalDate"))
    if not renewal:
        raise BridgeError("A valid renewal date is required.")
    notes = str(data.get("notes") or "")[:240]
    plan_id = str(data.get("id") or "")
    if plan_id != expected_id or not plan_id or len(plan_id) > 64:
        raise BridgeError("Plan id does not match the request.")
    clean = {
        "id": plan_id, "name": name, "amount": round(amount, 2), "currency": currency,
        "frequency": frequency, "renewalDate": renewal, "notes": notes,
    }
    # Optional classic-dialog fields: only copied when the frontend sent them.
    if "category" in data:
        clean["category"] = str(data.get("category") or "📦 Other")[:40]
    if "icon" in data:
        clean["icon"] = str(data.get("icon") or "📦")[:8]
    if "reminderDays" in data:
        clean["reminderDays"] = _safe_int(data.get("reminderDays"), 3)
    if "paymentMethod" in data:
        clean["paymentMethod"] = str(data.get("paymentMethod") or "").strip()[:40]
    if "website" in data:
        clean["website"] = str(data.get("website") or "").strip()[:200]
    if "accountEmail" in data:
        clean["accountEmail"] = str(data.get("accountEmail") or "").strip()[:120]
    if "autoRenew" in data:
        clean["autoRenew"] = bool(data.get("autoRenew"))
    if "paymentStatus" in data:
        clean["paymentStatus"] = "PAID" if str(data.get("paymentStatus")).upper() == "PAID" else "PENDING PAYMENT"
    if "lastPaymentDate" in data:
        raw = str(data.get("lastPaymentDate") or "").strip()
        last = _parse_any_date(raw) if raw else ""
        if raw and not last:
            raise BridgeError("Last payment date is not a valid date.")
        clean["lastPaymentDate"] = last
    return clean


def apply_frontend_plan(plan: dict, fp: dict) -> None:
    """Copy ONLY the fields the frontend owns; everything else in the plan is untouched."""
    plan["name"] = fp["name"]
    plan["amount"] = f"{fp['amount']:.2f}"
    plan["currency"] = fp["currency"]
    plan["renewal_date"] = _to_local_date(fp["renewalDate"])
    plan["notes"] = fp["notes"]
    plan["billing_cycle"] = BILLING_FROM_FREQ[fp["frequency"]]
    plan["web_frequency"] = fp["frequency"]
    if "category" in fp:
        plan["category"] = fp["category"]
    if "icon" in fp:
        plan["icon_emoji"] = fp["icon"]
    if "reminderDays" in fp:
        plan["reminder_days"] = str(fp["reminderDays"])
    if "paymentMethod" in fp:
        plan["payment_method"] = fp["paymentMethod"]
    if "website" in fp:
        plan["website"] = fp["website"]
    if "accountEmail" in fp:
        plan["account_email"] = fp["accountEmail"]
    if "autoRenew" in fp:
        plan["auto_renew"] = fp["autoRenew"]
    if "paymentStatus" in fp:
        plan["payment_status"] = fp["paymentStatus"]
    if "lastPaymentDate" in fp:
        plan["last_payment_date"] = _to_local_date(fp["lastPaymentDate"]) if fp["lastPaymentDate"] else ""


def new_main_plan(fp: dict, default_reminder_days: Any) -> dict:
    """A complete plan dict with every key main.py's own PlanDialog would produce."""
    plan = {
        "id": int(time.time() * 1000),
        "web_id": fp["id"],
        "reminder_days": str(default_reminder_days if default_reminder_days is not None else 3),
        "category": "📦 Other",
        "icon_emoji": "📦",
        "icon_path": "",
        "payment_method": "",
        "website": "",
        "account_email": "",
        "auto_renew": False,
        "payment_status": "PENDING PAYMENT",
        "last_payment_date": "",
    }
    apply_frontend_plan(plan, fp)
    return plan


# --------------------------------------------------------------------------------------
# Engine: every touch of main.py's state. Must run on the Qt GUI thread.
# --------------------------------------------------------------------------------------

class RenewlyEngine:
    def __init__(self, main_window: Any, host: Any):
        self.main = main_window
        self.host = host  # main.py's module: load_json, save_json, RECYCLE_FILE, ...

    # ---- helpers -------------------------------------------------------------------
    def _default_currency(self) -> str:
        s = self.main.settings
        label = s.get("currency")
        # web_currency only counts while the desktop currency is still the one it was set
        # against; if the desktop Settings window changed it since, the desktop value wins.
        if s.get("web_currency") and s.get("web_currency_base", label) == label:
            return str(s["web_currency"])
        return _currency_code(label) or "INR"

    def _find(self, items: list, key: str) -> dict | None:
        for item in items:
            if isinstance(item, dict) and plan_key(item) == key:
                return item
        return None

    def _after_plan_change(self) -> None:
        self.main.save_data()
        self.main.refresh_all()
        # Ask the existing auto-sync to pick the change up soon (no-op if cloud is off).
        schedule = getattr(self.main, "_schedule_cloud_auto_sync", None)
        if callable(schedule) and getattr(self.main, "_cloud_auto_sync_active", False):
            try:
                schedule(1500)
            except Exception:
                pass

    # ---- state ---------------------------------------------------------------------
    def state(self) -> dict:
        m = self.main
        default_currency = self._default_currency()
        history = getattr(m, "notifications_history", []) or []
        api = getattr(m, "_cloud_api", None)
        return {
            "plans": [plan_to_frontend(p, default_currency) for p in m.plans if isinstance(p, dict)],
            "settings": self._settings_to_frontend(),
            "notifications": [
                {k: n.get(k) for k in ("id", "title", "message", "created_at", "read")}
                for n in history[:50] if isinstance(n, dict)
            ],
            "cloud": bool(api is not None and getattr(api, "is_authenticated", False)),
            "version": getattr(self.host, "APP_VERSION", ""),
            "user": self._user(),
            "meta": {
                "categories": list(getattr(self.host, "PLAN_CATEGORIES", [])),
                "icons": list(getattr(self.host, "PLAN_ICON_CHOICES", [])),
                "defaultReminderDays": _safe_int(m.settings.get("default_reminder_days"), 3),
            },
        }

    def _user(self) -> dict:
        try:
            profile = self.host.load_user() or {}
        except Exception:
            profile = {}
        email = str(getattr(self.main, "_cloud_email", "") or profile.get("email") or "")
        return {"name": str(profile.get("username") or email or "Renewly user"), "email": email}

    def open_desktop(self, name: str) -> dict:
        """Open one of the original desktop windows WITHOUT blocking the HTTP request.

        The Qt dialogs run their own event loop (dialog.exec()), so they are started from a
        zero-delay timer and the request returns immediately.
        """
        method_name = DESKTOP_ACTIONS.get(name)
        method = getattr(self.main, method_name, None) if method_name else None
        if not callable(method):
            raise BridgeError("That desktop window is not available in this version of Renewly.", 404)
        QTimer.singleShot(0, method)
        return {"ok": True}

    def _settings_to_frontend(self) -> dict:
        s = self.main.settings
        return {
            "currency": self._default_currency(),
            "clockFormat": "12" if s.get("web_clock_format") == "12" else "24",
            "showSeconds": bool(s.get("web_show_seconds", False)),
            "theme": self._current_theme(),
        }

    def _current_theme(self) -> str:
        """The theme the Qt side is really using (Theme.mode); settings.json is only a fallback."""
        mode = getattr(getattr(self.host, "Theme", None), "mode", None)
        if mode not in ("dark", "light"):
            mode = self.main.settings.get("theme")
        return "dark" if mode == "dark" else "light"

    # ---- plans ---------------------------------------------------------------------
    def upsert_plan(self, fp: dict) -> dict:
        m = self.main
        key = fp["id"]
        existing = self._find(m.plans, key)
        if existing is not None:
            apply_frontend_plan(existing, fp)
            action = "updated"
        else:
            binned = self._find(m.recycle, key)
            if binned is not None:  # Undo of a delete: bring it back from the recycle bin
                m.recycle.remove(binned)
                apply_frontend_plan(binned, fp)
                m.plans.append(binned)
                self.host.save_json(self.host.RECYCLE_FILE, m.recycle)
                action = "restored"
            else:
                m.plans.append(new_main_plan(fp, m.settings.get("default_reminder_days", 3)))
                action = "created"
        self._after_plan_change()
        return {"ok": True, "action": action}

    def delete_plan(self, key: str) -> dict:
        m = self.main
        plan = self._find(m.plans, key)
        if plan is None:
            return {"ok": True, "action": "already-gone"}
        m._queue_cloud_plan_deletion(plan)  # same offline-safe cloud deletion as the Qt UI
        m.recycle.append(plan)
        m.plans.remove(plan)
        self.host.save_json(self.host.RECYCLE_FILE, m.recycle)
        m.selected_id = None
        self._after_plan_change()
        return {"ok": True, "action": "recycled"}

    # ---- settings ------------------------------------------------------------------
    def save_settings(self, data: Any) -> dict:
        if not isinstance(data, dict):
            raise BridgeError("Settings must be a JSON object.")
        s = self.main.settings
        currency = str(data.get("currency") or "").strip().upper()
        if re.fullmatch(r"[A-Z]{3}", currency):
            s["web_currency"] = currency
            for label in getattr(self.host, "CURRENCY_SYMBOLS", {}):
                if _currency_code(label) == currency:
                    s["currency"] = label
                    break
            s["web_currency_base"] = s.get("currency")
        s["web_clock_format"] = "12" if data.get("clockFormat") == "12" else "24"
        s["web_show_seconds"] = bool(data.get("showSeconds"))
        theme = "dark" if data.get("theme") == "dark" else "light"
        s["theme"] = theme
        self.host.save_app_settings(s)
        try:
            self.host.save_theme(theme)
        except Exception:
            pass
        try:
            # Keep Qt popups (Settings, Profile...) in the same mode as the web UI.
            self.host.Theme.mode = theme
        except Exception:
            pass
        return {"ok": True}

    # ---- misc ----------------------------------------------------------------------
    def cloud_api(self) -> Any:
        api = getattr(self.main, "_cloud_api", None)
        if api is None or not getattr(api, "is_authenticated", False):
            raise BridgeError("Renewly Cloud is not signed in. Please sign in again.", 401)
        return api


# --------------------------------------------------------------------------------------
# GUI-thread marshalling
# --------------------------------------------------------------------------------------

class _Job:
    def __init__(self, fn: Callable[[], Any]):
        self.fn = fn
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class _GuiInvoker(QObject):
    """Runs callables on the thread this object was created in (the Qt GUI thread)."""

    request = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.request.connect(self._run, Qt.QueuedConnection)

    def _run(self, job: _Job) -> None:
        try:
            job.result = job.fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
            job.error = exc
        finally:
            job.done.set()

    def call(self, fn: Callable[[], Any], timeout: float = GUI_CALL_TIMEOUT) -> Any:
        if threading.current_thread() is threading.main_thread():
            return fn()
        job = _Job(fn)
        self.request.emit(job)
        if not job.done.wait(timeout):
            raise BridgeError("Renewly is busy. Please try again.", 503)
        if job.error is not None:
            raise job.error
        return job.result


# --------------------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # never share the port with another process

    def __init__(self, port: int, handler: type, bridge: "BridgeServer"):
        super().__init__(("127.0.0.1", port), handler)
        self.bridge = bridge


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RenewlyBridge"
    sys_version = ""

    def log_message(self, *_args: Any) -> None:  # keep the console quiet
        pass

    # ---- plumbing ------------------------------------------------------------------
    @property
    def bridge(self) -> "BridgeServer":
        return self.server.bridge  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _consume_body(self) -> None:
        """Always read the request body so a keep-alive connection stays in sync.

        (A body left unread would be parsed as the start of the NEXT request.)
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BridgeError("Invalid Content-Length.") from None
        if length < 0:
            raise BridgeError("Invalid Content-Length.")
        if length > MAX_BODY_BYTES:
            raise BridgeError("Request too large.", 413)  # connection is closed by the error path
        self._raw_body = self.rfile.read(length) if length else b""

    def _read_json(self) -> Any:
        raw = getattr(self, "_raw_body", b"")
        if not raw:
            return {}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise BridgeError("Content-Type must be application/json.", 415)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BridgeError("Invalid JSON.") from None

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").strip().lower() in self.bridge.allowed_hosts

    def _token_ok(self) -> bool:
        sent = self.headers.get("X-Renewly-Token") or ""
        return bool(sent) and hmac.compare_digest(sent, self.bridge.token)

    # ---- verbs ---------------------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            self._raw_body = b""
            self._consume_body()
            if not self._host_ok():
                raise BridgeError("Forbidden.", 403)
            path = unquote(urlparse(self.path).path)
            if path.startswith("/bridge/"):
                if not self._token_ok():
                    raise BridgeError("Missing or invalid token.", 401)
                self._json(200, self.bridge.handle_api(method, path, self))
            elif method == "GET":
                self._serve_static(path)
            else:
                raise BridgeError("Method not allowed.", 405)
        except BridgeError as exc:
            self.close_connection = True  # an unread request body must not leak into the next request
            self._json(exc.status, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self.close_connection = True
            self._json(500, {"error": f"Unexpected error: {type(exc).__name__}"})

    # ---- static files --------------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        root = self.bridge.frontend_dir
        if path in ("/", "/index.html"):
            html = (root / "index.html").read_text(encoding="utf-8")
            inject = ('<link rel="stylesheet" href="/index.css">'
                      '<script type="module" src="/bridge.js"></script>')
            html = html.replace("</head>", inject + "</head>", 1)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/app.js":
            # The Electron build bundles the CSS through JS; in the browser it is a <link>.
            source = (root / "renderer.js").read_text(encoding="utf-8")
            source = re.sub(r'^\s*import\s+"\./index\.css";\s*$', "", source, flags=re.M)
            self._send(200, source.encode("utf-8"), "text/javascript; charset=utf-8")
            return
        name = path.lstrip("/")
        file = (root / name).resolve()
        allowed = name in STATIC_NAMES or file.suffix.lower() in STATIC_EXTENSIONS
        if not allowed or root not in file.parents or not file.is_file():
            raise BridgeError("Not found.", 404)
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if file.suffix in (".js", ".css"):
            ctype += "; charset=utf-8"
        self._send(200, file.read_bytes(), ctype)


class BridgeServer:
    def __init__(self, engine: RenewlyEngine, invoker: _GuiInvoker, frontend_dir: Path):
        self.engine = engine
        self.invoker = invoker
        self.frontend_dir = frontend_dir.resolve()
        self.token = secrets.token_urlsafe(32)
        self.shell: "WebShellWindow | None" = None
        self.httpd = self._bind()
        self.port = self.httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.base_url = f"http://127.0.0.1:{self.port}/"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="RenewlyBridge", daemon=True)

    @staticmethod
    def _bind_handler(port: int, server: "BridgeServer") -> _Server:
        return _Server(port, _Handler, server)

    def _bind(self) -> _Server:
        for port in list(range(PREFERRED_PORT, PREFERRED_PORT + 20)) + [0]:
            try:
                return self._bind_handler(port, self)
            except OSError:
                continue
        raise RuntimeError("Could not open a local port for the Renewly bridge.")

    @property
    def app_url(self) -> str:
        return f"{self.base_url}?t={self.token}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass

    # ---- API -----------------------------------------------------------------------
    def handle_api(self, method: str, path: str, req: _Handler) -> Any:
        gui = self.invoker.call
        eng = self.engine
        route = path[len("/bridge"):]

        if method == "GET" and route == "/state":
            return gui(eng.state)

        m = re.fullmatch(r"/plans/([^/]{1,64})", route)
        if m and method == "PUT":
            key = m.group(1)
            fp = validate_frontend_plan(req._read_json(), key)
            return gui(lambda: eng.upsert_plan(fp))
        if m and method == "DELETE":
            key = m.group(1)
            return gui(lambda: eng.delete_plan(key))

        if method == "PUT" and route == "/settings":
            data = req._read_json()
            return gui(lambda: eng.save_settings(data))

        m = re.fullmatch(r"/window/(minimize|toggle-maximize|close)", route)
        if m and method == "POST":
            action = m.group(1)
            gui(lambda: self.shell.window_action(action) if self.shell else None)
            return {"ok": True}

        m = re.fullmatch(r"/desktop/([a-z-]{1,24})", route)
        if m and method == "POST":
            name = m.group(1)
            return gui(lambda: eng.open_desktop(name))

        # ---- Web Push test (same backend route main.py already uses) --------------------
        if method == "POST" and route == "/push/test":
            api = gui(eng.cloud_api)  # raises 401 if not signed in
            result = api._request("POST", PUSH_TEST_PATH, authenticated=True, timeout=30)
            return result if isinstance(result, dict) else {"ok": True}

        raise BridgeError("Not found.", 404)


# --------------------------------------------------------------------------------------
# Window that shows the frontend inside main.py
# --------------------------------------------------------------------------------------

class WebShellWindow(QMainWindow):
    def __init__(self, main_window: Any, url: str):
        super().__init__()
        from PySide6.QtWebEngineWidgets import QWebEngineView  # may be missing -> caller falls back

        self._main = main_window
        self.setWindowTitle(f"{getattr(main_window, 'windowTitle', lambda: 'Renewly')()}")
        try:
            self.setWindowIcon(main_window.windowIcon())
        except Exception:
            pass
        # The frontend draws its own title bar and window buttons (see /bridge/window).
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.view = QWebEngineView(self)
        try:  # hide harmless "Failed to create WebGPU Context Provider" console lines
            from PySide6.QtWebEngineCore import QWebEnginePage

            class _QuietPage(QWebEnginePage):
                def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
                    if "WebGPU" in str(message):
                        return
                    super().javaScriptConsoleMessage(level, message, line, source)

            self.view.setPage(_QuietPage(self.view))
        except Exception:
            pass
        self.setCentralWidget(self.view)
        self.view.setUrl(QUrl(url))

    def force_close(self) -> None:
        self._force = True
        try:
            self.close()
        except RuntimeError:
            pass  # window already deleted by Qt during application shutdown

    def show_window(self) -> None:
        # Same behaviour as the classic window: full screen, taskbar stays reachable via tray.
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def window_action(self, action: str) -> None:
        if action == "minimize":
            self.showMinimized()
        elif action == "toggle-maximize":
            self.showMaximized() if self.isFullScreen() else self.showFullScreen()
        elif action == "close":
            self.close()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        if getattr(self, "_force", False):
            event.accept()
            return
        try:
            allow_real_close = getattr(self._main, "_allow_real_close", False)
            tray = getattr(self._main, "tray_icon", None)
            tray_visible = tray is not None and tray.isVisible()
        except RuntimeError:  # MainWindow already deleted (e.g. after Logout)
            event.accept()
            return
        if not allow_real_close and tray_visible:
            event.ignore()
            self.hide()
            tray.showMessage("Renewly",
                             "The app is still running in the system tray and will continue checking renewals.",
                             QSystemTrayIcon.Information, 5000)
            return
        event.accept()


# --------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------

def _resolve_frontend_dir() -> Path:
    candidates = []
    if os.environ.get("RENEWLY_FRONTEND_DIR"):
        candidates.append(Path(os.environ["RENEWLY_FRONTEND_DIR"]))
    if getattr(sys, "frozen", False):  # PyInstaller one-file / one-dir build
        candidates.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "frontend")
    candidates.append(Path(__file__).resolve().parent / "frontend")
    for path in candidates:
        if (path / "index.html").is_file() and (path / "renderer.js").is_file():
            return path
    raise RuntimeError("Renewly frontend folder not found (expected frontend/index.html and renderer.js).")


def install_web_frontend(main_window: Any) -> WebShellWindow:
    """Start the bridge for an existing MainWindow and return the window to show.

    Raises on any problem so the caller can fall back to the classic Qt window.
    """
    frontend_dir = _resolve_frontend_dir()
    host = sys.modules[type(main_window).__module__]

    invoker = _GuiInvoker()  # created on the GUI thread
    engine = RenewlyEngine(main_window, host)
    server = BridgeServer(engine, invoker, frontend_dir)
    shell = None
    try:
        server.start()
        main_window.setup_system_tray()  # reminders keep running after the window is closed
        shell = WebShellWindow(main_window, server.app_url)
    except Exception:
        server.stop()
        raise
    server.shell = shell

    # The tray's "Open Renewly" ends in main_window.enter_true_fullscreen(); point it at the web window.
    main_window.enter_true_fullscreen = shell.show_window
    main_window._web_bridge = server
    main_window._web_shell = shell

    # Anything that hides the (invisible) MainWindow - tray "Hide Window", Logout - must hide the
    # visible web window too.
    original_hide = main_window.hide

    def hide_both() -> None:
        original_hide()
        shell.hide()

    main_window.hide = hide_both

    # Logout deletes MainWindow and opens the login screen: close the web window and stop the
    # server so the next sign-in starts a clean one.
    def teardown(*_args: Any) -> None:
        try:
            server.stop()
        except Exception:
            pass
        try:
            shell.force_close()
        except RuntimeError:
            pass  # app is quitting and Qt already deleted the shell window

    try:
        main_window.destroyed.connect(teardown)
    except Exception:
        pass
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(server.stop)
    return shell


__all__ = ["install_web_frontend", "plan_to_frontend", "validate_frontend_plan", "BridgeServer",
           "RenewlyEngine", "WebShellWindow"]
