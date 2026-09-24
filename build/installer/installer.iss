; Dash Installer Script for Inno Setup
; Calem Young

#define MyAppName "Dash"
; The version is supplied by build/scripts/build_installer.py from
; build/installer/version.txt via ISCC /DMyAppVersion=<version>. The fallback
; below only applies when this script is compiled directly.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
; Windows version resources need four plain integers; the build script
; derives this from MyAppVersion so a label like "2.9.0-dev" still compiles.
#ifndef MyAppNumericVersion
  #define MyAppNumericVersion "0.0.0.0"
#endif
#define MyAppPublisher "Calem Young"
#define MyAppURL "https://github.com/calemyoung/Dash"
#define MyAppExeName "Dash.exe"
#define MyAppDescription "A quick program launcher for Windows"
; Copyright years run from the first release (2025) to the year of the
; build, so they never go stale. build_installer.py passes the same value
; here and to the exe's version resource; the fallback covers compiling this
; script directly.
#ifndef MyAppCopyrightYears
  #define MyAppCopyrightYears "2025-" + GetDateTimeString('yyyy', '', '')
#endif

[Setup]
; Note the doubled closing brace: Inno escapes "{{" to "{" but leaves "}}"
; as is, so the real AppId (and the Add/Remove Programs key, and the winget
; ProductCode) is "{88DC9BD4-16FC-452E-87DD-4B0F54603ED1}}". It has been
; that way since the first release. Do not "fix" it: a changed AppId makes
; every existing install look like a different product.
AppId={{88DC9BD4-16FC-452E-87DD-4B0F54603ED1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) {#MyAppCopyrightYears} {#MyAppPublisher}

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
VersionInfoVersion={#MyAppNumericVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDescription}
VersionInfoCopyright=Copyright (C) {#MyAppCopyrightYears} {#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppNumericVersion}

; Uninstall
Uninstallable=yes
UninstallDisplayName={#MyAppName}

; Architecture
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Ticked by default: Dash is a background hotkey launcher, so it is only
; useful once it is already running when you press its hotkey.
Name: "startup"; Description: "Start {#MyAppName} automatically when Windows starts"; GroupDescription: "Startup Options:"

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

[InstallDelete]
; Earlier versions added Start Menu shortcuts that opened the raw settings
; and commands files in Notepad. Upgrades remove them; both are edited in Dash.
Type: files; Name: "{group}\Edit Settings.lnk"
Type: files; Name: "{group}\Edit Commands.lnk"

[Icons]
; Start menu shortcuts
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Open Dash, the keyboard launcher"
; Settings and commands are edited inside Dash (Ctrl+, and Ctrl+Enter), so
; there are no shortcuts that open the raw TOML files in Notepad.
Name: "{group}\Open Config Folder"; Filename: "{userappdata}\{#MyAppName}"; Comment: "Open the configuration folder"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; Comment: "Remove Dash from your computer"

; Desktop shortcut (optional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Open Dash, the keyboard launcher"

[Registry]
; Add to Windows startup (optional task). --startup tells Dash it was started
; by Windows, so it stays in the tray; any other launch shows the search bar.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --startup"; Flags: uninsdeletevalue; Tasks: startup
; Reinstalling or upgrading with the task unticked removes an earlier entry.
; Dash itself never writes to the Run key, so "Dash" is the only value name
; to clean up.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#MyAppName}"; Flags: deletevalue; Tasks: not startup

; Register application
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "AppDataPath"; ValueData: "{userappdata}\{#MyAppName}"; Flags: uninsdeletekey

[Run]
; Launch after installation. Interactive installs offer it as a checkbox.
; Silent installs launch only when asked with /RELAUNCH=1, which is how
; Dash's in-app updater restarts the app; package managers such as winget
; run silently without it and must not have an app pop up mid-install.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall; Check: ShouldLaunchAfterInstall

[UninstallRun]
; Kill the app before uninstalling (if running)
Filename: "taskkill"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillDash"

[Code]
var
  AppDataPath: String;
  DeleteUserData: Boolean;

// Silent installs stay silent unless the caller asks for a relaunch.
function ShouldLaunchAfterInstall(): Boolean;
begin
  Result := (not WizardSilent) or (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;

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

// Ask whether to delete the user's data as well. Nothing is deleted here:
// the choice is only recorded, and the folder is removed after the
// uninstall has finished (see CurUninstallStepChanged), so cancelling or a
// failed uninstall never costs anyone their commands.
//
// Silent uninstalls (winget uninstall, /SILENT, /VERYSILENT) never prompt
// and always keep the data. SuppressibleMsgBox returns the default (IDNO,
// keep) when /SUPPRESSMSGBOXES is given.
function InitializeUninstall(): Boolean;
begin
  AppDataPath := ExpandConstant('{userappdata}\{#MyAppName}');
  DeleteUserData := False;
  Result := True;

  if UninstallSilent() or not DirExists(AppDataPath) then
    Exit;

  DeleteUserData := SuppressibleMsgBox(
    'Also delete your Dash settings and commands?' + #13#10 + #13#10 +
    'Yes: delete them, along with custom icons, usage history and logs, from:' + #13#10 +
    AppDataPath + #13#10 + #13#10 +
    'No: keep them, so reinstalling Dash later picks up where you left off.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;

  if DeleteUserData then
    Log('User chose to delete the AppData folder after uninstall')
  else
    Log('User chose to keep the AppData folder');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  TempDashPath: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  // Installers downloaded by the in-app updater. src/updater.py saves them
  // under tempfile.gettempdir()\Dash\updates, which is the user's %TEMP%.
  // GetTempDir follows %TEMP% even when it has been moved, which a fixed
  // {localappdata}\Temp entry in [UninstallDelete] would not.
  TempDashPath := AddBackslash(GetTempDir()) + '{#MyAppName}';
  if DirExists(TempDashPath + '\updates') then
    DelTree(TempDashPath + '\updates', True, True, True);
  RemoveDir(TempDashPath);

  if DeleteUserData and DirExists(AppDataPath) then
  begin
    if DelTree(AppDataPath, True, True, True) then
      Log('Deleted AppData folder at user request')
    else
      Log('Could not delete all of the AppData folder');
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
      '• Press Alt+Space (unless you have chosen another hotkey) to open Dash' + #13#10 +
      '• Type to search your commands instantly' + #13#10 +
      '• Press Ctrl+, in Dash to open Settings' + #13#10 +
      '• Press Ctrl+N in Dash to add a command' + #13#10 + #13#10 +
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
    '• Customize hotkeys, icons, colors and more in Settings' + #13#10 + #13#10 +
    'Press Alt+Space to open the launcher anytime.' + #13#10 + #13#10 +
    'Click Next to continue.';
end;