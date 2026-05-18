# 飞书开放平台应用配置指南

本项目现在优先使用飞书开放平台应用机器人发送卡片。应用机器人发送的卡片支持点击回调，可用于记录 👍/👎 反馈；群自定义机器人 Webhook 只作为兜底推送通道，不支持点击回调。

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
export FEISHU_CHAT_ID="oc_xxxxx"   # 目标群聊 ID，推荐
# 或者发给个人：
export FEISHU_USER_ID="ou_xxxxx"

# 其他配置
export MINIMAX_API_KEY="xxxxx"
export GEMINI_API_KEY="xxxxx"
export YOUTUBE_API_KEY="AIzaXxx"
```

## 七、配置点击反馈回调

点击反馈通过 `worker/` 下的 Cloudflare Worker 接收：

```bash
cd worker
wrangler secret put GH_TOKEN
wrangler deploy
```

然后在飞书开放平台应用里配置卡片回调地址：

```text
https://<your-worker>.workers.dev/
```

Worker 会把点击反馈写入 GitHub `data` 分支的 `feedback.json`。GitHub Actions 每次运行前会执行 `update_preferences.py`，生成 `ranking_hints.txt` 并注入排序 prompt。

## 对比：Webhook vs 开放平台应用

| 特性 | Webhook | 开放平台应用 |
|------|---------|-------------|
| 配置难度 | 简单 | 中等 |
| 发送目标 | 仅添加机器人的群 | 任意用户/群 |
| 认证方式 | 签名（可选） | tenant_access_token |
| 功能 | 仅发消息 | 发消息、接收消息、卡片交互等 |
| 适用场景 | 单向通知 | 双向交互 |

## 建议

- **需要点击反馈**：必须用开放平台应用机器人发送卡片
- **只需要单向通知**：可以保留 `FEISHU_WEBHOOK_URL` 作为兜底
