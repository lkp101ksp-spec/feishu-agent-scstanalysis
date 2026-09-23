# 一键注册 ws_guardian 到 Windows 计划任务（登录自启、独立于会话/沙箱）。
# 必须在普通终端（非 agent 沙箱）执行——沙箱内 Register-ScheduledTask 会被拒（拒绝访问）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\install_guardian_task.ps1
# 卸载：powershell -ExecutionPolicy Bypass -File scripts\install_guardian_task.ps1 -Uninstall
# 2026-09-23 沉淀：会话绑定实例随会话死亡；沙箱作业对象会连坐击杀子进程，
# 计划任务在沙箱外运行，异步清扫（ws_guardian_sweep.log）才能真正生效。
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$taskName = "FeishuAgentGuardian"

# 统一失败出口：红字 + exit 2
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 2 }

# ---------- 0) 自提权：注册计划任务需管理员（2026-09-23 实测 UAC 未提权终端同样 Access denied）----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "当前非管理员，弹 UAC 提权窗口继续..." -ForegroundColor Yellow
    $arg = "-NoProfile -ExecutionPolicy Bypass -Command `"& '$PSCommandPath'"
    if ($Uninstall) { $arg += " -Uninstall" }
    $arg += "; pause`""
    Start-Process powershell -Verb RunAs -ArgumentList $arg
    Write-Host "已转交提权窗口执行，本窗口可直接关闭。"
    exit 0
}

# ---------- 卸载路径 ----------
if ($Uninstall) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[PASS] 计划任务 $taskName 已卸载" -ForegroundColor Green
    exit 0
}

# ---------- 1) 前置存在性 ----------
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { Fail "未找到 .venv，先执行 scripts\setup.ps1" }
$guardianPy = Join-Path $root "scripts\ws_guardian.py"
if (-not (Test-Path $guardianPy)) { Fail "未找到 scripts\ws_guardian.py" }

# ---------- 2) 注册计划任务（-Force 幂等覆盖）----------
Write-Host "== [1/3] 注册计划任务 $taskName ==" -ForegroundColor Cyan
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$guardianPy`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Description "飞书agent ws_client 守护进程（登录自启，详见 scripts/ws_guardian.py）" `
    -Force | Out-Null
Write-Host "已注册（登录时自启，当前用户上下文）"

# ---------- 3) 立即启动一次 ----------
Write-Host "`n== [2/3] 立即启动 ==" -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName

# ---------- 4) 启动确认（最多 90s：冷启动 import 链约 20s；看 pidfile + 启动日志）----------
Write-Host "`n== [3/3] 启动确认 ==" -ForegroundColor Cyan
$logFile = Join-Path $root "logs\ws_guardian.log"
$pidFile = Join-Path $root ".ws_guardian.pid"
$ok = $false
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path $logFile) -and
        (Select-String -Path $logFile -Pattern "ws guardian started" -SimpleMatch -Quiet -ErrorAction SilentlyContinue)) {
        # 需确认是本次新写：日志 mtime 在 90s 内
        if ((Get-Item $logFile).LastWriteTime -gt (Get-Date).AddSeconds(-90)) { $ok = $true; break }
    }
    Start-Sleep -Seconds 2
}
if (-not $ok) { Fail "90s 内未见启动日志，请查 logs\ws_guardian.log（任务已注册，下次登录仍会自启）" }
$gpid = (Get-Content $pidFile).Trim()
Write-Host "`n[PASS] guardian 已由计划任务拉起：pid=$gpid" -ForegroundColor Green
Write-Host "心跳：logs\ws_guardian.heartbeat（每 60s 刷新）；清扫数：logs\ws_guardian_sweep.log（沙箱外启动才会落盘）"
Write-Host "卸载：scripts\install_guardian_task.ps1 -Uninstall；查状态：Get-ScheduledTask -TaskName $taskName"
