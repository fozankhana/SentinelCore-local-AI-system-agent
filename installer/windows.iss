; Inno Setup script for SentinelCore v1.0.0
; Requires: Inno Setup 6+ (https://jrsoftware.org/isdownload.php)
; Build:    iscc installer\windows.iss
; Output:   dist\SentinelCore-1.0.0-Setup.exe

#define AppName      "SentinelCore"
#define AppVersion   "1.0.0"
#define AppPublisher "fozankhana"
#define AppURL       "https://github.com/fozankhana/SentinelCore-local-AI-system-agent"
#define AppExeName   "SentinelCore.exe"
#define AppDashboard "http://localhost:4080"

[Setup]
AppId={{A3F92C81-4B7E-4D2A-9F3B-8C0D1E5F6A7B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=SentinelCore-{#AppVersion}-Setup
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";          GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startupentry"; Description: "Start SentinelCore with &Windows";    GroupDescription: "Startup:";             Flags: unchecked

[Files]
Source: "..\dist\SentinelCore\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";                     Filename: "{app}\{#AppExeName}"
Name: "{group}\Open Dashboard";                  Filename: "{app}\{#AppExeName}"; Parameters: "--open-browser"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";              Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}";                Filename: "{app}\{#AppExeName}"; Tasks: startupentry

[Registry]
Root: HKCU; Subkey: "Software\{#AppName}"; \
  ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#AppName}"; \
  ValueType: string; ValueName: "Version";     ValueData: "{#AppVersion}"

[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName} (opens dashboard at {#AppDashboard})"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Leave user data — only remove the installed binaries
Type: filesandordirs; Name: "{app}"
