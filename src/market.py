"""市場全体の把握。全銘柄の指標を計算し、地合い・業種・AIに見せる候補を作る。

使うのは判断日までのデータだけ。個別銘柄の最終判断はAI、制限判定はPython。
"""
import pandas as pd

MAX_PRICE = 30_000  # 1株の単価がこれを超える銘柄は、損失上限(0.5%)の都合で実質買えないので候補から外す
MKT_MIN = 0.5       # 地合いの警告線: 60日線の上にある銘柄の割合がこれ未満の日は「地合いが弱い」（backtestと共通）


def snapshot(prices: pd.DataFrame, universe: pd.DataFrame | None = None) -> pd.DataFrame:
    """銘柄ごとの最新指標。universeがあれば銘柄名・業種を付ける。"""
    rows = []
    for t, g in prices.groupby("ticker"):
        g = g.sort_values("date")
        c, v = g["close"].reset_index(drop=True), g["volume"].reset_index(drop=True)
        n = len(c)
        if n < 21:
            continue
        ret = lambda k: c.iloc[-1] / c.iloc[-1 - k] - 1 if n > k else float("nan")
        sma20, sma60 = c.tail(20).mean(), c.tail(60).mean()
        rows.append({
            "ticker": t, "date": g["date"].iloc[-1], "close": c.iloc[-1],
            "ret1": ret(1), "ret5": ret(5), "ret20": ret(20),
            "gap20": c.iloc[-1] / sma20 - 1, "gap60": c.iloc[-1] / sma60 - 1,
            "trend_up": bool(sma20 > sma60), "above60": bool(c.iloc[-1] > sma60),
            "turnover20": float((g["close"] * g["volume"]).tail(20).mean()),
            "vol_ratio": float(v.iloc[-1] / v.tail(20).mean()) if v.tail(20).mean() > 0 else 0.0,
            "new_high20": bool(c.iloc[-1] >= g["high"].iloc[-21:-1].max()),
            "dist_high60": c.iloc[-1] / g["high"].tail(60).max() - 1,
        })
    snap = pd.DataFrame(rows)
    if universe is not None and len(snap):
        snap = snap.merge(universe[["ticker", "name", "sector33"]], on="ticker", how="left")
    return snap


def breadth(snap: pd.DataFrame) -> dict:
    """地合い: 上昇銘柄の割合など。"""
    if snap.empty:
        return {}
    return {
        "銘柄数": len(snap),
        "本日上昇": (snap["ret1"] > 0).mean(),
        "20日線の上": (snap["gap20"] > 0).mean(),
        "60日線の上": (snap["above60"]).mean(),
        "20日高値更新": int(snap["new_high20"].sum()),
        "20日平均騰落": snap["ret20"].mean(),
        "本日平均騰落": snap["ret1"].mean(),
    }


def sector_table(snap: pd.DataFrame) -> pd.DataFrame:
    if snap.empty or "sector33" not in snap:
        return pd.DataFrame()
    t = snap.groupby("sector33").agg(銘柄数=("ticker", "count"), 本日=("ret1", "mean"),
                                    五日=("ret5", "mean"), 二十日=("ret20", "mean"))
    return t.sort_values("二十日", ascending=False).reset_index().rename(columns={"sector33": "業種"})


def index_table(prices: pd.DataFrame, names: dict) -> pd.DataFrame:
    rows = []
    for t, label in names.items():
        c = prices.loc[prices["ticker"] == t].sort_values("date")["close"].reset_index(drop=True)
        if len(c) > 20:
            rows.append({"指標": label, "値": c.iloc[-1], "本日": c.iloc[-1] / c.iloc[-2] - 1,
                         "五日": c.iloc[-1] / c.iloc[-6] - 1, "二十日": c.iloc[-1] / c.iloc[-21] - 1})
    return pd.DataFrame(rows)


def candidates(snap: pd.DataFrame, held: list[str] = (), n_pullback=12, n_momentum=0) -> pd.DataFrame:
    """AIに見せる候補。全銘柄は多すぎるので、ルールで絞る（絞り込みの根拠は列 reason に残す）。

    共通条件: 売買代金が全体の上位半分、単価がMAX_PRICE以下。
    押し目: 上昇基調(20日線>60日線、終値>60日線)で直近5日が下落。
    勢い: 20日高値を更新し、出来高が平均の1.3倍以上。既定では候補に入れない（n_momentum=0）。
          過去約1年の検証で、総リターンがランダム選択の幅を下回り、地合いフィルターを付けても改善しなかったため。
    保有銘柄は売り判断のため常に含める。
    ※ 地合いが弱い日にこの押し目ルールで買うと成績が悪かった（backtest.run_variants）。候補は空にせず、
       依頼文に警告として載せてAIに判断させる（prompt.build_prompt）。
    """
    if snap.empty:
        return snap.assign(reason=[])
    liquid = snap[(snap["turnover20"] >= snap["turnover20"].median()) & (snap["close"] <= MAX_PRICE)]
    pull = liquid[liquid["trend_up"] & liquid["above60"] & (liquid["ret5"] < 0)] \
        .sort_values("ret20", ascending=False).head(n_pullback).assign(reason="上昇基調の押し目")
    mom = liquid[liquid["new_high20"] & (liquid["vol_ratio"] >= 1.3)] \
        .sort_values("vol_ratio", ascending=False).head(n_momentum).assign(reason="高値更新+出来高増")
    own = snap[snap["ticker"].isin(held)].assign(reason="保有中")
    out = pd.concat([own, pull, mom]).drop_duplicates("ticker")
    return out.reset_index(drop=True)


def screened(snap: pd.DataFrame, held: list[str] = (), per=10, limit=80) -> pd.DataFrame:
    """AIが自分で銘柄を選ぶための一覧。複数の観点で機械的に集め、該当した条件を reason に残す。

    観点: 上昇基調の押し目 / 高値更新+出来高増 / 20日の上昇・下落上位 / 本日の上昇・下落上位 /
    出来高急増 / 強い業種の主力。いずれも売買代金が上位半分・単価がMAX_PRICE以下に限る。
    保有銘柄は先頭に、常に含める。抽出条件に有効性の保証はない（過去検証では押し目も高値更新も不安定だった）。
    """
    if snap.empty:
        return snap.assign(reason=[])
    liquid = snap[(snap["turnover20"] >= snap["turnover20"].median()) & (snap["close"] <= MAX_PRICE)]
    parts = [snap[snap["ticker"].isin(held)].assign(reason="保有中"),
             liquid[liquid["trend_up"] & liquid["above60"] & (liquid["ret5"] < 0)]
             .sort_values("ret20", ascending=False).head(12).assign(reason="上昇基調の押し目"),
             liquid[liquid["new_high20"] & (liquid["vol_ratio"] >= 1.3)]
             .sort_values("vol_ratio", ascending=False).head(per).assign(reason="高値更新+出来高増"),
             liquid.sort_values("ret20", ascending=False).head(per).assign(reason="20日上昇上位"),
             liquid.sort_values("ret20").head(per).assign(reason="20日下落上位"),
             liquid.sort_values("ret1", ascending=False).head(8).assign(reason="本日上昇上位"),
             liquid.sort_values("ret1").head(8).assign(reason="本日下落上位"),
             liquid.sort_values("vol_ratio", ascending=False).head(per).assign(reason="出来高急増")]
    if "sector33" in liquid and liquid["sector33"].notna().any():
        top = liquid.groupby("sector33")["ret20"].mean().sort_values(ascending=False).head(3).index
        for s in top:
            parts.append(liquid[liquid["sector33"] == s].sort_values("ret20", ascending=False).head(3)
                         .assign(reason=f"強い業種の主力({s})"))
    allp = pd.concat(parts)
    reasons = allp.groupby("ticker", sort=False)["reason"].agg(lambda r: "/".join(dict.fromkeys(r)))
    out = allp.drop_duplicates("ticker")
    out = out.assign(reason=out["ticker"].map(reasons))
    own = out[out["reason"].str.startswith("保有中")]
    rest = out[~out["ticker"].isin(own["ticker"])]
    return pd.concat([own, rest.head(max(limit - len(own), 0))]).reset_index(drop=True)
