# 飞书开放平台应用配置指南

## 一、创建应用

1. 访问 [飞书开放平台](https://open.feishu.cn/app)
2. 点击"创建企业自建应用"
3. 填写应用名称（如"YouTube 视频摘要"）、描述、图标

## 二、配置权限

进入应用 → 权限管理 → 添加以下权限：

### 必需权限
- `im:message` - 获取与发送单聊、群组消息
- `im:message:send_as_bot` - 以应用身份发消息

### 可选权限（如需发给特定用户）
- `contact:user.id:readonly` - 获取用户 user_id

## 三、获取凭证

进入应用 → 凭证与基础信息：
- **App ID**: `cli_xxxxx`
- **App Secret**: `xxxxx`（点击查看）

## 四、发布应用

1. 进入应用 → 版本管理与发布
2. 创建版本 → 提交审核
3. 审核通过后，点击"发布"
4. 在"可用范围"中添加需要使用的成员/部门

## 五、获取群聊 Chat ID

### 方法 1：通过群设置
1. 打开飞书群 → 设置 → 群机器人
2. 添加你创建的应用
3. 群设置中会显示 Chat ID（`oc_xxxxx`）

### 方法 2：通过 API
```bash
# 先获取 tenant_access_token
curl -X POST 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "cli_xxxxx",
    "app_secret": "xxxxx"
  }'

# 获取机器人所在的群列表
curl -X GET 'https://open.feishu.cn/open-apis/im/v1/chats?page_size=20' \
  -H 'Authorization: Bearer t-xxxxx'
```

## 六、环境变量配置

```bash
# 飞书开放平台应用方式
export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="xxxxx"
export FEISHU_CHAT_ID="oc_xxxxx"  # 目标群聊 ID

# 其他配置
export MINIMAX_API_KEY="xxxxx"
export YOUTUBE_API_KEY="AIzaXxx"
```

## 七、代码改动

当前 `main.py` 使用 Webhook 方式，需要修改为开放平台 API：

### 改动点
1. 删除 `FEISHU_WEBHOOK_URL` 和 `FEISHU_WEBHOOK_SECRET`
2. 添加 `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_CHAT_ID`
3. 修改 `send_to_feishu()` 函数：
   - 先调用 `/auth/v3/tenant_access_token/internal` 获取 token
   - 再调用 `/im/v1/messages?receive_id_type=chat_id` 发送消息

### API 文档
- [获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)
- [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

## 对比：Webhook vs 开放平台应用

| 特性 | Webhook | 开放平台应用 |
|------|---------|-------------|
| 配置难度 | 简单 | 中等 |
| 发送目标 | 仅添加机器人的群 | 任意用户/群 |
| 认证方式 | 签名（可选） | tenant_access_token |
| 功能 | 仅发消息 | 发消息、接收消息、卡片交互等 |
| 适用场景 | 单向通知 | 双向交互 |

## 建议

- **如果只需要推送到固定群**：继续用 Webhook（当前方式），配置最简单
- **如果需要发给多个群或用户**：用开放平台应用
