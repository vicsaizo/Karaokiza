# Publica una versión nueva de Karaokiza con un solo comando:
#   powershell -File tools\release.ps1 -Version 1.2.0 -NotesFile notas.md [-Itch]
# Hace: sube la versión en el código, compila el instalador, hace commit + etiqueta + push,
# crea la release en GitHub con el instalador adjunto y (con -Itch) lo sube también a itch.io.
# Requisitos: tools\build.ps1 (git, uv, Inno Setup), `gh auth login` hecho y, para -Itch,
# butler (https://itch.io/docs/butler/) con `butler login` hecho una vez.
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [Parameter(Mandatory)][string]$NotesFile,
    [switch]$Itch
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (-not (Test-Path $NotesFile)) { throw "No existe el archivo de notas: $NotesFile" }
if (git status --porcelain) { throw "Hay cambios sin confirmar; haz commit antes de publicar." }
$tag = "v$Version"
if (git tag --list $tag) { throw "La etiqueta $tag ya existe." }

# 1. Subir la versión en los tres sitios donde aparece
$parts = ($Version -split '\.') + '0'
function Set-Text($path, $pattern, $replacement) {
    $t = [IO.File]::ReadAllText($path)
    if ($t -notmatch $pattern) { throw "No encuentro la versión en $path" }
    [IO.File]::WriteAllText($path, [regex]::Replace($t, $pattern, $replacement))
}
Set-Text "$root\src\karaokiza.py" 'APP_VERSION = "[\d.]+"' "APP_VERSION = `"$Version`""
Set-Text "$root\installer\karaokiza.iss" '#define AppVersion "[\d.]+"' "#define AppVersion `"$Version`""
$v = "$Version"; $t4 = $parts -join ', '
Set-Text "$root\tools\version_info.txt" 'filevers=\([\d, ]+\), prodvers=\([\d, ]+\)' "filevers=($t4), prodvers=($t4)"
Set-Text "$root\tools\version_info.txt" "'FileVersion', '[\d.]+'" "'FileVersion', '$v'"
Set-Text "$root\tools\version_info.txt" "'ProductVersion', '[\d.]+'" "'ProductVersion', '$v'"

# 2. Compilar
& "$PSScriptRoot\build.ps1"
$exe = Join-Path $root "dist\Karaokiza-Setup-$Version.exe"
if (-not (Test-Path $exe)) { throw "No se generó $exe" }

# 3. Commit, etiqueta y push
git add -A
git commit -q -m "Karaokiza $Version"
git tag $tag
git push origin HEAD $tag

# 4. Release en GitHub con el instalador
gh release create $tag $exe --title "Karaokiza $Version" --notes-file $NotesFile --latest
Write-Host "GitHub: https://github.com/vicsaizo/Karaokiza/releases/tag/$tag"

# 5. itch.io (opcional)
if ($Itch) {
    if (-not (Get-Command butler -ErrorAction SilentlyContinue)) { throw "Falta butler (https://itch.io/docs/butler/)" }
    butler push $exe "vicsaizo/karaokiza:windows" --userversion $Version
}
