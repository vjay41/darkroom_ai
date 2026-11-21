# Prints Windows Firewall commands for the offline proof. THIS SCRIPT CHANGES NOTHING:
# it only writes the commands to the screen. Review them, then run them yourself in an
# elevated PowerShell if you want the block, and remove the rules afterwards.
#
# Idea: block ALL outbound traffic of the Python programs darkroom_ai runs in. Windows Firewall
# does not filter loopback (127.0.0.1) traffic, so Lightroom, Photoshop and the local model
# server stay reachable. If darkroom_ai still works with the block on, it needs no network.
#
# Usage:  powershell -File scripts\firewall_rules.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Output "Not found: $venvPython (run 'make install' first)."
    exit 1
}
# The .venv python.exe starts the base interpreter, which is the program that would open a
# connection. Ask Python for both paths (this runs Python once, offline).
# The base path can be a Windows 'app execution alias'; a firewall rule must name the real
# program file, so the running process is asked for its own image path as well.
$code = 'import ctypes, sys; b = ctypes.create_unicode_buffer(32768); ctypes.windll.kernel32.GetModuleFileNameW(None, b, 32768); print(sys.executable); print(sys._base_executable); print(b.value)'
$paths = & $venvPython -I -c $code
$programs = $paths | Where-Object { $_ } | Sort-Object -Unique
$name = 'darkroom_ai offline proof'

Write-Output '# --- 1. Add the block (elevated PowerShell) ---'
$index = 0
foreach ($program in $programs) {
    $index += 1
    Write-Output "New-NetFirewallRule -DisplayName '$name $index' -Direction Outbound -Program '$program' -Action Block -Profile Any"
}
Write-Output ''
Write-Output '# --- 2. Check that the rules exist ---'
Write-Output "Get-NetFirewallRule -DisplayName '$name*' | Format-Table DisplayName, Enabled, Direction, Action"
Write-Output ''
Write-Output '# --- 3. Optional: confirm the block works (this one line TRIES to reach the internet and should fail) ---'
Write-Output "& '$venvPython' -c `"import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)`""
Write-Output ''
Write-Output '# --- 4. Run darkroom_ai (normal PowerShell, in the repository) ---'
Write-Output 'poetry run darkroom doctor'
Write-Output 'poetry run python scripts\offline_proof.py'
Write-Output ''
Write-Output '# --- 5. Remove the block again (elevated PowerShell) ---'
Write-Output "Remove-NetFirewallRule -DisplayName '$name*'"
Write-Output ''
Write-Output '# Note: while the block is on, pip, poetry and pip-audit cannot reach the internet from'
Write-Output '# these Python programs either. Remove the rules before installing or auditing packages.'
