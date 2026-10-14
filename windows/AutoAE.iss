#define AppName "Auto AE"
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
#define AppPublisher "Auto AE"
#define AppExeName "Auto AE.exe"

[Setup]
AppId={{C7C2AAE0-5849-4557-AD11-FCDA298EAD6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Auto AE
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-windows-installer
OutputBaseFilename=AutoAE-Setup
SetupIconFile=..\assets\autoae.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist-windows-installer\Auto AE\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Auto AE"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Auto AE"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch Auto AE"; Flags: postinstall nowait skipifsilent
