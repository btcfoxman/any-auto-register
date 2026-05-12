[TOC]
    
### 1. 拉黑指定手机号

- 如果这个号码你检测知道他不符合你的使用要求或是不来码，你不想这个号码再分配出来给你，请调用本接口拉黑

**请求URL **
- ` https://服务器地址/sms/?api=addBlacklist&token=用户令牌&sid=项目ID&phone=号码 `
  
**请求方式**
- GET/POST

**参数**

|参数名|必选 |类型 |说明 |
|:-----|:--- |:--- |:--- |
|token  |是   |string   |令牌   |
|sid  |是   |int   |项目ID   |
|phone  |是   |int   |号码   |





**返回成功示例**
``` 
{
    "code": "0",
    "data": "null",
    "msg": "success"
}
```
**返回参数说明 **

|参数名       |说明     |
|:-----       |-----    |
|code         |状态码，code=0，code=其他为失败  |
|data          |-|
|msg          |描述|

**备注 **
- code=0，code=其他为失败


