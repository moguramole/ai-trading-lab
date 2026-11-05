# ポータルを止めて起動し直し、ブラウザで開く。
$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($c) { Stop-Process -Id ($c.OwningProcess | Select-Object -Unique) -Force; Start-Sleep -Seconds 2 }
Start-ScheduledTask -TaskName 'AI-Trading-Lab-Portal'
for ($i = 0; $i -lt 30; $i++) {
    if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) {
        Start-Process 'http://localhost:8501'
        Write-Host '起動しました'
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Host '起動を確認できませんでした。data\portal.log を確認してください'
exit 1