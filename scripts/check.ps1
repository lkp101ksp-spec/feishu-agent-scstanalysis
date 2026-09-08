# Phase 10 T5 质量门一键脚本（配套 ADR-0028 venv / ADR-0029 pg 分层）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\check.ps1
#       加 -Pg 追加真库层（需先 docker compose up -d postgres）
param(
    [switch]$Pg
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1) venv 存在性（Phase 10 T1 钉死 3.12.10 解释器）
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[FAIL] 未找到 .venv。执行: python -m venv .venv; .venv\Scripts\pip install -e .[dev] -i https://pypi.tuna.tsinghua.edu.cn/simple" -ForegroundColor Red
    exit 2
}
$py = ".venv\Scripts\python.exe"
$ruff = ".venv\Scripts\ruff.exe"
if (-not (Test-Path $ruff)) {
    Write-Host "[FAIL] venv 内缺 ruff。执行: .venv\Scripts\pip install -e .[dev]" -ForegroundColor Red
    exit 2
}

# 2) ruff check（lint 硬门禁）
Write-Host "`n== [1/4] ruff check ==" -ForegroundColor Cyan
& $ruff check .
if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] ruff check 未通过" -ForegroundColor Red; exit 2 }

# 3) ruff format --check（仅提示不阻塞：存量 200+ 文件未整体重排，Phase 10 约束不改行为）
Write-Host "`n== [2/4] ruff format --check (informational) ==" -ForegroundColor Cyan
$fmt = & $ruff format --check . 2>$null | Select-Object -Last 1
if ($LASTEXITCODE -eq 0) {
    Write-Host "format: 全部合规"
} else {
    Write-Host "format: $fmt （存量未整体 format，不阻塞；新文件建议 .venv\Scripts\ruff.exe format <file>）" -ForegroundColor DarkGray
}

# 3.5) mypy 宽松基线（硬门禁，2026-09-08 方案 A；范围/开关见 pyproject [tool.mypy]）
Write-Host "`n== [3/4] mypy 基线 ==" -ForegroundColor Cyan
& $py -m mypy
if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] mypy 未通过" -ForegroundColor Red; exit 2 }

# 4) pytest 默认层（SQLite）+ 覆盖率摘要（硬门禁；pg 层靠 -m 隔离，Docker 起着也不混入）
#    --basetemp 钉仓库内 .pytest_tmp：Windows 默认 Temp\pytest-of-* 曾遇 WinError 5 权限拒绝
Write-Host "`n== [4/4] pytest 默认层 + cov ==" -ForegroundColor Cyan
& $py -m pytest -q -m "not pg" --basetemp="$root\.pytest_tmp" --cov --cov-report=term
if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] pytest 默认层未通过" -ForegroundColor Red; exit 2 }

# 5) pg 层：可选执行，否则给出提示
if ($Pg) {
    Write-Host "`n== [+] pytest -m pg（真库层）==" -ForegroundColor Cyan
    & $py -m pytest tests/pg -m pg -q
    if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] pg 层未通过" -ForegroundColor Red; exit 2 }
} else {
    Write-Host "`n提示: 真库层未跑。docker compose up -d postgres 后执行本脚本加 -Pg（或 .venv\Scripts\python.exe -m pytest -m pg）" -ForegroundColor Yellow
}

Write-Host "`n[PASS] 质量门全部通过" -ForegroundColor Green
