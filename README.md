# YouTube Digest → 飞书

每日自动扫描 YouTube 订阅频道，用 LLM 智能筛选最值得深度观看的长视频，生成中文摘要，推送到飞书个人消息。

### 体验每日推送

扫码加入飞书群，直接看每天的推荐效果：

<img src="feishu-group-qr.png" width="280" alt="飞书体验群二维码">
![Uploading image.png…]()


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

1. 遍历 `channels.json` 中 76 个频道，**并发拉取** YouTube RSS（10 线程），获取最近 24h 内发布的视频
2. RSS 天然不包含 Shorts，再通过 YouTube Data API 过滤掉时长 < 3 分钟的短视频（带 quota 保护，接近上限自动停止）
3. 同时获取每个视频的 description 和播放量，作为后续排序和摘要的输入
4. 通过 `history.json` 去重，避免重复推送

### 阶段二：预过滤 + Gemini 智能排序（核心）

**硬规则预过滤**（在 LLM 排序前剔除明显不符合的候选）：
- 排除入门教程/全课程（标题匹配 "Full Course", "Tutorial For Beginners" 等）
- 排除播放量极低（<200）且不在常看频道列表中的视频

**Gemini 智能排序**：将预过滤后的候选视频列表（含标题、频道、时长、播放量、description 前 300 字）交给 Gemini 3 Flash，由 LLM 根据用户画像挑选 Top N 并给出推荐理由。

**用户画像**（内置于 prompt）：
- AI 行业从业者，关注 AI 技术前沿、创业、投资、产品策略
- 常看频道：AI Engineer, Lenny's Podcast, a16z, Dwarkesh Patel, Lex Fridman, Acquired, Latent Space, No Priors, Andrej Karpathy, Peter Yang, Hamel Husain, Y Combinator, 硅谷101播客, 張小珺Xiaojùn Podcast 等
- 最喜欢：创始人/研究者深度访谈、行业大会演讲、技术架构深度讨论

**必须排除**（即使播放量高也不选）：
- 纯新闻汇总/速报类标题党
- 入门教程/全课程
- 与 AI/科技行业无关的内容

**容错**：Gemini 调用失败 → MiniMax 兜底 → 播放量排序。

### 阶段三：摘要生成 + 飞书推送

1. 对 Top N 视频，优先用 yt-dlp 获取字幕生成摘要（内容最完整），字幕不可用时 fallback 到 description
2. MiniMax M2.1 生成结构化中文摘要：核心内容概括 + 编号要点（具体关键词/概念名 + 核心信息） + 推荐语
3. 所有视频合并为一条"今日推荐"日报，通过飞书开放平台推送到个人
4. 每条视频包含：频道名、时长、播放量、推荐理由、摘要、原视频链接

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 视频源 | YouTube RSS + Data API v3 | RSS 并发轮询 + API 补充详情 |
| 字幕 | yt-dlp | 获取视频字幕用于摘要生成 |
| 排序 LLM | Gemini 3 Flash | 智能筛选排序（MiniMax 兜底） |
| 摘要 LLM | MiniMax M2.1 | 通过 Anthropic 兼容 API 调用 |
| 推送 | 飞书开放平台应用 | tenant_access_token → 个人消息 |
| 调度 | GitHub Actions | 每日北京时间 10:00 自动运行 |
| 去重 | history.json | 存储在 Git `data` 分支，自动清理 30 天前记录 |

## 文件结构

```
├── main.py                          # 核心脚本（三阶段流水线）
├── channels.example.json            # 频道列表模板（5 个示例）
├── channels.json                    # 你的真实频道列表（gitignored，从 example 复制）
├── profile.example.json             # 用户画像模板（示例偏好）
├── profile.json                     # 你的真实偏好（gitignored，从 example 复制）
├── history.json                     # 已处理视频 ID（gitignored，运行时自动生成）
├── requirements.txt                 # 依赖：requests, yt-dlp, google-genai
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
| `FEISHU_USER_ID` | 是 | - | 推送目标用户 ID |
| `MINIMAX_API_KEY` | 是 | - | MiniMax API Key（摘要生成） |
| `GEMINI_API_KEY` | 是 | - | Gemini API Key（智能排序） |
| `YOUTUBE_API_KEY` | 是 | - | YouTube Data API Key |
| `YT_COOKIES_FILE` | 否 | - | YouTube cookies 文件路径（yt-dlp 字幕获取用，避免 bot 检测） |
| `MIN_DURATION_MINUTES` | 否 | `3` | 最短视频时长（分钟），过滤 Shorts |
| `TOP_N` | 否 | `5` | 每日推送视频数量 |
| `LOOKBACK_HOURS` | 否 | `24` | 回溯时间窗口（小时） |
| `HISTORY_MAX_DAYS` | 否 | `30` | 历史记录保留天数（自动清理） |

## 部署

### GitHub Actions（推荐）

1. Fork 本仓库
2. 复制配置模板并填入你的偏好：
   ```bash
   cp channels.example.json channels.json   # 编辑添加你关注的频道
   cp profile.example.json profile.json     # 编辑填入你的兴趣画像
   ```
3. Settings → Secrets → Actions → 添加 6 个必填环境变量（FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_USER_ID, MINIMAX_API_KEY, GEMINI_API_KEY, YOUTUBE_API_KEY）
4. Actions → YouTube Digest Daily → Run workflow 手动测试
5. 之后每天北京时间 10:00 (UTC 02:00) 自动运行
6. `channels.json`、`profile.json`、`history.json` 自动保存在 `data` 分支，跨次运行持久化

### 本地运行

```bash
pip install -r requirements.txt
cp channels.example.json channels.json   # 编辑添加你的频道
cp profile.example.json profile.json     # 编辑填入你的偏好

export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="xxxxx"
export FEISHU_USER_ID="xxxxx"
export MINIMAX_API_KEY="xxxxx"
export GEMINI_API_KEY="AIzaXxx"
export YOUTUBE_API_KEY="AIzaXxx"

python main.py
```

## 成本

- **YouTube Data API**：免费配额（每日 10,000 quota，每次视频详情查询消耗 3 quota）
- **MiniMax API**：按 token 计费，每日约 ¥0.1-0.3（摘要 5 次）
- **Gemini API**：按 token 计费，每日仅 1 次排序调用，费用极低
- **飞书 API**：免费
- **GitHub Actions**：公开仓库免费，私有仓库每月 2000 分钟免费额度
