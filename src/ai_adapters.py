"""GPT/Claudeの判断JSONを厳格に検証する。不正なものは「見送り」扱いにする。

APIは呼ばない。ChatGPT/Claudeのチャットで得たJSONを貼り付けて使う（手動運用）。
"""
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from . import jpcal, schedule

REQUIRED = ("model", "as_of", "ticker", "action", "confidence", "reason", "risk_notes")
ACTIONS = {"buy", "sell", "hold"}  # 買い・売り・見送り


@dataclass(frozen=True)
class Signal:
    model: str
    as_of: str
    ticker: str
    action: str
    confidence: float  # 主観スコア。確率ではない
    reason: str
    risk_notes: str
    qty: int = 0
    stop_price: float | None = None


class InvalidSignal(ValueError):
    pass


def parse_signals(text, *, expected_model=None, known_tickers=None):
    """JSON文字列（オブジェクトまたは配列）を検証して Signal のリストを返す。

    1件でも不正なら InvalidSignal。呼び出し側は全件を見送りにする。
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        raise InvalidSignal(f"JSONとして読めない: {e}")
    items = data if isinstance(data, list) else [data]
    if not items:
        raise InvalidSignal("空の応答")
    return [_validate(i, expected_model, known_tickers) for i in items]


JST = timezone(timedelta(hours=9))
FILL_OPEN = time(9, 0)  # 約定に使う始値が決まる時刻


def check_deadline(signals, latest_price_date, now=None):
    """約定に使う翌営業日の始値が分かった後の登録を拒否する（未来情報の混入防止）。

    1) 判断日より新しい株価がDBにある → 始値は取得済み。
    2) 現在時刻が、判断日の翌営業日の9:00(JST)以降 → 始値が公開済み。
    翌営業日は東証のカレンダー（土日・祝日・年末年始を除く）で決める。
    """
    now = now or datetime.now(JST)
    # pandasのTimestampはdatetime(=dateの子)なので、isinstance(date)では判別できない。日付に正規化する
    latest = latest_price_date.date() if isinstance(latest_price_date, datetime) else latest_price_date
    for s in signals:
        try:
            as_of = date.fromisoformat(s.as_of)
        except ValueError:
            raise InvalidSignal(f"日付の形式が不正です: {s.as_of!r}")
        if as_of < latest:
            raise InvalidSignal(f"締め切り後です: {s.ticker} {s.as_of} の翌営業日の始値はすでに取得済み")
        deadline = datetime.combine(jpcal.next_business_day(as_of), FILL_OPEN, tzinfo=JST)
        if now >= deadline:
            raise InvalidSignal(f"締め切り後です: {s.ticker} {s.as_of} は {deadline:%m/%d %H:%M} までに登録が必要")


def check_not_early(signals, now=None):
    """情報の締め切り（判断日の21:30 JST）より前の判断を拒否する。

    締め切りまでに出る株価・適時開示が揃っていない状態で判断すると、GPTとClaudeで見た情報が変わるため。
    """
    now = now or datetime.now(JST)
    for s in signals:
        try:
            as_of = date.fromisoformat(s.as_of)
        except ValueError:
            raise InvalidSignal(f"日付の形式が不正です: {s.as_of!r}")
        opens = datetime.combine(as_of, schedule.DAILY_RUN, tzinfo=JST)
        if now < opens:
            raise InvalidSignal(f"情報の締め切り前です: {s.as_of} の判断は {opens:%m/%d %H:%M} 以降に出してください"
                                "（その時刻までの株価・適時開示がそろってから）")


def _validate(d, expected_model, known_tickers):
    if not isinstance(d, dict):
        raise InvalidSignal("オブジェクトではない")
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        raise InvalidSignal(f"項目欠損: {missing}")
    if d["action"] not in ACTIONS:
        raise InvalidSignal(f"未知のアクション: {d['action']!r}")
    if expected_model and d["model"] != expected_model:
        raise InvalidSignal(f"モデル名不一致: {d['model']!r}")
    if known_tickers is not None and d["ticker"] not in known_tickers:
        raise InvalidSignal(f"未知の銘柄: {d['ticker']!r}")
    conf = d["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise InvalidSignal("confidenceは0〜1の数値")
    qty = d.get("qty", 0)
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
        raise InvalidSignal("qtyは0以上の整数")
    if d["action"] in ("buy", "sell") and qty == 0:
        raise InvalidSignal("売買にはqtyが必要")
    stop = d.get("stop_price")
    if stop is not None and (isinstance(stop, bool) or not isinstance(stop, (int, float)) or stop <= 0):
        raise InvalidSignal("stop_priceは正の数")
    return Signal(d["model"], str(d["as_of"]), d["ticker"], d["action"], float(conf),
                  str(d["reason"]), str(d["risk_notes"]), qty, stop)

