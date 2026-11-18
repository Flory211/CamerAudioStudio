# -*- coding: utf-8 -*-
Unicode true
RequestExecutionLevel user
SetCompressor zlib

!include "MUI2.nsh"
!include "x64.nsh"

!define APP_NAME "Камер Аудио Студио"
!define APP_VERSION "2.0 Alpha"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamerAudioStudio"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${SETUP_EXE}"
InstallDir "$LOCALAPPDATA\Programs\CamerAudioStudio"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
VIProductVersion "2.0.0.0"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\CamerAudioStudio.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

VIAddVersionKey /LANG=${LANG_RUSSIAN} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_RUSSIAN} "FileDescription" "Установщик «${APP_NAME}»"
VIAddVersionKey /LANG=${LANG_RUSSIAN} "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_RUSSIAN} "LegalCopyright" "Copyright (c) 2026 CamerAudioStudio"

Function .onInit
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONSTOP "Для установки нужна 64-битная Windows." /SD IDOK
        SetErrorLevel 1
        Abort
    ${EndIf}
FunctionEnd

Section "Install"
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    File /r "${PAYLOAD_DIR}\*"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\CamerAudioStudio.exe"

    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\CamerAudioStudio.exe"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
    SetShellVarContext current
    !include "${UNINSTALL_FILES}"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    Delete "$SMPROGRAMS\${APP_NAME}.lnk"
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
