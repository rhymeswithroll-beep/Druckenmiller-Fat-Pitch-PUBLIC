# Druckenmiller Dashboard Watchdog
$ProjectDir = "C:\Users\rhyme\OneDrive\Desktop\Claude Code\Druckenmiller-Fat-Pitch-PUBLIC\dashboard"
$LogFile    = "C:\Users\rhyme\OneDrive\Desktop\Claude Code\Druckenmiller-Fat-Pitch-PUBLIC\.tmp\dashboard_watchdog.log"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "$ts  $msg" -Encoding UTF8
}

$listening = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
if ($listening) { exit 0 }

Write-Log "Dashboard not on port 3001 - starting Next.js..."

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName         = "cmd.exe"
$psi.Arguments        = "/c npm run dev -- --port 3001"
$psi.WorkingDirectory = $ProjectDir
$psi.WindowStyle      = [System.Diagnostics.ProcessWindowStyle]::Hidden
$psi.UseShellExecute  = $true

try {
    $proc = [System.Diagnostics.Process]::Start($psi)
    Write-Log "Next.js started."
} catch {
    Write-Log "ERROR starting Next.js."
}
