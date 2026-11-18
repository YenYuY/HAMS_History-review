[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string] $Source = 'Y:\',

    [Parameter(Position = 1)]
    [ValidateNotNullOrEmpty()]
    [string] $DestinationDir = 'C:\Temp'
)

$ErrorActionPreference = 'Stop'

function Quote-ProcessPath([string] $Path) {
    # In a Windows argv string, double trailing backslashes before a closing quote.
    return '"' + ($Path -replace '(\\+)$', '$1$1') + '"'
}

try {
    $python = Join-Path $PSScriptRoot 'runtime\python\python.exe'
    $script = Join-Path $PSScriptRoot 'sync_mdb.py'
    if (!(Test-Path -LiteralPath $python -PathType Leaf) -or
        !(Test-Path -LiteralPath $script -PathType Leaf)) {
        throw 'Extract the entire ZIP before installing the scheduled task.'
    }

    $Source = [IO.Path]::GetFullPath($Source)
    $DestinationDir = [IO.Path]::GetFullPath($DestinationDir)
    if (Test-Path -LiteralPath $DestinationDir -PathType Leaf) {
        throw "The destination must be a folder: $DestinationDir"
    }
    if (!(Test-Path -LiteralPath $Source)) {
        Write-Warning "Source unavailable: $Source. Check the Y: mapping and source path before running the task."
    }

    $arguments = '{0} --source {1} --destination-dir {2}' -f (Quote-ProcessPath $script),
        (Quote-ProcessPath $Source), (Quote-ProcessPath $DestinationDir)
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $PSScriptRoot

    $now = Get-Date
    $daysToSaturday = (7 + [int][DayOfWeek]::Saturday - [int]$now.DayOfWeek) % 7
    $nextRun = $now.Date.AddDays($daysToSaturday).AddHours(16)
    if ($nextRun -le $now) {
        $nextRun = $nextRun.AddDays(7)
    }
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At $nextRun
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    $taskName = 'HAMSAccessQuery Weekly Sync'
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force `
        -Description 'Copies the Access database and prepares the local SQLite index.' | Out-Null

    Write-Host "Installed: $taskName"
    Write-Host ('Next scheduled start: {0:yyyy-MM-dd HH:mm} (local Windows time)' -f $nextRun)
    Write-Host "Keep $user signed in with access to $Source at the scheduled time."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
