# YouTube Digest → 飞书

每日自动扫描 YouTube 订阅频道，用 LLM 智能筛选最值得深度观看的长视频，生成中文摘要，推送到飞书个人消息。

### 体验每日推送

扫码加入飞书群，直接看每天的推荐效果：

<img src="feishu-group-qr.png" width="280" alt="飞书体验群二维码">
<img width="2304" height="1810" alt="image" src="https://github.com/user-attachments/assets/9540f55d-43e4-4e2e-bd53-d3b2c0592610" />

## 核心逻辑：三阶段流水线

```
阶段一：收集候选            阶段二：预过滤 + Gemini 排序     阶段三：摘要 + 推送

76个频道 RSS 并发轮询       硬规则预过滤                     Top N 视频
    ↓                        ↓                               ↓
过滤 Shorts(<3min)         Gemini 3 Flash 智能排序          yt-dlp 获取字幕
    ↓                        ↓                               ↓
YouTube Data API            输出 Top N + 推荐理由            MiniMax 生成中文摘要
(时长+描述+播放量)          (失败→MiniMax→播放量排序)         ↓
                                                            飞书日报合并推送
```

### 阶段一：收集候选视频

1. 遍历 `channels.json` 中的订阅频道，**并发拉取** YouTube RSS（10 线程），获取最近 24h 内发布的视频；只有 RSS 请求失败时，才用 YouTube Data API 的 uploads playlist 兜底
2. RSS 天然不包含 Shorts，再通过 YouTube Data API 过滤掉时长 < 3 分钟的短视频（带 quota 保护，接近上限自动停止）
3. 同时获取每个视频的 description 和播放量，作为后续排序和摘要的输入
4. 通过 `history.json` 去重，避免重复推送

### 阶段二：预过滤 + Gemini 智能排序（核心）

**硬规则预过滤**（在 LLM 排序前剔除明显不符合的候选）：
- 排除入门教程/全课程（标题匹配 "Full Course", "Tutorial For Beginners" 等）
- 排除播放量极低（<200）且不在常看频道列表中的视频
- 排除明显偏投资/金融（股票、估值、融资、portfolio 等）和纯技术实现（论文精读、代码、API、RAG 调参等）的标题或描述
- 对偏投资或偏技术频道做频道级过滤，只保留明确相关的 AI 产品、GTM、SaaS、创意/广告、客户案例或工作流内容

**Gemini 智能排序**：将预过滤后的候选视频列表（含标题、频道、时长、播放量、description 前 300 字）交给 Gemini 3 Flash，由 LLM 根据用户画像挑选最多 Top N 并给出推荐理由。默认宁缺毋滥，达不到标准时可以少选。

**用户画像**（内置于 prompt）：
- AI 产品经理，关注 AI 产品设计、用户体验、商业化、广告创意智能体、海外市场和产品策略
- 常看频道：Peter Yang, Lenny's Podcast, Hamel Husain, AI Engineer, Latent Space, OpenAI, Anthropic, Figma, Product Talk, Every, Intercom, Stripe 等
- 最喜欢：能转化为产品判断的高密度内容，少而精

**必须排除**（即使播放量高也不选）：
- 纯新闻汇总/速报类标题党
- 入门教程/全课程
- 纯投资、融资、估值、股票、基金、宏观市场、VC 观点输出
- 纯技术细节：论文精读、代码实现、模型架构、框架/API 教程、RAG/向量库调参
- 与 AI/科技行业无关的内容

**容错**：Gemini 调用失败 → MiniMax 兜底 → 播放量排序。

### 阶段三：摘要生成 + 飞书推送

1. 对 Top N 视频，优先用 yt-dlp 获取字幕生成摘要（内容最完整），字幕不可用时 fallback 到 description
2. MiniMax M2.1 生成短摘要：结论 + 最多 3 个要点 + 适合场景，默认控制在 350 中文字符以内
3. 所有视频合并为一条"今日推荐"日报，优先通过飞书应用机器人推送
4. 应用机器人卡片包含 👍/👎 点击反馈，卡片回调会先返回成功提示，再异步写入 `feedback.json`，下次运行前生成动态排序提示
   - 反馈学习以主题为主：单次点踩只影响主题，不直接惩罚频道
   - 同一频道累计多次净点踩后，才会生成频道级回避提示
5. 如果当天没有符合条件的视频，会推送一条简短状态卡，避免静默失败
6. 每条视频包含：频道名、时长、播放量、推荐理由、摘要、原视频链接

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 视频源 | YouTube RSS + Data API v3 | RSS 并发轮询 + API 补充详情 |
| 字幕 | yt-dlp | 获取视频字幕用于摘要生成 |
| 排序 LLM | Gemini 3 Flash | 智能筛选排序（MiniMax 兜底） |
| 摘要 LLM | MiniMax M2.1 | 通过 Anthropic 兼容 API 调用 |
| 推送 | 飞书开放平台应用 | tenant_access_token → 个人或群消息 |
| 反馈 | 飞书卡片回调 + Cloudflare Worker | 按钮点击 → GitHub data 分支 feedback.json |
| 调度 | GitHub Actions | 每日北京时间 09:30 自动运行 |
| 去重 | history.json | 存储在 Git `data` 分支，自动清理 30 天前记录 |

## 文件结构

```
├── main.py                          # 核心脚本（三阶段流水线）
├── channels.json                    # 76 个订阅频道（channel_id + name）
├── requirements.txt                 # 依赖：requests, yt-dlp, google-genai
├── history.json                     # 已处理视频 ID + 时间戳（运行时生成，自动清理 30 天前记录）
├── update_preferences.py            # 从 feedback.json 生成动态排序提示
├── worker/                          # 飞书卡片点击反馈回调 Worker
├── .github/workflows/digest.yml     # GitHub Actions 定时任务
├── FEISHU_APP_SETUP.md              # 飞书应用配置指南
├── GET_USER_ID.md                   # 获取飞书 User ID 指南
└── QUICK_START.md                   # 快速开始
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `FEISHU_APP_ID` | 是 | - | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 是 | - | 飞书应用 App Secret |
| `FEISHU_USER_ID` | 否 | - | 推送目标用户 ID；未配置 `FEISHU_CHAT_ID` 时使用 |
| `FEISHU_CHAT_ID` | 否 | - | 推送目标群 ID；配置后优先发群聊 |
| `MINIMAX_API_KEY` | 是 | - | MiniMax API Key（摘要生成） |
| `GEMINI_API_KEY` | 是 | - | Gemini API Key（智能排序） |
| `YOUTUBE_API_KEY` | 是 | - | YouTube Data API Key |
| `FEISHU_WEBHOOK_URL` | 否 | - | 群自定义机器人兜底通道，不支持点击反馈 |
| `YT_COOKIES_FILE` | 否 | - | YouTube cookies 文件路径（yt-dlp 字幕获取用，避免 bot 检测） |
| `MIN_DURATION_MINUTES` | 否 | `3` | 最短视频时长（分钟），过滤 Shorts |
| `TOP_N` | 否 | `3` | 每日推送视频数量 |
| `LOOKBACK_HOURS` | 否 | `24` | 回溯时间窗口（小时） |
| `HISTORY_MAX_DAYS` | 否 | `30` | 历史记录保留天数（自动清理） |
| `YOUTUBE_UPLOADS_PAGE_SIZE` | 否 | `5` | RSS 兜底时每个频道检查的最新 uploads 数量 |

## 部署

### GitHub Actions（推荐）

1. Fork 本仓库
2. Settings → Secrets → Actions → 添加必填环境变量（FEISHU_APP_ID, FEISHU_APP_SECRET, MINIMAX_API_KEY, GEMINI_API_KEY, YOUTUBE_API_KEY），并配置 `FEISHU_CHAT_ID` 或 `FEISHU_USER_ID`
3. Actions → YouTube Digest Daily → Run workflow 手动测试
4. 之后每天北京时间 09:30 (UTC 01:30) 自动运行
5. `history.json` 自动保存在 `data` 分支，跨次运行去重
6. 如需点击反馈，部署 `worker/` 并在飞书开放平台配置卡片回调地址

### 本地运行

```bash
pip install -r requirements.txt

export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="xxxxx"
export FEISHU_CHAT_ID="oc_xxxxx"  # 或 FEISHU_USER_ID="ou_xxxxx"
export MINIMAX_API_KEY="xxxxx"
export GEMINI_API_KEY="AIzaXxx"
export YOUTUBE_API_KEY="AIzaXxx"

python main.py
```

## 成本

- **YouTube Data API**：扣免费 quota，不直接按请求扣钱；默认每日 10,000 quota，当前只对 RSS 成功发现的新视频查详情，RSS 请求失败时才额外用 uploads playlist 兜底
- **MiniMax API**：按 token 计费，每日约 ¥0.1-0.3（默认摘要最多 3 次）
- **Gemini API**：免费额度充足（排序 1 次/天）
- **飞书 API**：免费
- **GitHub Actions**：公开仓库免费，私有仓库每月 2000 分钟免费额度
