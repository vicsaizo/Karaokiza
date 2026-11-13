# Construye el instalador de Karaokiza desde cero:
#   1. descarga UltraSinger en el commit fijado y le aplica nuestros parches (patches\)
#   2. empaqueta la ventanita (PyInstaller, carpeta) con el motor y uv dentro
#   3. compila el instalador (Inno Setup)
# Requisitos: git, uv (https://docs.astral.sh/uv/) e Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Resultado: dist\Karaokiza-Setup-<versión>.exe
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$build = Join-Path $root "build"
$res = Join-Path $build "recursos"
$env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Falta Inno Setup 6: winget install JRSoftware.InnoSetup" }

Remove-Item $build -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $res | Out-Null

# 1. Motor: UltraSinger fijado + parches propios
$pin = (Get-Content "$root\patches\UPSTREAM_COMMIT.txt").Trim()
$motor = Join-Path $build "motor-src"
git clone --quiet https://github.com/rakuri255/UltraSinger.git $motor
git -C $motor checkout --quiet $pin
$patches = Get-ChildItem "$root\patches\*.patch" | Sort-Object Name | ForEach-Object FullName
git -C $motor -c user.name=Karaokiza -c user.email=karaokiza@localhost am --quiet @patches
if ($LASTEXITCODE -ne 0) { throw "No se pudieron aplicar los parches" }
git -C $motor archive --format=zip -o "$res\motor.zip" HEAD

# 2. uv y programa
Copy-Item (Get-Command uv).Source "$res\uv.exe"
Copy-Item "$root\src\karaokiza.py" "$build\karaoke.py"
uvx --python 3.12 pyinstaller --noconfirm --onedir --windowed --name Karaokiza `
    --icon "$root\assets\karaoke.ico" --version-file "$root\tools\version_info.txt" `
    --add-data "$root\assets\karaoke.ico;." `
    --add-data "$res\motor.zip;." `
    --add-data "$res\uv.exe;." `
    --distpath "$build\dist" --workpath "$build\work" --specpath $build "$build\karaoke.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }

# 3. Instalador
& $iscc /Q "$root\installer\karaokiza.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup falló" }
Get-ChildItem "$root\dist\Karaokiza-Setup-*.exe" | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
