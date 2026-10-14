# HEC-RAS modal-dialog watchdog.
#
# WHY THIS EXISTS
# ---------------
# When HEC-RAS is given invalid input it pops up a MODAL dialog, and the COM
# call blocks on it forever. The automation script then neither gets the error
# text nor can continue. This has wedged the process twice during development.
#
# This script polls all top-level windows while the probe runs. When it finds a
# HEC-RAS dialog it dumps the text (so the error is not lost) and closes it (so
# COM unblocks).
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 using the
# system ANSI codepage unless the file has a UTF-8 BOM, which mangles any
# non-ASCII characters and can break string literals at parse time.
#
# Usage: powershell -ExecutionPolicy Bypass -File watch_dialogs.ps1 -Seconds 150

param([int]$Seconds = 150)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Close {
    [DllImport("user32.dll")]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
}
"@

$WM_CLOSE = 0x0010
$deadline = (Get-Date).AddSeconds($Seconds)
$seen = @{}
$found = 0

Write-Host "watchdog started, monitoring for $Seconds seconds ..."
$root = [System.Windows.Automation.AutomationElement]::RootElement

while ((Get-Date) -lt $deadline) {
    try {
        $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
                              [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $wins) {
            $wpid = $w.Current.ProcessId
            $name = $w.Current.Name
            $cls  = $w.Current.ClassName

            $proc = Get-Process -Id $wpid -ErrorAction SilentlyContinue
            if (-not $proc) { continue }
            $pname = $proc.ProcessName

            if ($pname -notmatch '^Ras' -and $pname -notmatch 'PipeServer') { continue }
            if ($cls -ne '#32770') { continue }

            $key = "$wpid|$name"
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            $found = $found + 1

            Write-Host ""
            Write-Host ("=" * 68)
            Write-Host "CAUGHT HEC-RAS DIALOG  pid=$wpid  proc=$pname"
            Write-Host ("=" * 68)
            if ($name) { Write-Host "title: $name" }

            $kids = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                               [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($e in $kids) {
                $t = $e.Current.Name
                if ($t -and $t.Trim().Length -gt 0) {
                    Write-Host "   > $t"
                }
            }
            Write-Host ("=" * 68)

            try {
                [Win32Close]::SendMessage([IntPtr]$w.Current.NativeWindowHandle, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
                Write-Host "close signal sent"
            } catch {
                Write-Host "close failed: $_"
            }
        }
    } catch {
        # window enumeration can fail while windows are being destroyed; ignore
    }
    Start-Sleep -Milliseconds 1200
}

Write-Host ""
Write-Host "watchdog done, caught $found dialog(s)"
