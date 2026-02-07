# 如何获取飞书 User ID

推送到个人飞书账号需要你的 **User ID**（格式：`ou_xxxxx`）。

## 方法 1：通过飞书管理后台（最简单）

1. 访问 [飞书管理后台](https://feishu.cn/admin)
2. 进入"通讯录" → "成员与部门"
3. 找到你的账号，点击进入
4. 在个人信息页面，复制 **User ID**（`ou_xxxxx`）

## 方法 2：通过 API 获取（需要管理员权限）

### 前提条件
应用需要添加权限：`contact:user.id:readonly`

### 步骤

```bash
# 1. 获取 tenant_access_token
curl -X POST 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "你的 App ID",
    "app_secret": "你的 App Secret"
  }'

# 2. 通过手机号或邮箱获取 User ID
curl -X POST 'https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id?user_id_type=user_id' \
  -H 'Authorization: Bearer t-xxxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "emails": ["your.email@company.com"]
  }'

# 或者通过手机号
curl -X POST 'https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id?user_id_type=user_id' \
  -H 'Authorization: Bearer t-xxxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "mobiles": ["+8613800138000"]
  }'
```

## 方法 3：让机器人发消息给你（最快）

如果你已经创建了飞书应用，可以用这个方法快速获取：

### 步骤 1：创建测试脚本

创建文件 `get_user_id.py`：

```python
import os
import requests

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET")

# 获取 tenant_access_token
def get_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
    resp = requests.post(url, json=payload)
    return resp.json()["tenant_access_token"]

# 获取机器人收到的消息（需要用户先给机器人发消息）
def get_messages(token):
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"container_id_type": "chat", "page_size": 20}
    resp = requests.get(url, headers=headers, params=params)
    data = resp.json()

    if data.get("code") == 0:
        for msg in data.get("data", {}).get("items", []):
            sender = msg.get("sender", {})
            print(f"User ID: {sender.get('id')}")
            print(f"Sender Type: {sender.get('sender_type')}")
            print(f"Message: {msg.get('body', {}).get('content', '')[:50]}...")
            print("-" * 50)
    else:
        print(f"Error: {data}")

if __name__ == "__main__":
    token = get_token()
    print("请先在飞书中给机器人发送一条消息（任意内容），然后按回车...")
    input()
    get_messages(token)
```

### 步骤 2：运行脚本

```bash
export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="xxxxx"

python get_user_id.py
```

### 步骤 3：获取 User ID

1. 在飞书中搜索你创建的应用名称
2. 给机器人发送任意消息（如"hello"）
3. 回到终端，按回车
4. 脚本会显示你的 User ID

## 方法 4：通过飞书开发者工具

1. 在飞书中打开"开发者工具"（搜索"开发者工具"小程序）
2. 点击"获取我的信息"
3. 复制显示的 User ID

## 配置环境变量

获取到 User ID 后，配置环境变量：

```bash
export FEISHU_USER_ID="ou_xxxxx"
```

或在 GitHub Actions 中添加 Secret：`FEISHU_USER_ID`

## 注意事项

1. **User ID 格式**: 必须是 `ou_` 开头的字符串
2. **权限要求**: 应用需要有 `im:message:send_as_bot` 权限才能给用户发消息
3. **应用可见范围**: 确保你在应用的"可用范围"内（发布应用时设置）

## 测试推送

配置完成后，运行：

```bash
export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="xxxxx"
export FEISHU_USER_ID="ou_xxxxx"
export MINIMAX_API_KEY="xxxxx"
export YOUTUBE_API_KEY="AIzaXxx"

python main.py
```

如果配置正确，你会在飞书中收到机器人发来的视频摘要消息。
