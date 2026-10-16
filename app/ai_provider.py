from __future__ import annotations

from openai import OpenAI
import requests

from .chatgpt_auth import get_access_token, has_profile
from . import config


class AIProvider:
    def __init__(self, kind: str, client: OpenAI, model: str):
        self.kind = kind
        self.client = client
        self.model = model


def _valid_api_key() -> bool:
    key = config.OPENAI_API_KEY
    return bool(key and key.lower() not in {"replace-me", "your-api-key", "your-api-key-here"})


def _chatgpt_model(access_token: str) -> str:
    try:
        response = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.SSLError as exc:
        raise RuntimeError(
            "HTTPS certificate validation failed while contacting api.openai.com. "
            "This app is configured to use the Windows system certificate store. "
            "Confirm the corporate HTTPS-inspection CA is trusted by Windows and "
            "that 'truststore' is installed in this virtual environment. "
            f"Original error: {exc}"
        ) from exc
    body = response.json()
    models = body.get("models", body.get("data", []))
    slugs = []
    for model in models:
        if model.get("visibility") not in (None, "list"):
            continue
        slug = model.get("slug") or model.get("id")
        if slug:
            slugs.append(slug)
    if not slugs:
        raise RuntimeError("No ChatGPT-plan models are available for this signed-in account.")
    if config.OPENAI_MODEL in slugs:
        return config.OPENAI_MODEL
    return slugs[0]


def get_provider() -> AIProvider:
    config.reload_settings()
    provider = config.AI_PROVIDER
    if provider not in {"auto", "api", "chatgpt"}:
        raise RuntimeError("AI_PROVIDER must be one of: auto, api, chatgpt")

    if provider in {"auto", "api"} and _valid_api_key():
        return AIProvider("api", OpenAI(api_key=config.OPENAI_API_KEY), config.OPENAI_MODEL)

    if provider == "api":
        raise RuntimeError("AI_PROVIDER=api but OPENAI_API_KEY is not configured.")

    if provider in {"auto", "chatgpt"} and has_profile():
        token = get_access_token()
        model = _chatgpt_model(token)
        client = OpenAI(api_key=token, base_url="https://api.openai.com/v1", max_retries=0)
        return AIProvider("chatgpt", client, model)

    if provider == "chatgpt":
        raise RuntimeError("AI_PROVIDER=chatgpt but no ChatGPT sign-in is saved.")

    raise RuntimeError("No AI provider is configured. Choose ChatGPT sign-in or an OpenAI API key.")
