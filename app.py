"""Small chat app for Posit Connect, backed by mAI Factory through the `mai-posit` integration."""

import os

from openai import AsyncOpenAI
from posit.connect import Client
from shiny import App, ui

BASE_URL = os.environ.get(
    "MAI_BASE_URL", "https://azapimdev.worldbank.org/maifactory/openai"
)
MODEL = os.environ.get("MAI_MODEL", "gpt-4o")
TEAM_NAME = os.environ.get("MAI_TEAM_NAME", "posit-ai")
SYSTEM_PROMPT = os.environ.get("MAI_SYSTEM_PROMPT", "You are a helpful assistant.")


def get_token() -> str:
    # Local development: set MAI_TOKEN to skip the Connect integration.
    token = os.environ.get("MAI_TOKEN")
    if token:
        return token
    # On Connect, the service account integration attached to this content
    # hands back an Azure access token. Connect injects the session token.
    credentials = Client().oauth.get_content_credentials()
    if not credentials or not credentials.get("access_token"):
        raise RuntimeError("No token returned; is mai-posit attached to this content?")
    return credentials["access_token"]


def make_client() -> AsyncOpenAI:
    # The gateway takes the Azure token as a plain bearer token.
    return AsyncOpenAI(
        base_url=BASE_URL,
        api_key=get_token(),
        default_headers={"x-source-type": "interactive", "x-team-name": TEAM_NAME},
    )


app_ui = ui.page_fillable(
    ui.panel_title("mAI Chat"),
    ui.chat_ui("chat", placeholder="Ask mAI something..."),
    fillable_mobile=True,
)


def server(input, output, session):
    chat = ui.Chat(id="chat")
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    @chat.on_user_submit
    async def _(user_input: str):
        history.append({"role": "user", "content": user_input})
        try:
            resp = await make_client().chat.completions.create(
                model=MODEL, messages=history
            )
            answer = resp.choices[0].message.content or ""
        except Exception as e:
            history.pop()
            await chat.append_message(
                f"**Error calling mAI:** `{type(e).__name__}: {e}`"
            )
            return
        history.append({"role": "assistant", "content": answer})
        await chat.append_message(answer)


app = App(app_ui, server)
