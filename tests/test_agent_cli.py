import json
from datetime import datetime

import pytest

from src import agent_cli, data, news, store
from src.ai_adapters import JST


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    con = data.connect(db)
    px = data.load_csv("sample/sample_prices.csv")
    data.save_prices(con, px, "test")
    con.close()
    real_connect = data.connect
    monkeypatch.setattr(data, "connect", lambda *a, **k: real_connect(db))
    monkeypatch.setattr(agent_cli, "OUT", tmp_path / "agent")
    monkeypatch.setattr(news, "update", lambda *a, **k: 0)          # ネットワークに出ない
    last = px["date"].max()
    as_of = last.strftime("%Y-%m-%d")
    now = datetime(last.year, last.month, last.day, 22, 0, tzinfo=JST)  # 判断日の夜（情報の締め切り21:30の後、登録の締め切り前）
    return dict(db=db, as_of=as_of, now=now, ticker=px["ticker"].iloc[0], tmp=tmp_path)


def _answer(env, **kw):
    d = dict(model="claude", as_of=env["as_of"], ticker=env["ticker"], action="hold", confidence=0.5,
             reason="r", risk_notes="n", qty=0)
    d.update(kw)
    p = env["tmp"] / "answer.json"
    p.write_text("```json\n" + json.dumps([d]) + "\n```", encoding="utf-8")
    return str(p)


def test_prompt_written_and_registered_once(env, capsys):
    assert agent_cli.cmd_prompt("claude", env["now"]) == 0
    assert (env["tmp"] / "agent" / f"prompt_claude_{env['as_of']}.txt").read_text(encoding="utf-8").startswith("あなた")
    assert agent_cli.cmd_register("claude", _answer(env), "test-llm", env["now"]) == 0
    con = data.connect()
    assert [(m, s, l) for m, s, l, _ in store.source_summary(con)] == [("claude", "agent", "test-llm")]
    con.close()
    # 出し直しは不可（依頼文も、登録も）
    assert agent_cli.cmd_prompt("claude", env["now"]) == 2
    assert agent_cli.cmd_register("claude", _answer(env, action="hold", reason="別の答え"), "x", env["now"]) == 1


def test_register_rejects_wrong_date_model_and_late(env):
    assert agent_cli.cmd_register("claude", _answer(env, as_of="2000-01-01"), "x", env["now"]) == 1
    assert agent_cli.cmd_register("claude", _answer(env, model="gpt"), "x", env["now"]) == 1
    late = datetime.fromisoformat(env["as_of"]).replace(tzinfo=JST).replace(day=28, month=12, year=2030)
    assert agent_cli.cmd_register("claude", _answer(env), "x", late) == 1       # 翌営業日の始値が出た後
    assert agent_cli.cmd_prompt("claude", late) == 2
    con = data.connect()
    assert not store.has_signals(con, "claude", env["as_of"])                    # 何も保存されていない
    con.close()


def test_duplicate_ticker_rejected(env):
    p = env["tmp"] / "dup.json"
    one = dict(model="claude", as_of=env["as_of"], ticker=env["ticker"], action="hold", confidence=0.5,
               reason="r", risk_notes="n")
    p.write_text(json.dumps([one, one]), encoding="utf-8")
    assert agent_cli.cmd_register("claude", str(p), "x", env["now"]) == 1



def test_too_early_is_refused(env):
    early = env["now"].replace(hour=18)                                  # 21:30より前
    assert agent_cli.cmd_prompt("claude", early) == 2
    assert agent_cli.cmd_register("claude", _answer(env), "x", early) == 1
    con = data.connect()
    assert not store.has_signals(con, "claude", env["as_of"])
    con.close()


def test_v2_rejects_tickers_outside_the_list(env, monkeypatch):
    from src import prompt
    monkeypatch.setattr(prompt, "START_V2", "2000-01-01")                # このテストでは新しい依頼文を使う
    assert agent_cli.cmd_prompt("claude", env["now"]) == 0
    text = (env["tmp"] / "agent" / f"prompt_claude_{env['as_of']}.txt").read_text(encoding="utf-8")
    assert "一覧にない銘柄は出力しないでください" in text
    assert agent_cli.cmd_register("claude", _answer(env, ticker="1003"), "x", env["now"]) == 1   # 一覧外
    assert agent_cli.cmd_register("claude", _answer(env, ticker="1001"), "x", env["now"]) == 0   # 一覧内
    con = data.connect()
    assert con.execute("SELECT prompt_version FROM signals").fetchone()[0] == prompt.version(env["as_of"])
    con.close()
