' 后台启动 NapCat（扫码登录模式，不传 QQ 号）
Option Explicit

Dim sh, fso, toolsDir, napcatDir, argUin
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
toolsDir = fso.GetParentFolderName(WScript.ScriptFullName)
napcatDir = toolsDir & "\NapCat"

If WScript.Arguments.Count > 0 Then
  argUin = WScript.Arguments(0)
Else
  argUin = ""
End If

' 仅结束 NapCat 引导进程，不关闭用户已打开的 QQ 电脑版
sh.Run "taskkill /F /IM NapCatWinBootMain.exe /T", 0, True
WScript.Sleep 2000

If argUin <> "" Then
  sh.Run "cmd /c cd /d """ & napcatDir & """ && call launcher-win10-user.bat " & argUin, 0, False
Else
  sh.Run "cmd /c cd /d """ & napcatDir & """ && call launcher-win10-user.bat", 0, False
End If
