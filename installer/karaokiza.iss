; Instalador de Karaokiza (Inno Setup 6). Se compila desde tools\build.ps1.
; Instala solo para el usuario (sin administrador). El motor de IA (varios GB) lo descarga
; el propio programa la primera vez en %USERPROFILE%\Karaokiza\motor; las canciones quedan en
; %USERPROFILE%\Karaokiza\Canciones y no se borran al desinstalar.
; Quien tenía la versión antigua («Karaoke») se actualiza sobre ella (mismo AppId) y sigue
; usando su carpeta %USERPROFILE%\Karaoke con el motor ya instalado.

#define AppVersion "1.1.0"

[Setup]
AppId={{99C4FEFE-60C7-4F6B-8CE4-C26DE807214D}
AppName=Karaokiza
AppVersion={#AppVersion}
AppVerName=Karaokiza {#AppVersion}
AppPublisher=vicsaizo
AppPublisherURL=https://github.com/vicsaizo/Karaokiza
AppSupportURL=https://github.com/vicsaizo/Karaokiza/issues
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador de Karaokiza
DefaultDirName={autopf}\Karaokiza
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
LicenseFile=..\LICENCIAS.txt
OutputDir=..\dist
OutputBaseFilename=Karaokiza-Setup-{#AppVersion}
SetupIconFile=..\assets\karaoke.ico
UninstallDisplayIcon={app}\Karaokiza.exe
UninstallDisplayName=Karaokiza
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Restos de la versión antigua, que se llamaba Karaoke
Type: files; Name: "{app}\Karaoke.exe"
Type: files; Name: "{autodesktop}\Karaoke.lnk"
Type: files; Name: "{autoprograms}\Karaoke.lnk"

[Files]
Source: "..\build\dist\Karaokiza\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENCIAS.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Karaokiza"; Filename: "{app}\Karaokiza.exe"
Name: "{autodesktop}\Karaokiza"; Filename: "{app}\Karaokiza.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Karaokiza.exe"; Description: "{cm:LaunchProgram,Karaokiza}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; El motor y los modelos (varios GB) se van; las canciones se quedan
Type: filesandordirs; Name: "{%USERPROFILE}\Karaokiza\motor"
Type: filesandordirs; Name: "{%USERPROFILE}\Karaokiza\registros"
Type: files; Name: "{%USERPROFILE}\Karaokiza\karaokiza.json"
Type: dirifempty; Name: "{%USERPROFILE}\Karaokiza"
Type: filesandordirs; Name: "{%USERPROFILE}\Karaoke\motor"
Type: filesandordirs; Name: "{%USERPROFILE}\Karaoke\registros"
Type: files; Name: "{%USERPROFILE}\Karaoke\karaoke.json"
Type: dirifempty; Name: "{%USERPROFILE}\Karaoke"

