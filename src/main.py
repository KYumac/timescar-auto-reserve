"""タイムズカー自動予約のエントリポイント

  python src/main.py reserve [--date YYYY-MM-DD --start HH:MM --end HH:MM] [--wait-open]
  python src/main.py remind          # 前日夜：明日の予約に「使う/キャンセル」ボタン付き通知
  python src/main.py auto-cancel     # 当日朝：「使う」が押されていない自動予約を取消
  python src/main.py keep <予約番号>
  python src/main.py cancel <予約番号>
  python src/main.py status          # 現在の予約一覧を通知
  python src/main.py test            # ログインと一覧取得だけ試す
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402
import state as st  # noqa: E402
import timescar as tc  # noqa: E402

JST = timezone(timedelta(hours=9))
CONFIG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
WD = "月火水木金土日"


def now() -> datetime:
    return datetime.now(JST)


def hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def fmt_day(d: date) -> str:
    return f"{d.month}/{d.day}（{WD[d.weekday()]}）"


class Session:
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=True)
        ctx = self.browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo")
        self.page = ctx.new_page()
        self.page.set_default_timeout(30000)
        tc.login(self.page, os.environ["TIMES_ID"], os.environ["TIMES_PW"])
        return self.page

    def __exit__(self, *exc):
        self.browser.close()
        self._pw.stop()


# ---------------------------------------------------------------- 予約

def candidates():
    s = CONFIG["stations"]
    for tier in ("tier1", "tier2", "tier3"):
        for station in s.get(tier, []):
            yield tier, station


def try_reserve(page, d: date, start: tuple[int, int], end: tuple[int, int]):
    anshin = CONFIG["reserve"]["anshin_hosho"]
    for tier, station in candidates():
        try:
            tc._open_input(page, station["code"])
            cars = tc.list_cars(page)
            slots = tc.vacancy(page, d, start[0])
        except tc.NotOpenYet:
            raise
        except tc.TimesError as e:
            print(f"[skip] {station['name']}: {e}")
            continue
        for car_id, car_name in cars:
            if not tc.is_free(slots.get(car_id, []), *start, *end, table_start_hour=start[0]):
                continue
            number = tc.reserve(page, station["code"], car_id, d, start, end, anshin,
                                dry_run=bool(os.environ.get("DRY_RUN")))
            if number:
                return number, station, car_name
        print(f"[full] {station['name']}")
    return None, None, None


def cmd_reserve(args) -> None:
    cfg = CONFIG["reserve"]
    if args.date:
        d = date.fromisoformat(args.date)
    else:
        # 23時台に起動したときは「日付が変わった後の14日先」を狙う
        base = now() + timedelta(hours=1) if now().hour == 23 else now()
        d = base.date() + timedelta(days=cfg["days_ahead"])
        if d.weekday() not in cfg["weekdays"]:
            print(f"{d} は対象曜日ではないので何もしません")
            return
    start = hm(args.start or cfg["start"])
    end = hm(args.end or cfg["end"])
    auto = not args.date

    state = st.load()
    if any(r["date"] == d.isoformat() for r in state["reservations"].values()):
        print(f"{d} はすでに予約済みです")
        return

    with Session() as page:
        if args.wait_open:
            # 0:00ちょうどに受付が開くまで待つ（締切は 0:00 + open_wait_minutes）
            midnight = datetime.combine(d - timedelta(days=cfg["days_ahead"]), datetime.min.time(), JST)
            deadline = (midnight + timedelta(minutes=cfg["open_wait_minutes"])).timestamp()
            while time.time() < midnight.timestamp() - 20:
                time.sleep(min(30, midnight.timestamp() - 20 - time.time()))
            tc.login(page, os.environ["TIMES_ID"], os.environ["TIMES_PW"])  # 待機中の切断に備えて再確認
            tc.wait_until_open(page, CONFIG["stations"]["tier1"][0]["code"], d, deadline)
        number, station, car = None, None, None
        for attempt in range(3):
            try:
                number, station, car = try_reserve(page, d, start, end)
                break
            except tc.NotOpenYet:
                time.sleep(10)

    label = f"{fmt_day(d)} {start[0]}:{start[1]:02d}〜{end[0]}:{end[1]:02d}"
    if not number:
        notify.send("タイムズカー：予約できませんでした", f"{label}\n候補のステーションがすべて埋まっていました。",
                    tags=["warning"])
        return
    if number == "DRY-RUN":
        print(f"[dry-run] {label} {station['name']} {car}")
        return

    st.add(state, number, date=d.isoformat(), start=f"{start[0]:02d}:{start[1]:02d}",
           end=f"{end[0]:02d}:{end[1]:02d}", station=station["name"], car=car, auto=auto)
    if not auto:
        st.set_keep(state, number, True)  # 個別に頼んだ予約は自動キャンセルしない
    st.save(state)
    msg = f"{label}\n{station['name']}\n{car}\n予約番号 {number}"
    if auto:
        msg += "\n\n前日21時ごろに使うかどうか確認します。"
    notify.send("タイムズカー：予約しました", msg, tags=["car"])


# ---------------------------------------------------------------- 前日リマインド・自動キャンセル

def cmd_remind(args) -> None:
    tomorrow = (now() + timedelta(days=1)).date().isoformat()
    state = st.load()
    for number, r in state["reservations"].items():
        if r["date"] != tomorrow or not r.get("auto") or r.get("keep"):
            continue
        d = date.fromisoformat(r["date"])
        notify.send(
            f"明日 {fmt_day(d)} の車、使いますか？",
            f"{r['start']}〜{r['end']} {r['station']}（{r['car']}）\n"
            f"「使う」を押さないと、当日朝8時ごろに自動でキャンセルします。",
            actions=notify.keep_or_cancel_buttons(number),
            priority=4, tags=["car"],
        )


def cmd_auto_cancel(args) -> None:
    run_commands()  # 直前に押された「使う」を先に反映する
    today = now().date().isoformat()
    state = st.load()
    targets = [(n, r) for n, r in state["reservations"].items()
               if r["date"] == today and r.get("auto") and not r.get("keep")]
    if not targets:
        return
    with Session() as page:
        live = {x.number: x for x in tc.list_reservations(page)}
        for number, r in targets:
            start = datetime.combine(date.fromisoformat(r["date"]), datetime.strptime(r["start"], "%H:%M").time(), JST)
            if now() > start - timedelta(minutes=3):
                notify.send("タイムズカー：自動キャンセルが間に合いませんでした",
                            f"{r['start']}開始の予約（{r['station']}）。アプリから取消してください。",
                            priority=5, tags=["rotating_light"])
                continue
            x = live.get(number)
            if x and x.rid:
                tc.cancel(page, x.rid)
            st.remove(state, number)
            notify.send("タイムズカー：キャンセルしました",
                        f"{fmt_day(date.fromisoformat(r['date']))} {r['start']}〜{r['end']} {r['station']}",
                        tags=["wastebasket"])
    st.save(state)


# ---------------------------------------------------------------- iPhoneからの操作

def cmd_keep(args) -> None:
    state = st.load()
    if st.set_keep(state, args.number, True):
        st.save(state)
        r = state["reservations"][args.number]
        notify.send("タイムズカー：使う予定で確定しました",
                    f"{fmt_day(date.fromisoformat(r['date']))} {r['start']}〜{r['end']} {r['station']}\n"
                    "時間を短くするならアプリで変更すると料金が下がります。", tags=["white_check_mark"])
    else:
        notify.send("タイムズカー：予約が見つかりません", f"予約番号 {args.number}")


def cmd_cancel(args) -> None:
    state = st.load()
    r = state["reservations"].get(args.number)
    with Session() as page:
        live = {x.number: x for x in tc.list_reservations(page)}
        x = live.get(args.number)
        if x and x.rid:
            tc.cancel(page, x.rid)
    st.remove(state, args.number)
    st.save(state)
    where = f"{fmt_day(date.fromisoformat(r['date']))} {r['start']}〜{r['end']} {r['station']}" if r else args.number
    notify.send("タイムズカー：キャンセルしました", where, tags=["wastebasket"])


def cmd_status(args) -> None:
    state = st.load()
    with Session() as page:
        live = tc.list_reservations(page)
    live_numbers = {x.number for x in live}
    # 手動キャンセル済みなど、サイト上に無いものは状態から消す
    for n in list(state["reservations"]):
        if n not in live_numbers:
            st.remove(state, n)
    st.save(state)
    if not live:
        notify.send("タイムズカー：予約はありません", "現在、予約は入っていません。")
        return
    lines = []
    for x in sorted(live, key=lambda r: r.start):
        r = state["reservations"].get(x.number, {})
        mark = "使う" if r.get("keep") else ("自動キャンセル対象" if r.get("auto") else "手動予約")
        end = x.end.strftime("%H:%M") if x.end else ""
        lines.append(f"{fmt_day(x.start.date())} {x.start:%H:%M}〜{end} "
                     f"{r.get('station', x.station)}［{mark}］")
    notify.send("タイムズカー：予約一覧", "\n".join(lines))


def cmd_test(args) -> None:
    with Session() as page:
        live = tc.list_reservations(page)
    notify.send("タイムズカー：接続テスト成功",
                f"ログインできました。現在の予約 {len(live)} 件。\n"
                "この通知をタップすると操作パネルが開きます。ホーム画面に追加しておくと便利です。",
                tags=["white_check_mark"])


# ---------------------------------------------------------------- iPhoneからのコマンド（ntfy経由）

def pending_commands() -> list[dict]:
    state = st.load()
    return notify.fetch_commands(state.get("cmd_since"))


def run_commands() -> int:
    """コマンド用トピックに届いた操作を順に実行する。実行件数を返す"""
    from types import SimpleNamespace as NS
    msgs = pending_commands()
    for m in msgs:
        parts = (m.get("message") or "").split()
        print(f"[command] {parts}")
        try:
            if len(parts) == 2 and parts[0] in ("keep", "cancel"):
                (cmd_keep if parts[0] == "keep" else cmd_cancel)(NS(number=parts[1]))
            elif len(parts) == 4 and parts[0] == "reserve":
                cmd_reserve(NS(date=parts[1], start=parts[2], end=parts[3], wait_open=False))
            elif parts == ["status"]:
                cmd_status(NS())
            elif parts == ["test"]:
                cmd_test(NS())
            else:
                print(f"[command] 不明な操作: {parts}")
        except Exception as e:  # 1件失敗しても残りは続ける
            notify.send("タイムズカー：エラー", f"{' '.join(parts)}: {e}"[:500], priority=4, tags=["x"])
        # 処理済みの位置を記録（同じ操作を二重に実行しない）
        state = st.load()
        state["cmd_since"] = m["id"]
        st.save(state)
    return len(msgs)


def cmd_poll(args) -> None:
    if args.check:
        has = bool(pending_commands())
        print(f"pending={'true' if has else 'false'}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"pending={'true' if has else 'false'}\n")
        return
    run_commands()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reserve")
    r.add_argument("--date")
    r.add_argument("--start")
    r.add_argument("--end")
    r.add_argument("--wait-open", action="store_true")
    sub.add_parser("remind")
    sub.add_parser("auto-cancel")
    k = sub.add_parser("keep")
    k.add_argument("number")
    c = sub.add_parser("cancel")
    c.add_argument("number")
    sub.add_parser("status")
    sub.add_parser("test")
    pl = sub.add_parser("poll")
    pl.add_argument("--check", action="store_true")
    args = p.parse_args()
    try:
        {
            "reserve": cmd_reserve, "remind": cmd_remind, "auto-cancel": cmd_auto_cancel,
            "keep": cmd_keep, "cancel": cmd_cancel, "status": cmd_status, "test": cmd_test,
            "poll": cmd_poll,
        }[args.cmd](args)
    except Exception as e:
        notify.send("タイムズカー：エラー", f"{args.cmd}: {e}"[:500], priority=4, tags=["x"])
        raise


if __name__ == "__main__":
    main()
