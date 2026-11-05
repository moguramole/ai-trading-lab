"""AIへの依頼文の組み立て。ポータルの表示・登録時の記録・判断用コマンドで同じ処理を使い、内容を一致させる。"""
from . import data, fetch, market, news, portfolio, prompt, runner, universe
from .ai_adapters import InvalidSignal
from .runner import INITIAL


def last_prices(prices) -> dict[str, float]:
    return {t: float(g["close"].iloc[-1]) for t, g in prices.groupby("ticker")}


def candidates_for(as_of: str, snap, held: list[str]):
    """その判断日の依頼文に載せる候補。V2（START_V2以降）は複数の観点で集めた一覧、それ以前は従来の押し目だけ。"""
    return (market.screened if as_of >= prompt.START_V2 else market.candidates)(snap, held=list(held))


def allowed_tickers(as_of: str, snap, held: list[str]) -> set[str] | None:
    """判断してよい銘柄。V2は依頼文に載せた一覧の中だけ（一覧外の銘柄は、判断材料を渡していないので登録しない）。
    V1は従来どおり制限しない（None）。"""
    if as_of < prompt.START_V2:
        return None
    return set(candidates_for(as_of, snap, held)["ticker"])


def check_allowed(sigs, allowed: set[str] | None):
    """依頼文の一覧にない銘柄の判断を拒否する（一覧外は、判断材料を渡していない）。"""
    if allowed is None:
        return
    out = [s.ticker for s in sigs if s.ticker not in allowed]
    if out:
        raise InvalidSignal(f"依頼文の候補銘柄にない銘柄です: {", ".join(out[:5])}（一覧の中から選んでください）")


def build(*, who, prices, bench, snap, broker, con):
    """(依頼文, 依頼文に載せた適時開示)。"""
    as_of = prices["date"].max()
    last = last_prices(prices)
    held = {t: (q, last[t]) for t, q in broker.positions.items()}
    cands = candidates_for(as_of.strftime("%Y-%m-%d"), snap, list(held))
    used = news.for_prompt(con, [*held, *cands["ticker"]], as_of.date())
    text = prompt.build_prompt(
        model=who, as_of=as_of.strftime("%Y-%m-%d"), initial=INITIAL, equity=broker.equity(last),
        cash=broker.cash, positions=held, index_df=market.index_table(bench, fetch.BENCHMARKS),
        breadth=market.breadth(snap), sectors=market.sector_table(snap), cands=cands, news=used)
    return text, used


def load_context(con):
    """ポータルの compute と同じ手順で、依頼文に必要なものを揃える。(prices, bench, snap, accounts)"""
    prices, _ = data.read_clean(con)
    if prices.empty:
        raise RuntimeError("株価データがありません")
    uni = universe.load(con)
    stocks, bench = runner.split_benchmarks(prices, portfolio.extra_tickers(con, uni))
    accounts, _ = runner.build_accounts(con, stocks)
    return stocks, bench, market.snapshot(stocks, uni), accounts


