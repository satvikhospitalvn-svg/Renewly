import sys
import os
import base64
import ctypes
from pathlib import Path
import json
import hashlib
import csv
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import urllib.parse
import ssl

try:
    from renewly_api import RenewlyAPI, RenewlyAPIError
except Exception:
    RenewlyAPI = None
    class RenewlyAPIError(Exception):
        pass

# Optional: lets the HTML/CSS/JS frontend run on top of this engine (see renewly_bridge.py).
try:
    from renewly_bridge import install_web_frontend
except Exception:
    install_web_frontend = None

from datetime import datetime, date, timezone

from PySide6.QtCore import QObject, Qt, QTimer, QDate, QEvent, QMimeData, QUrl, QPropertyAnimation, QEasingCurve, QThread, Signal, QSize, QByteArray, QRectF
from PySide6.QtGui import QColor, QPalette, QPainter, QPainterPath, QLinearGradient, QRadialGradient, QFont, QPen, QPixmap, QTextCharFormat, QBrush, QDrag, QDesktopServices, QShortcut, QKeySequence, QIcon, QAction
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFrame, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QHBoxLayout, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialog, QFormLayout, QDialogButtonBox, QMessageBox,
    QSpinBox, QDoubleSpinBox, QDateEdit, QStackedWidget, QGraphicsDropShadowEffect,
    QCalendarWidget, QCheckBox, QFileDialog, QComboBox, QScrollArea, QMenu, QSplitter,
    QColorDialog, QFontComboBox, QProgressBar, QProgressDialog, QToolTip, QTextEdit, QGraphicsOpacityEffect, QSystemTrayIcon, QTabWidget, QSizePolicy, QSizeGrip, QToolButton
)


# ---------------------------------------------------------
# RESPONSIVE WINDOW / SCREEN HELPERS
# ---------------------------------------------------------
def screen_available_geometry(widget=None):
    """Return the available work area of the screen hosting *widget*.
    Qt 6 handles OS DPI scaling; these helpers only size layouts/windows to
    the actual available pixels so small laptops and large monitors both fit.
    """
    screen = None
    if widget is not None:
        try:
            screen = widget.screen()
        except Exception:
            screen = None
        if screen is None:
            try:
                handle = widget.windowHandle()
                screen = handle.screen() if handle else None
            except Exception:
                screen = None
    if screen is None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
    return screen.availableGeometry() if screen else None


def fit_window_to_screen(widget, preferred_width, preferred_height,
                         width_ratio=0.90, height_ratio=0.90,
                         min_width=640, min_height=420):
    """Size a top-level window to its current screen without overflowing it."""
    geometry = screen_available_geometry(widget)
    if geometry is None:
        widget.resize(preferred_width, preferred_height)
        return

    safe_width = max(320, geometry.width() - 24)
    safe_height = max(240, geometry.height() - 24)
    width = min(int(preferred_width), max(320, int(geometry.width() * width_ratio)))
    height = min(int(preferred_height), max(240, int(geometry.height() * height_ratio)))

    # Never request a minimum larger than the current screen can accommodate.
    actual_min_w = min(int(min_width), safe_width)
    actual_min_h = min(int(min_height), safe_height)
    widget.setMinimumSize(actual_min_w, actual_min_h)

    width = max(actual_min_w, min(width, safe_width))
    height = max(actual_min_h, min(height, safe_height))
    widget.resize(width, height)

    # Center on the same monitor as the parent/current window.
    x = geometry.x() + max(0, (geometry.width() - width) // 2)
    y = geometry.y() + max(0, (geometry.height() - height) // 2)
    widget.move(x, y)


def fit_dialog(dlg, preferred_width, preferred_height,
               min_width=420, min_height=300):
    """Responsive dialog sizing; contents remain scrollable where provided."""
    fit_window_to_screen(
        dlg,
        preferred_width,
        preferred_height,
        width_ratio=0.90,
        height_ratio=0.88,
        min_width=min_width,
        min_height=min_height,
    )


def responsive_sidebar_width(total_width):
    """Choose a sidebar width that leaves useful space for the main content."""
    if total_width <= 760:
        return 168
    if total_width <= 980:
        return 190
    if total_width <= 1200:
        return 215
    return min(245, max(215, int(total_width * 0.18)))


APP_NAME = "Renewly"

# Internal Renewly Cloud API endpoint. Keep this out of the user-facing UI.
# For production deployment, replace this with the HTTPS production endpoint.
RENEWLY_BACKEND_URL = "https://renewly-api-4ka9.onrender.com"
APP_TAGLINE = "Stay Ahead of Every Renewal"

# ---------------------------------------------------------
# APPLICATION VERSION & ONLINE UPDATE SYSTEM
# ---------------------------------------------------------
# IMPORTANT: Change this value every time you build a new Renewly EXE.
APP_VERSION = "1.0.22"

GITHUB_REPOSITORY = "satvikhospitalvn-svg/Renewly"
UPDATE_RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/main/version.json"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


def _version_tuple(value):
    """Convert versions such as 1.2.10 into comparable tuples."""
    parts = []
    for item in str(value or "0").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _update_ssl_context():
    """Build a verified TLS context for GitHub updates.

    Prefer the native Windows certificate store when truststore is available,
    then fall back to certifi. Certificate verification is never disabled.
    """
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


def _github_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Renewly-Update-Checker",
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=8, context=_update_ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------
# TELEGRAM MOBILE ALERTS
# ---------------------------------------------------------
def _dpapi_protect(value):
    """Encrypt a small secret with Windows DPAPI for the current Windows user."""
    raw = str(value or "").encode("utf-8")
    if not raw:
        return ""
    if sys.platform != "win32":
        return base64.b64encode(raw).decode("ascii")
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    in_blob = DATA_BLOB(len(raw), in_buf)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("Windows could not protect the Telegram bot token.")
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(value):
    """Decrypt a DPAPI-protected value created by _dpapi_protect."""
    if not value:
        return ""
    if sys.platform != "win32":
        try:
            return base64.b64decode(str(value)).decode("utf-8")
        except Exception:
            return ""
    try:
        encrypted = base64.b64decode(str(value))
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
        in_blob = DATA_BLOB(len(encrypted), in_buf)
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            return ""
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return ""


def _telegram_ssl_context():
    """Build a verified TLS context using certifi when available.

    Some Windows/Python installations fail Telegram HTTPS verification because
    their local CA bundle is incomplete or intercepted by security software.
    certifi provides a current Mozilla CA bundle without disabling certificate
    verification.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def telegram_send_message(bot_token, chat_id, text):
    """Send one Telegram message without disabling HTTPS certificate verification."""
    # BotFather tokens never contain whitespace; remove accidental spaces or
    # line breaks introduced by copy/paste before building the URL.
    token = "".join(str(bot_token or "").split())
    chat = str(chat_id or "").strip()
    if not token or not chat:
        raise ValueError("Telegram Bot Token and Chat ID are required.")
    if len(token) > 256 or len(chat) > 128:
        raise ValueError("Telegram credentials are invalid or too long.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat,
        "text": str(text),
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"User-Agent": "Renewly-Mobile-Alerts", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10, context=_telegram_ssl_context()) as response:
            result = json.loads(response.read().decode("utf-8"))
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            "Telegram HTTPS certificate verification failed. Install/update certifi "
            "with: python -m pip install --upgrade certifi, then restart Renewly. "
            "Certificate verification is intentionally not disabled for security."
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        description = result.get("description", "Telegram rejected the message.") if isinstance(result, dict) else "Telegram returned an invalid response."
        raise RuntimeError(str(description))
    return result


class TelegramAlertWorker(QThread):
    finished_send = Signal(object)

    def __init__(self, bot_token, chat_id, text, marker=None, purpose="alert", parent=None):
        super().__init__(parent)
        self.bot_token = str(bot_token or "")
        self.chat_id = str(chat_id or "")
        self.text = str(text or "")
        self.marker = marker
        self.purpose = purpose

    def run(self):
        try:
            telegram_send_message(self.bot_token, self.chat_id, self.text)
            self.finished_send.emit({"ok": True, "marker": self.marker, "purpose": self.purpose})
        except Exception as exc:
            self.finished_send.emit({"ok": False, "marker": self.marker, "purpose": self.purpose, "error": str(exc)})


class WebPushTestWorker(QThread):
    """Send a real authenticated Web Push test through the Renewly backend."""
    finished_test = Signal(object)

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api

    def run(self):
        try:
            if self.api is None or not getattr(self.api, "is_authenticated", False):
                raise RuntimeError("Renewly Cloud is not authenticated. Please sign in again.")

            # Use the same authenticated API session created by normal Renewly
            # sign-in. The backend then sends the push to the browser subscription
            # registered for this Renewly account.
            result = self.api._request(
                "POST",
                "/api/push/test",
                authenticated=True,
                timeout=30,
            )

            if not isinstance(result, dict):
                raise RuntimeError("The backend returned an unexpected Web Push response.")

            sent = int(result.get("sent", 0) or 0)
            failed = int(result.get("failed", 0) or 0)
            if not result.get("ok") or sent <= 0:
                errors = result.get("errors") or []
                detail = "; ".join(str(x) for x in errors[:3]) if errors else "No active browser push subscription was found."
                raise RuntimeError(
                    f"Web Push was not delivered. Sent: {sent}, Failed: {failed}. {detail}"
                )

            self.finished_test.emit({"ok": True, "sent": sent, "failed": failed})
        except Exception as exc:
            self.finished_test.emit({"ok": False, "error": str(exc)})


class CloudSyncWorker(QThread):
    """Run backend login/synchronization away from the Qt GUI thread.

    Sync is fingerprint based so a change made in the cloud is not immediately
    overwritten by the older local copy. New local plans are uploaded; linked
    plans are compared against the last successful sync snapshot.
    """
    finished_sync = Signal(object)

    def __init__(self, backend_url, email, password, local_plans, api=None, deleted_cloud_ids=None, parent=None):
        super().__init__(parent)
        self.backend_url = str(backend_url or "").strip()
        self.email = str(email or "").strip()
        self.password = str(password or "")
        self.local_plans = [dict(p) for p in (local_plans or []) if isinstance(p, dict)]
        self.deleted_cloud_ids = {int(value) for value in (deleted_cloud_ids or []) if str(value).strip().isdigit() and int(value) > 0}
        self.api = api

    @staticmethod
    def _iso_date(value):
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                pass
        return None

    @staticmethod
    def _local_date(value):
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return datetime.strptime(text, "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            try:
                return datetime.strptime(text, "%d-%m-%Y").strftime("%d-%m-%Y")
            except ValueError:
                return text

    @staticmethod
    def _normalize_amount(value):
        """Normalize local/cloud amounts so fingerprints compare equal.

        Local JSON can contain 123, 123.0, or "123.00" while the API returns
        Decimal values serialized as "123.00". Without normalization those
        equivalent values look different and an unchanged local plan can
        overwrite a newer cloud edit on the next automatic sync.
        """
        text = str(value if value is not None else "").strip().replace(",", "")
        if not text:
            return None
        try:
            from decimal import Decimal, InvalidOperation
            amount = Decimal(text).quantize(Decimal("0.01"))
            return format(amount, "f")
        except (InvalidOperation, ValueError):
            return text

    def _payload(self, plan):
        payment = str(plan.get("payment_status", "PENDING PAYMENT")).upper()
        return {
            "name": str(plan.get("name", "")).strip() or "Unnamed Plan",
            "category": str(plan.get("category", "📦 Other")) or "📦 Other",
            "amount": self._normalize_amount(plan.get("amount", None)),
            "currency": str(plan.get("currency", "INR") or "INR").strip().upper(),
            "renewal_date": self._iso_date(plan.get("renewal_date")),
            "status": "Paid" if payment == "PAID" else "Active",
            "notes": str(plan.get("notes", "") or ""),
        }

    @staticmethod
    def _fingerprint_payload(payload):
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _remote_updated_at(rp):
        """Return a comparable UTC timestamp for the backend plan version."""
        text = str(getattr(rp, "updated_at", "") or "").strip()
        if not text:
            return ""
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        except Exception:
            return text

    def _remote_payload(self, rp):
        return {
            "name": str(rp.name or "").strip() or "Unnamed Plan",
            "category": str(rp.category or "📦 Other") or "📦 Other",
            "amount": self._normalize_amount(rp.amount),
            "currency": str(rp.currency or "INR").strip().upper(),
            "renewal_date": self._iso_date(rp.renewal_date),
            "status": "Paid" if str(rp.status or "").lower() == "paid" else "Active",
            "notes": str(rp.notes or ""),
        }

    def _apply_remote(self, local, rp):
        local["name"] = rp.name or local.get("name", "Unnamed Plan")
        local["category"] = rp.category or local.get("category", "📦 Other")
        local["amount"] = str(rp.amount or "0.00")
        local["currency"] = str(rp.currency or local.get("currency", "INR"))
        local["renewal_date"] = self._local_date(rp.renewal_date)
        local["payment_status"] = "PAID" if str(rp.status or "").lower() == "paid" else "PENDING PAYMENT"
        local["notes"] = rp.notes or ""
        local["cloud_plan_id"] = int(rp.id)
        local["cloud_sync_fingerprint"] = self._fingerprint_payload(self._remote_payload(rp))
        local["cloud_sync_remote_updated_at"] = self._remote_updated_at(rp)

    def run(self):
        try:
            if RenewlyAPI is None:
                raise RuntimeError("renewly_api.py is missing or could not be imported.")
            api = self.api or RenewlyAPI(self.backend_url, timeout=20)
            # If the authenticated API session is already available, reuse its
            # in-memory token. This is what allows automatic sync after the
            # normal Renewly sign-in without a second cloud password prompt.
            if not getattr(api, "is_authenticated", False):
                api.login(self.email, self.password)

            # Backend currently accepts a maximum of 200 plans per request.
            remote = api.list_plans(limit=200)
            remote_by_id = {int(p.id): p for p in remote if p.id is not None}

            # Push intentional local deletions before reconciliation. This prevents
            # stale local cache entries from recreating a plan the user deleted.
            deleted_confirmed = set()
            for deleted_id in self.deleted_cloud_ids:
                if deleted_id not in remote_by_id:
                    deleted_confirmed.add(deleted_id)
                    continue
                api.delete_plan(deleted_id)
                deleted_confirmed.add(deleted_id)

            remote = api.list_plans(limit=200)
            remote_by_id = {int(p.id): p for p in remote if p.id is not None}
            remote_by_name = {}
            for rp in remote:
                key = str(rp.name or "").strip().casefold()
                if key and key not in remote_by_name:
                    remote_by_name[key] = rp

            updated_local = [dict(p) for p in self.local_plans]
            uploaded = 0
            downloaded = 0
            deleted_local = 0

            for local in updated_local:
                cloud_id = local.get("cloud_plan_id")
                has_cloud_id = str(cloud_id or "").strip().isdigit() and int(cloud_id) > 0
                target = remote_by_id.get(int(cloud_id)) if has_cloud_id else None

                # A cloud ID is the identity of an already-linked plan. Never
                # re-link a missing ID by name: that can resurrect a deleted
                # plan or attach it to a different plan with the same name.
                # Name matching is reserved for genuinely new local plans.
                if not has_cloud_id:
                    local_name_key = str(local.get("name", "")).strip().casefold()
                    if local_name_key:
                        target = remote_by_name.get(local_name_key)

                local_payload = self._payload(local)
                local_fp = self._fingerprint_payload(local_payload)
                stored_fp = str(local.get("cloud_sync_fingerprint") or "")

                if target is None:
                    if has_cloud_id:
                        # The backend record was removed. Do not recreate it from
                        # stale desktop cache; the cloud deletion is authoritative.
                        deleted_local += 1
                        continue

                    saved = api.create_plan(**local_payload)
                    local["cloud_plan_id"] = int(saved.id)
                    local["cloud_sync_fingerprint"] = self._fingerprint_payload(self._remote_payload(saved))
                    local["cloud_sync_remote_updated_at"] = self._remote_updated_at(saved)
                    uploaded += 1
                    continue

                remote_payload = self._remote_payload(target)
                remote_fp = self._fingerprint_payload(remote_payload)
                remote_updated_at = self._remote_updated_at(target)
                stored_remote_updated_at = str(local.get("cloud_sync_remote_updated_at") or "").strip()

                # The backend's updated_at is the authoritative version marker.
                # This is important when the cloud copy was edited outside the
                # desktop app (for example through Swagger). A fingerprint can
                # be different because of an unrelated local edit, but a newer
                # remote updated_at must still be pulled down instead of being
                # overwritten by the older local copy.
                remote_is_newer = bool(
                    remote_updated_at
                    and stored_remote_updated_at
                    and remote_updated_at > stored_remote_updated_at
                )

                # Three-way sync decision:
                #   stored_fp = the cloud snapshot from the last successful sync
                #   local_fp  = the current desktop copy
                #   remote_fp = the current backend copy
                # This lets us distinguish a cloud-only change from a local-only
                # change instead of treating every fingerprint mismatch as a local
                # edit.  In particular, an amount changed through Swagger must be
                # downloaded when the desktop copy still matches the last snapshot.
                local_changed = bool(stored_fp and local_fp != stored_fp)
                cloud_changed = bool(stored_fp and remote_fp != stored_fp)

                # If the backend version is newer than the last version we
                # recorded, treat the cloud copy as changed even when an older
                # local fingerprint is unavailable or incomplete. This makes an
                # external Swagger edit (such as amount 649 -> 799) authoritative
                # for an already-linked plan.
                if remote_is_newer:
                    cloud_changed = True

                if not stored_fp and str(cloud_id or "").strip():
                    # Existing linked installs from older builds may not have a
                    # snapshot. Initialize them from the backend so the backend
                    # becomes the baseline for future three-way comparisons.
                    self._apply_remote(local, target)
                    downloaded += 1
                elif cloud_changed and not local_changed:
                    # Cloud-only change. This includes a price changed externally
                    # through Swagger: 649 -> 799 is pulled into the desktop.
                    self._apply_remote(local, target)
                    downloaded += 1
                elif local_changed and not cloud_changed:
                    # Local-only change. Keep the user's desktop edit and upload it.
                    saved = api.update_plan(target.id, **local_payload)
                    local["cloud_plan_id"] = int(saved.id)
                    local["cloud_sync_fingerprint"] = self._fingerprint_payload(self._remote_payload(saved))
                    local["cloud_sync_remote_updated_at"] = self._remote_updated_at(saved)
                    uploaded += 1
                elif cloud_changed and local_changed:
                    # Both sides changed since the last snapshot. Use the backend
                    # version marker when available; this is deterministic and avoids
                    # silently overwriting a newer cloud edit with an older desktop
                    # copy. If no version marker exists, keep the cloud copy as the
                    # safer synchronization baseline.
                    if remote_is_newer or not stored_remote_updated_at:
                        self._apply_remote(local, target)
                        downloaded += 1
                    else:
                        saved = api.update_plan(target.id, **local_payload)
                        local["cloud_plan_id"] = int(saved.id)
                        local["cloud_sync_fingerprint"] = self._fingerprint_payload(self._remote_payload(saved))
                        local["cloud_sync_remote_updated_at"] = self._remote_updated_at(saved)
                        uploaded += 1
                else:
                    # No payload changed. Refresh the linkage metadata without
                    # changing the user's visible plan.
                    local["cloud_plan_id"] = int(target.id)
                    local["cloud_sync_fingerprint"] = remote_fp
                    local["cloud_sync_remote_updated_at"] = remote_updated_at

            # Pull backend plans that have no corresponding local cloud id/name.
            local_cloud_ids = {
                int(p.get("cloud_plan_id")) for p in updated_local
                if str(p.get("cloud_plan_id", "")).isdigit()
            }
            local_names = {
                str(p.get("name", "")).strip().casefold() for p in updated_local
            }
            refreshed = api.list_plans(limit=200)
            for rp in refreshed:
                if rp.id is None or int(rp.id) in local_cloud_ids:
                    continue
                if str(rp.name or "").strip().casefold() in local_names:
                    continue
                local = {
                    "id": int(datetime.now().timestamp() * 1000) + downloaded,
                    "name": rp.name,
                    "renewal_date": self._local_date(rp.renewal_date),
                    "amount": str(rp.amount or "0.00"),
                    "currency": str(rp.currency or "INR"),
                    "reminder_days": "3",
                    "category": rp.category or "📦 Other",
                    "notes": rp.notes or "",
                    "icon_emoji": "📦",
                    "icon_path": "",
                    "billing_cycle": "Yearly",
                    "payment_method": "",
                    "website": "",
                    "account_email": "",
                    "auto_renew": False,
                    "payment_status": "PAID" if str(rp.status).lower() == "paid" else "PENDING PAYMENT",
                    "last_payment_date": "",
                    "cloud_plan_id": int(rp.id),
                    "cloud_sync_fingerprint": self._fingerprint_payload(self._remote_payload(rp)),
                    "cloud_sync_remote_updated_at": self._remote_updated_at(rp),
                }
                updated_local.append(local)
                local_cloud_ids.add(int(rp.id))
                local_names.add(str(rp.name or "").strip().casefold())
                downloaded += 1

            if deleted_local:
                updated_local = [
                    plan for plan in updated_local
                    if not (
                        str(plan.get("cloud_plan_id", "")).strip().isdigit()
                        and int(plan.get("cloud_plan_id")) not in remote_by_id
                    )
                ]

            self.finished_sync.emit({
                "ok": True,
                "api": api,
                "plans": updated_local,
                "uploaded": uploaded,
                "downloaded": downloaded,
                "deleted_cloud_ids": sorted(deleted_confirmed),
                "deleted_local": deleted_local,
                "email": self.email,
            })
        except Exception as exc:
            self.finished_sync.emit({"ok": False, "error": str(exc)})


class OnlineUpdateWorker(QThread):
    finished_check = Signal(object)

    def run(self):
        try:
            # version.json is the primary version source. Add a cache-busting query
            # so another PC never remains stuck on an older CDN-cached version file.
            version_url = f"{UPDATE_VERSION_URL}?_={int(datetime.now().timestamp())}"
            payload = _github_json(version_url)
            if not isinstance(payload, dict):
                raise ValueError("version.json must contain a JSON object")

            # Always have a deterministic direct EXE URL available. This does not
            # depend on the GitHub API being reachable from the user's PC. GitHub
            # redirects this URL to the latest release asset named Renewly.exe.
            payload.setdefault(
                "download_url",
                f"https://github.com/{GITHUB_REPOSITORY}/releases/latest/download/Renewly.exe",
            )
            payload.setdefault("release_url", UPDATE_RELEASES_URL)

            # If the GitHub Release API is reachable, prefer its exact Renewly.exe
            # asset URL. Never select a setup/installer EXE by accident.
            try:
                release = _github_json(UPDATE_API_URL)
                assets = release.get("assets", []) if isinstance(release, dict) else []
                exe_asset = next(
                    (a for a in assets
                     if str(a.get("name", "")).strip().lower() == "renewly.exe"),
                    None,
                )
                if exe_asset and exe_asset.get("browser_download_url"):
                    payload["download_url"] = str(exe_asset["browser_download_url"]).strip()
                payload["release_url"] = release.get("html_url", UPDATE_RELEASES_URL)
                payload["release_version"] = release.get("tag_name", "")
            except Exception:
                # The direct latest-release URL above remains valid when the API is
                # blocked/unreachable, so an API failure must not disable updates.
                pass

            self.finished_check.emit({"ok": True, "data": payload})
        except Exception as exc:
            self.finished_check.emit({"ok": False, "error": str(exc)})


class UpdateDownloadWorker(QThread):
    progress = Signal(int)
    finished_download = Signal(object)

    def __init__(self, download_url, parent=None):
        super().__init__(parent)
        self.download_url = str(download_url)

    def run(self):
        try:
            request = urllib.request.Request(
                self.download_url,
                headers={"User-Agent": "Renewly-Updater"},
            )
            with urllib.request.urlopen(
                request,
                timeout=30,
                context=_update_ssl_context(),
            ) as response:
                total = int(response.headers.get("Content-Length") or 0)
                fd, target = tempfile.mkstemp(prefix="Renewly_Update_", suffix=".exe")
                os.close(fd)
                received = 0
                try:
                    with open(target, "wb") as output:
                        while True:
                            chunk = response.read(1024 * 256)
                            if not chunk:
                                break
                            output.write(chunk)
                            received += len(chunk)
                            if total > 0:
                                self.progress.emit(min(100, int(received * 100 / total)))
                    if received <= 0:
                        raise RuntimeError("The update file was empty")
                    self.progress.emit(100)
                    self.finished_download.emit({"ok": True, "path": target})
                except Exception:
                    try:
                        os.remove(target)
                    except Exception:
                        pass
                    raise
        except Exception as exc:
            self.finished_download.emit({"ok": False, "error": str(exc)})


# ---------------------------------------------------------
# APPLICATION PATHS
# ---------------------------------------------------------
# RESOURCE_DIR contains files bundled with the application (logo, assets, etc.).
# USER_DATA_DIR contains the user's plans and settings.  Keeping these separate
# means updates/reinstalls do not erase the user's data.
if getattr(sys, "frozen", False):
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    LEGACY_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    LEGACY_DIR = RESOURCE_DIR

BASE_DIR = RESOURCE_DIR  # Existing resource lookups continue to work.
LOCAL_APPDATA = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
if LOCAL_APPDATA:
    USER_DATA_DIR = os.path.join(LOCAL_APPDATA, APP_NAME)
else:
    USER_DATA_DIR = os.path.join(str(Path.home()), ".renewly")
os.makedirs(USER_DATA_DIR, exist_ok=True)

def user_data_path(filename):
    return os.path.join(USER_DATA_DIR, filename)

DATA_FILE = user_data_path("renewals.json")
USER_FILE = user_data_path("users.json")
RECYCLE_FILE = user_data_path("recycle_bin.json")
THEME_FILE = user_data_path("theme.json")
SETTINGS_FILE = user_data_path("settings.json")
NOTIFICATIONS_FILE = user_data_path("notifications.json")
APP_ICON_ICO_PATH = os.path.join(RESOURCE_DIR, "renewly.ico")
APP_ICON_PNG_PATH = os.path.join(RESOURCE_DIR, "renewly_logo.png")
# Full Renewly brand logo used inside the application UI (sidebar branding).
# Keep this PNG in the same folder as main.py (or bundle it with the EXE).
APP_LOGO_PNG_PATH = os.path.join(RESOURCE_DIR, "renewly_logo_transparent.png")
PLAN_ICON_DIR = user_data_path("plan_icons")

# Move data from older portable/Python versions beside the app into AppData
# on first run, without overwriting anything already stored there.
def migrate_legacy_user_data():
    file_names = [
        "renewals.json", "users.json", "recycle_bin.json",
        "theme.json", "settings.json", "purchases.json"
    ]
    for name in file_names:
        source = os.path.join(LEGACY_DIR, name)
        target = user_data_path(name)
        if os.path.exists(source) and not os.path.exists(target):
            try:
                shutil.copy2(source, target)
            except Exception:
                pass

    for folder_name in ("plan_icons", "purchase_files", "Custom_Icons"):
        source = os.path.join(LEGACY_DIR, folder_name)
        target = user_data_path(folder_name)
        if os.path.isdir(source) and not os.path.exists(target):
            try:
                shutil.copytree(source, target)
            except Exception:
                pass

migrate_legacy_user_data()

def get_app_icon():
    """Return the Renewly ICO icon for the app window and taskbar.

    The ICO file is preferred because Windows uses it for native window/taskbar
    icons. PNG remains as a fallback for development or missing bundled assets.
    """
    for icon_path in (APP_ICON_ICO_PATH, APP_ICON_PNG_PATH):
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            if not icon.isNull():
                return icon
    return QIcon()

def set_windows_app_identity():
    """Give Renewly its own Windows taskbar/application identity."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Renewly.PlanRenewalReminder.1"
            )
        except Exception:
            pass


def apply_windows_acrylic(hwnd, dark=None):
    """Enable the native Windows 10 Acrylic backdrop for a top-level window."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint32),
                ("AnimationId", ctypes.c_int),
            ]
        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]
        accent = ACCENT_POLICY()
        accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        use_dark = Theme.dark() if dark is None else bool(dark)
        if use_dark:
            alpha, red, green, blue = 150, 12, 22, 37
        else:
            alpha, red, green, blue = 125, 226, 235, 246
        accent.GradientColor = (alpha << 24) | (red << 16) | (green << 8) | blue
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        user32 = ctypes.windll.user32
        set_wca = user32.SetWindowCompositionAttribute
        set_wca.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        set_wca.restype = ctypes.c_int
        return bool(set_wca(ctypes.c_void_p(int(hwnd)), ctypes.byref(data)))
    except Exception:
        return False

PLAN_ICON_CHOICES = ["📦", "🎬", "🎵", "☁️", "💻", "🎮", "🏋️", "📚", "🛒", "💳", "🧠", "🚗", "🏠", "❤️", "⭐"]


# ---------------------------------------------------------
# LUCIDE VECTOR ICONS
# ---------------------------------------------------------
# Renewly uses the official Lucide SVG set for application navigation.
# Icons are rendered by Qt from SVG at runtime, so they stay sharp at every
# DPI/scale.  Colors are applied to the SVG stroke before Qt renders it.
# No Custom_Icons folder or emoji fallback is used by the application UI.
LUCIDE_ICON_DIR = os.path.join(RESOURCE_DIR, "node_modules", "lucide-static", "icons")

# Each icon has a light-mode and dark-mode color.  The hues stay consistent,
# while contrast is automatically adjusted for the active glass theme.
LUCIDE_NAV_ICONS = {
    # Newer, more distinctive Lucide shapes for the Renewly navigation.
    # Light and dark modes intentionally use different hues.
    "add": ("square-plus", "#15803D", "#2DD4BF"),
    "dashboard": ("panels-top-left", "#1D4ED8", "#60A5FA"),
    "analytics": ("chart-pie", "#6D28D9", "#F0ABFC"),
    "assistant": ("wand-sparkles", "#0F766E", "#A3E635"),
    "purchases": ("receipt-text", "#C2410C", "#FBBF24"),
    "calendar": ("calendar-range", "#4338CA", "#22D3EE"),
    "timeline": ("clock-3", "#BE185D", "#FB7185"),
    "delete": ("archive-x", "#B91C1C", "#FF6B6B"),
    "settings": ("settings-2", "#334155", "#E2E8F0"),
    "profile": ("contact-round", "#1D4ED8", "#C4B5FD"),
    "password": ("shield-keyhole", "#7E22CE", "#F0ABFC"),
    "logout": ("door-open", "#9A3412", "#FCD34D"),
    "notification": ("bell-ring", "#1D4ED8", "#67E8F9"),
    "theme_light": ("sun", "#A16207", "#FDE047"),
    "theme_dark": ("moon", "#334155", "#C4B5FD"),
}



def lucide_nav_spec(key):
    return LUCIDE_NAV_ICONS.get(key, ("circle-help", "#475569", "#CBD5E1"))


def lucide_nav_color(key):
    spec = lucide_nav_spec(key)
    return spec[2] if Theme.dark() else spec[1]


def lucide_icon_pixmap(name, color, size=22):
    """Render one Lucide SVG as a transparent, colorized Qt pixmap."""
    try:
        svg_path = os.path.join(LUCIDE_ICON_DIR, f"{name}.svg")
        if not os.path.exists(svg_path):
            return QPixmap()
        svg = Path(svg_path).read_text(encoding="utf-8")
        svg = svg.replace("currentColor", str(color))
        # Keep the Lucide geometry/style identical in both themes.
        # Theme switching changes the actual icon color, not just contrast.
        svg = svg.replace('stroke-width="2"', 'stroke-width="3"')
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            return QPixmap()
        pixmap = QPixmap(int(size), int(size))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        return pixmap
    except Exception:
        return QPixmap()


def lucide_nav_icon(key, size=22):
    """Return a theme-aware Lucide icon for a navigation key."""
    name = lucide_nav_spec(key)[0]
    return lucide_icon_pixmap(name, lucide_nav_color(key), size=size)

# ---------------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------------

def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def create_default_user():
    if not os.path.exists(USER_FILE):
        save_json(USER_FILE, {
            "username": "admin",
            "password": hash_password("admin123"),
            "role": "Administrator"
        })

def load_user():
    return load_json(USER_FILE, {})

def save_user(user):
    save_json(USER_FILE, user)


DEFAULT_SETTINGS = {
    "default_reminder_days": 3,
    "notifications_enabled": True,
    "sound_enabled": False,
    "custom_reminder_time": "09:00",

    # Telegram mobile push alerts. The bot token is stored encrypted with
    # Windows DPAPI; the plaintext token is never written to settings.json.
    "telegram_alerts_enabled": False,
    "telegram_bot_token_protected": "",
    "telegram_chat_id": "",
    "telegram_alert_due_soon": True,
    "telegram_alert_expired": True,
    "telegram_sent_markers": [],
    "date_format": "DD-MM-YYYY",
    "currency": "INR (₹)",
    "show_clock": True,
    "clock_style": "Classic",
    "theme": "light",

    # Settings owned by the HTML frontend (renewly_bridge.py).
    "web_currency": "",
    "web_currency_base": "",
    "web_clock_format": "24",
    "web_show_seconds": False,

    # UI customization
    "background_enabled": True,
    "background_opacity": 24,
    "background_image": "background.png",
    "accent_color": "#4F8CFF",
    "sidebar_color": "#D7E0EE",
    "font_size": 10,
    "font_family": "Segoe UI",
    "theme_preset": "Custom",

    # Dashboard customization
    "dashboard_visible_cards": ["total", "active", "due", "monthly", "upcoming"],
    "dashboard_card_order": ["total", "active", "due", "monthly", "upcoming"],
    "dashboard_density": "comfortable",
    "dashboard_default_page": "dashboard",
    "dashboard_show_summary": True,
    "dashboard_show_search": True,
    "dashboard_show_status_filters": True,
    "dashboard_show_bulk_actions": True,

    # Optional application lock using Windows Hello. Renewly never stores
    # biometric data; Windows performs the verification.
    "app_lock_enabled": False,
    "app_lock_on_startup": True,
    "app_lock_on_minimize": False,
    "app_lock_inactivity_minutes": 0,

    # Optional cloud backend. The Renewly account authenticates Cloud Sync; no separate cloud password is stored.
    "cloud_sync_enabled": False,
    "backend_url": "https://renewly-api-4ka9.onrender.com",
    "cloud_account_email": "",
    "cloud_deleted_plan_ids_by_account": {},
    # Email that owns the local plan cache. This is separate from the last
    # cloud account setting so account switching cannot reuse another account's local plans.
    "local_data_account_email": "",
}

DATE_FORMATS = {
    "DD-MM-YYYY": "dd-MM-yyyy",
    "DD/MM/YYYY": "dd/MM/yyyy",
    "MM/DD/YYYY": "MM/dd/yyyy",
    "YYYY-MM-DD": "yyyy-MM-dd",
}

CURRENCY_SYMBOLS = {
    "INR (₹)": "₹",
    "USD ($)": "$",
    "EUR (€)": "€",
    "GBP (£)": "£",
    "JPY (¥)": "¥",
}

PLAN_CATEGORIES = [
    "📺 Entertainment",
    "☁️ Software / Cloud",
    "🏠 Home",
    "📱 Mobile",
    "💳 Finance",
    "🎓 Education",
    "📦 Other",
]

CATEGORY_COLORS = {
    "📺 Entertainment": "#8B5CF6",
    "☁️ Software / Cloud": "#3B82F6",
    "🏠 Home": "#22C55E",
    "📱 Mobile": "#06B6D4",
    "💳 Finance": "#F59E0B",
    "🎓 Education": "#EC4899",
    "📦 Other": "#64748B",
}

# ---------------- PURCHASES & WARRANTY DATA ----------------
PURCHASES_FILE = user_data_path("purchases.json")
PURCHASE_FILES_DIR = user_data_path("purchase_files")
PURCHASE_CATEGORIES = ["📱 Electronics","💻 Computers","🏠 Home & Furniture","🔧 Appliances","🚗 Vehicle","👕 Clothing","🎮 Gaming","⌚ Accessories","📦 Other"]

def load_purchases():
    try:
        with open(PURCHASES_FILE, "r", encoding="utf-8") as f:
            d=json.load(f); return d if isinstance(d,list) else []
    except Exception: return []

def save_purchases(items):
    os.makedirs(os.path.dirname(PURCHASES_FILE), exist_ok=True)
    with open(PURCHASES_FILE, "w", encoding="utf-8") as f: json.dump(items,f,indent=2,ensure_ascii=False)

def copy_purchase_attachment(path, name, kind):
    if not path or not os.path.exists(path): return ""
    os.makedirs(PURCHASE_FILES_DIR, exist_ok=True)
    safe="".join(x if x.isalnum() or x in "-_ " else "_" for x in name).strip() or "item"
    ext=Path(path).suffix
    out=os.path.join(PURCHASE_FILES_DIR,f"{safe}_{kind}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}")
    shutil.copy2(path,out); return out

def load_app_settings():
    data = load_json(SETTINGS_FILE, {})
    settings = DEFAULT_SETTINGS.copy()
    if isinstance(data, dict):
        settings.update({k: data[k] for k in DEFAULT_SETTINGS if k in data})
    return settings

def save_app_settings(settings):
    clean = DEFAULT_SETTINGS.copy()
    clean.update({k: settings.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS})
    save_json(SETTINGS_FILE, clean)

def load_notifications():
    data = load_json(NOTIFICATIONS_FILE, [])
    return data if isinstance(data, list) else []

def save_notifications(items):
    save_json(NOTIFICATIONS_FILE, items)

def load_theme():
    """Return the last selected appearance, defaulting to the original light glass."""
    return load_json(THEME_FILE, {"mode": "light"}).get("mode", "light")

def save_theme(mode):
    save_json(THEME_FILE, {"mode": mode})


def apply_global_font(settings, root_widget=None):
    """Apply the selected font family and size to the whole application.

    Existing widgets can keep an explicitly assigned font, so we also update
    the complete widget tree when a root widget is supplied.
    """
    app = QApplication.instance()
    family = settings.get("font_family", DEFAULT_SETTINGS["font_family"])
    size = int(settings.get("font_size", DEFAULT_SETTINGS["font_size"]))
    selected_font = QFont(family, size)

    if app is not None:
        app.setFont(selected_font)

    if root_widget is not None:
        root_widget.setFont(selected_font)
        for widget in root_widget.findChildren(QWidget):
            # Update widgets that inherit the application's normal font.
            # Custom bold/heading fonts are preserved below where possible.
            current = widget.font()
            new_font = QFont(current)
            new_font.setFamily(family)
            if current.pointSize() > 0:
                new_font.setPointSize(size)
            else:
                new_font.setPointSize(size)
            widget.setFont(new_font)


class Theme:
    """Small shared palette so every custom-painted glass surface changes together."""
    mode = load_theme()
    accent_color = DEFAULT_SETTINGS["accent_color"]

    @classmethod
    def dark(cls):
        return cls.mode == "dark"

    @classmethod
    def color(cls, light, dark):
        return QColor(dark if cls.dark() else light)

    @classmethod
    def set_accent(cls, color):
        cls.accent_color = color or DEFAULT_SETTINGS["accent_color"]



THEME_PRESET_PALETTES = {
    "Custom": {},
    "Cyber Blue": {"theme":"dark","accent":"#4F8CFF","sidebar":"#101A2B","window":"#08111F","base":"#101C2E","card":"#14243A","text":"#EAF4FF","muted":"#8FA9C7"},
    "Neon Purple": {"theme":"dark","accent":"#A855F7","sidebar":"#1B1230","window":"#130C22","base":"#211536","card":"#291B42","text":"#F4ECFF","muted":"#B8A3D6"},
    "Emerald": {"theme":"dark","accent":"#22C55E","sidebar":"#10261E","window":"#081A14","base":"#10261E","card":"#163329","text":"#E8FFF2","muted":"#9BC9B0"},
    "Sunset Orange": {"theme":"dark","accent":"#F97316","sidebar":"#2B1A12","window":"#211109","base":"#2B1A12","card":"#382016","text":"#FFF1E8","muted":"#D7AE96"},
    "Rose": {"theme":"dark","accent":"#F43F5E","sidebar":"#2D1420","window":"#210B13","base":"#2D1420","card":"#3A1A28","text":"#FFF0F4","muted":"#D9A3B4"},
    "Midnight": {"theme":"dark","accent":"#60A5FA","sidebar":"#0B1020","window":"#060914","base":"#0B1020","card":"#121A30","text":"#EEF4FF","muted":"#98A8C4"},
    "Minimal Light": {"theme":"light","accent":"#2563EB","sidebar":"#E7EDF7","window":"#F5F7FB","base":"#FFFFFF","card":"#FFFFFF","text":"#172033","muted":"#66758C"},
}

def apply_preset_palette(app, settings):
    preset = settings.get("theme_preset", "Custom")
    p = THEME_PRESET_PALETTES.get(preset, {})
    if not p:
        return
    settings["theme"] = p["theme"]
    settings["accent_color"] = p["accent"]
    settings["sidebar_color"] = p["sidebar"]
    Theme.mode = p["theme"]
    Theme.set_accent(p["accent"])
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(p["window"]))
    pal.setColor(QPalette.Base, QColor(p["base"]))
    pal.setColor(QPalette.AlternateBase, QColor(p["card"]))
    pal.setColor(QPalette.Text, QColor(p["text"]))
    pal.setColor(QPalette.WindowText, QColor(p["text"]))
    pal.setColor(QPalette.ButtonText, QColor(p["text"]))
    pal.setColor(QPalette.Highlight, QColor(p["accent"]))
    pal.setColor(QPalette.Link, QColor(p["accent"]))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QWidget {{ color:{p["text"]}; }}
        QDialog, QScrollArea {{ background:{p["window"]}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit {{
            background:{p["base"]}; color:{p["text"]}; border:1px solid {p["accent"]};
            border-radius:8px; padding:6px;
        }}
        QPushButton {{ border-radius:8px; }}
        QToolTip {{
            background:{p["card"]};
            color:{p["text"]};
            border:1px solid {p["accent"]};
            border-radius:7px;
            padding:6px 9px;
            font-size:12px;
            font-weight:700;
        }}
        QHeaderView::section {{ background:{p["card"]}; color:{p["text"]}; }}
    """)


LIGHT_TO_DARK = {
    "#AEB8C9": "#172033", "#B8C2D2": "#202D40", "#9DA8BA": "#101827",
    "#B9C4D4": "#1C293B", "#C7D1E2": "#172033", "#172033": "#F3F7FC",
    "#37445A": "#E4ECF6", "#53627A": "#BAC8D9", "#5E6D83": "#B8C6D7",
    "#66748A": "#C3D0E0", "#6A7890": "#BBC9D9", "#415068": "#D5E0EE",
    "#4B586D": "#E2EAF4", "#4A5870": "#E2EAF4",
    "rgba(255,255,255,210)": "rgba(231,240,255,32)",
    "rgba(255,255,255,230)": "rgba(231,240,255,50)",
    "rgba(255,255,255,240)": "rgba(231,240,255,60)",
    "rgba(255,255,255,185)": "rgba(231,240,255,45)",
    "rgba(255,255,255,170)": "rgba(231,240,255,38)",
    "rgba(255,255,255,145)": "rgba(231,240,255,34)",
    "rgba(255,255,255,115)": "rgba(231,240,255,28)",
    "rgba(255,255,255,112)": "rgba(231,240,255,28)",
    "rgba(255,255,255,100)": "rgba(231,240,255,26)",
    "rgba(255,255,255,92)": "rgba(231,240,255,24)",
    "rgba(246,249,253,78)": "rgba(15,23,42,95)",
    "rgba(180,205,245,150)": "rgba(59,130,246,105)",
}

def apply_theme_to(widget):
    """Recolour regular Qt controls while Glass* widgets repaint from Theme themselves."""
    for item in [widget] + widget.findChildren(QWidget):
        if not hasattr(item, "_light_style"):
            item._light_style = item.styleSheet()
        style = item._light_style
        if Theme.dark():
            for light, dark in LIGHT_TO_DARK.items():
                style = style.replace(light, dark)
        item.setStyleSheet(style)
        item.update()

# ---------------------------------------------------------
# GLASS POPUPS  (Settings, Profile, Notifications, message boxes, ...)
# One shared look so every popup matches the main Renewly window.
# ---------------------------------------------------------
_GLASS_MARK = "/*renewly-glass*/"
_GLASS_END = "/*end-renewly-glass*/"
_GLASS_BAR_H = 44
_GLASS_RADIUS = 1  # truthy flag: frameless popups get a glass edge (corners are rounded by Windows 11 itself)


def _glass_icon_dir():
    path = os.path.join(USER_DATA_DIR, "glass_icons")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _glass_icon(name, stroke):
    """Write a tiny SVG once and return a QSS-safe url() path (or '' on failure)."""
    shapes = {
        "check": '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
        "down": f'<path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="{stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "up": f'<path d="M3.5 10l4.5-4.5L12.5 10" fill="none" stroke="{stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    }
    try:
        file = os.path.join(_glass_icon_dir(), f"{name}_{stroke.strip('#')}.svg")
        if not os.path.exists(file):
            with open(file, "w", encoding="utf-8") as handle:
                handle.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">{shapes[name]}</svg>')
        return "url(" + file.replace("\\", "/") + ")"
    except Exception:
        return "none"


def glass_popup_qss(framed_radius=0):
    """Light/dark glass stylesheet for popups. *framed_radius* > 0 marks a frameless popup (adds a glass edge)."""
    dark = Theme.dark()
    accent = getattr(Theme, "accent_color", None) or "#6C8DFF"
    if dark:
        t = dict(
            text="#F3F7FC", muted="#AAB9CE", faint="#8496AE",
            bg0="rgb(31,43,66)", bg1="rgb(22,32,52)", bg2="rgb(13,21,37)",
            edge="rgba(231,240,255,70)", edge_low="rgba(0,0,0,70)",
            field="rgba(231,240,255,26)", field_hover="rgba(231,240,255,40)",
            btn0="rgba(231,240,255,52)", btn1="rgba(231,240,255,26)", btn_hover="rgba(231,240,255,72)",
            panel="rgba(231,240,255,22)", select="rgba(108,141,255,120)",
            scroll="rgba(231,240,255,70)", scroll_hover="rgba(231,240,255,115)",
            menu="rgba(24,34,54,250)", chev="#DCE6F4",
        )
    else:
        t = dict(
            text="#172033", muted="#5E6D83", faint="#7A89A0",
            bg0="rgb(226,235,250)", bg1="rgb(200,215,241)", bg2="rgb(182,200,232)",
            edge="rgba(255,255,255,210)", edge_low="rgba(105,125,160,90)",
            field="rgba(255,255,255,150)", field_hover="rgba(255,255,255,205)",
            btn0="rgba(255,255,255,205)", btn1="rgba(255,255,255,110)", btn_hover="rgba(255,255,255,235)",
            panel="rgba(255,255,255,105)", select="rgba(76,132,225,115)",
            scroll="rgba(70,90,125,95)", scroll_hover="rgba(70,90,125,150)",
            menu="rgba(238,243,252,252)", chev="#415068",
        )
    check = _glass_icon("check", "#FFFFFF")
    down = _glass_icon("down", t["chev"])
    up = _glass_icon("up", t["chev"])
    radius = (f"border:1px solid {t['edge']};" if framed_radius else "")
    checked_icon = f"image:{check};" if check != "none" else ""
    down_icon = f"image:{down};" if down != "none" else ""
    up_icon = f"image:{up};" if up != "none" else ""
    return f"""{_GLASS_MARK}
QDialog, QMessageBox {{
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {t['bg0']}, stop:0.5 {t['bg1']}, stop:1 {t['bg2']});
    color:{t['text']}; {radius}
}}
QDialog QLabel, QMessageBox QLabel {{ color:{t['text']}; background:transparent; border:none; }}
QDialog QCheckBox, QDialog QRadioButton, QMessageBox QCheckBox {{ color:{t['text']}; background:transparent; spacing:9px; }}
QDialog QGroupBox {{
    background:{t['panel']}; border:1px solid {t['edge']}; border-radius:16px; margin-top:16px; padding:14px 10px 10px 10px; font-weight:800;
}}
QDialog QGroupBox::title {{ subcontrol-origin:margin; left:16px; padding:0 6px; color:{t['muted']}; }}
QDialog QScrollArea, QDialog QAbstractScrollArea {{ background:transparent; border:none; }}
QDialog QScrollArea > QWidget > QWidget {{ background:transparent; }}
QDialog QTabWidget::pane {{ border:1px solid {t['edge']}; border-radius:14px; background:{t['panel']}; top:-1px; }}
QDialog QTabBar::tab {{ background:{t['field']}; color:{t['muted']}; padding:8px 14px; margin-right:4px; border-radius:11px; font-weight:800; }}
QDialog QTabBar::tab:selected {{ background:{accent}; color:#FFFFFF; }}
QDialog QLineEdit, QDialog QTextEdit, QDialog QPlainTextEdit, QDialog QSpinBox, QDialog QDoubleSpinBox, QDialog QDateEdit, QDialog QComboBox,
QMessageBox QLineEdit, QInputDialog QLineEdit {{
    background:{t['field']}; color:{t['text']}; border:1px solid {t['edge']}; border-bottom-color:{t['edge_low']};
    border-radius:12px; padding:7px 11px; selection-background-color:{t['select']}; selection-color:{t['text']};
}}
QDialog QLineEdit:hover, QDialog QTextEdit:hover, QDialog QSpinBox:hover, QDialog QDoubleSpinBox:hover, QDialog QDateEdit:hover, QDialog QComboBox:hover {{ background:{t['field_hover']}; }}
QDialog QLineEdit:focus, QDialog QTextEdit:focus, QDialog QPlainTextEdit:focus, QDialog QSpinBox:focus, QDialog QDoubleSpinBox:focus, QDialog QDateEdit:focus, QDialog QComboBox:focus {{
    border:1px solid {accent}; background:{t['field_hover']};
}}
QDialog QLineEdit:disabled, QDialog QLineEdit[readOnly="true"] {{ color:{t['muted']}; }}
QDialog QSpinBox::up-button, QDialog QDoubleSpinBox::up-button, QDialog QDateEdit::up-button {{
    subcontrol-origin:border; subcontrol-position:top right; width:22px; border:none; background:transparent;
}}
QDialog QSpinBox::down-button, QDialog QDoubleSpinBox::down-button, QDialog QDateEdit::down-button {{
    subcontrol-origin:border; subcontrol-position:bottom right; width:22px; border:none; background:transparent;
}}
QDialog QSpinBox::up-arrow, QDialog QDoubleSpinBox::up-arrow, QDialog QDateEdit::up-arrow {{ {up_icon} width:10px; height:10px; }}
QDialog QSpinBox::down-arrow, QDialog QDoubleSpinBox::down-arrow, QDialog QDateEdit::down-arrow {{ {down_icon} width:10px; height:10px; }}
QDialog QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:center right; width:26px; border:none; background:transparent; }}
QDialog QComboBox::down-arrow {{ {down_icon} width:10px; height:10px; }}
QDialog QComboBox QAbstractItemView, QMenu {{
    background:{t['menu']}; color:{t['text']}; border:1px solid {t['edge']}; border-radius:12px; padding:5px;
    selection-background-color:{t['select']}; selection-color:{t['text']}; outline:0;
}}
QMenu::item {{ padding:7px 18px; border-radius:8px; }}
QMenu::item:selected {{ background:{t['select']}; }}
QDialog QPushButton, QMessageBox QPushButton, QDialogButtonBox QPushButton {{
    background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {t['btn0']}, stop:1 {t['btn1']});
    color:{t['text']}; border:1px solid {t['edge']}; border-bottom-color:{t['edge_low']};
    border-radius:13px; padding:8px 18px; font-weight:800; min-width:64px;
}}
QDialog QPushButton:hover, QMessageBox QPushButton:hover {{ background:{t['btn_hover']}; border-color:{accent}; }}
QDialog QPushButton:pressed, QMessageBox QPushButton:pressed {{ background:{t['field']}; padding-top:10px; padding-bottom:6px; }}
QDialog QPushButton:disabled {{ color:{t['faint']}; background:{t['field']}; }}
QDialog QPushButton:default, QMessageBox QPushButton:default {{
    background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {accent}, stop:1 {accent});
    color:#FFFFFF; border:1px solid rgba(255,255,255,120);
}}
QDialog QPushButton:default:hover, QMessageBox QPushButton:default:hover {{ border:1px solid #FFFFFF; }}
QDialog QCheckBox::indicator, QDialog QRadioButton::indicator {{
    width:19px; height:19px; border-radius:7px; background:{t['field']}; border:1px solid {t['edge']}; border-bottom-color:{t['edge_low']};
}}
QDialog QRadioButton::indicator {{ border-radius:10px; }}
QDialog QCheckBox::indicator:hover, QDialog QRadioButton::indicator:hover {{ border-color:{accent}; }}
QDialog QCheckBox::indicator:checked {{ background:{accent}; border:1px solid {accent}; {checked_icon} }}
QDialog QRadioButton::indicator:checked {{ background:{accent}; border:5px solid {t['field_hover']}; }}
QDialog QListWidget, QDialog QTableWidget, QDialog QTreeWidget, QDialog QTableView, QDialog QListView {{
    background:{t['field']}; color:{t['text']}; border:1px solid {t['edge']}; border-radius:14px; outline:0;
    selection-background-color:{t['select']}; selection-color:{t['text']}; gridline-color:{t['edge_low']};
}}
QDialog QListWidget::item {{ padding:7px 9px; border-radius:9px; }}
QDialog QListWidget::item:selected {{ background:{t['select']}; }}
QDialog QHeaderView::section {{ background:{t['panel']}; color:{t['muted']}; border:none; padding:7px 9px; font-weight:800; }}
QDialog QProgressBar {{ background:{t['field']}; border:1px solid {t['edge']}; border-radius:9px; text-align:center; color:{t['text']}; }}
QDialog QProgressBar::chunk {{ background:{accent}; border-radius:8px; }}
QDialog QScrollBar:vertical {{ background:transparent; width:12px; margin:4px 2px 4px 2px; }}
QDialog QScrollBar:horizontal {{ background:transparent; height:12px; margin:2px 4px 2px 4px; }}
QDialog QScrollBar::handle:vertical {{ background:{t['scroll']}; border-radius:4px; min-height:34px; }}
QDialog QScrollBar::handle:horizontal {{ background:{t['scroll']}; border-radius:4px; min-width:34px; }}
QDialog QScrollBar::handle:hover {{ background:{t['scroll_hover']}; }}
QDialog QScrollBar::add-line, QDialog QScrollBar::sub-line {{ width:0; height:0; background:none; border:none; }}
QDialog QScrollBar::add-page, QDialog QScrollBar::sub-page {{ background:transparent; }}
QToolTip {{ background:{t['menu']}; color:{t['text']}; border:1px solid {t['edge']}; border-radius:8px; padding:6px 9px; }}
{_GLASS_END}
"""


class GlassTitleBar(QWidget):
    """Slim glass title bar used by frameless popups (drag to move, round close button)."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self._dialog = dialog
        dark = Theme.dark()
        text = "#F3F7FC" if dark else "#172033"
        hover = "rgba(239,68,68,200)"
        self.setObjectName("GlassTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(_GLASS_BAR_H)
        self.setStyleSheet(
            f"QWidget#GlassTitleBar {{ background:rgba({'231,240,255,22' if dark else '255,255,255,120'});"
            f" border:none; border-bottom:1px solid rgba({'231,240,255,40' if dark else '255,255,255,170'});"
            f" border-top-left-radius:0px; border-top-right-radius:0px; }}"
            f"QLabel {{ color:{text}; font-weight:800; font-size:12px; background:transparent; border:none; }}"
            f"QToolButton {{ color:{text}; background:transparent; border:none; border-radius:14px; font-size:15px; font-weight:700; }}"
            f"QToolButton:hover {{ background:{hover}; color:#FFFFFF; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 10, 0)
        row.setSpacing(9)
        icon = QLabel()
        pix = dialog.windowIcon().pixmap(18, 18) if not dialog.windowIcon().isNull() else QPixmap()
        if pix.isNull():
            app = QApplication.instance()
            pix = app.windowIcon().pixmap(18, 18) if app and not app.windowIcon().isNull() else QPixmap()
        if not pix.isNull():
            icon.setPixmap(pix)
            row.addWidget(icon)
        self._title = QLabel(dialog.windowTitle())
        row.addWidget(self._title, 1)
        self._close = QToolButton()
        self._close.setText("✕")
        self._close.setFixedSize(28, 28)
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setToolTip("Close")
        self._close.clicked.connect(dialog.reject)
        row.addWidget(self._close)
        dialog.windowTitleChanged.connect(self._title.setText)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                try:
                    handle.startSystemMove()
                    event.accept()
                    return
                except Exception:
                    pass
        super().mousePressEvent(event)


class _GlassDialogGeometry(QObject):
    """Keeps the overlay title bar and size grip pinned while a frameless popup resizes."""

    def __init__(self, dialog, bar, grip):
        super().__init__(dialog)
        self.dialog, self.bar, self.grip = dialog, bar, grip
        self.layout_popup()

    def layout_popup(self):
        w, h = self.dialog.width(), self.dialog.height()
        self.bar.setGeometry(0, 0, w, _GLASS_BAR_H)
        self.bar.raise_()
        self.grip.move(w - self.grip.width() - 6, h - self.grip.height() - 6)
        self.grip.raise_()

    def eventFilter(self, obj, event):
        if obj is self.dialog and event.type() in (QEvent.Resize, QEvent.Show):
            self.layout_popup()
        return False


def _glass_set_dwm_chrome(widget):
    """Windows 11: tint the native title bar/border of framed popups (message boxes) to match."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dark = Theme.dark()
        dwm = ctypes.windll.dwmapi

        def put(attr, value):
            val = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))

        put(20, 1 if dark else 0)                         # immersive dark mode
        caption = 0x00402A1C if dark else 0x00F6E6DC      # COLORREF is 0x00BBGGRR
        text = 0x00FCF7F3 if dark else 0x00332017
        put(35, caption)                                  # caption colour
        put(34, caption)                                  # border colour
        put(36, text)                                     # caption text colour
    except Exception:
        pass


def _glass_round_corners(widget):
    """Windows 11: let DWM round the corners of a frameless popup (no transparency needed)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        value = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 33, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def apply_glass_style(dialog, frameless=False):
    """Set (or refresh) the glass stylesheet block, keeping each dialog's own more-specific rules."""
    if type(dialog).__name__ in _GLASS_SKIP:
        return
    try:
        current = dialog.styleSheet()
        wanted = _GLASS_RADIUS if frameless else 0
        if _GLASS_MARK in current:
            if getattr(dialog, "_glass_radius", None) == wanted:
                return
            start = current.index(_GLASS_MARK)
            end = current.find(_GLASS_END, start)
            current = current[:start] + (current[end + len(_GLASS_END):] if end != -1 else "")
        dialog._glass_radius = wanted
        dialog.setStyleSheet(current + "\n" + glass_popup_qss(wanted))
    except Exception:
        pass


_GLASS_SKIP = {"WelcomeWindow"}


def make_glass_popup(dialog):
    """Turn a plain QDialog into a frameless rounded glass popup with its own title bar."""
    if getattr(dialog, "_glass_done", False):
        return
    dialog._glass_done = True
    dialog._glass_frameless = False
    if (
        type(dialog).__name__ in _GLASS_SKIP
        or not dialog.isWindow()
        or dialog.layout() is None
        or dialog.isVisible()
        or (dialog.windowFlags() & Qt.FramelessWindowHint)
        or os.environ.get("RENEWLY_NATIVE_POPUPS")
    ):
        apply_glass_style(dialog, frameless=False)
        return
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowCloseButtonHint)
    # Solid (not translucent) surface: always visible, even if the stylesheet fails to paint.
    dialog.setAttribute(Qt.WA_StyledBackground, True)
    palette = dialog.palette()
    palette.setColor(QPalette.Window, QColor(22, 32, 52) if Theme.dark() else QColor(200, 215, 241))
    dialog.setPalette(palette)
    dialog.setAutoFillBackground(True)
    dialog._glass_frameless = True
    apply_glass_style(dialog, frameless=True)
    margins = dialog.layout().contentsMargins()
    dialog.layout().setContentsMargins(
        margins.left(), margins.top() + _GLASS_BAR_H, margins.right(), margins.bottom()
    )
    bar = GlassTitleBar(dialog)
    grip = QSizeGrip(dialog)
    grip.setFixedSize(16, 16)
    helper = _GlassDialogGeometry(dialog, bar, grip)
    dialog.installEventFilter(helper)
    dialog._glass_helper = helper


class _GlassPopupFilter(QObject):
    """Application-wide: styles every QDialog/QMessageBox that Python code did not exec() itself."""

    def eventFilter(self, obj, event):
        try:
            if isinstance(obj, QDialog):
                kind = event.type()
                if kind == QEvent.Polish:
                    apply_glass_style(obj, frameless=getattr(obj, "_glass_frameless", False))
                elif kind == QEvent.Show:
                    if getattr(obj, "_glass_helper", None):
                        _glass_round_corners(obj)
                    else:
                        _glass_set_dwm_chrome(obj)
        except Exception:
            pass
        return False


def install_glass_popups(app):
    """Call once after QApplication() is created."""
    app._glass_popup_filter = _GlassPopupFilter(app)
    app.installEventFilter(app._glass_popup_filter)
    original_exec = QDialog.exec

    def glass_exec(self, *args, **kwargs):
        try:
            make_glass_popup(self)
        except Exception:
            pass
        return original_exec(self, *args, **kwargs)

    try:
        QDialog.exec = glass_exec
    except Exception:
        # Without the hook popups still get the glass colours and native frame.
        pass


# ---------------------------------------------------------
# REFERENCE GLASS / NEUMORPHIC VISUAL WIDGETS
# ---------------------------------------------------------

class GlassBackground(QWidget):
    """Gradient background with a subtle transparent artwork overlay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.background_art = QPixmap()
        self.reload_background()

    def reload_background(self):
        settings = load_app_settings()
        image_name = settings.get("background_image", "background.png")
        image_path = image_name if os.path.isabs(str(image_name)) else os.path.join(BASE_DIR, str(image_name))
        self.background_art = QPixmap(image_path)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # Stage 3: leave the window surface translucent so Windows 10 Acrylic
        # can remain visible beneath Renewly's existing liquid-glass UI.
        g = QLinearGradient(0, 0, self.width(), self.height())
        if Theme.dark():
            g.setColorAt(0.00, QColor(23, 32, 51, 72))
            g.setColorAt(0.46, QColor(32, 45, 64, 84))
            g.setColorAt(1.00, QColor(16, 24, 39, 94))
        else:
            g.setColorAt(0.00, QColor(174, 184, 201, 62))
            g.setColorAt(0.46, QColor(184, 194, 210, 72))
            g.setColorAt(1.00, QColor(157, 168, 186, 82))
        p.fillRect(self.rect(), g)

        # Subtle frosted light pools.
        for x, y, w, h, alpha in [
            (-120, -80, 620, 360, 38),
            (self.width() - 470, -40, 560, 360, 45),
            (120, self.height() - 260, 700, 340, 25),
        ]:
            p.setBrush(
                QColor(185, 205, 235, alpha // 2)
                if Theme.dark()
                else QColor(255, 255, 255, alpha)
            )
            p.setPen(Qt.NoPen)
            p.drawEllipse(x, y, w, h)

        # User background artwork appears ONLY in the main content area.
        # The left sidebar stays clean and free from the background image.
        settings = load_app_settings()
        background_enabled = bool(settings.get("background_enabled", True))
        opacity_percent = max(0, min(100, int(settings.get("background_opacity", 24))))

        if background_enabled and not self.background_art.isNull() and opacity_percent > 0:
            sidebar_area_width = 18 + 245 + 18  # left margin + sidebar + spacing
            main_x = min(sidebar_area_width, self.width())
            main_width = max(1, self.width() - main_x - 18)

            target_w = int(main_width * 0.90)
            target_h = int(self.height() * 0.82)
            art = self.background_art.scaled(
                target_w,
                target_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )

            x = main_x + (main_width - art.width()) // 2
            y = (self.height() - art.height()) // 2

            p.save()
            # Prevent any part of the artwork from appearing behind the sidebar.
            p.setClipRect(main_x, 0, main_width + 18, self.height())
            # Opacity is controlled from Settings.
            opacity = opacity_percent / 100.0
            if Theme.dark():
                opacity *= 0.85
            p.setOpacity(opacity)
            p.drawPixmap(x, y, art)
            p.restore()

        # A very soft overlay keeps the artwork blended into the gradient.
        overlay = QLinearGradient(0, 0, self.width(), self.height())
        overlay.setColorAt(0.0, QColor(255, 255, 255, 7) if not Theme.dark() else QColor(10, 20, 35, 6))
        overlay.setColorAt(1.0, QColor(90, 110, 145, 9) if not Theme.dark() else QColor(0, 0, 0, 14))
        p.fillRect(self.rect(), overlay)


class GlassFrame(QFrame):
    """
    Frosted panel with the same raised/inset visual language as the reference:
    pale translucent face, bright top-left edge and soft dark lower-right edge.
    """
    def __init__(self, parent=None, radius=24, alpha=145):
        super().__init__(parent)
        self.radius = radius
        # Stage 1 glass opacity: keep every GlassFrame noticeably translucent.
        # The original alpha values remain accepted by callers, but the visual
        # surface is intentionally reduced so the background can show through.
        self.alpha = max(55, min(110, int(alpha * 0.72)))
        self._glass_hover = False
        self.tint_color = None
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_tint_color(self, color):
        self.tint_color = QColor(color) if color else None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        r = self.rect().adjusted(0, 0, 0, -3) if self._glass_hover else self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)

        # Soft outer lower-right shadow.
        shadow_path = QPainterPath()
        shadow_rect = r.adjusted(3, 7 if self._glass_hover else 4, 3, 7 if self._glass_hover else 4)
        shadow_path.addRoundedRect(shadow_rect, self.radius, self.radius)
        if Theme.dark():
            shadow_color = QColor(2, 6, 23, 165 if self._glass_hover else 105)
        else:
            shadow_color = QColor(67, 78, 98, 70 if self._glass_hover else 45)
        p.fillPath(shadow_path, shadow_color)

        # Frosted face.
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        tint = self.tint_color
        if tint is not None:
            top = QColor(tint)
            mid = QColor(tint)
            bottom = QColor(tint)
            if Theme.dark():
                top.setAlpha(78)
                mid.setAlpha(96)
                bottom.setAlpha(118)
            else:
                top.setAlpha(min(155, self.alpha + 34))
                mid.setAlpha(min(135, self.alpha + 12))
                bottom.setAlpha(max(38, self.alpha - 42))
            gradient.setColorAt(0.0, top)
            gradient.setColorAt(0.52, mid)
            gradient.setColorAt(1.0, bottom)
        elif Theme.dark():
            gradient.setColorAt(0.0, QColor(132, 162, 205, min(78, self.alpha // 2 + (18 if self._glass_hover else 0))))
            gradient.setColorAt(0.52, QColor(35, 53, 77, min(135, self.alpha + 18 + (12 if self._glass_hover else 0))))
            gradient.setColorAt(1.0, QColor(12, 22, 37, max(82, self.alpha - 8)))
        else:
            gradient.setColorAt(0.0, QColor(255, 255, 255, min(145, self.alpha + (18 if self._glass_hover else 6))))
            gradient.setColorAt(0.52, QColor(220, 232, 246, min(118, self.alpha + (8 if self._glass_hover else 0))))
            gradient.setColorAt(1.0, QColor(150, 174, 205, max(28, self.alpha - 52)))
        p.fillPath(path, gradient)

        # Extra translucent sheen: keeps the existing GlassFrame language while
        # making the background clearly visible through the panel.
        sheen = QLinearGradient(0, r.top(), 0, r.bottom())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 42 if not Theme.dark() else 28))
        sheen.setColorAt(0.55, QColor(255, 255, 255, 10 if not Theme.dark() else 6))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, sheen)

        # Liquid-glass depth layer: a broad soft reflection near the upper edge.
        # It is painted inside the rounded path, so it behaves like a real glass
        # surface rather than an opaque gradient.
        reflection = QLinearGradient(0, r.top(), 0, r.top() + max(1, r.height() * 0.42))
        if Theme.dark():
            reflection.setColorAt(0.0, QColor(220, 240, 255, 30 if not self._glass_hover else 42))
            reflection.setColorAt(0.35, QColor(150, 205, 255, 12))
            reflection.setColorAt(1.0, QColor(255, 255, 255, 0))
        else:
            reflection.setColorAt(0.0, QColor(255, 255, 255, 72 if not self._glass_hover else 92))
            reflection.setColorAt(0.35, QColor(255, 255, 255, 26))
            reflection.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, reflection)

        # A very subtle vertical refraction band gives the surface a watery depth.
        refraction = QLinearGradient(r.left(), 0, r.right(), 0)
        if Theme.dark():
            refraction.setColorAt(0.0, QColor(120, 190, 255, 0))
            refraction.setColorAt(0.43, QColor(160, 215, 255, 7))
            refraction.setColorAt(0.50, QColor(255, 255, 255, 13))
            refraction.setColorAt(0.57, QColor(160, 215, 255, 7))
            refraction.setColorAt(1.0, QColor(120, 190, 255, 0))
        else:
            refraction.setColorAt(0.0, QColor(120, 170, 230, 0))
            refraction.setColorAt(0.43, QColor(120, 170, 230, 5))
            refraction.setColorAt(0.50, QColor(255, 255, 255, 16))
            refraction.setColorAt(0.57, QColor(120, 170, 230, 5))
            refraction.setColorAt(1.0, QColor(120, 170, 230, 0))
        p.fillPath(path, refraction)

        # Highlight edge: thin and luminous, not a heavy border.
        if Theme.dark():
            edge = QColor(195, 225, 255, 125 if self._glass_hover else 78)
        else:
            edge = QColor(255, 255, 255, 185 if self._glass_hover else 135)
        p.setPen(QPen(edge, 1.2))
        p.drawPath(path)

        # Gentle inner glass rim.
        inner = self.rect().adjusted(5, 5, -5, -5)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner, max(2, self.radius - 5), max(2, self.radius - 5))
        p.setPen(
            QPen(
                QColor(185, 220, 255, 38) if Theme.dark()
                else QColor(255, 255, 255, 70),
                1,
            )
        )
        p.drawPath(inner_path)

        # Small top-edge glint, similar to light catching a curved water surface.
        glint_rect = QRectF(
            r.left() + r.width() * 0.12,
            r.top() + 1,
            r.width() * 0.76,
            max(2.0, r.height() * 0.16),
        )
        glint = QLinearGradient(
            glint_rect.left(), 0, glint_rect.right(), 0
        )
        glint.setColorAt(0.0, QColor(255, 255, 255, 0))
        glint.setColorAt(0.18, QColor(255, 255, 255, 24 if Theme.dark() else 45))
        glint.setColorAt(0.50, QColor(255, 255, 255, 38 if Theme.dark() else 70))
        glint.setColorAt(0.82, QColor(255, 255, 255, 24 if Theme.dark() else 45))
        glint.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, glint)


class GlassButton(QPushButton):
    """Raised pill control using crisp, colored Lucide vector icons."""
    def __init__(self, text, parent=None, icon_path=None, icon_text=None, compact=False, icon_name=None, icon_color=None):
        super().__init__(text, parent)
        self._icon_text = icon_text or ""
        self._pressed = False
        self._hovered = False
        self._compact = bool(compact)
        self._icon_name = icon_name or ""
        self._icon_color = icon_color or "#64748B"
        self._icon_pixmap = lucide_icon_pixmap(self._icon_name, self._icon_color, 22) if self._icon_name else QPixmap()

        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(text)
        self.setMinimumHeight(36 if self._compact else 38)
        self.setFlat(True)
        self.setStyleSheet(
            "QPushButton { color:#4B586D; background:transparent; border:0; "
            "font-weight:700; padding:5px 10px; font-size:11px; }"
        )

    def mousePressEvent(self, event):
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def set_lucide_icon(self, key):
        self._icon_key = key
        self.setText("")
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._pressed = False
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # A small expansion and upward shift gives the control a tactile
        # "pop out" response without disturbing the surrounding layout.
        r = self.rect().adjusted(0, 0, 0, -3) if self._hovered else self.rect().adjusted(2, 2, -2, -2)
        radius = min(16, r.height() // 2)

        if not self._pressed:
            shadow_alpha = 150 if self._hovered else 90
            p.setBrush(QColor(5, 10, 20, shadow_alpha) if Theme.dark() else QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r.adjusted(2, 5 if self._hovered else 3, 2, 5 if self._hovered else 3), radius, radius)

            g = QLinearGradient(0, 0, 0, self.height())
            accent = QColor(Theme.accent_color)
            if Theme.dark():
                top = QColor(accent); top.setAlpha(54 if self._hovered else 24)
                bottom = QColor(31, 47, 69, 112)
                g.setColorAt(0, top)
                g.setColorAt(1, bottom)
            else:
                top = QColor(255, 255, 255, 125)
                bottom = QColor(accent); bottom.setAlpha(48 if self._hovered else 20)
                g.setColorAt(0, top)
                g.setColorAt(1, bottom)
        else:
            g = QLinearGradient(0, 0, 0, self.height())
            if Theme.dark():
                g.setColorAt(0, QColor(19, 34, 53, 125))
                g.setColorAt(1, QColor(65, 88, 120, 88))
            else:
                g.setColorAt(0, QColor(180, 194, 214, 112))
                g.setColorAt(1, QColor(240, 245, 252, 118))

        p.setBrush(g)
        p.setPen(QPen(QColor(190, 215, 245, 68) if Theme.dark() else QColor(255, 255, 255, 125), 1))
        p.drawRoundedRect(r, radius, radius)

        # Thin liquid-glass reflection inside the button.
        button_sheen = QLinearGradient(0, r.top(), 0, r.top() + max(1, r.height() * 0.55))
        button_sheen.setColorAt(0.0, QColor(255, 255, 255, 30 if Theme.dark() else 48))
        button_sheen.setColorAt(0.45, QColor(255, 255, 255, 7 if Theme.dark() else 14))
        button_sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        button_path = QPainterPath()
        button_path.addRoundedRect(r.adjusted(1, 1, -1, -1), max(1, radius - 1), max(1, radius - 1))
        p.fillPath(button_path, button_sheen)

        if self._hovered:
            p.setPen(QPen(QColor(210, 228, 250, 155) if Theme.dark() else QColor(255, 255, 255, 230), 1.3))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius - 1, radius - 1)

        icon_size = 19 if self._compact else 21
        text_rect = r

        if not self._icon_pixmap.isNull():
            icon_rect_x = r.left() + 11
            icon_rect_y = r.center().y() - icon_size // 2

            # Re-render from the active theme on every paint. This means switching
            # Dark/Light mode immediately updates the icon contrast without rebuilding
            # the sidebar or recreating the button.
            nav_color = None
            for _key, _spec in LUCIDE_NAV_ICONS.items():
                if _spec[0] == self._icon_name:
                    nav_color = _spec[2] if Theme.dark() else _spec[1]
                    break
            if nav_color:
                self._icon_pixmap = lucide_icon_pixmap(self._icon_name, nav_color, 22)

            target = self._icon_pixmap.scaled(
                icon_size, icon_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            p.drawPixmap(icon_rect_x, icon_rect_y, target)
            text_rect = r.adjusted(36 if self._compact else 40, 0, 0, 0)
            alignment = Qt.AlignVCenter | Qt.AlignLeft
        elif self._icon_text:
            icon_rect = r.adjusted(13, 0, 0, 0)
            p.setPen(QPen(QColor(Theme.accent_color) if self._hovered else Theme.color("#53627A", "#C9D8EA")))
            icon_font = QFont(self.font())
            icon_font.setPointSize(16)
            icon_font.setBold(True)
            p.setFont(icon_font)
            p.drawText(icon_rect, Qt.AlignVCenter | Qt.AlignLeft, self._icon_text)
            text_rect = r.adjusted(43, 0, 0, 0)
            alignment = Qt.AlignVCenter | Qt.AlignLeft
        else:
            alignment = Qt.AlignCenter

        p.setPen(Theme.color("#4A5870", "#E2EAF4"))
        font = self.font()
        font.setBold(True)
        p.setFont(font)
        p.drawText(text_rect, alignment, self.text())


class GlassCircleButton(QPushButton):
    """Premium transparent 3D glass circle with an internal popup hover effect.

    The hover effect is painted completely inside the widget bounds so it can never
    be clipped by the dashboard header or its parent layout.
    """
    def __init__(self, text="", parent=None, icon_key=None):
        super().__init__(text, parent)
        self._icon_key = icon_key
        self._hovered = False
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setStyleSheet("QPushButton { background: transparent; border: 0; padding: 0px; }")

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def set_lucide_icon(self, key):
        """Change the Lucide icon key and repaint using the current theme."""
        self._icon_key = key
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # IMPORTANT: keep every visual pixel inside this widget.  The old version
        # used negative top coordinates on hover, which caused the top to be cut.
        base = self.rect().adjusted(4, 4, -4, -4)

        # Internal popup illusion: slightly larger/taller appearance, but never
        # outside the widget rectangle.
        if self._pressed:
            r = base.adjusted(2, 3, -2, 1)
        elif self._hovered:
            r = base.adjusted(0, 0, 0, -2)
        else:
            r = base.adjusted(1, 1, -1, -1)

        diameter = min(r.width(), r.height())
        x = r.center().x() - diameter // 2
        y = r.center().y() - diameter // 2
        circle = r.__class__(x, y, diameter, diameter)

        accent = QColor(Theme.accent_color)

        # Soft depth shadow, deliberately clipped inside the available bounds.
        if not self._pressed:
            shadow = circle.adjusted(2, 3, -2, 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 62 if Theme.dark() else 45))
            p.drawEllipse(shadow)

        # Premium transparent glass body.
        glass = QLinearGradient(circle.left(), circle.top(), circle.right(), circle.bottom())
        if Theme.dark():
            glass.setColorAt(0.0, QColor(255, 255, 255, 42 if self._hovered else 26))
            glass.setColorAt(0.50, QColor(35, 53, 80, 24 if self._hovered else 14))
            glass.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 42 if self._hovered else 24))
        else:
            glass.setColorAt(0.0, QColor(255, 255, 255, 78 if self._hovered else 52))
            glass.setColorAt(0.52, QColor(255, 255, 255, 18 if self._hovered else 10))
            glass.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 34 if self._hovered else 18))

        p.setBrush(glass)
        border = QColor(255, 255, 255, 220 if self._hovered else 155)
        p.setPen(QPen(border, 1.15))
        p.drawEllipse(circle)

        # Fine inner glass edge.
        inner = circle.adjusted(3, 3, -3, -3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 92 if self._hovered else 58), 0.9))
        p.drawEllipse(inner)

        # 3D raised highlight at top-left.
        rim = circle.adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(255, 255, 255, 230 if self._hovered else 165), 1.35))
        p.drawArc(rim, 45 * 16, 150 * 16)

        # Recessed lower-right rim for premium depth.
        p.setPen(QPen(QColor(0, 0, 0, 120 if Theme.dark() else 72), 1.35))
        p.drawArc(rim, 225 * 16, 150 * 16)

        # Controlled glass shine.
        shine = QRadialGradient(circle.left() + diameter * 0.30,
                                circle.top() + diameter * 0.25,
                                diameter * 0.60)
        shine.setColorAt(0.0, QColor(255, 255, 255, 100 if self._hovered else 62))
        shine.setColorAt(0.55, QColor(255, 255, 255, 18 if self._hovered else 8))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(shine)
        p.drawEllipse(circle.adjusted(4, 4, -4, -4))

        # Subtle accent glow on hover, still inside the button.
        if self._hovered:
            glow = QColor(accent)
            glow.setAlpha(85)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(glow, 1.4))
            p.drawEllipse(circle.adjusted(1, 1, -1, -1))

        # Center Lucide icon. It is rendered from SVG each paint so theme
        # switches immediately update its color without relying on emoji glyphs.
        if self._icon_key:
            spec = lucide_nav_spec(self._icon_key)
            icon_name = spec[0]
            icon_color = lucide_nav_color(self._icon_key)
            icon_size = max(16, int(diameter * 0.43))
            pm = lucide_icon_pixmap(icon_name, icon_color, icon_size)
            if not pm.isNull():
                target = pm.rect()
                target.moveCenter(circle.center())
                p.drawPixmap(target, pm)
            if self._icon_key == "notification":
                parent_obj = self.parentWidget()
                unread = getattr(parent_obj, "notifications_history", []) if parent_obj else []
                unread_count = sum(1 for n in unread if not n.get("read")) if isinstance(unread, list) else 0
                if unread_count:
                    dot = max(5, int(diameter * 0.18))
                    dr = circle.adjusted(0, 0, -int(diameter * 0.18), -int(diameter * 0.18))
                    cx, cy = dr.right() - dot // 2, dr.top() + dot // 2
                    p.setPen(QPen(QColor(255, 255, 255, 220), 1))
                    p.setBrush(QColor("#EF4444"))
                    p.drawEllipse(cx - dot // 2, cy - dot // 2, dot, dot)
        else:
            font = QFont(self.font())
            font.setPointSize(15)
            font.setBold(False)
            p.setFont(font)
            p.setPen(QColor(Theme.accent_color) if self._hovered else Theme.color("#172033", "#EAF4FF"))
            p.drawText(circle, Qt.AlignCenter, self.text())


class StatCard(GlassFrame):
    def __init__(self, title, value, accent, parent=None):
        super().__init__(parent, radius=28, alpha=132)
        self.setMinimumSize(0, 108)
        self.setMaximumHeight(165)
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(6)

        # Keep the stat cards clean: no circular icon badge.
        # Center the label/value group so all four cards stay visually balanced.
        self.title = QLabel(title)
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(
            "color:#66748A; font-weight:800; font-size:11px; letter-spacing:1px;"
        )

        self.value = QLabel(str(value))
        self.value.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setStyleSheet(
            f"color:{accent}; font-size:32px; font-weight:900;"
        )

        lay.addStretch(1)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addStretch(1)

    def set_value(self, value):
        """Update the statistic value displayed on the card."""
        self.value.setText(str(value))

    def paintEvent(self, event):
        """Add the pronounced raised bevel used by the sidebar's glass buttons."""
        super().paintEvent(event)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 0, 0, -3) if self._glass_hover else self.rect().adjusted(2, 2, -2, -2)
        radius = self.radius
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)

        # A soft top sheen makes the large card feel raised, rather than flat.
        sheen = QLinearGradient(0, r.top(), 0, r.center().y())
        if Theme.dark():
            sheen.setColorAt(0, QColor(181, 212, 250, 48 if self._glass_hover else 31))
        else:
            sheen.setColorAt(0, QColor(255, 255, 255, 88 if self._glass_hover else 58))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.save()
        p.setClipPath(path)
        p.fillRect(r, sheen)
        p.restore()

        # Split light/dark rim: lit at the top-left and recessed at the bottom-right.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(
            QColor(220, 236, 255, 170 if self._glass_hover else 110)
            if Theme.dark() else QColor(255, 255, 255, 235 if self._glass_hover else 190),
            1.4
        ))
        p.drawArc(r.adjusted(1, 1, -1, -1), 45 * 16, 180 * 16)

        p.setPen(QPen(
            QColor(1, 8, 20, 175 if self._glass_hover else 120)
            if Theme.dark() else QColor(73, 89, 114, 82 if self._glass_hover else 55),
            1.5
        ))
        p.drawArc(r.adjusted(1, 1, -1, -1), 225 * 16, 180 * 16)

    def enterEvent(self, event):
        self._glass_hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glass_hover = False
        self.update()
        super().leaveEvent(event)


class DraggableStatCard(StatCard):
    """A stat card that can be dragged to a new dashboard position."""
    def __init__(self, card_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.card_id = card_id
        self._drag_start = None
        self.setToolTip("Drag this card to rearrange your dashboard")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and event.buttons() & Qt.LeftButton
            and (event.position().toPoint() - self._drag_start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(self.card_id)
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
            self._drag_start = None
            return
        super().mouseMoveEvent(event)


class DashboardCardArea(QWidget):
    """Responsive dashboard card area that changes from 5 columns to a compact grid."""
    def __init__(self, reorder_callback, parent=None):
        super().__init__(parent)
        self.reorder_callback = reorder_callback
        self.setAcceptDrops(True)
        self.layout = QGridLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(12)
        self.cards = {}
        self._columns = 0

    def column_count(self):
        width = max(1, self.width())
        # Five cards on normal desktop, then progressively stack them as space
        # becomes scarce. This avoids horizontal clipping on small laptops.
        if width >= 1080:
            return 5
        if width >= 760:
            return 3
        if width >= 500:
            return 2
        return 1

    def set_cards(self, cards, order, visible_ids):
        self.cards = cards
        self.rebuild(order, visible_ids)

    def rebuild(self, order, visible_ids):
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        visible_order = []
        for card_id in order:
            card = self.cards.get(card_id)
            if card and card_id in visible_ids:
                card.setVisible(True)
                visible_order.append(card_id)

        self._columns = self.column_count()
        for index, card_id in enumerate(visible_order):
            card = self.cards[card_id]
            row, col = divmod(index, self._columns)
            self.layout.addWidget(card, row, col)
            self.layout.setColumnStretch(col, 1)

        # Equal-width columns make the cards fill the available content area.
        for col in range(self._columns):
            self.layout.setColumnStretch(col, 1)

    def resizeEvent(self, event):
        old_columns = self._columns
        super().resizeEvent(event)
        new_columns = self.column_count()
        if old_columns and new_columns != old_columns and self.cards:
            visible = [cid for cid, card in self.cards.items() if card.isVisible()]
            order = [cid for cid in self.cards if cid in visible]
            self.rebuild(order, visible)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() in self.cards:
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() in self.cards:
            event.acceptProposedAction()

    def dropEvent(self, event):
        card_id = event.mimeData().text()
        if card_id not in self.cards:
            return

        visible_widgets = [
            card for card in self.cards.values()
            if isinstance(card, DraggableStatCard) and card.isVisible()
        ]
        visible_widgets.sort(key=lambda w: (w.geometry().top(), w.geometry().left()))

        target_index = len(visible_widgets)
        pos = event.position().toPoint()
        for index, widget in enumerate(visible_widgets):
            center = widget.geometry().center()
            if (pos.y() < center.y()) or (
                abs(pos.y() - center.y()) <= widget.height() // 2
                and pos.x() < center.x()
            ):
                target_index = index
                break

        ordered_visible_ids = [
            w.card_id for w in visible_widgets if w.card_id != card_id
        ]
        target_index = min(target_index, len(ordered_visible_ids))
        ordered_visible_ids.insert(target_index, card_id)
        self.reorder_callback(card_id, ordered_visible_ids)
        event.acceptProposedAction()


class GlassPlanTable(QTableWidget):
    """Modern plan table with polished hover and selection states."""
    NORMAL_ROW_HEIGHT = 42
    HOVER_ROW_HEIGHT = 42

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hovered_row = -1
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.cellEntered.connect(self.set_hovered_row)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Leave:
            self.set_hovered_row(-1, 0)
        return super().eventFilter(watched, event)

    def set_hovered_row(self, row, _column):
        if row == self._hovered_row:
            return
        self._paint_row(self._hovered_row, False)
        self._hovered_row = row
        self._paint_row(row, True)

    def _paint_row(self, row, hovered):
        if row < 0 or row >= self.rowCount():
            return
        self.setRowHeight(row, self.NORMAL_ROW_HEIGHT)
        for column in range(self.columnCount()):
            item = self.item(row, column)
            if item:
                item.setBackground(
                    QColor(255, 255, 255, 42 if hovered else 0)
                    if not Theme.dark() else QColor(255, 255, 255, 18 if hovered else 0)
                )

    def paintEvent(self, event):
        # Let Qt draw the table first, then paint rounded status capsules over
        # the STATUS column for a true pill-style appearance.
        super().paintEvent(event)
        if self.columnCount() < 7:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        status_column = 6
        palettes = {
            "ACTIVE": (QColor("#DCFCE7"), QColor("#166534")),
            "DUE SOON": (QColor("#FEF3C7"), QColor("#92400E")),
            "EXPIRED": (QColor("#FEE2E2"), QColor("#991B1B")),
            "PAID": (QColor("#DCFCE7"), QColor("#166534")),
        }
        if Theme.dark():
            palettes = {
                "ACTIVE": (QColor(22, 101, 52, 190), QColor("#BBF7D0")),
                "DUE SOON": (QColor(120, 53, 15, 190), QColor("#FDE68A")),
                "EXPIRED": (QColor(127, 29, 29, 190), QColor("#FECACA")),
                "PAID": (QColor(22, 101, 52, 190), QColor("#BBF7D0")),
            }
        for row in range(self.rowCount()):
            item = self.item(row, status_column)
            if not item:
                continue
            status = item.text().replace("● ", "")
            if status not in palettes:
                continue
            rect = self.visualItemRect(item).adjusted(10, 8, -10, -8)
            bg, fg = palettes[status]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            painter.setPen(fg)
            font = painter.font()
            font.setBold(True)
            font.setPointSize(max(8, font.pointSize()))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, item.text())
        painter.end()


def plan_display_name(plan):
    """Return a compact visual name with the plan's selected built-in icon."""
    icon = str(plan.get("icon_emoji", "📦") or "📦")
    name = str(plan.get("name", ""))
    return f"{icon}  {name}"


class PlanDialog(QDialog):
    def __init__(self, plan=None, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.app_settings = load_app_settings()
        self.setWindowTitle("Edit Plan" if plan else "Add New Plan")
        self.setMinimumSize(760, 520)

        # Use the available screen area so the dialog never extends behind the
        # Windows taskbar. The form itself is compact and horizontal.
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if available:
            width = min(900, max(760, available.width() - 80))
            height = min(680, max(520, available.height() - 70))
        else:
            width, height = 820, 600
        self.resize(width, height)
        self.setMaximumSize(width, height)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # Header
        header = QFrame()
        header.setObjectName("planDialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 8, 15, 8)
        header_layout.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(1)
        title = QLabel("Edit Plan" if plan else "Add New Plan")
        title.setObjectName("planDialogTitle")
        subtitle = QLabel(
            "Update your renewal details" if plan else
            "Add a subscription or plan to your Renewly account"
        )
        subtitle.setObjectName("planDialogSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        outer.addWidget(header)

        # Scrollable compact horizontal form. Two columns keep the dialog short
        # and make the available fields much easier to scan.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("planDialogScroll")
        outer.addWidget(scroll, 1)

        form_widget = QWidget()
        form_widget.setObjectName("planDialogForm")
        grid = QGridLayout(form_widget)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        scroll.setWidget(form_widget)
        self._form = grid

        def field_label(text):
            label = QLabel(text)
            label.setObjectName("planFieldLabel")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            return label

        def add_field(row, col, label_text, widget, label_width=None):
            wrapper = QWidget()
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(3)
            layout.addWidget(field_label(label_text))
            layout.addWidget(widget)
            grid.addWidget(wrapper, row, col)
            return wrapper

        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Netflix, Adobe Creative Cloud")

        self.renewal = QDateEdit()
        self.renewal.setCalendarPopup(True)
        self.renewal.setDisplayFormat(DATE_FORMATS.get(self.app_settings.get("date_format"), "dd-MM-yyyy"))

        self.amount = QDoubleSpinBox()
        self.amount.setMaximum(999999999)
        self.amount.setPrefix(CURRENCY_SYMBOLS.get(self.app_settings.get("currency"), "₹") + " ")
        self.amount.setDecimals(2)

        self.reminder = QSpinBox()
        self.reminder.setMaximum(3650)
        self.reminder.setValue(int(self.app_settings.get("default_reminder_days", 3)))

        self.category = QComboBox()
        self.category.addItems(PLAN_CATEGORIES)

        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Optional notes")

        # Plan visual identity
        self.icon_choice = QComboBox()
        self.icon_choice.addItems(PLAN_ICON_CHOICES)
        self.icon_choice.setToolTip("Choose a built-in icon for this plan")
        self.icon_path = QLineEdit()
        self.icon_path.setReadOnly(True)
        self.icon_path.setPlaceholderText("No custom logo selected")
        self.icon_browse = QPushButton("🖼  Upload Logo")
        self.icon_clear = QPushButton("✕")
        self.icon_browse.clicked.connect(self.choose_plan_logo)
        self.icon_clear.clicked.connect(lambda: self.icon_path.setText(""))

        # Payment/subscription details
        self.billing_cycle = QComboBox()
        self.billing_cycle.addItems(["Monthly", "Quarterly", "Half-Yearly", "Yearly", "Custom"])

        self.payment_method = QComboBox()
        self.payment_method.setEditable(True)
        self.payment_method.addItems(["", "Credit Card", "Debit Card", "UPI", "Bank Transfer", "Cash", "Other"])

        self.website = QLineEdit()
        self.website.setPlaceholderText("https://example.com")

        self.account_email = QLineEdit()
        self.account_email.setPlaceholderText("Optional account/email")

        self.auto_renew = QCheckBox("Automatically renew this plan")
        self.auto_renew.setObjectName("autoRenew")

        self.payment_status = QComboBox()
        self.payment_status.addItems(["PENDING PAYMENT", "PAID"])

        self.last_payment = QDateEdit()
        self.last_payment.setCalendarPopup(True)
        self.last_payment.setDisplayFormat(DATE_FORMATS.get(self.app_settings.get("date_format"), "dd-MM-yyyy"))
        self.last_payment.setDate(QDate.currentDate())

        self.last_payment_known = QCheckBox("Known")

        controls = (
            self.name, self.renewal, self.amount, self.reminder, self.category,
            self.icon_choice, self.notes, self.billing_cycle, self.payment_method,
            self.website, self.icon_path, self.account_email, self.last_payment,
            self.payment_status,
        )
        for w in controls:
            w.setMinimumHeight(34)
            w.setMaximumHeight(36)

        # Main fields in two columns.
        add_field(0, 0, "Plan Name", self.name)
        add_field(0, 1, "Renewal Date", self.renewal)
        add_field(1, 0, "Amount", self.amount)
        add_field(1, 1, "Reminder Days", self.reminder)
        add_field(2, 0, "Category", self.category)
        add_field(2, 1, "Notes", self.notes)

        # Icon/logo row spans the full dialog width.
        icon_label = field_label("Plan Icon / Logo")
        icon_row = QWidget()
        icon_layout = QHBoxLayout(icon_row)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(7)
        self.icon_choice.setFixedWidth(115)
        self.icon_browse.setFixedWidth(120)
        self.icon_clear.setFixedWidth(42)
        icon_layout.addWidget(self.icon_choice)
        icon_layout.addWidget(self.icon_path, 1)
        icon_layout.addWidget(self.icon_browse)
        icon_layout.addWidget(self.icon_clear)
        icon_wrapper = QWidget()
        icon_wrapper_layout = QVBoxLayout(icon_wrapper)
        icon_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        icon_wrapper_layout.setSpacing(3)
        icon_wrapper_layout.addWidget(icon_label)
        icon_wrapper_layout.addWidget(icon_row)
        grid.addWidget(icon_wrapper, 3, 0, 1, 2)

        # Section separator.
        details_title = QLabel("💳  PAYMENT & SUBSCRIPTION DETAILS")
        details_title.setObjectName("planSectionTitle")
        details_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(details_title, 4, 0, 1, 2)

        add_field(5, 0, "Billing Cycle", self.billing_cycle)
        add_field(5, 1, "Payment Method", self.payment_method)
        add_field(6, 0, "Website", self.website)
        add_field(6, 1, "Account / Email", self.account_email)

        auto_wrapper = QWidget()
        auto_layout = QVBoxLayout(auto_wrapper)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        auto_layout.setSpacing(3)
        auto_layout.addWidget(field_label("Auto-renew"))
        auto_layout.addWidget(self.auto_renew)
        grid.addWidget(auto_wrapper, 7, 0)

        add_field(7, 1, "Payment Status", self.payment_status)

        payment_wrapper = QWidget()
        payment_layout = QHBoxLayout(payment_wrapper)
        payment_layout.setContentsMargins(0, 0, 0, 0)
        payment_layout.setSpacing(8)
        payment_layout.addWidget(self.last_payment, 1)
        payment_layout.addWidget(self.last_payment_known)
        last_payment_outer = QWidget()
        last_payment_layout = QVBoxLayout(last_payment_outer)
        last_payment_layout.setContentsMargins(0, 0, 0, 0)
        last_payment_layout.setSpacing(3)
        last_payment_layout.addWidget(field_label("Last Payment"))
        last_payment_layout.addWidget(payment_wrapper)
        grid.addWidget(last_payment_outer, 8, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        if plan:
            self.name.setText(str(plan.get("name", "")))
            try:
                dt = datetime.strptime(plan.get("renewal_date", ""), "%d-%m-%Y").date()
                self.renewal.setDate(QDate(dt.year, dt.month, dt.day))
            except Exception:
                self.renewal.setDate(QDate.currentDate())
            try:
                self.amount.setValue(float(plan.get("amount", 0)))
            except Exception:
                pass
            self.reminder.setValue(int(plan.get("reminder_days", 3)))
            saved_category = str(plan.get("category", "📦 Other"))
            self.category.setCurrentText(saved_category if saved_category in PLAN_CATEGORIES else "📦 Other")
            self.notes.setText(str(plan.get("notes", "")))
            saved_icon = str(plan.get("icon_emoji", "📦"))
            self.icon_choice.setCurrentText(saved_icon if saved_icon in PLAN_ICON_CHOICES else "📦")
            self.icon_path.setText(str(plan.get("icon_path", "")))
            self.billing_cycle.setCurrentText(str(plan.get("billing_cycle", "Yearly")))
            self.payment_method.setCurrentText(str(plan.get("payment_method", "")))
            self.website.setText(str(plan.get("website", "")))
            self.account_email.setText(str(plan.get("account_email", "")))
            self.auto_renew.setChecked(bool(plan.get("auto_renew", False)))
            saved_payment_status = str(plan.get("payment_status", "PENDING PAYMENT")).upper()
            self.payment_status.setCurrentText(
                saved_payment_status if saved_payment_status in ("PAID", "PENDING PAYMENT")
                else "PENDING PAYMENT"
            )
            saved_payment = str(plan.get("last_payment_date", ""))
            if saved_payment:
                qdate = QDate.fromString(saved_payment, "dd-MM-yyyy")
                if qdate.isValid():
                    self.last_payment.setDate(qdate)
                    self.last_payment_known.setChecked(True)
        else:
            self.renewal.setDate(QDate.currentDate())

        # Fixed bottom action bar.
        action_frame = QFrame()
        action_frame.setObjectName("planActionBar")
        button_bar = QHBoxLayout(action_frame)
        button_bar.setContentsMargins(8, 5, 8, 5)
        button_bar.setSpacing(8)
        button_bar.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.setObjectName("planDialogButtons")
        buttons.setMinimumHeight(38)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        button_bar.addWidget(buttons)
        outer.addWidget(action_frame)

        # Explicit high-contrast styling fixes the low-visibility labels from
        # the previous version while keeping the modern dark Renewly look.
        self.setStyleSheet("""
            QDialog {
                background: #142033;
                color: #F3F7FC;
            }
            QFrame#planDialogHeader {
                background: #213754;
                border: 1px solid #3F5E82;
                border-radius: 12px;
            }
            QLabel#planDialogTitle {
                color: #FFFFFF;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#planDialogSubtitle {
                color: #BFD0E4;
                font-size: 9px;
            }
            QScrollArea#planDialogScroll {
                background: #192A40;
                border: 1px solid #38516F;
                border-radius: 11px;
            }
            QWidget#planDialogForm {
                background: #192A40;
            }
            QLabel#planFieldLabel {
                color: #F2F6FB;
                font-size: 10px;
                font-weight: 700;
                background: transparent;
                padding-left: 1px;
            }
            QLineEdit, QDateEdit, QDoubleSpinBox, QSpinBox, QComboBox {
                background: #2A3E59;
                color: #FFFFFF;
                border: 1px solid #526F91;
                border-radius: 8px;
                padding: 5px 9px;
                selection-background-color: #4E8DFF;
                selection-color: #FFFFFF;
            }
            QLineEdit::placeholder {
                color: #A9BDD4;
            }
            QLineEdit:hover, QDateEdit:hover, QDoubleSpinBox:hover,
            QSpinBox:hover, QComboBox:hover {
                background: #304760;
                border: 1px solid #6B9BE0;
            }
            QLineEdit:focus, QDateEdit:focus, QDoubleSpinBox:focus,
            QSpinBox:focus, QComboBox:focus {
                background: #304863;
                border: 1px solid #79ADFF;
            }
            QComboBox QAbstractItemView {
                background: #213650;
                color: #FFFFFF;
                border: 1px solid #526F91;
                selection-background-color: #3976D4;
                selection-color: #FFFFFF;
            }
            QComboBox::drop-down {
                width: 28px;
                border: none;
                border-left: 1px solid #4A6584;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 22px;
                border: none;
                background: #38516F;
            }
            QCheckBox {
                color: #F1F6FC;
                spacing: 7px;
                font-size: 10px;
                font-weight: 600;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #6B84A1;
                background: #2A3E59;
            }
            QCheckBox::indicator:checked {
                background: #4E8DFF;
                border: 1px solid #80B2FF;
            }
            QLabel#planSectionTitle {
                color: #FFD95A;
                background: #20334B;
                border: 1px solid #49627F;
                border-radius: 8px;
                padding: 5px 9px;
                font-size: 9px;
                font-weight: 800;
            }
            QFrame#planActionBar {
                background: #1B2D45;
                border: 1px solid #3C5775;
                border-radius: 10px;
            }
            QDialogButtonBox QPushButton {
                min-width: 96px;
                min-height: 36px;
                border-radius: 8px;
                padding: 0 18px;
                font-weight: 700;
            }
            QDialogButtonBox QPushButton[text="Save"] {
                background: #4E8DFF;
                color: #FFFFFF;
                border: 1px solid #78ACFF;
            }
            QDialogButtonBox QPushButton[text="Save"]:hover {
                background: #69A0FF;
            }
            QDialogButtonBox QPushButton[text="Cancel"] {
                background: #2B405A;
                color: #F1F6FC;
                border: 1px solid #5B7593;
            }
            QDialogButtonBox QPushButton[text="Cancel"]:hover {
                background: #38516F;
            }
            QScrollBar:vertical {
                background: #16263A;
                width: 9px;
                margin: 2px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #587390;
                min-height: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #7392B2;
            }
        """)
        self.update()

    def choose_plan_logo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Plan Logo", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        try:
            os.makedirs(PLAN_ICON_DIR, exist_ok=True)
            suffix = Path(path).suffix.lower() or ".png"
            safe = "plan_logo_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + suffix
            target = os.path.join(PLAN_ICON_DIR, safe)
            shutil.copy2(path, target)
            self.icon_path.setText(target)
        except Exception as e:
            QMessageBox.warning(self, "Logo", f"Could not copy logo:\n{e}")

    def validate(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Missing Information", "Please enter the plan name.")
            return
        self.accept()

    def result_plan(self):
        return {
            "name": self.name.text().strip(),
            "renewal_date": self.renewal.date().toString("dd-MM-yyyy"),
            "amount": f"{self.amount.value():.2f}",
            "reminder_days": str(self.reminder.value()),
            "category": self.category.currentText(),
            "notes": self.notes.text().strip(),
            "icon_emoji": self.icon_choice.currentText(),
            "icon_path": self.icon_path.text().strip(),
            "billing_cycle": self.billing_cycle.currentText(),
            "payment_method": self.payment_method.currentText().strip(),
            "website": self.website.text().strip(),
            "account_email": self.account_email.text().strip(),
            "auto_renew": self.auto_renew.isChecked(),
            "payment_status": self.payment_status.currentText(),
            "last_payment_date": (
                self.last_payment.date().toString("dd-MM-yyyy")
                if self.last_payment_known.isChecked() else ""
            ),
        }


class RenewlyAuthWorker(QThread):
    """Authenticate and complete Renewly cloud signup/login without blocking the GUI."""
    finished_auth = Signal(object)

    def __init__(self, backend_url, mode, email, password="", name="", code="", parent=None):
        super().__init__(parent)
        self.backend_url = str(backend_url or "").strip().rstrip("/")
        self.mode = str(mode or "login")
        self.email = str(email or "").strip().lower()
        self.password = str(password or "")
        self.name = str(name or "").strip()
        self.code = str(code or "").strip()

    def run(self):
        try:
            if RenewlyAPI is None:
                raise RuntimeError("renewly_api.py is missing or could not be imported.")
            if not self.backend_url:
                raise RuntimeError("Renewly backend URL is not configured.")

            api = RenewlyAPI(self.backend_url, timeout=20)

            if self.mode == "login":
                api.login(self.email, self.password)
                self.finished_auth.emit({
                    "ok": True,
                    "api": api,
                    "email": self.email,
                    "name": self.name,
                    "mode": self.mode,
                })
                return

            if self.mode == "signup_start":
                if not self.name:
                    raise ValueError("Name is required.")
                if not self.password:
                    raise ValueError("Password is required.")
                response = api.start_signup(self.email, self.name, self.password)
                self.finished_auth.emit({
                    "ok": True,
                    "api": api,
                    "email": self.email,
                    "name": self.name,
                    "password": self.password,
                    "mode": self.mode,
                    "signup": response,
                })
                return

            if self.mode == "signup_verify":
                response = api.verify_signup(self.email, self.code)
                self.finished_auth.emit({
                    "ok": True,
                    "api": api,
                    "email": self.email,
                    "name": self.name,
                    "password": self.password,
                    "mode": self.mode,
                    "signup": response,
                })
                return

            if self.mode == "signup_complete":
                response = api.complete_signup(self.email)
                self.finished_auth.emit({
                    "ok": True,
                    "api": api,
                    "email": self.email,
                    "name": self.name,
                    "password": self.password,
                    "mode": self.mode,
                    "signup": response,
                })
                return

            raise ValueError(f"Unsupported authentication mode: {self.mode}")

        except Exception as exc:
            self.finished_auth.emit({
                "ok": False,
                "error": str(exc),
                "mode": self.mode,
            })


def paint_glass_screen_background(painter, width, height):
    """Opaque version of the main window's frosted gradient + light pools (full-screen pages)."""
    p = painter
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, width, height)
    if Theme.dark():
        g.setColorAt(0.00, QColor(23, 32, 51))
        g.setColorAt(0.50, QColor(32, 45, 64))
        g.setColorAt(1.00, QColor(14, 22, 38))
    else:
        g.setColorAt(0.00, QColor(190, 205, 232))
        g.setColorAt(0.50, QColor(206, 218, 240))
        g.setColorAt(1.00, QColor(168, 187, 224))
    p.fillRect(0, 0, width, height, g)
    p.setPen(Qt.NoPen)
    for x, y, pw, ph, alpha in [
        (-int(width * 0.10), -int(height * 0.15), int(width * 0.55), int(height * 0.55), 70),
        (int(width * 0.55), -int(height * 0.10), int(width * 0.50), int(height * 0.50), 80),
        (int(width * 0.15), int(height * 0.62), int(width * 0.65), int(height * 0.55), 55),
    ]:
        p.setBrush(QColor(120, 150, 205, alpha // 3) if Theme.dark() else QColor(255, 255, 255, alpha))
        p.drawEllipse(x, y, pw, ph)


class LoginWindow(QWidget):
    """Full-screen glass sign-in page."""

    def __init__(self):
        super().__init__()
        self.auth_worker = None
        self.pending_signup = None
        self.otp_dialog = None
        self.otp_verified = False
        self._went_fullscreen = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(get_app_icon())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setWindowState(Qt.WindowFullScreen)
        screen = screen_available_geometry(self) or QApplication.primaryScreen().availableGeometry()
        self.resize(screen.size())

        settings = load_app_settings()
        self.backend_url = RENEWLY_BACKEND_URL

        dark = Theme.dark()
        text = "#F3F7FC" if dark else "#172033"
        muted = "#B8C6D7" if dark else "#53627A"
        faint = "#8FA3BB" if dark else "#66758C"
        accent = getattr(Theme, "accent_color", None) or "#6C8DFF"
        field_bg = "rgba(231,240,255,30)" if dark else "rgba(255,255,255,170)"
        field_hover = "rgba(231,240,255,44)" if dark else "rgba(255,255,255,215)"
        field_edge = "rgba(231,240,255,75)" if dark else "rgba(255,255,255,235)"
        ghost_bg = "rgba(231,240,255,28)" if dark else "rgba(255,255,255,105)"
        ghost_hover = "rgba(231,240,255,52)" if dark else "rgba(255,255,255,160)"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(1)

        card = GlassFrame(radius=38, alpha=165)
        card.setFixedWidth(max(340, min(500, int(screen.width() * 0.88))))
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(45)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(2, 6, 23, 140) if dark else QColor(55, 75, 110, 90))
        card.setGraphicsEffect(shadow)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(46, 40, 46, 38)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background:transparent; border:none;")
        pix = get_app_icon().pixmap(76, 76)
        if not pix.isNull():
            logo.setPixmap(pix)
            lay.addWidget(logo)

        title = QLabel("RENEWLY")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size:32px; font-weight:900; letter-spacing:5px; color:{text}; background:transparent; border:none;")

        subtitle = QLabel("Sign in to your Renewly account")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(f"color:{muted}; font-size:13px; font-weight:600; background:transparent; border:none;")

        self.email = QLineEdit()
        self.email.setPlaceholderText("Email address")
        self.email.setClearButtonEnabled(True)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.Password)

        for e in (self.email, self.password):
            e.setMinimumHeight(50)
            e.setStyleSheet(f"""
                QLineEdit {{
                    background:{field_bg};
                    border:1px solid {field_edge};
                    border-radius:18px;
                    padding:0 18px;
                    color:{text};
                    font-size:14px;
                    selection-background-color:{accent};
                    selection-color:#FFFFFF;
                }}
                QLineEdit:hover {{ background:{field_hover}; }}
                QLineEdit:focus {{ background:{field_hover}; border:1px solid {accent}; }}
            """)
        self.email.returnPressed.connect(lambda: self.password.setFocus())
        self.password.returnPressed.connect(self.do_login)

        self.login_button = GlassButton("SIGN IN")
        self.login_button.setMinimumHeight(48)
        self.login_button.clicked.connect(self.do_login)
        self.register_button = QPushButton("CREATE NEW ACCOUNT")
        self.register_button.setCursor(Qt.PointingHandCursor)
        self.register_button.setMinimumHeight(46)
        self.register_button.setStyleSheet(f"""
            QPushButton {{ background:{ghost_bg}; border:1px solid {field_edge}; border-radius:18px; color:{text}; font-weight:800; font-size:12px; letter-spacing:1px; }}
            QPushButton:hover {{ background:{ghost_hover}; border-color:{accent}; }}
            QPushButton:pressed {{ background:{field_bg}; }}
            QPushButton:disabled {{ color:{faint}; }}
        """)
        self.register_button.clicked.connect(self.create_account)

        self.status = QLabel("Sign in to automatically enable Cloud Sync")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{faint}; font-size:11px; font-weight:600; background:transparent; border:none;")

        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addSpacing(14)
        lay.addWidget(self.email)
        lay.addWidget(self.password)
        lay.addSpacing(4)
        lay.addWidget(self.login_button)
        lay.addWidget(self.register_button)
        lay.addSpacing(2)
        lay.addWidget(self.status)

        root.addWidget(card, 0, Qt.AlignCenter)
        footer = QLabel(f"{APP_TAGLINE}")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet(f"color:{faint}; font-size:11px; font-weight:700; letter-spacing:2px; padding-top:18px; background:transparent;")
        root.addWidget(footer)
        root.addStretch(1)

        # Frameless full screen: provide the window buttons ourselves (top right).
        hover_close = "rgba(239,68,68,210)"
        window_button_style = (
            f"QToolButton {{ color:{text}; background:{ghost_bg}; border:1px solid {field_edge}; border-radius:17px; font-size:15px; font-weight:800; }}"
            f"QToolButton:hover {{ background:{ghost_hover}; }}"
        )
        self._min_button = QToolButton(self)
        self._min_button.setText("–")
        self._min_button.setToolTip("Minimize")
        self._min_button.clicked.connect(self.showMinimized)
        self._close_button = QToolButton(self)
        self._close_button.setText("✕")
        self._close_button.setToolTip("Close")
        self._close_button.clicked.connect(self.close)
        for button in (self._min_button, self._close_button):
            button.setFixedSize(34, 34)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(window_button_style)
        self._close_button.setStyleSheet(window_button_style + f"QToolButton:hover {{ background:{hover_close}; color:#FFFFFF; border-color:{hover_close}; }}")
        self._place_window_buttons()
        self.email.setFocus()

    def _place_window_buttons(self):
        if not hasattr(self, "_close_button") or not hasattr(self, "_min_button"):
            return  # resize events arrive while __init__ is still building the page
        right = self.width() - 20
        self._close_button.move(right - 34, 18)
        self._min_button.move(right - 34 - 10 - 34, 18)
        self._close_button.raise_()
        self._min_button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_window_buttons()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._went_fullscreen:
            self._went_fullscreen = True
            QTimer.singleShot(0, self.showFullScreen)

    def paintEvent(self, event):
        p = QPainter(self)
        paint_glass_screen_background(p, self.width(), self.height())

    def _set_busy(self, busy, text=None):
        self.login_button.setEnabled(not busy)
        self.register_button.setEnabled(not busy)
        self.email.setEnabled(not busy)
        self.password.setEnabled(not busy)
        if text:
            self.status.setText(text)

    def do_login(self):
        email = self.email.text().strip().lower()
        password = self.password.text()
        if not email or "@" not in email:
            QMessageBox.warning(self, "Sign In", "Enter a valid email address.")
            self.email.setFocus()
            return
        if not password:
            QMessageBox.warning(self, "Sign In", "Enter your password.")
            self.password.setFocus()
            return
        self._start_auth("login", email, password)

    def create_account(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Create Renewly Account")
        fit_dialog(dlg, 480, 420, 380, 350)
        form = QFormLayout(dlg)
        form.setContentsMargins(28, 26, 28, 26)
        form.setSpacing(12)

        name = QLineEdit()
        name.setPlaceholderText("Your name")
        email = QLineEdit(self.email.text().strip())
        email.setPlaceholderText("Email address")
        password = QLineEdit()
        password.setPlaceholderText("At least 8 characters")
        password.setEchoMode(QLineEdit.Password)
        confirm = QLineEdit()
        confirm.setPlaceholderText("Repeat password")
        confirm.setEchoMode(QLineEdit.Password)
        for field in (name, email, password, confirm):
            field.setMinimumHeight(42)

        form.addRow("Name", name)
        form.addRow("Email", email)
        form.addRow("Password", password)
        form.addRow("Confirm", confirm)
        info = QLabel(
            "A 6-digit verification code will be sent to your email before your Renewly account is created."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#66758C; font-size:11px;")
        form.addRow("Verification", info)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("CONTINUE")
        form.addRow(buttons)

        def submit():
            n = name.text().strip()
            em = email.text().strip().lower()
            pw = password.text()
            cp = confirm.text()
            if not n:
                QMessageBox.warning(dlg, "Create Account", "Enter your name.")
                return
            if not em or "@" not in em:
                QMessageBox.warning(dlg, "Create Account", "Enter a valid email address.")
                return
            if len(pw) < 8:
                QMessageBox.warning(dlg, "Create Account", "Use at least 8 characters for your password.")
                return
            if pw != cp:
                QMessageBox.warning(dlg, "Create Account", "Passwords do not match.")
                return

            self.pending_signup = {
                "name": n,
                "email": em,
                "password": pw,
            }
            dlg.accept()
            self._start_auth("signup_start", em, pw, n)

        buttons.accepted.connect(submit)
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

    def _start_auth(self, mode, email, password="", name="", code=""):
        self._set_busy(True, "Connecting to Renewly Cloud…")
        self.auth_worker = RenewlyAuthWorker(
            self.backend_url,
            mode,
            email,
            password,
            name,
            code,
            self,
        )
        self.auth_worker.finished_auth.connect(self._handle_auth_result)
        self.auth_worker.finished.connect(lambda: setattr(self, "auth_worker", None))
        self.auth_worker.start()

    def _show_otp_dialog(self, signup_response):
        pending = self.pending_signup or {}
        email = str(pending.get("email", "")).strip().lower()
        if not email:
            QMessageBox.warning(self, "Verification Error", "The signup session is missing the email address.")
            return

        if self.otp_dialog is not None:
            try:
                self.otp_dialog.close()
                self.otp_dialog.deleteLater()
            except Exception:
                pass

        dlg = QDialog(self)
        self.otp_dialog = dlg
        dlg.setWindowTitle("Verify Your Email")
        fit_dialog(dlg, 500, 360, 400, 300)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(30, 28, 30, 28)
        root.setSpacing(12)

        title = QLabel("VERIFY YOUR EMAIL")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:22px; font-weight:900; color:#172033;")
        root.addWidget(title)

        message = QLabel(
            f"We sent a 6-digit verification code to:\n{email}\n\n"
            "Enter the code below to verify your email address."
        )
        message.setAlignment(Qt.AlignCenter)
        message.setWordWrap(True)
        message.setStyleSheet("color:#53627A; font-size:12px;")
        root.addWidget(message)

        code_edit = QLineEdit()
        code_edit.setPlaceholderText("6-digit verification code")
        code_edit.setMaxLength(6)
        code_edit.setAlignment(Qt.AlignCenter)
        code_edit.setMinimumHeight(50)
        code_edit.setStyleSheet("""
            QLineEdit {
                background:rgba(255,255,255,220);
                border:1px solid rgba(120,150,190,100);
                border-radius:14px;
                padding:0 14px;
                font-size:20px;
                font-weight:900;
                letter-spacing:4px;
                color:#172033;
            }
        """)
        root.addWidget(code_edit)

        expires = int((signup_response or {}).get("expires_in_seconds") or 600)
        resend_after = int((signup_response or {}).get("resend_after_seconds") or 60)
        timer_label = QLabel()
        timer_label.setAlignment(Qt.AlignCenter)
        timer_label.setStyleSheet("color:#66758C; font-size:11px;")
        root.addWidget(timer_label)

        dev_otp = str((signup_response or {}).get("development_only_email_otp") or "").strip()
        if dev_otp:
            dev_label = QLabel(f"Development OTP: {dev_otp}")
            dev_label.setAlignment(Qt.AlignCenter)
            dev_label.setStyleSheet("color:#B45309; font-weight:800;")
            root.addWidget(dev_label)

        buttons = QHBoxLayout()
        verify_btn = QPushButton("VERIFY EMAIL")
        resend_btn = QPushButton("RESEND CODE")
        cancel_btn = QPushButton("CANCEL")
        for button in (verify_btn, resend_btn, cancel_btn):
            button.setMinimumHeight(40)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(resend_btn)
        buttons.addWidget(verify_btn)
        root.addLayout(buttons)
        dlg.verify_btn = verify_btn
        dlg.resend_btn = resend_btn
        dlg.cancel_btn = cancel_btn

        remaining = {"seconds": max(1, expires), "resend": max(0, resend_after)}
        dlg._remaining = remaining
        dlg._timer_label = timer_label
        timer = QTimer(dlg)
        timer.setInterval(1000)

        def update_timer():
            remaining["seconds"] = max(0, remaining["seconds"] - 1)
            remaining["resend"] = max(0, remaining["resend"] - 1)
            mins, secs = divmod(remaining["seconds"], 60)
            if remaining["seconds"] > 0:
                timer_label.setText(f"Code expires in {mins:02d}:{secs:02d}")
            else:
                timer_label.setText("This code has expired. Request a new code.")
            resend_btn.setEnabled(remaining["resend"] == 0 and not self.auth_worker)

        update_timer()
        timer.timeout.connect(update_timer)
        timer.start()

        def verify():
            code = code_edit.text().strip()
            if len(code) != 6 or not code.isdigit():
                QMessageBox.warning(dlg, "Verification", "Enter the 6-digit verification code.")
                code_edit.setFocus()
                return
            verify_btn.setEnabled(False)
            resend_btn.setEnabled(False)
            cancel_btn.setEnabled(False)
            timer_label.setText("Checking verification code…")
            self._start_auth(
                "signup_verify",
                email,
                str(pending.get("password", "")),
                str(pending.get("name", "")),
                code,
            )

        def resend():
            if self.auth_worker is not None and self.auth_worker.isRunning():
                return
            resend_btn.setEnabled(False)
            verify_btn.setEnabled(False)
            cancel_btn.setEnabled(False)
            timer_label.setText("Sending a new verification code…")
            self._start_auth(
                "signup_start",
                email,
                str(pending.get("password", "")),
                str(pending.get("name", "")),
            )

        def cancel():
            self.pending_signup = None
            self._set_busy(False, "Account creation cancelled. You can try again anytime.")
            dlg.reject()

        verify_btn.clicked.connect(verify)
        resend_btn.clicked.connect(resend)
        cancel_btn.clicked.connect(cancel)
        code_edit.returnPressed.connect(verify)
        dlg.finished.connect(lambda: timer.stop())
        dlg.finished.connect(lambda: setattr(self, "otp_dialog", None) if self.otp_dialog is dlg else None)

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        code_edit.setFocus()

    def _finish_authenticated_session(self, result):
        api = result.get("api")
        email = str(result.get("email") or self.email.text().strip()).strip().lower()
        if not api or not getattr(api, "is_authenticated", False):
            QMessageBox.warning(self, "Sign In Failed", "The server did not return a valid authenticated session.")
            return

        profile_name = str(result.get("name") or email).strip()
        save_user({
            "username": profile_name,
            "email": email,
            "role": "User",
            "profile_updated": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        })

        welcome = WelcomeWindow(profile_name, self)
        welcome.exec()

        self.main = MainWindow(cloud_login_email=email, cloud_api=api)
        # New HTML frontend (set RENEWLY_CLASSIC_UI=1 to keep the original Qt window).
        # MainWindow stays alive as the engine either way, so reminders, tray alerts,
        # Telegram, cloud sync, updates and app lock behave exactly as before.
        web_shell = None
        if install_web_frontend is not None and not os.environ.get("RENEWLY_CLASSIC_UI"):
            try:
                web_shell = install_web_frontend(self.main)
            except Exception as exc:
                print(f"[Renewly] Web frontend unavailable, using classic window: {exc}", file=sys.stderr)
                web_shell = None
        if web_shell is not None:
            web_shell.show_window()
        else:
            self.main.showFullScreen()
            QTimer.singleShot(0, self.main.enter_true_fullscreen)
        self.main.start_cloud_sync_after_login(email, api)

        self.pending_signup = None
        self.password.clear()
        self.close()

    def _handle_auth_result(self, result):
        mode = str(result.get("mode") or "")
        self._set_busy(False)

        if mode == "signup_start":
            if not result.get("ok"):
                self.status.setText("Could not start email verification.")
                if self.otp_dialog is not None:
                    try:
                        self.otp_dialog.verify_btn.setEnabled(True)
                        self.otp_dialog.cancel_btn.setEnabled(True)
                        self.otp_dialog.resend_btn.setEnabled(False)
                        self.otp_dialog._timer_label.setText(str(result.get("error", "Unable to send the verification code.")))
                    except Exception:
                        pass
                QMessageBox.warning(
                    self,
                    "Create Account",
                    str(result.get("error", "Unable to send the verification code.")),
                )
                return

            signup_response = result.get("signup") or {}
            self.status.setText("Verification code sent. Check your email.")
            self._show_otp_dialog(signup_response)
            return

        if mode == "signup_verify":
            if not result.get("ok"):
                self.status.setText("Email verification failed.")
                if self.otp_dialog is not None:
                    try:
                        self.otp_dialog.verify_btn.setEnabled(True)
                        self.otp_dialog.cancel_btn.setEnabled(True)
                        self.otp_dialog.resend_btn.setEnabled(False)
                        self.otp_dialog._resend_cooldown = max(0, int((self.otp_dialog._remaining or {}).get("resend", 0)))
                        self.otp_dialog._timer_label.setText("Enter the code and try again.")
                    except Exception:
                        pass
                QMessageBox.warning(
                    self,
                    "Email Verification Failed",
                    str(result.get("error", "The verification code could not be accepted.")),
                )
                return

            self.otp_verified = True
            pending = self.pending_signup or {}
            self.status.setText("Email verified. Creating your Renewly account…")
            self._start_auth(
                "signup_complete",
                str(pending.get("email", "")),
                str(pending.get("password", "")),
                str(pending.get("name", "")),
            )
            return

        if mode == "signup_complete":
            if not result.get("ok"):
                self.status.setText("Account creation failed.")
                if self.otp_dialog is not None:
                    try:
                        self.otp_dialog.verify_btn.setEnabled(True)
                        self.otp_dialog.cancel_btn.setEnabled(True)
                        self.otp_dialog._timer_label.setText(str(result.get("error", "Unable to create the account.")))
                    except Exception:
                        pass
                QMessageBox.warning(
                    self,
                    "Create Account Failed",
                    str(result.get("error", "Unable to create the account.")),
                )
                return

            # Close the OTP dialog before authenticating the newly-created account.
            if self.otp_dialog is not None:
                try:
                    self.otp_dialog.close()
                except Exception:
                    pass

            pending = self.pending_signup or {}
            email = str(pending.get("email", result.get("email", ""))).strip().lower()
            password = str(pending.get("password", ""))
            name = str(pending.get("name", result.get("name", ""))).strip()
            self.status.setText("Account created. Signing you in…")
            self._start_auth("login", email, password, name)
            return

        if mode == "login":
            if not result.get("ok"):
                self.status.setText("Sign in failed. Check your account details and server connection.")
                QMessageBox.warning(self, "Sign In Failed", str(result.get("error", "Unable to sign in.")))
                self.password.clear()
                return
            self._finish_authenticated_session(result)
            return

        self.status.setText("Authentication failed.")
        QMessageBox.warning(self, "Authentication Error", "An unsupported authentication flow was requested.")


class WelcomeWindow(QDialog):
    """Full-screen glass welcome / loading screen shown right after sign-in."""

    def __init__(self, username, parent=None):
        super().__init__(parent)
        self.username = username or "User"
        self.progress_value = 0
        self._went_fullscreen = False

        self.setWindowTitle("Welcome to Renewly")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowState(Qt.WindowFullScreen)
        screen = screen_available_geometry(self) or QApplication.primaryScreen().availableGeometry()
        self.resize(screen.size())

        dark = Theme.dark()
        text = "#F3F7FC" if dark else "#172033"
        muted = "#B8C6D7" if dark else "#53627A"
        faint = "#8FA3BB" if dark else "#66758C"
        accent = getattr(Theme, "accent_color", None) or "#6C8DFF"
        track = "rgba(231,240,255,38)" if dark else "rgba(255,255,255,150)"
        track_edge = "rgba(231,240,255,70)" if dark else "rgba(255,255,255,210)"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        card = GlassFrame(radius=38, alpha=150)
        card.setFixedWidth(max(360, min(680, int(screen.width() * 0.82))))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(56, 48, 56, 44)
        card_layout.setSpacing(12)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background:transparent; border:none;")
        pix = get_app_icon().pixmap(92, 92)
        if not pix.isNull():
            logo.setPixmap(pix)
            card_layout.addWidget(logo)
            card_layout.addSpacing(6)

        kicker = QLabel("RENEWLY GLASS EDITION")
        kicker.setAlignment(Qt.AlignCenter)
        kicker.setStyleSheet(f"color:{faint}; font-size:11px; font-weight:800; letter-spacing:3px; background:transparent; border:none;")

        welcome = QLabel("WELCOME TO RENEWLY")
        welcome.setAlignment(Qt.AlignCenter)
        welcome.setStyleSheet(f"color:{text}; font-size:34px; font-weight:900; letter-spacing:3px; background:transparent; border:none;")

        subtitle = QLabel(f"Hello, {self.username}")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(f"color:{muted}; font-size:18px; font-weight:600; background:transparent; border:none;")

        self.status = QLabel("Loading your workspace...")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(f"color:{muted}; font-size:14px; font-weight:700; padding-top:12px; background:transparent; border:none;")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(14)
        self.progress.setStyleSheet(f"""
            QProgressBar {{ background:{track}; border:1px solid {track_edge}; border-radius:7px; }}
            QProgressBar::chunk {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 #7DD3FC);
                border-radius:6px; margin:1px;
            }}
        """)

        self.percent = QLabel("0%")
        self.percent.setAlignment(Qt.AlignCenter)
        self.percent.setStyleSheet(f"color:{faint}; font-size:13px; font-weight:800; letter-spacing:1px; background:transparent; border:none;")

        card_layout.addWidget(kicker)
        card_layout.addWidget(welcome)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(18)
        card_layout.addWidget(self.status)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.progress)
        card_layout.addWidget(self.percent)

        outer.addWidget(card, 0, Qt.AlignCenter)
        outer.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_loading)
        self.timer.start(25)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._went_fullscreen:
            self._went_fullscreen = True
            QTimer.singleShot(0, self.showFullScreen)

    def paintEvent(self, event):
        paint_glass_screen_background(QPainter(self), self.width(), self.height())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:  # do not allow Esc to skip the loader half-way
            event.ignore()
            return
        super().keyPressEvent(event)

    def advance_loading(self):
        self.progress_value = min(100, self.progress_value + 1)
        self.progress.setValue(self.progress_value)
        self.percent.setText(f"{self.progress_value}%")

        if self.progress_value < 40:
            self.status.setText("Loading your plans...")
        elif self.progress_value < 75:
            self.status.setText("Preparing your home page...")
        elif self.progress_value < 100:
            self.status.setText("Almost ready...")
        else:
            self.status.setText("Ready!")
            self.timer.stop()
            QTimer.singleShot(300, self.accept)


class InteractiveBarChart(QWidget):
    """Large hoverable category chart without adding external dependencies."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = []
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.hover_index = -1

    def set_data(self, data):
        self.data = list(data)
        self.hover_index = -1
        self.update()

    def mouseMoveEvent(self, event):
        if not self.data:
            return
        left, right = 58, 24
        width = max(1, self.width() - left - right)
        step = width / max(1, len(self.data))
        index = int((event.position().x() - left) / step)
        if 0 <= index < len(self.data):
            self.hover_index = index
            name, value, color = self.data[index]
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{name}\n{value}",
                self
            )
        else:
            self.hover_index = -1
        self.update()

    def leaveEvent(self, event):
        self.hover_index = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.transparent)

        if not self.data:
            p.setPen(Theme.color("#66748A", "#BAC8D9"))
            p.drawText(self.rect(), Qt.AlignCenter, "No category spending data yet")
            return

        left, top, right, bottom = 58, 26, 24, 52
        chart = self.rect().adjusted(left, top, -right, -bottom)
        max_value = max(value for _, value, _ in self.data) or 1.0

        grid_pen = QPen(QColor(100, 120, 150, 45) if not Theme.dark() else QColor(220, 235, 255, 30))
        text_color = Theme.color("#53627A", "#C3D0E0")
        p.setFont(QFont(self.font().family(), 9))

        for i in range(5):
            y = chart.bottom() - (chart.height() * i / 4)
            p.setPen(grid_pen)
            p.drawLine(chart.left(), int(y), chart.right(), int(y))

        step = chart.width() / len(self.data)
        bar_width = max(18, min(74, step * 0.62))
        for i, (name, value, color) in enumerate(self.data):
            x = chart.left() + step * i + (step - bar_width) / 2
            height = (value / max_value) * chart.height()
            y = chart.bottom() - height
            rect = QPainterPath()
            rect.addRoundedRect(x, y, bar_width, height, 9, 9)
            c = QColor(color)
            if i != self.hover_index and self.hover_index >= 0:
                c.setAlpha(110)
            elif i == self.hover_index:
                c = c.lighter(120)
            p.fillPath(rect, c)

            p.setPen(text_color)
            short = name.replace("📺 ", "").replace("☁️ ", "").replace("🏠 ", "").replace("📱 ", "").replace("💳 ", "").replace("🎓 ", "").replace("📦 ", "")
            p.drawText(
                int(x - 16), chart.bottom() + 8, int(bar_width + 32), 34,
                Qt.AlignHCenter | Qt.AlignTop,
                short[:16]
            )

        p.setPen(text_color)
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(8, top - 2, f"Max: {max_value:,.0f}")


class InteractiveLineChart(QWidget):
    """Hoverable 12-month spending estimate trend."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.hover_index = -1

    def set_values(self, values):
        self.values = list(values)
        self.hover_index = -1
        self.update()

    def mouseMoveEvent(self, event):
        if not self.values:
            return
        left, right = 54, 24
        width = max(1, self.width() - left - right)
        step = width / max(1, len(self.values) - 1)
        index = round((event.position().x() - left) / step)
        index = max(0, min(len(self.values) - 1, index))
        self.hover_index = index
        label, value = self.values[index]
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{label}\n{value}",
            self
        )
        self.update()

    def leaveEvent(self, event):
        self.hover_index = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.values:
            p.setPen(Theme.color("#66748A", "#BAC8D9"))
            p.drawText(self.rect(), Qt.AlignCenter, "No monthly estimate data yet")
            return

        left, top, right, bottom = 54, 28, 24, 48
        chart = self.rect().adjusted(left, top, -right, -bottom)
        values = [v for _, v in self.values]
        max_value = max(values) or 1.0
        min_value = min(values)
        span = max(max_value - min_value, max_value * 0.12, 1.0)

        grid_pen = QPen(QColor(100, 120, 150, 45) if not Theme.dark() else QColor(220, 235, 255, 30))
        text_color = Theme.color("#53627A", "#C3D0E0")
        accent = QColor(Theme.accent_color)

        for i in range(5):
            y = chart.top() + chart.height() * i / 4
            p.setPen(grid_pen)
            p.drawLine(chart.left(), int(y), chart.right(), int(y))

        points = []
        for i, (_, value) in enumerate(self.values):
            x = chart.left() + (chart.width() * i / max(1, len(self.values) - 1))
            y = chart.bottom() - ((value - min_value) / span) * (chart.height() * 0.88) - chart.height() * 0.06
            points.append((x, y))

        if len(points) > 1:
            path = QPainterPath()
            path.moveTo(*points[0])
            for x, y in points[1:]:
                path.lineTo(x, y)
            area = QPainterPath(path)
            area.lineTo(points[-1][0], chart.bottom())
            area.lineTo(points[0][0], chart.bottom())
            area.closeSubpath()
            fill = QColor(accent)
            fill.setAlpha(35)
            p.fillPath(area, fill)
            p.setPen(QPen(accent, 3))
            p.drawPath(path)

        for i, ((label, value), (x, y)) in enumerate(zip(self.values, points)):
            point_color = accent.lighter(130) if i == self.hover_index else accent
            p.setBrush(point_color)
            p.setPen(Qt.NoPen)
            radius = 7 if i == self.hover_index else 4
            p.drawEllipse(int(x - radius), int(y - radius), radius * 2, radius * 2)
            p.setPen(text_color)
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(int(x - 22), chart.bottom() + 8, 44, 28, Qt.AlignHCenter | Qt.AlignTop, label)

# ---------------------------------------------------------
# MAIN APPLICATION
# ---------------------------------------------------------

def windows_hello_verify(reason="Unlock Renewly"):
    """Ask Windows Hello to verify the current Windows user.

    Windows chooses the configured method (Face, fingerprint, or PIN).
    Renewly never receives or stores biometric data.

    The WinRT async calls are converted to .NET Tasks through reflection.
    This avoids the PowerShell "Cannot find an overload for AsTask" error
    that occurs when extension methods are called directly as static methods.
    Returns (success, message).
    """
    if sys.platform != "win32":
        return False, "Windows Hello is available only on Windows."

    safe_reason = str(reason).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$VerifierType = [Windows.Security.Credentials.UI.UserConsentVerifier,Windows.Security.Credentials.UI,ContentType=WindowsRuntime]
$AvailabilityEnumType = [Windows.Security.Credentials.UI.UserConsentVerifierAvailability,Windows.Security.Credentials.UI,ContentType=WindowsRuntime]
$VerificationResultType = [Windows.Security.Credentials.UI.UserConsentVerificationResult,Windows.Security.Credentials.UI,ContentType=WindowsRuntime]
$ExtensionsType = [System.WindowsRuntimeSystemExtensions]

function Convert-WinRTAsyncToTask {{
    param(
        [Parameter(Mandatory=$true)] $Operation,
        [Parameter(Mandatory=$true)] [Type] $ResultType
    )

    $method = $ExtensionsType.GetMethods() |
        Where-Object {{
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethodDefinition -and
            $_.GetParameters().Count -eq 1
        }} |
        Select-Object -First 1

    if ($null -eq $method) {{
        throw 'Windows Runtime AsTask conversion method was not found.'
    }}

    $genericMethod = $method.MakeGenericMethod($ResultType)
    return $genericMethod.Invoke($null, @($Operation))
}}

$availabilityOperation = $VerifierType::CheckAvailabilityAsync()
$availabilityTask = Convert-WinRTAsyncToTask -Operation $availabilityOperation -ResultType $AvailabilityEnumType
$availabilityResult = $availabilityTask.GetAwaiter().GetResult()

if ($availabilityResult.ToString() -ne 'Available') {{
    Write-Output ('UNAVAILABLE:' + $availabilityResult.ToString())
    exit 2
}}

$verificationOperation = $VerifierType::RequestVerificationAsync('{safe_reason}')
$verificationTask = Convert-WinRTAsyncToTask -Operation $verificationOperation -ResultType $VerificationResultType
$result = $verificationTask.GetAwaiter().GetResult()

Write-Output $result.ToString()

if ($result.ToString() -eq 'Verified') {{
    exit 0
}}
else {{
    exit 3
}}
"""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        output = stdout or stderr

        if completed.returncode == 0 and stdout.splitlines() and stdout.splitlines()[-1].strip() == "Verified":
            return True, "Verified"

        if "UNAVAILABLE:" in output:
            return False, (
                "Windows Hello is not available for this Windows user. "
                "Configure Windows Hello Face, fingerprint, or PIN in Windows Settings."
            )

        if "Canceled" in output or "Timeout" in output:
            return False, "Windows Hello verification was canceled or timed out."

        return False, output or f"Windows Hello verification failed (exit code {completed.returncode})."

    except subprocess.TimeoutExpired:
        return False, "Windows Hello verification timed out."
    except Exception as exc:
        return False, str(exc)





class MainWindow(QMainWindow):
    def __init__(self, cloud_login_email=None, cloud_api=None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} — Glass Edition")
        self.setWindowIcon(get_app_icon())
        # Stage 3: make the top-level surface transparent so native Acrylic can show through.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("QMainWindow { background: transparent; }")
        fit_window_to_screen(self, 1366, 768, width_ratio=0.96, height_ratio=0.92, min_width=720, min_height=520)
        # Renewly opens in true fullscreen. A compact custom title bar inside the
        # app provides Minimize, Hide-to-Tray and Close controls.
        self.setMinimumSize(720, 520)
        self.plans = load_json(DATA_FILE, [])
        for _plan in self.plans:
            if not isinstance(_plan, dict):
                continue
            if _plan.get("category") not in PLAN_CATEGORIES:
                _plan["category"] = "📦 Other"
        self.recycle = load_json(RECYCLE_FILE, [])
        self.notifications_history = load_notifications()
        self.selected_id = None
        self.settings = load_app_settings()
        self._app_locked = False
        self._last_activity_ms = 0
        self._lock_dialog = None
        QApplication.instance().installEventFilter(self)
        # Tracks which reminder popups were already shown during this app session.
        # A new day automatically gets a fresh notification cycle.
        self._notified_reminders = set()
        self._telegram_sent_markers = set(self.settings.get("telegram_sent_markers", []) or [])
        self._telegram_inflight_markers = set()
        self._telegram_workers = set()
        self._web_push_test_workers = set()
        self._allow_real_close = False
        self.tray_icon = None
        self._update_worker = None
        self._download_worker = None
        self._update_progress = None
        # Cloud authentication is inherited directly from the real Renewly account sign-in.
        # The authenticated API object/token stays in memory; the account password is never stored.
        self._cloud_api = cloud_api if cloud_api is not None and getattr(cloud_api, "is_authenticated", False) else None
        self._cloud_worker = None
        self._cloud_email = str(
            cloud_login_email
            or self.settings.get("cloud_account_email", "")
            or ""
        ).strip()
        self._cloud_auto_sync_interval_ms = 5 * 60 * 1000
        self._cloud_retry_interval_ms = 30 * 1000
        self._cloud_next_sync_timer = QTimer(self)
        self._cloud_next_sync_timer.setSingleShot(True)
        self._cloud_next_sync_timer.timeout.connect(self._cloud_auto_sync)
        self._cloud_last_sync_text = "Not connected"
        self._cloud_auto_sync_active = False
        self._cloud_sync_generation = 0
        # Load Purchases & Warranty data before any dashboard refresh occurs.
        self.purchases = load_purchases()
        Theme.set_accent(self.settings.get("accent_color"))
        apply_global_font(self.settings, self)
        # Advanced sorting state: keeps numeric/date columns sorting correctly.
        self.sort_column = None
        self.sort_order = Qt.AscendingOrder

        # Global command palette — available from anywhere in the app.
        self.command_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.command_shortcut.activated.connect(self.open_command_palette)

        central = GlassBackground()
        self.setCentralWidget(central)
        central.setStyleSheet("""
            QWidget#Central {
                background:transparent;
                background-color:transparent;
                border:none;
            }
        """)
        central.setObjectName("Central")

        # Renewly uses a true fullscreen surface, so Windows' native title bar is
        # intentionally hidden.  Provide a compact custom title bar with explicit
        # Minimize, Hide-to-Tray and Close controls instead of leaving the user
        # without window controls.
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        titlebar = GlassFrame(radius=16, alpha=150)
        titlebar.setFixedHeight(38)
        titlebar_layout = QHBoxLayout(titlebar)
        titlebar_layout.setContentsMargins(12, 4, 6, 4)
        titlebar_layout.setSpacing(5)

        app_title = QLabel(f"Renewly v{APP_VERSION}  —  Glass Edition")
        app_title.setStyleSheet(
            "QLabel { color:#172033; font-size:11px; font-weight:900; "
            "padding:0 4px; background:transparent; border:none; }"
        )
        titlebar_layout.addWidget(app_title)
        titlebar_layout.addStretch(1)

        def _window_control(text, tooltip, callback, accent=False):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.setFixedSize(32, 28)
            button.setCursor(Qt.PointingHandCursor)
            normal = "#4F8CFF" if accent else "rgba(255,255,255,125)"
            hover = "#3778E8" if accent else "rgba(255,255,255,205)"
            fg = "white" if accent else "#172033"
            button.setStyleSheet(
                f"QPushButton {{ background:{normal}; color:{fg}; border:1px solid "
                f"rgba(255,255,255,180); border-radius:9px; font-size:13px; "
                f"font-weight:900; padding:0; }} "
                f"QPushButton:hover {{ background:{hover}; }} "
                "QPushButton:pressed { padding-top:1px; }"
            )
            button.clicked.connect(callback)
            return button

        self.minimize_button = _window_control("—", "Minimize Renewly", self.showMinimized)
        self.hide_button = _window_control("⌄", "Hide Renewly to the system tray", self.hide_to_tray)
        self.close_button = _window_control("×", "Close Renewly", self.exit_application, accent=True)
        titlebar_layout.addWidget(self.minimize_button)
        titlebar_layout.addWidget(self.hide_button)
        titlebar_layout.addWidget(self.close_button)
        root.addWidget(titlebar)

        content_row = QWidget()
        content_row.setStyleSheet("background:transparent; border:none;")
        content_layout = QHBoxLayout(content_row)
        content_layout.setContentsMargins(8, 2, 8, 8)
        content_layout.setSpacing(18)

        # Sidebar
        sidebar = GlassFrame(radius=30, alpha=105)
        sidebar.set_tint_color(self.settings.get("sidebar_color", DEFAULT_SETTINGS["sidebar_color"]))
        sidebar.setMinimumWidth(188)
        sidebar.setMaximumWidth(225)
        sidebar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.sidebar = sidebar
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 16, 14, 14)
        # Compact navigation spacing so every option fits comfortably on laptop screens.
        side.setSpacing(3)

        # Renewly brand logo. Keep it compact so it looks sharp and balanced
        # inside the sidebar instead of appearing oversized or blurry.
        brand_logo = QLabel()
        brand_logo.setObjectName("SidebarBrandLogo")
        brand_logo.setAlignment(Qt.AlignCenter)
        brand_logo.setMinimumSize(72, 48)
        brand_logo.setMaximumSize(118, 72)
        brand_logo.setToolTip("Renewly")
        brand_logo.setStyleSheet("background:transparent; border:none;")

        # Load the Renewly logo supplied with the application.
        logo_pixmap = QPixmap(APP_LOGO_PNG_PATH)
        if logo_pixmap.isNull():
            # Also support the original uploaded filename if it is kept unchanged.
            logo_pixmap = QPixmap(os.path.join(RESOURCE_DIR, "renewly_logo(1).png"))
        if logo_pixmap.isNull():
            # Compatibility fallback for older project folders.
            logo_pixmap = QPixmap(os.path.join(RESOURCE_DIR, "in_app_logo.png"))
        if logo_pixmap.isNull():
            logo_pixmap = QPixmap(APP_ICON_PNG_PATH)

        if not logo_pixmap.isNull():
            brand_logo.setPixmap(
                logo_pixmap.scaled(
                    92, 66,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

        side.addWidget(brand_logo, 0, Qt.AlignHCenter)

        # Keep the RENEWLY name visible below the logo mark.
        brand_name = QLabel("RENEWLY")
        brand_name.setObjectName("SidebarBrandName")
        brand_name.setAlignment(Qt.AlignCenter)
        brand_name.setToolTip("Renewly")
        brand_name.setStyleSheet(
            "QLabel#SidebarBrandName {"
            "background: transparent; border: none; "
            "color: #2F69A8; font-size: 18px; font-weight: 800; "
            "letter-spacing: 1.5px; padding: 0px; margin: 0px; font-size:16px;"
            "}"
        )
        side.addWidget(brand_name, 0, Qt.AlignHCenter)
        side.addSpacing(0)

        for glyph_key, text, fn in [
            ("add", "Add Plan", self.add_plan),
            ("dashboard", "Home Page", self.show_dashboard_home),
            ("analytics", "Analytics", self.show_analytics),
            ("assistant", "Smart Assistant", self.show_smart_assistant),
            ("purchases", "Purchases", self.show_purchases),
            ("calendar", "Calendar", self.show_calendar),
            ("timeline", "Renewal Timeline", self.show_renewal_timeline),
            ("delete", "Recycle Bin", self.show_recycle),
            ("settings", "Settings", self.show_settings),
        ]:
            icon_name = lucide_nav_spec(glyph_key)[0]
            icon_color = lucide_nav_color(glyph_key)
            b = GlassButton(text, icon_name=icon_name, icon_color=icon_color, compact=True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(fn)
            side.addWidget(b)

        # Keep the main navigation at the top and push the account actions
        # together to the bottom of the sidebar.
        side.addStretch(1)

        # Keep Account and Reset Password as two separate compact bottom actions.
        profile = GlassButton("Account", icon_name=lucide_nav_spec("profile")[0], icon_color=lucide_nav_color("profile"), compact=True)
        profile.clicked.connect(self.show_profile)
        reset = GlassButton("Reset Password", icon_name=lucide_nav_spec("password")[0], icon_color=lucide_nav_color("password"), compact=True)
        reset.clicked.connect(self.reset_password)
        logout = GlassButton("Logout Account", icon_name=lucide_nav_spec("logout")[0], icon_color=lucide_nav_color("logout"), compact=True)
        logout.setToolTip("Securely sign out and return to the Renewly login screen")
        logout.clicked.connect(self.logout_account)
        for account_button in (profile, reset, logout):
            account_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            side.addWidget(account_button)
        side.addSpacing(2)

        content_layout.addWidget(sidebar)

        # Main area: dashboard and Analytics are integrated pages in one stack.
        self.content_stack = QStackedWidget()
        self.dashboard_page = QWidget()
        main = QVBoxLayout(self.dashboard_page)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(14)

        header = GlassFrame(radius=30, alpha=145)
        h = QHBoxLayout(header)
        h.setContentsMargins(25, 18, 25, 18)

        title_box = QVBoxLayout()
        hello = QLabel("Good to see you")
        hello.setStyleSheet("color:#53627A; font-size:12px;")
        title = QLabel("RENEWLY HOME PAGE")
        title.setStyleSheet("color:#172033; font-size:27px; font-weight:900;")
        sub = QLabel("Track every plan, renewal and reminder in one place")
        sub.setStyleSheet("color:#5E6D83;")
        title_box.addWidget(hello)
        title_box.addWidget(title)
        title_box.addWidget(sub)

        self.clock = QLabel()
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setMinimumWidth(180)
        self.clock.setStyleSheet("""
            background:rgba(255,255,255,100);
            border:1px solid rgba(255,255,255,185);
            border-radius:18px;
            padding:10px;
            font-size:16px;
            font-weight:800;
            color:#172033;
        """)

        h.addLayout(title_box)
        h.addStretch(1)

        # Compact glass cloud-sync control. The old header sync/status control was
        # visually oversized and its status text could become hard to read. Keep
        # the action as a small glass circle and place the readable status beside it.
        self.cloud_header_status = GlassCircleButton("↻")
        self.cloud_header_status.setToolTip("Sync Renewly Cloud now")
        self.cloud_header_status.setMinimumSize(34, 34)
        self.cloud_header_status.setMaximumSize(34, 34)
        self.cloud_header_status.clicked.connect(self._header_sync_clicked)

        # Keep only the compact glass sync button in the header.
        # The large "Synced just now" status bar has been removed to keep
        # the Home Page header clean and uncluttered.
        h.addWidget(self.cloud_header_status)
        h.addSpacing(4)

        # Premium transparent 3D glass controls with a tactile popup-on-hover effect.
        self.notification_button = GlassCircleButton("", icon_key="notification")
        self.notification_button.setToolTip("Notifications")
        self.notification_button.setMinimumSize(38, 38)
        self.notification_button.setMaximumSize(44, 44)
        self.notification_button.clicked.connect(self.show_notifications)

        self.theme_button = GlassCircleButton("", icon_key="theme_dark")
        self.theme_button.setToolTip("Switch between light and dark mode")
        self.theme_button.setMinimumSize(38, 38)
        self.theme_button.setMaximumSize(44, 44)
        self.theme_button.clicked.connect(self.toggle_theme)
        h.addWidget(self.notification_button)
        h.addSpacing(6)
        h.addWidget(self.theme_button)
        h.addWidget(self.clock)
        self.clock.setVisible(bool(self.settings.get("show_clock", True)))
        self.apply_clock_style()

        main.addWidget(header)

        # Customizable dashboard stat cards. Drag any visible card to change its position.
        self.dashboard_card_area = DashboardCardArea(self.reorder_dashboard_card)
        self.total_card = DraggableStatCard("total", "TOTAL PLANS", 0, "#3182F6")
        self.active_card = DraggableStatCard("active", "ACTIVE PLANS", 0, "#22C55E")
        self.due_card = DraggableStatCard("due", "DUE SOON", 0, "#F59E0B")
        self.monthly_card = DraggableStatCard("monthly", "MONTHLY SPENDING", "₹ 0.00", "#8B5CF6")
        self.upcoming_card = DraggableStatCard("upcoming", "UPCOMING RENEWALS", 0, "#EC4899")
        self.dashboard_cards = {
            "total": self.total_card,
            "active": self.active_card,
            "due": self.due_card,
            "monthly": self.monthly_card,
            "upcoming": self.upcoming_card,
        }
        self.dashboard_card_area.set_cards(
            self.dashboard_cards,
            self.dashboard_card_order(),
            self.dashboard_visible_cards(),
        )
        self.summary_widget = self.dashboard_card_area
        main.addWidget(self.summary_widget)

        # Search
        search_card = GlassFrame(radius=22, alpha=135)
        sl = QHBoxLayout(search_card)
        sl.setContentsMargins(16, 8, 16, 8)
        search_label = QLabel("⌕  SEARCH")
        search_label.setStyleSheet("font-weight:800; color:#415068;")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search plans, notes, amounts...")
        self.search.textChanged.connect(self.refresh_table)
        self.search.setMinimumHeight(38)
        self.search.setStyleSheet("""
            QLineEdit {
                background:rgba(255,255,255,92);
                border:1px solid rgba(255,255,255,170);
                border-radius:13px;
                padding:0 12px;
                color:#172033;
            }
        """)
        self.category_filter = QComboBox()
        self.category_filter.addItem("All Categories")
        self.category_filter.addItems(PLAN_CATEGORIES)
        self.category_filter.setMinimumHeight(38)
        self.category_filter.setMinimumWidth(190)
        self.category_filter.currentTextChanged.connect(self.refresh_table)
        self.category_filter.setStyleSheet("""
            QComboBox {
                background:rgba(255,255,255,92);
                border:1px solid rgba(255,255,255,170);
                border-radius:13px;
                padding:0 12px;
                color:#172033;
                font-weight:700;
            }
        """)
        sl.addWidget(search_label)
        sl.addWidget(self.search, 1)
        sl.addWidget(self.category_filter)
        self.search_widget = search_card
        main.addWidget(self.search_widget)

        # Quick status filters
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)
        self.status_filter = "ALL"
        self.status_buttons = {}
        for label, value in [("All", "ALL"), ("Active", "ACTIVE"), ("Due Soon", "DUE SOON"), ("Paid", "PAID"), ("Expired", "EXPIRED")]:
            btn = GlassButton(label)
            btn.setMinimumHeight(38)
            btn.clicked.connect(lambda checked=False, v=value: self.set_status_filter(v))
            self.status_buttons[value] = btn
            filter_bar.addWidget(btn)
        filter_bar.addStretch(1)
        self.status_filter_widget = QWidget()
        self.status_filter_widget.setLayout(filter_bar)
        main.addWidget(self.status_filter_widget)

        # Table
        table_card = GlassFrame(radius=30, alpha=125)
        tl = QVBoxLayout(table_card)
        # Extra-compact YOUR PLANS section: reduce empty vertical space while
        # keeping enough breathing room for the heading and table.
        tl.setContentsMargins(12, 7, 12, 8)
        tl.setSpacing(2)

        plans_title = QLabel("YOUR PLANS")
        plans_title.setStyleSheet("font-size:17px; font-weight:900; color:#172033; padding:0px; margin:0px;")
        plans_sub = QLabel("Select a plan to edit or manage it")
        plans_sub.setStyleSheet("color:#5E6D83; font-size:10px; padding:0px; margin:0px;")
        tl.addWidget(plans_title)
        tl.addWidget(plans_sub)

        # Bulk actions
        bulk_bar = QHBoxLayout()
        bulk_bar.setSpacing(4)
        self.select_all_btn = GlassButton("Select All")
        self.clear_selection_btn = GlassButton("Clear Selection")
        self.bulk_edit_btn = GlassButton("Bulk Edit")
        self.bulk_delete_btn = GlassButton("Delete Selected")
        self.select_all_btn.clicked.connect(self.select_all_plans)
        self.clear_selection_btn.clicked.connect(self.clear_plan_selection)
        self.bulk_edit_btn.clicked.connect(self.bulk_edit_plans)
        self.bulk_delete_btn.clicked.connect(self.bulk_delete_plans)
        for btn in (self.select_all_btn, self.clear_selection_btn, self.bulk_edit_btn, self.bulk_delete_btn):
            bulk_bar.addWidget(btn)
        bulk_bar.addStretch(1)
        self.bulk_actions_widget = QWidget()
        self.bulk_actions_widget.setLayout(bulk_bar)
        tl.addWidget(self.bulk_actions_widget)

        self.table = GlassPlanTable(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["ID", "PLAN NAME", "CATEGORY", "RENEWAL DATE", "AMOUNT", "DAYS LEFT", "STATUS", "PAYMENT", "NOTES", "ACTIONS"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_plan_context_menu)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_plan())
        self.table.itemSelectionChanged.connect(self.on_select)
        # Payment status is changed directly by clicking its table cell.
        # Using a normal table item instead of an embedded button keeps the
        # layout clean and prevents text from clipping or overlapping.
        self.table.cellClicked.connect(self.handle_plan_table_click)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_by_column)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        # Keep the plan list compact and readable on normal laptop/desktop screens.
        # The internal ID is still kept for selection/sorting, but it does not need
        # to take visible space in the user interface.
        header = self.table.horizontalHeader()
        self.table.setColumnHidden(0, True)
        # All visible columns are sized dynamically.  The previous version used
        # large fixed widths whose total could exceed a 1366px laptop display,
        # forcing a horizontal scrollbar.  Responsive sizing below keeps the
        # important columns readable while allowing secondary text to elide.
        for column in range(10):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        header.setMinimumSectionSize(52)
        header.setDefaultAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        header.setFixedHeight(46)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setSizeAdjustPolicy(QTableWidget.AdjustToContents)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(False)
        self.table.setStyleSheet("""
            QTableWidget {
                background:rgba(246,249,253,88);
                border:1px solid rgba(255,255,255,165);
                border-radius:18px;
                gridline-color:transparent;
                color:#172033;
                selection-background-color:rgba(122,166,230,120);
                selection-color:#172033;
                outline:0;
            }
            QTableWidget::item {
                padding:5px 8px;
                border:0;
                border-bottom:1px solid rgba(205,215,228,62);
            }
            QTableWidget::item:hover {
                background:rgba(255,255,255,78);
                border-bottom:1px solid rgba(150,185,230,110);
            }
            QTableWidget::item:selected {
                background:rgba(120,166,232,128);
                color:#10233E;
                border-top:1px solid rgba(255,255,255,125);
                border-bottom:1px solid rgba(80,135,210,135);
            }
            QHeaderView::section {
                background:rgba(232,238,247,178);
                border:0;
                border-right:1px solid rgba(255,255,255,55);
                border-bottom:1px solid rgba(180,198,222,145);
                padding:0 5px;
                color:#34445E;
                font-size:10px;
                font-weight:900;
            }
            QHeaderView::section:first {
                border-top-left-radius:14px;
            }
            QHeaderView::section:last {
                border-top-right-radius:14px;
                border-right:0;
            }
        """)
        tl.addWidget(self.table)
        main.addWidget(table_card, 1)

        self.content_stack.addWidget(self.dashboard_page)
        self.analytics_page = self.build_analytics_page()
        self.content_stack.addWidget(self.analytics_page)
        self.smart_assistant_page = self.build_smart_assistant_page()
        self.content_stack.addWidget(self.smart_assistant_page)
        self.purchases_page = self.build_purchases_page()
        self.content_stack.addWidget(self.purchases_page)
        content_layout.addWidget(self.content_stack, 1)
        root.addWidget(content_row, 1)

        timer = QTimer(self)
        timer.timeout.connect(self.update_clock)
        timer.start(1000)

        self.update_clock()
        self.apply_dashboard_customization()
        self.dashboard_card_area.updateGeometry()
        self.refresh_all()
        self.apply_theme()
        if self.settings.get("dashboard_default_page", "dashboard") == "analytics":
            self.content_stack.setCurrentWidget(self.analytics_page)
        else:
            self.content_stack.setCurrentWidget(self.dashboard_page)
        # Keep checking reminders while the application is running, even when
        # the main window is hidden in the Windows system tray.
        self.setup_system_tray()
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_renewal_notifications)
        self.reminder_timer.start(60 * 1000)  # check every minute
        QTimer.singleShot(1200, self.check_renewal_notifications)
        # Optional Windows Hello app lock. Delayed until the main UI is fully ready.
        if self.settings.get("app_lock_enabled", False) and self.settings.get("app_lock_on_startup", True):
            QTimer.singleShot(350, lambda: self.lock_app("Unlock Renewly to continue"))
        # Check GitHub for a newer official version without interrupting startup.
        QTimer.singleShot(2500, lambda: self.check_for_updates(silent=True))

    # ---------------- updates ----------------

    def check_for_updates(self, silent=False):
        """Check the official GitHub version.json without freezing the UI."""
        if self._update_worker and self._update_worker.isRunning():
            return
        self._update_check_silent = bool(silent)
        self._update_worker = OnlineUpdateWorker(self)
        self._update_worker.finished_check.connect(self._handle_update_result)
        self._update_worker.start()

    def _handle_update_result(self, result):
        worker = self._update_worker
        self._update_worker = None
        if worker:
            worker.deleteLater()

        if not result.get("ok"):
            if not getattr(self, "_update_check_silent", True):
                QMessageBox.warning(self, "Update Check",
                    "Could not check for updates right now. Please check your internet connection and try again.\n\n"
                    + result.get("error", "Unknown error"))
            return

        data = result.get("data") or {}
        latest = str(data.get("version", "")).strip()
        if not latest:
            if not getattr(self, "_update_check_silent", True):
                QMessageBox.warning(self, "Update Check", "The online version information is invalid.")
            return

        if _version_tuple(latest) <= _version_tuple(APP_VERSION):
            if not getattr(self, "_update_check_silent", True):
                QMessageBox.information(self, "Renewly is Up to Date",
                    f"You are already using the latest version.\n\nVersion: {APP_VERSION}")
            return

        # Prefer the URL supplied by version.json. If it is absent for any reason,
        # fall back to GitHub's stable latest-release asset URL. This keeps updates
        # independent of GitHub API availability on the user's PC.
        download_url = str(data.get("download_url") or "").strip()
        if not download_url:
            download_url = (
                f"https://github.com/{GITHUB_REPOSITORY}/releases/latest/download/Renewly.exe"
            )

        message = (
            f"A new version of Renewly is available!\n\n"
            f"Current version: {APP_VERSION}\n"
            f"Latest version: {latest}\n\n"
            "Download and install the update automatically now?"
        )
        answer = QMessageBox.question(
            self, "Update Available", message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if answer == QMessageBox.Yes:
            self._download_and_install_update(download_url, latest)

    def _download_and_install_update(self, download_url, latest):
        if self._download_worker and self._download_worker.isRunning():
            return

        self._update_progress = QProgressDialog(
            f"Downloading Renewly v{latest}...", None, 0, 100, self
        )
        self._update_progress.setWindowTitle("Downloading Update")
        self._update_progress.setWindowModality(Qt.ApplicationModal)
        self._update_progress.setAutoClose(False)
        self._update_progress.setCancelButton(None)
        self._update_progress.setMinimumDuration(0)
        self._update_progress.show()

        self._download_worker = UpdateDownloadWorker(download_url, self)
        self._download_worker.progress.connect(self._update_progress.setValue)
        self._download_worker.finished_download.connect(self._finish_downloaded_update)
        self._download_worker.start()

    def _finish_downloaded_update(self, result):
        """Stage the downloaded EXE, replace the running EXE, and restart Renewly.

        The updater is intentionally external because Windows cannot reliably replace
        a running EXE.  When Renewly is installed under Program Files, the helper is
        elevated so it has permission to replace the installed application file.
        """
        worker = self._download_worker
        self._download_worker = None
        if worker:
            worker.deleteLater()

        if self._update_progress:
            self._update_progress.close()
            self._update_progress.deleteLater()
            self._update_progress = None

        if not result.get("ok"):
            QMessageBox.critical(
                self,
                "Update Download Failed",
                "The update could not be downloaded.\n\n" + result.get("error", "Unknown error"),
            )
            return

        downloaded_exe = os.path.abspath(str(result.get("path") or ""))
        if not downloaded_exe or not os.path.isfile(downloaded_exe):
            QMessageBox.critical(
                self,
                "Update Error",
                "The downloaded Renewly update file could not be found.",
            )
            return

        # When Renewly is running as a PyInstaller EXE, replace that EXE.
        # When testing from main.py, install Renewly.exe beside the script.
        running_exe = os.path.abspath(sys.executable)
        script_path = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else os.path.abspath(__file__)
        if getattr(sys, "frozen", False) and running_exe.lower().endswith(".exe"):
            target_exe = running_exe
        elif script_path.lower().endswith(".exe") and os.path.isfile(script_path):
            target_exe = script_path
        else:
            target_exe = os.path.join(os.path.dirname(script_path), "Renewly.exe")

        try:
            pid = os.getpid()
            update_dir = tempfile.mkdtemp(prefix="Renewly_Update_")
            staged_exe = os.path.join(update_dir, "Renewly_new.exe")
            shutil.copy2(downloaded_exe, staged_exe)

            # A CMD helper is used because it can wait for the current process to
            # disappear before touching the EXE.  It retries replacement instead of
            # silently starting the old version when Windows still has a file lock.
            batch_path = os.path.join(update_dir, "Apply_Renewly_Update.cmd")
            with open(batch_path, "w", encoding="utf-8", newline="\r\n") as batch:
                batch.write("@echo off\r\n")
                batch.write("setlocal EnableExtensions DisableDelayedExpansion\r\n")
                batch.write(f'set "PID={pid}"\r\n')
                batch.write(f'set "SOURCE={staged_exe}"\r\n')
                batch.write(f'set "TARGET={target_exe}"\r\n')
                batch.write('for %%I in ("%TARGET%") do set "TARGET_DIR=%%~dpI"\r\n')
                batch.write('echo Renewly updater started. > "%TEMP%\\Renewly_Update.log"\r\n')
                batch.write(":WAIT_FOR_APP\r\n")
                batch.write('tasklist /FI "PID eq %PID%" /NH | findstr /R /C:"[0-9][0-9]*" >nul\r\n')
                batch.write("if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto WAIT_FOR_APP)\r\n")
                batch.write(":REPLACE\r\n")
                batch.write('copy /Y "%SOURCE%" "%TARGET%" >nul 2>&1\r\n')
                batch.write("if errorlevel 1 (timeout /t 1 /nobreak >nul & goto REPLACE)\r\n")
                batch.write('if not exist "%TARGET%" (timeout /t 1 /nobreak >nul & goto REPLACE)\r\n')
                batch.write('set "PYINSTALLER_RESET_ENVIRONMENT=1"\r\n')
                batch.write('start "" /D "%TARGET_DIR%" "%TARGET%"\r\n')
                batch.write("timeout /t 2 /nobreak >nul\r\n")
                batch.write('del /F /Q "%SOURCE%" >nul 2>&1\r\n')
                batch.write('del /F /Q "%~f0" >nul 2>&1\r\n')
                batch.write("exit /b 0\r\n")

            # Program Files and other protected locations require elevation.  The
            # UAC prompt is shown before Renewly closes, so cancelling it leaves the
            # current app running instead of pretending that the update succeeded.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            needs_elevation = False
            if sys.platform == "win32":
                protected_roots = [
                    os.environ.get("ProgramFiles", ""),
                    os.environ.get("ProgramFiles(x86)", ""),
                    os.environ.get("SystemRoot", ""),
                ]
                target_norm = os.path.normcase(os.path.abspath(target_exe))
                for root in protected_roots:
                    if root:
                        root_norm = os.path.normcase(os.path.abspath(root))
                        if target_norm == root_norm or target_norm.startswith(root_norm + os.sep):
                            needs_elevation = True
                            break

            if needs_elevation:
                # Use a tiny PowerShell launcher so Windows requests elevation for
                # the updater itself.  The command is kept separate from the batch
                # contents, avoiding fragile quoting of paths with spaces.
                ps_path = os.path.join(update_dir, "Start_Renewly_Updater.ps1")
                with open(ps_path, "w", encoding="utf-8") as ps:
                    ps.write(
                        "$p = Start-Process -FilePath 'cmd.exe' "
                        "-ArgumentList @('/c', '" + batch_path.replace("'", "''") + "') "
                        "-Verb RunAs -PassThru\n"
                    )
                    ps.write("if ($null -eq $p) { exit 1 }\n")

                launch = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                    creationflags=creationflags,
                    close_fds=False,
                    timeout=30,
                )
                if launch.returncode != 0:
                    raise RuntimeError(
                        "Administrator permission was required to update Renewly. "
                        "The update was not applied because the permission request was cancelled or failed."
                    )
            else:
                subprocess.Popen(
                    ["cmd.exe", "/c", batch_path],
                    creationflags=creationflags,
                    close_fds=False,
                )

            QMessageBox.information(
                self,
                "Installing Update",
                "Renewly will now close. The new version will replace the old EXE and restart automatically.",
            )

            # The normal close button intentionally keeps Renewly alive in the
            # system tray.  An update must do a real process exit, otherwise the
            # updater waits forever for this PID and the new version never starts.
            self._allow_real_close = True
            if getattr(self, "tray_icon", None) is not None:
                self.tray_icon.hide()
            QApplication.closeAllWindows()
            QTimer.singleShot(100, QApplication.quit)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Update Installation Failed",
                f"The update was downloaded but could not be installed automatically.\n\n{exc}",
            )

    # ---------------- data ----------------

    # ---------------- cloud backend ----------------

    def _header_sync_clicked(self):
        """Run a manual cloud sync from the compact home-page sync control."""
        if not self._cloud_api:
            QMessageBox.information(
                self,
                "Cloud Sync",
                "Please sign in to your Renewly account first. Cloud Sync uses your normal Renewly sign-in.",
            )
            return
        self._cloud_sync_from_authenticated_session(automatic=False)

    def _set_cloud_status(self, text, connected=False, busy=False, error=False):
        """Update the compact home-page sync status and any open Settings dialog."""
        if error:
            color = "#DC2626" if not Theme.dark() else "#FF6B6B"
        elif busy:
            color = "#D97706" if not Theme.dark() else "#FBBF24"
        elif connected:
            color = "#16A34A" if not Theme.dark() else "#4ADE80"
        else:
            color = "#53627A" if not Theme.dark() else "#D7E3F2"

        self._cloud_last_sync_text = str(text)
        if hasattr(self, "cloud_header_status"):
            self.cloud_header_status.setText("↻")
            self.cloud_header_status.setToolTip(f"Renewly Cloud Sync: {text}")
        dialog = getattr(self, "_cloud_settings_dialog", None)
        if dialog is not None and hasattr(dialog, "cloud_status_label"):
            dialog.cloud_status_label.setText(f"● {text}")
            dialog.cloud_status_label.setStyleSheet(f"font-weight:800; color:{color};")

    def _schedule_cloud_auto_sync(self, delay_ms=None):
        """Schedule the next background sync without blocking the GUI."""
        if not self._cloud_auto_sync_active or not self._cloud_api:
            self._cloud_next_sync_timer.stop()
            return
        delay = int(delay_ms if delay_ms is not None else self._cloud_auto_sync_interval_ms)
        self._cloud_next_sync_timer.start(max(1000, delay))

    def _cloud_auto_sync(self):
        if not self._cloud_auto_sync_active or not self._cloud_api:
            return
        if self._cloud_worker is not None and self._cloud_worker.isRunning():
            self._schedule_cloud_auto_sync(self._cloud_retry_interval_ms)
            return
        self._cloud_sync_from_authenticated_session(automatic=True)

    def _cloud_sync_from_authenticated_session(self, settings_dialog=None, automatic=False):
        """Start cloud sync using the authentication from the normal app sign-in.

        No separate cloud password is requested. On the first sync, the password
        entered in the Renewly login window is used only in memory to obtain the
        backend token. Later syncs reuse the in-memory API session.
        """
        if self._cloud_worker is not None and self._cloud_worker.isRunning():
            return
        backend_url = RENEWLY_BACKEND_URL
        email = str(self._cloud_email or "").strip()
        if not backend_url or not email or not self._cloud_api or not getattr(self._cloud_api, "is_authenticated", False):
            if not automatic:
                QMessageBox.warning(
                    self,
                    "Cloud Sync",
                    "Cloud Sync is available automatically after a successful Renewly account sign-in. "
                    "No separate Cloud Sync password is used."
                )
            return

        self.settings["backend_url"] = backend_url
        self.settings["cloud_account_email"] = email
        self.settings["cloud_sync_enabled"] = True
        save_app_settings(self.settings)
        self._cloud_auto_sync_active = True
        self._cloud_sync_generation += 1
        sync_generation = self._cloud_sync_generation
        self._set_cloud_status("Syncing…", busy=True)
        deleted_by_account = self.settings.get("cloud_deleted_plan_ids_by_account", {})
        if not isinstance(deleted_by_account, dict):
            deleted_by_account = {}
        queued_deleted_ids = deleted_by_account.get(email, [])
        if not isinstance(queued_deleted_ids, list):
            queued_deleted_ids = []

        self._cloud_worker = CloudSyncWorker(
            backend_url, email, "", self.plans, self._cloud_api,
            deleted_cloud_ids=queued_deleted_ids, parent=self
        )
        self._cloud_worker.finished_sync.connect(
            lambda result, d=settings_dialog, a=automatic, g=sync_generation:
                self._handle_cloud_sync_result(result, d, a, g)
        )
        self._cloud_worker.finished.connect(lambda: setattr(self, "_cloud_worker", None))
        self._cloud_worker.start()

    def start_cloud_sync_after_login(self, email, api):
        """Start Cloud Sync immediately after the real Renewly account signs in.

        Local plan data belongs to the previously signed-in account. When a
        different Renewly account signs in on the same PC, never send those old
        local plans to the new account and never display them while the new
        account is being synchronized. The cloud worker will download only the
        plans belonging to the newly authenticated account.
        """
        new_email = str(email or "").strip().lower()
        previous_email = str(self.settings.get("cloud_account_email", "") or "").strip().lower()
        local_owner = str(self.settings.get("local_data_account_email", "") or "").strip().lower()

        if api is None or not getattr(api, "is_authenticated", False):
            self._set_cloud_status("Sign-in complete • Cloud authentication unavailable", error=True)
            return

        # HARD ACCOUNT ISOLATION:
        # DATA_FILE is shared by all Renewly accounts on the same Windows user.
        # Therefore the cloud account email alone is NOT enough to determine who
        # owns the plans currently sitting in DATA_FILE. A dedicated local owner
        # marker is used. If it is missing (older build / first migration), clear
        # the legacy local plans once and rebuild the workspace from the signed-in
        # cloud account. If the owner differs, clear it before ANY cloud request.
        # This prevents Account A plans from ever being shown to or uploaded into
        # Account B.
        if not local_owner or local_owner != new_email:
            self.plans = []
            self.recycle = []
            self.selected_id = None
            self.save_data()
            save_json(RECYCLE_FILE, self.recycle)

        self._cloud_email = new_email
        self._cloud_api = api
        self.settings["cloud_account_email"] = new_email
        self.settings["cloud_sync_enabled"] = True
        save_app_settings(self.settings)
        self.refresh_all()
        self._cloud_sync_from_authenticated_session(automatic=True)

    def _handle_cloud_sync_result(self, result, settings_dialog=None, automatic=False, sync_generation=None):
        # Ignore a late result from a worker that was superseded (for example,
        # when the user disconnected while a network request was still running).
        if sync_generation is not None and sync_generation != self._cloud_sync_generation:
            return
        if result.get("ok"):
            self._cloud_api = result.get("api")
            self.plans = result.get("plans", self.plans)
            self.settings["cloud_sync_enabled"] = True
            self.settings["cloud_account_email"] = result.get("email", self.settings.get("cloud_account_email", ""))
            self.settings["local_data_account_email"] = str(self.settings["cloud_account_email"] or "").strip().lower()

            deleted_by_account = self.settings.get("cloud_deleted_plan_ids_by_account", {})
            if not isinstance(deleted_by_account, dict):
                deleted_by_account = {}
            account_key = str(self.settings["cloud_account_email"] or "").strip().lower()
            pending = deleted_by_account.get(account_key, [])
            if not isinstance(pending, list):
                pending = []
            confirmed = {int(v) for v in result.get("deleted_cloud_ids", []) if str(v).strip().isdigit()}
            remaining = [v for v in pending if str(v).strip().isdigit() and int(v) not in confirmed]
            if remaining:
                deleted_by_account[account_key] = remaining
            else:
                deleted_by_account.pop(account_key, None)
            self.settings["cloud_deleted_plan_ids_by_account"] = deleted_by_account

            self._cloud_email = self.settings["cloud_account_email"]
            self._cloud_auto_sync_active = True
            save_app_settings(self.settings)
            self.save_data()
            self.refresh_all()
            uploaded = int(result.get("uploaded", 0))
            downloaded = int(result.get("downloaded", 0))
            self._set_cloud_status("Synced just now", connected=True)
            self._schedule_cloud_auto_sync()
            if settings_dialog is not None and hasattr(settings_dialog, "cloud_status_label"):
                settings_dialog.cloud_status_label.setText(f"● Connected • {self.settings['cloud_account_email']} • {uploaded} synced / {downloaded} downloaded")
            if not automatic:
                QMessageBox.information(self, "Cloud Sync Complete", f"Renewly is connected to the backend.\n\nPlans synchronized: {uploaded}\nNew cloud plans downloaded: {downloaded}\n\nAutomatic background sync is now enabled every 5 minutes.")
        else:
            self._cloud_api = None
            self._set_cloud_status("Retrying in 30s", error=True)
            self._schedule_cloud_auto_sync(self._cloud_retry_interval_ms)
            if settings_dialog is not None and hasattr(settings_dialog, "cloud_status_label"):
                settings_dialog.cloud_status_label.setText("● Sync failed • retrying in 30 seconds")
            if not automatic:
                QMessageBox.critical(self, "Cloud Sync Failed", str(result.get("error", "Unknown backend error.")))

    def cloud_logout(self):
        """Disconnect cloud sync without leaving the current Renewly account."""
        self._cloud_next_sync_timer.stop()
        self._cloud_auto_sync_active = False
        self._cloud_sync_generation += 1
        self._cloud_api = None
        self._cloud_email = ""
        self.settings["cloud_sync_enabled"] = False
        self.settings["cloud_account_email"] = ""
        save_app_settings(self.settings)
        self._set_cloud_status("Disconnected")

    def _finish_account_logout(self):
        """Destroy the authenticated desktop session and return to LoginWindow."""
        self._cloud_next_sync_timer.stop()
        self._cloud_auto_sync_active = False
        self._cloud_sync_generation += 1

        # Detach the worker from the GUI result path. A network request already
        # in flight may finish naturally, but its late result is invalidated by
        # the generation counter and cannot restore the old account session.
        worker = self._cloud_worker
        if worker is not None and worker.isRunning():
            try:
                worker.finished_sync.disconnect()
            except Exception:
                pass
            try:
                worker.requestInterruption()
            except Exception:
                pass

        # Drop every desktop-side reference to the authenticated API/JWT.
        self._cloud_api = None
        self._cloud_email = ""
        self._cloud_worker = None if worker is None or not worker.isRunning() else worker

        # Clear the current account's local workspace. The account owner marker
        # is also cleared, so the next login must rebuild the workspace from its
        # own authenticated cloud account.
        self.plans = []
        self.recycle = []
        self.selected_id = None
        self.save_data()
        save_json(RECYCLE_FILE, self.recycle)

        self.settings["cloud_sync_enabled"] = False
        self.settings["cloud_account_email"] = ""
        self.settings["local_data_account_email"] = ""
        save_app_settings(self.settings)

        # Never retain an old local password hash as an account-session secret.
        # Renewly authentication is performed by the backend account system.
        try:
            user = load_user() or {}
            if isinstance(user, dict) and "password" in user:
                user.pop("password", None)
                save_user(user)
        except Exception:
            pass

        self._set_cloud_status("Signed out")
        self._allow_real_close = True
        self.hide()

        login = LoginWindow()
        login.show()
        self._login_window = login
        self.deleteLater()

    def logout_account(self):
        """Securely sign out of the current Renewly account."""
        email = str(self._cloud_email or self.settings.get("cloud_account_email", "") or "").strip()
        label = email or "the current account"
        answer = QMessageBox.question(
            self,
            "Logout Account",
            f"Sign out of {label}?\n\n"
            "Your authenticated cloud session will be cleared, background sync will stop, "
            "and this account's local workspace cache will be cleared. "
            "Your plans already synchronized to Renewly Cloud will remain available when you sign in again.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        # Do not tear down the authenticated window while a worker still owns a
        # reference to the in-memory API/JWT. Let the current sync finish first;
        # this keeps logout deterministic and avoids destroying a live QThread.
        if self._cloud_worker is not None and self._cloud_worker.isRunning():
            QMessageBox.information(
                self,
                "Cloud Sync In Progress",
                "A cloud synchronization is currently running.\n\n"
                "Please click Logout Account again after the sync finishes. "
                "This prevents the active session from being torn down while a network request is still using it.",
            )
            return

        self._finish_account_logout()

    def save_data(self):
        save_json(DATA_FILE, self.plans)

    def refresh_lucide_icons(self):
        """Repaint all theme-aware Lucide controls after a theme change."""
        for name in ("theme_button", "notification_button"):
            button = getattr(self, name, None)
            if isinstance(button, GlassCircleButton):
                button.update()
        for button in self.findChildren(GlassCircleButton):
            button.update()
        for button in self.findChildren(GlassButton):
            if getattr(button, "_icon_key", None):
                button.update()

    def apply_theme(self):
        """Refresh Qt controls and the custom-painted translucent glass after a mode change."""
        # Keep the top controls compact and circular.
        self.theme_button.set_lucide_icon("theme_light" if Theme.dark() else "theme_dark")
        self.theme_button.setToolTip("Switch to Light Mode" if Theme.dark() else "Switch to Dark Mode")
        if hasattr(self, "notification_button"):
            unread = sum(1 for n in self.notifications_history if not n.get("read")) if hasattr(self, "notifications_history") else 0
            self.notification_button.set_lucide_icon("notification")
            self.notification_button.setToolTip(f"Notifications ({unread} unread)" if unread else "Notifications")
        apply_theme_to(self)
        if sys.platform == "win32":
            try:
                apply_windows_acrylic(int(self.winId()), Theme.dark())
            except Exception:
                pass
        self.refresh_lucide_icons()

    def toggle_theme(self):
        Theme.mode = "light" if Theme.dark() else "dark"
        save_theme(Theme.mode)
        if hasattr(self, "settings"):
            self.settings["theme"] = Theme.mode
            save_app_settings(self.settings)
        self.apply_theme()

    def days_left(self, plan):
        try:
            renewal = datetime.strptime(plan.get("renewal_date", ""), "%d-%m-%Y").date()
            return (renewal - date.today()).days
        except Exception:
            return 0

    def format_plan_date(self, value):
        try:
            dt = datetime.strptime(str(value), "%d-%m-%Y")
            return dt.strftime({
                "DD-MM-YYYY": "%d-%m-%Y",
                "DD/MM/YYYY": "%d/%m/%Y",
                "MM/DD/YYYY": "%m/%d/%Y",
                "YYYY-MM-DD": "%Y-%m-%d",
            }.get(self.settings.get("date_format"), "%d-%m-%Y"))
        except Exception:
            return str(value)

    def status(self, plan):
        days = self.days_left(plan)
        try:
            reminder = int(plan.get("reminder_days", self.settings.get("default_reminder_days", 3)))
        except Exception:
            reminder = int(self.settings.get("default_reminder_days", 3))
        payment_status = str(plan.get("payment_status", "PENDING PAYMENT")).upper()
        # A plan that has passed its renewal date but has already been paid
        # should be shown as PAID instead of EXPIRED.
        if days < 0:
            return "PAID" if payment_status == "PAID" else "EXPIRED"
        if days <= reminder:
            return "DUE SOON"
        return "ACTIVE"

    def refresh_all(self):
        self.refresh_table()
        self.update_dashboard()
        if hasattr(self, "analytics_page"):
            self.refresh_analytics_page()
        if hasattr(self, "smart_assistant_page"):
            self.refresh_smart_assistant()
        if hasattr(self, "purchases_page"):
            self.refresh_purchases()

    def sort_by_column(self, column):
        """Sort plans using real numeric/date values instead of displayed text."""
        if self.sort_column == column:
            self.sort_order = (
                Qt.DescendingOrder
                if self.sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self.sort_column = column
            self.sort_order = Qt.AscendingOrder

        self.table.horizontalHeader().setSortIndicator(
            column, self.sort_order
        )
        self.refresh_table()

    def _amount_sort_value(self, plan):
        try:
            return float(str(plan.get("amount", 0)).replace(",", "").strip())
        except Exception:
            return 0.0

    def _date_sort_value(self, plan):
        try:
            return datetime.strptime(
                str(plan.get("renewal_date", "")), "%d-%m-%Y"
            ).date().toordinal()
        except Exception:
            return date.max.toordinal()

    def _sort_value(self, plan, column):
        """Return the correct underlying value for each table column."""
        if column == 0:  # ID
            try:
                return int(plan.get("id", 0))
            except Exception:
                return 0
        if column == 1:  # Plan Name
            return str(plan.get("name", "")).lower()
        if column == 2:  # Category
            return str(plan.get("category", "")).lower()
        if column == 3:  # Renewal Date
            return self._date_sort_value(plan)
        if column == 4:  # Amount
            return self._amount_sort_value(plan)
        if column == 5:  # Days Left
            return self.days_left(plan)
        if column == 6:  # Status
            # Useful logical order: Expired -> Due Soon -> Active.
            order = {"EXPIRED": 0, "DUE SOON": 1, "ACTIVE": 2, "PAID": 3}
            return order.get(self.status(plan), 99)
        if column == 7:  # Payment Status
            order = {"PENDING PAYMENT": 0, "PAID": 1}
            return order.get(str(plan.get("payment_status", "PENDING PAYMENT")).upper(), 99)
        if column == 8:  # Notes
            return str(plan.get("notes", "")).lower()
        return ""

    def refresh_table(self):
        # Keep table columns responsive after data/filter changes.
        if hasattr(self, "table"):
            QTimer.singleShot(0, self._fit_dashboard_table)
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        selected_category = (
            self.category_filter.currentText()
            if hasattr(self, "category_filter")
            else "All Categories"
        )

        # Search across every useful field, including notes and amount.
        rows = [
            p for p in self.plans
            if (
                query in str(p.get("name", "")).lower()
                or query in str(p.get("renewal_date", "")).lower()
                or query in self.status(p).lower()
                or query in str(p.get("category", "📦 Other")).lower()
                or query in str(p.get("notes", "")).lower()
                or query in str(p.get("amount", "")).lower()
                or query in str(p.get("payment_status", "PENDING PAYMENT")).lower()
            )
            and (
                selected_category == "All Categories"
                or str(p.get("category", "📦 Other")) == selected_category
            )
        ]

        # Advanced sorting uses the original data values, not the table text.
        if self.sort_column is not None:
            reverse = self.sort_order == Qt.DescendingOrder
            rows.sort(
                key=lambda plan: self._sort_value(plan, self.sort_column),
                reverse=reverse
            )

        self.table.setUpdatesEnabled(False)
        self.table.clearContents()
        self.table._hovered_row = -1
        self.table.setRowCount(len(rows))

        for r, p in enumerate(rows):
            values = [
                str(p.get("id", "")),
                plan_display_name(p),
                str(p.get("category", "📦 Other"))
                if str(p.get("category", "📦 Other")) in PLAN_CATEGORIES
                else "📦 Other",
                self.format_plan_date(p.get("renewal_date", "")),
                CURRENCY_SYMBOLS.get(self.settings.get("currency"), "₹")
                + " " + str(p.get("amount", "")),
                ("—" if self.status(p) == "PAID" else str(self.days_left(p))),
                self.status(p),
                ("PAID" if str(p.get("payment_status", "PENDING PAYMENT")).upper() == "PAID"
                 else "PENDING"),
                str(p.get("notes", "—") or "—"),
                ""
            ]

            for c, value in enumerate(values):
                item = QTableWidgetItem(value)

                # Store the real sortable value as UserRole data too.
                item.setData(Qt.UserRole, self._sort_value(p, c))

                item.setTextAlignment(
                    Qt.AlignCenter
                    if c not in (1, 2, 8)
                    else Qt.AlignLeft | Qt.AlignVCenter
                )

                if c in (4, 5, 6, 7):
                    item.setFont(QFont(item.font().family(), item.font().pointSize(), QFont.Bold))

                if c == 2:
                    # Category names need stronger contrast because the dashboard
                    # uses a translucent glass table background.  Use brighter
                    # display colors and a slightly larger bold font so every
                    # category remains easy to read.
                    category = value
                    category_display_colors = {
                        "📺 Entertainment": "#A78BFA",
                        "☁️ Software / Cloud": "#60A5FA",
                        "🏠 Home": "#4ADE80",
                        "📱 Mobile": "#22D3EE",
                        "💳 Finance": "#FBBF24",
                        "🎓 Education": "#F472B6",
                        "📦 Other": "#CBD5E1",
                    }
                    color = category_display_colors.get(category, "#CBD5E1")
                    item.setForeground(QColor(color))
                    category_font = QFont(item.font())
                    category_font.setBold(True)
                    category_font.setPointSize(max(category_font.pointSize() + 1, 11))
                    item.setFont(category_font)
                    item.setToolTip(category)

                if c == 7:
                    payment_is_paid = str(p.get("payment_status", "PENDING PAYMENT")).upper() == "PAID"
                    item.setText("●  PAID" if payment_is_paid else "●  PENDING")
                    item.setForeground(QColor("#5FE0A0" if payment_is_paid else "#FFB45C"))
                    item.setBackground(QColor(27, 156, 99, 32) if payment_is_paid else QColor(217, 130, 43, 30))
                    item.setFont(
                        QFont(
                            item.font().family(),
                            item.font().pointSize(),
                            QFont.Bold
                        )
                    )
                    item.setToolTip("Click to toggle payment status")

                self.table.setItem(r, c, item)

            # Clean centered action button.  The container prevents the three-dot
            # button from stretching to the full cell and keeps it visually balanced.
            action_container = QWidget()
            action_layout = QHBoxLayout(action_container)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(0)

            action_button = QPushButton("•••")
            action_button.setCursor(Qt.PointingHandCursor)
            action_button.setToolTip("Open plan actions")
            action_button.setFixedSize(46, 30)
            action_button.setStyleSheet("""
                QPushButton {
                    color: #DCE8F7;
                    font-size: 16px;
                    font-weight: 800;
                    letter-spacing: 1px;
                    border: 1px solid rgba(170, 195, 230, 72);
                    border-radius: 10px;
                    background: rgba(255, 255, 255, 20);
                }
                QPushButton:hover {
                    color: white;
                    border: 1px solid rgba(120, 185, 255, 150);
                    background: rgba(95, 145, 220, 70);
                }
                QPushButton:pressed {
                    background: rgba(70, 115, 180, 100);
                }
            """)
            action_layout.addStretch(1)
            action_layout.addWidget(action_button)
            action_layout.addStretch(1)

            plan_id = str(p.get("id", ""))
            action_button.clicked.connect(
                lambda checked=False, pid=plan_id, btn=action_button:
                self.show_row_actions(pid, btn)
            )
            self.table.setCellWidget(r, 9, action_container)

            self.table.setRowHeight(r, max(48, self.table.NORMAL_ROW_HEIGHT))

        self.table.setUpdatesEnabled(True)

        if self.sort_column is not None:
            self.table.horizontalHeader().setSortIndicator(
                self.sort_column, self.sort_order
            )

    # ---------------- dashboard customization ----------------

    def dashboard_card_order(self):
        valid = ["total", "active", "due", "monthly", "upcoming"]
        saved = self.settings.get("dashboard_card_order", valid)
        if not isinstance(saved, list):
            saved = valid
        order = [item for item in saved if item in valid]
        for item in valid:
            if item not in order:
                order.append(item)
        return order

    def dashboard_visible_cards(self):
        valid = ["total", "active", "due", "monthly", "upcoming"]
        saved = self.settings.get("dashboard_visible_cards", valid)
        if not isinstance(saved, list):
            saved = valid
        return [item for item in saved if item in valid]

    def reorder_dashboard_card(self, dragged_id, visible_order):
        current = self.dashboard_card_order()
        hidden = [item for item in current if item not in visible_order]
        self.settings["dashboard_card_order"] = list(visible_order) + hidden
        save_app_settings(self.settings)
        self.apply_dashboard_customization()

    def apply_dashboard_customization(self):
        if not hasattr(self, "dashboard_card_area"):
            return

        visible = self.dashboard_visible_cards()
        order = self.dashboard_card_order()
        density = self.settings.get("dashboard_density", "comfortable")

        self.dashboard_card_area.rebuild(order, visible)
        self.summary_widget.setVisible(bool(self.settings.get("dashboard_show_summary", True)))
        self.search_widget.setVisible(bool(self.settings.get("dashboard_show_search", True)))
        self.status_filter_widget.setVisible(
            bool(self.settings.get("dashboard_show_status_filters", True))
        )
        self.bulk_actions_widget.setVisible(
            bool(self.settings.get("dashboard_show_bulk_actions", True))
        )
        self.clock.setVisible(bool(self.settings.get("show_clock", True)))

        if density == "compact":
            card_height = 118
            card_min = (0, 108)
            spacing = 8
            row_height = 40
        else:
            card_height = 165
            card_min = (0, 150)
            spacing = 12
            row_height = 44

        self.dashboard_card_area.layout.setSpacing(spacing)
        for card in self.dashboard_cards.values():
            card.setMinimumSize(*card_min)
            card.setMaximumHeight(card_height)

        if hasattr(self, "table"):
            self.table.NORMAL_ROW_HEIGHT = row_height
            self.table.HOVER_ROW_HEIGHT = row_height
            for row in range(self.table.rowCount()):
                self.table.setRowHeight(row, row_height)

    def update_dashboard(self):
        """Refresh the five main dashboard statistics."""
        total = len(self.plans)
        due = sum(1 for p in self.plans if self.status(p) == "DUE SOON")
        active = sum(1 for p in self.plans if self.status(p) == "ACTIVE")

        # Convert each billing cycle into an estimated monthly cost.
        cycle_divisors = {
            "Monthly": 1, "Quarterly": 3, "Half-Yearly": 6,
            "Yearly": 12, "Custom": 1,
        }
        monthly_spending = 0.0
        for p in self.plans:
            try:
                amount = float(p.get("amount", 0) or 0)
            except (TypeError, ValueError):
                amount = 0.0
            monthly_spending += amount / cycle_divisors.get(
                str(p.get("billing_cycle", "Monthly")), 1
            )

        # Upcoming = renewals within the next 30 days (including today).
        today = date.today()
        upcoming = 0
        for p in self.plans:
            try:
                renewal = datetime.strptime(str(p.get("renewal_date", "")), "%d-%m-%Y").date()
                days = (renewal - today).days
                if 0 <= days <= 30:
                    upcoming += 1
            except Exception:
                pass

        currency = CURRENCY_SYMBOLS.get(self.settings.get("currency"), "₹")
        self.total_card.value.setText(str(total))
        self.active_card.value.setText(str(active))
        self.due_card.value.setText(str(due))
        self.monthly_card.value.setText(f"{currency} {monthly_spending:,.2f}")
        self.upcoming_card.value.setText(str(upcoming))

    def handle_plan_table_click(self, row, column):
        """Open the premium plan details popup, while keeping payment/actions interactive."""
        id_item = self.table.item(row, 0)
        if id_item is None:
            return

        # Payment remains a one-click toggle.
        if column == 7:
            self.toggle_payment_status(id_item.text())
            return

        # The actions column contains its own three-dot button.
        if column == 9:
            return

        # Preserve Ctrl/Shift multi-selection for bulk actions.
        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            return

        self.selected_id = id_item.text()
        self.show_plan_details()

    def on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.selected_id = self.table.item(rows[0].row(), 0).text()

    def selected_plan(self):
        if not self.selected_id:
            return None
        for p in self.plans:
            if str(p.get("id")) == str(self.selected_id):
                return p
        return None

    def get_plan_by_id(self, plan_id):
        for plan in self.plans:
            if str(plan.get("id")) == str(plan_id):
                return plan
        return None

    def toggle_payment_status(self, plan_id):
        """Toggle PAID/PENDING PAYMENT directly from the Payment table column."""
        plan = self.get_plan_by_id(plan_id)
        if not plan:
            return

        current = str(plan.get("payment_status", "PENDING PAYMENT")).upper()
        becoming_paid = current != "PAID"
        plan["payment_status"] = "PAID" if becoming_paid else "PENDING PAYMENT"
        if becoming_paid:
            paid_on = date.today().strftime("%d-%m-%Y")
            plan["last_payment_date"] = paid_on
            history = plan.setdefault("payment_history", [])
            history.append({
                "date": paid_on,
                "amount": str(plan.get("amount", "")),
                "method": str(plan.get("payment_method") or "Not specified"),
                "status": "PAID",
            })
        self.save_data()
        self.refresh_all()

    def show_row_actions(self, plan_id, button):
        """Show a polished three-dot action menu for one specific plan."""
        plan = self.get_plan_by_id(plan_id)
        if not plan:
            return

        self.selected_id = str(plan_id)

        menu = QMenu(self)
        menu.setObjectName("planActionsMenu")
        menu.setStyleSheet("""
            QMenu#planActionsMenu {
                background: #182235;
                color: #EAF2FF;
                border: 1px solid rgba(125, 170, 235, 120);
                border-radius: 14px;
                padding: 8px;
            }
            QMenu#planActionsMenu::item {
                padding: 10px 32px 10px 14px;
                border-radius: 9px;
                margin: 2px 0;
                min-width: 185px;
            }
            QMenu#planActionsMenu::item:selected {
                background: rgba(79, 140, 255, 120);
                color: white;
            }
            QMenu#planActionsMenu::separator {
                height: 1px;
                background: rgba(255,255,255,35);
                margin: 6px 8px;
            }
        """)

        edit_action = menu.addAction("✏️  Edit Plan")
        notes_action = menu.addAction("📝  Add / View Notes")
        paid_action = menu.addAction("💳  Mark as Paid")
        duplicate_action = menu.addAction("📋  Duplicate Plan")
        menu.addSeparator()
        delete_action = menu.addAction("🗑️  Delete Plan")

        # Make the current state clear when the plan is already paid.
        if str(plan.get("payment_status", "PENDING PAYMENT")).upper() == "PAID":
            paid_action.setText("💳  Mark as Unpaid")

        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

        if chosen == edit_action:
            self.edit_plan()
        elif chosen == notes_action:
            self.quick_edit_notes(plan)
        elif chosen == paid_action:
            self.mark_selected_plan_paid()
        elif chosen == duplicate_action:
            self.duplicate_selected_plan()
        elif chosen == delete_action:
            self.delete_plan()

    def mark_selected_plan_paid(self):
        """Toggle the selected plan's payment state from the Actions menu."""
        plan = self.selected_plan()
        if not plan:
            return
        current = str(plan.get("payment_status", "PENDING PAYMENT")).upper()
        is_paid = current == "PAID"
        plan["payment_status"] = "PENDING PAYMENT" if is_paid else "PAID"
        if not is_paid:
            paid_on = date.today().strftime("%d-%m-%Y")
            plan["last_payment_date"] = paid_on
            history = plan.setdefault("payment_history", [])
            history.append({
                "date": paid_on,
                "amount": str(plan.get("amount", "")),
                "method": str(plan.get("payment_method") or "Not specified"),
                "status": "PAID",
            })
        self.save_data()
        self.refresh_all()

    def duplicate_selected_plan(self):
        """Create a copy of the selected plan with a fresh unique ID."""
        plan = self.selected_plan()
        if not plan:
            return
        duplicate = dict(plan)
        duplicate["id"] = int(datetime.now().timestamp() * 1000)
        duplicate["name"] = f"{plan.get('name', 'Plan')} (Copy)"
        duplicate["payment_status"] = "PENDING PAYMENT"
        duplicate["last_payment_date"] = ""
        self.plans.append(duplicate)
        self.selected_id = str(duplicate["id"])
        self.save_data()
        self.refresh_all()

    def quick_edit_notes(self, plan):
        """Quickly add or update notes without opening the full plan editor."""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Notes — {plan.get('name', 'Plan')}")
        fit_dialog(dlg, 480, 280, 360, 240)

        layout = QVBoxLayout(dlg)

        title = QLabel(f"📝 Notes for: {plan.get('name', 'Plan')}")
        title.setStyleSheet("font-size:16px; font-weight:700;")
        layout.addWidget(title)

        notes_box = QTextEdit()
        notes_box.setPlaceholderText("Write notes about this plan...")
        notes_box.setPlainText(str(plan.get("notes", "")))
        layout.addWidget(notes_box)

        buttons = QHBoxLayout()
        save_btn = QPushButton("💾 Save Notes")
        cancel_btn = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def save_notes():
            plan["notes"] = notes_box.toPlainText().strip()
            save_plans(self.plans)
            self.apply_dashboard_customization()
            self.refresh_all()
            dlg.accept()

        save_btn.clicked.connect(save_notes)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def show_plan_details(self):
        """Premium glass Plan Details popup with information, payments, notes and timeline."""
        plan = self.selected_plan()
        if not plan:
            QMessageBox.information(self, "Plan Details", "Please select a plan first.")
            return

        accent = getattr(Theme, "accent_color", "#6C8DFF") or "#6C8DFF"
        currency = CURRENCY_SYMBOLS.get(self.settings.get("currency"), "₹")
        status = self.status(plan)
        payment_status = str(plan.get("payment_status", "PENDING PAYMENT")).upper()

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Plan Details — {plan.get('name', 'Plan')}")
        fit_dialog(dlg, 660, 520, 440, 360)
        dlg.setModal(True)
        dlg.setStyleSheet(f"""
            QDialog {{ background:#142033; color:#EAF2FF; }}
            QTabWidget::pane {{ border:1px solid rgba(255,255,255,38); border-radius:13px; background:rgba(255,255,255,10); top:-1px; }}
            QTabBar::tab {{ background:rgba(255,255,255,10); color:#B9C7DB; padding:8px 12px; margin-right:3px; border-radius:9px; font-weight:700; }}
            QTabBar::tab:selected {{ background:rgba(108,141,255,95); color:white; }}
            QLabel#sectionTitle {{ font-size:15px; font-weight:850; color:white; }}
            QLabel#fieldName {{ color:#91A3BC; font-weight:700; }}
            QLabel#fieldValue {{ color:#F4F8FF; font-weight:700; }}
            QTextEdit {{ background:rgba(255,255,255,12); color:#F4F8FF; border:1px solid rgba(255,255,255,35); border-radius:11px; padding:7px; }}
            QPushButton {{ background:rgba(108,141,255,95); color:white; border:1px solid rgba(255,255,255,70); border-radius:10px; padding:7px 12px; font-weight:800; }}
            QPushButton:hover {{ background:rgba(108,141,255,145); }}
        """)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        # Header
        header = QFrame()
        header.setStyleSheet("background:rgba(255,255,255,12); border:1px solid rgba(255,255,255,45); border-radius:15px;")
        hl = QHBoxLayout(header); hl.setContentsMargins(13, 10, 13, 10)
        icon = QLabel(str(plan.get("icon") or "📦")); icon.setStyleSheet("font-size:24px; border:0;")
        titles = QVBoxLayout()
        name = QLabel(str(plan.get("name", "Plan"))); name.setStyleSheet("font-size:19px; font-weight:900; color:white; border:0;")
        subtitle = QLabel(f"{plan.get('category', '📦 Other')}  •  {status}")
        subtitle.setStyleSheet("color:#AFC0D8; font-weight:700; border:0;")
        titles.addWidget(name); titles.addWidget(subtitle)
        badge = QLabel("● PAID" if payment_status == "PAID" else "● PENDING")
        badge.setAlignment(Qt.AlignCenter); badge.setMinimumWidth(92)
        badge.setStyleSheet(f"color:{'#62E0A5' if payment_status == 'PAID' else '#FFB45C'}; background:rgba(255,255,255,10); border:1px solid rgba(255,255,255,30); border-radius:11px; padding:6px 8px; font-weight:900;")
        hl.addWidget(icon); hl.addLayout(titles, 1); hl.addWidget(badge)
        root.addWidget(header)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        def add_field(layout, label, value):
            row = QFrame(); row.setStyleSheet("background:rgba(255,255,255,7); border:1px solid rgba(255,255,255,20); border-radius:9px;")
            rl = QHBoxLayout(row); rl.setContentsMargins(10, 7, 10, 7)
            a = QLabel(label); a.setObjectName("fieldName")
            b = QLabel(str(value if value not in (None, "") else "—")); b.setObjectName("fieldValue"); b.setAlignment(Qt.AlignRight | Qt.AlignVCenter); b.setWordWrap(True)
            rl.addWidget(a, 1); rl.addWidget(b, 2)
            layout.addWidget(row)

        # Full information
        info = QWidget(); info_layout = QVBoxLayout(info); info_layout.setContentsMargins(12, 12, 12, 12); info_layout.setSpacing(6)
        section = QLabel("FULL PLAN INFORMATION"); section.setObjectName("sectionTitle"); info_layout.addWidget(section)
        add_field(info_layout, "Plan Name", plan.get("name"))
        add_field(info_layout, "Category", plan.get("category", "📦 Other"))
        add_field(info_layout, "Renewal Date", self.format_plan_date(plan.get("renewal_date", "")))
        add_field(info_layout, "Amount", f"{currency} {plan.get('amount', '—')}")
        add_field(info_layout, "Days Left", "—" if status == "PAID" else self.days_left(plan))
        add_field(info_layout, "Billing Cycle", plan.get("billing_cycle", "Yearly"))
        add_field(info_layout, "Reminder Timing", f"{plan.get('reminder_days', self.settings.get('default_reminder_days', 3))} days before")
        add_field(info_layout, "Auto Renew", "Enabled" if plan.get("auto_renew", False) else "Disabled")
        add_field(info_layout, "Website", plan.get("website") or "—")
        add_field(info_layout, "Account / Email", plan.get("account_email") or "—")
        info_layout.addStretch(1)
        info_scroll = QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setFrameShape(QFrame.NoFrame)
        info_scroll.setStyleSheet("background:transparent;")
        info_scroll.setWidget(info)
        tabs.addTab(info_scroll, "📋 Information")

        # Payment history
        payments = QWidget(); pay_layout = QVBoxLayout(payments); pay_layout.setContentsMargins(12, 12, 12, 12); pay_layout.setSpacing(7)
        pay_title = QLabel("PAYMENT HISTORY"); pay_title.setObjectName("sectionTitle"); pay_layout.addWidget(pay_title)
        history = list(plan.get("payment_history", []))
        if not history and plan.get("last_payment_date"):
            history = [{"date": plan.get("last_payment_date"), "amount": plan.get("amount", ""), "method": plan.get("payment_method") or "Not specified", "status": "PAID"}]
        if history:
            for entry in reversed(history):
                card = QFrame(); card.setStyleSheet("background:rgba(255,255,255,9); border:1px solid rgba(255,255,255,28); border-radius:14px;")
                cl = QHBoxLayout(card); cl.setContentsMargins(14, 11, 14, 11)
                left = QVBoxLayout(); date_lbl = QLabel(f"💳  {entry.get('date', '—')}"); date_lbl.setStyleSheet("font-weight:850; color:white; border:0;")
                method_lbl = QLabel(str(entry.get("method") or "Not specified")); method_lbl.setStyleSheet("color:#9FB0C8; border:0;")
                left.addWidget(date_lbl); left.addWidget(method_lbl)
                amount_lbl = QLabel(f"{currency} {entry.get('amount', plan.get('amount', '—'))}"); amount_lbl.setStyleSheet("font-size:15px; font-weight:900; color:#62E0A5; border:0;")
                cl.addLayout(left, 1); cl.addWidget(amount_lbl)
                pay_layout.addWidget(card)
        else:
            empty = QLabel("No payment has been recorded yet.\nWhen you mark this plan as paid, Renewly will save it here.")
            empty.setAlignment(Qt.AlignCenter); empty.setStyleSheet("color:#91A3BC; padding:45px; border:1px dashed rgba(255,255,255,35); border-radius:16px;")
            pay_layout.addWidget(empty)
        pay_layout.addStretch(1)
        tabs.addTab(payments, "💳 Payment History")

        # Notes
        notes_page = QWidget(); notes_layout = QVBoxLayout(notes_page); notes_layout.setContentsMargins(12, 12, 12, 12); notes_layout.setSpacing(8)
        notes_title = QLabel("NOTES"); notes_title.setObjectName("sectionTitle"); notes_layout.addWidget(notes_title)
        notes_box = QTextEdit(); notes_box.setMinimumHeight(150); notes_box.setPlaceholderText("Write useful notes about this plan..."); notes_box.setPlainText(str(plan.get("notes", ""))); notes_layout.addWidget(notes_box, 1)
        save_notes_btn = QPushButton("💾 Save Notes")
        notes_layout.addWidget(save_notes_btn, alignment=Qt.AlignRight)
        def save_details_notes():
            plan["notes"] = notes_box.toPlainText().strip()
            self.save_data(); self.refresh_all()
            QMessageBox.information(dlg, "Notes Saved", "Your plan notes were saved successfully.")
        save_notes_btn.clicked.connect(save_details_notes)
        tabs.addTab(notes_page, "📝 Notes")

        # Renewal timeline
        timeline = QWidget(); tl = QVBoxLayout(timeline); tl.setContentsMargins(12, 12, 12, 12); tl.setSpacing(8)
        timeline_title = QLabel("RENEWAL TIMELINE"); timeline_title.setObjectName("sectionTitle"); tl.addWidget(timeline_title)
        events = []
        if plan.get("last_payment_date"):
            events.append(("💳", plan.get("last_payment_date"), "Last payment recorded", "Payment status updated to PAID"))
        events.append(("🔔", f"{plan.get('reminder_days', self.settings.get('default_reminder_days', 3))} days before renewal", "Reminder window", "Renewly will notify you based on your reminder timing."))
        events.append(("📅", self.format_plan_date(plan.get("renewal_date", "")), "Next renewal date", f"Current status: {status}"))
        for symbol, when, title_text, desc in events:
            card = QFrame(); card.setStyleSheet("background:rgba(255,255,255,8); border-left:3px solid %s; border-radius:13px;" % accent)
            cl = QHBoxLayout(card); cl.setContentsMargins(14, 12, 14, 12)
            ico = QLabel(symbol); ico.setStyleSheet("font-size:22px; border:0;")
            text_col = QVBoxLayout(); a = QLabel(title_text); a.setStyleSheet("font-weight:900; color:white; border:0;"); b = QLabel(desc); b.setWordWrap(True); b.setStyleSheet("color:#9FB0C8; border:0;")
            text_col.addWidget(a); text_col.addWidget(b)
            when_lbl = QLabel(str(when)); when_lbl.setStyleSheet("color:#C7D5E8; font-weight:750; border:0;")
            cl.addWidget(ico); cl.addLayout(text_col, 1); cl.addWidget(when_lbl)
            tl.addWidget(card)
        tl.addStretch(1)
        tabs.addTab(timeline, "🗓️ Renewal Timeline")

        footer = QHBoxLayout()
        edit_btn = QPushButton("✏️ Edit Plan")
        close_btn = QPushButton("Close")
        footer.addWidget(edit_btn); footer.addStretch(1); footer.addWidget(close_btn)
        root.addLayout(footer)
        edit_btn.clicked.connect(lambda: (dlg.accept(), self.edit_plan()))
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()

    def show_plan_context_menu(self, position):
        """Show right-click actions for the plan under the mouse."""
        row = self.table.rowAt(position.y())
        if row < 0:
            return

        # Make the right-clicked plan the active selection.
        self.table.clearSelection()
        self.table.selectRow(row)

        id_item = self.table.item(row, 0)
        if not id_item:
            return
        self.selected_id = id_item.text()

        menu = QMenu(self)
        view_action = menu.addAction("📋 View Full Details")
        edit_action = menu.addAction("✏️ Edit Plan")
        delete_action = menu.addAction("Move to Recycle Bin")
        menu.addSeparator()
        copy_name_action = menu.addAction("Copy Plan Name")

        chosen = menu.exec(self.table.viewport().mapToGlobal(position))

        if chosen == view_action:
            self.show_plan_details()
        elif chosen == edit_action:
            self.edit_plan()
        elif chosen == delete_action:
            self.delete_plan()
        elif chosen == copy_name_action:
            plan = self.selected_plan()
            if plan:
                QApplication.clipboard().setText(str(plan.get("name", "")))

    def selected_plans(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        ids = []
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                ids.append(item.text())
        return [p for p in self.plans if str(p.get("id")) in ids]

    def select_all_plans(self):
        self.table.selectAll()

    def clear_plan_selection(self):
        self.table.clearSelection()
        self.selected_id = None

    def bulk_edit_plans(self):
        plans = self.selected_plans()
        if not plans:
            QMessageBox.warning(self, "Select Plans", "Please select one or more plans first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Bulk Edit Plans")
        form = QFormLayout(dlg)
        category = QComboBox()
        category.addItem("Keep Current Category")
        category.addItems(PLAN_CATEGORIES)
        reminder = QSpinBox()
        reminder.setRange(-1, 3650)
        reminder.setSpecialValueText("Keep Current Reminder Days")
        reminder.setValue(-1)
        form.addRow("Plans selected", QLabel(str(len(plans))))
        form.addRow("Category", category)
        form.addRow("Reminder Days", reminder)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec():
            for plan in plans:
                if category.currentIndex() > 0:
                    plan["category"] = category.currentText()
                if reminder.value() >= 0:
                    plan["reminder_days"] = str(reminder.value())
            self.save_data()
            self.refresh_all()

    def bulk_delete_plans(self):
        plans = self.selected_plans()
        if not plans:
            QMessageBox.warning(self, "Select Plans", "Please select one or more plans first.")
            return
        if QMessageBox.question(
            self, "Delete Selected Plans",
            f"Move {len(plans)} selected plan(s) to the Recycle Bin?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        for plan in plans:
            if plan in self.plans:
                self._queue_cloud_plan_deletion(plan)
                self.recycle.append(plan)
                self.plans.remove(plan)
        save_json(RECYCLE_FILE, self.recycle)
        self.save_data()
        self.selected_id = None
        self.refresh_all()

    # ---------------- plan actions ----------------

    def _queue_cloud_plan_deletion(self, plan):
        """Queue a cloud deletion so it survives offline/restart scenarios."""
        cloud_id = plan.get("cloud_plan_id") if isinstance(plan, dict) else None
        if not str(cloud_id or "").strip().isdigit() or int(cloud_id) <= 0:
            return
        account = str(self._cloud_email or self.settings.get("cloud_account_email", "") or "").strip().lower()
        if not account:
            return
        deleted_by_account = self.settings.get("cloud_deleted_plan_ids_by_account", {})
        if not isinstance(deleted_by_account, dict):
            deleted_by_account = {}
        pending = deleted_by_account.get(account, [])
        if not isinstance(pending, list):
            pending = []
        cloud_id = int(cloud_id)
        existing = {int(v) for v in pending if str(v).strip().isdigit()}
        if cloud_id not in existing:
            pending.append(cloud_id)
        deleted_by_account[account] = pending
        self.settings["cloud_deleted_plan_ids_by_account"] = deleted_by_account
        save_app_settings(self.settings)

    def add_plan(self):
        dlg = PlanDialog(parent=self)
        if dlg.exec():
            p = dlg.result_plan()
            p["id"] = int(datetime.now().timestamp() * 1000)
            p.setdefault("notes", "")
            self.plans.append(p)
            self.save_data()
            self.refresh_all()

    def edit_plan(self):
        p = self.selected_plan()
        if not p:
            QMessageBox.warning(self, "Select Plan", "Please select a plan first.")
            return
        dlg = PlanDialog(p, self)
        if dlg.exec():
            p.update(dlg.result_plan())
            self.save_data()
            self.refresh_all()

    def delete_plan(self):
        p = self.selected_plan()
        if not p:
            QMessageBox.warning(self, "Select Plan", "Please select a plan first.")
            return
        if QMessageBox.question(
            self, "Delete Plan",
            f"Move '{p.get('name')}' to Recycle Bin?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self._queue_cloud_plan_deletion(p)
            self.recycle.append(p)
            self.plans.remove(p)
            save_json(RECYCLE_FILE, self.recycle)
            self.save_data()
            self.selected_id = None
            self.refresh_all()

    # ---------------- secondary windows ----------------

    def analytics_metrics(self):
        """Return normalized spending metrics for the Analytics dashboard."""
        category_totals = {category: 0.0 for category in PLAN_CATEGORIES}
        monthly_total = 0.0
        yearly_total = 0.0
        monthly_count = 0
        yearly_count = 0
        normalized = []

        multipliers = {
            "Monthly": 12,
            "Quarterly": 4,
            "Half-Yearly": 2,
            "Yearly": 1,
        }

        for plan in self.plans:
            amount = self._amount_sort_value(plan)
            cycle = str(plan.get("billing_cycle", "Yearly"))
            multiplier = multipliers.get(cycle, 1)
            annual = amount * multiplier
            monthly_equivalent = annual / 12.0

            monthly_total += monthly_equivalent
            yearly_total += annual
            if cycle == "Monthly":
                monthly_count += 1
            elif cycle == "Yearly":
                yearly_count += 1

            category = str(plan.get("category", "📦 Other"))
            if category not in category_totals:
                category = "📦 Other"
            category_totals[category] += annual

            normalized.append({
                "plan": plan,
                "amount": amount,
                "annual": annual,
                "monthly_equivalent": monthly_equivalent,
                "cycle": cycle,
            })

        expensive = max(normalized, key=lambda item: item["annual"], default=None)
        upcoming = []
        today = date.today()
        for item in normalized:
            try:
                renewal = datetime.strptime(
                    str(item["plan"].get("renewal_date", "")), "%d-%m-%Y"
                ).date()
                days = (renewal - today).days
                if days >= 0:
                    upcoming.append((days, renewal, item))
            except Exception:
                pass
        upcoming.sort(key=lambda row: row[0])

        return {
            "monthly_total": monthly_total,
            "yearly_total": yearly_total,
            "monthly_count": monthly_count,
            "yearly_count": yearly_count,
            "category_totals": category_totals,
            "most_expensive": expensive,
            "upcoming": upcoming[:8],
        }

    def show_analytics(self):
        self.refresh_analytics_page()
        self.content_stack.setCurrentWidget(self.analytics_page)

    def show_dashboard_home(self):
        self.content_stack.setCurrentWidget(self.dashboard_page)

    def show_smart_assistant(self):
        self.refresh_smart_assistant()
        self.content_stack.setCurrentWidget(self.smart_assistant_page)

    def build_smart_assistant_page(self):
        page = QWidget()
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(14)
        header = GlassFrame(radius=30, alpha=145); h = QHBoxLayout(header); h.setContentsMargins(25,18,25,18)
        box = QVBoxLayout(); title = QLabel("🤖 SMART RENEWAL ASSISTANT"); title.setStyleSheet("font-size:26px; font-weight:900; color:#172033;")
        sub = QLabel("Your personal renewal assistant — priorities, spending and quick recommendations"); sub.setStyleSheet("color:#5E6D83;")
        box.addWidget(title); box.addWidget(sub)
        refresh_btn=GlassButton("↻ Refresh Insights"); refresh_btn.clicked.connect(self.refresh_smart_assistant)
        back=GlassButton("← Home Page"); back.clicked.connect(self.show_dashboard_home)
        h.addLayout(box); h.addStretch(1); h.addWidget(refresh_btn); h.addWidget(back); layout.addWidget(header)
        hero=GlassFrame(radius=28, alpha=145); hl=QHBoxLayout(hero); hl.setContentsMargins(24,20,24,20)
        icon=QLabel("🤖"); icon.setStyleSheet("font-size:42px;")
        vb=QVBoxLayout(); self.smart_summary_title=QLabel("💡 Your smart summary"); self.smart_summary_title.setStyleSheet("font-size:21px; font-weight:900; color:#172033;")
        self.smart_summary_text=QLabel(); self.smart_summary_text.setWordWrap(True); self.smart_summary_text.setStyleSheet("font-size:14px; color:#526178;")
        vb.addWidget(self.smart_summary_title); vb.addWidget(self.smart_summary_text); hl.addWidget(icon); hl.addLayout(vb,1); layout.addWidget(hero)
        cards=QHBoxLayout(); self.smart_due_card=StatCard("DUE THIS WEEK","0","#F59E0B"); self.smart_expired_card=StatCard("NEEDS ATTENTION","0","#EF4444"); self.smart_spending_card=StatCard("MONTHLY ESTIMATE","₹ 0.00","#3182F6"); self.smart_next_card=StatCard("NEXT RENEWAL","—","#8B5CF6")
        for c in (self.smart_due_card,self.smart_expired_card,self.smart_spending_card,self.smart_next_card): cards.addWidget(c)
        layout.addLayout(cards)
        body=QHBoxLayout(); body.setSpacing(14)
        card=GlassFrame(radius=28,alpha=135); il=QVBoxLayout(card); lab=QLabel("✨ SMART INSIGHTS"); lab.setStyleSheet("font-size:18px; font-weight:900; color:#172033;"); hint=QLabel("Urgent issues are shown first and insights update from your plan data."); hint.setStyleSheet("color:#66748A; font-size:11px;")
        self.smart_insights=QTableWidget(0,3); self.smart_insights.setHorizontalHeaderLabels(["PRIORITY","INSIGHT","RECOMMENDED ACTION"]); self.smart_insights.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents); self.smart_insights.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.smart_insights.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.smart_insights.verticalHeader().hide(); self.smart_insights.setEditTriggers(QTableWidget.NoEditTriggers); self.smart_insights.setSelectionMode(QTableWidget.NoSelection)
        il.addWidget(lab); il.addWidget(hint); il.addWidget(self.smart_insights,1); body.addWidget(card,3)
        actions=GlassFrame(radius=28,alpha=135); al=QVBoxLayout(actions); at=QLabel("⚡ QUICK ACTIONS"); at.setStyleSheet("font-size:17px; font-weight:900; color:#172033;"); al.addWidget(at)
        for text,fn in [("➕ Add New Plan",self.add_plan),("📅 View Home Page",self.show_dashboard_home),("📊 Open Analytics",self.show_analytics),("🗓 Open Calendar",self.show_calendar)]:
            b=GlassButton(text); b.setMinimumHeight(42); b.clicked.connect(fn); al.addWidget(b)
        al.addStretch(1); body.addWidget(actions,1); layout.addLayout(body,1)
        return page

    def refresh_smart_assistant(self):
        if not hasattr(self,"smart_insights"): return
        metrics=self.analytics_metrics(); symbol=CURRENCY_SYMBOLS.get(self.settings.get("currency"),"₹"); today=date.today(); week=[]; expired=[]; upcoming=[]; insights=[]
        for plan in self.plans:
            status=self.status(plan)
            if status=="EXPIRED": expired.append(plan)
            try: renewal=self.parse_date(plan.get("renewal_date",""))
            except Exception: renewal=None
            if renewal:
                days=(renewal-today).days
                if 0<=days<=7: week.append((days,plan))
                if days>=0: upcoming.append((days,renewal,plan))
        week.sort(key=lambda x:x[0]); upcoming.sort(key=lambda x:x[0])
        monthly=metrics.get("monthly_total",metrics.get("monthly_estimate",0))
        if expired: insights.append(("🔴 URGENT",f"You have {len(expired)} expired renewal(s) that need attention.","Review them and renew, cancel, or remove outdated plans."))
        if week:
            names=", ".join(str(p.get("name","Plan")) for _,p in week[:3]); insights.append(("🟠 THIS WEEK",f"You have {len(week)} renewal(s) due this week: {names}.","Check payment readiness before the renewal date."))
        most=metrics.get("most_expensive")
        if most: insights.append(("🔵 SPENDING",f"{most['plan'].get('name','A plan')} is your highest yearly-cost plan at {symbol} {most['annual']:,.2f}.","Review whether the plan still provides enough value."))
        if monthly: insights.append(("🟣 BUDGET",f"Your estimated monthly renewal spending is {symbol} {monthly:,.2f}.","Use Analytics to find opportunities to reduce costs."))
        if upcoming:
            days,renewal,plan=upcoming[0]; when="today" if days==0 else "tomorrow" if days==1 else f"in {days} days"; insights.append(("🟢 NEXT UP",f"Your next renewal is {plan.get('name','Plan')} {when} ({renewal.strftime('%d %b %Y')}).","Plan ahead so the renewal is not missed."))
        if not insights: insights.append(("🔵 GET STARTED","Add your plans to unlock personalized renewal and spending insights.","Use Add New Plan to get started."))
        if expired: summary=f"⚠️ You have {len(expired)} expired renewal(s) and {len(week)} renewal(s) due this week. Start with the urgent items."
        elif week: summary=f"📅 You have {len(week)} renewal(s) due this week. Your assistant recommends checking them before renewal."
        elif upcoming: summary=f"✅ Things look under control. Your next renewal is {upcoming[0][2].get('name','Plan')} in {upcoming[0][0]} day(s)."
        else: summary="Add renewal plans and the assistant will automatically track priorities, spending and upcoming dates."
        self.smart_summary_text.setText(summary)
        self.smart_due_card.set_value(str(len(week))); self.smart_expired_card.set_value(str(len(expired)+len(week))); self.smart_spending_card.set_value(f"{symbol} {monthly:,.2f}"); self.smart_next_card.set_value(upcoming[0][2].get("name","—")[:18] if upcoming else "—")
        self.smart_insights.setRowCount(0)
        for row,values in enumerate(insights):
            self.smart_insights.insertRow(row)
            for col,value in enumerate(values):
                item=QTableWidgetItem(value)
                if col==0: item.setTextAlignment(Qt.AlignCenter)
                self.smart_insights.setItem(row,col,item)

    def build_analytics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        header = GlassFrame(radius=30, alpha=145)
        h = QHBoxLayout(header)
        h.setContentsMargins(25, 18, 25, 18)
        title_box = QVBoxLayout()
        title = QLabel("ANALYTICS & EXPENSE HOME PAGE")
        title.setStyleSheet("font-size:26px; font-weight:900; color:#172033;")
        sub = QLabel("Interactive spending insights based on your active plan data")
        sub.setStyleSheet("color:#5E6D83;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        back = GlassButton("← Home Page")
        back.setMinimumWidth(150)
        back.clicked.connect(self.show_dashboard_home)
        h.addLayout(title_box)
        h.addStretch(1)
        h.addWidget(back)
        layout.addWidget(header)

        cards = QHBoxLayout()
        self.analytics_monthly_card = StatCard("MONTHLY SPENDING", "₹ 0.00", "#3182F6")
        self.analytics_yearly_card = StatCard("YEARLY SPENDING", "₹ 0.00", "#22C55E")
        self.analytics_estimate_card = StatCard("MONTHLY ESTIMATE", "₹ 0.00", "#8B5CF6")
        self.analytics_expensive_card = StatCard("MOST EXPENSIVE", "₹ 0.00", "#F59E0B")
        for card in (
            self.analytics_monthly_card, self.analytics_yearly_card,
            self.analytics_estimate_card, self.analytics_expensive_card
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        charts = QHBoxLayout()
        charts.setSpacing(14)

        category_card = GlassFrame(radius=28, alpha=135)
        category_layout = QVBoxLayout(category_card)
        category_title = QLabel("📊 SPENDING BY CATEGORY")
        category_title.setStyleSheet("font-size:18px; font-weight:900; color:#172033;")
        category_hint = QLabel("Hover over a bar for exact spending")
        category_hint.setStyleSheet("color:#66748A; font-size:11px;")
        self.category_chart = InteractiveBarChart()
        category_layout.addWidget(category_title)
        category_layout.addWidget(category_hint)
        category_layout.addWidget(self.category_chart, 1)
        charts.addWidget(category_card, 1)

        trend_card = GlassFrame(radius=28, alpha=135)
        trend_layout = QVBoxLayout(trend_card)
        trend_title = QLabel("📈 12-MONTH EXPENSE ESTIMATE")
        trend_title.setStyleSheet("font-size:18px; font-weight:900; color:#172033;")
        trend_hint = QLabel("Hover over a point to inspect the monthly estimate")
        trend_hint.setStyleSheet("color:#66748A; font-size:11px;")
        self.trend_chart = InteractiveLineChart()
        trend_layout.addWidget(trend_title)
        trend_layout.addWidget(trend_hint)
        trend_layout.addWidget(self.trend_chart, 1)
        charts.addWidget(trend_card, 1)

        layout.addLayout(charts, 1)

        lower = QHBoxLayout()
        lower.setSpacing(14)

        cycle_card = GlassFrame(radius=26, alpha=135)
        cycle_layout = QVBoxLayout(cycle_card)
        cycle_title = QLabel("🔄 MONTHLY VS YEARLY PLANS")
        cycle_title.setStyleSheet("font-size:17px; font-weight:900; color:#172033;")
        self.monthly_cycle_label = QLabel()
        self.monthly_cycle_bar = QProgressBar()
        self.yearly_cycle_label = QLabel()
        self.yearly_cycle_bar = QProgressBar()
        for bar, color in [
            (self.monthly_cycle_bar, "#3182F6"),
            (self.yearly_cycle_bar, "#22C55E"),
        ]:
            bar.setRange(0, 100)
            bar.setTextVisible(True)
            bar.setStyleSheet(
                f"QProgressBar {{ background:rgba(255,255,255,70); border:0; border-radius:9px; height:24px; }}"
                f"QProgressBar::chunk {{ background:{color}; border-radius:9px; }}"
            )
        self.other_cycle_label = QLabel()
        cycle_layout.addWidget(cycle_title)
        cycle_layout.addWidget(self.monthly_cycle_label)
        cycle_layout.addWidget(self.monthly_cycle_bar)
        cycle_layout.addWidget(self.yearly_cycle_label)
        cycle_layout.addWidget(self.yearly_cycle_bar)
        cycle_layout.addWidget(self.other_cycle_label)
        cycle_layout.addStretch(1)
        lower.addWidget(cycle_card, 1)

        upcoming_card = GlassFrame(radius=26, alpha=135)
        upcoming_layout = QVBoxLayout(upcoming_card)
        upcoming_title = QLabel("📅 UPCOMING RENEWAL EXPENSES")
        upcoming_title.setStyleSheet("font-size:17px; font-weight:900; color:#172033;")
        self.analytics_upcoming = QTableWidget(0, 4)
        self.analytics_upcoming.setHorizontalHeaderLabels(["PLAN", "RENEWAL", "IN", "EXPENSE"])
        self.analytics_upcoming.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.analytics_upcoming.verticalHeader().hide()
        self.analytics_upcoming.setEditTriggers(QTableWidget.NoEditTriggers)
        self.analytics_upcoming.setSelectionMode(QTableWidget.NoSelection)
        upcoming_layout.addWidget(upcoming_title)
        upcoming_layout.addWidget(self.analytics_upcoming, 1)
        lower.addWidget(upcoming_card, 2)

        layout.addLayout(lower, 1)
        return page

    def refresh_analytics_page(self):
        if not hasattr(self, "category_chart"):
            return
        metrics = self.analytics_metrics()
        symbol = CURRENCY_SYMBOLS.get(self.settings.get("currency"), "₹")

        self.analytics_monthly_card.value.setText(f"{symbol} {metrics['monthly_total']:,.2f}")
        self.analytics_yearly_card.value.setText(f"{symbol} {metrics['yearly_total']:,.2f}")
        self.analytics_estimate_card.value.setText(f"{symbol} {metrics['monthly_total']:,.2f}")

        most = metrics["most_expensive"]
        if most:
            self.analytics_expensive_card.value.setText(f"{symbol} {most['annual']:,.2f}")
            self.analytics_expensive_card.setToolTip(
                f"{most['plan'].get('name', '')} • {symbol} {most['annual']:,.2f} yearly estimate"
            )
        else:
            self.analytics_expensive_card.value.setText(f"{symbol} 0.00")
            self.analytics_expensive_card.setToolTip("No plans available")

        category_data = []
        for category, total in sorted(
            metrics["category_totals"].items(), key=lambda item: item[1], reverse=True
        ):
            if total > 0:
                category_data.append(
                    (category, total, CATEGORY_COLORS.get(category, "#64748B"))
                )
        self.category_chart.set_data(
            [(name, f"{symbol} {value:,.2f}", color) for name, value, color in category_data]
        )
        # Store numeric values separately for accurate chart scaling.
        self.category_chart.data = category_data
        self.category_chart.update()

        monthly_value = metrics["monthly_total"]
        month_labels = []
        current_month = date.today().replace(day=1)
        for offset in range(12):
            month_number = current_month.month + offset
            year = current_month.year + (month_number - 1) // 12
            month = ((month_number - 1) % 12) + 1
            month_date = date(year, month, 1)
            month_labels.append((month_date.strftime("%b"), monthly_value))
        self.trend_chart.set_values(
            [(label, f"{symbol} {value:,.2f}") for label, value in month_labels]
        )
        self.trend_chart.values = month_labels
        self.trend_chart.update()

        total_cycle = metrics["monthly_count"] + metrics["yearly_count"]
        monthly_pct = (metrics["monthly_count"] / total_cycle * 100) if total_cycle else 0
        yearly_pct = (metrics["yearly_count"] / total_cycle * 100) if total_cycle else 0
        self.monthly_cycle_label.setText(
            f"Monthly plans: {metrics['monthly_count']} ({monthly_pct:.0f}%)"
        )
        self.yearly_cycle_label.setText(
            f"Yearly plans: {metrics['yearly_count']} ({yearly_pct:.0f}%)"
        )
        self.monthly_cycle_bar.setValue(round(monthly_pct))
        self.monthly_cycle_bar.setFormat(f"{monthly_pct:.0f}%")
        self.yearly_cycle_bar.setValue(round(yearly_pct))
        self.yearly_cycle_bar.setFormat(f"{yearly_pct:.0f}%")
        self.other_cycle_label.setText(
            f"Other billing cycles: {len(self.plans) - total_cycle}"
        )

        self.analytics_upcoming.setRowCount(0)
        for row, (days, renewal, item) in enumerate(metrics["upcoming"]):
            self.analytics_upcoming.insertRow(row)
            plan = item["plan"]
            values = [
                str(plan.get("name", "")),
                self.format_plan_date(plan.get("renewal_date", "")),
                f"{days} days",
                f"{symbol} {item['amount']:,.2f}",
            ]
            for col, value in enumerate(values):
                self.analytics_upcoming.setItem(row, col, QTableWidgetItem(value))

        apply_theme_to(self.analytics_page)

    # ---------------- Purchases & Warranty ----------------
    def purchase_warranty_status(self, item):
        exp=str(item.get("warranty_expiry",""))
        if not exp: return "No Warranty"
        try:
            d=datetime.strptime(exp,"%d-%m-%Y").date(); days=(d-date.today()).days
            return "Expired" if days<0 else ("Due Soon" if days<=30 else "Active")
        except Exception: return "Unknown"

    def show_purchases(self):
        self.refresh_purchases(); self.content_stack.setCurrentWidget(self.purchases_page)

    def build_purchases_page(self):
        page=QWidget(); layout=QVBoxLayout(page); layout.setContentsMargins(0,0,0,0); layout.setSpacing(14)
        header=GlassFrame(radius=30,alpha=145); h=QHBoxLayout(header)
        box=QVBoxLayout(); title=QLabel("PURCHASES & WARRANTY MANAGEMENT"); title.setStyleSheet("font-size:25px; font-weight:900; color:#172033;")
        sub=QLabel("Store items, bills, photos, purchase details and warranty information in one place"); sub.setStyleSheet("color:#5E6D83;")
        box.addWidget(title); box.addWidget(sub); add=GlassButton("+ Add Item"); add.clicked.connect(self.add_purchase)
        h.addLayout(box); h.addStretch(1); h.addWidget(add); layout.addWidget(header)
        cards=QHBoxLayout()
        self.purchase_total_card=StatCard("TOTAL SPENT","₹ 0.00","#3182F6"); self.purchase_items_card=StatCard("TOTAL ITEMS","0","#8B5CF6")
        self.purchase_active_card=StatCard("UNDER WARRANTY","0","#22C55E"); self.purchase_due_card=StatCard("WARRANTY DUE SOON","0","#F59E0B")
        for x in (self.purchase_total_card,self.purchase_items_card,self.purchase_active_card,self.purchase_due_card): cards.addWidget(x)
        layout.addLayout(cards)
        filt=GlassFrame(radius=24,alpha=135); fl=QHBoxLayout(filt)
        self.purchase_search=QLineEdit(); self.purchase_search.setPlaceholderText("Search item, brand, store, model or serial number..."); self.purchase_search.textChanged.connect(self.refresh_purchase_table)
        self.purchase_category_filter=QComboBox(); self.purchase_category_filter.addItem("All Categories"); self.purchase_category_filter.addItems(PURCHASE_CATEGORIES); self.purchase_category_filter.currentTextChanged.connect(self.refresh_purchase_table)
        self.purchase_warranty_filter=QComboBox(); self.purchase_warranty_filter.addItems(["All Warranty Status","Active","Due Soon","Expired","No Warranty"]); self.purchase_warranty_filter.currentTextChanged.connect(self.refresh_purchase_table)
        fl.addWidget(self.purchase_search,1); fl.addWidget(self.purchase_category_filter); fl.addWidget(self.purchase_warranty_filter); layout.addWidget(filt)
        card=GlassFrame(radius=28,alpha=135); tl=QVBoxLayout(card); actions=QHBoxLayout()
        for label,slot in [("Edit Selected",self.edit_selected_purchase),("Delete Selected",self.delete_selected_purchase),("View Bill",lambda:self.preview_selected_purchase_attachment("bill_path")),("View Photo",lambda:self.preview_selected_purchase_attachment("photo_path"))]:
            b=GlassButton(label); b.clicked.connect(slot); actions.addWidget(b)
        actions.addStretch(1); tl.addLayout(actions)
        self.purchase_table=QTableWidget(0,10); self.purchase_table.setHorizontalHeaderLabels(["ITEM","CATEGORY","PRICE","PURCHASE DATE","BOUGHT FROM","WARRANTY","WARRANTY EXPIRY","STATUS","BILL","PHOTO"])
        self.purchase_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.purchase_table.verticalHeader().hide()
        self.purchase_table.setSelectionBehavior(QTableWidget.SelectRows); self.purchase_table.setSelectionMode(QTableWidget.SingleSelection); self.purchase_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.purchase_table.doubleClicked.connect(lambda index: self.preview_purchase_table_attachment(index) if index.column() in (8, 9) else self.edit_selected_purchase()); tl.addWidget(self.purchase_table,1); layout.addWidget(card,1)
        return page

    def purchase_dialog(self, item=None):
        data=dict(item or {}); dlg=QDialog(self); dlg.setWindowTitle("Edit Purchase" if item else "Add Purchase"); fit_dialog(dlg, 720, 760, 440, 420)
        root=QVBoxLayout(dlg); scroll=QScrollArea(); scroll.setWidgetResizable(True); body=QWidget(); form=QFormLayout(body)
        name=QLineEdit(data.get("name","")); cat=QComboBox(); cat.addItems(PURCHASE_CATEGORIES); cat.setCurrentText(data.get("category",PURCHASE_CATEGORIES[-1]))
        brand=QLineEdit(data.get("brand","")); model=QLineEdit(data.get("model","")); serial=QLineEdit(data.get("serial",""))
        qty=QSpinBox(); qty.setRange(1,9999); qty.setValue(int(data.get("quantity",1)))
        price=QDoubleSpinBox(); price.setRange(0,100000000); price.setDecimals(2); price.setValue(float(data.get("price",0)))
        store=QLineEdit(data.get("store","")); location=QLineEdit(data.get("location",""))
        payment=QComboBox(); payment.addItems(["Cash","UPI","Credit Card","Debit Card","Bank Transfer","Other"]); payment.setCurrentText(data.get("payment_method","Other"))
        pd=QDateEdit(); pd.setCalendarPopup(True); d=QDate.fromString(data.get("purchase_date",""),"dd-MM-yyyy"); pd.setDate(d if d.isValid() else QDate.currentDate())
        months=QSpinBox(); months.setRange(0,240); months.setValue(int(data.get("warranty_months",0)))
        ws=QDateEdit(); ws.setCalendarPopup(True); d=QDate.fromString(data.get("warranty_start",""),"dd-MM-yyyy"); ws.setDate(d if d.isValid() else pd.date())
        we=QDateEdit(); we.setCalendarPopup(True); d=QDate.fromString(data.get("warranty_expiry",""),"dd-MM-yyyy"); we.setDate(d if d.isValid() else ws.date().addMonths(months.value()))
        bill=QLineEdit(data.get("bill_path","")); bill.setReadOnly(True); photo=QLineEdit(data.get("photo_path","")); photo.setReadOnly(True)
        bb=QPushButton("Browse Bill / Invoice"); pb=QPushButton("Browse Item Photo")
        bb.clicked.connect(lambda: bill.setText(QFileDialog.getOpenFileName(dlg,"Select Bill / Invoice","","Documents and Images (*.pdf *.png *.jpg *.jpeg *.webp)")[0] or bill.text()))
        pb.clicked.connect(lambda: photo.setText(QFileDialog.getOpenFileName(dlg,"Select Item Photo","","Images (*.png *.jpg *.jpeg *.webp *.bmp)")[0] or photo.text()))
        notes=QTextEdit(data.get("notes","")); notes.setMinimumHeight(90)
        for lab,w in [("Item name *",name),("Category",cat),("Brand",brand),("Model number",model),("Serial number",serial),("Quantity",qty),("Purchase price",price),("Purchase date",pd),("Bought from",store),("Store / website",location),("Payment method",payment),("Warranty (months)",months),("Warranty start",ws),("Warranty expiry",we),("Saved bill path",bill),("",bb),("Saved item photo",photo),("",pb),("Notes",notes)]: form.addRow(lab,w)
        scroll.setWidget(body); root.addWidget(scroll,1); buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); root.addWidget(buttons)
        def upd(): we.setDate(ws.date().addMonths(months.value()))
        months.valueChanged.connect(upd); ws.dateChanged.connect(upd)
        def save():
            if not name.text().strip(): QMessageBox.warning(dlg,"Item Required","Please enter an item name."); return
            n=name.text().strip(); oldb=data.get("bill_path",""); oldp=data.get("photo_path","")
            chosenb=bill.text().strip(); chosenp=photo.text().strip()
            data.update({"id":data.get("id") or datetime.now().strftime("%Y%m%d%H%M%S%f"),"name":n,"category":cat.currentText(),"brand":brand.text().strip(),"model":model.text().strip(),"serial":serial.text().strip(),"quantity":qty.value(),"price":price.value(),"purchase_date":pd.date().toString("dd-MM-yyyy"),"store":store.text().strip(),"location":location.text().strip(),"payment_method":payment.currentText(),"warranty_months":months.value(),"warranty_start":ws.date().toString("dd-MM-yyyy") if months.value() else "","warranty_expiry":we.date().toString("dd-MM-yyyy") if months.value() else "","bill_path":oldb if chosenb==oldb else copy_purchase_attachment(chosenb,n,"bill"),"photo_path":oldp if chosenp==oldp else copy_purchase_attachment(chosenp,n,"photo"),"notes":notes.toPlainText().strip()}); dlg.accept()
        buttons.accepted.connect(save); buttons.rejected.connect(dlg.reject); apply_theme_to(dlg)
        return data if dlg.exec()==QDialog.Accepted else None

    def add_purchase(self):
        x=self.purchase_dialog()
        if x: self.purchases.append(x); save_purchases(self.purchases); self.refresh_purchases()

    def selected_purchase_index(self):
        rows=self.purchase_table.selectionModel().selectedRows()
        if not rows: QMessageBox.information(self,"Select Item","Please select an item first."); return None
        return self.purchase_table.item(rows[0].row(),0).data(Qt.UserRole)

    def edit_selected_purchase(self):
        i=self.selected_purchase_index()
        if i is None:return
        x=self.purchase_dialog(self.purchases[i])
        if x:self.purchases[i]=x; save_purchases(self.purchases); self.refresh_purchases()

    def delete_selected_purchase(self):
        i=self.selected_purchase_index()
        if i is not None and QMessageBox.question(self,"Delete Item",f"Delete '{self.purchases[i].get('name','')}'?",QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
            self.purchases.pop(i); save_purchases(self.purchases); self.refresh_purchases()

    def preview_purchase_table_attachment(self, index):
        row = index.row()
        item_cell = self.purchase_table.item(row, 0)
        if not item_cell:
            return
        purchase_index = item_cell.data(Qt.UserRole)
        if purchase_index is None or not (0 <= purchase_index < len(self.purchases)):
            return

        self.purchase_table.selectRow(row)
        key = "bill_path" if index.column() == 8 else "photo_path"
        self.preview_selected_purchase_attachment(key)

    def preview_selected_purchase_attachment(self, key):
        i = self.selected_purchase_index()
        if i is None:
            return

        path = self.purchases[i].get(key, "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Attachment", "No saved attachment is available for this item.")
            return

        suffix = Path(path).suffix.lower()
        image_types = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

        # Images are shown directly inside the app.
        if suffix in image_types:
            dlg = QDialog(self)
            dlg.setWindowTitle("Bill Preview" if key == "bill_path" else "Item Photo Preview")
            fit_dialog(dlg, 900, 700, 460, 360)

            root = QVBoxLayout(dlg)
            root.setContentsMargins(14, 14, 14, 14)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)

            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setMinimumSize(500, 400)

            pixmap = QPixmap(path)
            if pixmap.isNull():
                QMessageBox.warning(self, "Preview Error", "The selected image could not be displayed.")
                dlg.close()
                return

            def resize_preview():
                viewport = scroll.viewport().size()
                scaled = pixmap.scaled(
                    max(1, viewport.width() - 20),
                    max(1, viewport.height() - 20),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                image_label.setPixmap(scaled)

            scroll.setWidget(image_label)
            root.addWidget(scroll, 1)

            buttons = QHBoxLayout()
            open_btn = GlassButton("Open Full File")
            close_btn = GlassButton("Close")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path))))
            close_btn.clicked.connect(dlg.accept)
            buttons.addStretch(1)
            buttons.addWidget(open_btn)
            buttons.addWidget(close_btn)
            root.addLayout(buttons)

            dlg.resizeEvent = lambda event: (resize_preview(), QDialog.resizeEvent(dlg, event))
            resize_preview()
            apply_theme_to(dlg)
            dlg.exec()
            return

        # PDF and other non-image documents are opened with the system viewer.
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))

    def open_selected_purchase_attachment(self,key):
        i=self.selected_purchase_index()
        if i is None:return
        path=self.purchases[i].get(key,"")
        if path and os.path.exists(path): QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))
        else: QMessageBox.information(self,"Attachment","No saved attachment is available for this item.")

    def refresh_purchases(self):
        if not hasattr(self,"purchase_table"):return
        sym=CURRENCY_SYMBOLS.get(self.settings.get("currency"),"₹"); total=sum(float(x.get("price",0))*int(x.get("quantity",1)) for x in self.purchases)
        self.purchase_total_card.value.setText(f"{sym} {total:,.2f}"); self.purchase_items_card.value.setText(str(len(self.purchases)))
        self.purchase_active_card.value.setText(str(sum(self.purchase_warranty_status(x)=="Active" for x in self.purchases))); self.purchase_due_card.value.setText(str(sum(self.purchase_warranty_status(x)=="Due Soon" for x in self.purchases)))
        self.refresh_purchase_table()

    def refresh_purchase_table(self):
        if not hasattr(self,"purchase_table"):return
        q=self.purchase_search.text().strip().lower(); cat=self.purchase_category_filter.currentText(); wf=self.purchase_warranty_filter.currentText(); sym=CURRENCY_SYMBOLS.get(self.settings.get("currency"),"₹")
        self.purchase_table.setRowCount(0)
        for i,x in enumerate(self.purchases):
            status=self.purchase_warranty_status(x); search=" ".join(str(x.get(k,"")) for k in ("name","brand","store","model","serial")).lower()
            if q and q not in search:continue
            if cat!="All Categories" and x.get("category")!=cat:continue
            if wf!="All Warranty Status" and status!=wf:continue
            r=self.purchase_table.rowCount(); self.purchase_table.insertRow(r)
            vals=[x.get("name",""),x.get("category",""),f"{sym} {float(x.get('price',0))*int(x.get('quantity',1)):,.2f}",x.get("purchase_date",""),x.get("store",""),f"{x.get('warranty_months',0)} months" if int(x.get("warranty_months",0)) else "No Warranty",x.get("warranty_expiry",""),status,"📄 Open" if x.get("bill_path") else "—","📷 Open" if x.get("photo_path") else "—"]
            for col,v in enumerate(vals):
                cell=QTableWidgetItem(str(v))
                if col==0:cell.setData(Qt.UserRole,i)
                self.purchase_table.setItem(r,col,cell)

    def show_renewal_timeline(self):
        """Interactive chronological timeline for all renewal dates."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Renewal Timeline")
        fit_dialog(dlg, 980, 720, 500, 380)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        head = QHBoxLayout()
        box = QVBoxLayout()
        title = QLabel("🗓️ RENEWAL TIMELINE")
        title.setStyleSheet("font-size:24px; font-weight:900;")
        sub = QLabel("A chronological view of your upcoming and past plan renewals.")
        sub.setStyleSheet("color:#66758C;")
        box.addWidget(title); box.addWidget(sub)
        head.addLayout(box); head.addStretch()

        status_filter = QComboBox()
        status_filter.addItems(["All Plans", "Upcoming", "Due Soon", "Expired"])
        status_filter.setMinimumWidth(150)
        today_btn = QPushButton("Today")
        today_btn.setMinimumHeight(36)
        head.addWidget(QLabel("Filter:")); head.addWidget(status_filter); head.addWidget(today_btn)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        timeline = QVBoxLayout(content)
        timeline.setContentsMargins(4, 4, 10, 4)
        timeline.setSpacing(10)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        def parse_date(plan):
            raw = str(plan.get("renewal_date", "")).strip()
            for fmt in ("yyyy-MM-dd", "dd-MM-yyyy", "dd/MM/yyyy", "MM/dd/yyyy"):
                d = QDate.fromString(raw, fmt)
                if d.isValid(): return d
            return QDate()

        def get_status(plan, d):
            days = QDate.currentDate().daysTo(d)
            if days < 0: return "Expired", days
            reminder = int(plan.get("reminder_days", self.settings.get("default_reminder_days", 3)) or 3)
            if days <= reminder: return "Due Soon", days
            return "Upcoming", days

        def clear_layout(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
                elif item.layout(): clear_layout(item.layout())

        def open_plan(plan):
            try:
                idx = self.plans.index(plan)
                dlg.close()
                self.edit_plan(idx)
            except Exception:
                pass

        def rebuild():
            clear_layout(timeline)
            rows=[]
            filt=status_filter.currentText()
            for plan in self.plans:
                d=parse_date(plan)
                if not d.isValid(): continue
                status, days=get_status(plan,d)
                if filt!="All Plans" and status!=filt: continue
                rows.append((d,plan,status,days))
            rows.sort(key=lambda x:x[0])
            if not rows:
                empty=QLabel("No renewals match this filter.")
                empty.setAlignment(Qt.AlignCenter)
                empty.setStyleSheet("color:#66758C; font-size:15px; padding:50px;")
                timeline.addWidget(empty); timeline.addStretch(); return

            current_month=None
            sym=CURRENCY_SYMBOLS.get(self.settings.get("currency"),"₹")
            for d,plan,status,days in rows:
                month_key=(d.year(),d.month())
                if month_key!=current_month:
                    current_month=month_key
                    month=QLabel(d.toString("MMMM yyyy").upper())
                    month.setStyleSheet("font-size:14px; font-weight:900; letter-spacing:1px; padding:10px 4px;")
                    timeline.addWidget(month)

                card=GlassFrame(radius=18, alpha=135)
                lay=QHBoxLayout(card)
                lay.setContentsMargins(16,12,16,12); lay.setSpacing(14)
                date_box=QLabel(d.toString("dd\nMMM").upper())
                date_box.setAlignment(Qt.AlignCenter)
                date_box.setFixedSize(62,62)
                date_box.setStyleSheet("background:rgba(79,140,255,45); border-radius:14px; font-size:13px; font-weight:900;")
                line=QLabel("│")
                line.setStyleSheet("color:#4F8CFF; font-size:32px;")
                info=QVBoxLayout()
                name=QLabel(str(plan.get("name","Unnamed Plan")))
                name.setStyleSheet("font-size:16px; font-weight:900;")
                amount=plan.get("amount", plan.get("price",0))
                meta=QLabel(f"{sym} {float(amount or 0):,.2f}  •  {plan.get('category','Other')}")
                meta.setStyleSheet("color:#66758C;")
                info.addWidget(name); info.addWidget(meta)
                badge=QLabel(status.upper())
                color={"Upcoming":"#3B82F6","Due Soon":"#F59E0B","Expired":"#EF4444"}[status]
                badge.setStyleSheet(f"color:{color}; background:rgba(120,120,120,20); border:1px solid {color}; border-radius:12px; padding:6px 10px; font-size:10px; font-weight:900;")
                when = "Today" if days==0 else (f"In {days} days" if days>0 else f"{abs(days)} days ago")
                days_lbl=QLabel(when); days_lbl.setStyleSheet("color:#66758C; font-weight:700;")
                lay.addWidget(date_box); lay.addWidget(line); lay.addLayout(info,1); lay.addWidget(days_lbl); lay.addWidget(badge)
                card.mousePressEvent=lambda event,p=plan: open_plan(p)
                card.setCursor(Qt.PointingHandCursor)
                timeline.addWidget(card)
            timeline.addStretch()

        status_filter.currentTextChanged.connect(rebuild)
        today_btn.clicked.connect(lambda: (status_filter.setCurrentText("All Plans"), scroll.verticalScrollBar().setValue(0)))
        rebuild()
        apply_theme_to(dlg)
        dlg.exec()

    def show_calendar(self):
        """Improved interactive renewal calendar."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Renewal Calendar")
        fit_dialog(dlg, 1120, 720, 520, 380)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("📅 RENEWAL CALENDAR")
        title.setStyleSheet("font-size:24px; font-weight:900;")
        subtitle = QLabel("Browse renewals, filter by status, and double-click a plan to edit it.")
        subtitle.setStyleSheet("color:#66758C;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch()

        today_btn = QPushButton("Today")
        prev_btn = QPushButton("‹ Previous")
        next_btn = QPushButton("Next ›")
        for btn in (today_btn, prev_btn, next_btn):
            btn.setMinimumHeight(36)
            btn.setCursor(Qt.PointingHandCursor)
        title_row.addWidget(today_btn)
        title_row.addWidget(prev_btn)
        title_row.addWidget(next_btn)
        root.addLayout(title_row)

        # Monthly summary cards.
        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        summary_labels = {}
        for key, caption in (
            ("total", "TOTAL THIS MONTH"),
            ("active", "ACTIVE"),
            ("due", "DUE SOON"),
            ("expired", "EXPIRED"),
        ):
            card = GlassFrame(radius=16, alpha=135)
            lay = QVBoxLayout(card)
            lay.setContentsMargins(14, 10, 14, 10)
            small = QLabel(caption)
            small.setStyleSheet("font-size:11px; font-weight:800; color:#66758C;")
            value = QLabel("0")
            value.setStyleSheet("font-size:24px; font-weight:900;")
            lay.addWidget(small)
            lay.addWidget(value)
            summary_labels[key] = value
            summary_row.addWidget(card)
        root.addLayout(summary_row)

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        right = QVBoxLayout()
        left.setSpacing(12)
        right.setSpacing(12)
        body.addLayout(left, 3)
        body.addLayout(right, 2)

        filter_row = QHBoxLayout()
        filter_label = QLabel("SHOW:")
        filter_label.setStyleSheet("font-weight:800;")
        status_filter = QComboBox()
        status_filter.addItems(["All Plans", "Active", "Due Soon", "Expired"])
        filter_row.addWidget(filter_label)
        filter_row.addWidget(status_filter)
        filter_row.addStretch()
        left.addLayout(filter_row)

        calendar = QCalendarWidget()
        calendar.setGridVisible(True)
        calendar.setSelectedDate(QDate.currentDate())
        calendar.setMinimumHeight(410)
        left.addWidget(calendar)

        legend = QHBoxLayout()
        for label, color in (("Active", "#3B82F6"), ("Due Soon", "#F59E0B"), ("Expired", "#EF4444")):
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color}; font-size:16px;")
            txt = QLabel(label)
            txt.setStyleSheet("font-weight:700; color:#66758C;")
            legend.addWidget(dot)
            legend.addWidget(txt)
            legend.addSpacing(12)
        legend.addStretch()
        left.addLayout(legend)

        upcoming_title = QLabel("UPCOMING RENEWALS")
        upcoming_title.setStyleSheet("font-size:13px; font-weight:900; letter-spacing:1px;")
        right.addWidget(upcoming_title)

        upcoming = QTableWidget(0, 3)
        upcoming.setHorizontalHeaderLabels(["PLAN", "DATE", "STATUS"])
        upcoming.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        upcoming.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        upcoming.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        upcoming.setSelectionBehavior(QTableWidget.SelectRows)
        upcoming.setSelectionMode(QTableWidget.SingleSelection)
        upcoming.setEditTriggers(QTableWidget.NoEditTriggers)
        right.addWidget(upcoming, 1)

        day_title = QLabel("SELECTED DATE")
        day_title.setStyleSheet("font-size:13px; font-weight:900; letter-spacing:1px;")
        right.addWidget(day_title)

        day_table = QTableWidget(0, 3)
        day_table.setHorizontalHeaderLabels(["PLAN", "RENEWAL", "STATUS"])
        day_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        day_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        day_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        day_table.setMaximumHeight(185)
        day_table.setSelectionBehavior(QTableWidget.SelectRows)
        day_table.setSelectionMode(QTableWidget.SingleSelection)
        day_table.setEditTriggers(QTableWidget.NoEditTriggers)
        right.addWidget(day_table)

        edit_btn = QPushButton("✏️ Edit Selected Plan")
        edit_btn.setMinimumHeight(40)
        edit_btn.setCursor(Qt.PointingHandCursor)
        right.addWidget(edit_btn)

        def parse_plan_date(plan):
            raw = str(plan.get("renewal_date", "")).strip()
            for fmt in ("yyyy-MM-dd", "dd-MM-yyyy", "dd/MM/yyyy"):
                qd = QDate.fromString(raw, fmt)
                if qd.isValid():
                    return qd
            return QDate()

        def plan_status(plan):
            qd = parse_plan_date(plan)
            if not qd.isValid():
                return "Active"
            days = QDate.currentDate().daysTo(qd)
            try:
                reminder_days = int(plan.get(
                    "reminder_days",
                    load_app_settings().get("default_reminder_days", 3)
                ))
            except Exception:
                reminder_days = 3
            if days < 0:
                return "Expired"
            if days <= reminder_days:
                return "Due Soon"
            return "Active"

        status_colors = {
            "Active": "#3B82F6",
            "Due Soon": "#F59E0B",
            "Expired": "#EF4444",
        }

        def visible_plan(plan):
            selected = status_filter.currentText()
            return selected == "All Plans" or plan_status(plan) == selected

        def build_renewal_map():
            mapping = {}
            for plan in self.plans:
                qd = parse_plan_date(plan)
                if qd.isValid() and visible_plan(plan):
                    mapping.setdefault(qd.toString("yyyy-MM-dd"), []).append(plan)
            return mapping

        def update_summary():
            month = calendar.selectedDate().month()
            year = calendar.selectedDate().year()
            month_plans = [
                p for p in self.plans
                if (qd := parse_plan_date(p)).isValid()
                and qd.month() == month and qd.year() == year
            ]
            summary_labels["total"].setText(str(len(month_plans)))
            summary_labels["active"].setText(str(sum(plan_status(p) == "Active" for p in month_plans)))
            summary_labels["due"].setText(str(sum(plan_status(p) == "Due Soon" for p in month_plans)))
            summary_labels["expired"].setText(str(sum(plan_status(p) == "Expired" for p in month_plans)))

        def paint_dates():
            # Clear formatting around the calendar's practical working range.
            center = calendar.selectedDate()
            for offset in range(-800, 801):
                calendar.setDateTextFormat(center.addDays(offset), QTextCharFormat())

            renewal_dates = build_renewal_map()
            for date_text, plans in renewal_dates.items():
                qd = QDate.fromString(date_text, "yyyy-MM-dd")
                statuses = {plan_status(p) for p in plans}
                if "Expired" in statuses:
                    color = "#EF4444"
                elif "Due Soon" in statuses:
                    color = "#F59E0B"
                else:
                    color = "#3B82F6"

                names = "\n".join("• " + str(p.get("name", "")) for p in plans)
                fmt = QTextCharFormat()
                fmt.setFontWeight(QFont.Bold)
                fmt.setBackground(QColor(color))
                fmt.setForeground(QColor("#FFFFFF"))
                fmt.setToolTip(names)
                calendar.setDateTextFormat(qd, fmt)

        def add_row(table, row, plan):
            status = plan_status(plan)
            qd = parse_plan_date(plan)
            date_text = qd.toString("dd MMM yyyy") if qd.isValid() else str(plan.get("renewal_date", ""))
            table.setItem(row, 0, QTableWidgetItem(str(plan.get("name", ""))))
            table.setItem(row, 1, QTableWidgetItem(date_text))
            item = QTableWidgetItem(status)
            item.setForeground(QColor(status_colors[status]))
            item.setFont(QFont("Arial", 9, QFont.Bold))
            item.setData(Qt.UserRole, str(plan.get("id", "")))
            table.setItem(row, 2, item)

        def refresh_upcoming():
            dated = []
            for plan in self.plans:
                qd = parse_plan_date(plan)
                if qd.isValid() and visible_plan(plan):
                    dated.append((qd, plan))
            dated.sort(key=lambda item: item[0].toJulianDay())
            future = [(qd, p) for qd, p in dated if qd >= QDate.currentDate()]
            rows = future[:12] if future else dated[:12]
            upcoming.setRowCount(len(rows))
            for r, (_, plan) in enumerate(rows):
                add_row(upcoming, r, plan)

        def refresh_selected_day():
            selected = calendar.selectedDate().toString("yyyy-MM-dd")
            matches = build_renewal_map().get(selected, [])
            day_title.setText("SELECTED DATE • " + calendar.selectedDate().toString("dd MMM yyyy"))
            day_table.setRowCount(len(matches))
            for r, plan in enumerate(matches):
                add_row(day_table, r, plan)

        def selected_plan_from(table):
            row = table.currentRow()
            if row < 0:
                return None
            status_item = table.item(row, 2)
            if not status_item:
                return None
            plan_id = status_item.data(Qt.UserRole)
            for plan in self.plans:
                if str(plan.get("id", "")) == str(plan_id):
                    return plan
            return None

        def edit_selected():
            plan = selected_plan_from(day_table) or selected_plan_from(upcoming)
            if not plan:
                QMessageBox.information(dlg, "Select Plan", "Select a plan from the list first.")
                return
            editor = PlanDialog(plan, dlg)
            if editor.exec():
                plan.update(editor.result_plan())
                self.save_data()
                self.refresh_all()
                update_all()

        def update_all():
            update_summary()
            paint_dates()
            refresh_upcoming()
            refresh_selected_day()

        def move_month(offset):
            target = calendar.selectedDate().addMonths(offset)
            calendar.setSelectedDate(target)
            calendar.showSelectedDate()
            update_all()

        prev_btn.clicked.connect(lambda: move_month(-1))
        next_btn.clicked.connect(lambda: move_month(1))
        today_btn.clicked.connect(lambda: (calendar.setSelectedDate(QDate.currentDate()), calendar.showToday(), update_all()))
        calendar.selectionChanged.connect(lambda: (update_summary(), refresh_selected_day()))
        status_filter.currentTextChanged.connect(lambda _: update_all())
        day_table.itemDoubleClicked.connect(lambda _: edit_selected())
        upcoming.itemDoubleClicked.connect(lambda _: edit_selected())
        edit_btn.clicked.connect(edit_selected)

        update_all()
        dlg.exec()


    def show_recycle(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Recycle Bin")
        fit_dialog(dlg, 600, 420, 400, 300)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(self.recycle), 3)
        table.setHorizontalHeaderLabels(["ID", "PLAN", "RENEWAL DATE"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, p in enumerate(self.recycle):
            table.setItem(r, 0, QTableWidgetItem(str(p.get("id", ""))))
            table.setItem(r, 1, QTableWidgetItem(str(p.get("name", ""))))
            table.setItem(r, 2, QTableWidgetItem(str(p.get("renewal_date", ""))))
        lay.addWidget(table)

        restore = QPushButton("Restore Selected")
        def do_restore():
            row = table.currentRow()
            if row < 0:
                return
            pid = table.item(row, 0).text()
            for p in list(self.recycle):
                if str(p.get("id")) == pid:
                    self.recycle.remove(p)
                    self.plans.append(p)
                    save_json(RECYCLE_FILE, self.recycle)
                    self.save_data()
                    dlg.accept()
                    self.refresh_all()
                    return
        restore.clicked.connect(do_restore)
        lay.addWidget(restore)
        dlg.exec()

    def open_selective_export(self):
        """Export selected plans as CSV or a clean HTML subscription report."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Selective Export & Subscription Report")
        fit_dialog(dlg, 660, 520, 440, 360)
        dlg.setModal(True)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(22, 22, 22, 22)
        outer.setSpacing(14)

        title = QLabel("ADVANCED SELECTIVE EXPORT")
        title.setStyleSheet("font-size:22px; font-weight:900;")
        subtitle = QLabel("Choose exactly which subscription plans you want to include.")
        subtitle.setStyleSheet("color:#6B778C;")
        outer.addWidget(title); outer.addWidget(subtitle)

        controls = QHBoxLayout()
        select_all = QPushButton("Select All")
        clear_all = QPushButton("Clear All")
        controls.addWidget(select_all); controls.addWidget(clear_all); controls.addStretch(1)
        controls.addWidget(QLabel("Format:"))
        format_choice = QComboBox()
        format_choice.addItems(["CSV Spreadsheet (.csv)", "Subscription Report (.html)"])
        controls.addWidget(format_choice)
        outer.addLayout(controls)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        host = QWidget(); rows_layout = QVBoxLayout(host)
        rows_layout.setContentsMargins(8,8,8,8); rows_layout.setSpacing(8)
        plan_checks = []
        for plan in self.plans:
            if not isinstance(plan, dict): continue
            card = QFrame()
            card.setObjectName("glassCard")
            card.setStyleSheet("QFrame#glassCard { background:rgba(255,255,255,55); border:1px solid rgba(120,150,190,90); border-radius:14px; }")
            row = QHBoxLayout(card); row.setContentsMargins(14,10,14,10)
            check = QCheckBox(); check.setChecked(True)
            name = str(plan.get("name", "Unnamed Plan"))
            info = QLabel(f"<b>{name}</b><br><span style='color:#718096'>{plan.get('category','📦 Other')}  •  {plan.get('amount','')}  •  {plan.get('renewal_date','')}</span>")
            row.addWidget(check); row.addWidget(info,1)
            rows_layout.addWidget(card); plan_checks.append((check, plan))
        rows_layout.addStretch(1); scroll.setWidget(host); outer.addWidget(scroll,1)

        options = QFrame()
        options.setObjectName("glassCard")
        options.setStyleSheet("QFrame#glassCard { background:rgba(255,255,255,45); border:1px solid rgba(120,150,190,80); border-radius:14px; }")
        opt = QHBoxLayout(options)
        include_notes = QCheckBox("Include notes"); include_notes.setChecked(True)
        include_status = QCheckBox("Include renewal status"); include_status.setChecked(True)
        opt.addWidget(include_notes); opt.addWidget(include_status); opt.addStretch(1); outer.addWidget(options)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Export Selected Plans")
        outer.addWidget(buttons)
        select_all.clicked.connect(lambda: [c.setChecked(True) for c,_ in plan_checks])
        clear_all.clicked.connect(lambda: [c.setChecked(False) for c,_ in plan_checks])

        def export_selected():
            selected = [plan for check, plan in plan_checks if check.isChecked()]
            if not selected:
                QMessageBox.warning(dlg, "Nothing Selected", "Please select at least one plan to export."); return
            is_html = format_choice.currentIndex() == 1
            default_name = "subscription_report.html" if is_html else "selected_plans.csv"
            file_filter = "HTML Files (*.html)" if is_html else "CSV Files (*.csv)"
            path, _ = QFileDialog.getSaveFileName(dlg, "Export Selected Plans", os.path.join(USER_DATA_DIR, default_name), file_filter)
            if not path: return
            try:
                data_rows=[]
                total=0.0
                for plan in selected:
                    try: total += float(plan.get("amount",0) or 0)
                    except Exception: pass
                    item={"Plan Name":str(plan.get("name","")),"Category":str(plan.get("category","")),"Amount":str(plan.get("amount","")),"Billing Cycle":str(plan.get("billing_cycle",plan.get("cycle",""))),"Renewal Date":str(plan.get("renewal_date",""))}
                    if include_status.isChecked(): item["Status"] = self.status(plan)
                    if include_notes.isChecked(): item["Notes"] = str(plan.get("notes",""))
                    data_rows.append(item)
                if is_html:
                    import html
                    headers=list(data_rows[0].keys())
                    header_html=''.join('<th>'+html.escape(h)+'</th>' for h in headers)
                    body_html=''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(h,'')))+'</td>' for h in headers)+'</tr>' for r in data_rows)
                    report=("<!doctype html><html><head><meta charset='utf-8'><title>Subscription Report</title><style>body{font-family:Segoe UI,Arial;background:#f4f7fb;color:#172033;padding:32px}.card{background:white;border-radius:18px;padding:24px;box-shadow:0 8px 30px rgba(20,35,60,.08)}.meta{color:#64748b;margin:8px 0 22px}.stat{display:inline-block;background:#eef4ff;border-radius:12px;padding:14px 18px;font-weight:700;margin-right:10px}table{width:100%;border-collapse:collapse;margin-top:22px}th{background:#172033;color:white}th,td{padding:12px;text-align:left;border-bottom:1px solid #e5eaf2}</style></head><body><div class='card'><h1>Subscription Plans Report</h1><div class='meta'>Generated "+html.escape(datetime.now().strftime('%d %b %Y, %I:%M %p'))+"</div><div class='stat'>Selected Plans: "+str(len(selected))+"</div><div class='stat'>Listed Total: "+str(total)+"</div><table><thead><tr>"+header_html+"</tr></thead><tbody>"+body_html+"</tbody></table></div></body></html>")
                    Path(path).write_text(report, encoding='utf-8')
                else:
                    with open(path,'w',newline='',encoding='utf-8-sig') as f:
                        writer=csv.DictWriter(f,fieldnames=list(data_rows[0].keys())); writer.writeheader(); writer.writerows(data_rows)
                QMessageBox.information(dlg, "Export Complete", f"Successfully exported {len(selected)} selected plan(s).")
                dlg.accept()
            except Exception as e: QMessageBox.critical(dlg, "Export Error", str(e))
        buttons.accepted.connect(export_selected); buttons.rejected.connect(dlg.reject)
        dlg.exec()

    def add_notification_history(self, title, message, plan_id=None, snoozed_until=None):
        entry = {
            "id": f"notif_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            "title": str(title), "message": str(message), "plan_id": plan_id,
            "created_at": datetime.now().strftime("%d-%m-%Y %H:%M"),
            "read": False, "snoozed_until": snoozed_until or ""
        }
        self.notifications_history.insert(0, entry)
        self.notifications_history = self.notifications_history[:500]
        save_notifications(self.notifications_history)
        if hasattr(self, "notification_button"):
            unread = sum(1 for n in self.notifications_history if not n.get("read"))
            self.notification_button.set_lucide_icon("notification")
            self.notification_button.setToolTip(f"Notifications ({unread} unread)" if unread else "Notifications")

    def show_notifications(self):
        """Premium compact Notification Center."""
        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        dlg = QDialog(self)
        dlg.setWindowTitle("Notifications")
        fit_dialog(dlg, 760, 590, 440, 380)
        dlg.setModal(True)
        dlg.setStyleSheet("""
            QDialog {
                background: #DDE5F1;
                color: #172033;
            }
            QLabel { color: #172033; }
            QFrame#notificationHeader, QFrame#notificationStats, QFrame#notificationPanel {
                background: rgba(255,255,255,150);
                border: 1px solid rgba(255,255,255,210);
                border-radius: 18px;
            }
            QFrame#notificationRow {
                background: rgba(255,255,255,168);
                border: 1px solid rgba(122,142,170,70);
                border-radius: 14px;
            }
            QFrame#notificationRow[unread="true"] {
                background: rgba(232,242,255,225);
                border: 1px solid rgba(76,132,205,150);
            }
            QPushButton#filterButton {
                background: rgba(255,255,255,115);
                border: 1px solid rgba(120,145,180,70);
                border-radius: 11px;
                padding: 7px 13px;
                font-weight: 700;
                color: #43536A;
            }
            QPushButton#filterButton:checked {
                background: rgba(53,118,205,210);
                color: white;
                border-color: rgba(53,118,205,230);
            }
            QPushButton#softButton {
                background: rgba(255,255,255,145);
                border: 1px solid rgba(120,145,180,75);
                border-radius: 11px;
                padding: 8px 14px;
                font-weight: 700;
                color: #35455D;
            }
            QPushButton#softButton:hover {
                background: rgba(255,255,255,220);
                border-color: rgba(72,128,205,145);
            }
            QPushButton#primaryButton {
                background: #3478C9;
                color: white;
                border: none;
                border-radius: 11px;
                padding: 8px 16px;
                font-weight: 800;
            }
            QPushButton#primaryButton:hover { background: #276AB8; }
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
                padding: 2px;
            }
            QListWidget::item { border: none; background: transparent; margin: 3px 0; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(95,115,145,95);
                border-radius: 4px;
                min-height: 28px;
            }
        """)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        # ---------- Header ----------
        header = QFrame(); header.setObjectName("notificationHeader")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(16, 13, 16, 13)
        header_lay.setSpacing(12)

        icon = QLabel("🔔")
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size:24px; background:rgba(52,120,201,35); border:1px solid rgba(52,120,201,80); border-radius:14px;")
        header_lay.addWidget(icon)

        title_box = QVBoxLayout(); title_box.setSpacing(2)
        title = QLabel("Notification Center")
        title.setStyleSheet("font-size:20px; font-weight:900; color:#1D2A3D;")
        subtitle = QLabel("Stay on top of renewals, reminders and snoozed alerts.")
        subtitle.setStyleSheet("font-size:11px; color:#68788E;")
        title_box.addWidget(title); title_box.addWidget(subtitle)
        header_lay.addLayout(title_box, 1)

        unread_badge = QLabel()
        unread_badge.setAlignment(Qt.AlignCenter)
        unread_badge.setMinimumWidth(84)
        unread_badge.setStyleSheet("background:rgba(52,120,201,24); color:#276AB8; border:1px solid rgba(52,120,201,65); border-radius:11px; padding:7px 10px; font-size:11px; font-weight:800;")
        header_lay.addWidget(unread_badge)
        lay.addWidget(header)

        # ---------- Compact statistics ----------
        stats = QFrame(); stats.setObjectName("notificationStats")
        stats_lay = QHBoxLayout(stats)
        stats_lay.setContentsMargins(12, 9, 12, 9)
        stats_lay.setSpacing(8)
        total_stat = QLabel(); unread_stat = QLabel(); snoozed_stat = QLabel()
        for label in (total_stat, unread_stat, snoozed_stat):
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("background:rgba(255,255,255,110); border-radius:10px; padding:8px 12px; color:#405169; font-size:11px; font-weight:700;")
            stats_lay.addWidget(label, 1)
        lay.addWidget(stats)

        # ---------- Filters ----------
        filter_lay = QHBoxLayout(); filter_lay.setSpacing(7)
        filters = {}
        for key, text in (("all", "All"), ("unread", "Unread"), ("snoozed", "Snoozed")):
            btn = QPushButton(text)
            btn.setObjectName("filterButton")
            btn.setCheckable(True)
            filters[key] = btn
            filter_lay.addWidget(btn)
        filters["all"].setChecked(True)
        filter_lay.addStretch(1)
        filter_hint = QLabel("Select a reminder to manage it")
        filter_hint.setStyleSheet("font-size:11px; color:#75849A;")
        filter_lay.addWidget(filter_hint)
        lay.addLayout(filter_lay)

        # ---------- Notification list ----------
        panel = QFrame(); panel.setObjectName("notificationPanel")
        panel_lay = QVBoxLayout(panel); panel_lay.setContentsMargins(8, 8, 8, 8)
        listing = QListWidget()
        listing.setSelectionMode(QListWidget.SingleSelection)
        panel_lay.addWidget(listing)
        lay.addWidget(panel, 1)

        # ---------- Bottom actions ----------
        controls = QHBoxLayout(); controls.setSpacing(8)
        mark = QPushButton("✓  Mark as Read"); mark.setObjectName("primaryButton")
        snooze = QPushButton("😴  Snooze"); snooze.setObjectName("softButton")
        clear = QPushButton("Clear Read"); clear.setObjectName("softButton")
        close = QPushButton("Close"); close.setObjectName("softButton")
        controls.addWidget(mark); controls.addWidget(snooze); controls.addWidget(clear)
        controls.addStretch(1); controls.addWidget(close)
        lay.addLayout(controls)

        current_filter = {"value": "all"}

        def update_button_badge():
            if hasattr(self, "notification_button"):
                unread = sum(1 for n in self.notifications_history if not n.get("read"))
                self.notification_button.set_lucide_icon("notification")
                self.notification_button.setToolTip(f"Notifications ({unread} unread)" if unread else "Notifications")

        def is_snoozed(n):
            value = str(n.get("snoozed_until") or "").strip()
            if not value:
                return False
            try:
                return datetime.strptime(value, "%d-%m-%Y %H:%M") > datetime.now()
            except Exception:
                return True

        def filtered_items():
            mode = current_filter["value"]
            if mode == "unread":
                return [n for n in self.notifications_history if not n.get("read")]
            if mode == "snoozed":
                return [n for n in self.notifications_history if is_snoozed(n)]
            return list(self.notifications_history)

        def refresh():
            update_button_badge()
            listing.clear()
            unread_count = sum(1 for n in self.notifications_history if not n.get("read"))
            snoozed_count = sum(1 for n in self.notifications_history if is_snoozed(n))
            unread_badge.setText(f"{unread_count} UNREAD" if unread_count else "ALL READ ✓")
            total_stat.setText(f"📬  {len(self.notifications_history)} Total")
            unread_stat.setText(f"🔵  {unread_count} Unread")
            snoozed_stat.setText(f"😴  {snoozed_count} Snoozed")

            rows = filtered_items()
            if not rows:
                empty = QListWidgetItem()
                empty.setFlags(Qt.NoItemFlags)
                empty.setSizeHint(QSize(0, 190))
                listing.addItem(empty)
                card = QFrame(); card.setStyleSheet("background:transparent; border:none;")
                card_lay = QVBoxLayout(card); card_lay.setAlignment(Qt.AlignCenter); card_lay.setSpacing(5)
                emoji = QLabel("🔔"); emoji.setAlignment(Qt.AlignCenter); emoji.setStyleSheet("font-size:34px;")
                msg = QLabel("No notifications here")
                msg.setAlignment(Qt.AlignCenter); msg.setStyleSheet("font-size:15px; font-weight:800; color:#4B5B70;")
                sub = QLabel("Renewal reminders will appear automatically when needed.")
                sub.setAlignment(Qt.AlignCenter); sub.setStyleSheet("font-size:11px; color:#7B899C;")
                card_lay.addWidget(emoji); card_lay.addWidget(msg); card_lay.addWidget(sub)
                listing.setItemWidget(empty, card)
                return

            for n in rows:
                unread = not n.get("read")
                row_item = QListWidgetItem()
                row_item.setData(Qt.UserRole, n.get("id"))
                row_item.setSizeHint(QSize(0, 82))
                listing.addItem(row_item)

                row = QFrame(); row.setObjectName("notificationRow")
                row.setProperty("unread", "true" if unread else "false")
                row_lay = QHBoxLayout(row)
                row_lay.setContentsMargins(12, 8, 12, 8); row_lay.setSpacing(10)

                dot = QLabel("●" if unread else "✓")
                dot.setAlignment(Qt.AlignCenter); dot.setFixedSize(30, 30)
                dot.setStyleSheet(
                    "color:#2F7BCB; background:rgba(47,123,203,24); border-radius:15px; font-weight:900;"
                    if unread else "color:#4F9B73; background:rgba(79,155,115,20); border-radius:15px; font-weight:900;"
                )
                row_lay.addWidget(dot)

                text_box = QVBoxLayout(); text_box.setSpacing(2)
                row_title = QLabel(str(n.get("title", "Notification")))
                row_title.setStyleSheet("font-size:12px; font-weight:900; color:#233147;" if unread else "font-size:12px; font-weight:700; color:#526176;")
                row_message = QLabel(str(n.get("message", "")))
                row_message.setWordWrap(False)
                row_message.setText(row_message.fontMetrics().elidedText(row_message.text(), Qt.ElideRight, 430))
                row_message.setStyleSheet("font-size:11px; color:#68788E;")
                text_box.addWidget(row_title); text_box.addWidget(row_message)
                row_lay.addLayout(text_box, 1)

                meta = QVBoxLayout(); meta.setSpacing(4); meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                created = QLabel("🕒 " + str(n.get("created_at", "")))
                created.setStyleSheet("font-size:10px; color:#7D8A9C;")
                created.setAlignment(Qt.AlignRight)
                meta.addWidget(created)
                if is_snoozed(n):
                    snooze_label = QLabel("😴 Snoozed")
                    snooze_label.setStyleSheet("font-size:10px; color:#9A6A24; font-weight:800;")
                    snooze_label.setAlignment(Qt.AlignRight)
                    meta.addWidget(snooze_label)
                row_lay.addLayout(meta)
                listing.setItemWidget(row_item, row)

        def selected_id():
            item = listing.currentItem()
            return item.data(Qt.UserRole) if item and item.flags() != Qt.NoItemFlags else None

        def mark_read():
            nid = selected_id()
            if not nid:
                return
            for n in self.notifications_history:
                if n.get("id") == nid:
                    n["read"] = True
                    break
            save_notifications(self.notifications_history)
            refresh()

        def mark_selected_on_open(item):
            nid = item.data(Qt.UserRole)
            if not nid:
                return
            changed = False
            for n in self.notifications_history:
                if n.get("id") == nid and not n.get("read"):
                    n["read"] = True
                    changed = True
                    break
            if changed:
                save_notifications(self.notifications_history)
                refresh()

        def snooze_selected():
            nid = selected_id()
            if not nid:
                return
            menu = QMenu(dlg)
            choices = [("1 Hour", 60), ("3 Hours", 180), ("Tomorrow", 24 * 60), ("1 Week", 7 * 24 * 60)]
            for label, minutes in choices:
                action = menu.addAction(label)
                action.triggered.connect(lambda checked=False, m=minutes: do_snooze(m))
            menu.exec(snooze.mapToGlobal(snooze.rect().bottomLeft()))

        def do_snooze(minutes):
            nid = selected_id()
            if not nid:
                return
            until = datetime.now() + __import__("datetime").timedelta(minutes=minutes)
            for n in self.notifications_history:
                if n.get("id") == nid:
                    n["snoozed_until"] = until.strftime("%d-%m-%Y %H:%M")
                    n["read"] = False
                    break
            save_notifications(self.notifications_history)
            refresh()

        def clear_read():
            self.notifications_history[:] = [n for n in self.notifications_history if not n.get("read")]
            save_notifications(self.notifications_history)
            refresh()

        def set_filter(mode):
            current_filter["value"] = mode
            for key, button in filters.items():
                button.setChecked(key == mode)
            refresh()

        for key, button in filters.items():
            button.clicked.connect(lambda checked=False, m=key: set_filter(m))
        mark.clicked.connect(mark_read)
        snooze.clicked.connect(snooze_selected)
        clear.clicked.connect(clear_read)
        close.clicked.connect(dlg.accept)
        listing.itemClicked.connect(mark_selected_on_open)

        refresh()
        apply_theme_to(dlg)
        dlg.exec()


    def show_settings(self):
        """Full application settings with persistent preferences and data tools."""
        current = load_app_settings()

        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        fit_dialog(dlg, 680, 760, 440, 420)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(14)

        title = QLabel("APPLICATION SETTINGS")
        title.setStyleSheet("font-size:22px; font-weight:900;")
        outer.addWidget(title)
        subtitle = QLabel("Customize reminders, background, colors, fonts, appearance, date and currency preferences, app security, and manage your data.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#66758C;")
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(16)

        def section_label(text):
            label = QLabel(text)
            label.setStyleSheet("font-size:12px; font-weight:900; letter-spacing:1px; color:#53627A;")
            lay.addWidget(label)

        def panel():
            frame = QFrame()
            frame.setObjectName("SettingsPanel")
            frame.setStyleSheet("QFrame#SettingsPanel { background:rgba(255,255,255,65); border:1px solid rgba(255,255,255,110); border-radius:16px; }")
            form = QFormLayout(frame)
            form.setContentsMargins(18, 14, 18, 14)
            form.setHorizontalSpacing(20)
            form.setVerticalSpacing(12)
            lay.addWidget(frame)
            return frame, form

        section_label("REMINDERS")
        _, reminder_form = panel()
        reminder_days = QSpinBox()
        reminder_days.setRange(0, 3650)
        reminder_days.setValue(int(current.get("default_reminder_days", 3)))
        reminder_days.setSuffix(" days")
        reminder_form.addRow("Default reminder", reminder_days)
        notifications = QCheckBox("Enable renewal notifications")
        notifications.setChecked(bool(current.get("notifications_enabled", True)))
        reminder_form.addRow("Notifications", notifications)
        sound = QCheckBox("Play a sound with notifications")
        sound.setChecked(bool(current.get("sound_enabled", False)))
        reminder_form.addRow("Notification sound", sound)

        test_notification_btn = QPushButton("🔔 Test Notification")
        test_notification_btn.setMinimumHeight(40)
        test_notification_btn.setCursor(Qt.PointingHandCursor)
        test_notification_btn.setToolTip(
            "Send a test notification using the current notification settings"
        )
        reminder_form.addRow("Test notification", test_notification_btn)

        section_label("WEB PUSH NOTIFICATIONS")
        _, push_form = panel()
        push_info = QLabel(
            "Use Renewly Web Notifications to receive renewal alerts on your phone or browser without installing a separate mobile app. "
            "Sign in with the same Renewly account, allow browser notifications, and the cloud server will handle renewal alerts even when the desktop app is closed."
        )
        push_info.setWordWrap(True)
        push_info.setStyleSheet("color:#66758C; font-size:11px;")
        push_form.addRow("How it works", push_info)
        push_portal_btn = QPushButton("🌐 Open Renewly Web Notifications")
        push_portal_btn.setMinimumHeight(40)
        push_portal_btn.setCursor(Qt.PointingHandCursor)
        push_portal_btn.setToolTip("Open the Renewly browser notification portal")
        push_form.addRow("Web Portal", push_portal_btn)
        push_test_hint = QLabel("The web portal controls push permissions, notification preferences, test notifications, and notification history.")
        push_test_hint.setWordWrap(True)
        push_test_hint.setStyleSheet("color:#66758C; font-size:11px;")
        push_form.addRow("Manage", push_test_hint)

        section_label("CLOUD SYNC — RENEWLY BACKEND")
        _, cloud_form = panel()
        cloud_email = QLineEdit(str(current.get("cloud_account_email", "")))
        cloud_email.setPlaceholderText("your account email")
        cloud_email.setReadOnly(True)
        cloud_form.addRow("Account Email", cloud_email)
        cloud_status_label = QLabel("● Connected" if self._cloud_api and self._cloud_api.is_authenticated else "● Automatic after sign-in")
        cloud_status_label.setObjectName("cloud_status_label")
        cloud_status_label.setStyleSheet("font-weight:800; color:#22C55E;" if self._cloud_api and self._cloud_api.is_authenticated else "font-weight:800; color:#66758C;")
        cloud_form.addRow("Status", cloud_status_label)
        cloud_auto_label = QLabel("☁️ Cloud Sync starts automatically after your normal Renewly sign-in. No separate Cloud Sync password is used.")
        cloud_auto_label.setWordWrap(True)
        cloud_auto_label.setStyleSheet("color:#66758C; font-size:11px;")
        cloud_form.addRow("Automatic", cloud_auto_label)
        cloud_sync_now_btn = QPushButton("🔄 Sync Now")
        cloud_sync_now_btn.setMinimumHeight(40)
        cloud_sync_now_btn.setToolTip("Start a cloud synchronization immediately")
        cloud_form.addRow("Manual Sync", cloud_sync_now_btn)

        cloud_disconnect_btn = QPushButton("Disconnect Cloud")
        cloud_disconnect_btn.setMinimumHeight(40)
        cloud_form.addRow("Cloud", cloud_disconnect_btn)
        cloud_hint = QLabel("Your Renewly sign-in authenticates Cloud Sync. The sign-in password is used only in memory to obtain the backend session and is never saved to settings or files. Use HTTPS for production.")
        cloud_hint.setWordWrap(True)
        cloud_hint.setStyleSheet("color:#66758C; font-size:11px;")
        cloud_form.addRow("Security", cloud_hint)
        # Expose the status label to the async completion handler without leaking credentials.
        dlg.cloud_status_label = cloud_status_label
        self._cloud_settings_dialog = dlg
        dlg.finished.connect(lambda: setattr(self, "_cloud_settings_dialog", None) if getattr(self, "_cloud_settings_dialog", None) is dlg else None)

        def manual_cloud_sync():
            if not self._cloud_api or not getattr(self._cloud_api, "is_authenticated", False):
                QMessageBox.information(
                    dlg,
                    "Cloud Sync",
                    "Please sign in to your Renewly account first. Cloud Sync uses your normal Renewly sign-in and does not require a separate password."
                )
                return
            cloud_status_label.setText("☁ Syncing…")
            cloud_status_label.setStyleSheet("font-weight:800; color:#D97706;")
            self._cloud_sync_from_authenticated_session(settings_dialog=dlg, automatic=False)

        cloud_sync_now_btn.clicked.connect(manual_cloud_sync)

        def disconnect_cloud():
            self.cloud_logout()
            cloud_status_label.setText("● Disconnected")
            cloud_status_label.setStyleSheet("font-weight:800; color:#66758C;")
        cloud_disconnect_btn.clicked.connect(disconnect_cloud)

        section_label("DISPLAY")
        _, display_form = panel()
        date_format = QComboBox()
        date_format.addItems(list(DATE_FORMATS.keys()))
        date_format.setCurrentText(current.get("date_format", "DD-MM-YYYY"))
        display_form.addRow("Date format", date_format)
        currency = QComboBox()
        currency.addItems(list(CURRENCY_SYMBOLS.keys()))
        currency.setCurrentText(current.get("currency", "INR (₹)"))
        display_form.addRow("Currency", currency)
        clock_toggle = QCheckBox("Show live clock on the dashboard")
        clock_toggle.setChecked(bool(current.get("show_clock", True)))
        display_form.addRow("Home Page clock", clock_toggle)

        clock_style = QComboBox()
        clock_style.addItems(["Classic", "Digital", "Compact", "Minimal", "Detailed"])
        saved_clock_style = current.get("clock_style", "Classic")
        if clock_style.findText(saved_clock_style) >= 0:
            clock_style.setCurrentText(saved_clock_style)
        display_form.addRow("Clock style", clock_style)

        theme_choice = QComboBox()
        theme_choice.addItems(["Light", "Dark"])
        theme_choice.setCurrentText("Dark" if Theme.dark() else "Light")
        display_form.addRow("Theme preference", theme_choice)

        section_label("HOME PAGE CUSTOMIZATION")
        _, dashboard_form = panel()

        dashboard_summary = QLabel(
            "Choose what appears on the home page. You can drag visible summary cards directly on the home page to change their order."
        )
        dashboard_summary.setWordWrap(True)
        dashboard_summary.setStyleSheet("color:#66758C;")
        dashboard_form.addRow("", dashboard_summary)

        current_visible_cards = set(current.get(
            "dashboard_visible_cards", ["total", "active", "due", "monthly", "upcoming"]
        ))
        card_visibility = {}
        for card_id, card_name in [
            ("total", "Total Plans"),
            ("active", "Active Plans"),
            ("due", "Due Soon"),
            ("monthly", "Monthly Spending"),
            ("upcoming", "Upcoming Renewals"),
        ]:
            checkbox = QCheckBox(card_name)
            checkbox.setChecked(card_id in current_visible_cards)
            card_visibility[card_id] = checkbox
            dashboard_form.addRow("Summary card", checkbox)

        density_choice = QComboBox()
        density_choice.addItems(["Comfortable", "Compact"])
        density_choice.setCurrentText(
            "Compact" if current.get("dashboard_density", "comfortable") == "compact"
            else "Comfortable"
        )
        dashboard_form.addRow("Home Page density", density_choice)

        default_page_choice = QComboBox()
        default_page_choice.addItems(["Home Page", "Analytics"])
        default_page_choice.setCurrentText(
            "Analytics" if current.get("dashboard_default_page", "dashboard") == "analytics"
            else "Home Page"
        )
        dashboard_form.addRow("Default opening page", default_page_choice)

        show_summary = QCheckBox("Show summary cards area")
        show_summary.setChecked(bool(current.get("dashboard_show_summary", True)))
        dashboard_form.addRow("Summary area", show_summary)

        show_search = QCheckBox("Show search and category filter")
        show_search.setChecked(bool(current.get("dashboard_show_search", True)))
        dashboard_form.addRow("Search widget", show_search)

        show_status_filters = QCheckBox("Show All / Active / Due Soon / Expired filters")
        show_status_filters.setChecked(bool(current.get("dashboard_show_status_filters", True)))
        dashboard_form.addRow("Status filters", show_status_filters)

        show_bulk_actions = QCheckBox("Show plan bulk action controls")
        show_bulk_actions.setChecked(bool(current.get("dashboard_show_bulk_actions", True)))
        dashboard_form.addRow("Bulk action widget", show_bulk_actions)

        reset_dashboard_btn = QPushButton("Reset Home Page Layout")
        reset_dashboard_btn.setMinimumHeight(36)
        dashboard_form.addRow("Layout", reset_dashboard_btn)

        section_label("PREMIUM THEME PRESETS")
        _, preset_form = panel()

        THEME_PRESETS = {
            "Custom": {},
            "Cyber Blue": {"theme": "dark", "accent": "#4F8CFF", "sidebar": "#101A2B"},
            "Neon Purple": {"theme": "dark", "accent": "#A855F7", "sidebar": "#1B1230"},
            "Emerald": {"theme": "dark", "accent": "#22C55E", "sidebar": "#10261E"},
            "Sunset Orange": {"theme": "dark", "accent": "#F97316", "sidebar": "#2B1A12"},
            "Rose": {"theme": "dark", "accent": "#F43F5E", "sidebar": "#2D1420"},
            "Midnight": {"theme": "dark", "accent": "#60A5FA", "sidebar": "#0B1020"},
            "Minimal Light": {"theme": "light", "accent": "#2563EB", "sidebar": "#E7EDF7"},
        }

        preset_choice = QComboBox()
        preset_choice.addItems(list(THEME_PRESETS.keys()))
        saved_preset = current.get("theme_preset", "Custom")
        if preset_choice.findText(saved_preset) >= 0:
            preset_choice.setCurrentText(saved_preset)
        preset_form.addRow("Theme preset", preset_choice)

        preset_hint = QLabel("Choose a ready-made color style. Selecting Custom keeps your own colors.")
        preset_hint.setWordWrap(True)
        preset_hint.setStyleSheet("color:#66758C; font-size:11px;")
        preset_form.addRow("", preset_hint)

        section_label("UI CUSTOMIZATION")
        _, ui_form = panel()

        background_toggle = QCheckBox("Show background artwork")
        background_toggle.setChecked(bool(current.get("background_enabled", True)))
        ui_form.addRow("Background image", background_toggle)

        opacity_row = QWidget()
        opacity_lay = QHBoxLayout(opacity_row)
        opacity_lay.setContentsMargins(0, 0, 0, 0)
        background_opacity = QSpinBox()
        background_opacity.setRange(0, 100)
        background_opacity.setSuffix("%")
        background_opacity.setValue(int(current.get("background_opacity", 24)))
        opacity_lay.addWidget(background_opacity)
        opacity_lay.addStretch(1)
        ui_form.addRow("Background opacity", opacity_row)

        bg_image_row = QWidget()
        bg_image_lay = QHBoxLayout(bg_image_row)
        bg_image_lay.setContentsMargins(0, 0, 0, 0)
        background_path_label = QLabel(str(current.get("background_image", "background.png")))
        background_path_label.setWordWrap(True)
        choose_background_btn = QPushButton("Choose Image…")
        choose_background_btn.setMinimumHeight(34)
        bg_image_lay.addWidget(background_path_label, 1)
        bg_image_lay.addWidget(choose_background_btn)
        ui_form.addRow("Choose background", bg_image_row)

        accent_row = QWidget()
        accent_lay = QHBoxLayout(accent_row)
        accent_lay.setContentsMargins(0, 0, 0, 0)
        accent_preview = QPushButton()
        accent_preview.setFixedSize(54, 30)
        accent_preview.setCursor(Qt.PointingHandCursor)
        accent_preview.setToolTip("Choose accent color")
        accent_color = {"value": current.get("accent_color", "#4F8CFF")}
        def refresh_accent_preview():
            accent_preview.setStyleSheet(
                f"QPushButton {{ background:{accent_color['value']}; border:1px solid rgba(0,0,0,60); border-radius:9px; }}"
            )
        refresh_accent_preview()
        accent_lay.addWidget(accent_preview)
        accent_lay.addStretch(1)
        ui_form.addRow("Accent color", accent_row)

        sidebar_row = QWidget()
        sidebar_lay = QHBoxLayout(sidebar_row)
        sidebar_lay.setContentsMargins(0, 0, 0, 0)
        sidebar_preview = QPushButton()
        sidebar_preview.setFixedSize(54, 30)
        sidebar_preview.setCursor(Qt.PointingHandCursor)
        sidebar_preview.setToolTip("Choose sidebar color")
        sidebar_color = {"value": current.get("sidebar_color", "#D7E0EE")}
        def refresh_sidebar_preview():
            sidebar_preview.setStyleSheet(
                f"QPushButton {{ background:{sidebar_color['value']}; border:1px solid rgba(0,0,0,60); border-radius:9px; }}"
            )
        refresh_sidebar_preview()
        sidebar_lay.addWidget(sidebar_preview)
        sidebar_lay.addStretch(1)
        ui_form.addRow("Sidebar color", sidebar_row)

        font_size = QSpinBox()
        font_size.setRange(8, 24)
        font_size.setSuffix(" pt")
        font_size.setValue(int(current.get("font_size", 10)))
        ui_form.addRow("Font size", font_size)

        font_family = QFontComboBox()
        font_family.setCurrentFont(QFont(current.get("font_family", "Segoe UI")))
        ui_form.addRow("Font style", font_family)

        def apply_theme_preset(name):
            preset = THEME_PRESETS.get(name, {})
            if not preset:
                return
            theme_choice.setCurrentText("Dark" if preset.get("theme") == "dark" else "Light")
            accent_color["value"] = preset.get("accent", accent_color["value"])
            sidebar_color["value"] = preset.get("sidebar", sidebar_color["value"])
            refresh_accent_preview()
            refresh_sidebar_preview()

        preset_choice.currentTextChanged.connect(apply_theme_preset)

        selected_background = {"value": current.get("background_image", "background.png")}

        def choose_background():
            path, _ = QFileDialog.getOpenFileName(
                dlg,
                "Choose Background Image",
                BASE_DIR,
                "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
            )
            if not path:
                return
            try:
                extension = os.path.splitext(path)[1].lower() or ".png"
                destination_name = "custom_background" + extension
                destination = os.path.join(USER_DATA_DIR, destination_name)
                if os.path.abspath(path) != os.path.abspath(destination):
                    shutil.copy2(path, destination)
                selected_background["value"] = destination_name
                background_path_label.setText(destination_name)
                background_toggle.setChecked(True)
            except Exception as e:
                QMessageBox.critical(dlg, "Background Error", f"Could not use this image.\n\n{e}")

        def choose_accent():
            color = QColorDialog.getColor(QColor(accent_color["value"]), dlg, "Choose Accent Color")
            if color.isValid():
                accent_color["value"] = color.name()
                preset_choice.setCurrentText("Custom")
                refresh_accent_preview()

        def choose_sidebar_color():
            color = QColorDialog.getColor(QColor(sidebar_color["value"]), dlg, "Choose Sidebar Color")
            if color.isValid():
                sidebar_color["value"] = color.name()
                preset_choice.setCurrentText("Custom")
                refresh_sidebar_preview()

        choose_background_btn.clicked.connect(choose_background)
        accent_preview.clicked.connect(choose_accent)
        sidebar_preview.clicked.connect(choose_sidebar_color)

        section_label("APP SECURITY — WINDOWS HELLO")
        _, security_form = panel()
        app_lock_enabled = QCheckBox("Enable App Lock")
        app_lock_enabled.setChecked(bool(current.get("app_lock_enabled", False)))
        security_form.addRow("App Lock", app_lock_enabled)

        hello_info = QLabel("Uses Windows Hello. If Face Unlock is configured in Windows, Windows will use it; otherwise it may use your fingerprint or Windows PIN. Renewly never stores face or biometric data.")
        hello_info.setWordWrap(True)
        hello_info.setStyleSheet("color:#66758C; font-size:11px;")
        security_form.addRow("Unlock method", hello_info)

        lock_on_startup = QCheckBox("Require Windows Hello when Renewly opens")
        lock_on_startup.setChecked(bool(current.get("app_lock_on_startup", True)))
        security_form.addRow("On startup", lock_on_startup)

        lock_on_minimize = QCheckBox("Lock when Renewly is minimized or hidden to the tray")
        lock_on_minimize.setChecked(bool(current.get("app_lock_on_minimize", False)))
        security_form.addRow("On minimize", lock_on_minimize)

        inactivity_minutes = QSpinBox()
        inactivity_minutes.setRange(0, 240)
        inactivity_minutes.setSuffix(" min (0 = off)")
        inactivity_minutes.setValue(int(current.get("app_lock_inactivity_minutes", 0)))
        security_form.addRow("Auto-lock after inactivity", inactivity_minutes)

        test_hello_btn = QPushButton("🙂 Test Windows Hello")
        test_hello_btn.setMinimumHeight(38)
        security_form.addRow("Test", test_hello_btn)

        def test_windows_hello():
            ok, message = windows_hello_verify("Verify Windows Hello for Renewly")
            if ok:
                QMessageBox.information(dlg, "Windows Hello", "Windows Hello verification succeeded.")
            else:
                QMessageBox.warning(dlg, "Windows Hello", message)

        test_hello_btn.clicked.connect(test_windows_hello)

        section_label("APPLICATION UPDATE")
        update_frame = QFrame()
        update_frame.setObjectName("glassCard")
        update_frame.setStyleSheet("QFrame#glassCard { background:rgba(255,255,255,65); border:1px solid rgba(255,255,255,110); border-radius:16px; }")
        update_lay = QVBoxLayout(update_frame)
        update_lay.setContentsMargins(16, 14, 16, 14)
        update_lay.setSpacing(8)
        update_info = QLabel(f"Current version: {APP_VERSION}\nRenewly checks the official GitHub version automatically when the app starts.")
        update_info.setWordWrap(True)
        update_info.setStyleSheet("color:#53627A;")
        check_updates_btn = QPushButton("🔄 Check for Updates Now")
        check_updates_btn.setMinimumHeight(40)
        check_updates_btn.clicked.connect(lambda: self.check_for_updates(silent=False))
        update_lay.addWidget(update_info)
        update_lay.addWidget(check_updates_btn)
        lay.addWidget(update_frame)

        section_label("DATA BACKUP & EXPORT")
        data_frame = QFrame()
        data_frame.setObjectName("glassCard")
        data_frame.setStyleSheet("QFrame#glassCard { background:rgba(255,255,255,65); border:1px solid rgba(255,255,255,110); border-radius:16px; }")
        data_lay = QVBoxLayout(data_frame)
        data_lay.setContentsMargins(16, 14, 16, 14)
        data_lay.setSpacing(10)
        backup_btn = QPushButton("Create Full Backup (.json)")
        export_btn = QPushButton("Export Plans to CSV")
        advanced_export_btn = QPushButton("Advanced Selective Export & Reports")
        import_btn = QPushButton("Restore From Backup (.json)")
        for b in (backup_btn, export_btn, advanced_export_btn, import_btn):
            b.setMinimumHeight(40)
            data_lay.addWidget(b)
        lay.addWidget(data_frame)
        lay.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        outer.addWidget(buttons)

        def create_backup():
            path, _ = QFileDialog.getSaveFileName(dlg, "Create Full Backup", os.path.join(USER_DATA_DIR, "plan_reminder_backup.json"), "JSON Files (*.json)")
            if not path:
                return
            backup = {
                "plans": self.plans,
                "recycle_bin": self.recycle,
                "settings": load_app_settings(),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            try:
                save_json(path, backup)
                QMessageBox.information(dlg, "Backup Created", "Your application backup was created successfully.")
            except Exception as e:
                QMessageBox.critical(dlg, "Backup Error", str(e))

        def export_csv():
            path, _ = QFileDialog.getSaveFileName(dlg, "Export Plans to CSV", os.path.join(USER_DATA_DIR, "plans_export.csv"), "CSV Files (*.csv)")
            if not path:
                return
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["ID", "Plan Name", "Renewal Date", "Amount", "Reminder Days", "Status", "Payment Status", "Notes"])
                    for plan in self.plans:
                        writer.writerow([
                            plan.get("id", ""), plan.get("name", ""), plan.get("renewal_date", ""),
                            plan.get("amount", ""), plan.get("reminder_days", current.get("default_reminder_days", 3)),
                            self.status(plan), plan.get("notes", "")
                        ])
                QMessageBox.information(dlg, "Export Complete", "Your plan list was exported successfully.")
            except Exception as e:
                QMessageBox.critical(dlg, "Export Error", str(e))

        def restore_backup():
            path, _ = QFileDialog.getOpenFileName(dlg, "Restore Backup", USER_DATA_DIR, "JSON Files (*.json)")
            if not path:
                return
            try:
                backup = load_json(path, None)
                if not isinstance(backup, dict) or not isinstance(backup.get("plans"), list):
                    raise ValueError("This file is not a valid Renewly backup.")
                answer = QMessageBox.question(dlg, "Restore Backup", "This will replace the current plans and recycle bin with the backup data. Continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
                self.plans = backup.get("plans", [])
                self.recycle = backup.get("recycle_bin", [])
                self.save_data()
                save_json(RECYCLE_FILE, self.recycle)
                if isinstance(backup.get("settings"), dict):
                    restored = DEFAULT_SETTINGS.copy()
                    restored.update(backup["settings"])
                    save_app_settings(restored)
                    self.settings = restored
                    Theme.mode = "dark" if restored.get("theme") == "dark" else "light"
                    Theme.set_accent(restored.get("accent_color"))
                    apply_global_font(restored, self)
                    save_theme(Theme.mode)
                    self.apply_theme()
                    if hasattr(self, "sidebar"):
                        self.sidebar.set_tint_color(restored.get("sidebar_color", DEFAULT_SETTINGS["sidebar_color"]))
                    central = self.centralWidget()
                    if isinstance(central, GlassBackground):
                        central.reload_background()
                    self.clock.setVisible(bool(restored.get("show_clock", True)))
                    self.apply_dashboard_customization()
                    self.apply_clock_style()
                self.refresh_all()
                QMessageBox.information(dlg, "Restore Complete", "Backup restored successfully.")
            except Exception as e:
                QMessageBox.critical(dlg, "Restore Error", str(e))

        def test_notification():
            # This button must test the real Web Push path, not merely show a
            # local Qt message box. The backend sends the notification to the
            # browser subscription registered for the signed-in Renewly account.
            if sound.isChecked():
                QApplication.beep()
            self._send_web_push_test(dlg)

        backup_btn.clicked.connect(create_backup)
        export_btn.clicked.connect(export_csv)
        advanced_export_btn.clicked.connect(self.open_selective_export)
        import_btn.clicked.connect(restore_backup)
        test_notification_btn.clicked.connect(test_notification)
        push_portal_btn.clicked.connect(self.open_web_push_portal)

        def reset_dashboard_layout():
            for checkbox in card_visibility.values():
                checkbox.setChecked(True)
            density_choice.setCurrentText("Comfortable")
            default_page_choice.setCurrentText("Home Page")
            show_summary.setChecked(True)
            show_search.setChecked(True)
            show_status_filters.setChecked(True)
            show_bulk_actions.setChecked(True)

        reset_dashboard_btn.clicked.connect(reset_dashboard_layout)

        # Remember what the Theme/Preset boxes showed on opening: saving other settings must
        # never change the light/dark mode unless the user actually changed one of them.
        initial_theme_text = theme_choice.currentText()
        initial_preset_text = preset_choice.currentText()

        def save_settings():
            appearance_touched = (
                theme_choice.currentText() != initial_theme_text
                or preset_choice.currentText() != initial_preset_text
            )
            web_keys = {k: v for k, v in self.settings.items() if str(k).startswith("web_")}
            self.settings = {
                "default_reminder_days": int(reminder_days.value()),
                "notifications_enabled": notifications.isChecked(),
                "sound_enabled": sound.isChecked(),
                "date_format": date_format.currentText(),
                "currency": currency.currentText(),
                "show_clock": clock_toggle.isChecked(),
                "clock_style": clock_style.currentText(),
                "theme": "dark" if theme_choice.currentText() == "Dark" else "light",
                "background_enabled": background_toggle.isChecked(),
                "background_opacity": int(background_opacity.value()),
                "background_image": selected_background["value"],
                "accent_color": accent_color["value"],
                "sidebar_color": sidebar_color["value"],
                "font_size": int(font_size.value()),
                "font_family": font_family.currentFont().family(),
                "theme_preset": preset_choice.currentText(),

                "dashboard_visible_cards": [
                    card_id for card_id, checkbox in card_visibility.items()
                    if checkbox.isChecked()
                ],
                "dashboard_card_order": self.dashboard_card_order(),
                "dashboard_density": (
                    "compact" if density_choice.currentText() == "Compact"
                    else "comfortable"
                ),
                "dashboard_default_page": (
                    "analytics" if default_page_choice.currentText() == "Analytics"
                    else "dashboard"
                ),
                "dashboard_show_summary": show_summary.isChecked(),
                "dashboard_show_search": show_search.isChecked(),
                "dashboard_show_status_filters": show_status_filters.isChecked(),
                "dashboard_show_bulk_actions": show_bulk_actions.isChecked(),

                "app_lock_enabled": app_lock_enabled.isChecked(),
                "app_lock_on_startup": lock_on_startup.isChecked(),
                "app_lock_on_minimize": lock_on_minimize.isChecked(),
                "app_lock_inactivity_minutes": int(inactivity_minutes.value()),
                "cloud_sync_enabled": bool(self._cloud_api and self._cloud_api.is_authenticated),
                "cloud_account_email": str(self._cloud_email or cloud_email.text().strip()),
            }
            self.settings.update(web_keys)  # keep the web UI's currency / clock preferences
            # Apply the complete preset palette before saving, so presets affect the whole app.
            preset = self.settings.get("theme_preset", "Custom")
            if not appearance_touched:
                self.settings["theme"] = "dark" if Theme.dark() else "light"
            if appearance_touched and preset in THEME_PRESET_PALETTES and preset != "Custom":
                p = THEME_PRESET_PALETTES[preset]
                self.settings["theme"] = p["theme"]
                self.settings["accent_color"] = p["accent"]
                self.settings["sidebar_color"] = p["sidebar"]
            save_app_settings(self.settings)
            app_instance = QApplication.instance()
            if app_instance is not None and appearance_touched:
                apply_preset_palette(app_instance, self.settings)
            Theme.set_accent(self.settings["accent_color"])
            apply_global_font(self.settings, self)
            if hasattr(self, "sidebar"):
                self.sidebar.set_tint_color(self.settings["sidebar_color"])
            central = self.centralWidget()
            if isinstance(central, GlassBackground):
                central.reload_background()
            self.clock.setVisible(self.settings["show_clock"])
            self.apply_dashboard_customization()
            new_theme = self.settings["theme"]
            if Theme.mode != new_theme:
                Theme.mode = new_theme
                save_theme(new_theme)
                self.apply_theme()
            self.apply_clock_style()
            self.refresh_all()
            dlg.accept()

        buttons.accepted.connect(save_settings)
        buttons.rejected.connect(dlg.reject)
        apply_theme_to(dlg)
        dlg.exec()

    # ---------------- Background reminder + Windows system tray ----------------
    def setup_system_tray(self):
        """Create a system-tray controller so reminders continue after the window closes."""
        if self.tray_icon is not None:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        # Use the Renewly logo for the system tray when available.
        tray_logo = get_app_icon()
        if tray_logo.isNull():
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor("#4F8CFF"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(6, 6, 52, 52)
            painter.setPen(QPen(QColor("white"), 5))
            painter.drawLine(32, 18, 32, 34)
            painter.drawLine(32, 34, 43, 41)
            painter.end()
            tray_logo = QIcon(pixmap)

        self.tray_icon = QSystemTrayIcon(tray_logo, self)
        self.tray_icon.setToolTip(APP_NAME + " — running in background")

        menu = QMenu(self)
        open_action = QAction("Open Renewly", self)
        open_action.triggered.connect(self.restore_from_tray)
        menu.addAction(open_action)

        hide_action = QAction("Hide Window", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)
        menu.addSeparator()

        quit_action = QAction("Exit Application", self)
        quit_action.triggered.connect(self.exit_application)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_from_tray()

    def hide_to_tray(self):
        """Hide Renewly while keeping reminders and background services running."""
        if self.tray_icon is None:
            self.setup_system_tray()
        self.hide()
        if self.tray_icon is not None:
            self.tray_icon.show()

    def enter_true_fullscreen(self):
        """Force a Windows-level fullscreen surface that covers the taskbar.

        Qt's showFullScreen() normally reserves the Windows taskbar area on
        some Windows configurations. Renewly needs the taskbar underneath the
        application, so on Windows we explicitly size the native window to the
        complete physical screen and place it above the taskbar.
        """
        try:
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

        if sys.platform.startswith("win"):
            try:
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                screen = self.screen() or QApplication.primaryScreen()
                geometry = screen.geometry() if screen else None
                if geometry is not None:
                    HWND_TOPMOST = -1
                    SWP_SHOWWINDOW = 0x0040
                    SWP_FRAMECHANGED = 0x0020
                    user32.SetWindowPos(
                        hwnd, HWND_TOPMOST,
                        int(geometry.x()), int(geometry.y()),
                        int(geometry.width()), int(geometry.height()),
                        SWP_SHOWWINDOW | SWP_FRAMECHANGED,
                    )
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                apply_windows_acrylic(hwnd, Theme.dark())
                user32.SetForegroundWindow(hwnd)
            except Exception:
                # Qt fullscreen remains as the safe fallback.
                pass

        try:
            apply_windows_acrylic(int(self.winId()), Theme.dark())
        except Exception:
            pass
        self.raise_()
        self.activateWindow()

    def restore_from_tray(self):
        # Restore Renewly directly into true fullscreen so the Windows taskbar
        # remains underneath the application.
        self.enter_true_fullscreen()

    def exit_application(self):
        self._allow_real_close = True
        if self.tray_icon is not None:
            self.tray_icon.hide()
        QApplication.quit()

    def lock_app(self, reason="Unlock Renewly"):
        """Show a modal lock screen and verify through Windows Hello."""
        if not self.settings.get("app_lock_enabled", False) or self._app_locked:
            return
        self._app_locked = True
        dlg = QDialog(self)
        self._lock_dialog = dlg
        dlg.setWindowTitle("Renewly Locked")
        dlg.setModal(True)
        dlg.setWindowFlag(Qt.WindowCloseButtonHint, False)
        fit_dialog(dlg, 430, 330, 360, 270)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(14)
        icon = QLabel("🔒")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size:52px;")
        title = QLabel("RENEWLY IS LOCKED")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:20px; font-weight:900;")
        subtitle = QLabel("Verify with Windows Hello to continue.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#66758C;")
        status = QLabel(reason)
        status.setAlignment(Qt.AlignCenter)
        status.setWordWrap(True)
        status.setStyleSheet("color:#66758C; font-size:11px;")
        unlock_btn = QPushButton("🙂 UNLOCK WITH WINDOWS HELLO")
        unlock_btn.setMinimumHeight(46)
        exit_btn = QPushButton("Exit Renewly")
        exit_btn.setMinimumHeight(36)
        layout.addWidget(icon); layout.addWidget(title); layout.addWidget(subtitle)
        layout.addWidget(status); layout.addSpacing(4); layout.addWidget(unlock_btn); layout.addWidget(exit_btn)

        def unlock():
            unlock_btn.setEnabled(False)
            status.setText("Waiting for Windows Hello verification…")
            QApplication.processEvents()
            ok, message = windows_hello_verify("Unlock Renewly")
            if ok:
                self._app_locked = False
                self._last_activity_ms = 0
                dlg.accept()
                self.activateWindow()
                self.raise_()
            else:
                status.setText(message or "Verification failed. Try again.")
                unlock_btn.setEnabled(True)

        def exit_app():
            self._app_locked = False
            self._allow_real_close = True
            dlg.reject()
            self.close()

        unlock_btn.clicked.connect(unlock)
        exit_btn.clicked.connect(exit_app)
        dlg.exec()
        self._lock_dialog = None

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if self.settings.get("app_lock_enabled", False) and self.settings.get("app_lock_on_minimize", False):
                if self.isMinimized():
                    QTimer.singleShot(0, lambda: self.lock_app("Renewly was minimized. Unlock to continue."))

    def eventFilter(self, watched, event):
        if watched is QApplication.instance() and self.settings.get("app_lock_enabled", False):
            timeout = int(self.settings.get("app_lock_inactivity_minutes", 0) or 0)
            if timeout > 0 and not self._app_locked:
                if event.type() in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
                    now = datetime.now().timestamp()
                    if self._last_activity_ms and (now - self._last_activity_ms) >= timeout * 60:
                        QTimer.singleShot(0, lambda: self.lock_app("Renewly was locked after inactivity."))
                    self._last_activity_ms = now
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        # Clicking the X button keeps the reminder service alive in the tray.
        if not self._allow_real_close and self.tray_icon is not None and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Renewly",
                "The app is still running in the system tray and will continue checking renewals.",
                QSystemTrayIcon.Information,
                5000,
            )
            return
        event.accept()

    def _send_web_push_test(self, dialog=None):
        """Send a real Web Push test through the authenticated backend session."""
        api = getattr(self, "_cloud_api", None)
        if api is None or not getattr(api, "is_authenticated", False):
            QMessageBox.warning(
                dialog or self,
                "Web Push",
                "Renewly Cloud is not connected. Please sign in to your Renewly account first."
            )
            return

        # Avoid overlapping test requests.
        for worker in list(getattr(self, "_web_push_test_workers", set())):
            if worker.isRunning():
                return

        worker = WebPushTestWorker(api, self)
        self._web_push_test_workers.add(worker)

        def handle_result(result):
            if result.get("ok"):
                QMessageBox.information(
                    dialog or self,
                    "Web Push Test Successful",
                    "🔔 Web Push notification sent successfully.\n\n"
                    f"Browser subscriptions reached: {result.get('sent', 0)}"
                )
            else:
                QMessageBox.warning(
                    dialog or self,
                    "Web Push Test Failed",
                    "The Renewly backend could not deliver the Web Push notification.\n\n"
                    + str(result.get("error", "Unknown Web Push error."))
                )

        worker.finished_test.connect(handle_result)
        worker.finished.connect(lambda w=worker: self._web_push_test_workers.discard(w))
        worker.start()

    def open_web_push_portal(self):
        try:
            url = f"{RENEWLY_BACKEND_URL.rstrip('/')}/web/"
            QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:
            QMessageBox.warning(self, "Web Push", f"Could not open the Renewly Web Notification portal.\n\n{exc}")

    def _telegram_token_for_settings_test(self, field, current_settings):
        value = "".join(field.text().split())
        if value and value != "••••••••••••••••••••":
            return value
        return _dpapi_unprotect(current_settings.get("telegram_bot_token_protected", ""))

    def _telegram_bot_token(self):
        return _dpapi_unprotect(self.settings.get("telegram_bot_token_protected", ""))

    def _start_telegram_alert(self, text, marker=None, purpose="alert"):
        # Telegram alerts are retired in favor of server-side Web Push.
        return False

        if not self.settings.get("telegram_alerts_enabled", False):
            return False
        token = self._telegram_bot_token()
        chat_id = str(self.settings.get("telegram_chat_id", "")).strip()
        if not token or not chat_id:
            return False
        if marker and (marker in self._telegram_sent_markers or marker in self._telegram_inflight_markers):
            return False
        if marker:
            self._telegram_inflight_markers.add(marker)
        worker = TelegramAlertWorker(token, chat_id, text, marker, purpose, self)
        self._telegram_workers.add(worker)
        worker.finished_send.connect(self._handle_telegram_result)
        worker.finished.connect(lambda w=worker: self._telegram_workers.discard(w))
        worker.start()
        return True

    def _handle_telegram_result(self, result):
        marker = result.get("marker")
        if marker:
            self._telegram_inflight_markers.discard(marker)
        if not result.get("ok"):
            # Keep the local reminder working even if Telegram is temporarily unavailable.
            if result.get("purpose") == "test":
                QMessageBox.warning(self, "Telegram Test Failed", "The Telegram alert could not be sent.\n\n" + str(result.get("error", "Unknown error")))
            return
        if marker:
            self._telegram_sent_markers.add(marker)
            today_prefix = (date.today().toordinal() - 30,)
            # Markers start with an ISO date. Keep the list bounded to the last 31 days.
            kept = []
            cutoff = date.today().toordinal() - 31
            for item in self._telegram_sent_markers:
                try:
                    marker_date = date.fromisoformat(str(item).split("|", 1)[0])
                    if marker_date.toordinal() >= cutoff:
                        kept.append(item)
                except Exception:
                    pass
            self._telegram_sent_markers = set(kept)
            self.settings["telegram_sent_markers"] = sorted(self._telegram_sent_markers)
            save_app_settings(self.settings)
        if result.get("purpose") == "test":
            QMessageBox.information(self, "Telegram Test Successful", "A test alert was sent to your Telegram chat.")

    def _send_telegram_test(self, bot_token, chat_id):
        if not bot_token.strip() or not chat_id.strip():
            QMessageBox.warning(self, "Telegram Setup", "Enter both the Bot Token and Chat ID first.")
            return
        worker = TelegramAlertWorker(
            bot_token.strip(), chat_id.strip(),
            "🔔 Renewly Mobile Alert Test\n\nYour Telegram mobile alerts are connected successfully.",
            purpose="test", parent=self
        )
        self._telegram_workers.add(worker)
        worker.finished_send.connect(self._handle_telegram_result)
        worker.finished.connect(lambda w=worker: self._telegram_workers.discard(w))
        worker.start()

    def check_renewal_notifications(self):
        """Check due plans and send Windows + Telegram alerts without reopening the app."""
        if not self.settings.get("notifications_enabled", True):
            return

        self.plans = load_json(DATA_FILE, self.plans)
        today_key = date.today().isoformat()
        due = []
        new_due = []
        new_expired = []

        for plan in self.plans:
            if not isinstance(plan, dict):
                continue
            current_status = self.status(plan)
            plan_key = str(plan.get("id") or plan.get("name") or id(plan))
            if current_status == "DUE SOON":
                due.append(plan)
                notification_key = (today_key, plan_key, "due")
                if notification_key not in self._notified_reminders:
                    self._notified_reminders.add(notification_key)
                    new_due.append(plan)
            elif current_status == "EXPIRED":
                notification_key = (today_key, plan_key, "expired")
                if notification_key not in self._notified_reminders:
                    self._notified_reminders.add(notification_key)
                    new_expired.append(plan)

        self._notified_reminders = {k for k in self._notified_reminders if k[0] == today_key}

        if not new_due and not new_expired:
            return

        # Local Windows notification.
        if new_due:
            names = ", ".join(str(p.get("name", "Plan")) for p in new_due[:3])
            if len(new_due) > 3:
                names += f" and {len(new_due) - 3} more"
            title = "🔔 Renewal Reminder"
            message = (
                f"{names} is due soon. Open the app to review your renewal."
                if len(new_due) == 1 else
                f"{len(new_due)} plans need attention: {names}"
            )
            self.add_notification_history(title, message)
            if self.settings.get("sound_enabled", False):
                QApplication.beep()
            if self.tray_icon is not None and self.tray_icon.isVisible():
                self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 10000)
            else:
                QMessageBox.information(self, title, message)

        if new_expired:
            names = ", ".join(str(p.get("name", "Plan")) for p in new_expired[:3])
            if len(new_expired) > 3:
                names += f" and {len(new_expired) - 3} more"
            title = "⚠️ Renewal Expired"
            message = f"{names} has expired." if len(new_expired) == 1 else f"{len(new_expired)} plans have expired: {names}"
            self.add_notification_history(title, message)
            if self.tray_icon is not None and self.tray_icon.isVisible():
                self.tray_icon.showMessage(title, message, QSystemTrayIcon.Warning, 10000)

        # Telegram mobile push alerts run in background threads so the UI never freezes.
        if self.settings.get("telegram_alerts_enabled", False):
            currency = CURRENCY_SYMBOLS.get(self.settings.get("currency"), "₹")
            if new_due and self.settings.get("telegram_alert_due_soon", True):
                for plan in new_due:
                    pid = str(plan.get("id") or plan.get("name") or "plan")
                    marker = f"{today_key}|{pid}|due"
                    days = self.days_left(plan)
                    text = (
                        "🔔 Renewly Renewal Reminder\n\n"
                        f"Plan: {plan.get('name', 'Plan')}\n"
                        f"Renewal: {self.format_plan_date(plan.get('renewal_date', ''))}\n"
                        f"Amount: {currency} {plan.get('amount', '—')}\n"
                        f"Days left: {days}\n"
                        f"Status: {self.status(plan)}"
                    )
                    self._start_telegram_alert(text, marker=marker, purpose="due")
            if new_expired and self.settings.get("telegram_alert_expired", True):
                for plan in new_expired:
                    pid = str(plan.get("id") or plan.get("name") or "plan")
                    marker = f"{today_key}|{pid}|expired"
                    text = (
                        "⚠️ Renewly Renewal Expired\n\n"
                        f"Plan: {plan.get('name', 'Plan')}\n"
                        f"Renewal: {self.format_plan_date(plan.get('renewal_date', ''))}\n"
                        f"Amount: {currency} {plan.get('amount', '—')}\n"
                        "Status: EXPIRED"
                    )
                    self._start_telegram_alert(text, marker=marker, purpose="expired")

    def check_startup_reminders(self):
        # Kept for compatibility with older calls.
        self.check_renewal_notifications()

    # ---------------- Global Search & Command Palette ----------------
    def open_command_palette(self):
        """Open the Ctrl+K global search and command palette."""
        if getattr(self, "_command_palette", None) and self._command_palette.isVisible():
            self._command_palette.activateWindow()
            return

        dark = Theme.dark()
        bg = "#101827" if dark else "#F4F8FF"
        card = "#182337" if dark else "#FFFFFF"
        text = "#F4F8FF" if dark else "#172033"
        muted = "#94A3B8" if dark else "#66758C"
        accent = self.settings.get("accent_color", "#6C8DFF")

        dlg = QDialog(self)
        self._command_palette = dlg
        dlg.setWindowTitle("Command Palette")
        dlg.setModal(True)
        fit_dialog(dlg, 720, 560, 440, 360)
        dlg.setStyleSheet(f"""
            QDialog {{ background:{bg}; }}
            QLineEdit {{ background:{card}; color:{text}; border:2px solid {accent};
                         border-radius:16px; padding:14px 18px; font-size:16px; }}
            QScrollArea {{ border:none; background:transparent; }}
            QLabel {{ color:{text}; }}
        """)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("⌕  GLOBAL SEARCH")
        title.setStyleSheet(f"font-size:22px; font-weight:900; color:{text};")
        sub = QLabel("Search plans or run a quick command")
        sub.setStyleSheet(f"font-size:11px; color:{muted};")
        title_box.addWidget(title); title_box.addWidget(sub)
        hint = QLabel("CTRL + K")
        hint.setStyleSheet(f"background:{card}; color:{muted}; border:1px solid rgba(130,150,185,90); border-radius:9px; padding:7px 10px; font-size:10px; font-weight:800;")
        top.addLayout(title_box); top.addStretch(); top.addWidget(hint, 0, Qt.AlignTop)
        root.addLayout(top)

        search = QLineEdit()
        search.setPlaceholderText("Type a command or search your plans...")
        root.addWidget(search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 2, 4, 2)
        results_layout.setSpacing(8)
        scroll.setWidget(results)
        root.addWidget(scroll, 1)

        def clear_results():
            while results_layout.count():
                item = results_layout.takeAt(0)
                w = item.widget()
                if w: w.deleteLater()

        def section(label):
            lab = QLabel(label.upper())
            lab.setStyleSheet(f"color:{muted}; font-size:10px; font-weight:900; letter-spacing:1px; padding:8px 4px 2px 4px;")
            results_layout.addWidget(lab)

        def add_item(icon, label, description, callback):
            btn = QPushButton()
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(58)
            btn.setText(f"{icon}   {label}\n     {description}")
            btn.setStyleSheet(f"""
                QPushButton {{ background:{card}; color:{text}; border:1px solid rgba(130,150,185,80);
                              border-radius:14px; text-align:left; padding:8px 14px; font-size:12px; font-weight:700; }}
                QPushButton:hover {{ border-color:{accent}; background:rgba(108,141,255,28); }}
            """)
            def run():
                dlg.accept()
                callback()
            btn.clicked.connect(run)
            results_layout.addWidget(btn)

        commands = [
            ("➕", "Add New Plan", "Create a new renewal plan", self.add_plan, "add new plan"),
            ("⌂", "Open Home Page", "Return to the main home page", self.show_dashboard_home, "home page dashboard"),
            ("📊", "Open Analytics", "View spending and renewal insights", self.show_analytics, "analytics"),
            ("🤖", "Open Smart Assistant", "View smart renewal recommendations", self.show_smart_assistant, "smart assistant"),
            ("🛒", "Open Purchases", "Manage purchases and warranties", self.show_purchases, "purchases warranty"),
            ("📅", "Open Calendar", "Browse upcoming renewal dates", self.show_calendar, "calendar"),
            ("⚙", "Open Settings", "Customize your application", self.show_settings, "settings"),
            ("◈", "Open Profile", "Manage profile and account", self.show_profile, "profile account"),
            ("◑", "Toggle Theme", "Switch between light and dark mode", self.toggle_theme, "theme dark light"),
            ("♲", "Open Recycle Bin", "View deleted plans", self.show_recycle, "recycle bin trash"),
        ]

        def show_results(query=""):
            clear_results()
            q = query.strip().lower()
            matching_commands = [c for c in commands if not q or q in (c[1] + " " + c[2] + " " + c[4]).lower()]
            if matching_commands:
                section("Quick Actions")
                for icon, label, desc, callback, _keywords in matching_commands:
                    add_item(icon, label, desc, callback)

            matching_plans = []
            if q:
                for plan in self.plans:
                    haystack = " ".join(str(plan.get(k, "")) for k in ("name", "category", "notes", "billing_cycle"))
                    if q in haystack.lower():
                        matching_plans.append(plan)

            if matching_plans:
                section("Plans")
                for plan in matching_plans[:12]:
                    name = str(plan.get("name", "Unnamed Plan"))
                    category = str(plan.get("category", "Plan"))
                    renewal = str(plan.get("renewal_date", "")) or "No renewal date"
                    def open_plan(p=plan):
                        self.show_dashboard_home()
                        self.search.setText(str(p.get("name", "")))
                    add_item("◉", name, f"{category} • Renewal: {renewal}", open_plan)

            if q and not matching_commands and not matching_plans:
                empty = QLabel("No commands or plans found.")
                empty.setAlignment(Qt.AlignCenter)
                empty.setStyleSheet(f"color:{muted}; padding:45px; font-size:13px;")
                results_layout.addWidget(empty)
            results_layout.addStretch(1)

        search.textChanged.connect(show_results)
        show_results()
        QTimer.singleShot(0, search.setFocus)
        dlg.exec()

    def show_profile(self):
        """Open the upgraded Profile / Account control center."""
        user = load_user() or {}
        username = str(user.get("username") or "admin")
        role = str(user.get("role") or "Administrator")
        profile_picture = str(user.get("profile_picture") or "")

        dark_mode = Theme.dark()
        # Keep the Profile page visually consistent with Renewly's transparent 3D glass UI.
        bg = "rgba(8,16,30,238)" if dark_mode else "rgba(205,218,236,238)"
        card_bg = "rgba(24,35,53,118)" if dark_mode else "rgba(255,255,255,78)"
        card_soft = "rgba(31,45,68,92)" if dark_mode else "rgba(255,255,255,105)"
        text_color = "#F4F8FF" if dark_mode else "#142033"
        muted = "#9BAAC0" if dark_mode else "#65748A"
        accent = Theme.accent_color or "#6C8DFF"
        cyan = "#35D4FF"
        green = "#48E0A4"
        orange = "#F5B84B"
        border = "rgba(124,154,205,100)" if dark_mode else "rgba(145,166,201,125)"

        dlg = QDialog(self)
        dlg.setWindowTitle("Profile / Account")
        fit_dialog(dlg, 760, 760, 480, 440)
        dlg.setStyleSheet(f"""
            QDialog {{ background:{bg}; color:{text_color}; }}
            QScrollArea {{ border:none; background:transparent; }}
            QWidget#ProfileContent {{ background:transparent; }}
            QLabel#ProfileTitle {{ font-size:27px; font-weight:900; color:{text_color}; }}
            QLabel#ProfileSub, QLabel#SectionTitle {{ color:{muted}; font-size:10px; font-weight:900; letter-spacing:1.2px; }}
            QLineEdit, QComboBox, QSpinBox {{ background:{card_soft}; color:{text_color}; border:1px solid {border}; border-radius:10px; padding:7px 10px; }}
            QCheckBox {{ color:{text_color}; font-weight:700; spacing:8px; }}
        """)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("ProfileContent")
        content.setAttribute(Qt.WA_TranslucentBackground)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        def card_layout(radius=20):
            # Real Renewly glass surface: transparent, raised and softly beveled.
            frame = GlassFrame(radius=radius, alpha=78 if dark_mode else 68)
            frame.setAttribute(Qt.WA_TranslucentBackground)
            frame.setStyleSheet("background:transparent;")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(11)
            return frame, layout

        def section_title(text):
            label = QLabel(text)
            label.setObjectName("SectionTitle")
            root.addWidget(label)
            return label

        def action_button(icon, title, subtitle, tint):
            button = QPushButton(f"{icon}  {title}\n     {subtitle}")
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(66)
            button.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(255,255,255,{22 if dark_mode else 58});
                    color:{text_color};
                    border:1px solid rgba(255,255,255,{68 if dark_mode else 150});
                    border-radius:16px;
                    text-align:left;
                    padding:11px 14px;
                    font-size:11px;
                    font-weight:850;
                }}
                QPushButton:hover {{
                    background:rgba(255,255,255,{42 if dark_mode else 92});
                    border:1px solid {tint};
                    padding:9px 14px 13px 14px;
                }}
                QPushButton:pressed {{
                    background:rgba(108,141,255,70);
                    padding:12px 14px 10px 14px;
                }}
            """)
            return button

        # ===== PREMIUM TRANSPARENT GLASS HEADER =====
        header_card = GlassFrame(radius=24, alpha=74 if dark_mode else 64)
        header_card.setMinimumHeight(92)
        header = QHBoxLayout(header_card)
        header.setContentsMargins(22, 17, 22, 17)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("PROFILE / ACCOUNT")
        title.setObjectName("ProfileTitle")
        subtitle = QLabel("YOUR PERSONAL RENEWLY CONTROL CENTER")
        subtitle.setObjectName("ProfileSub")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        active_badge = QLabel("●  ACTIVE")
        active_badge.setStyleSheet(f"color:{green}; background:rgba(72,224,164,18); border:1px solid rgba(72,224,164,70); border-radius:14px; padding:7px 12px; font-size:10px; font-weight:900;")
        header.addLayout(title_box, 1)
        header.addWidget(active_badge, 0, Qt.AlignTop)
        root.addWidget(header_card)

        # ===== PROFILE INFORMATION =====
        profile_card, profile_layout = card_layout(22)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(16)
        avatar = QLabel()
        avatar.setFixedSize(86, 86)
        avatar.setAlignment(Qt.AlignCenter)
        selected_picture = {"path": profile_picture}
        current_username = {"value": username}

        def circular_avatar(source, size=80):
            result = QPixmap(size, size)
            result.fill(Qt.transparent)
            if source.isNull():
                return result
            scaled = source.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter = QPainter(result)
            painter.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath(); path.addEllipse(0, 0, size, size)
            painter.setClipPath(path)
            painter.drawPixmap((size-scaled.width())//2, (size-scaled.height())//2, scaled)
            painter.end()
            return result

        def update_avatar():
            pix = QPixmap(selected_picture["path"]) if selected_picture["path"] and os.path.isfile(selected_picture["path"]) else QPixmap()
            if not pix.isNull():
                avatar.setPixmap(circular_avatar(pix, 80)); avatar.setText("")
                avatar.setStyleSheet(f"background:transparent; border:3px solid {accent}; border-radius:43px;")
            else:
                avatar.setPixmap(QPixmap()); avatar.setText((current_username["value"][:1] or "A").upper())
                avatar.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {accent},stop:1 {cyan}); color:white; border:3px solid rgba(255,255,255,150); border-radius:43px; font-size:30px; font-weight:900;")

        identity = QVBoxLayout()
        identity.setSpacing(4)
        name_label = QLabel(username); name_label.setStyleSheet(f"font-size:23px; font-weight:900; color:{text_color};")
        role_label = QLabel(role.upper()); role_label.setStyleSheet(f"font-size:10px; font-weight:900; color:{cyan}; letter-spacing:1px;")
        updated_label = QLabel(f"Last updated: {user.get('profile_updated') or 'Not updated yet'}")
        updated_label.setStyleSheet(f"font-size:10px; color:{muted};")
        identity.addWidget(name_label); identity.addWidget(role_label); identity.addWidget(updated_label)
        profile_row.addWidget(avatar); profile_row.addLayout(identity, 1)
        profile_layout.addLayout(profile_row)

        actions = QHBoxLayout(); actions.setSpacing(9)
        upload_button = action_button("◉", "PROFILE IMAGE", "Change picture", cyan)
        name_button = action_button("✦", "DISPLAY NAME", "Edit your name", accent)
        password_button = action_button("⬡", "SECURITY", "Reset password", green)
        for b in (upload_button, name_button, password_button): actions.addWidget(b, 1)
        profile_layout.addLayout(actions)
        root.addWidget(profile_card)

        section_title("ACCOUNT INFORMATION")
        info_card, info_layout = card_layout(18)
        def info_row(key_text, value_text):
            row = QHBoxLayout()
            key = QLabel(key_text); key.setStyleSheet(f"color:{muted}; font-size:10px; font-weight:900; letter-spacing:.8px;")
            value = QLabel(value_text); value.setStyleSheet(f"color:{text_color}; font-size:13px; font-weight:800;")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(key); row.addStretch(1); row.addWidget(value)
            return row
        info_layout.addLayout(info_row("DISPLAY NAME", username))
        info_layout.addLayout(info_row("ACCOUNT ROLE", role))
        info_layout.addLayout(info_row("APP VERSION", APP_VERSION))
        root.addWidget(info_card)

        # ===== ACCOUNT STATISTICS =====
        section_title("ACCOUNT STATISTICS")
        stats_card, stats_layout = card_layout(20)
        plans = list(getattr(self, "plans", []) or [])
        today = date.today()
        active_count = 0; due_count = 0; paid_count = 0; total_amount = 0.0
        for plan in plans:
            try: total_amount += float(plan.get("amount", 0) or 0)
            except Exception: pass
            if str(plan.get("payment_status", "")).upper() == "PAID": paid_count += 1
            try:
                renewal = datetime.strptime(str(plan.get("renewal_date", "")), "%d-%m-%Y").date()
                days = (renewal - today).days
                if days >= 0 and str(plan.get("payment_status", "")).upper() != "PAID": active_count += 1
                if 0 <= days <= 7 and str(plan.get("payment_status", "")).upper() != "PAID": due_count += 1
            except Exception: pass

        stat_grid = QGridLayout(); stat_grid.setSpacing(10)
        def stat_box(icon, label, value, tint):
            box = QFrame(); box.setObjectName("glassCard"); box.setStyleSheet(f"QFrame#glassCard {{ background:{card_soft}; border:1px solid {border}; border-radius:16px; }}")
            lay = QVBoxLayout(box); lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(4)
            top = QLabel(f"{icon}  {label}"); top.setStyleSheet(f"color:{muted}; font-size:9px; font-weight:900; letter-spacing:.8px;")
            num = QLabel(str(value)); num.setStyleSheet(f"color:{tint}; font-size:24px; font-weight:900;")
            lay.addWidget(top); lay.addWidget(num)
            return box
        stat_grid.addWidget(stat_box("◈", "TOTAL PLANS", len(plans), accent), 0, 0)
        stat_grid.addWidget(stat_box("✓", "ACTIVE", active_count, green), 0, 1)
        stat_grid.addWidget(stat_box("!", "DUE SOON", due_count, orange), 0, 2)
        stat_grid.addWidget(stat_box("₹", "TOTAL VALUE", f"₹ {total_amount:,.2f}", cyan), 1, 0)
        stat_grid.addWidget(stat_box("●", "PAID", paid_count, green), 1, 1)
        stat_grid.addWidget(stat_box("▣", "DATA ITEMS", len(plans) + len(getattr(self, "recycle", []) or []), accent), 1, 2)
        stats_layout.addLayout(stat_grid)
        root.addWidget(stats_card)

        # ===== APP PREFERENCES =====
        section_title("APP PREFERENCES")
        prefs_card, prefs_layout = card_layout(20)
        prefs_grid = QGridLayout(); prefs_grid.setHorizontalSpacing(14); prefs_grid.setVerticalSpacing(12)
        current_settings = dict(getattr(self, "settings", load_app_settings()))

        notifications_check = QCheckBox("Enable renewal notifications")
        notifications_check.setChecked(bool(current_settings.get("notifications_enabled", True)))
        sound_check = QCheckBox("Play notification sound")
        sound_check.setChecked(bool(current_settings.get("sound_enabled", False)))
        reminder_spin = QSpinBox(); reminder_spin.setRange(0, 3650); reminder_spin.setValue(int(current_settings.get("default_reminder_days", 3)))
        date_combo = QComboBox(); date_combo.addItems(list(DATE_FORMATS.keys())); date_combo.setCurrentText(current_settings.get("date_format", "DD-MM-YYYY"))
        currency_combo = QComboBox(); currency_combo.addItems(list(CURRENCY_SYMBOLS.keys())); currency_combo.setCurrentText(current_settings.get("currency", "INR (₹)"))
        save_prefs = QPushButton("Save Preferences")
        save_prefs.setCursor(Qt.PointingHandCursor)
        save_prefs.setStyleSheet(f"QPushButton {{ background:{accent}; color:white; border:none; border-radius:11px; padding:9px 16px; font-weight:900; }} QPushButton:hover {{ background:{cyan}; }}")
        prefs_grid.addWidget(notifications_check, 0, 0, 1, 2)
        prefs_grid.addWidget(sound_check, 1, 0, 1, 2)
        prefs_grid.addWidget(QLabel("Default reminder days"), 2, 0); prefs_grid.addWidget(reminder_spin, 2, 1)
        prefs_grid.addWidget(QLabel("Date format"), 3, 0); prefs_grid.addWidget(date_combo, 3, 1)
        prefs_grid.addWidget(QLabel("Currency"), 4, 0); prefs_grid.addWidget(currency_combo, 4, 1)
        prefs_layout.addLayout(prefs_grid); prefs_layout.addWidget(save_prefs, 0, Qt.AlignRight)
        root.addWidget(prefs_card)

        def persist_preferences():
            current = load_app_settings()
            current["notifications_enabled"] = notifications_check.isChecked()
            current["sound_enabled"] = sound_check.isChecked()
            current["default_reminder_days"] = reminder_spin.value()
            current["date_format"] = date_combo.currentText()
            current["currency"] = currency_combo.currentText()
            save_app_settings(current)
            self.settings = current
            QMessageBox.information(dlg, "Preferences Saved", "Your app preferences were updated successfully.")
        save_prefs.clicked.connect(persist_preferences)

        # ===== BACKUP & RESTORE =====
        section_title("BACKUP & RESTORE DATA")
        backup_card, backup_layout = card_layout(20)
        backup_desc = QLabel("Create a secure Renewly backup containing plans, recycle bin, settings and account profile information.")
        backup_desc.setWordWrap(True); backup_desc.setStyleSheet(f"color:{muted}; font-size:11px;")
        backup_layout.addWidget(backup_desc)
        backup_actions = QHBoxLayout(); backup_actions.setSpacing(10)
        create_backup_btn = action_button("⬇", "CREATE BACKUP", "Save all Renewly data", accent)
        restore_backup_btn = action_button("⬆", "RESTORE BACKUP", "Recover saved data", orange)
        backup_actions.addWidget(create_backup_btn, 1); backup_actions.addWidget(restore_backup_btn, 1)
        backup_layout.addLayout(backup_actions)
        root.addWidget(backup_card)

        def create_backup():
            default_name = os.path.join(USER_DATA_DIR, f"renewly_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
            path, _ = QFileDialog.getSaveFileName(dlg, "Create Renewly Backup", default_name, "Renewly Backup (*.json)")
            if not path: return
            backup = {
                "app": APP_NAME, "version": APP_VERSION, "created": datetime.now().isoformat(),
                "plans": getattr(self, "plans", []), "recycle_bin": getattr(self, "recycle", []),
                "settings": load_app_settings(), "user": load_user(),
            }
            try:
                save_json(path, backup)
                QMessageBox.information(dlg, "Backup Created", "Your Renewly backup was created successfully.")
            except Exception as exc:
                QMessageBox.critical(dlg, "Backup Error", str(exc))

        def restore_backup():
            path, _ = QFileDialog.getOpenFileName(dlg, "Restore Renewly Backup", USER_DATA_DIR, "Renewly Backup (*.json)")
            if not path: return
            backup = load_json(path, None)
            if not isinstance(backup, dict) or not isinstance(backup.get("plans"), list):
                QMessageBox.warning(dlg, "Invalid Backup", "This file is not a valid Renewly backup.")
                return
            answer = QMessageBox.question(dlg, "Restore Backup", "This will replace your current Renewly data. Continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes: return
            self.plans = backup.get("plans", [])
            self.recycle = backup.get("recycle_bin", [])
            save_json(DATA_FILE, self.plans); save_json(RECYCLE_FILE, self.recycle)
            if isinstance(backup.get("settings"), dict):
                restored = DEFAULT_SETTINGS.copy(); restored.update(backup["settings"])
                save_app_settings(restored); self.settings = restored
                Theme.mode = "dark" if restored.get("theme") == "dark" else "light"
                Theme.set_accent(restored.get("accent_color"))
            if isinstance(backup.get("user"), dict): save_user(backup["user"])
            if hasattr(self, "refresh_table"): self.refresh_table()
            if hasattr(self, "refresh_dashboard"): self.refresh_dashboard()
            QMessageBox.information(dlg, "Restore Complete", "Your Renewly data was restored successfully. Restart the app if some visual preferences do not update immediately.")

        create_backup_btn.clicked.connect(create_backup)
        restore_backup_btn.clicked.connect(restore_backup)

        # ===== FOOTER =====
        close_button = QPushButton("Close Profile / Account")
        close_button.setMinimumHeight(42); close_button.setCursor(Qt.PointingHandCursor)
        close_button.setStyleSheet(f"QPushButton {{ background:transparent; color:{muted}; border:1px solid {border}; border-radius:14px; font-weight:850; }} QPushButton:hover {{ color:{text_color}; border-color:{accent}; background:rgba(108,141,255,25); }}")
        close_button.clicked.connect(dlg.accept)
        root.addWidget(close_button)

        def choose_picture():
            path, _ = QFileDialog.getOpenFileName(dlg, "Choose Profile Picture", selected_picture["path"] or USER_DATA_DIR, "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
            if not path: return
            selected_picture["path"] = path; update_avatar()
            current_user = load_user() or {}; current_user.setdefault("username", current_username["value"]); current_user.setdefault("role", role)
            current_user["profile_picture"] = path; current_user["profile_updated"] = datetime.now().strftime("%d %b %Y, %I:%M %p")
            save_user(current_user); updated_label.setText(f"Last updated: {current_user['profile_updated']}")

        def change_name():
            name_dlg = QDialog(dlg); name_dlg.setWindowTitle("Update Display Name")
            form = QFormLayout(name_dlg); name_edit = QLineEdit(current_username["value"]); name_edit.selectAll(); form.addRow("New Name", name_edit)
            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); form.addRow(buttons)
            def save_name():
                new_name = name_edit.text().strip()
                if not new_name: QMessageBox.warning(name_dlg, "Display Name", "Name cannot be empty."); return
                current_username["value"] = new_name; name_label.setText(new_name)
                current_user = load_user() or {}; current_user["username"] = new_name; current_user.setdefault("role", role)
                current_user["profile_picture"] = selected_picture["path"]; current_user["profile_updated"] = datetime.now().strftime("%d %b %Y, %I:%M %p")
                save_user(current_user); updated_label.setText(f"Last updated: {current_user['profile_updated']}"); update_avatar(); name_dlg.accept()
            buttons.accepted.connect(save_name); buttons.rejected.connect(name_dlg.reject); name_dlg.exec()

        upload_button.clicked.connect(choose_picture); name_button.clicked.connect(change_name); password_button.clicked.connect(self.reset_password)
        avatar.setCursor(Qt.PointingHandCursor); avatar.mousePressEvent = lambda event: choose_picture()
        update_avatar()
        dlg.exec()

    def reset_password(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Reset Password")
        dlg.setMinimumWidth(430)
        form = QFormLayout(dlg)
        form.setContentsMargins(24, 24, 24, 24)
        form.setSpacing(12)
        new = QLineEdit()
        new.setEchoMode(QLineEdit.Password)
        confirm = QLineEdit()
        confirm.setEchoMode(QLineEdit.Password)
        strength = QLabel("Strength: Enter a password")
        strength.setStyleSheet("font-weight:800; color:#8A98AB;")

        def update_strength(text):
            score = 0
            if len(text) >= 8: score += 1
            if any(c.islower() for c in text) and any(c.isupper() for c in text): score += 1
            if any(c.isdigit() for c in text): score += 1
            if any(not c.isalnum() for c in text): score += 1
            labels = ["Very Weak", "Weak", "Fair", "Strong", "Very Strong"]
            colors = ["#D9534F", "#E67E22", "#D6A800", "#2E9B67", "#1F8A5B"]
            if not text:
                strength.setText("Strength: Enter a password")
                strength.setStyleSheet("font-weight:800; color:#8A98AB;")
            else:
                strength.setText(f"Strength: {labels[score]}")
                strength.setStyleSheet(f"font-weight:900; color:{colors[score]};")

        new.textChanged.connect(update_strength)
        form.addRow("New Password", new)
        form.addRow("Confirm Password", confirm)
        form.addRow("", strength)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.rejected.connect(dlg.reject)

        def save():
            if len(new.text()) < 8:
                QMessageBox.warning(dlg, "Password", "Use at least 8 characters for a stronger password.")
                return
            if new.text() != confirm.text():
                QMessageBox.warning(dlg, "Password", "Passwords do not match.")
                return
            user = load_user()
            user["password"] = hash_password(new.text())
            user["profile_updated"] = datetime.now().strftime("%d %b %Y, %I:%M %p")
            save_user(user)
            dlg.accept()
            QMessageBox.information(self, "Password Updated", "Your password was updated successfully.")

        buttons.accepted.connect(save)
        form.addRow(buttons)
        dlg.exec()

    def _fit_dashboard_table(self):
        """Fit the plans table to the available width without horizontal scrolling.

        The table has many useful fields, so instead of allowing fixed widths to
        overflow on laptop screens, this method uses breakpoint-based column
        visibility and proportional widths.  Long values are elided with ...
        rather than pushing the table outside the window.
        """
        if not hasattr(self, "table"):
            return

        table = self.table
        header = table.horizontalHeader()

        # Use the real viewport width, which is the space available for cells.
        available = max(420, table.viewport().width())
        # Small safety margin prevents a 1-2px rounding overflow.
        available = max(400, available - 4)

        # ID is always internal only.
        table.setColumnHidden(0, True)

        # Keep the important headings fully visible.  Hide only secondary
        # columns when the viewport becomes genuinely narrow.
        narrow = available < 860
        very_narrow = available < 700

        table.setColumnHidden(8, narrow)       # Notes
        table.setColumnHidden(7, very_narrow)  # Payment
        table.setColumnHidden(5, very_narrow)  # Days Left

        visible = [c for c in range(1, 10) if not table.isColumnHidden(c)]

        # Minimum useful widths by semantic importance.
        base = {
            1: 118,  # Plan name
            2: 110,  # Category
            3: 108,  # Renewal date
            4: 82,   # Amount
            5: 82,   # Days left
            6: 96,   # Status
            7: 86,   # Payment
            8: 100,  # Notes
            9: 82,   # Actions
        }

        # At ordinary laptop width use compact proportions.  At wider widths,
        # let the extra room flow primarily into Plan Name and Notes.
        preferred = dict(base)
        if not narrow:
            preferred.update({
                1: 140, 2: 128, 3: 112, 4: 88, 5: 88,
                6: 104, 7: 96, 8: 120, 9: 82
            })

        total_min = sum(base[c] for c in visible)

        # If the available area is tighter than our ideal minimums, scale all
        # columns proportionally. This keeps every visible field inside bounds.
        if total_min > available:
            scale = available / float(total_min)
            widths = {c: max(48, int(base[c] * scale)) for c in visible}
        else:
            total_pref = sum(preferred[c] for c in visible)
            widths = dict(preferred)
            extra = max(0, available - total_pref)

            # Give spare room to the two text-heavy fields first.
            if extra:
                weights = {1: 0.55, 8: 0.45}
                if 8 not in visible:
                    weights = {1: 1.0}
                for c, weight in weights.items():
                    if c in visible:
                        widths[c] += int(extra * weight)

            # Correct rounding so the final sum is never wider than the viewport.
            overflow = sum(widths.values()) - available
            if overflow > 0:
                for c in reversed(visible):
                    reduction = min(max(0, widths[c] - 48), overflow)
                    widths[c] -= reduction
                    overflow -= reduction
                    if overflow <= 0:
                        break

        for c in range(1, 10):
            if table.isColumnHidden(c):
                continue
            header.setSectionResizeMode(c, QHeaderView.Fixed)
            table.setColumnWidth(c, max(48, widths.get(c, 60)))

        # Re-check after widths are applied.  This also handles Windows DPI
        # scaling and monitor changes where Qt may update the viewport later.
        QTimer.singleShot(0, self._finalize_dashboard_table_fit)

    def _finalize_dashboard_table_fit(self):
        if not hasattr(self, "table"):
            return
        table = self.table
        header = table.horizontalHeader()
        available = max(400, table.viewport().width() - 2)
        visible = [c for c in range(1, 10) if not table.isColumnHidden(c)]
        if not visible:
            return
        total = sum(table.columnWidth(c) for c in visible)
        if total > available:
            overflow = total - available
            # Reduce text columns first; keep action/status columns usable.
            for c in (8, 1, 2, 7, 6, 3, 4, 5, 9):
                if c in visible and overflow > 0:
                    floor = 72 if c == 9 else (48 if c in (1, 2, 8) else 44)
                    reduction = min(max(0, table.columnWidth(c) - floor), overflow)
                    table.setColumnWidth(c, table.columnWidth(c) - reduction)
                    overflow -= reduction
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        """Keep the main shell responsive when the user resizes or changes monitors."""
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            width = responsive_sidebar_width(self.width())
            self.sidebar.setFixedWidth(width)

        # Header controls gracefully collapse before the clock becomes cramped.
        if hasattr(self, "clock") and self.width() < 900:
            self.clock.setVisible(False)
        elif hasattr(self, "clock"):
            self.clock.setVisible(bool(self.settings.get("show_clock", True)))

        # The dashboard card area automatically changes its column count.
        if hasattr(self, "dashboard_card_area"):
            self.dashboard_card_area.updateGeometry()

        # Recalculate the table after Qt has completed the main-window resize.
        if hasattr(self, "table"):
            QTimer.singleShot(0, self._fit_dashboard_table)

    def apply_clock_style(self):
        """Apply the selected live-clock appearance and refresh its text."""
        if not hasattr(self, "clock"):
            return

        style = self.settings.get("clock_style", "Classic")
        dark = Theme.dark()

        if dark:
            text = "#F3F6FB"
            soft = "#AAB5C8"
            background = "rgba(23, 30, 43, 205)"
            border = "rgba(255,255,255,55)"
        else:
            text = "#172033"
            soft = "#53627A"
            background = "rgba(255,255,255,120)"
            border = "rgba(255,255,255,185)"

        styles = {
            "Classic": (
                180,
                f"""
                background:{background};
                border:1px solid {border};
                border-radius:18px;
                padding:5px 8px;
                font-size:16px;
                font-weight:800;
                color:{text};
                """
            ),
            "Digital": (
                210,
                f"""
                background:{'#10151F' if dark else '#172033'};
                border:1px solid {'#364154' if dark else '#263449'};
                border-radius:16px;
                padding:10px 16px;
                font-family:Consolas, 'Courier New', monospace;
                font-size:18px;
                font-weight:900;
                letter-spacing:1px;
                color:{'#8FD3FF' if dark else '#F4F8FF'};
                """
            ),
            "Compact": (
                165,
                f"""
                background:{background};
                border:1px solid {border};
                border-radius:22px;
                padding:7px 14px;
                font-size:14px;
                font-weight:800;
                color:{text};
                """
            ),
            "Minimal": (
                150,
                f"""
                background:transparent;
                border:none;
                padding:4px 8px;
                font-size:22px;
                font-weight:900;
                color:{text};
                """
            ),
            "Detailed": (
                245,
                f"""
                background:{background};
                border:1px solid {border};
                border-radius:20px;
                padding:12px 18px;
                font-size:15px;
                font-weight:800;
                color:{text};
                """
            ),
        }

        width, stylesheet = styles.get(style, styles["Classic"])
        self.clock.setMinimumWidth(min(width, max(120, int(self.width() * 0.18))))
        self.clock.setStyleSheet(stylesheet)
        self.update_clock()

    def update_clock(self):
        now = datetime.now()
        style = self.settings.get("clock_style", "Classic") if hasattr(self, "settings") else "Classic"

        if style == "Digital":
            text = now.strftime("%H:%M:%S\n%d %b %Y")
        elif style == "Compact":
            text = now.strftime("%I:%M %p  •  %d %b")
        elif style == "Minimal":
            text = now.strftime("%H:%M:%S")
        elif style == "Detailed":
            text = now.strftime("%A\n%d %B %Y  •  %I:%M:%S %p")
        else:
            text = now.strftime("%A, %d %B %Y\n%I:%M:%S %p")

        self.clock.setText(text)


if __name__ == "__main__":
    # Set the Windows application identity before creating the Qt application so
    # the running window/taskbar uses Renewly's own icon instead of a generic one.
    set_windows_app_identity()
    # Hide harmless Chromium GPU fallback messages (GLES3 -> GLES2) from the console.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--log-level=3")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(get_app_icon())
    install_glass_popups(app)
    app.setFont(QFont("Segoe UI", 10))
    # Keep navigation/tooltips readable on every Windows theme.
    app.setStyleSheet(app.styleSheet() + """
        QToolTip {
            background:#202A3A;
            color:#F8FAFC;
            border:1px solid #4F8CFF;
            border-radius:7px;
            padding:6px 9px;
            font-size:12px;
            font-weight:700;
        }
    """)
    login = LoginWindow()
    login.show()
    sys.exit(app.exec())
