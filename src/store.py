"""AI判断(Signal)のSQLite永続化。画面を開き直しても判断履歴が残る。"""
from datetime import datetime, timezone

from .ai_adapters import Signal
from .sim import order_id


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS signals (
        order_id TEXT PRIMARY KEY, model TEXT, as_of TEXT, ticker TEXT, action TEXT,
        confidence REAL, reason TEXT, risk_notes TEXT, qty INTEGER, stop_price REAL,
        registered_at TEXT)""")
    if "prompt_version" not in {r[1] for r in con.execute("PRAGMA table_info(signals)")}:
        con.execute("ALTER TABLE signals ADD COLUMN prompt_version TEXT")  # 既存の判断はNULL（版は未記録）
    cols = {r[1] for r in con.execute("PRAGMA table_info(signals)")}
    for c in ("source", "llm"):  # 判断の出どころ（portal=人が貼った / agent=判断用エージェント）と、申告されたモデル名
        if c not in cols:
            con.execute(f"ALTER TABLE signals ADD COLUMN {c} TEXT")
    con.execute("""CREATE TABLE IF NOT EXISTS prompt_versions (
        version TEXT PRIMARY KEY, template TEXT, first_seen_at TEXT)""")


def save_signals(con, signals: list[Signal], prompt_version: str | None = None,
                 template: str | None = None, source: str | None = None, llm: str | None = None) -> int:
    """登録件数を返す。同じ注文ID（モデル|判断日|銘柄|売買）は無視して重複登録しない。

    prompt_version: 判断に使った依頼文の版。templateを添えると、その版の全文も初回だけ保存する。
    """
    ensure(con)
    now = datetime.now(timezone.utc).isoformat()
    if prompt_version and template:
        con.execute("INSERT OR IGNORE INTO prompt_versions VALUES (?,?,?)", (prompt_version, template, now))
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO signals (order_id,model,as_of,ticker,action,confidence,reason,risk_notes,"
        "qty,stop_price,registered_at,prompt_version,source,llm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(order_id(s), s.model, s.as_of, s.ticker, s.action, s.confidence, s.reason,
          s.risk_notes, s.qty, s.stop_price, now, prompt_version, source, llm) for s in signals])
    con.commit()
    return con.total_changes - before


def void_signals(con, model: str, as_of: str, reason: str) -> int:
    """そのモデル・判断日の判断を「無効」にして、登録から外す。件数を返す。

    追記専用の原則の例外。約定前（まだ取引履歴に出ていない）の判断を、ルール変更で出し直すときだけ使う。
    消さずに signals_voided へ全項目を退避する（理由と日時つき）。約定済みの判断は拒否する。
    """
    ensure(con)
    ensure_ledger(con)
    like = f"{model}|{as_of}|%"
    if con.execute("SELECT 1 FROM trades WHERE order_id LIKE ? LIMIT 1", (like,)).fetchone() or \
            con.execute("SELECT 1 FROM rejections WHERE order_id LIKE ? LIMIT 1", (like,)).fetchone():
        raise ValueError("すでに約定・拒否の記録があるため無効にできません")
    cols = [r[1] for r in con.execute("PRAGMA table_info(signals)")]
    con.execute("CREATE TABLE IF NOT EXISTS signals_voided AS SELECT *, '' AS voided_at, '' AS void_reason "
                "FROM signals WHERE 0")
    have = {r[1] for r in con.execute("PRAGMA table_info(signals_voided)")}
    for c in cols:  # 後から signals に列が増えても退避できるようにする
        if c not in have:
            con.execute(f"ALTER TABLE signals_voided ADD COLUMN {c} TEXT")
    names = ",".join(cols)
    con.execute(f"INSERT INTO signals_voided ({names}, voided_at, void_reason) "
                f"SELECT {names}, ?, ? FROM signals WHERE model=? AND as_of=?",
                (datetime.now(timezone.utc).isoformat(), reason, model, as_of))
    n = con.execute("DELETE FROM signals WHERE model=? AND as_of=?", (model, as_of)).rowcount
    con.commit()
    return n


def has_signals(con, model: str, as_of: str) -> bool:
    """そのモデルがその判断日にすでに判断を登録しているか。"""
    ensure(con)
    return con.execute("SELECT 1 FROM signals WHERE model=? AND as_of=? LIMIT 1", (model, as_of)).fetchone() is not None


def source_summary(con) -> list[tuple]:
    """(モデル, 出どころ, 申告モデル名, 判断日の数)。出どころ未記録の判断はNULLの行。"""
    ensure(con)
    return con.execute("SELECT model, source, llm, COUNT(DISTINCT as_of) FROM signals "
                       "GROUP BY model, source, llm ORDER BY model, MIN(registered_at)").fetchall()


def prompt_version_summary(con) -> list[tuple]:
    """(版, 初めて使った日時, その版で登録した判断の件数)。版が未記録の判断は版=NULLの行にまとめる。"""
    ensure(con)
    return con.execute(
        "SELECT s.prompt_version, p.first_seen_at, COUNT(*) FROM signals s "
        "LEFT JOIN prompt_versions p ON p.version = s.prompt_version "
        "GROUP BY s.prompt_version ORDER BY MIN(s.registered_at)").fetchall()


def ensure_ledger(con):
    con.execute("""CREATE TABLE IF NOT EXISTS trades (
        order_id TEXT PRIMARY KEY, model TEXT, ticker TEXT, side TEXT, qty INTEGER,
        price REAL, fee REAL, trade_date TEXT, decided_at TEXT, reason TEXT, recorded_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS rejections (
        order_id TEXT PRIMARY KEY, model TEXT, reason TEXT, recorded_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS equity_daily (
        model TEXT, date TEXT, equity REAL, PRIMARY KEY (model, date))""")


def record_ledger(con, accounts) -> dict:
    """約定・拒否・日次資産を記録する。accounts: {model: (broker, equity_curve)}

    注文IDが主キーで INSERT OR IGNORE のため、一度記録した約定は書き換わらず、
    何度呼んでも二重登録されない（記録は追記専用）。戻り値は新規に記録した件数。
    """
    ensure_ledger(con)
    now = datetime.now(timezone.utc).isoformat()
    before = con.total_changes
    for model, (broker, curve) in accounts.items():
        con.executemany(
            "INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(t.order_id, model, t.ticker, t.side, t.qty, t.price, t.fee, t.trade_date,
              t.decided_at, t.reason, now) for t in broker.trades])
        con.executemany("INSERT OR IGNORE INTO rejections VALUES (?,?,?,?)",
                        [(r.order_id, model, r.reason, now) for r in broker.rejections])
        con.executemany("INSERT OR IGNORE INTO equity_daily VALUES (?,?,?)",
                        [(model, d.strftime("%Y-%m-%d"), float(e)) for d, e in zip(curve["date"], curve["equity"])])
    con.commit()
    return {"new_rows": con.total_changes - before}


def ledger_mismatches(con, accounts) -> list[str]:
    """記録済みの約定と、いまの再計算結果が食い違う注文ID。

    ルールや株価データが後から変わった・手で書き換えた場合に気付くための点検。
    記録済みなのに再計算で消えた約定も含む。
    """
    ensure_ledger(con)
    stored = {r[0]: r[1:] for r in con.execute(
        "SELECT order_id,model,ticker,side,qty,price,fee,trade_date FROM trades")}
    replay = {t.order_id: (m, t.ticker, t.side, t.qty, t.price, t.fee, t.trade_date)
              for m, (b, _) in accounts.items() for t in b.trades}
    bad = []
    for oid, s in stored.items():
        r = replay.get(oid)
        if r is None or r[:4] != s[:4] or r[6] != s[6] or abs(r[4] - s[4]) > 1e-6 or abs(r[5] - s[5]) > 1e-6:
            bad.append(oid)
    return sorted(bad)


def load_signals(con, model: str) -> list[Signal]:
    ensure(con)
    rows = con.execute(
        "SELECT model,as_of,ticker,action,confidence,reason,risk_notes,qty,stop_price "
        "FROM signals WHERE model=? ORDER BY as_of, ticker", (model,)).fetchall()
    return [Signal(*r) for r in rows]


