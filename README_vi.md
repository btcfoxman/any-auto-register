<div align="center">

# Any Auto Register

Tự động đăng ký tài khoản ChatGPT / Cursor / Kiro / Grok / Windsurf / Trae & 13+ nền tảng AI · Chế độ giao thức/trình duyệt · Khởi động một chạm trên Mac/Windows

<p>
  <a href="https://github.com/lxf746/any-auto-register/stargazers"><img src="https://img.shields.io/github/stars/lxf746/any-auto-register?style=flat-square&logo=github&color=FFB003" alt="Stars" /></a>
  <a href="https://github.com/lxf746/any-auto-register/releases/latest"><img src="https://img.shields.io/github/v/release/lxf746/any-auto-register?style=flat-square&logo=github&color=22c55e" alt="Release" /></a>
  <a href="https://github.com/lxf746/any-auto-register/network/members"><img src="https://img.shields.io/github/forks/lxf746/any-auto-register?style=flat-square&logo=github&color=3b82f6" alt="Forks" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/lxf746/any-auto-register?style=flat-square&color=f97316" alt="License" /></a>
</p>

<p>
  <a href="https://github.com/lxf746/any-auto-register/releases/latest">Tải bản desktop</a>
  &nbsp;·&nbsp;
  <a href="README.md">中文</a>
  &nbsp;·&nbsp;
  <a href="README_en.md">English</a>
</p>

<img src="assets/screenshots/dashboard.png" alt="Any Auto Register Dashboard" width="92%" />

</div>

---

> **Đây là kho chính thức của [`lxf746/any-auto-register`](https://github.com/lxf746/any-auto-register)** — kho gốc của tác giả với các cập nhật mới nhất. Các kho cùng tên khác đều là fork.

> Dự án này chỉ dành cho mục đích học tập và nghiên cứu, không được sử dụng cho mục đích thương mại trái phép. Người dùng tự chịu trách nhiệm về mọi hậu quả phát sinh khi sử dụng.

## Mục lục

- [Điểm nổi bật](#điểm-nổi-bật)
- [Tính năng](#tính-năng)
- [Ảnh chụp màn hình](#ảnh-chụp-màn-hình)
- [Công nghệ sử dụng](#công-nghệ-sử-dụng)
- [Tải ứng dụng Desktop](#tải-ứng-dụng-desktop)
- [Bắt đầu nhanh](#bắt-đầu-nhanh)
- [Triển khai Docker](#triển-khai-docker)
- [Dịch vụ Email](#dịch-vụ-email)
- [Dịch vụ Captcha](#dịch-vụ-captcha)
- [Proxy Pool](#proxy-pool)
- [Dịch vụ SMS](#dịch-vụ-sms)
- [Vòng đời tài khoản](#vòng-đời-tài-khoản)
- [Dashboard thống kê](#dashboard-thống-kê)
- [Tích hợp Any2API](#tích-hợp-any2api)
- [Phát triển plugin](#phát-triển-plugin)
- [Câu hỏi thường gặp](#câu-hỏi-thường-gặp)
- [Cộng đồng](#cộng-đồng)
- [Star History](#star-history)
- [Giấy phép](#giấy-phép)

## Điểm nổi bật

Vì sao nên chọn any-auto-register thay vì các công cụ tương tự:

| Khả năng | any-auto-register | Công cụ khác |
|------|------|------|
| 🖥️ **Ứng dụng desktop một cú nhấp** | ✅ Electron client cho Mac / Windows, không cần CLI | ❌ Thường chỉ có CLI / Docker |
| 🧩 **Độ phủ nền tảng** | ✅ 13+ nền tảng sẵn sàng + Anything adapter tổng quát | Thường 1-3 nền tảng |
| 📨 **Dịch vụ email** | ✅ 9 dịch vụ (tự host + công cộng + DDG) | Thường 1-2 |
| ⚡ **Ba chế độ thực thi** | ✅ Pure protocol (không browser, nhanh nhất) / headless / headed | Thường chỉ browser |
| 🔁 **Vòng đời tài khoản** | ✅ Kiểm tra hợp lệ, tự refresh token, cảnh báo trial hết hạn | ❌ Đa số chỉ đăng ký |
| 📊 **Dashboard tỷ lệ thành công** | ✅ Thống kê theo nền tảng / proxy / ngày, tổng hợp lỗi | ❌ |
| 🔌 **Tích hợp Any2API** | ✅ Đăng ký xong dùng được ngay, tự động đẩy lên gateway | ❌ |
| 📦 **Kiến trúc plugin** | ✅ Platform / mailbox / captcha / SMS / proxy đều cắm rời | Thường hardcode |

> 💡 Kết hợp [`Any2API`](https://github.com/lxf746/any2api) gateway + `any-auto-register` cho phép thực hiện chuỗi đầy đủ: **đăng ký hàng loạt tài khoản → tự động đẩy lên gateway → dùng ngay như API tương thích OpenAI/Claude**.

## Tính năng

- **Đa nền tảng**: ChatGPT, Cursor, Kiro, Trae.ai, Tavily, Grok, Blink, Cerebras, OpenBlockLabs, Windsurf, kèm Anything adapter tổng quát cho plugin tùy chỉnh
- **Đa dịch vụ email**: MoeMail (tự host), Laoudo, DuckMail, Testmail, Cloudflare Worker tự host, Freemail, TempMail.lol, Temp-Mail Web, DuckDuckGo Email
- **Thực thi đa chế độ**: API protocol (không browser) / headless browser / headed browser (theo từng nền tảng)
- **Captcha**: YesCaptcha, 2Captcha, Solver cục bộ (Camoufox)
- **SMS**: SMS-Activate, HeroSMS (cho các nền tảng yêu cầu xác minh số điện thoại)
- **Proxy pool**: Static round-robin + dynamic API extract + rotating gateway, weight theo tỷ lệ thành công, tự động vô hiệu hóa proxy lỗi
- **Vòng đời tài khoản**: Kiểm tra hợp lệ định kỳ, tự refresh token, cảnh báo trial hết hạn
- **Dashboard tỷ lệ thành công**: Thống kê theo nền tảng, ngày, proxy; tổng hợp lỗi
- **Đăng ký đồng thời**: Có thể cấu hình mức độ song song
- **Log thời gian thực**: Đẩy log SSE về frontend ngay khi chạy
- **Xuất tài khoản**: JSON, CSV, CPA, Sub2API, Kiro-Go, Any2API
- **Đồng bộ Any2API**: Tự động đẩy tài khoản đã đăng ký lên gateway, dùng được ngay
- **Hành động theo nền tảng**: Tùy biến từng nền tảng (ví dụ: chuyển tài khoản Kiro, sinh link nâng cấp Trae Pro)

## Ảnh chụp màn hình

> 📸 *Ảnh chụp sẽ được cập nhật theo từng phiên bản. Để trải nghiệm đầy đủ, hãy thử [Tải Desktop](#tải-ứng-dụng-desktop).*

### Dashboard
![Dashboard](assets/screenshots/dashboard.png)

### Tác vụ đăng ký
![Tác vụ đăng ký](assets/screenshots/register-task.png)

### Cài đặt
![Cài đặt](assets/screenshots/settings.png)

### Quản lý tài khoản
![Quản lý tài khoản](assets/screenshots/accounts.png)

## Công nghệ sử dụng

| Tầng | Công nghệ |
|------|------|
| Backend | FastAPI + SQLite (SQLModel) |
| Frontend | React + TypeScript + Vite + TailwindCSS |
| HTTP | curl_cffi (giả lập browser fingerprint) |
| Browser automation | Playwright / Camoufox |

## Tải ứng dụng Desktop

> 🚀 **Một cú nhấp, không cần cấu hình**: Không muốn cài Python và Node.js? Tải client desktop về và nhấp đôi là dùng được.

| Nền tảng | Tải xuống |
|------|------|
| 🍎 macOS (Intel / Apple Silicon) | [Tải `.dmg` từ Releases](https://github.com/lxf746/any-auto-register/releases/latest) |
| 🪟 Windows | [Tải `.exe` từ Releases](https://github.com/lxf746/any-auto-register/releases/latest) |

Client desktop được đóng gói bằng Electron, đã bao gồm sẵn backend Python và frontend React — sử dụng ngay không cần cấu hình. Mỗi phiên bản mới (`v*` tag) sẽ được build và phát hành tự động trên [Releases](https://github.com/lxf746/any-auto-register/releases).

## Bắt đầu nhanh

### Yêu cầu hệ thống

- Python 3.11+
- Node.js 18+

### Cài đặt

#### macOS / Linux

```bash
git clone https://github.com/lxf746/any-auto-register.git
cd any-auto-register

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cd frontend && npm install && npm run build && cd ..
```

#### Windows

```bat
git clone https://github.com/lxf746/any-auto-register.git
cd any-auto-register

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..
```

### Cài đặt browser (tùy chọn, cần thiết cho chế độ headless / headed)

```bash
python3 -m playwright install chromium
python3 -m camoufox fetch
```

### Chạy

```bash
.venv/bin/python3 -m uvicorn main:app --port 8000
```

Mở `http://localhost:8000` trên trình duyệt.

## Triển khai Docker

```bash
docker compose up -d
```

Truy cập `http://localhost:8000`. Database được lưu vào `./data/`.

Với chế độ headed browser, có thể xem quá trình automation qua noVNC tại `http://localhost:6080`.

## Dịch vụ Email

Chọn một dịch vụ email để nhận mã xác minh. Tất cả provider được quản lý qua trang **Settings → Providers** trong Web UI:

- **MoeMail** (khuyến nghị): tự host trên Cloudflare bằng [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email)
- **Laoudo**: email custom domain ổn định
- **Cloudflare Worker tự host**: kiểm soát hoàn toàn, tự deploy
- **DuckMail / TempMail.lol / Temp-Mail Web**: dịch vụ email tạm thời công cộng
- **DuckDuckGo Email**: alias `@duck.com` qua DDG Email Protection (cần forward IMAP)
- **Freemail**: dựa trên Cloudflare Worker, hỗ trợ admin token + username/password
- **Testmail**: chế độ namespace của `testmail.app`, phù hợp cho tác vụ song song

Chi tiết các trường cấu hình có trong UI editor và trong [README tiếng Anh](README_en.md#mailbox-providers).

## Dịch vụ Captcha

| Dịch vụ | Ghi chú |
|------|------|
| YesCaptcha | Đăng ký tại [yescaptcha.com](https://yescaptcha.com) để lấy Client Key |
| 2Captcha | Đăng ký tại [2captcha.com](https://2captcha.com) để lấy API Key |
| Local Solver | Dùng Camoufox cục bộ, chạy `python3 -m camoufox fetch` trước |

## Proxy Pool

Hệ thống hỗ trợ cả static và dynamic proxy:

- **Static proxy**: quản lý qua trang Proxy; weight theo tỷ lệ thành công; tự vô hiệu hóa sau 5 lần thất bại liên tiếp
- **Dynamic proxy**: provider dạng API extraction hoặc rotating gateway (BrightData, Oxylabs, IPRoyal...); fallback về static pool khi thất bại

## Dịch vụ SMS

Cho các nền tảng yêu cầu xác minh số điện thoại (ví dụ: Cursor):

| Dịch vụ | Ghi chú |
|------|------|
| SMS-Activate | API key + quốc gia mặc định |
| HeroSMS | API key + service code, country ID, giá tối đa, chính sách reuse số |

## Vòng đời tài khoản

Lifecycle manager nền chạy tự động:

- **Kiểm tra hợp lệ** mỗi 6 giờ — đánh dấu tài khoản không còn hợp lệ
- **Refresh token** mỗi 12 giờ — tự refresh các token sắp hết hạn (hiện hỗ trợ ChatGPT)
- **Cảnh báo trial** — đánh dấu các tài khoản trial sắp hết hạn

Trigger thủ công:

- `POST /api/lifecycle/check` — kích hoạt kiểm tra hợp lệ
- `POST /api/lifecycle/refresh` — kích hoạt refresh token
- `POST /api/lifecycle/warn` — kích hoạt cảnh báo trial
- `GET /api/lifecycle/status` — xem trạng thái manager

## Dashboard thống kê

Truy vấn thống kê đăng ký qua API:

- `GET /api/stats/overview` — tổng quan toàn cục
- `GET /api/stats/by-platform` — tỷ lệ thành công theo nền tảng
- `GET /api/stats/by-day?days=30` — xu hướng theo ngày
- `GET /api/stats/by-proxy` — xếp hạng proxy theo tỷ lệ thành công
- `GET /api/stats/errors?days=7` — tổng hợp lỗi

## Tích hợp Any2API

Kết hợp với [Any2API](https://github.com/lxf746/any2api) — tài khoản sau khi đăng ký được tự động đẩy lên gateway, dùng ngay như API tương thích OpenAI/Claude.

Cấu hình trong Settings:

| Trường | Mô tả |
|------|------|
| `any2api_url` | URL của Any2API instance, ví dụ: `http://localhost:8099` |
| `any2api_password` | Mật khẩu admin của Any2API |

Đích đẩy theo nền tảng: Kiro → `kiroAccounts`, Grok → `grokTokens`, Cursor → `cursorConfig`, ChatGPT → `chatgptConfig`, Blink → `blinkConfig`, Windsurf → `windsurfAccounts`.

Nếu không cấu hình `any2api_url`, tích hợp này sẽ được bỏ qua một cách yên lặng.

## Phát triển plugin

Thêm nền tảng mới theo hệ plugin hiện tại:

1. Tạo `platforms/myplatform/` với `__init__.py` và `plugin.py`.
2. Giữ `plugin.py` chỉ để ghép module bằng `ConfiguredPlatformPlugin`.
3. Đặt registration trong `registration/module.py`, logic protocol trong `registration/protocol.py`, worker mailbox trong `registration/worker.py`, và browser/OAuth tùy chọn trong `registration/browser.py` / `registration/oauth.py`.
4. Chỉ thêm `query.py`, `payment.py`, hoặc `transfer.py` khi nền tảng có capability tương ứng.

```python
from core.platform_plugin import ConfiguredPlatformPlugin
from core.plugin_registry import register

@register
class MyPlatformPlugin(ConfiguredPlatformPlugin):
    name = "myplatform"
    display_name = "My Platform"
    version = "2.0.0"
    query_class = "MyPlatformQuery"
```

Plugin sẽ được tự động load từ `platforms/*/plugin.py` khi khởi động. Xem [README tiếng Trung](README.md#插件开发) để biết ví dụ registration module đầy đủ.

## Câu hỏi thường gặp

**Captcha liên tục thất bại?**
Kiểm tra xem captcha provider đã cấu hình đúng chưa. Trong chế độ protocol, ưu tiên YesCaptcha/2Captcha. Trong chế độ browser, Camoufox sẽ thử click Turnstile checkbox trước rồi fallback về remote solver. Nếu vẫn thất bại, hãy kiểm tra chất lượng IP proxy — IP rủi ro cao sẽ gặp challenge nghiêm ngặt hơn.

**Proxy bị chặn / tỷ lệ thành công thấp?**
Dùng residential proxy thay vì datacenter IP. Giảm số lượng song song. Kiểm tra thống kê từng proxy trong trang Proxy và vô hiệu hóa các proxy yếu. Mỗi nền tảng có độ nhạy IP khác nhau — có thể cân nhắc proxy pool riêng cho từng nền tảng.

**Cài đặt cho chế độ browser?**
```bash
python3 -m playwright install chromium
python3 -m camoufox fetch
```

**Solver khởi động timeout?**
Local Turnstile Solver cần Camoufox được cài đặt. Chạy `python3 -m camoufox fetch` trước, sau đó nhấp "Restart Solver" trong trang Settings. Hoặc bỏ qua local Solver hoàn toàn và dùng YesCaptcha / 2Captcha.

## Cộng đồng

Tham gia nhóm người dùng để cập nhật thông tin, kinh nghiệm cấu hình và mẹo đăng ký:

### Nhóm QQ

| Nhóm | Mã | Trạng thái |
|---|---|---|
| Nhóm 1 | `1081650009` | Đã đầy |
| Nhóm 2 | `1097916468` | Còn chỗ |

Tìm mã nhóm trong QQ để tham gia.

Để báo bug và yêu cầu tính năng, vui lòng dùng [Issues](https://github.com/lxf746/any-auto-register/issues).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=lxf746/any-auto-register&type=Date)](https://star-history.com/#lxf746/any-auto-register&Date)

## Nhà tài trợ

<table>
<tr>
<td width="220" align="center">
<a href="https://www.rapidproxy.io/?ref=lxf" target="_blank">
<img src="https://www.rapidproxy.io/static/rapidproxy/images/rapid_logo_700.png" alt="RapidProxy" width="180" />
</a>
</td>
<td>
<b><a href="https://www.rapidproxy.io/?ref=lxf">RapidProxy</a></b> — Proxy dân cư chất lượng cao, xoay IP thông minh, tỷ lệ bị chặn thấp, lưu lượng không hết hạn, hỗ trợ thu thập dữ liệu mạnh mẽ.<br/>
90M+ IP dân cư thực · 200+ quốc gia / khu vực · 99.9% uptime · &lt;0.5s phản hồi.<br/>
<a href="https://www.rapidproxy.io/?ref=lxf"><b>Dùng thử miễn phí →</b></a>
</td>
</tr>
</table>

## Giấy phép

Dự án này được cấp phép theo [AGPL-3.0](LICENSE). Học tập và nghiên cứu cá nhân được sử dụng tự do; sử dụng thương mại phải tuân thủ AGPL-3.0 (sản phẩm phái sinh phải open source).
