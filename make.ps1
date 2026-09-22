<#
.SYNOPSIS
    PowerShell stand-in for GNU make, which Windows does not ship.

.DESCRIPTION
    Mirrors the targets in ./Makefile so the documented commands work on
    Windows without installing make. Keep the two in sync: the Makefile is the
    reference, this is the shim.

.EXAMPLE
    ./make.ps1 up
    ./make.ps1 eval -Config configs/hybrid.yaml -Split dev -Mode retrieval
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help',

    [string]$Config = 'configs/baseline.yaml',
    [string]$Split = 'dev',
    [string]$Mode = 'retrieval',
    [string]$M = 'migration'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'

function Invoke-Step {
    param([string]$Command, [string[]]$Arguments, [string]$WorkingDirectory = $RepoRoot)
    Push-Location $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Command exited with $LASTEXITCODE" }
    }
    finally { Pop-Location }
}

function Invoke-Backend {
    param([string[]]$Arguments)
    Invoke-Step -Command 'uv' -Arguments (@('run') + $Arguments) -WorkingDirectory $BackendDir
}

function Wait-Health {
    Write-Host 'Waiting for the API to become healthy...'
    foreach ($i in 1..40) {
        try {
            $r = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 3
            $r | ConvertTo-Json -Depth 5
            return
        }
        catch { Start-Sleep -Seconds 2 }
    }
    throw 'API did not become healthy in 80s'
}

switch ($Target) {
    'help' {
        Write-Host 'GroundTruth targets (see ./Makefile for the reference definitions):'
        @(
            @('install', 'Create the venv and install backend deps'),
            @('up', 'Start Postgres + backend, then run migrations'),
            @('down', 'Stop the stack (data volumes survive)'),
            @('logs', 'Tail backend logs'),
            @('ps', 'Show container status'),
            @('dev', 'Run the API natively (no container)'),
            @('migrate', 'Apply Alembic migrations'),
            @('revision', 'Autogenerate a migration: ./make.ps1 revision -M "add chunks"'),
            @('health', 'Poll /health until the API answers'),
            @('test', 'Run the full test suite'),
            @('test-unit', 'Run tests that need no database'),
            @('lint', 'Lint with ruff'),
            @('fmt', 'Format with ruff'),
            @('typecheck', 'Type-check with mypy'),
            @('check', 'Everything CI runs'),
            @('ingest', 'Ingest the corpus'),
            @('eval', 'Run an evaluation'),
            @('results', 'Regenerate the README results table'),
            @('clean', 'Remove caches and build artifacts')
        ) | ForEach-Object { '  {0,-12} {1}' -f $_[0], $_[1] } | Write-Host
    }
    'install' { Invoke-Step -Command 'uv' -Arguments @('sync', '--all-extras') -WorkingDirectory $BackendDir }
    'up' {
        Invoke-Step -Command 'docker' -Arguments @('compose', 'up', '-d', '--build', 'db', 'backend')
        Wait-Health
        Invoke-Backend @('python', '-m', 'alembic', 'upgrade', 'head')
    }
    'down' { Invoke-Step -Command 'docker' -Arguments @('compose', 'down') }
    'restart' { Invoke-Step -Command 'docker' -Arguments @('compose', 'restart', 'backend') }
    'logs' { Invoke-Step -Command 'docker' -Arguments @('compose', 'logs', '-f', 'backend') }
    'ps' { Invoke-Step -Command 'docker' -Arguments @('compose', 'ps') }
    'dev' { Invoke-Backend @('python', '-m', 'app.run') }
    'migrate' { Invoke-Backend @('python', '-m', 'alembic', 'upgrade', 'head') }
    'revision' { Invoke-Backend @('python', '-m', 'alembic', 'revision', '--autogenerate', '-m', $M) }
    'health' { Wait-Health }
    'test' { Invoke-Backend @('python', '-m', 'pytest', '-q') }
    'test-unit' { Invoke-Backend @('python', '-m', 'pytest', '-q', '-m', 'not integration') }
    'lint' {
        Invoke-Backend @('python', '-m', 'ruff', 'check', '.')
        Invoke-Backend @('python', '-m', 'ruff', 'format', '--check', '.')
    }
    'fmt' {
        Invoke-Backend @('python', '-m', 'ruff', 'format', '.')
        Invoke-Backend @('python', '-m', 'ruff', 'check', '--fix', '.')
    }
    'typecheck' { Invoke-Backend @('python', '-m', 'mypy', 'app', 'evals') }
    'check' {
        & $PSCommandPath lint
        & $PSCommandPath typecheck
        & $PSCommandPath test
    }
    'ingest' { Invoke-Backend @('python', '-m', 'app.ingestion.run', '--config', "../$Config") }
    'eval' { Invoke-Backend @('python', '-m', 'evals.runner', '--config', "../$Config", '--split', $Split, '--mode', $Mode) }
    'results' { Invoke-Backend @('python', '../scripts/generate_results_table.py') }
    'clean' {
        Get-ChildItem -Path $RepoRoot -Include '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache' -Recurse -Directory -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host 'Caches removed.'
    }
    default { throw "Unknown target '$Target'. Run ./make.ps1 help" }
}
