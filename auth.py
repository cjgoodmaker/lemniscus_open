"""Supabase authentication for Lemniscus Bantom — REST API, no SDK."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = "https://oiiqylmaanzwognmjltf.supabase.co"
SUPABASE_KEY = "sb_publishable_DhcUi2fLaHtNXiqNvlBpiQ_2BY1uJTJ"
AUTH_FILE = Path(__file__).parent / ".auth.json"

VERIFY_INTERVAL_HOURS = 24
GRACE_PERIOD_HOURS = 72


def signup(email: str, password: str) -> dict:
    """Create a new account via Supabase."""
    resp = httpx.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        json={"email": email, "password": password},
        headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        # Supabase may require email confirmation
        return {"needs_confirmation": True, "email": email}

    session = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "user_id": data["user"]["id"],
        "email": email,
        "tier": "free",
        "expires_at": None,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_session(session)
    return session


def login(email: str, password: str) -> dict:
    """Sign in with email/password via Supabase."""
    resp = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        json={"email": email, "password": password},
        headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    access_token = data["access_token"]

    # Verify tier via edge function
    tier = "free"
    expires_at = None
    try:
        verify_resp = _verify_token(access_token)
        if verify_resp.get("valid"):
            tier = verify_resp.get("tier", "free")
            expires_at = verify_resp.get("expiresAt")
    except Exception as e:
        logger.warning(f"Tier verification failed (defaulting to free): {e}")

    session = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "user_id": data["user"]["id"],
        "email": email,
        "tier": tier,
        "expires_at": expires_at,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_session(session)
    return session


def logout() -> None:
    """Remove stored auth session."""
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()


def check_auth() -> dict | None:
    """Load and validate the stored session.

    Returns the session dict if valid, None if auth is needed.
    Re-verifies with Supabase if >24h since last check.
    Allows 72h offline grace period.
    """
    session = _load_session()
    if session is None:
        return None

    verified_at = session.get("verified_at")
    if not verified_at:
        return None

    try:
        last_verified = datetime.fromisoformat(verified_at)
    except (ValueError, TypeError):
        return None

    hours_since = (datetime.now(timezone.utc) - last_verified).total_seconds() / 3600

    # Within check interval — use cached session
    if hours_since < VERIFY_INTERVAL_HOURS:
        return session

    # Try to re-verify online
    try:
        verify_resp = _verify_token(session["access_token"])
        if verify_resp.get("valid"):
            session["tier"] = verify_resp.get("tier", session.get("tier", "free"))
            session["expires_at"] = verify_resp.get("expiresAt")
            session["verified_at"] = datetime.now(timezone.utc).isoformat()
            _save_session(session)
            return session
        else:
            logger.warning("Auth verification returned invalid")
            return None
    except Exception as e:
        # Offline — check grace period
        if hours_since < GRACE_PERIOD_HOURS:
            logger.info(f"Offline but within grace period ({int(hours_since)}h): {e}")
            return session
        logger.warning(f"Auth expired (offline for {int(hours_since)}h): {e}")
        return None


def _verify_token(access_token: str) -> dict:
    """Call the verify-auth Supabase Edge Function."""
    resp = httpx.post(
        f"{SUPABASE_URL}/functions/v1/verify-auth",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {access_token}",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _save_session(session: dict) -> None:
    """Write session to .auth.json."""
    AUTH_FILE.write_text(json.dumps(session, indent=2, default=str))


def _load_session() -> dict | None:
    """Read session from .auth.json."""
    if not AUTH_FILE.exists():
        return None
    try:
        return json.loads(AUTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
