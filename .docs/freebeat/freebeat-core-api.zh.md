# freebeat 核心接口链路分析

来源：`tmp/playwright-capture/requests.jsonl`、`responses.jsonl`、`response-bodies/`、`session.har`。  
采集环境：Playwright Chromium，代理 `http://127.0.0.1:10809`。  
说明：本文档已脱敏邮箱、验证码、Authorization token、S3 签名参数；请求编号如 `R002023` 可回查原始 JSONL/HAR。

## 结论

核心业务域名是 `https://freebeat.ai`，大多数业务接口走 `/api/proxy/v1/...`。登录注册不是普通 JSON API，而是 Next.js Server Action：`POST /zh/ai-video-generator`，成功后响应里返回 `token/accessToken/deviceToken`，后续业务接口用请求头 `Authorization: <token>`，没有 `Bearer` 前缀。

本次采集到的新账号积分链路：

| 动作 | 接口 | 积分变化 |
| --- | --- | --- |
| 邮箱验证码注册登录成功 | `POST /zh/ai-video-generator` | 随后查积分得到 `free=500,totalCredits=500` |
| 回答 onboarding 问题 | `POST /api/proxy/v1/user/questionnaire/submit` | `creditsGranted=300`，随后 `boost=300,totalCredits=800` |
| 每日签到 | `POST /api/proxy/v1/user/signin/submit` | `rewardAmount=200`，随后 `event=200,totalCredits=1000` |
| 创建本次视频 | `POST /api/proxy/v1/aiVideo/createAiVideo` | modelId `101`、4 秒、720p，扣 `632`，随后 `totalCredits=368` |

## 公共请求

受保护接口使用：

```http
Authorization: <login_response.data.token>
Content-Type: application/json
Referer: https://freebeat.ai/zh/ai-video-generator
```

`Authorization` 的值来自登录 Server Action 响应里的 `data.token` 或 `data.accessToken`，采集中二者相同。上传签名接口 `https://api.freebeatfit.com/api/v2/file/genUploadSignUrl` 在本次采集中没有携带 Authorization。

## 验证码注册登录

### 1. 发送邮箱验证码

证据：`R000282/R000305/R000329/R000353`

```http
POST https://freebeat.ai/api/proxy/v1/user/com/sendEmailVerifyCodeV2
Content-Type: application/json
```

请求体：

```json
{
  "email": "<email>",
  "verifySource": "WEB_SHOPIFY_LOGIN"
}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": true
}
```

### 2. 提交验证码完成注册/登录

证据：`R000375`

```http
POST https://freebeat.ai/zh/ai-video-generator
Accept: text/x-component
Content-Type: text/plain;charset=UTF-8
next-action: 40284e1e63e50bc18b2033770e8fa1412662d607d8
```

请求体：

```json
[
  {
    "email": "<email>",
    "code": "<6-digit-code>"
  }
]
```

响应是 `text/x-component` 的 React Server Component 流，其中 `1:` 行包含登录结果 JSON：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "accessToken": "<token>",
    "deviceToken": "<token>",
    "token": "<token>",
    "userId": "<user-id>",
    "newUser": true,
    "platform": "web",
    "member": 0,
    "vip": false,
    "expireTime": 1781635058486
  }
}
```

注意：`next-action` 是 Next.js 构建产物 ID，部署更新后可能变化。做自动化时应从当前页面脚本/提交行为中刷新，而不要长期硬编码。

## 查看积分

证据：`R000408`，本次共捕获 282 次。

```http
GET https://freebeat.ai/api/proxy/v1/user/credits/findCredits
Authorization: <token>
```

响应字段：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "boost": "0",
    "event": "0",
    "free": "500",
    "membership": "0",
    "showBindAlter": 0,
    "showBindingGuide": 1,
    "totalCredits": 500,
    "userSubscriptionType": "0"
  }
}
```

本次积分快照变化：

| 时间点 | free | event | boost | totalCredits | 说明 |
| --- | ---: | ---: | ---: | ---: | --- |
| 登录后 | 500 | 0 | 0 | 500 | 注册赠送 |
| 问卷后 | 500 | 0 | 300 | 800 | onboarding 问卷奖励 |
| 签到后 | 500 | 200 | 300 | 1000 | 每日签到奖励 |
| 创建视频后 | 68 | 0 | 300 | 368 | 扣除 632 积分 |

## 注册领积分

本次没有单独的“注册领积分”提交接口。注册/登录 Server Action 成功返回 `newUser=true` 后，紧接着 `findCredits` 返回 `free=500,totalCredits=500`，说明注册赠送积分是登录注册成功后由后端直接入账。

## 回答问题领积分

### 1. 检查问卷状态

证据：`R000474`

```http
GET https://freebeat.ai/api/proxy/v1/user/questionnaire/check?questionnaireCode=onboarding_v1
Authorization: <token>
```

本次响应体为空字符串，随后前端展示并提交问卷。

### 2. 提交问卷

证据：`R000508`

```http
POST https://freebeat.ai/api/proxy/v1/user/questionnaire/submit
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{
  "questionnaireCode": "onboarding_v1",
  "source": "TRIGGER",
  "triggerEvent": "register_success",
  "answers": [
    {
      "questionKey": "q1_describe_you",
      "options": ["content_creator"]
    },
    {
      "questionKey": "q2_hear_about",
      "options": ["google"]
    },
    {
      "questionKey": "q3_use_for",
      "options": ["client_projects"]
    }
  ]
}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "creditsGranted": 300
  }
}
```

## 每日签到领积分

### 1. 查询签到状态

证据：`R000410`

```http
GET https://freebeat.ai/api/proxy/v1/user/signin/status
Authorization: <token>
```

未签到响应：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "canSignIn": true,
    "granted": false,
    "nextRefreshAt": 1779062400000,
    "serverUtcDate": "2026-05-17",
    "signedToday": false
  }
}
```

### 2. 提交签到

证据：`R000532`

```http
POST https://freebeat.ai/api/proxy/v1/user/signin/submit
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "canSignIn": false,
    "eventCode": "SIGN_IN_20260517",
    "granted": true,
    "message": "Signed in successfully",
    "nextRefreshAt": 1779062400000,
    "rewardAmount": 200,
    "serverUtcDate": "2026-05-17",
    "signedToday": true
  }
}
```

## 积分要求

页面初始化会拉取完整规则：

```http
GET https://freebeat.ai/api/proxy/v1/aiVideo/getRuleConfig
```

实际计算某个模型消耗时使用：

```http
POST https://freebeat.ai/api/proxy/v1/aiModelConfig/model/getModelRuleConfig
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{
  "businessType": 3,
  "modelId": 101
}
```

本次重点模型规则：

| modelId | 规则 | 捕获值 |
| ---: | --- | --- |
| 101 | `ceil(baseCredits + duration * creditsPerSecond)` | `baseCredits=128, creditsPerSecond=126` |
| 102 | `ceil(baseCredits + duration * creditsPerSecond)` | `baseCredits=160, creditsPerSecond=158` |
| 111 | `duration_combo_map`，按 `resolution` | `720p=73, 1080p=146` |
| 52 | `combo_map`，按 `duration|resolution` | `5|720p=48, 8|720p=96, 5|1080p=96` |

本次创建视频参数为 `modelId=101,duration=4,resolution=720p`，按规则计算：

```text
ceil(128 + 4 * 126) = 632
```

`findCredits` 也验证了积分从 `1000` 降到 `368`，实际扣除 `632`。

## 制作视频

### 1. 上传素材获取签名 URL

证据：`R001852/R001872/R001898/R001962/R001981` 等。

```http
POST https://api.freebeatfit.com/api/v2/file/genUploadSignUrl
Content-Type: application/json
```

请求体：

```json
{
  "reqList": [
    {
      "key": "dance/aivideo/<timestamp>.<ext>",
      "fileName": "<local-file-name>",
      "bucketName": "freebeat-static"
    }
  ]
}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": [
    {
      "signURL": "https://freebeat-static.s3.us-east-2.amazonaws.com/dance/aivideo/<file>?<signed-query>",
      "finalStaticUrl": "https://static.freebeatfit.com/dance/aivideo/<file>"
    }
  ],
  "ext": {},
  "success": true
}
```

随后上传文件：

```http
PUT <signURL>
Content-Type: multipart/form-data
```

成功响应 HTTP `200`，响应体为空。创建视频时使用 `finalStaticUrl`，不是 S3 `signURL`。

### 2. 敏感词检查

证据：`R002019`

```http
POST https://freebeat.ai/api/proxy/v1/tools/sensitiveWord
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{
  "source": "<prompt>"
}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": false
}
```

`data=false` 表示本次提示词没有命中敏感词。

### 3. 创建视频任务

证据：`R002023`

```http
POST https://freebeat.ai/api/proxy/v1/aiVideo/createAiVideo
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{
  "aspectRatio": "9:16",
  "generationType": 3,
  "model": "seedance-2.0-fast",
  "modelId": 101,
  "duration": 4,
  "resolution": "720p",
  "style": "",
  "images": [
    "https://static.freebeatfit.com/dance/aivideo/<image1>.jpeg",
    "https://static.freebeatfit.com/dance/aivideo/<image2>.png",
    "https://static.freebeatfit.com/dance/aivideo/<image3>.jpg"
  ],
  "prompt": "<prompt>",
  "watermark": 1,
  "movementAmplitude": ""
}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": "D001505763759317061632"
}
```

`data` 是视频任务 `serialNo`。

## 任务轮询

证据：`R002031` 起，本次约每 10 秒轮询一次。

```http
POST https://freebeat.ai/api/proxy/v1/aiVideo/list
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{
  "limit": 500,
  "anchor": 1
}
```

响应中从 `data.list` 按 `serialNo` 找任务。捕获到的状态流：

| 时间 | 请求 | status | videoUrl | 推断 |
| --- | --- | ---: | --- | --- |
| 18:47:43 | `R002031` | 0 | 无 | 刚创建/排队 |
| 18:47:53 | `R002049` | 1 | 无 | 已进入处理 |
| 18:48:13 到 18:51:33 | `R002057` 到 `R002147` | 3 | 无 | 生成中 |
| 18:51:43 | `R002151` | 5 | 有非 prod mp4 | 中间结果/转码中 |
| 18:51:53 | `R002155` | 100 | 有 prod mp4 | 完成 |

完成态示例字段：

```json
{
  "serialNo": "D001505763759317061632",
  "status": 100,
  "videoUrl": "https://fb-video-n3.freebeat.ai/aiVideo/video/prod/D001505763759317061632_<timestamp>.mp4",
  "coverUrl": "https://static.freebeatfit.com/aiVideo/cover/defaultCover.png",
  "duration": 4,
  "resolution": "720p",
  "model": "seedance-2.0-fast"
}
```

轮询建议：10 秒间隔可复刻前端行为。`status=100` 且 `videoUrl` 存在时可停止轮询。

## 下载链接

### 1. 查询单个视频详情

证据：`R002240/R002310`

```http
POST https://freebeat.ai/api/proxy/v1/aiVideo/index
Authorization: <token>
Content-Type: application/json
```

请求体：

```json
{
  "sn": "D001505763759317061632"
}
```

响应：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "serialNo": "D001505763759317061632",
    "status": 100,
    "videoUrl": "https://fb-video-n3.freebeat.ai/aiVideo/video/prod/D001505763759317061632_<timestamp>.mp4",
    "coverUrl": "https://static.freebeatfit.com/aiVideo/cover/defaultCover.png"
  }
}
```

### 2. 下载 MP4

证据：`R002350/R002364/R002381`

```http
GET https://fb-video-n3.freebeat.ai/aiVideo/video/prod/D001505763759317061632_<timestamp>.mp4
```

响应：`Content-Type: video/mp4`，HTTP `200`。浏览器播放或分段加载时也可能出现 `206 Partial Content`。

## 推荐自动化顺序

1. `sendEmailVerifyCodeV2` 发送验证码。
2. 用当前页面的 Next Server Action 提交 `email + code`，解析 RSC 响应里的 `data.token`。
3. `findCredits` 确认注册赠送积分。
4. `questionnaire/check`，如未完成则 `questionnaire/submit` 领取 300。
5. `signin/status`，如 `canSignIn=true` 则 `signin/submit` 领取 200。
6. `findCredits` 确认可用总积分。
7. `getModelRuleConfig` 计算目标模型、时长、分辨率需要的积分。
8. `genUploadSignUrl` 获取素材上传签名，`PUT signURL` 上传，保存 `finalStaticUrl`。
9. `sensitiveWord` 检查 prompt。
10. `createAiVideo` 创建任务，拿到 `serialNo`。
11. 每 10 秒 `aiVideo/list` 轮询，过滤 `serialNo`，等待 `status=100 && videoUrl`。
12. 可选 `aiVideo/index` 查询详情，最终 `GET videoUrl` 下载。
