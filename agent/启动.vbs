' 双击此文件启动：无黑色命令行窗口
Option Explicit

Dim sh, fso, root
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c """ & root & "\start_silent.bat""", 0, False
