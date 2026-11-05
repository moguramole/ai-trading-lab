"""クラウド用: answers/ に置かれた判断JSONを、最新の株価日のぶんだけ登録する。 python -m src.cloud_register [answers]

ファイル名は answer_<gpt|claude>_<判断日>.json。ポータル・agent_cliと同じ検証（銘柄・日付・締め切り・
1日1回）を通る。登録済み・締め切り前後で拒否された場合はそのまま見送り、次の実行で再試行する。
判断日が最新の株価日と違うファイルは触らない。
"""
import re
import sys
from pathlib import Path

from . import agent_cli, data, store

NAME = re.compile(r"^answer_(gpt|claude)_(\d{4}-\d{2}-\d{2})\.json$")


def run(folder="answers", llm_suffix="(cloud登録)") -> int:
    folder = Path(folder)
    if not folder.is_dir():
        print("answers フォルダがありません。登録するものはありません")
        return 0
    con = data.connect()
    try:
        latest = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    finally:
        con.close()
    latest = str(latest)[:10]
    failed = 0
    for f in sorted(folder.glob("answer_*.json")):
        m = NAME.match(f.name)
        if not m or m.group(2) != latest:
            continue
        model = m.group(1)
        con = data.connect()
        try:
            if store.has_signals(con, model, latest):
                print(f"{f.name}: 登録済み")
                continue
        finally:
            con.close()
        rc = agent_cli.cmd_register(model, str(f), f"{model}{llm_suffix}")
        failed += rc != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run(*(sys.argv[1:2])))
