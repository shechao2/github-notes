# Windows 一键启动：建 venv、装依赖、灌演示数据、起服务
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { python -m venv .venv }
. .\.venv\Scripts\Activate.ps1
pip install -q -r requirements.txt
if (-not (Test-Path "data/app.db")) { python -m app.seed }
python -m app.main
