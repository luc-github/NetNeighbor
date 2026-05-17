; Inno Setup script — NetNeighbor 2.0 (Qt skeleton).
; Build after PyInstaller: packaging\windows\build_installer.ps1

#define MyAppName "NetNeighbor"
#define MyAppPublisher "ESP3D"
#define MyAppURL "https://github.com/luc-github/NetNeighbor"
#define MyAppExeName "NetNeighbor.exe"

[Setup]
AppId={{A3B8F2E1-4C5D-6E7F-8091-2A3B4C5D6E7F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\..\..\dist
OutputBaseFilename=NetNeighbor-{#MyAppVersion}-win64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PyInstallerDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  if not DirExists(ExpandConstant('{#PyInstallerDir}')) then
  begin
    MsgBox('PyInstaller output not found. Run packaging\windows\build.ps1 first.', mbError, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;
