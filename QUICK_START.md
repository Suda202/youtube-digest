# 飞书开放平台应用快速配置指南（推送到个人）

## 第一步：创建应用

1. 访问 https://open.feishu.cn/app
2. 点击"创建企业自建应用"
3. 填写：
   - 应用名称：YouTube 视频摘要
   - 应用描述：每日推送 YouTube 订阅频道的热门长视频摘要
   - 上传应用图标（可选）

## 第二步：配置权限

进入应用 → **权限管理** → 搜索并添加以下权限：

- ✅ `im:message` - 获取与发送单聊、群组消息
- ✅ `im:message:send_as_bot` - 以应用身份发消息

点击"保存"。

## 第三步：获取凭证

进入应用 → **凭证与基础信息**：

- **App ID**: `cli_xxxxx`（复制保存）
- **App Secret**: 点击"查看"，复制保存

## 第四步：发布应用

1. 进入应用 → **版本管理与发布**
2. 点击"创建版本"
3. 填写版本号（如 1.0.0）和更新说明
4. 点击"保存" → "申请发布"
5. 等待审核通过（通常几分钟）
6. 审核通过后，点击"全员发布"或"指定范围发布"（确保你在可用范围内）

## 第五步：获取你的 User ID

详见 [GET_USER_ID.md](./GET_USER_ID.md)

### 最简单方法：通过飞书管理后台

1. 访问 [飞书管理后台](https://feishu.cn/admin)
2. 进入"通讯录" → "成员与部门"
3. 找到你的账号，点击进入
4. 在个人信息页面，复制 **User ID**（格式：`ou_xxxxx`）

## 第六步：配置环境变量

### 本地测试

```bash
export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="你的 App Secret"
export FEISHU_USER_ID="ou_xxxxx"
export MINIMAX_API_KEY="你的 MiniMax API Key"
export YOUTUBE_API_KEY="你的 YouTube API Key"

python main.py
```

### GitHub Actions 部署

1. 进入 GitHub 仓库
2. Settings → Secrets and variables → Actions
3. 点击"New repository secret"，依次添加：
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_USER_ID`
   - `MINIMAX_API_KEY`
   - `YOUTUBE_API_KEY`

## 常见问题

### Q: 应用审核需要多久？
A: 通常 5-30 分钟，工作时间更快。

### Q: 找不到 User ID？
A: 访问飞书管理后台 → 通讯录 → 找到你的账号 → 查看个人信息。

### Q: 推送失败，提示"权限不足"？
A: 检查应用权限是否正确添加，并且应用已发布。

### Q: 推送失败，提示"invalid user_id"？
A: 确认 User ID 格式正确（`ou_` 开头），且你在应用的可用范围内。

### Q: 可以推送到多个人吗？
A: 可以。修改代码支持多个 User ID（用逗号分隔或数组），循环发送。

## 测试推送

配置完成后，运行：

```bash
python main.py
```

如果配置正确，你会在飞书中收到机器人发来的视频摘要消息。

## 下一步

- 调整 `MIN_DURATION_MINUTES` 和 `TOP_N` 参数
- 在 `channels.json` 中增删订阅频道
- 设置 GitHub Actions 定时任务
