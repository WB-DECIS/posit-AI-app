"""Small chat app for Posit Connect, backed by mAI Factory through the `mai-posit` integration."""

import base64
import json
import os
from datetime import datetime, timezone

import httpx
from openai import AsyncOpenAI
from shiny import App, reactive, render, ui

BASE_URL = os.environ.get(
    "MAI_BASE_URL", "https://azapimdev.worldbank.org/maifactory/openai"
).rstrip("/") + "/"
MODEL = os.environ.get("MAI_MODEL", "gpt-6-luna")
TEAM_NAME = os.environ.get("MAI_TEAM_NAME", "posit-ai")
SYSTEM_PROMPT = os.environ.get("MAI_SYSTEM_PROMPT", "You are a helpful assistant.")


def session_token() -> str:
    # Connect injects the content session token as a file or an env var.
    token_file = os.environ.get("CONNECT_CONTENT_SESSION_TOKEN_FILE")
    if token_file and os.path.exists(token_file):
        with open(token_file) as f:
            return f.read().strip()
    return os.environ.get("CONNECT_CONTENT_SESSION_TOKEN", "")


async def get_token() -> str:
    # Local development: set MAI_TOKEN to skip the Connect integration.
    token = os.environ.get("MAI_TOKEN")
    if token:
        return token
    # On Connect, exchange the content session token for an Azure access token
    # from the service account integration attached to this content.
    server = os.environ.get("CONNECT_SERVER", "").rstrip("/")
    api_key = os.environ.get("CONNECT_API_KEY", "")
    subject = session_token()
    missing = [
        name
        for name, value in [
            ("CONNECT_SERVER", server),
            ("CONNECT_API_KEY", api_key),
            ("CONNECT_CONTENT_SESSION_TOKEN", subject),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Connect did not provide: {', '.join(missing)}")
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"{server}/__api__/v1/oauth/integrations/credentials",
            headers={"Authorization": f"Key {api_key}"},
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token_type": "urn:posit:connect:content-session-token",
                "subject_token": subject,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Connect token exchange failed (HTTP {resp.status_code}); "
            "is mai-posit attached to this content?"
        )
    access_token = resp.json().get("access_token")
    if not access_token:
        raise RuntimeError("Connect token exchange returned no access_token")
    return access_token


def describe_token(token: str) -> str:
    # Show who the token is for and when it expires, never the token itself.
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        return "Token received (could not read its details)."
    expires = datetime.fromtimestamp(claims.get("exp", 0), timezone.utc)
    return (
        f"Token received. Audience: {claims.get('aud')} | "
        f"App: {claims.get('appid') or claims.get('azp')} | "
        f"Expires: {expires:%Y-%m-%d %H:%M} UTC"
    )


async def make_client() -> AsyncOpenAI:
    # The gateway takes the Azure token as a plain bearer token.
    return AsyncOpenAI(
        base_url=BASE_URL,
        api_key=await get_token(),
        default_headers={"x-source-type": "interactive", "x-team-name": TEAM_NAME},
    )


app_ui = ui.page_fillable(
    ui.panel_title("mAI Chat"),
    ui.layout_columns(
        ui.input_action_button("check_token", "Check token exchange"),
        ui.output_text("token_status"),
        col_widths=(3, 9),
        fill=False,
    ),
    ui.chat_ui("chat", placeholder="Ask mAI something..."),
    fillable_mobile=True,
)


def server(input, output, session):
    chat = ui.Chat(id="chat")
    history = []

    @render.text
    @reactive.event(input.check_token)
    async def token_status():
        try:
            status = describe_token(await get_token())
        except Exception as e:
            return f"Token exchange failed: {type(e).__name__}: {e}"
        status += f"\nUsing model: {MODEL} | URL: {BASE_URL} | Team: {TEAM_NAME}"
        status += "\nToken check succeeded. Model access has not been tested."
        return status

    @chat.on_user_submit
    async def _(user_input: str):
        history.append({"role": "user", "content": user_input})
        try:
            async with await make_client() as client:
                resp = await client.responses.create(
                    model=MODEL, instructions=SYSTEM_PROMPT, input=history
                )
            answer = resp.output_text or "\n".join(
                content.text
                for item in resp.output
                for content in getattr(item, "content", [])
                if getattr(content, "type", None) == "output_text"
            )
        except Exception as e:
            history.pop()
            await chat.append_message(
                f"**Error calling mAI:** `{type(e).__name__}: {e}`"
            )
            return
        history.append({"role": "assistant", "content": answer})
        await chat.append_message(answer)


app = App(app_ui, server)