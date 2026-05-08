# QQ Contacts Platform Integration

## 📋 Summary

This integration adds complete support for QQ Contacts (Lingya) platform registration to the `any-auto-register` system.

## 🎯 What's Included

### Core Files

- **`plugin.py`** (70 lines)
  - Main platform class inheriting `BasePlatform`
  - Platform capability declarations
  - Lifecycle configuration (heartbeat interval: 5min, check interval: 1hour)
  - Account validity checking via API heartbeat

- **`browser_register.py`** (350 lines)
  - Full browser automation registration flow
  - Phone number submission
  - Captcha handling
  - SMS code submission
  - Cookie extraction and QQ UID retrieval
  - Headless/headed browser support

- **`heartbeat_manager.py`** (150 lines)
  - Background session heartbeat (5-minute interval)
  - Space API and Hello API fallback
  - Automatic retry on failure
  - Cookie persistence

- **`captcha_solver.py`** (250 lines)
  - Multi-strategy captcha recognition
  - Local OCR (Tesseract) - fast, free
  - YesCaptcha remote service - accurate, paid
  - 2Captcha fallback - alternative service
  - Automatic strategy fallback

- **`README.md`** (400 lines)
  - Comprehensive platform documentation
  - Configuration guide
  - Usage examples
  - Troubleshooting tips

## 📊 Registration Flow

```
1. Visit lingya.qq.com
   ↓
2. Submit phone number
   ↓
3. Solve CAPTCHA (mixed-element AI)
   ↓
4. Receive SMS verification code
   ↓
5. Complete registration, extract Cookie + QQ UID
   ↓
6. Start background heartbeat (every 5 minutes)
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install playwright>=1.40.0 aiohttp>=3.9.0

# Optional: Local OCR support
pip install pytesseract>=0.3.10 Pillow>=10.0.0

# Install browser
python -m playwright install chromium
```

### 2. Configure Services

```json
{
  "platform": "qq_contacts",
  "executor": "headless",
  "identity_mode": "phone",
  "config": {
    "sms_provider": "sms_activate",
    "captcha_provider": "yescaptcha",
    "proxy_pool": ["http://proxy:8080"]
  }
}
```

### 3. Start Registration

```bash
POST /api/tasks/register
Content-Type: application/json

{
  "platform": "qq_contacts",
  "num_accounts": 5,
  "executor": "headless"
}
```

## ✅ Features

| Feature | Status | Note |
|---------|--------|------|
| Browser automation | ✅ | Headless/headed modes |
| Phone registration | ✅ | SMS-Activate / HeroSMS |
| CAPTCHA solving | ✅ | OCR + YesCaptcha + 2Captcha |
| Session heartbeat | ✅ | 5-minute interval |
| Cookie management | ✅ | Persistent storage |
| Proxy support | ✅ | HTTP/HTTPS/SOCKS5 |
| Account lifecycle | ✅ | Auto check, refresh, heartbeat |
| Export formats | ✅ | JSON, CSV, Any2API |

## 📁 Integration

All files are under:
```
account_manager/platforms/qq_contacts/
```

System will auto-discover via `@register` decorator.

## 🔧 Configuration Examples

### YesCaptcha
```bash
export YESCAPTCHA_CLIENT_KEY="your_key"
```

### SMS-Activate
```json
{
  "sms_provider": "sms_activate",
  "api_key": "your_api_key",
  "country": "CN"
}
```

### Proxy Pool
```json
{
  "proxy_pool": [
    "http://proxy1:8080",
    "http://user:pass@proxy2:8080"
  ]
}
```

## 📞 Support

- Check logs for detailed debug info
- Use "headed" mode for UI observation
- Test CAPTCHA service separately first
- Verify SMS service with test numbers

## 📈 Statistics

| Metric | Value |
|--------|-------|
| Total Lines | ~820 |
| Functions | 30+ |
| Error Handling | Comprehensive |
| Async Support | Full |
| Documentation | Extensive |

---

**Version**: 1.0.0  
**Status**: Production Ready  
**Last Updated**: 2025-05-08
