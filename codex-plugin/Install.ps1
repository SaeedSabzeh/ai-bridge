#Requires -Version 5.1
[CmdletBinding()]
param([switch]$BuildOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$bridge = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Get-Command python.exe, python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $python) { throw 'Python is required to run the local bridge.' }
$pythonPath = & $python.Source -c 'import sys; print(sys.executable)'
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Could not locate the Python executable.'
}

$marketplace = Join-Path $bridge 'codex-plugin-build'
$package = Join-Path $marketplace 'claude-bridge'
$manifestFolder = Join-Path $package '.codex-plugin'
$catalogFolder = Join-Path $marketplace '.agents/plugins'
$null = New-Item -ItemType Directory -Path $manifestFolder, $catalogFolder -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.codex-plugin/plugin.json') -Destination $manifestFolder -Force

function Write-Json($Path, $Value) {
    [System.IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))
}

Write-Json (Join-Path $package '.mcp.json') @{
    mcpServers = @{
        'claude-bridge' = @{
            command = $pythonPath
            args = @((Join-Path $bridge 'ai_bridge.py'), '--data-dir', $bridge,
                     'mcp', '--identity', 'chatgpt', '--conversation-view')
            cwd = $bridge
        }
    }
}
Write-Json (Join-Path $catalogFolder 'marketplace.json') @{
    name = 'ai-bridge-local'
    interface = @{ displayName = 'AI Bridge Local' }
    plugins = @(@{
        name = 'claude-bridge'
        source = @{ source = 'local'; path = './claude-bridge' }
        policy = @{ installation = 'AVAILABLE'; authentication = 'ON_INSTALL' }
        category = 'Productivity'
    })
}

Write-Output "Local marketplace prepared at $marketplace"
if ($BuildOnly) { return }

$resolved = & $pythonPath -c 'import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from ai_bridge import config_for, executable_for; base=Path(sys.argv[1]); print(json.dumps(executable_for("chatgpt", base, config_for(base))))' $bridge
if ($LASTEXITCODE -ne 0) { throw 'Could not locate Codex. Run python ai_bridge.py doctor for details.' }
$codex = @($resolved | ConvertFrom-Json)
$program = $codex[0]
$prefix = @($codex | Select-Object -Skip 1)
& $program @prefix plugin marketplace add $marketplace --json
if ($LASTEXITCODE -ne 0) { throw 'Could not register the local marketplace.' }
& $program @prefix plugin add 'claude-bridge@ai-bridge-local' --json
if ($LASTEXITCODE -ne 0) { throw 'Could not install Claude Bridge.' }
