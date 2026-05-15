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

$ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$stamp = $ts -replace '[: ]', '_'
$OutLog = "$ProjectDir\.tmp\uvicorn_${stamp}.out"
$ErrLog = "$ProjectDir\.tmp\uvicorn_${stamp}.err"

# -u = unbuffered Python I/O so crash tracebacks flush to files immediately
try {
    $proc = Start-Process `
        -FilePath $VenvPython `
        -ArgumentList "-u -m uvicorn tools.api:app --port 8000 --host 0.0.0.0 --log-level info" `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError  $ErrLog `
        -PassThru
    Write-Log "uvicorn started (PID $($proc.Id)) err=$ErrLog"
} catch {
    Write-Log "ERROR starting uvicorn: $_"
}
