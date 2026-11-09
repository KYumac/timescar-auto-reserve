"""ntfy でiPhoneへ通知する

iPhoneからの操作は「コマンド用トピック」(NTFY_TOPIC + "-cmd") にメッセージを送る形で受け取る。
PCのタスクスケジューラが5分おきにコマンド用トピックを読みに行って処理する。
"""
from __future__ import annotations

import json
import os
import urllib.request

import settings

NTFY = "https://ntfy.sh"
PANEL = "https://kyumac.github.io/timescar-auto-reserve/"


def topic() -> str | None:
    return settings.ntfy_topic()


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


REMOTE_DESKTOP = "https://remotedesktop.google.com/access"


def login_buttons() -> list[dict]:
    acts = [{"action": "view", "label": "リモートデスクトップを開く", "url": REMOTE_DESKTOP}]
    if cmd_topic():
        acts.insert(0, _cmd_action("PCにログイン画面を開く", "login"))
    return acts


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


def publish_state(state: dict) -> None:
    """操作パネルが予約一覧を読めるよう、状態を状態用トピックに配信する"""
    t = topic()
    if not t:
        return
    view = {"reservations": state.get("reservations", {})}
    req = urllib.request.Request(
        f"{NTFY}/{t}-state", data=json.dumps(view, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8", "Cache": "yes"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            res.read()
    except Exception as e:  # 一覧の配信に失敗しても本体の処理は止めない
        print(f"[state] 配信失敗: {e}")
