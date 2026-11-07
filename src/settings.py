"""PCローカルの設定（settings.json）と認証情報（Windowsの資格情報マネージャー）"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "settings.json"
SERVICE = "timescar-auto-reserve"


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    return {}


def ntfy_topic() -> str | None:
    return os.environ.get("NTFY_TOPIC") or load().get("ntfy_topic")


def credentials() -> tuple[str, str]:
    """環境変数 → Windowsの資格情報マネージャー の順に探す"""
    mid, pw = os.environ.get("TIMES_ID"), os.environ.get("TIMES_PW")
    if mid and pw:
        return mid, pw
    import keyring
    mid = keyring.get_password(SERVICE, "member_id")
    pw = keyring.get_password(SERVICE, "password")
    if not mid or not pw:
        raise RuntimeError("会員番号・パスワードが未登録です。setup.bat をもう一度実行してください")
    return mid, pw


def save_credentials(mid: str, pw: str) -> None:
    import keyring
    keyring.set_password(SERVICE, "member_id", mid)
    keyring.set_password(SERVICE, "password", pw)
