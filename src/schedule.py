"""1日の運用時刻。株価・適時開示の取得と、判断に使う情報の締め切りを同じ時刻にそろえる。

取得タスクは平日この時刻に走る。AIの判断は、この時刻までに出た情報（株価・適時開示）だけを使い、
この時刻より前には出せない（GPTとClaudeをいつ聞いても同じ情報になるようにするため）。
"""
from datetime import datetime, time, timedelta, timezone

import pandas as pd

from . import jpcal

JST = timezone(timedelta(hours=9))
DAILY_RUN = time(21, 30)


def expected_latest(now: datetime | None = None) -> pd.Timestamp:
    """この時刻に取得済みであるべき、最新の株価日。運用時刻より前は前営業日。"""
    now = (now or datetime.now(JST)).astimezone(JST)
    d = now.date() if now.time() >= DAILY_RUN else now.date() - timedelta(days=1)
    return pd.Timestamp(jpcal.last_business_day(d))
