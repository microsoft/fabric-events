param(
    [Parameter(Mandatory)][string]$StateFile,
    [long]$BrowserHandle=0,
    [long]$TeamsHandle=0,
    [switch]$Resume
)
$ErrorActionPreference = 'Stop'
$StateFile = (Resolve-Path -LiteralPath $StateFile).Path
$state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
if (-not $Resume) {
    python "$PSScriptRoot\native_test.py" init --state-file $StateFile
    if ($LASTEXITCODE -ne 0) { throw 'The explicit external baseline could not be initialized.' }
} elseif (-not $state.test) { throw 'There is no existing explicit test to resume.' }
$state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
Add-Type -AssemblyName UIAutomationClient
$name = $state.configuration.resource_names.publisher
if ($BrowserHandle -eq 0) {
    $hostName = if ($state.configuration.environment -eq 'msit') {'msit.powerbi.com'} elseif ($state.configuration.environment -eq 'daily') {'daily.powerbi.com'} else {'app.fabric.microsoft.com'}
    $url = "https://$hostName/groups/$($state.configuration.workspace_id)/reflexes/$($state.items.publisher)/models/$($state.test.source_view_id)"
    Start-Process msedge -ArgumentList '--new-window',$url
    $deadline=[DateTime]::UtcNow.AddMinutes(2)
    do {
        $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition
        ) | Where-Object {$_.Current.Name -like "$name - Activator*"})
        if ($windows.Count -eq 1) { $BrowserHandle=$windows[0].Current.NativeWindowHandle; break }
        if ($windows.Count -gt 1) { throw 'Supply the exact owned browser handle; duplicate source windows are ambiguous.' }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($BrowserHandle -eq 0) { throw 'The exact authenticated source page did not become available.' }
    Start-Sleep -Seconds 20
}
$output="$StateFile.source-latest.json"
$index=$state.test.steps.Count-1
if ($Resume -and $state.test.steps[$index].verified) {
    $index++
    if ($index -le 7) {
        python "$PSScriptRoot\native_test.py" step --state-file $StateFile --index $index
        if ($LASTEXITCODE -ne 0) { throw 'The next explicit source step could not be installed.' }
    }
}
try {
    while ($index -le 7) {
        $deadline=[DateTime]::UtcNow.AddMinutes(16)
        do {
            if ($index -lt 6) {
                & "$PSScriptRoot\capture_native_source.ps1" -StateFile $StateFile -BrowserHandle $BrowserHandle -OutputPath $output
                python "$PSScriptRoot\native_test.py" observe --state-file $StateFile --snapshot-file $output
            } else {
                python "$PSScriptRoot\native_test.py" observe --state-file $StateFile
            }
            if ($LASTEXITCODE -eq 0) { break }
            if ($LASTEXITCODE -ne 2) { throw "Actual native source/guard/publication verification failed at input $index." }
            if ([DateTime]::UtcNow -ge $deadline) { throw "Actual source/publication observation timed out at input $index." }
            Start-Sleep -Seconds 30
        } while ($true)
        $index++
        if ($index -le 7) {
            python "$PSScriptRoot\native_test.py" step --state-file $StateFile --index $index
            if ($LASTEXITCODE -ne 0) { throw "The explicit source step $index failed; do not replay it." }
        }
    }
    if ($TeamsHandle -eq 0) {
        $teams = @([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition
        ) | Where-Object {$_.Current.Name -match 'Fabric Activator.*Teams|Teams.*Fabric Activator'})
        if ($teams.Count -ne 1) { throw 'Open the authorized Fabric Activator Teams chat or supply its exact --teams-handle; no other chat is inspected.' }
        $TeamsHandle = $teams[0].Current.NativeWindowHandle
    }
    $teamsOutput = "$StateFile.teams.json"
    & "$PSScriptRoot\capture_native_teams.ps1" -StateFile $StateFile -TeamsHandle $TeamsHandle -OutputPath $teamsOutput
    python "$PSScriptRoot\native_test.py" teams --state-file $StateFile --teams-file $teamsOutput
    if ($LASTEXITCODE -ne 0) { throw 'The actual Teams evidence does not match both verified native crossings.' }
    $deadline = [DateTime]::UtcNow.AddMinutes(10)
    do {
        python "$PSScriptRoot\native_test.py" verify --state-file $StateFile
        if ($LASTEXITCODE -eq 0) { break }
        if ($LASTEXITCODE -ne 2) { throw 'The actual native pipeline and useful-output verification failed.' }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Actual native pipeline completion remains pending.' }
        Start-Sleep -Seconds 30
    } while ($true)
} catch {
    python "$PSScriptRoot\native_test.py" pause --state-file $StateFile
    throw
}
