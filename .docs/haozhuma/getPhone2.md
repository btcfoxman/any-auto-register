
[TOC]
    

### 1. 指定手机号

- 更新时间：2022年10月12日 20时30分20秒
- 此接口使用场景，当某个号码需要再次接吗，需要调用该接口进行占用，然后请求读取短信接口

**请求URL **
- ` https://服务器地址/sms/?api=getPhone&token=用户令牌&sid=项目ID&phone=号码 `
  
**请求方式**
- GET/POST

**参数**

|参数名|必选 |类型 |说明 |
|:-----|:--- |:--- |:--- |
|token  |是   |string   |令牌   |
|sid  |是   |int   |项目ID   |
|phone  |是   |int   |号码   |
|author  |否   |string   |开发者账号（置入该参数获取消费分成）   |




**返回成功示例**
``` 
{
    "code": "0",
    "msg": "成功",
    "sid": "22563",
    "country_name": "中国",
    "country_code": "cn",
    "country_qu": "+86",
    "phone": "132548966",
    "sp": "联通",
    "phone_gsd": "上海 上海"
}
```
**返回参数说明 **

|参数名       |说明     |
|:-----       |-----    |
|code         |状态码，code=0，code=其他为失败  |
|msg          |描述|
|sid          |项目ID|
|country_name |国家名称  |
|country_code |国家代码  |
|country_qu   |国家区号  |
|phone        |号码   |
|sp           |号码运营商      |
|phone_gsd    |号码归属地   |

**备注 **

- code=0，code=其他为失败

