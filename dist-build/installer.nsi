; Synapse Voice — Windows installer (NSIS).
; Wraps the PyInstaller bundle from dist\synapse-voice\ into a polished Setup.exe.
;
; Build (on Windows or via GitHub Actions windows-latest with NSIS installed):
;   makensis dist-build\installer.nsi
;
; Output: dist\SynapseVoice-Setup.exe

!define APP_NAME       "Synapse Voice"
!define APP_PUBLISHER  "subunit"
!define APP_EXEC       "synapse-voice.exe"
!define APP_REGKEY     "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define APP_RUNKEY     "Software\Microsoft\Windows\CurrentVersion\Run"

; Version is passed in via /DAPP_VERSION=x.y.z when invoking makensis.
!ifndef APP_VERSION
  !define APP_VERSION "0.0.0"
!endif

!include "MUI2.nsh"

Name "${APP_NAME}"
OutFile "..\dist\SynapseVoice-Setup-${APP_VERSION}.exe"
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

Section "Synapse Voice" SecCore
  SectionIn RO
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
