[TOC]
    
### 1. 获取手机号

- 更新时间：2023年06月29日 00时30分20秒
- 更新内容：加入了uid参数，用于只取该对接码，加入多个对接码时可以用该参数只取这个对接码的手机号
**请求URL **
- ` https://服务器地址/sms/?api=getPhone&token=用户令牌&sid=项目ID`
  
**请求方式**
- GET/POST

**参数**

|参数名|必选 |类型 |说明 |
|:-----|:--- |:--- |:--- |
|token  |是   |string   |令牌   |
|sid  |是   |int   |项目ID   |
|isp  |否   |int   |运营商，isp=1代表中国移动，参考运营商参数代码表   |
|Province  |否   |string   |号码省份，Province=44代表广东，参考省份代码表   |
|ascription  |否   |int   |号码类型，留空为不限制，ascription=1只取虚拟，ascription=2只取实卡   |
|paragraph  |否   |int   |只取号段，留空为不限制|
|exclude  |否   |int   |排除号段，留空为不限制|
|uid  |否   |string   |只取该对接码，加入多个对接码时可以用该参数只取这个对接码的手机号|
|author  |否   |string   |开发者账号（置入该参数获取消费分成）开发者分成50%   |
**备注 **
只取号段和排除号段  这两个参数多选的话可以使用 | 符号连接，如  1380|1580|1880



**返回成功示例**
``` 
{
    "code": "0",
    "msg": "成功",
    "sid": "1000",
    "shop_name": "淘宝网",
    "country_name": "cn",
    "country_code": "cn",
    "country_qu": "+86",
    "uid": null,
    "phone": "手机号",
    "sp": "移动",
    "phone_gsd": "广东"
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
|uid   |手机号所属对接码  |
|phone        |号码   |
|sp           |号码运营商      |
|phone_gsd    |号码归属地   |

**备注 **
- code=0，code=其他为失败





