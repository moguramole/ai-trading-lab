"""東証の営業日カレンダー。土日・祝日・年末年始(12/31, 1/1〜1/3)を休場とする。

臨時休場（システム障害による終日停止など）は含まない。その日は株価が存在しないだけなので、
約定はデータ上の次の日付で行われ、仮想売買の結果には影響しない。
"""
from datetime import date, datetime, timedelta

import jpholiday


def _d(d) -> date:
    return d.date() if isinstance(d, datetime) else d if isinstance(d, date) else d.to_pydatetime().date()


def is_business_day(d) -> bool:
    d = _d(d)
    if d.weekday() >= 5 or jpholiday.is_holiday(d):
        return False
    return not ((d.month, d.day) in {(12, 31), (1, 1), (1, 2), (1, 3)})


def next_business_day(d) -> date:
    d = _d(d) + timedelta(days=1)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def last_business_day(d) -> date:
    """d以前で最も新しい営業日（dが営業日ならd）。"""
    d = _d(d)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def business_days_between(start, end) -> int:
    """startの翌日からend(含む)までの営業日数。start>=endなら0。"""
    start, end = _d(start), _d(end)
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += is_business_day(d)
    return n
