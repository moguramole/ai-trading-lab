"""判断用エージェント向けのコマンド。依頼文の書き出しと、判断JSONの登録だけを行う。

    python -m src.agent_cli prompt   --model claude        依頼文を data/agent/ に書き出す
    python -m src.agent_cli register --model claude --file data/agent/answer_claude_YYYY-MM-DD.json --llm <モデル名>

ポータルのフォームと同じ検証（JSON・銘柄・日付・締め切り）を通す。加えて次を守る。
- 判断日は、DBの最新の株価日と一致していること（過去日・未来日の判断は登録しない）。
- 同じモデルが同じ判断日に2回登録することはできない（出し直しで結果を選べないようにする）。
登録した判断には出どころ(source=agent)と、申告されたモデル名を記録する。
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from . import data, news, prompt, request, store
from .ai_adapters import JST, InvalidSignal, Signal, check_deadline, check_not_early, parse_signals

OUT = Path(__file__).resolve().parent.parent / "data" / "agent"
MODELS = ("gpt", "claude")


def _fence_free(text: str) -> str:
    """```json ... ``` で囲まれていたら中身だけにする。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def cmd_prompt(model: str, now=None) -> int:
    con = data.connect()
    try:
        try:
            print(f"適時開示: 新規{news.update(con, (now or datetime.now(JST)).date())}件")
        except Exception as e:  # 取得失敗は致命的ではない（依頼文は「該当なし」か、取得済みの分になる）
            print(f"適時開示の取得に失敗（続行）: {e}")
        prices, bench, snap, accounts = request.load_context(con)
        as_of = prices["date"].max().strftime("%Y-%m-%d")
        if store.has_signals(con, model, as_of):
            print(f"{model} の {as_of} の判断はすでに登録済みです。出し直しはできません。")
            return 2
        try:
            probe = [Signal(model, as_of, "-", "hold", 0, "", "")]
            check_not_early(probe, now)
            check_deadline(probe, prices["date"].max(), now)
        except InvalidSignal as e:
            print(f"今は依頼文を作れません: {e}")
            return 2
        text, used = request.build(who=model, prices=prices, bench=bench, snap=snap,
                                   broker=accounts[model][0], con=con)
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"prompt_{model}_{as_of}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"判断日: {as_of}\n依頼文: {path}\n載せた適時開示: {len(used)}件\n依頼文の版: {prompt.version(as_of)}")
        return 0
    finally:
        con.close()


def cmd_register(model: str, file: str, llm: str, now=None) -> int:
    con = data.connect()
    try:
        prices, bench, snap, accounts = request.load_context(con)
        latest = prices["date"].max()
        as_of = latest.strftime("%Y-%m-%d")
        text = _fence_free(Path(file).read_text(encoding="utf-8-sig"))
        try:
            sigs = parse_signals(text, expected_model=model, known_tickers=set(prices["ticker"]))
            wrong = [s for s in sigs if s.as_of != as_of]
            if wrong:
                raise InvalidSignal(f"判断日が最新の株価日({as_of})と違います: {wrong[0].ticker} {wrong[0].as_of}")
            avail = set(zip(prices["ticker"], prices["date"].dt.strftime("%Y-%m-%d")))
            bad = [s for s in sigs if (s.ticker, s.as_of) not in avail]
            if bad:
                raise InvalidSignal(f"データに存在しない銘柄・日付です: {bad[0].ticker} {bad[0].as_of}")
            if len({s.ticker for s in sigs}) != len(sigs):
                raise InvalidSignal("同じ銘柄の判断が複数あります（1銘柄につき1件にしてください）")
            held = list(accounts[model][0].positions)
            request.check_allowed(sigs, request.allowed_tickers(as_of, snap, held))
            check_not_early(sigs, now)
            check_deadline(sigs, latest, now)
            if store.has_signals(con, model, as_of):
                raise InvalidSignal(f"{model} の {as_of} の判断はすでに登録済みです。出し直しはできません")
        except InvalidSignal as e:
            print(f"登録しません（見送り）: {e}")
            return 1
        _, used = request.build(who=model, prices=prices, bench=bench, snap=snap,
                                broker=accounts[model][0], con=con)
        n = store.save_signals(con, sigs, prompt.version(as_of), prompt.template(as_of), source="agent", llm=llm)
        news.record_used(con, model, as_of, used["url"])
        acts = {a: sum(s.action == a for s in sigs) for a in ("buy", "sell", "hold")}
        print(f"{n}件登録（買い{acts['buy']} / 売り{acts['sell']} / 見送り{acts['hold']}）。"
              f"載せた開示{len(used)}件を記録。版 {prompt.version(as_of)}、出どころ agent、モデル {llm}")
        return 0
    finally:
        con.close()


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prompt")
    p.add_argument("--model", choices=MODELS, required=True)
    r = sub.add_parser("register")
    r.add_argument("--model", choices=MODELS, required=True)
    r.add_argument("--file", required=True)
    r.add_argument("--llm", required=True, help="判断したモデルの名前（自己申告。記録用）")
    a = ap.parse_args(argv)
    return cmd_prompt(a.model) if a.cmd == "prompt" else cmd_register(a.model, a.file, a.llm)


if __name__ == "__main__":
    sys.exit(main())



