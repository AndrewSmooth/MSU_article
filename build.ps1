$ErrorActionPreference = 'Stop'

$compiler = Get-Command lualatex -ErrorAction SilentlyContinue

if ($compiler) {
    $compilerPath = $compiler.Source
} else {
    $candidates = @(
        'C:\Users\user\AppData\Local\Programs\MiKTeX\miktex\bin\x64\lualatex.exe',
        'C:\Program Files\MiKTeX\miktex\bin\x64\lualatex.exe'
    )
    $compilerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

if (-not $compilerPath) {
    throw 'LuaLaTeX was not found. Install MiKTeX or restart the terminal after installation.'
}

Push-Location $PSScriptRoot
try {
    1..2 | ForEach-Object {
        & $compilerPath --enable-installer --interaction=nonstopmode --halt-on-error article.tex
        if ($LASTEXITCODE -ne 0) {
            throw "LuaLaTeX failed with exit code $LASTEXITCODE."
        }
    }

    Write-Host "Built: $PSScriptRoot\article.pdf"
} finally {
    Pop-Location
}
