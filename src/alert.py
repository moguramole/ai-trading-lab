"""取得失敗の通知。アラートファイル(data/alert.json)とWindowsのトースト通知。

アラートは次の取得が成功すると消える。ポータルは全タブの上にこのファイルの内容を表示する。
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path


def write(path: Path, message: str, detail: list[str] | None = None, now: datetime | None = None):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"at": (now or datetime.now()).isoformat(timespec="seconds"),
                                "message": message, "detail": detail or []}, ensure_ascii=False),
                    encoding="utf-8")


def clear(path: Path):
    path.unlink(missing_ok=True)


def read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def notify(title: str, message: str) -> bool:
    """Windowsのトースト通知。表示できなければFalse（ポータルのバナーが本命なので失敗しても続行）。"""
    esc = lambda s: s.replace("'", "''").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('ToastText02');"
        f"$x.GetElementsByTagName('text').Item(0).AppendChild($x.CreateTextNode('{esc(title)}')) | Out-Null;"
        f"$x.GetElementsByTagName('text').Item(1).AppendChild($x.CreateTextNode('{esc(message)}')) | Out-Null;"
        "$n=[Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AI Trading Lab').Show($n)")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
