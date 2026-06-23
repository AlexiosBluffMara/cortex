param(
    [string]$OutputDir = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "build\huggingface-space"
}

function Resolve-FullPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Test-IsUnder([string]$Child, [string]$Parent) {
    $childFull = [System.IO.Path]::GetFullPath($Child).TrimEnd('\', '/')
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    return $childFull.StartsWith($parentFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Copy-TreeFiltered([string]$Source, [string]$Target) {
    $sourceFull = [System.IO.Path]::GetFullPath($Source)
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    Get-ChildItem -LiteralPath $sourceFull -Recurse -Force | ForEach-Object {
        $rel = [System.IO.Path]::GetRelativePath($sourceFull, $_.FullName)
        if ($rel -match '(^|[\\/])(__pycache__|\.pytest_cache|\.ruff_cache|\.mypy_cache)([\\/]|$)') {
            return
        }
        $destPath = Join-Path $Target $rel
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Force -Path $destPath | Out-Null
        } else {
            $destParent = Split-Path -Parent $destPath
            New-Item -ItemType Directory -Force -Path $destParent | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destPath -Force
        }
    }
}

$Dest = Resolve-FullPath $OutputDir
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "build"))
if ($Clean -and (Test-Path -LiteralPath $Dest)) {
    if (-not (Test-IsUnder $Dest $BuildRoot)) {
        throw "Refusing to clean '$Dest'. Use a destination under '$BuildRoot'."
    }
    Remove-Item -LiteralPath $Dest -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

Copy-Item -LiteralPath (Join-Path $RepoRoot "cloud\huggingface_space\app.py") -Destination (Join-Path $Dest "app.py") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "cloud\huggingface_space\SPACE_README.md") -Destination (Join-Path $Dest "README.md") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "cloud\huggingface_space\requirements.txt") -Destination (Join-Path $Dest "requirements.txt") -Force

Copy-TreeFiltered (Join-Path $RepoRoot "cloud") (Join-Path $Dest "cloud")
Copy-TreeFiltered (Join-Path $RepoRoot "cortex") (Join-Path $Dest "cortex")

Copy-Item -LiteralPath (Join-Path $RepoRoot "pyproject.toml") -Destination (Join-Path $Dest "pyproject.toml") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "NOTICE") -Destination (Join-Path $Dest "NOTICE") -Force

Write-Host "Hugging Face Space bundle exported to $Dest"
Write-Host "Upload that directory as the Space root. Start with CORTEX_WORKER_MODE=fake, then switch to real after TRIBE deps, weights, and CUDA are verified."
