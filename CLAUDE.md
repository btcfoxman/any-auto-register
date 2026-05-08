# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指引。

## 项目概述

多平台账号自动注册与管理系统（支持 13+ AI 平台：ChatGPT、Cursor、Grok、Windsurf、Trae 等）。插件化架构，FastAPI 后端 + React 前端 + SQLite 数据库。

## 常用命令

### 后端

```bash
# 启动服务（项目根目录，需激活虚拟环境）
python3 -m uvicorn main:app --port 8000

# 运行全部测试
pytest

# 运行单个测试文件
pytest tests/test_api_accounts.py

# 运行单个测试函数
pytest tests/test_api_accounts.py::test_function_name -v

# 安装依赖
pip install -r requirements.txt

# 安装浏览器（可选，浏览器模式注册需要）
python3 -m playwright install chromium
python3 -m camoufox fetch
```

### 前端

```bash
cd frontend
npm install
npm run build    # 生产构建（tsc -b && vite build）
npm run dev      # 开发服务器 localhost:5173（代理 /api 到后端）
npm run lint     # ESLint 检查
```

### Docker

```bash
docker compose up -d --build    # 构建并启动
docker compose logs -f          # 查看日志
```

## 架构

项目采用分层架构：

```
api/            → HTTP 路由层（FastAPI routers，挂载在 /api/*）
application/    → 应用服务层（编排 domain + infrastructure）
domain/         → 领域模型与业务规则
infrastructure/ → 仓储实现与运行时适配器
core/           → 共享基类、数据库模型、工具函数、注册流程引擎
platforms/      → 平台插件（每个平台一个目录）
providers/      → Provider 插件（邮箱、验证码、接码、代理）
services/       → 后台服务（Turnstile Solver、任务运行时）
frontend/       → React + TypeScript + Vite + TailwindCSS
```

### 插件系统

平台插件通过 `pkgutil.iter_modules` 自动扫描 `platforms/` 目录发现。每个平台目录必须包含 `__init__.py` 和 `plugin.py`，其中定义一个继承 `BasePlatform` 并使用 `@register` 装饰器的类。

Provider 插件（mailbox、captcha、sms、proxy）遵循相同模式，位于 `providers/` 下，通过 `providers/registry.py` 加载。

### 注册流程引擎

注册引擎位于 `core/registration/`，使用适配器模式：
- `ProtocolMailboxAdapter` — 协议模式注册（无浏览器，最快）
- 浏览器适配器 — 无头或有头 Playwright/Camoufox

每个平台的 `plugin.py` 通过 `build_protocol_mailbox_adapter()` 等方法构建适配器。

### 启动流程（main.py lifespan）

1. `init_db()` — SQLite/SQLModel 数据库初始化
2. `load_all()` — 扫描并注册平台插件
3. `load_providers()` — 扫描并注册 Provider 插件
4. 启动 scheduler、task_runtime、solver_manager、lifecycle_manager

### 前后端交互

- 生产模式：后端直接托管前端构建产物，访问 `/` 即可
- 开发模式：Vite 开发服务器运行在 `:5173`，代理 `/api` 到后端 `:8000`
- 实时日志通过 SSE（Server-Sent Events）推送

## 技术栈

- 后端：FastAPI、SQLModel（SQLite）、curl_cffi（TLS 指纹伪装）、Playwright/Camoufox
- 前端：React 19、TypeScript、Vite、TailwindCSS v4、Radix UI
- 测试：pytest + httpx（TestClient）

## 开发规范

- 提交信息遵循 Conventional Commits（`feat:`、`fix:`、`docs:`、`refactor:`、`test:`）
- 代码注释和 UI 文案以中文为主
- 所有 API 路由统一前缀 `/api/`
- Provider 配置在数据库中以 `provider_type + provider_key` 为唯一约束
