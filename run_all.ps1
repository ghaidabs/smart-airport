# Lance les 4 nœuds dans des processus séparés, version Windows de run_all.sh (P1).
#
#   .\run_all.ps1               # lance les nœuds, Ctrl+C pour tout arrêter
#   .\run_all.ps1 -KeepLogs     # ne vide pas logs\*.csv au démarrage
#   .\run_all.ps1 -Python py    # autre interpréteur Python
#
# Si PowerShell refuse les scripts :
#   powershell -ExecutionPolicy Bypass -File .\run_all.ps1
#
# Puis, dans un autre terminal :
#   python -m scenarios.inject tu701
#   python -m tools.timeline
#
# Pour la démo finale et les mesures M7, utiliser run_all.sh sous Linux/WSL
# (l'horloge de Windows est trop grossière pour mesurer des latences < 1 ms).

param(
    [switch]$KeepLogs,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$nodes = @("flight", "ai", "decision", "passenger")

New-Item -ItemType Directory -Force -Path logs | Out-Null
if (-not $KeepLogs) {
    Remove-Item logs\*.csv -ErrorAction SilentlyContinue
}

$procs = @()
try {
    foreach ($node in $nodes) {
        $procs += Start-Process -FilePath $Python -ArgumentList "-m", "nodes.${node}_node" `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput "logs\$node.out" -RedirectStandardError "logs\$node.err"
    }

    & $Python -m common.net 10
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Un nœud n'a pas démarré, voir logs\*.err" -ForegroundColor Red
        exit 1
    }
    Write-Host "4 nœuds prêts (ports 5001-5004). Ctrl+C pour arrêter." -ForegroundColor Green
    Write-Host "Sorties console : logs\<nœud>.out / .err ; journaux : logs\<nœud>.csv"

    # Surveille les nœuds : si l'un s'arrête, on le signale et on arrête tout.
    while ($true) {
        foreach ($i in 0..($procs.Count - 1)) {
            if ($procs[$i].HasExited) {
                Write-Host "Le nœud $($nodes[$i]) s'est arrêté, voir logs\$($nodes[$i]).err" `
                    -ForegroundColor Red
                exit 1
            }
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    # Exécuté aussi sur Ctrl+C : n'arrête que les processus lancés par ce script.
    Write-Host "`nArrêt des nœuds..."
    foreach ($p in $procs) {
        if (-not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
