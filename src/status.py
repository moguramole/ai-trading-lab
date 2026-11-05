"""ポータル用のシステム状況の判定（取得ログ・データの鮮度・定時実行）。"""
import json
import re
import subprocess
from datetime import datetime, timedelta

import pandas as pd

from . import jpcal

TASK_NAME = "AI-Trading-Lab-DailyUpdate"
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ (INFO|ERROR|WARNING) (.*)$")


def parse_log(lines: list[str]) -> dict:
    """更新ログから、最終成功時刻・最終エラー時刻・健全性を返す。"""
    last_ok = last_err = None
    for l in lines:
        m = _LINE.match(l.strip())
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if m.group(2) == "INFO":
            last_ok = ts
        elif m.group(2) == "ERROR":
            last_err = ts
    healthy = last_ok is not None and (last_err is None or last_ok > last_err)
    return {"last_ok": last_ok, "last_err": last_err, "healthy": healthy}


def business_lag(latest: pd.Timestamp, expected: pd.Timestamp) -> int:
    """最新データ日が期待日より何営業日遅れか（東証の休場日を除く）。"""
    return jpcal.business_days_between(latest, expected)


def freshness(lag: int) -> tuple[str, str]:
    """(表示ラベル, 重大度ok/warn/bad)。取得前の時間帯に1営業日遅れて見えることがあるのでwarn止まり。"""
    if lag == 0:
        return "最新", "ok"
    if lag == 1:
        return "1営業日遅れ", "warn"
    return f"{lag}営業日遅れ（要確認）", "bad"


def task_info() -> dict | None:
    """タスクスケジューラの状態。取得できなければ None。"""
    ps = (f"$i=Get-ScheduledTaskInfo -TaskName '{TASK_NAME}';"
          f"$t=Get-ScheduledTask -TaskName '{TASK_NAME}';"
          "[pscustomobject]@{state=[string]$t.State;"
          "last=$i.LastRunTime.ToString('s');result=$i.LastTaskResult;"
          "next=$i.NextRunTime.ToString('s')} | ConvertTo-Json")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=15, encoding="utf-8")
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def parse_dt(s: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(s)
        return d if d.year > 1999 else None  # 未実行は 1999-11-30 などになる
    except (TypeError, ValueError):
        return None
