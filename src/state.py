"""予約の状態（どれを自動で取ったか・「使う」が押されたか）を state.json に保存する"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "state.json"


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    return {"reservations": {}}


def save(state: dict) -> None:
    PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add(state: dict, number: str, **info) -> None:
    state["reservations"][number] = {"keep": False, **info}


def set_keep(state: dict, number: str, keep: bool = True) -> bool:
    r = state["reservations"].get(number)
    if not r:
        return False
    r["keep"] = keep
    return True


def remove(state: dict, number: str) -> None:
    state["reservations"].pop(number, None)
