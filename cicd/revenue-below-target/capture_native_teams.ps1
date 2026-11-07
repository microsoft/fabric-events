param(
    [Parameter(Mandatory)][string]$StateFile,
    [Parameter(Mandatory)][long]$TeamsHandle,
    [Parameter(Mandatory)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
$state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
$run = $state.test
if (-not $run.region) { throw 'An existing explicit test run is required.' }
$window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$TeamsHandle)
if ($window.Current.Name -notmatch 'Fabric Activator') {
    throw 'Only the explicitly authorized Fabric Activator chat may be inspected.'
}
$cards = @($window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Group
    )
) | Where-Object {
    $_.Current.AutomationId -like 'message-body-*' -and
    $_.Current.Name -like "*$($run.region)*" -and
    $_.Current.Name -match 'This is a card message'
})
if ($cards.Count -ne 2) { throw "Exactly two actual cards for this unique run are required; found $($cards.Count)." }
$evidence = @()
foreach ($card in $cards) {
    $body = $card.Current.Name
    $fields = [ordered]@{}
    foreach ($field in @('region','period_start','revenue','target','variance_pct','detected_at')) {
        $match = [regex]::Match($body, [regex]::Escape($field) + ':\s+(\S+)')
        if (-not $match.Success) { throw "The actual Teams card is missing $field." }
        $value = $match.Groups[1].Value
        $fields[$field] = if ($field -in @('revenue','target','variance_pct')) {
            [double]::Parse($value, [Globalization.CultureInfo]::InvariantCulture)
        } else { $value }
    }
    if ($fields.region -ne $run.region -or $fields.period_start -ne $run.period_start) {
        throw 'The actual Teams card belongs to a different source context.'
    }
    $crossings = @($run.steps | Where-Object {
        $_.verified -and $_.expected_new_events -eq 1 -and
        $_.actual_native_snapshot.detected_at -eq $fields.detected_at -and
        $_.revenue -eq $fields.revenue -and $_.target -eq $fields.target -and
        $_.expected_variance_pct -eq $fields.variance_pct
    })
    if ($crossings.Count -ne 1) { throw 'The actual card is not tied to one verified native crossing.' }
    $evidence += [ordered]@{
        message_body_id = $card.Current.AutomationId
        native_card_text = $body
        actual_fields = $fields
    }
}
if (@($evidence | ForEach-Object { $_.actual_fields.detected_at } | Select-Object -Unique).Count -ne 2) {
    throw 'The actual Teams cards contain a duplicated crossing.'
}
[ordered]@{
    observed_at = [DateTime]::UtcNow.ToString('o')
    run_id = $run.run_id
    region = $run.region
    status = 'actual_authorized_teams_cards_and_full_native_snapshot_fields_verified'
    cards = $evidence
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output 'Two actual authorized Teams cards match the complete native crossing snapshots.'
