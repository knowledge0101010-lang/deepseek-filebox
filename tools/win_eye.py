r"""Windows "eye" — one place to observe the whole OS (read-only).

What already existed before this file
  * `operator_mcp.py`  — the SCREEN (capture) and HANDS (mouse/keyboard)
  * `window_ctl.py`    — every top-level WINDOW (title, rect, state, focus)
  * filebox            — the FILESYSTEM index (one SQLite file)
  * DSH `pwsh`         — full shell reach

What this file adds: the SYSTEM layer — processes, services, disks, network, event log,
startup items, installed software, sessions, environment. Together they are a full
observation surface for Windows: screen + windows + files + OS state.

Everything here is READ-ONLY. Nothing is started, stopped, killed or modified.

usage:
  python tools/win_eye.py overview
  python tools/win_eye.py processes --top 15 [--sort cpu|mem]
  python tools/win_eye.py services [--state running] [--limit 20]
  python tools/win_eye.py disk
  python tools/win_eye.py network
  python tools/win_eye.py events --level error --last 15
  python tools/win_eye.py startup
  python tools/win_eye.py software --limit 30
  python tools/win_eye.py sessions
  python tools/win_eye.py env
  python tools/win_eye.py all            # compact digest of everything above
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


def ps(script: str, timeout: int = 60):
    """Run a PowerShell snippet that emits JSON; return the parsed object (or raw text)."""
    p = subprocess.run(PS + [script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "").strip()
    if not out:
        return {"error": (p.stderr or "no output").strip()[:300]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out[:2000]}


def overview() -> dict:
    return ps(r"""
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
[pscustomobject]@{
  hostname   = $env:COMPUTERNAME
  user       = "$env:USERDOMAIN\$env:USERNAME"
  os         = $os.Caption
  version    = $os.Version
  build      = $os.BuildNumber
  uptime_h   = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
  cpu        = $cpu.Name
  cores      = $cpu.NumberOfCores
  threads    = $cpu.NumberOfLogicalProcessors
  cpu_pct    = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor |
                Where-Object Name -eq '_Total').PercentProcessorTime
  ram_total_gb = [math]::Round($os.TotalVisibleMemorySize/1MB, 1)
  ram_free_gb  = [math]::Round($os.FreePhysicalMemory/1MB, 1)
  processes  = (Get-Process).Count
  gpu        = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join '; '
} | ConvertTo-Json -Depth 4
""")


def processes(top: int, sort: str) -> dict:
    key = "CPU" if sort == "cpu" else "WorkingSet64"
    return ps(rf"""
Get-Process | Sort-Object {key} -Descending | Select-Object -First {top} |
  Select-Object Id, ProcessName,
    @{{n='cpu_s';e={{[math]::Round($_.CPU,1)}}}},
    @{{n='mem_mb';e={{[math]::Round($_.WorkingSet64/1MB,1)}}}},
    @{{n='responding';e={{$_.Responding}}}},
    @{{n='started';e={{ if ($_.StartTime) {{ $_.StartTime.ToString('MM-dd HH:mm') }} else {{ '-' }} }} }} |
  ConvertTo-Json -Depth 3
""")


def services(state: str | None, limit: int) -> dict:
    filt = f"Where-Object {{ $_.State -eq '{state}' }}" if state else ""
    return ps(rf"""
Get-Service | {filt} | Sort-Object Status, DisplayName | Select-Object -First {limit} |
  Select-Object Name, DisplayName, Status, StartType | ConvertTo-Json -Depth 3
""")


def disk() -> dict:
    return ps(r"""
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
  Select-Object DeviceID, @{n='size_gb';e={[math]::Round($_.Size/1GB,1)}},
    @{n='free_gb';e={[math]::Round($_.FreeSpace/1GB,1)}},
    @{n='free_pct';e={ if ($_.Size) { [math]::Round(100*$_.FreeSpace/$_.Size,1) } else { 0 } }} |
  ConvertTo-Json -Depth 3
""")


def network() -> dict:
    return ps(r"""
$adapters = Get-NetAdapter | Where-Object Status -eq 'Up' |
  Select-Object Name, InterfaceDescription, @{n='mbps';e={$_.LinkSpeed}}
$listen = Get-NetTCPConnection -State Listen |
  Group-Object LocalPort | ForEach-Object { $_.Name } | Sort-Object {[int]$_} |
  Select-Object -First 40
$est = Get-NetTCPConnection -State Established | Select-Object -First 25 |
  Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort,
    @{n='pid';e={$_.OwningProcess}}
[pscustomobject]@{ adapters=$adapters; listening_ports=$listen; established=$est } |
  ConvertTo-Json -Depth 4
""")


def events(level: str, last: int) -> dict:
    return ps(rf"""
Get-WinEvent -FilterHashtable @{{LogName='System','Application'; Level={ {1:'Critical',2:'Error',3:'Warning',4:'Information'}[level]!r} }} -MaxEvents {last} -ErrorAction SilentlyContinue |
  Select-Object TimeCreated, ProviderName, Id, LevelDisplayName,
    @{{n='message';e={{ ($_.Message -split "`n")[0].Substring(0, [Math]::Min(160, ($_.Message -split "`n")[0].Length)) }} }} |
  ConvertTo-Json -Depth 3
""")


def startup() -> dict:
    return ps(r"""
$run = @()
foreach ($hive in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run') {
  if (Test-Path $hive) {
    $p = Get-ItemProperty $hive
    $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
      ForEach-Object { $run += [pscustomobject]@{ hive=$hive; name=$_.Name; cmd=$_.Value } }
  }
}
$startupFolder = Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" `
  -File -ErrorAction SilentlyContinue | Select-Object Name
[pscustomobject]@{ run_keys=$run; startup_folder=$startupFolder } | ConvertTo-Json -Depth 4
""")


def software(limit: int) -> dict:
    return ps(rf"""
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
Get-ItemProperty $keys -ErrorAction SilentlyContinue |
  Where-Object DisplayName | Select-Object -Unique DisplayName, DisplayVersion, Publisher |
  Sort-Object DisplayName | Select-Object -First {limit} | ConvertTo-Json -Depth 3
""")


def sessions() -> dict:
    return ps(r"""
[q] 2>$null | Out-Null
Get-CimInstance Win32_LogonSession | Select-Object -First 10 LogonId, LogonType, StartTime |
  ConvertTo-Json -Depth 3
""")


def env() -> dict:
    return ps(r"""
[pscustomobject]@{
  user = $env:USERNAME
  domain = $env:USERDOMAIN
  home = $env:USERPROFILE
  temp = $env:TEMP
  path_count = ($env:PATH -split ';').Count
  ps_version = $PSVersionTable.PSVersion.ToString()
  culture = (Get-Culture).Name
  timezone = (Get-TimeZone).Id
  admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
      [Security.Principal.WindowsBuiltInRole]::Administrator)
} | ConvertTo-Json -Depth 3
""")


def digest() -> dict:
    return {"overview": overview(), "disk": disk(),
            "processes_top": processes(10, "mem"), "events_recent": events("error", 8)}


COMMANDS = {
    "overview": lambda a: overview(),
    "processes": lambda a: processes(a.top, a.sort),
    "services": lambda a: services(a.state, a.limit),
    "disk": lambda a: disk(),
    "network": lambda a: network(),
    "events": lambda a: events(a.level, a.last),
    "startup": lambda a: startup(),
    "software": lambda a: software(a.limit),
    "sessions": lambda a: sessions(),
    "env": lambda a: env(),
    "all": lambda a: digest(),
}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="read-only Windows observability")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("overview"); sub.add_parser("disk"); sub.add_parser("network")
    sub.add_parser("startup"); sub.add_parser("sessions"); sub.add_parser("env")
    sub.add_parser("all")
    pp = sub.add_parser("processes")
    pp.add_argument("--top", type=int, default=15); pp.add_argument("--sort", choices=["cpu", "mem"], default="mem")
    sp = sub.add_parser("services")
    sp.add_argument("--state", default=None); sp.add_argument("--limit", type=int, default=20)
    ep = sub.add_parser("events")
    ep.add_argument("--level", choices=["critical", "error", "warning", "information"], default="error")
    ep.add_argument("--last", type=int, default=15)
    sfp = sub.add_parser("software"); sfp.add_argument("--limit", type=int, default=30)
    a = ap.parse_args()
    try:
        result = COMMANDS[a.cmd](a)
    except subprocess.TimeoutExpired:
        result = {"error": "PowerShell query timed out"}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
