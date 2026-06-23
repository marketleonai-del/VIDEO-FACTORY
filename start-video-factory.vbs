' start-video-factory.vbs — run the video-factory server in the BACKGROUND (no window, can't be closed by accident), then open the page.
Set sh = CreateObject("WScript.Shell")
p = WScript.ScriptFullName
d = Left(p, InStrRev(p, "\"))
' 0 = hidden window, False = do not wait -> detached background process, survives
sh.Run "cmd /c node """ & d & "live-server.js""", 0, False
WScript.Sleep 3500
' open the page (will connect to localhost:8088)
sh.Run """" & d & "video-factory.html""", 1, False
