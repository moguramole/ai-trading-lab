"""動作確認用の【架空】株価データを生成する。実在の株価ではない。"""
import numpy as np
import pandas as pd
from pathlib import Path

rng = np.random.default_rng(42)
dates = pd.bdate_range("2026-01-05", periods=200)
rows = []
for ticker, start in {"1001": 1500, "1002": 2800, "1003": 900, "1004": 4200, "1005": 600}.items():
    close = start * np.exp(np.cumsum(rng.normal(0.0003, 0.015, len(dates))))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.003, len(dates)))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.004, len(dates))))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.004, len(dates))))
    vol = rng.integers(50_000, 500_000, len(dates))
    for i, d in enumerate(dates):
        rows.append((d.strftime("%Y-%m-%d"), ticker, round(open_[i], 1), round(high[i], 1),
                     round(low[i], 1), round(close[i], 1), int(vol[i])))
out = Path(__file__).parent / "sample_prices.csv"
pd.DataFrame(rows, columns=["date", "ticker", "open", "high", "low", "close", "volume"]).to_csv(out, index=False)
print("wrote", out)
