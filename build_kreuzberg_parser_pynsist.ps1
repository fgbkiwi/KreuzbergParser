# ============================================================
# Build script: KreuzbergParser (Pynsist + NSIS) com version bump
# e publicacao da Release no GitHub.
#
# Prerequisitos:
#   uv / venv .venv (Python 3.12) com dependencias instaladas
#   .\scripts\update_deps.ps1 -Sync
#   uv pip install pynsist  (instalado automaticamente abaixo)
#   NSIS instalado (https://nsis.sourceforge.io)
#   GitHub CLI (gh) autenticado — so necessario se for publicar
#
# Uso:
#   .\build_kreuzberg_parser_pynsist.ps1                 # patch + publica Release
#   .\build_kreuzberg_parser_pynsist.ps1 minor           # minor + publica Release
#   .\build_kreuzberg_parser_pynsist.ps1 major           # major + publica Release
#   .\build_kreuzberg_parser_pynsist.ps1 -NoPublish      # so gera o .exe (sem Release)
#   .\build_kreuzberg_parser_pynsist.ps1 -NoBump         # build com versao atual (sem bump)
#   .\build_kreuzberg_parser_pynsist.ps1 -NoBump -NoPublish
#   .\build_kreuzberg_parser_pynsist.ps1 minor -NoPublish
# ============================================================

param(
    [ValidateSet("patch", "minor", "major")]
    [string]$Bump = "patch",

    # Escape hatch: builds de teste sem criar Release no GitHub
    [switch]$NoPublish,

    # Reutiliza APP_VERSION atual sem incrementar
    [switch]$NoBump
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$ICON_SOURCE  = "assets\KiwiDown.ico"
$CONFIG_FILE  = "kreuzberg_parser_pynsist.cfg"
$BUMP_SCRIPT  = "bump_version.py"
$PYTHON_EXE   = ".venv\Scripts\python.exe"
$GITHUB_REPO  = "fgbkiwi/KreuzbergParser"

if (-not (Test-Path $CONFIG_FILE)) {
    Write-Error "Config Pynsist nao encontrado: '$CONFIG_FILE'."
    exit 1
}

# Nome do app vem do [Application] name= no cfg (Pynsist troca espacos por _).
$configText = Get-Content -Raw -Path (Join-Path $ScriptDir $CONFIG_FILE)
if ($configText -notmatch '(?m)^name\s*=\s*(.+?)\s*$') {
    Write-Error "Nao foi possivel ler 'name=' de $CONFIG_FILE."
    exit 1
}
$APP_NAME = $Matches[1].Trim()
$INSTALLER_STEM = ($APP_NAME -replace '\s+', '_')

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Building $APP_NAME Windows Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not (Test-Path $PYTHON_EXE)) {
    Write-Error "Python do venv nao encontrado em '$PYTHON_EXE'. Rode .\scripts\update_deps.ps1 -Sync"
    exit 1
}

if (-not $NoBump -and -not (Test-Path $BUMP_SCRIPT)) {
    Write-Error "Script de bump nao encontrado: '$BUMP_SCRIPT'."
    exit 1
}

if (-not (Test-Path $ICON_SOURCE)) {
    Write-Error "Icone nao encontrado: '$ICON_SOURCE'."
    exit 1
}

if (-not $NoPublish) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Error "GitHub CLI (gh) nao encontrado. Instale-o ou use -NoPublish para gerar so o instalador."
        exit 1
    }
    gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "GitHub CLI nao autenticado. Rode 'gh auth login' ou use -NoPublish."
        exit 1
    }
}

# --- Versao (fonte unica: APP_VERSION em main.py) ------------------------
if ($NoBump) {
    Write-Host "`n[0/4] Mantendo versao atual (-NoBump)..." -ForegroundColor Cyan
    $mainSource = Get-Content -Raw -Path (Join-Path $ScriptDir "main.py")
    if ($mainSource -notmatch 'APP_VERSION\s*=\s*["''](\d+\.\d+\.\d+)["'']') {
        Write-Error "Nao foi possivel ler APP_VERSION de main.py."
        exit 1
    }
    $VERSION = $Matches[1]
    Write-Host "Versao (sem bump): $VERSION" -ForegroundColor Green
} else {
    Write-Host "`n[0/4] Incrementando versao ($Bump)..." -ForegroundColor Cyan
    $bumpOutput = & $PYTHON_EXE $BUMP_SCRIPT $Bump 2>&1
    $bumpExit = $LASTEXITCODE
    $bumpOutput | ForEach-Object { Write-Host $_ }
    if ($bumpExit -ne 0) {
        Write-Error "Falha ao incrementar a versao (exit code $bumpExit)."
        exit 1
    }
    $VERSION = ($bumpOutput | Where-Object { $_ -match '^\d+\.\d+\.\d+$' } | Select-Object -Last 1)
    if (-not $VERSION) {
        Write-Error "bump_version.py nao retornou a nova versao em stdout."
        exit 1
    }
    $VERSION = $VERSION.ToString().Trim()
    Write-Host "Nova versao: $VERSION" -ForegroundColor Green
}

$INSTALLER = Join-Path $ScriptDir "build\nsis\${INSTALLER_STEM}_${VERSION}.exe"
$TAG = "v$VERSION"

# --- Garantir pynsist instalado -----------------------------------------
Write-Host "`n[1/4] Instalando pynsist..." -ForegroundColor Cyan
uv pip install --quiet pynsist --python $PYTHON_EXE
if ($LASTEXITCODE -ne 0) {
    Write-Error "Falha ao instalar pynsist."
    exit 1
}
Write-Host "  pynsist OK" -ForegroundColor Gray

Write-Host "  Icone: $ICON_SOURCE" -ForegroundColor Gray

# Limpar saida anterior para validar que o .exe atual veio deste build
if (Test-Path "build\nsis") {
    Remove-Item -Recurse -Force "build\nsis"
}

# --- Compilar com Pynsist ------------------------------------------------
Write-Host "`n[2/4] Gerando instalador com Pynsist + NSIS..." -ForegroundColor Cyan
Write-Host "  (o stack CUDA torna este passo lento e o .exe grande)" -ForegroundColor Yellow

$venvSp  = Join-Path $ScriptDir ".venv\Lib\site-packages"

# Pre-seed do cliente Flet no venv para o pynsist copiar dentro do pacote
# (File /r pkgs\*.* so inclui o que existir ANTES/durante o prepare).
Write-Host "  Preparando flet_desktop/app/flet-windows.zip no venv..." -ForegroundColor Gray
$fletAppInVenv = Join-Path $venvSp "flet_desktop\app"
New-Item -ItemType Directory -Force -Path $fletAppInVenv | Out-Null
$fletZipVenv = Join-Path $fletAppInVenv "flet-windows.zip"
if (-not (Test-Path $fletZipVenv)) {
    $fletVer = & $PYTHON_EXE -c "import flet_desktop.version as v; print(v.version)"
    if ($LASTEXITCODE -ne 0 -or -not $fletVer) {
        Write-Error "Nao foi possivel ler flet_desktop.version no venv."
        exit 1
    }
    $fletVer = $fletVer.ToString().Trim()
    $cacheClient = Join-Path $env:USERPROFILE ".flet\client\flet-desktop-full-$fletVer"
    if ((Test-Path $cacheClient) -and (Test-Path (Join-Path $cacheClient "flet\flet.exe"))) {
        Write-Host "  Compactando cache local $cacheClient" -ForegroundColor Gray
        Compress-Archive -Path (Join-Path $cacheClient "*") -DestinationPath $fletZipVenv -Force
    } else {
        $url = "https://github.com/flet-dev/flet/releases/download/v$fletVer/flet-windows.zip"
        Write-Host "  Baixando $url" -ForegroundColor Gray
        Invoke-WebRequest -Uri $url -OutFile $fletZipVenv
    }
}
if (-not (Test-Path $fletZipVenv)) {
    Write-Error "Falha ao preparar flet-windows.zip em $fletZipVenv"
    exit 1
}

# Substitui o icone padrao do flet.exe pelo KiwiDown.ico (barra de tarefas / pin).
$patchIconScript = Join-Path $ScriptDir "scripts\patch_flet_exe_icon.py"
if (-not (Test-Path $patchIconScript)) {
    Write-Error "Script de icone nao encontrado: $patchIconScript"
    exit 1
}
Write-Host "  Aplicando KiwiDown.ico em flet.exe dentro do zip..." -ForegroundColor Gray
& $PYTHON_EXE $patchIconScript --zip $fletZipVenv --icon (Join-Path $ScriptDir $ICON_SOURCE)
if ($LASTEXITCODE -ne 0) {
    Write-Error "Falha ao gravar icone no flet-windows.zip (exit code $LASTEXITCODE)."
    exit 1
}

# Extrai FORA de site-packages: o Flutter Windows traz data\app.so e o Pynsist
# rejeita qualquer .so ao copiar o pacote flet_desktop (ExtensionModuleMismatch).
# O cliente entra no instalador via files= (kreuzberg_parser_pynsist.cfg).
$fletRuntimeRoot = Join-Path $ScriptDir "build\flet_runtime"
$fletExtracted = Join-Path $fletRuntimeRoot "flet"
if (Test-Path $fletRuntimeRoot) {
    Remove-Item -LiteralPath $fletRuntimeRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $fletRuntimeRoot | Out-Null

# Remove extracao antiga dentro do pacote Python (builds anteriores).
$legacyExtracted = Join-Path $fletAppInVenv "flet"
if (Test-Path $legacyExtracted) {
    Write-Host "  Removendo extracao legada em flet_desktop\app\flet..." -ForegroundColor Gray
    Remove-Item -LiteralPath $legacyExtracted -Recurse -Force
}

Write-Host "  Extraindo cliente Flet patchado para build\flet_runtime..." -ForegroundColor Gray
Expand-Archive -Path $fletZipVenv -DestinationPath $fletRuntimeRoot -Force
if (-not (Test-Path (Join-Path $fletExtracted "flet.exe"))) {
    Write-Error "flet.exe nao encontrado apos extracao em $fletExtracted"
    exit 1
}

Write-Host ("  flet-windows.zip no venv OK ({0:N1} MB)" -f ((Get-Item $fletZipVenv).Length / 1MB)) -ForegroundColor Gray

# Remove NSI/icone residual de builds anteriores (evita NSI corrompido e KiwiDown.1.ico).
$nsisDir = Join-Path $ScriptDir "build\nsis"
if (Test-Path $nsisDir) {
    Remove-Item -LiteralPath (Join-Path $nsisDir "installer.nsi") -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $nsisDir -Filter "KiwiDown*.ico" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "KiwiDown.ico" } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# Prepara pkgs/NSI sem makensis para podermos completar deps/metadados.
& $PYTHON_EXE -m nsist --no-makensis $CONFIG_FILE
if ($LASTEXITCODE -ne 0) {
    Write-Error "Pynsist falhou (exit code $LASTEXITCODE). Abortando."
    exit 1
}

$pkgsOut = Join-Path $ScriptDir "build\nsis\pkgs"
if (-not (Test-Path $pkgsOut)) {
    Write-Error "Pasta de pacotes nao encontrada: $pkgsOut"
    exit 1
}

Write-Host "  Completando deps nativas / metadados do venv..." -ForegroundColor Gray
$extraItems = @(
    "torchgen",
    "functorch",
    "typing_extensions.py",
    "huggingface_hub",
    "tokenizers",
    "safetensors",
    "regex",
    "requests",
    "packaging",
    "filelock",
    "fsspec",
    "jinja2",
    "networkx",
    "sympy",
    "mpmath",
    "markupsafe",
    "certifi",
    "charset_normalizer",
    "idna",
    "urllib3",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "h11",
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "typing_inspection",
    "msgpack",
    "flet_desktop",
    "rich",
    "markdown_it",
    "mdurl",
    "pygments"
)
foreach ($item in $extraItems) {
    $src = Join-Path $venvSp $item
    $dest = Join-Path $pkgsOut $item
    if (-not (Test-Path $src)) { continue }
    # Evita aninhar pasta/pasta quando o pynsist ja copiou o pacote.
    if (Test-Path $dest) { continue }
    Copy-Item -LiteralPath $src -Destination $dest -Recurse -Force
}

# Metadados necessarios para importlib.metadata.version("kreuzberg"/...)
Get-ChildItem -Path $venvSp -Directory -Filter "*.dist-info" | ForEach-Object {
    $distName = $_.Name
    $pkgKey = ($distName -split '-')[0].ToLower().Replace('_', '-')
    $aliases = @{
        "pillow" = "PIL"
        "python-dotenv" = "dotenv"
        "pyyaml" = "yaml"
        "scikit-image" = "skimage"
        "opencv-python-headless" = "cv2"
        "flet-desktop" = "flet_desktop"
    }
    $probe = if ($aliases.ContainsKey($pkgKey)) { $aliases[$pkgKey] } else { ($distName -split '-')[0] }
    $already = (Test-Path (Join-Path $pkgsOut $probe)) -or
               (Test-Path (Join-Path $pkgsOut ($probe + ".py"))) -or
               (Test-Path (Join-Path $pkgsOut ($probe.Replace("-", "_"))))
    $destMeta = Join-Path $pkgsOut $distName
    if ($already -and -not (Test-Path $destMeta)) {
        Copy-Item -LiteralPath $_.FullName -Destination $destMeta -Recurse -Force
    }
}

# Garantir zip do Flet no pkgs (File /r pkgs\*.* inclui o que estiver aqui).
$fletZipPkgs = Join-Path $pkgsOut "flet_desktop\app\flet-windows.zip"
if (-not (Test-Path $fletZipPkgs)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $fletZipPkgs -Parent) | Out-Null
    Copy-Item -LiteralPath $fletZipVenv -Destination $fletZipPkgs -Force
}
if (-not (Test-Path $fletZipPkgs)) {
    Write-Error "flet-windows.zip ausente em $fletZipPkgs"
    exit 1
}
if (-not (Test-Path (Join-Path $pkgsOut "rich"))) {
    Write-Error "Pacote 'rich' ausente em $pkgsOut (necessario para import flet_desktop)."
    exit 1
}
Write-Host "  Verificacao Flet/rich OK" -ForegroundColor Gray

# Se o mapeamento files= aninhar *.libs\*.libs, desfaz.
foreach ($libsName in @("numpy.libs", "scipy.libs", "shapely.libs", "pandas.libs")) {
    $outer = Join-Path $pkgsOut $libsName
    $inner = Join-Path $outer $libsName
    if (Test-Path $inner) {
        Get-ChildItem -Path $inner -File | ForEach-Object {
            Move-Item -LiteralPath $_.FullName -Destination (Join-Path $outer $_.Name) -Force
        }
        Remove-Item -LiteralPath $inner -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Patch installer.nsi: close running Kiwi Down before overwriting assets.
# Never put PowerShell $_ / regex replacements into the .nsi — that corrupted
# prior builds (unterminated string). Helper .ps1 is File'd into PLUGINSDIR.
$nsiPath = Join-Path $ScriptDir "build\nsis\installer.nsi"
$closeHelperPath = Join-Path $ScriptDir "build\nsis\close_kiwi_down.ps1"
if (-not (Test-Path $nsisDir)) {
    New-Item -ItemType Directory -Path $nsisDir -Force | Out-Null
}

# Drop any leftover duplicate icon so pynsist/nsist does not ship KiwiDown.1.ico.
Get-ChildItem -LiteralPath $nsisDir -Filter "KiwiDown*.ico" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne "KiwiDown.ico" } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$closeHelperLines = @(
    "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |"
    "  Where-Object {"
    "    `$_.Name -match '^(pythonw|python)\.exe$' -and"
    "    `$_.CommandLine -match 'Kiwi_Down|KreuzbergParser|kiwi_down'"
    "  } |"
    "  ForEach-Object {"
    "    Stop-Process -Id `$_.ProcessId -Force -ErrorAction SilentlyContinue"
    "  }"
)
[System.IO.File]::WriteAllLines($closeHelperPath, $closeHelperLines)

if (-not (Test-Path $nsiPath)) {
    Write-Error "installer.nsi nao encontrado apos pynsist."
    exit 1
}

$nsi = [System.IO.File]::ReadAllText($nsiPath)
$productNameHits = ([regex]::Matches($nsi, '(?m)^!define PRODUCT_NAME ')).Count
if ($productNameHits -ne 1) {
    Write-Error "installer.nsi invalido apos pynsist (PRODUCT_NAME x$productNameHits). Apague build\nsis e rode de novo."
    exit 1
}
if ($nsi.Contains("Get-CimInstance") -or $nsi.Contains("close_kiwi_down.ps1")) {
    Write-Error "installer.nsi ja contem patch residual. Apague build\nsis\installer.nsi e rode de novo."
    exit 1
}

# Padrao: C:\Program Files\Kiwi Down (AllUsers) em vez de
# %LOCALAPPDATA%\Programs\Kiwi Down (CurrentUser).
# O usuario ainda pode escolher "apenas para mim" na pagina MultiUser.
$defaultUserMode = '!define MULTIUSER_INSTALLMODE_DEFAULT_CURRENTUSER'
$defaultAllUsers = '!define MULTIUSER_INSTALLMODE_DEFAULT_ALLUSERS'
if ($nsi.Contains($defaultAllUsers)) {
    Write-Host "  Install dir padrao ja e AllUsers (Program Files)" -ForegroundColor Gray
} elseif ($nsi.Contains($defaultUserMode)) {
    $nsi = $nsi.Replace($defaultUserMode, $defaultAllUsers)
    Write-Host "  Patch NSIS: install dir padrao -> Program Files\Kiwi Down" -ForegroundColor Gray
} else {
    Write-Error "Nao foi possivel localizar MULTIUSER_INSTALLMODE_DEFAULT_CURRENTUSER no installer.nsi."
    exit 1
}

$marker = "; Kiwi Down: release splash/GIF file locks before overwrite"
# Build inject as string[] then Join — avoids here-string / regex $ pitfalls.
$injectLines = @(
    "  $marker"
    '  DetailPrint "Encerrando instancias em execucao do Kiwi Down (se houver)..."'
    "  InitPluginsDir"
    '  SetOutPath "$PLUGINSDIR"'
    '  File "close_kiwi_down.ps1"'
    '  nsExec::ExecToLog ''powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\close_kiwi_down.ps1"'''
    "  Sleep 800"
    "  ; Limpa residuos de installs antigos que bloqueiam a gravacao de assets"
    '  RMDir /r "$INSTDIR\assets\assets"'
    '  RMDir /r "$INSTDIR\assets\kiwi_down.gif"'
    '  Delete "$INSTDIR\assets\kiwi_down.gif"'
    '  RMDir /r "$INSTDIR\assets\kreuzberg-parser.png"'
    '  Delete "$INSTDIR\assets\kreuzberg-parser.png"'
    '  RMDir /r "$INSTDIR\assets\KiwiDown.ico"'
    '  Delete "$INSTDIR\assets\KiwiDown.ico"'
    '  Delete "$INSTDIR\kreuzberg-parser.ico"'
    '  Delete "$INSTDIR\kreuzberg-parser.1.ico"'
    '  Delete "$INSTDIR\assets\KiwiDown.1.ico"'
    ""
)
$inject = ($injectLines -join "`r`n") + "`r`n"

$anchor = "; Install files"
$idx = $nsi.IndexOf($anchor)
if ($idx -lt 0) {
    $anchor = "; Install directories"
    $idx = $nsi.IndexOf($anchor)
}
if ($idx -lt 0) {
    Write-Error "Nao foi possivel localizar ponto de injecao no installer.nsi."
    exit 1
}

$nsi = $nsi.Insert($idx, $inject)
# Strip accidental KiwiDown.1.ico File lines if residual icon was copied.
$nsi = [regex]::Replace($nsi, '(?m)^\s*File "KiwiDown\.\d+\.ico"\s*\r?\n', "")
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($nsiPath, $nsi, $utf8NoBom)

$productNameHits = ([regex]::Matches($nsi, '(?m)^!define PRODUCT_NAME ')).Count
if ($productNameHits -ne 1) {
    Write-Error "Patch NSIS corrompeu installer.nsi (PRODUCT_NAME x$productNameHits). Abortando antes do makensis."
    exit 1
}
if ($nsi.Contains("Get-CimInstance")) {
    Write-Error "Patch NSIS inseriu comando inline indevido. Abortando."
    exit 1
}
Write-Host "  Patch NSIS: liberacao de lock do GIF / limpeza assets aninhados" -ForegroundColor Gray

$makensis = $null
foreach ($candidate in @(
    "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
    "$env:ProgramFiles\NSIS\makensis.exe",
    "makensis"
)) {
    if ($candidate -eq "makensis") {
        $cmd = Get-Command makensis -ErrorAction SilentlyContinue
        if ($cmd) { $makensis = $cmd.Source; break }
    } elseif (Test-Path $candidate) {
        $makensis = $candidate
        break
    }
}
if (-not $makensis) {
    Write-Error "makensis nao encontrado. Instale o NSIS."
    exit 1
}

Write-Host "  Executando makensis..." -ForegroundColor Gray
& $makensis (Join-Path $ScriptDir "build\nsis\installer.nsi")
if ($LASTEXITCODE -ne 0) {
    Write-Error "makensis falhou (exit code $LASTEXITCODE). Abortando."
    exit 1
}

# pynsist pode retornar 0 mesmo se makensis falhar; confirmar o .exe
Write-Host "`n[3/4] Verificando saida do instalador..." -ForegroundColor Cyan
$produced = @(Get-ChildItem -Path "build\nsis" -Filter "*.exe" -ErrorAction SilentlyContinue)
if (-not $produced) {
    Write-Error "Nenhum instalador .exe foi gerado em build\nsis. makensis provavelmente falhou."
    exit 1
}
if (-not (Test-Path $INSTALLER)) {
    $fallback = $produced | Where-Object { $_.Name -like "*_${VERSION}.exe" } | Select-Object -First 1
    if ($fallback) {
        Write-Host "Aviso: esperado $($INSTALLER | Split-Path -Leaf); usando $($fallback.Name)." -ForegroundColor Yellow
        $INSTALLER = $fallback.FullName
    } else {
        Write-Error "Instalador esperado nao encontrado: $INSTALLER (gerados: $($produced.Name -join ', '))"
        exit 1
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " INSTALLER BUILD SUCCESSFUL!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "Versao:     $VERSION"
Write-Host "Instalador: $INSTALLER"

# --- Publicar Release no GitHub ------------------------------------------
if ($NoPublish) {
    Write-Host "`nPublicacao no GitHub omitida (-NoPublish)." -ForegroundColor Yellow
    Write-Host "Para publicar depois:" -ForegroundColor Gray
    Write-Host "  gh release create $TAG `"$INSTALLER`" --repo $GITHUB_REPO --title `"$APP_NAME $VERSION`" --latest" -ForegroundColor Gray
} else {
    Write-Host "`n[4/4] Publicando Release $TAG no GitHub..." -ForegroundColor Cyan

    $existing = gh release view $TAG --repo $GITHUB_REPO 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Error "A Release $TAG ja existe em $GITHUB_REPO. Abortando publicacao (o instalador local permanece em $INSTALLER)."
        exit 1
    }

    $notes = @"
Instalador Windows do $APP_NAME $VERSION.

Baixe o arquivo .exe e execute o assistente de instalacao.

Requisitos: Windows 64-bit, driver NVIDIA atualizado para modos GPU (PyTorch cu130).
Poppler e tessdata sao baixados na primeira execucao.
"@

    gh release create $TAG $INSTALLER `
        --repo $GITHUB_REPO `
        --title "$APP_NAME $VERSION" `
        --notes $notes `
        --latest

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Falha ao criar a Release $TAG. O instalador local esta em: $INSTALLER"
        exit 1
    }

    $releaseUrl = "https://github.com/$GITHUB_REPO/releases/tag/$TAG"
    Write-Host "Release publicada: $releaseUrl" -ForegroundColor Green
    if (-not $NoBump) {
        Write-Host "Lembre-se de fazer commit/push do bump de versao ($VERSION) se ainda nao estiver no remoto." -ForegroundColor Yellow
    }
}

Write-Host "`nBuild concluido!" -ForegroundColor Green
