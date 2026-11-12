param([Parameter(Mandatory = $true)][string]$Recipient)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$scope = [System.Windows.Automation.TreeScope]
$condition = [System.Windows.Automation.Condition]::TrueCondition
$root = [System.Windows.Automation.AutomationElement]::RootElement
$candidates = @()
foreach ($window in $root.FindAll($scope::Children, $condition)) {
    if ($window.Current.ClassName -ne 'TeamsWebView' -or $window.Current.Name -notlike '*Fabric Activator*') {
        continue
    }
    $profiles = @($window.FindAll($scope::Descendants, $condition) | Where-Object {
        $_.Current.AutomationId -eq 'idna-me-control-avatar-trigger'
    })
    if ($profiles.Count -eq 1) {
        $candidates += [pscustomobject]@{ Window = $window; Profile = $profiles[0] }
    }
}
if ($candidates.Count -ne 1) {
    throw 'Open one Fabric Activator conversation in the Windows Teams desktop app, with its profile control accessible.'
}
$window = $candidates[0].Window
$pattern = $candidates[0].Profile.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
$original = $pattern.Current.ExpandCollapseState
if ($original -ne [System.Windows.Automation.ExpandCollapseState]::Expanded) { $pattern.Expand() }
try {
    Start-Sleep -Milliseconds 700
    $menus = @($window.FindAll($scope::Descendants, $condition) | Where-Object {
        $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window -and
        $_.Current.Name -eq 'Profile menu'
    })
    if ($menus.Count -ne 1) { throw 'Cannot independently locate the visible Teams profile menu. Use English Teams UI.' }
    $accounts = @($menus[0].FindAll($scope::Descendants, $condition) | Where-Object {
        $_.Current.AutomationId -eq 'me-control-sign-out-button'
    })
    if ($accounts.Count -ne 1 -or $accounts[0].Current.Name -notmatch '[A-Za-z0-9_.%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}') {
        throw 'Cannot independently read the selected account from the Teams profile.'
    }
    $actualRecipient = $Matches[0]
    if ($actualRecipient -ine $Recipient) {
        throw 'The visible Teams account differs from the approved Fabric recipient.'
    }
} finally {
    if ($original -ne [System.Windows.Automation.ExpandCollapseState]::Expanded) { $pattern.Collapse() }
}
$cards = @()
foreach ($element in $window.FindAll($scope::Descendants, $condition)) {
    $id = $element.Current.AutomationId
    $text = $element.Current.Name
    if ($id -notmatch '^message-body-\d+$' -or -not $text.Trim().StartsWith('This is a card message.') -or
        -not $text.Contains('via Fabric Activator') -or -not $text.Contains('View rule in Activator')) {
        continue
    }
    $cards += [pscustomobject]@{ id = $id; actual_card_text = $text }
}
[pscustomobject]@{
    origin = 'actual_rendered_teams_cards'
    recipient = $actualRecipient
    recipient_verified_from_visible_teams_profile = $true
    cards = @($cards)
} | ConvertTo-Json -Depth 5 -Compress
