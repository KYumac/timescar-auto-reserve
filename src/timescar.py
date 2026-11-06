"""タイムズカー会員サイトの操作（Playwright）

画面の流れ（2026-11-06に実機で確認）
- ログイン: api.timesclub.jp/view/pc/tpLogin.jsp（会員番号 + パスワードのみ）
- 予約入力: /view/reserve/input.jsp?scd=<ステーションコード>
    → 「入力内容確認」(#doCheck) → /view/reserve/confirm.jsp
    → 「予約確定」(#doOnceRegist) → /view/reserve/complete.jsp?bid=<予約番号>
- 予約一覧: /view/reserve/list.jsp  （取消リンク cancel.jsp?rid=<内部ID>）
- 取消: /view/reserve/cancel.jsp?rid=... → 「取消確定」(#doOnceCancelComplete)
    → /view/reserve/cancelComplete.jsp
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime

from playwright.sync_api import Page, TimeoutError as PWTimeout

BASE = "https://share.timescar.jp"
MYPAGE = f"{BASE}/view/member/mypage.jsp"


class TimesError(Exception):
    pass


class NotOpenYet(TimesError):
    """予約受付期間がまだ開いていない（日付が選択肢に無い）"""


@dataclass
class Reservation:
    number: str          # 予約番号（bid）
    rid: str | None      # 取消・変更用の内部ID
    station: str
    start: datetime
    end: datetime | None
    car: str = ""


# ---------------------------------------------------------------- ログイン

def login(page: Page, member_id: str, password: str) -> None:
    page.goto(MYPAGE, wait_until="domcontentloaded")
    if "mypage.jsp" in page.url and "timesclub" not in page.url:
        return  # すでにログイン済み

    digits = re.sub(r"\D", "", member_id)
    text_inputs = page.locator("input[type=text]:visible, input[type=tel]:visible, input[type=number]:visible")
    n = text_inputs.count()
    if n == 0:
        raise TimesError("ログイン画面の会員番号欄が見つかりません")

    # 会員番号は複数の欄に分かれているので maxlength に合わせて分配する
    pos = 0
    for i in range(n):
        el = text_inputs.nth(i)
        ml = el.get_attribute("maxlength")
        size = int(ml) if ml and ml.isdigit() else len(digits) - pos
        el.fill(digits[pos:pos + size])
        pos += size
        if pos >= len(digits):
            break

    page.locator("input[type=password]:visible").first.fill(password)

    submit = page.locator(
        "input[type=image][alt*='ログイン'], input[type=submit][value*='ログイン'], "
        "button:has-text('ログイン'), a:has-text('ログイン')"
    ).first
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        submit.click()

    if "mypage.jsp" not in page.url:
        body = page.inner_text("body")[:300]
        raise TimesError(f"ログインに失敗しました: {page.url} / {body}")


# ---------------------------------------------------------------- 予約入力

def _open_input(page: Page, station_code: str) -> None:
    page.goto(f"{BASE}/view/reserve/input.jsp?scd={station_code}", wait_until="domcontentloaded")
    if page.locator("#carId").count() == 0:
        raise TimesError(f"予約入力画面を開けません: {station_code} / {page.url}")


def list_cars(page: Page) -> list[tuple[str, str]]:
    """予約入力画面にある車両 (carId, 名称) の一覧"""
    return page.eval_on_selector(
        "#carId",
        "s => [...s.options].filter(o => o.value).map(o => [o.value, o.text])",
    )


def _date_value(d: date) -> str:
    return f"{d:%Y-%m-%d} 00:00:00.0"


def _set_select(page: Page, sel_id: str, value: str) -> None:
    ok = page.evaluate(
        """([id, v]) => {
            const s = document.getElementById(id);
            if (!s || ![...s.options].some(o => o.value === v)) return false;
            s.value = v;
            s.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""",
        [sel_id, value],
    )
    if not ok:
        if sel_id in ("dateStart", "dateEnd", "dateSpace"):
            raise NotOpenYet(f"{sel_id} に {value} がまだありません")
        raise TimesError(f"{sel_id} に {value} を設定できません")
    page.wait_for_timeout(400)  # 画面側のchangeハンドラ(時間計算)を待つ


def vacancy(page: Page, d: date, start_hour: int) -> dict[str, list[str]]:
    """タイムテーブルを指定日時に切り替え、車両ごとの15分枠の状態を返す
    戻り値: {carId: ["vacant"|"full"|"impossible"|..., ...]}（start_hourから12時間分）
    """
    _set_select(page, "dateSpace", _date_value(d))
    _set_select(page, "hourSpace", str(start_hour))
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.click("#doSearchTargetTimetable")

    cars = [c for c, _ in list_cars(page)]
    states = page.evaluate(
        r"""() => [...document.querySelectorAll('table')]
            .filter(t => /\d\d:00/.test(t.innerText) && t.querySelector('[class*="timeline"]'))
            .map(t => {
                const rows = [...t.rows];
                const last = rows[rows.length - 1];
                return [...last.cells].map(c => {
                    const m = c.className.match(/(vacant|full|impossible|mine|maintenance|own|reserved)/);
                    return m ? m[1] : 'unknown';
                });
            })"""
    )
    return dict(zip(cars, states))


def is_free(slots: list[str], start_hour: int, start_min: int, end_hour: int, end_min: int,
            table_start_hour: int) -> bool:
    """タイムテーブル(15分刻み)上で、開始〜終了がすべて空きか"""
    first = ((start_hour - table_start_hour) * 60 + start_min) // 15
    last = ((end_hour - table_start_hour) * 60 + end_min) // 15  # 終了枠は含まない
    if first < 0 or last > len(slots):
        return False
    return all(s == "vacant" for s in slots[first:last])


def reserve(page: Page, station_code: str, car_id: str, d: date,
            start: tuple[int, int], end: tuple[int, int], anshin: bool,
            dry_run: bool = False) -> str | None:
    """予約を確定し予約番号を返す。dry_run=Trueなら確認画面で止める。
    空いていなければ None を返す。
    """
    _open_input(page, station_code)
    _set_select(page, "carId", car_id)
    _set_select(page, "pack", "1")  # 時間料金
    _set_select(page, "dateStart", _date_value(d))
    _set_select(page, "hourStart", str(start[0]))
    _set_select(page, "minuteStart", f"{start[1]:02d}")
    _set_select(page, "dateEnd", _date_value(d))
    _set_select(page, "hourEnd", str(end[0]))
    _set_select(page, "minuteEnd", f"{end[1]:02d}")
    page.check("#exemptNocFlgYes" if anshin else "#exemptNocFlgNo")

    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        page.click("#doCheck")

    if "confirm.jsp" not in page.url:
        msg = page.inner_text("body")
        if "予約できない期間" in msg:
            return None  # 埋まっている
        m = re.search(r"入力内容に誤りがあります\s*(.{0,120})", msg)
        raise TimesError("入力エラー: " + (m.group(1).strip() if m else page.url))

    if dry_run:
        return "DRY-RUN"

    with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
        page.click("#doOnceRegist")

    m = re.search(r"bid=(\d+)", page.url)
    if not m:
        body = page.inner_text("body")
        if "予約できない" in body:
            return None  # 確定直前に他の人に取られた
        raise TimesError(f"予約確定後の画面が想定外です: {page.url}")
    return m.group(1)


# ---------------------------------------------------------------- 一覧・取消

_DT = re.compile(r"(\d{4})年(\d{2})月(\d{2})日（.）(\d{2}):(\d{2})")


def list_reservations(page: Page) -> list[Reservation]:
    page.goto(f"{BASE}/view/reserve/list.jsp", wait_until="domcontentloaded")
    rows = page.evaluate(
        r"""() => {
            const out = [];
            const seen = new Set();
            document.querySelectorAll('a[href*="cancel.jsp?rid="]').forEach(a => {
                const rid = (a.getAttribute('href').match(/rid=(\d+)/) || [])[1];
                if (!rid || seen.has(rid)) return;
                seen.add(rid);
                // 予約番号を含む最小の行ブロックまでさかのぼる
                let el = a;
                while (el && !/\d{9}/.test(el.innerText || '')) el = el.parentElement;
                out.push({rid, text: el ? el.innerText : ''});
            });
            return out;
        }"""
    )
    result = []
    for r in rows:
        text = r["text"]
        num = re.search(r"\b(\d{9})\b", text)
        dts = [datetime(*map(int, m.groups())) for m in _DT.finditer(text)]
        # 一覧の並び: 予約開始日時 → (予約受付日時) → 返却予定日時
        start = dts[0] if dts else None
        end = dts[-1] if len(dts) >= 2 else None
        st = ""
        for line in text.splitlines():
            if any(k in line for k in ("タイムズ", "ビル", "マンション", "東日本橋", "町")) and "年" not in line:
                st = line.strip()
                break
        if num and start:
            result.append(Reservation(num.group(1), r["rid"], st, start, end))
    return result


def cancel(page: Page, rid: str) -> None:
    page.goto(f"{BASE}/view/reserve/cancel.jsp?rid={rid}", wait_until="domcontentloaded")
    if page.locator("#doOnceCancelComplete").count() == 0:
        raise TimesError(f"取消画面を開けません: rid={rid} / {page.url}")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        page.click("#doOnceCancelComplete")
    if "cancelComplete" not in page.url:
        raise TimesError(f"取消が完了しませんでした: {page.url}")


def wait_until_open(page: Page, station_code: str, d: date, deadline: float) -> None:
    """予約受付期間に d が入るまで、入力画面を開き直して待つ"""
    while True:
        _open_input(page, station_code)
        has = page.evaluate(
            "v => [...document.getElementById('dateStart').options].some(o => o.value === v)",
            _date_value(d),
        )
        if has:
            return
        if time.time() > deadline:
            raise NotOpenYet(f"{d} の予約受付が開きませんでした")
        time.sleep(5)
