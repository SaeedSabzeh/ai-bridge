#Requires -Version 5.1
# Exercises the provisioning script without a Google Cloud CLI or account.
$ErrorActionPreference = 'Stop'
$provision = Join-Path $PSScriptRoot '..\cloud\New-BridgeVm.ps1'
$originalPath = $env:Path
$record = @{ Calls = [System.Collections.Generic.List[string]]::new(); Collision = $false }
try {
    $env:Path = ''
    if (Get-Command gcloud.cmd, gcloud.exe, gcloud -CommandType Application -ErrorAction SilentlyContinue) {
        throw 'An actual Google Cloud CLI is still visible. Refusing to run this test.'
    }
    function gcloud {
        $line = $args -join ' '
        if ($line -notmatch '(?:^| )--project=bridge-test(?: |$)') { throw "Unscoped command: $line" }
        $record.Calls.Add($line)
        $global:LASTEXITCODE = 0
        if ($line -like 'projects describe *') { 'bridge-test' }
        if ($record.Collision -and $line -like 'compute instances list *') { 'ai-bridge' }
    }

    $options = @{ Project = 'bridge-test'; Zone = 'europe-west8-b' }
    $null = & $provision @options
    if ($record.Calls.Count -ne 0) { throw 'The plan contacted Google Cloud.' }

    $null = & $provision @options -Apply
    $created = $record.Calls | Where-Object { $_ -match '\bcreate\b' }
    $instance = $created | Where-Object { $_ -like 'compute instances create *' }
    if ($created.Count -ne 6 -or $instance -notmatch '--no-address --no-service-account --no-scopes' -or
        $instance -notmatch '--no-boot-disk-auto-delete --deletion-protection') {
        throw 'The VM did not have the expected private configuration and disk protection.'
    }
    $ingress = $created | Where-Object { $_ -like 'compute firewall-rules create *' }
    if ($ingress -notmatch '--source-ranges=35\.235\.240\.0/20' -or $ingress -match '0\.0\.0\.0/0') {
        throw 'RDP was not restricted to IAP.'
    }
    $record.Calls.Clear()
    $record.Collision = $true
    try {
        $null = & $provision @options -Apply
        throw 'The existing instance did not stop provisioning.'
    }
    catch {
        if ($_.Exception.Message -notlike 'A resource already matches this name:*') { throw }
    }
    if ($record.Calls | Where-Object { $_ -match '\bcreate\b' }) {
        throw 'The script tried to create resources despite a name collision.'
    }
    'Cloud plan and mocked provisioning passed.'
}
finally {
    $env:Path = $originalPath
}
