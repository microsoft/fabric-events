param(
    [Parameter(Mandatory)][string]$StateFile,
    [Parameter(Mandatory)][long]$BrowserHandle,
    [Parameter(Mandatory)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
$state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
$name = $state.configuration.resource_names.publisher
$w = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$BrowserHandle)
if ($w.Current.Name -notlike "$name - Activator*") {
    throw 'The supplied browser handle is not the exact owned native publisher page.'
}
function Find-OwnedControl {
    param([string]$Name,[string]$Type,$Root=$w)
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,$Name),
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::$Type)
    )
    $deadline=[DateTime]::UtcNow.AddSeconds(60)
    do {
        $found = @($Root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition))
        if ($found.Count -eq 1) { return $found[0] }
        if ($found.Count -gt 1) { throw "The exact owned $Type $Name is ambiguous." }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "The exact owned $Type $Name did not become available."
}
$address = (Find-OwnedControl 'Address and search bar' 'Edit').GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
$expected = "/groups/$($state.configuration.workspace_id)/reflexes/$($state.items.publisher)/models/$($state.test.source_view_id)"
$uri = [uri]$address
$hosts = if ($state.configuration.environment -eq 'msit') { @('msit.powerbi.com') } elseif ($state.configuration.environment -eq 'daily') { @('daily.powerbi.com') } else { @('app.fabric.microsoft.com','app.powerbi.com') }
if ($uri.Scheme -ne 'https' -or $uri.Host -notin $hosts -or $uri.UserInfo -or $uri.AbsolutePath -ne $expected) {
    throw 'The native browser route belongs to another source view; no unrelated page may be read.'
}
(Find-OwnedControl 'Refresh' 'Button').GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Seconds 12
$doc = Find-OwnedControl "$name - Activator — Power BI" 'Document'
$fields = @('Time','___id','___source','___type','Composite key','detected_at','period_start','region','revenue','target','variance_pct','System.IngestionTime')
$headers = @('Time Time','___id ___id','___source ___source','___type ___type','Composite key','detected_at detected_at','period_start period_start','region region','revenue revenue','target target','variance_pct variance_pct','System.IngestionTime System.IngestionTime')
$deadline=[DateTime]::UtcNow.AddSeconds(60)
do {
    $tables = $doc.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Table))
    $matches = @()
    foreach ($table in $tables) {
        $rows = $table.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        if ($rows.Count -eq 0) { continue }
        $actual = @($rows[0].FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current.Name})
        if (($actual -join "`n") -eq ($headers -join "`n")) { $matches += $table }
    }
    if ($matches.Count -eq 1) { break }
    if ($matches.Count -gt 1) { throw 'The complete native source table is ambiguous.' }
    Write-Output 'Waiting for the exact complete native source table to finish rendering.'
    Start-Sleep -Seconds 2
} while ([DateTime]::UtcNow -lt $deadline)
if ($matches.Count -ne 1) { throw 'The complete twelve-field genuine source table did not finish rendering.' }
$rows = $matches[0].FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$output = @()
$placeholders = 0
for ($index=1;$index -lt $rows.Count;$index++) {
    $cells = @($rows[$index].FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current.Name})
    if ($cells.Count -eq 0 -and $rows[$index].Current.Name -eq '') { $placeholders++; continue }
    if ($cells.Count -ne $fields.Count) { throw 'A real source row has incomplete context.' }
    $record = [ordered]@{}
    for ($column=0;$column -lt $fields.Count;$column++) { $record[$fields[$column]]=$cells[$column] }
    $output += [PSCustomObject]$record
}
[ordered]@{
    observed_at=[DateTime]::UtcNow.ToString('o')
    source_view=$state.test.source_view_id
    empty_render_placeholders=$placeholders
    actual_native_rows=$output
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output "Captured $($output.Count) complete native source rows; $placeholders empty render placeholders."
