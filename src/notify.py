"""ntfy でiPhoneへ通知する。ボタンを押すと GitHub の repository_dispatch が飛ぶ"""
from __future__ import annotations

import json
import os
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "KYumac/timescar-auto-reserve")


def _dispatch_action(label: str, event: str, payload: dict) -> dict:
    return {
        "action": "http",
        "label": label,
        "url": f"https://api.github.com/repos/{REPO}/dispatches",
        "method": "POST",
        "headers": {
            "Authorization": f"Bearer {os.environ['GH_PAT']}",
            "Accept": "application/vnd.github+json",
        },
        "body": json.dumps({"event_type": event, "client_payload": payload}),
        "clear": True,
    }


def send(title: str, message: str, actions: list[dict] | None = None,
         priority: int = 3, tags: list[str] | None = None, click: str | None = None) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[ntfy未設定] {title}\n{message}")
        return
    body = {"topic": topic, "title": title, "message": message, "priority": priority}
    if actions:
        body["actions"] = actions
    if tags:
        body["tags"] = tags
    if click:
        body["click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        res.read()


def keep_or_cancel_buttons(number: str) -> list[dict]:
    if not os.environ.get("GH_PAT"):
        return []
    return [
        _dispatch_action("使う", "keep", {"number": number}),
        _dispatch_action("キャンセル", "cancel", {"number": number}),
    ]
