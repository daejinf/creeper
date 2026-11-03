[CmdletBinding(PositionalBinding=$false)]
param(
    [switch]$Web,
    [int]$Port = 8010,
    [string]$PythonPath,
    [Parameter(Position=0, ValueFromRemainingArguments=$true)]
    [string[]]$CoreArgs
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $taskRoot '.venv\Scripts\python.exe'),
        (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
$searchPaths = @((Join-Path $taskRoot 'src'))
if (Test-Path -LiteralPath (Join-Path $taskRoot '.deps')) { $searchPaths += (Join-Path $taskRoot '.deps') }
$env:PYTHONPATH = $searchPaths -join [IO.Path]::PathSeparator
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User') }
if ($Web) {
    & $PythonPath -m uvicorn wemeet_ai.adapters.web:app --host 127.0.0.1 --port $Port
} else {
    if (-not $CoreArgs) { $CoreArgs = @('--help') }
    & $PythonPath -m wemeet_ai @CoreArgs
}
exit $LASTEXITCODE
