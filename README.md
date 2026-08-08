# Feishu Research Agent — Phase 1

## 启动

```bash
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env

# 初始化数据库
alembic upgrade head

uvicorn gateway.app:create_app --factory --host 0.0.0.0 --port 8000
```

## 测试

```bash
pytest tests/ -v
```