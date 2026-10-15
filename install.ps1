# jev-claud - instalador para Windows (PowerShell 5.1+).
#
#   irm https://raw.githubusercontent.com/thiago-lage/jev-claud/main/install.ps1 | iex
#
# 1. Encontra (ou oferece instalar) o Python 3.10+.
# 2. Cria um ambiente isolado em ~\.local\share\jev-claud (nao mexe no Python global).
# 3. Coloca o comando `jev-claud` em ~\.local\bin e garante que essa pasta esta no PATH.
# 4. Abre o assistente `jev-claud setup` (Ollama, Claude Code, skill TypeSafe, chave do Jev).
#
# Instala a versao lancada no PyPI. Variaveis opcionais: JEV_CLAUD_SPEC (o que o pip instala;
# versao em desenvolvimento: "jev-claud[jev] @ git+https://github.com/thiago-lage/jev-claud"),
# JEV_CLAUD_INSTALL_DIR,
# JEV_CLAUD_BIN_DIR, JEV_CLAUD_NO_MODIFY_PATH=1, JEV_CLAUD_SKIP_SETUP=1.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Get-Setting($name, $default) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $default }
    return $value
}

$Spec = Get-Setting "JEV_CLAUD_SPEC" "jev-claud[jev]"
$InstallDir = Get-Setting "JEV_CLAUD_INSTALL_DIR" (Join-Path $env:USERPROFILE ".local\share\jev-claud")
$BinDir = Get-Setting "JEV_CLAUD_BIN_DIR" (Join-Path $env:USERPROFILE ".local\bin")

function Write-Step($text) { Write-Host "  * $text" -ForegroundColor DarkYellow }
function Write-Ok($text) { Write-Host "  + $text" -ForegroundColor Green }

function Find-Python {
    $candidates = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $extra = @($candidate | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            & $exe @extra -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return , $candidate }
        } catch { }
    }
    return $null
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Add-ToUserPath($dir) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
    try {
        $raw = $key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $entries = @($raw -split ";" | Where-Object { $_ -ne "" })
        foreach ($entry in $entries) {
            if ([Environment]::ExpandEnvironmentVariables($entry).TrimEnd("\") -ieq $dir.TrimEnd("\")) { return $false }
        }
        $kind = if ($raw) { $key.GetValueKind("Path") } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
        $key.SetValue("Path", (($entries + $dir) -join ";"), $kind)
    } finally {
        $key.Close()
    }
    # Avisa o Windows para os proximos terminais enxergarem o PATH novo.
    [Environment]::SetEnvironmentVariable("JEV_CLAUD_PATH_REFRESH", "1", "User")
    [Environment]::SetEnvironmentVariable("JEV_CLAUD_PATH_REFRESH", $null, "User")
    return $true
}

Write-Host ""
Write-Host "  jev-claud - instalador" -ForegroundColor DarkYellow
Write-Host "  Jev decide o que o codigo ramifica. LLM escreve o que a pessoa le." -ForegroundColor DarkGray
Write-Host ""

# 1. Python ---------------------------------------------------------------------------
$python = Find-Python
if (-not $python) {
    Write-Host "  Python 3.10+ nao encontrado." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host "  Instalar o Python 3.12 via winget? (S/n)"
        if ($answer -eq "" -or $answer -match "^(s|sim|y|yes)$") {
            winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
            Update-SessionPath
            $python = Find-Python
        }
    }
    if (-not $python) {
        throw "Instale o Python 3.10+ (https://www.python.org/downloads/) e rode o instalador de novo."
    }
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)
Write-Ok ("Python: " + (& $pyExe @pyArgs -c "import sys; print(sys.version.split()[0])"))

# 2. Ambiente isolado -------------------------------------------------------------------
$venv = Join-Path $InstallDir "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Criando ambiente isolado em $venv"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & $pyExe @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente virtual." }
}
Write-Step "Instalando jev-claud"
& $venvPython -m pip install --quiet --disable-pip-version-check --upgrade pip
& $venvPython -m pip install --quiet --disable-pip-version-check --upgrade $Spec
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar: $Spec" }
Write-Ok ("Instalado: " + (& $venvPython -m jev_claud --version))

# 3. Comando no PATH ---------------------------------------------------------------------
# O atalho chama `python -m jev_claud` (e nao o jev-claud.exe): assim o `jev-claud update`
# consegue substituir o proprio pacote, ja que o Windows trava executaveis em uso.
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$shim = Join-Path $BinDir "jev-claud.cmd"
Set-Content -Path $shim -Encoding ASCII -Value "@echo off`r`n`"$venvPython`" -m jev_claud %*"
Write-Ok "Comando: $shim"
# Atalho sem extensao para o Git Bash (usado pelo Claude Code e pelos hooks do plugin),
# que nao encontra arquivos .cmd. Fim de linha LF: o bash rejeita CRLF no shebang.
# Os dois nomes: `jev-claud` e o antigo `jev-claude` (ate a 0.6), apontando para a instalacao nova.
$posixPython = $venvPython -replace '\\', '/'
foreach ($name in @("jev-claud", "jev-claude")) {
    Set-Content -Path (Join-Path $BinDir "$name.cmd") -Encoding ASCII -Value "@echo off`r`n`"$venvPython`" -m jev_claud %*"
    [System.IO.File]::WriteAllText((Join-Path $BinDir $name), "#!/bin/sh`nexec `"$posixPython`" -m jev_claud `"`$@`"`n")
}
if (-not $env:JEV_CLAUD_NO_MODIFY_PATH) {
    if (Add-ToUserPath $BinDir) { Write-Ok "$BinDir adicionado ao PATH do usuario" }
    Update-SessionPath
}

# 4. Assistente --------------------------------------------------------------------------
if ($env:JEV_CLAUD_SKIP_SETUP) {
    Write-Host ""
    Write-Host "  Pronto. Rode: jev-claud setup" -ForegroundColor Green
} else {
    Write-Host ""
    & $venvPython -m jev_claud setup
    Write-Host ""
    Write-Host "  Abra um terminal novo para usar o comando jev-claud em qualquer pasta." -ForegroundColor DarkGray
}
