; Dash Installer Script for Inno Setup
; Calem Young

#define MyAppName "Dash"
; The version is supplied by build/scripts/build_installer.py from
; build/installer/version.txt via ISCC /DMyAppVersion=<version>. The fallback
; below only applies when this script is compiled directly.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Calem Young"
#define MyAppURL "https://github.com/calemyoung/Dash"
#define MyAppExeName "Dash.exe"
#define MyAppDescription "A quick program launcher for Windows"

[Setup]
AppId={{88DC9BD4-16FC-452E-87DD-4B0F54603ED1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) 2025 {#MyAppPublisher}

; Installation directories
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Output - go up one level to project root
OutputDir=..\..\dist
OutputBaseFilename=DashSetup-{#MyAppVersion}
SetupIconFile=..\..\assets\icons\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; Compression
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=2

; UI
WizardStyle=modern

; Privileges - use lowest to install to user directory without UAC
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Version info
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDescription}
VersionInfoCopyright=Copyright (C) 2025 {#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; Uninstall
Uninstallable=yes
UninstallDisplayName={#MyAppName}

; Architecture
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start {#MyAppName} automatically when Windows starts"; GroupDescription: "Startup Options:"; Flags: unchecked

[Files]
; Main executable and dependencies from PyInstaller output
Source: "..\..\dist\dash\Dash.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "..\..\dist\dash\*.dll"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "..\..\dist\dash\*.pyd"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "..\..\dist\dash\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; Icons folder
Source: "..\..\assets\icons\*"; DestDir: "{app}\assets\icons"; Flags: ignoreversion recursesubdirs createallsubdirs

; Default config files
Source: "..\..\config\settings.default.toml"; DestDir: "{app}\config"; DestName: "settings.toml"; Flags: ignoreversion
Source: "..\..\config\commands.default.toml"; DestDir: "{app}\config"; DestName: "commands.toml"; Flags: ignoreversion

[Dirs]
; Create AppData directories
Name: "{userappdata}\{#MyAppName}"; Flags: uninsneveruninstall
Name: "{userappdata}\{#MyAppName}\config"; Flags: uninsneveruninstall
Name: "{userappdata}\{#MyAppName}\assets\icons"; Flags: uninsneveruninstall

[Icons]
; Start menu shortcuts
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Quick command Dash - Press Alt+F to open"
Name: "{group}\Edit Settings"; Filename: "notepad.exe"; Parameters: """{userappdata}\{#MyAppName}\config\settings.toml"""; Comment: "Customize Dash settings"
Name: "{group}\Edit Commands"; Filename: "notepad.exe"; Parameters: """{userappdata}\{#MyAppName}\config\commands.toml"""; Comment: "Add or modify commands"
Name: "{group}\Open Config Folder"; Filename: "{userappdata}\{#MyAppName}"; Comment: "Open the configuration folder"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; Comment: "Remove Dash from your computer"

; Desktop shortcut (optional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Quick command Dash - Press Alt+F to open"

[Registry]
; Add to Windows startup (optional task)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

; Register application
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "AppDataPath"; ValueData: "{userappdata}\{#MyAppName}"; Flags: uninsdeletekey

[Run]
; Launch after installation
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill the app before uninstalling (if running)
Filename: "taskkill"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillDash"

[Code]
var
  AppDataPath: String;

// Initialize paths
function InitializeSetup(): Boolean;
begin
  AppDataPath := ExpandConstant('{userappdata}\{#MyAppName}');
  Result := True;
end;

// Copy default configs to AppData if they don't exist
procedure CurStepChanged(CurStep: TSetupStep);
var
  SettingsPath, CommandsPath, IconsPath, ConfigPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ConfigPath := AppDataPath + '\config';
    SettingsPath := ConfigPath + '\settings.toml';
    CommandsPath := ConfigPath + '\commands.toml';
    IconsPath := AppDataPath + '\assets\icons';
    
    // Create config directory first
    if not DirExists(ConfigPath) then
    begin
      if CreateDir(ConfigPath) then
        Log('Created config directory')
      else
        Log('Failed to create config directory');
    end;
    
    // Create settings.toml if it doesn't exist
    if not FileExists(SettingsPath) then
    begin
      if CopyFile(ExpandConstant('{app}\config\settings.toml'), SettingsPath, False) then
        Log('Created default settings.toml in AppData')
      else
        Log('Failed to create settings.toml in AppData');
    end;
    
    // Create commands.toml if it doesn't exist
    if not FileExists(CommandsPath) then
    begin
      if CopyFile(ExpandConstant('{app}\config\commands.toml'), CommandsPath, False) then
        Log('Created default commands.toml in AppData')
      else
        Log('Failed to create commands.toml in AppData');
    end;
    
    // Create custom icons directory
    if not DirExists(IconsPath) then
    begin
      if CreateDir(IconsPath) then
        Log('Created custom icons directory')
      else
        Log('Failed to create custom icons directory');
    end;
  end;
end;

// Custom uninstall message
function InitializeUninstall(): Boolean;
var
  Response: Integer;
begin
  AppDataPath := ExpandConstant('{userappdata}\{#MyAppName}');
  Result := True;
  
  Response := MsgBox('Do you want to keep your personal settings and commands?' + #13#10 + #13#10 + 
            'Choose "Yes" to preserve your configuration files in:' + #13#10 +
            AppDataPath + #13#10 + #13#10 +
            'Choose "No" to remove everything (fresh start).', 
            mbConfirmation, MB_YESNO or MB_DEFBUTTON1);
  
  if Response = IDNO then
  begin
    // User wants to delete everything
    if DirExists(AppDataPath) then
    begin
      DelTree(AppDataPath, True, True, True);
      Log('Deleted AppData folder at user request');
      MsgBox('All Dash data has been removed.', mbInformation, MB_OK);
    end;
  end
  else
  begin
    Log('Preserved AppData folder at user request');
    MsgBox('Your settings and commands have been preserved in:' + #13#10 + 
           AppDataPath + #13#10 + #13#10 +
           'You can manually delete this folder if needed.', 
           mbInformation, MB_OK);
  end;
end;

// Show finish message with helpful info
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption := 
      'Setup has finished installing Dash on your computer.' + #13#10 + #13#10 +
      'Quick Start Guide:' + #13#10 +
      '• Press Alt+F anywhere to open the launcher' + #13#10 +
      '• Type to search your commands instantly' + #13#10 +
      '• Right-click the system tray icon for settings' + #13#10 +
      '• Edit commands.toml to add your own shortcuts' + #13#10 + #13#10 +
      'Your configuration files are in:' + #13#10 +
      AppDataPath + #13#10 + #13#10 +
      'Click Finish to close Setup.';
  end;
end;

// Welcome message
procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption := 
    'This will install Dash {#MyAppVersion} on your computer.' + #13#10 + #13#10 +
    'Dash is a quick command launcher that lets you:' + #13#10 +
    '• Open files, folders, and websites instantly' + #13#10 +
    '• Launch applications with keyboard shortcuts' + #13#10 +
    '• Perform calculations on the fly' + #13#10 +
    '• Customize everything with simple config files' + #13#10 + #13#10 +
    'Press Alt+F to open the launcher anytime!' + #13#10 + #13#10 +
    'Click Next to continue.';
end;