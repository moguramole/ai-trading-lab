# ポータルを起動する（タスクスケジューラのログオン時タスクと、手動起動の両方から使う）。
# すでに 8501 番ポートで動いていれば何もしない（二重起動の防止）。
$ErrorActionPreference = "Stop"   # 失敗したら終了コードに反映し、タスクの結果で気付けるようにする
$root = $PSScriptRoot
Set-Location $root

$port = 8501
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { exit 0 }

New-Item -ItemType Directory -Force "$root\data" | Out-Null
& "$root\.venv\Scripts\streamlit.exe" run app.py --server.port $port --server.address localhost --server.headless true `
    --browser.gatherUsageStats false *> "$root\data\portal.log"
exit $LASTEXITCODE
