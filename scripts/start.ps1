# 一键启动脚本：预检（venv/.env/环境校验/PG）→ 后台启动 ws_client（--force 接管旧实例）→ 确认启动日志
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start.ps1
# 停止：taskkill /PID <pid> /F（pid 见输出，或读 .ws_client.pid）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 统一失败出口：红字 + exit 2
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 2 }

# ---------- 1) 前置存在性（缺则引导走 setup）----------
if (-not (Test-Path ".venv\Scripts\python.exe")) { Fail "未找到 .venv，先执行 scripts\setup.ps1" }
if (-not (Test-Path ".env")) { Fail "未找到 .env，先执行 scripts\setup.ps1（会从 .env.example 生成）" }
$vpy = ".venv\Scripts\python.exe"

# ---------- 2) 环境校验（硬门禁：缺必填项直接退出，避免半启动）----------
Write-Host "== [1/4] config_check 环境校验 ==" -ForegroundColor Cyan
& $vpy scripts\config_check.py --env-file .env
if ($LASTEXITCODE -ne 0) { Fail "config_check 未通过（检查 .env）" }

# ---------- 3) PostgreSQL（未起则自动拉起；幂等无副作用）----------
Write-Host "`n== [2/4] PostgreSQL ==" -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker 未运行，请先启动 Docker Desktop" }
docker compose up -d postgres
if ($LASTEXITCODE -ne 0) { Fail "postgres 容器启动失败（首次部署请先跑 scripts\setup.ps1）" }
$cid = (docker compose ps -q postgres)
if (-not $cid) { Fail "未找到 postgres 容器，请先跑 scripts\setup.ps1" }
$health = (docker inspect --format "{{.State.Health.Status}}" $cid).Trim()
if ($health -ne "healthy") {
    # 刚拉起时等就绪（已 healthy 则零等待）
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $health = (docker inspect --format "{{.State.Health.Status}}" $cid).Trim()
        if ($health -eq "healthy") { break }
        Start-Sleep -Seconds 2
    }
    if ($health -ne "healthy") { Fail "postgres 未就绪（$health）" }
}
Write-Host "postgres healthy"

# ---------- 4) 后台启动 ws_client（单实例守卫 + --force 接管旧实例）----------
Write-Host "`n== [3/4] 启动 ws_client ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$outLog = Join-Path $root "logs\ws_client.out.log"
$errLog = Join-Path $root "logs\ws_client.err.log"
$proc = Start-Process -FilePath (Resolve-Path $vpy).Path `
    -ArgumentList "-m", "gateway.ws_client", "--force" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "已拉起进程 pid=$($proc.Id)（--force：旧实例在跑会被接管替换）"

# ---------- 5) 启动确认（最多 60s：冷启动 import 重依赖链约 20s；进程存活 + 启动日志行）----------
Write-Host "`n== [4/4] 启动确认 ==" -ForegroundColor Cyan
$ok = $false
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
        Write-Host "--- stderr 尾部 ---"
        Get-Content $errLog -Tail 20 -ErrorAction SilentlyContinue
        Fail "ws_client 进程已退出（exit=$($proc.ExitCode)），完整日志见 $errLog"
    }
    foreach ($f in @($outLog, $errLog)) {
        if ((Test-Path $f) -and (Select-String -Path $f -Pattern "ws long-connection starting" -SimpleMatch -Quiet -ErrorAction SilentlyContinue)) {
            $ok = $true; break
        }
    }
    if ($ok) { break }
    Start-Sleep -Seconds 1
}
if (-not $ok) { Fail "60s 内未见启动日志（进程 pid=$($proc.Id) 仍在跑，请查 logs\ws_client.err.log）" }
$head = (git rev-parse --short HEAD).Trim()
Write-Host "`n[PASS] ws_client 已启动：pid=$($proc.Id) HEAD=$head" -ForegroundColor Green
Write-Host "（验收纪律：核对进程启动时间晚于最新 commit——本轮即 HEAD=$head）"
Write-Host "日志：logs\ws_client.err.log（logging 输出）/ logs\ws_client.out.log；停止：taskkill /PID $($proc.Id) /T /F（/T 连同 venv 重定向的真实解释器子进程一起结束）"
