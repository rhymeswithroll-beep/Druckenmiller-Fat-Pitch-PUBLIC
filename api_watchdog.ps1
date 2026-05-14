# Druckenmiller API Watchdog
$ProjectDir = "C:\Users\rhyme\OneDrive\Desktop\Claude Code\Druckenmiller-Fat-Pitch-PUBLIC"
$VenvPython = "$ProjectDir\venv\Scripts\python.exe"
$LogFile    = "$ProjectDir\.tmp\api_watchdog.log"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "$ts  $msg" -Encoding UTF8
}

$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listening) { exit 0 }

Write-Log "API not on port 8000 - starting uvicorn..."

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName         = $VenvPython
$psi.Arguments        = "-m uvicorn tools.api:app --port 8000 --host 0.0.0.0"
$psi.WorkingDirectory = $ProjectDir
$psi.WindowStyle      = [System.Diagnostics.ProcessWindowStyle]::Hidden
$psi.UseShellExecute  = $true

try {
    $proc = [System.Diagnostics.Process]::Start($psi)
    Write-Log "uvicorn started."
} catch {
    Write-Log "ERROR starting uvicorn."
}
