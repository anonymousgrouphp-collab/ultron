; =====================================================================
; ULTRON AI Desktop Assistant — Inno Setup Script
; Generates: Release\ULTRON_Setup.exe
; =====================================================================

#define MyAppName "ULTRON AI Assistant"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "ULTRON AI Engine"
#define MyAppURL "https://github.com/ultron-ai"
#define MyAppExeName "Ultron.exe"

[Setup]
AppId={{8F932D1A-9E8C-4B72-A23F-8C56F7418C01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\Ultron
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=ULTRON_Setup
OutputDir=Release
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=config\jarvis.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Bundle entire PyInstaller built output folder recursively
Source: "dist\Ultron\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\config\jarvis.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\config\jarvis.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Helper for silent VC++ and WebView2 checks if needed
procedure InitializeWizard;
begin
  // Setup initializes cleanly
end;
