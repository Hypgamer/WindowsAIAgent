"""Sign in with ChatGPT support for a local/open-source Windows client.

This module implements the public-client OAuth + PKCE flow documented for
ChatGPT plan usage in open-source/local apps. Tokens are encrypted at rest with
Windows DPAPI and are never written to .env or source control.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
from typing import Callable
import secrets
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
import requests

from .config import APP_NAME

AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
OIDC_CONFIG_URL = "https://auth.openai.com/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "WindowsAISupportAgent"
    path.mkdir(parents=True, exist_ok=True)
    return path


HOST_FILE = lambda: _data_dir() / "chatgpt_host.json"
PROFILE_FILE = lambda: _data_dir() / "chatgpt_profile.dat"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("ChatGPT credential storage is currently implemented for Windows only.")
    in_blob, in_buf = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "Windows AI Support Agent",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _ = in_buf


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("ChatGPT credential storage is currently implemented for Windows only.")
    in_blob, in_buf = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _ = in_buf


def _save_profile(profile: dict) -> None:
    payload = json.dumps(profile, separators=(",", ":")).encode("utf-8")
    encrypted = _dpapi_protect(payload)
    tmp = PROFILE_FILE().with_suffix(".tmp")
    tmp.write_bytes(encrypted)
    tmp.replace(PROFILE_FILE())


def load_profile() -> dict | None:
    path = PROFILE_FILE()
    if not path.exists():
        return None
    try:
        return json.loads(_dpapi_unprotect(path.read_bytes()).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Saved ChatGPT sign-in could not be opened: {exc}") from exc


def has_profile() -> bool:
    try:
        profile = load_profile()
        return bool(profile and profile.get("refresh_token"))
    except Exception:
        return False


def _get_host_id() -> str:
    path = HOST_FILE()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("ext_agent_host_id"):
            return data["ext_agent_host_id"]
    host_id = f"urn:uuid:{uuid.uuid4()}"
    path.write_text(json.dumps({"ext_agent_host_id": host_id}, indent=2), encoding="utf-8")
    return host_id


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _oidc_config() -> dict:
    response = requests.get(OIDC_CONFIG_URL, timeout=20)
    response.raise_for_status()
    return response.json()


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict | None = None
    event = threading.Event()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        self.__class__.result = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        body = (
            "<html><body style='font-family:Segoe UI,Arial;padding:2rem'>"
            "<h2>Sign-in complete</h2>"
            "<p>You can close this browser tab and return to Windows AI Support Agent.</p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.__class__.event.set()

    def log_message(self, format, *args):
        return


def _validate_id_token(id_token: str, client_id: str, nonce: str) -> dict:
    config = _oidc_config()
    issuer = config.get("issuer", "https://auth.openai.com")
    jwks_uri = config.get("jwks_uri")
    if not jwks_uri:
        raise RuntimeError("OpenAI OIDC discovery did not provide jwks_uri.")
    signing_key = jwt.PyJWKClient(jwks_uri, timeout=15).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=client_id,
        issuer=issuer,
    )
    if claims.get("nonce") != nonce:
        raise RuntimeError("ChatGPT sign-in nonce validation failed.")
    return claims


def sign_in(progress: Callable[[str], None] | None = None) -> dict:
    """Open the system browser, complete Sign in with ChatGPT, and save the profile.

    ``progress`` is optional and is used by the GUI to show the user exactly
    which OAuth stage is active. It is safe to call this function from a worker
    thread.
    """
    if os.name != "nt":
        raise RuntimeError("This prototype's ChatGPT sign-in storage is Windows-only.")

    def report(message: str) -> None:
        print(message)
        if progress:
            try:
                progress(message)
            except Exception:
                pass

    existing = load_profile()
    host_id = _get_host_id()
    requested_client_id = existing.get("client_id") if existing else DYNAMIC_CLIENT_ID

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())

    _CallbackHandler.result = None
    _CallbackHandler.event.clear()
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"

    params = {
        "client_id": requested_client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if existing:
        if existing.get("id_token"):
            params["id_token_hint"] = existing["id_token"]
        if existing.get("email"):
            params["login_hint"] = existing["email"]
    else:
        params["agent_name_hint"] = APP_NAME

    # Keep the listener alive for the whole authorization window. Browsers may
    # make unrelated localhost requests (for example favicon.ico); those must
    # not end the authorization attempt.
    listener = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    listener.start()

    url = AUTH_URL + "?" + urlencode(params)
    report("Opening your browser for Continue with ChatGPT...")
    if not webbrowser.open(url):
        server.shutdown()
        server.server_close()
        raise RuntimeError("Could not open the default browser for ChatGPT sign-in.")

    try:
        report("Waiting for the browser authorization callback...")
        if not _CallbackHandler.event.wait(timeout=180):
            raise RuntimeError("ChatGPT sign-in timed out before the browser callback was received.")
    finally:
        server.shutdown()
        server.server_close()
        listener.join(timeout=2)

    report("Browser callback received. Finishing ChatGPT sign-in...")
    result = _CallbackHandler.result
    if not result:
        raise RuntimeError("ChatGPT sign-in callback was received but contained no authorization result.")
    if result.get("state") != state:
        raise RuntimeError("ChatGPT sign-in state validation failed.")
    if result.get("error"):
        description = result.get("error_description") or result.get("error")
        raise RuntimeError(f"ChatGPT sign-in was not completed: {description}")
    code = result.get("code")
    if not code:
        raise RuntimeError("ChatGPT sign-in callback did not include an authorization code.")

    callback_client_id = result.get("client_id")
    if existing:
        issued_client_id = existing["client_id"]
        if callback_client_id and callback_client_id != issued_client_id:
            raise RuntimeError("ChatGPT returned a different client registration than expected.")
    else:
        issued_client_id = callback_client_id
        if not issued_client_id or issued_client_id == DYNAMIC_CLIENT_ID:
            raise RuntimeError("ChatGPT dynamic registration did not return an issued client_id.")

    report("Authorization received. Exchanging it for local credentials...")
    token_response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": issued_client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        },
        timeout=30,
    )
    if not token_response.ok:
        request_id = token_response.headers.get("x-request-id", "")
        detail = token_response.text[:1000]
        suffix = f" (request id: {request_id})" if request_id else ""
        raise RuntimeError(f"ChatGPT token exchange failed with HTTP {token_response.status_code}{suffix}: {detail}")
    tokens = token_response.json()

    id_token = tokens.get("id_token")
    if not id_token:
        raise RuntimeError("ChatGPT token response did not include an ID token.")
    report("Credentials received. Validating your ChatGPT identity...")
    claims = _validate_id_token(id_token, issued_client_id, nonce)

    if existing and claims.get("sub") != existing.get("subject"):
        raise RuntimeError("The signed-in ChatGPT account does not match the saved registration.")

    scope_text = tokens.get("scope") or result.get("scope") or ""
    scopes = sorted(set(scope_text.split()))
    if "chatgpt.tokens.use.direct" not in scopes:
        raise RuntimeError(
            "ChatGPT sign-in succeeded, but permission to use the ChatGPT plan for this app was not granted. "
            "Choose the API-key option or authorize plan usage when available for this account."
        )

    expires_in = int(tokens.get("expires_in", 3600))
    profile = {
        "email": claims.get("email", ""),
        "issuer": claims.get("iss", "https://auth.openai.com"),
        "subject": claims.get("sub", ""),
        "client_id": issued_client_id,
        "ext_agent_host_id": host_id,
        "id_token": id_token,
        "access_token": tokens.get("access_token", ""),
        "refresh_token": tokens.get("refresh_token", ""),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": expires_in,
        "expires_at": time.time() + expires_in,
        "scopes": scopes,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    if not profile["access_token"] or not profile["refresh_token"]:
        raise RuntimeError("ChatGPT sign-in did not return renewable credentials.")
    _save_profile(profile)
    report("ChatGPT connection established.")
    return profile

def _refresh(profile: dict) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": profile["client_id"],
            "refresh_token": profile["refresh_token"],
            "resource": RESOURCE,
        },
        timeout=30,
    )
    response.raise_for_status()
    tokens = response.json()

    profile = dict(profile)
    profile["access_token"] = tokens.get("access_token", profile.get("access_token", ""))
    profile["refresh_token"] = tokens.get("refresh_token", profile.get("refresh_token", ""))
    if tokens.get("id_token"):
        profile["id_token"] = tokens["id_token"]
    if tokens.get("scope"):
        profile["scopes"] = sorted(set(tokens["scope"].split()))
    expires_in = int(tokens.get("expires_in", 3600))
    profile["expires_in"] = expires_in
    profile["expires_at"] = time.time() + expires_in
    profile["saved_at"] = datetime.now(timezone.utc).isoformat()
    _save_profile(profile)
    return profile


def get_access_token() -> str:
    profile = load_profile()
    if not profile:
        raise RuntimeError("No ChatGPT sign-in is saved. Run: python -m app.chatgpt_login")
    if "chatgpt.tokens.use.direct" not in profile.get("scopes", []):
        raise RuntimeError("Saved ChatGPT connection does not have plan-usage permission.")
    # Refresh two minutes early to avoid expiry mid-request.
    if float(profile.get("expires_at", 0)) <= time.time() + 120:
        profile = _refresh(profile)
    return profile["access_token"]
