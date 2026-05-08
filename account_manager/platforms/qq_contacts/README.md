# QQ 通讯录平台（Lingya）

完整的 QQ 通讯录（lingya.qq.com）自动注册和管理集成。

## 📋 平台特性

| 特性 | 说明 |
|------|------|
| **注册方式** | 浏览器自动化 |
| **执行模式** | 无头浏览器 / 有头浏览器 |
| **身份验证** | 手机号 + 短信验证码 |
| **验证码** | 图形验证码（混元AI） |
| **凭证** | Cookie（存储在数据库中） |
| **会话保活** | 每 5 分钟心跳请求 |
| **代理支持** | ✅ 支持 HTTP/HTTPS 代理 |

## 🔄 注册流程

```
1. 访问 lingya.qq.com
   ↓
2. 提交手机号
   ↓
3. 解决图形验证码（混元AI，支持 OCR + 远程服务）
   ↓
4. 接收手机短信验证码
   ↓
5. 完成注册，获取 Cookie + QQ UID
   ↓
6. 启动后台心跳保活（每 5 分钟）
```

## 🚀 使用方法

### Web UI 注册

1. **选择平台**：在下拉菜单中选择 "QQ 通讯录"
2. **选择执行器**：选择 "无头浏览器" 或 "有头浏览器"
3. **配置身份**：
   - 使用 SMS-Activate 或 HeroSMS 服务提供手机号
   - 系统将自动使用该手机号注册
4. **配置验证码**：
   - 选择验证码服务（推荐 YesCaptcha）
   - 或使用本地 OCR（需要 Tesseract 引擎）
5. **配置其他**：
   - 代理池（可选）
   - 并发数等参数
6. **提交**：开始注册

### API 调用示例

```bash
POST /api/tasks/register
Content-Type: application/json

{
  "platform": "qq_contacts",
  "executor": "headless",
  "identity_mode": "phone",
  "num_accounts": 5,
  "config": {
    "sms_provider": "sms_activate",
    "sms_config": {
      "api_key": "YOUR_SMS_API_KEY",
      "country": "CN"
    },
    "captcha_provider": "yescaptcha",
    "captcha_config": {
      "client_key": "YOUR_YESCAPTCHA_KEY"
    },
    "proxy_pool": [
      "http://proxy1:8080",
      "http://proxy2:8080"
    ]
  }
}
```

## 🔧 环境配置

### 1. 安装依赖

```bash
# 浏览器自动化
pip install playwright>=1.40.0

# HTTP 客户端
pip install aiohttp>=3.9.0

# 本地 OCR（可选）
pip install pytesseract>=0.3.10 Pillow>=10.0.0

# Tesseract 引擎安装（仅本地 OCR 需要）
# Windows: https://github.com/UB-Mannheim/tesseract/wiki
# macOS: brew install tesseract
# Linux: apt-get install tesseract-ocr
```

### 2. 配置浏览器

```bash
# 安装 Playwright 浏览器
python -m playwright install chromium
```

### 3. 配置验证码服务

#### YesCaptcha（推荐）

```bash
# 设置环境变量
export YESCAPTCHA_CLIENT_KEY="your_client_key"
```

或在全局配置中设置：

```json
{
  "captcha_provider": "yescaptcha",
  "captcha_config": {
    "client_key": "YOUR_CLIENT_KEY"
  }
}
```

#### 2Captcha（备选）

```bash
export 2CAPTCHA_API_KEY="your_api_key"
```

#### 本地 OCR

```bash
# 安装 Tesseract 引擎后，系统将自动使用本地 OCR
```

### 4. 配置 SMS 服务

#### SMS-Activate

```json
{
  "sms_provider": "sms_activate",
  "sms_config": {
    "api_key": "YOUR_API_KEY",
    "country": "CN",
    "service": "qq"
  }
}
```

#### HeroSMS

```json
{
  "sms_provider": "herosms",
  "sms_config": {
    "api_key": "YOUR_API_KEY",
    "service_code": "qq",
    "country_id": 1,
    "max_price": 5
  }
}
```

## 📁 文件结构

```
platforms/qq_contacts/
├── __init__.py                  # 包声明
├── plugin.py                    # 平台主类（~70 行）
├── browser_register.py          # 浏览器注册逻辑（~350 行）
├── heartbeat_manager.py         # 会话心跳管理（~150 行）
└── captcha_solver.py            # 验证码识别（~250 行）
```

**总代码量**：约 820 行

## 🔄 账号生命周期

### 自动检测和保活

- **心跳间隔**：每 5 分钟发送一次心跳请求
- **检测间隔**：每小时自动检测账号有效性
- **刷新阈值**：24 小时后需要刷新（可选）

### 检测逻辑

系统会定期调用以下接口验证账号：

```
POST https://lingya.qq.com/api/space
或
GET https://lingya.qq.com/api/hello
```

如果返回 HTTP 200，则认为账号仍有效。

## 💾 账号数据存储

注册成功后，系统存储以下信息：

```python
{
    "phone": "13800138000",           # 注册手机号
    "cookie": "...",                  # HTTP Cookie（关键凭证）
    "qq_uid": "12345678",             # QQ UID
    "cookies_json": "[...]",          # Cookie JSON 备份
    "status": "registered",           # 账号状态
    "created_at": "2025-05-08T...",  # 创建时间
}
```

## 🚨 错误处理

常见错误和解决方案：

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 图形验证码失败 | 验证码服务未配置或无效 | 检查 YesCaptcha 密钥 |
| 手机验证码超时 | 未收到短信 | 检查 SMS 服务配置 |
| Cookie 过期 | 账号 7 天未使用 | 定期发送心跳或重新注册 |
| 浏览器超时 | 网络连接慢 | 检查代理或网络环境 |

## 📊 成功率统计

在"注册成功率仪表盘"中可查看：

- 按平台统计的注册成功率
- 按代理统计的成功率
- 按天统计的注册趋势
- 失败错误聚合分析

## 🔗 相关配置

### 代理池配置

支持以下代理格式：

```
http://proxy:8080
http://username:password@proxy:8080
socks5://proxy:1080
```

### 并发控制

```json
{
  "concurrent_count": 3,
  "max_workers": 5,
  "timeout": 120000
}
```

## ⚙️ 高级配置

### 心跳管理

在 `plugin.py` 中调整心跳间隔：

```python
def get_lifecycle_config(self):
    return {
        "check_interval": 3600,      # 1 小时检测一次
        "heartbeat_interval": 300,   # 5 分钟心跳一次
        "refresh_threshold": 86400,  # 24 小时后需要刷新
    }
```

### 浏览器参数

调整 `browser_register.py` 中的 Playwright 配置：

```python
launch_args = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
    ]
}
```

## 📝 日志示例

```
[1/5] 访问注册页面: https://lingya.qq.com
[2/5] 提交手机号: 13800138000
✓ 已填入手机号
✓ 已点击发送验证码按钮，等待验证码界面
[3/5] 处理图形验证码
🔍 检测到图形验证码，开始识别...
✓ 识别结果: 8K4W
✓ 已提交验证码
[4/5] 等待手机验证码
[4/5] 收到验证码: 123456
✓ 已填入短信验证码
✓ 已提交短信验证码
[5/5] 等待注册完成
✅ 注册成功! Phone: 13800138000, QQ UID: 12345678
🔄 启动会话心跳保活 (每5分钟一次)
✓ 心跳成功 #1 (14:30:45)
```

## 🤝 常见问题

### Q: 如何处理图形验证码？

**A:** 系统支持三层回退：
1. 本地 OCR（最快，准确率 70-80%）
2. YesCaptcha（准确率 >95%，需付费）
3. 2Captcha（备选方案）

配置优先级从高到低尝试，无需手动干预。

### Q: 账号保活多久有效？

**A:** 心跳可保持账号活跃，理论上无限期有效。但建议：
- 最少每 7 天主动使用一次账号
- 每 30 天检查一次 Cookie 有效性

### Q: 支持多少个账号并发注册？

**A:** 建议：
- 本地环境：2-3 个并发
- Docker 环境：5-10 个并发（取决于 CPU/内存）
- 云服务器：10-50 个并发

## 📚 扩展开发

### 添加自定义操作

在 `plugin.py` 中添加：

```python
def get_actions(self):
    return [
        {
            "name": "refresh_cookie",
            "display": "刷新 Cookie",
            "description": "强制刷新账号 Cookie",
        }
    ]

async def execute_action(self, action: str, account: Account) -> dict:
    if action == "refresh_cookie":
        # 实现刷新 Cookie 的逻辑
        pass
```

### 修改注册流程

编辑 `browser_register.py` 中的各个步骤方法，如 `_submit_phone()`、`_solve_captcha()` 等。

## 📞 支持

- 遇到问题？检查日志文件
- 需要调试？启用"有头浏览器"模式观察实时流程
- 代码贡献？欢迎提交 Pull Request

---

**版本**：1.0.0  
**更新日期**：2025-05-08  
**作者**：QQ Contacts Integration Team
