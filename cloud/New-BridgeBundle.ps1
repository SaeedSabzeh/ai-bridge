#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\cloud-output'),
    [ValidatePattern('^(HEAD|[0-9a-f]{7,40})$')]
    [string]$Revision = 'HEAD'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$git = Get-Command git.exe, git -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $git) { throw 'Git is required to package the committed source.' }
$commit = & $git -C $source rev-parse --verify "$Revision^{commit}"
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[a-f0-9]{40}$') { throw 'Git could not resolve the source revision.' }

# Deliberately explicit. In particular, Git-tracked fixtures or future files
# do not enter this bundle just because they are committed.
$sourceFiles = @(
    '.gitignore', 'AGENTS.md', 'CLAUDE.md', 'README.md', 'Launch AI Bridge.cmd',
    'ai_bridge.py', 'bridge_desktop.pyw', 'lead_run.py', 'lead_tools.py',
    'windows_job.py', 'workspace_state.py', 'cloud/README.md'
)
$folder = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
$path = Join-Path $folder "ai-bridge-cloud-$($commit.Substring(0,12)).zip"
if (Test-Path -LiteralPath $path) { throw "Bundle already exists: $path" }
$temp = Join-Path $folder ('.ai-bridge-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    & $git -C $source archive --format=zip --prefix=ai-bridge/ "--output=$temp" $commit -- @sourceFiles
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed. Commit the allowlisted files and try again.' }
    Move-Item -LiteralPath $temp -Destination $path
}
finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
}
Get-FileHash -LiteralPath $path -Algorithm SHA256 | Select-Object Algorithm,Hash,Path
