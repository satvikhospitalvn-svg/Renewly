[Setup]
AppName=Renewly
AppVersion=1.0
AppPublisher=Renewly
DefaultDirName={autopf}\Renewly
DefaultGroupName=Renewly
UninstallDisplayName=Renewly
OutputDir=Output
OutputBaseFilename=Renewly_Setup
SetupIconFile=renewly.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "dist\Renewly\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Renewly"; Filename: "{app}\Renewly.exe"
Name: "{autodesktop}\Renewly"; Filename: "{app}\Renewly.exe"

[Run]
Filename: "{app}\Renewly.exe"; Description: "Launch Renewly"; Flags: nowait postinstall skipifsilent
