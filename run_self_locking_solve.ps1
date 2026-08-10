$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$job = "self_locking_pullout_explicit"
# Set ABQ_BAT or edit here to point at your Abaqus launcher.
$abaqus = if ($env:ABQ_BAT) { $env:ABQ_BAT } else { "D:\SIMULIA\Commands\abaqus.bat" }
$inp = Join-Path $root "$job.inp"
$sta = Join-Path $root "$job.sta"
$log = Join-Path $root "$job.log"

if (-not (Test-Path -LiteralPath $inp)) {
  throw "Input file not found: $inp. Run Abaqus CAE noGUI=self_locking_needle_pullout.py first."
}

foreach ($ext in @(".lck")) {
  $p = Join-Path $root "$job$ext"
  if (Test-Path -LiteralPath $p) {
    Remove-Item -LiteralPath $p -Force
  }
}

Write-Host "Starting Abaqus Explicit job: $job"
$args = "job=$job input=`"$inp`" cpus=4 memory=85% interactive"
$process = Start-Process -FilePath $abaqus -ArgumentList $args -WorkingDirectory $root -PassThru -WindowStyle Hidden

$lastLine = ""
while (-not $process.HasExited) {
  if (Test-Path -LiteralPath $sta) {
    $tail = Get-Content -LiteralPath $sta -Tail 8 -ErrorAction SilentlyContinue
    $line = ($tail | Where-Object { $_ -match "^\s*\d+\s+" } | Select-Object -Last 1)
    if ($line -and $line -ne $lastLine) {
      Write-Host $line
      $lastLine = $line
    }
    $done = $tail | Where-Object { $_ -match "THE ANALYSIS HAS COMPLETED SUCCESSFULLY|Abaqus/Analysis exited with errors|ERROR" }
    if ($done) {
      Write-Host ($done -join "`n")
    }
  } elseif (Test-Path -LiteralPath $log) {
    Get-Content -LiteralPath $log -Tail 4 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
  } else {
    Write-Host "Waiting for status file..."
  }
  Start-Sleep -Seconds 10
  $process.Refresh()
}

Write-Host "Abaqus launcher exited with code $($process.ExitCode)"
if (Test-Path -LiteralPath $sta) {
  Write-Host "Final status:"
  Get-Content -LiteralPath $sta -Tail 30
}
exit $process.ExitCode
