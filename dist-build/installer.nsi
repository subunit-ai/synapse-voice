; Sonar — Windows installer (NSIS).
; Wraps the PyInstaller bundle from dist\synapse-voice\ into a polished Setup.exe.
;
; v0.9.18: rebranded Synapse Voice → Sonar everywhere user-visible. The
; underlying PyInstaller output dir + binary keep the synapse-voice name
; to avoid a coordinated build.yml refactor (the .exe inside is what
; Windows shows in Process Manager; everything in Start Menu / Programs /
; install path is now "Sonar").
;
; Build (on Windows or via GitHub Actions windows-latest with NSIS installed):
;   makensis dist-build\installer.nsi
;
; Output: dist\Sonar-Setup-<version><arch>.exe

!define APP_NAME       "Sonar"
!define APP_PUBLISHER  "Subunit"
!define APP_EXEC       "synapse-voice.exe"
!define APP_REGKEY     "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define APP_RUNKEY     "Software\Microsoft\Windows\CurrentVersion\Run"
; Legacy registry / install path from the Synapse Voice era — used during
; uninstall + clean-old-install so users who upgrade from < v0.9.18 don't
; end up with two copies in Add/Remove Programs.
!define LEGACY_APP_NAME  "Synapse Voice"
!define LEGACY_APP_REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${LEGACY_APP_NAME}"

; Version is passed in via /DAPP_VERSION=x.y.z when invoking makensis.
!ifndef APP_VERSION
  !define APP_VERSION "0.0.0"
!endif

; Optional architecture suffix appended to the installer filename so we
; can ship parallel x64 + ARM64 setups without filename collisions.
; Pass /DARCH_SUFFIX=-arm64 (or empty) when invoking makensis.
!ifndef ARCH_SUFFIX
  !define ARCH_SUFFIX ""
!endif

!include "MUI2.nsh"

Name "${APP_NAME}"
OutFile "..\dist\Sonar-Setup-${APP_VERSION}${ARCH_SUFFIX}.exe"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "${APP_REGKEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} installer"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "(c) ${APP_PUBLISHER}"

!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXEC}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "German"

Section "Sonar" SecCore
  SectionIn RO

  ; Kill any running instance before overwriting binaries (the .exe is locked
  ; by Windows while it's running → "Fehler beim Überschreiben der Datei").
  ; /F = force, /T = also kill children, errors silently ignored if not found.
  DetailPrint "Stopping any running ${APP_NAME} instance..."
  nsExec::Exec 'taskkill /F /IM "${APP_EXEC}" /T'
  Sleep 800

  ; v0.9.18: clean up the Synapse Voice-era install if present so users
  ; who upgrade from < v0.9.18 don't end up with two copies in Add/Remove
  ; Programs. Best-effort — we don't want a missing legacy install to
  ; block the new one.
  ReadRegStr $0 HKLM "${LEGACY_APP_REGKEY}" "UninstallString"
  StrCmp $0 "" no_legacy
    DetailPrint "Removing legacy Synapse Voice install..."
    ExecWait '"$0" /S'
    DeleteRegKey HKLM "${LEGACY_APP_REGKEY}"
    DeleteRegValue HKCU "${APP_RUNKEY}" "${LEGACY_APP_NAME}"
  no_legacy:

  SetOverwrite try
  SetOutPath "$INSTDIR"
  ; Copy entire PyInstaller output tree
  File /r "..\dist\synapse-voice\*.*"

  ; Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXEC}"
  CreateShortCut  "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"

  ; Desktop shortcut
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXEC}"

  ; Uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Add/Remove Programs entry
  WriteRegStr HKLM "${APP_REGKEY}" "DisplayName"     "${APP_NAME}"
  WriteRegStr HKLM "${APP_REGKEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr HKLM "${APP_REGKEY}" "Publisher"       "${APP_PUBLISHER}"
  WriteRegStr HKLM "${APP_REGKEY}" "DisplayIcon"     "$INSTDIR\${APP_EXEC}"
  WriteRegStr HKLM "${APP_REGKEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "${APP_REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKLM "${APP_REGKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${APP_REGKEY}" "NoRepair" 1
SectionEnd

Section "Start with Windows" SecAutostart
  WriteRegStr HKCU "${APP_RUNKEY}" "${APP_NAME}" "$INSTDIR\${APP_EXEC}"
SectionEnd

LangString DESC_SecCore      ${LANG_ENGLISH} "Core application files (required)."
LangString DESC_SecAutostart ${LANG_ENGLISH} "Launch ${APP_NAME} automatically when you sign into Windows."
LangString DESC_SecCore      ${LANG_GERMAN}  "Programmdateien (erforderlich)."
LangString DESC_SecAutostart ${LANG_GERMAN}  "${APP_NAME} automatisch beim Windows-Start ausführen."

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore}      $(DESC_SecCore)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAutostart} $(DESC_SecAutostart)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  ; Stop the app if it's running
  ExecWait 'taskkill /F /IM "${APP_EXEC}"'

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"

  ; Autostart entry
  DeleteRegValue HKCU "${APP_RUNKEY}" "${APP_NAME}"

  ; Program files
  RMDir /r "$INSTDIR"

  ; Add/Remove Programs
  DeleteRegKey HKLM "${APP_REGKEY}"
SectionEnd
