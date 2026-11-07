#Requires -Version 5.1
<#
.SYNOPSIS
Prints an isolated Google Compute Engine plan. Changes require -Apply.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$Project,
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z]+(?:-[a-z]+)*[0-9]+-[a-z]$')]
    [string]$Zone,
    [ValidatePattern('^[a-z](?:[a-z0-9-]{0,22}[a-z0-9])?$')]
    [string]$Name = 'ai-bridge',
    [ValidateSet('e2-standard-2', 'e2-standard-4', 'e2-standard-8')]
    [string]$MachineType = 'e2-standard-2',
    [ValidateRange(50, 2048)]
    [int]$DiskSizeGb = 80,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$region = $Zone.Substring(0, $Zone.LastIndexOf('-'))
$network = "$Name-vpc"
$subnet = "$Name-subnet"
$router = "$Name-router"
$nat = "$Name-nat"
$firewall = "$Name-iap-rdp"
$projectFlag = "--project=$Project"

$steps = @(
    @{ Label = 'Enable Compute Engine and IAP'; Args = @(
        'services', 'enable', 'compute.googleapis.com', 'iap.googleapis.com', $projectFlag) }
    @{ Label = 'Create an isolated VPC'; Args = @(
        'compute', 'networks', 'create', $network, '--subnet-mode=custom', $projectFlag) }
    @{ Label = 'Create the VM subnet'; Args = @(
        'compute', 'networks', 'subnets', 'create', $subnet, "--network=$network",
        "--region=$region", '--range=10.76.0.0/24', '--enable-private-ip-google-access', $projectFlag) }
    @{ Label = 'Allow RDP from Google IAP only'; Args = @(
        'compute', 'firewall-rules', 'create', $firewall, "--network=$network",
        '--direction=INGRESS', '--action=ALLOW', '--rules=tcp:3389',
        '--source-ranges=35.235.240.0/20', "--target-tags=$firewall", $projectFlag) }
    @{ Label = 'Create the NAT router'; Args = @(
        'compute', 'routers', 'create', $router, "--network=$network", "--region=$region", $projectFlag) }
    @{ Label = 'Provide outbound internet without a public VM address'; Args = @(
        'compute', 'routers', 'nats', 'create', $nat, "--router=$router", "--region=$region",
        "--nat-custom-subnet-ip-ranges=$subnet", '--auto-allocate-nat-external-ips', $projectFlag) }
    @{ Label = 'Create the protected Windows VM'; Args = @(
        'compute', 'instances', 'create', $Name, "--zone=$Zone", "--machine-type=$MachineType",
        "--subnet=$subnet", "--tags=$firewall", '--no-address', '--no-service-account', '--no-scopes',
        '--image-family=windows-2022', '--image-project=windows-cloud',
        "--boot-disk-size=${DiskSizeGb}GB", '--boot-disk-type=pd-balanced',
        '--no-boot-disk-auto-delete', '--deletion-protection',
        '--shielded-secure-boot', '--shielded-vtpm', '--shielded-integrity-monitoring',
        '--maintenance-policy=MIGRATE', '--metadata=serial-port-enable=FALSE',
        $projectFlag) }
)

if (-not $Apply) {
    Write-Output "PLAN ONLY: $Project / $Zone / $Name. No Google Cloud calls were made."
    foreach ($step in $steps) {
        Write-Output $step.Label
        Write-Output ('  gcloud ' + ($step.Args -join ' '))
    }
    Write-Output 'Billable resources: Windows VM and license, persistent disk, NAT and its IP, and applicable traffic.'
    Write-Output 'IAP access is granted separately to the intended Google identity on this VM only.'
    return
}

$gcloud = Get-Command gcloud.cmd, gcloud.exe, gcloud -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $gcloud) { throw 'Install and sign in to the Google Cloud CLI before running -Apply.' }

function Invoke-Cloud([string[]]$Command) {
    & $gcloud @Command
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed (exit $LASTEXITCODE): $($Command -join ' '). Inspect this project for any resources already created. No rollback or deletion was attempted."
    }
}

$actualProject = Invoke-Cloud @('projects', 'describe', $Project, $projectFlag, '--format=value(projectId)')
if (($actualProject -join '').Trim() -cne $Project) { throw 'Google Cloud returned a different project ID.' }
Invoke-Cloud ($steps[0].Args + '--quiet')
# A clean, dedicated set of names keeps the script from reusing or changing
# another deployment. If a create later fails, review the partial deployment.
$checks = @(
    @{ Args = @('compute', 'networks', 'list', "--filter=name=$network") }
    @{ Args = @('compute', 'networks', 'subnets', 'list', "--filter=name=$subnet") }
    @{ Args = @('compute', 'routers', 'list', "--filter=name=$router") }
    @{ Args = @('compute', 'firewall-rules', 'list', "--filter=name=$firewall") }
    @{ Args = @('compute', 'instances', 'list', "--filter=name=$Name") }
    @{ Args = @('compute', 'disks', 'list', "--filter=name=$Name") }
)
foreach ($check in $checks) {
    $found = Invoke-Cloud ($check.Args + @($projectFlag, '--format=value(name)'))
    if (($found -join '').Trim()) {
        throw "A resource already matches this name: $($found -join ', '). Review the existing deployment before continuing."
    }
}
# Fail before creating billable resources if the chosen zone lacks the shape.
$null = Invoke-Cloud @('compute', 'machine-types', 'describe', $MachineType, "--zone=$Zone",
                       $projectFlag, '--format=value(name)')
foreach ($step in $steps | Select-Object -Skip 1) {
    Write-Host $step.Label
    Invoke-Cloud ($step.Args + '--quiet')
}

Write-Output "VM created: $Name. Grant per-instance IAP access and create a Windows login before opening RDP."
Write-Output "Tunnel: gcloud compute start-iap-tunnel $Name 3389 --local-host-port=127.0.0.1:13389 --zone=$Zone $projectFlag"
