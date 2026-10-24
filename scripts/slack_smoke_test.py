"""Local Slack connection check: mention the bot to receive a fixed reply."""

import os
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


REPLY = "Score Copilot is connected. This is a connection test; no LLM was called."


def register_listener(app, allowed_channel):
    @app.event("app_mention")
    def reply_to_mention(event, say):
        if event.get("channel") != allowed_channel or event.get("bot_id"):
            return
        say(text=REPLY, thread_ts=event.get("thread_ts") or event["ts"])


def main():
    # Resolve .env from this file so PyCharm's working directory does not matter.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    names = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_ALLOWED_CHANNEL_ID")
    settings = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise SystemExit("Missing configuration: " + ", ".join(missing))
    for name, prefix in (("SLACK_BOT_TOKEN", "xoxb-"), ("SLACK_APP_TOKEN", "xapp-")):
        if not settings[name].startswith(prefix):
            raise SystemExit(f"{name} must start with {prefix}")

    app = App(token=settings["SLACK_BOT_TOKEN"])
    register_listener(app, settings["SLACK_ALLOWED_CHANNEL_ID"])
    print("Starting Slack connection test. Keep this process running; Ctrl+C stops it.", flush=True)
    SocketModeHandler(app, settings["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
