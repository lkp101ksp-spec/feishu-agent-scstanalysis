# 一键部署/安装脚本：Python 检查 → venv → 依赖（清华源）→ .env → 环境校验 → PG 容器 → 数据库迁移
# 用法：powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# 幂等：已完成步骤自动跳过/无副作用，可重复执行（改完 .env 后重跑即可续上）。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$TunaMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"

# 统一失败出口：红字 + exit 2
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 2 }

# ---------- 1) Python >= 3.10（pyproject requires-python）----------
Write-Host "`n== [1/6] Python 版本检查 ==" -ForegroundColor Cyan
try {
    $pyVer = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
} catch {
    Fail "python 不在 PATH 中，请先安装 Python 3.10+"
}
if ([version]$pyVer -lt [version]"3.10") { Fail "需要 Python >= 3.10（当前 $pyVer）" }
Write-Host "Python $pyVer OK"

# ---------- 2) venv（已存在则跳过）----------
Write-Host "`n== [2/6] venv ==" -ForegroundColor Cyan
if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host ".venv 已存在，跳过创建"
} else {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "venv 创建失败" }
    Write-Host ".venv 已创建"
}
$vpy = ".venv\Scripts\python.exe"

# ---------- 3) 依赖安装（pip install -e .[dev]，清华源；幂等）----------
Write-Host "`n== [3/6] 依赖安装 (pip -e .[dev]，清华源) ==" -ForegroundColor Cyan
& $vpy -m pip install -e ".[dev]" -i $TunaMirror
if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败" }

# ---------- 4) .env（不存在则从模板生成并引导填写）----------
Write-Host "`n== [4/6] .env 环境文件 ==" -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[WARN] 已从 .env.example 生成 .env。请填写飞书三件套 / LLM 六项 / DATABASE_URL 后重跑本脚本完成部署。" -ForegroundColor Yellow
    exit 2
}
Write-Host ".env 已存在"

# ---------- 5) 环境校验（config_check：必填项/DB 前缀，exit 2 即失败）----------
Write-Host "`n== [5/6] config_check 环境校验 ==" -ForegroundColor Cyan
& $vpy scripts\config_check.py --env-file .env
if ($LASTEXITCODE -ne 0) { Fail "config_check 未通过（检查 .env 必填项）" }

# ---------- 6) PostgreSQL 容器 + alembic 迁移 ----------
Write-Host "`n== [6/6] PostgreSQL 容器 + alembic 迁移 ==" -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker 未运行，请先启动 Docker Desktop" }
# compose 声明 volume external:true，必须先显式创建（已存在时无副作用）
docker volume create feishu-agent-pgdata | Out-Null
docker compose up -d postgres
if ($LASTEXITCODE -ne 0) { Fail "postgres 容器启动失败" }
$cid = (docker compose ps -q postgres)
if (-not $cid) { Fail "未找到 postgres 容器" }
# 等待 healthcheck 就绪（已 healthy 则首个循环即通过）
$health = ""
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $health = (docker inspect --format "{{.State.Health.Status}}" $cid).Trim()
    if ($health -eq "healthy") { break }
    Start-Sleep -Seconds 2
}
if ($health -ne "healthy") { Fail "postgres 未在 60s 内就绪（当前 $health），可稍后重跑" }
Write-Host "postgres healthy"
& $vpy -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { Fail "alembic 迁移失败" }
Write-Host "数据库 schema 已是最新"

Write-Host "`n[PASS] 部署完成。启动服务：powershell -ExecutionPolicy Bypass -File scripts\start.ps1" -ForegroundColor Green
