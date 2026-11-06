"""ntfy でiPhoneへ通知する

iPhoneからの操作は「コマンド用トピック」(NTFY_TOPIC + "-cmd") にメッセージを送る形で受け取る。
GitHubのトークンは不要。GitHub Actions が5分おきにコマンド用トピックを読みに行って処理する。
"""
from __future__ import annotations

import json
import os
import urllib.request

NTFY = "https://ntfy.sh"
PANEL = "https://kyumac.github.io/timescar-auto-reserve/"


def topic() -> str | None:
    return os.environ.get("NTFY_TOPIC")


def cmd_topic() -> str | None:
    t = topic()
    return f"{t}-cmd" if t else None


def panel_url() -> str:
    """操作パネルのURL（初回に開くとコマンド用トピックが端末に保存される）"""
    t = cmd_topic()
    return f"{PANEL}#t={t}" if t else PANEL


def send(title: str, message: str, actions: list[dict] | None = None,
         priority: int = 3, tags: list[str] | None = None, click: str | None = None) -> None:
    t = topic()
    if not t:
        print(f"[ntfy未設定] {title}\n{message}")
        return
    body = {"topic": t, "title": title, "message": message, "priority": priority,
            "click": click or panel_url()}
    if actions:
        body["actions"] = actions
    if tags:
        body["tags"] = tags
    req = urllib.request.Request(
        f"{NTFY}/", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        res.read()


def _cmd_action(label: str, command: str) -> dict:
    return {
        "action": "http",
        "label": label,
        "url": f"{NTFY}/{cmd_topic()}",
        "method": "POST",
        "body": command,
        "clear": True,
    }


def keep_or_cancel_buttons(number: str) -> list[dict]:
    if not cmd_topic():
        return []
    return [_cmd_action("使う", f"keep {number}"), _cmd_action("キャンセル", f"cancel {number}")]


def fetch_commands(since: str | None) -> list[dict]:
    """コマンド用トピックに届いたメッセージを古い順に返す"""
    t = cmd_topic()
    if not t:
        return []
    url = f"{NTFY}/{t}/json?poll=1&since={since or '24h'}"
    with urllib.request.urlopen(url, timeout=20) as res:
        lines = res.read().decode("utf-8").splitlines()
    msgs = [json.loads(x) for x in lines if x.strip()]
    return [m for m in msgs if m.get("event") == "message"]
