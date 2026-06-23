# YouTube Digest → 飞书

每日自动扫描 YouTube 订阅频道，用 LLM 智能筛选最值得深度观看的长视频，生成中文摘要，推送到飞书个人消息。

### 体验每日推送

扫码加入飞书群，直接看每天的推荐效果：

<img src="feishu-group-qr.png" width="280" alt="飞书体验群二维码">
<img width="2304" height="1810" alt="image" src="https://github.com/user-attachments/assets/9540f55d-43e4-4e2e-bd53-d3b2c0592610" />

## 核心逻辑：三阶段流水线

```
阶段一：收集候选            阶段二：预过滤 + DeepSeek 排序   阶段三：摘要 + 推送

76个频道 RSS 并发轮询       硬规则预过滤                     Top N 视频
    ↓                        ↓                               ↓
过滤 Shorts(<3min)         DeepSeek v4 Flash 智能排序       yt-dlp 获取字幕
    ↓                        ↓                               ↓
YouTube Data API            输出 Top N + 推荐理由            DeepSeek 生成中文摘要
(时长+描述+播放量)          (失败→播放量排序)                 ↓
                                                            合并 AI HOT 精选
                                                            ↓
                                                            飞书日报合并推送
```

### 阶段一：收集候选视频

1. 遍历 `channels.json` 中的订阅频道，**并发拉取** YouTube RSS（10 线程），获取最近 24h 内发布的视频；`channel_id` RSS 失败时，会先重试并改用同频道 uploads playlist RSS，两个 RSS 都失败才用 YouTube Data API 兜底
2. RSS 天然不包含 Shorts，再通过 YouTube Data API 过滤掉时长 < 3 分钟的短视频（带 quota 保护，接近上限自动停止）
3. 同时获取每个视频的 description 和播放量，作为后续排序和摘要的输入
4. 通过 `history.json` 去重，避免重复推送

### 阶段二：预过滤 + DeepSeek 智能排序（核心）

**硬规则预过滤**（在 LLM 排序前剔除明显不符合的候选）：
- 排除入门教程/全课程（标题匹配 "Full Course", "Tutorial For Beginners" 等）
- 排除播放量极低（<200）且不在常看频道列表中的视频
- 排除明显偏投资/金融（股票、估值、融资、portfolio 等）和纯技术实现（论文精读、代码、API、RAG 调参等）的标题或描述
- 对偏投资或偏技术频道做频道级过滤，只保留明确相关的 AI 产品、GTM、SaaS、创意/广告、客户案例或工作流内容

**DeepSeek 智能排序**：将预过滤后的候选视频列表（含标题、频道、时长、播放量、description 前 300 字）交给 DeepSeek v4 Flash，由 LLM 根据用户画像挑选最多 Top N 并给出推荐理由。默认宁缺毋滥，达不到标准时可以少选。

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

**容错**：DeepSeek 调用失败 → 播放量排序。

### 阶段三：摘要生成 + 飞书推送

1. 对 Top N 视频，优先用 yt-dlp 获取字幕生成摘要（内容最完整），字幕不可用时 fallback 到 description
2. DeepSeek v4 Flash 生成短摘要：结论 + 最多 3 个要点 + 适合场景，默认控制在 350 中文字符以内
3. 所有视频合并为一条"今日推荐"日报，优先通过飞书应用机器人推送
4. YouTube 和 AI HOT 都提供 👍/👎 一键反馈；回调先返回成功提示，再异步写入 `feedback.json`
   - 每天只分析新增点击，提取主题、内容形态、价值和来源四类弱信号，避免旧反馈被重复累计
   - 单次点击只进入短期偏好；每满 7 天自动归纳一次，至少 2 条不同内容形成同向证据才会升级为稳定偏好
   - 点踩“入门教程”只降低这种内容形态，不会连带惩罚 Agent 等上层主题
5. 默认拉取最近 `LOOKBACK_HOURS` 内的 AI HOT 候选，经过质量门槛和个性化筛选后最多选 3 条，也允许 0 条；优先 Agent / Loop Engineering、硅谷前沿趋势和有产品或商业价值的内容，排除节日营销、转售拼接新闻与普通 API/安装教程
   - AI HOT 卡片只显示标题、分段摘要和原文链接，不展示来源、时间、分数或分类
6. 如果当天没有符合条件的视频且 AI HOT 也无内容，默认只写日志；需要状态卡时可开启 `FEISHU_SEND_STATUS_CARD`
7. 每条视频包含：频道名、时长、播放量、推荐理由、摘要、原视频链接

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 视频源 | YouTube RSS + Data API v3 | RSS 并发轮询 + API 补充详情 |
| AI 资讯 | AI HOT Public API | 拉取最近精选 AI 动态，无需 API Key |
| 字幕 | yt-dlp | 获取视频字幕用于摘要生成 |
| 排序与摘要 LLM | DeepSeek v4 Flash | 通过 OpenAI 兼容 API 调用 |
| 推送 | 飞书开放平台应用 | tenant_access_token → 个人或群消息 |
| 反馈 | 飞书卡片回调 + Cloudflare Worker | YouTube / AI HOT 按钮点击 → GitHub data 分支 |
| 偏好学习 | DeepSeek + 增量状态机 | 每日轻量更新，满 7 天自动归纳稳定偏好 |
| 调度 | GitHub Actions | 通过外部定时触发 `workflow_dispatch`，避免 GitHub schedule 重复发送 |
| 去重 | history.json | 存储在 Git `data` 分支，自动清理 30 天前记录 |

## 文件结构

```
├── main.py                          # 核心脚本（三阶段流水线）
├── channels.json                    # 76 个订阅频道（channel_id + name）
├── requirements.txt                 # 依赖：requests, yt-dlp
├── history.json                     # 已处理视频 ID + 时间戳（运行时生成，自动清理 30 天前记录）
├── preference_learning.py           # 偏好去重、衰减与每日/每周状态转换
├── preference_state.json            # 增量偏好状态（运行时生成，保存在 data 分支）
├── update_preferences.py            # 分析新增反馈并生成动态排序提示
├── worker/                          # 飞书卡片点击反馈回调 Worker
├── .github/workflows/digest.yml     # GitHub Actions 日报任务（外部定时触发）
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
| `DEEPSEEK_API_KEY` | 是 | - | DeepSeek API Key |
| `DEEPSEEK_API_BASE` | 否 | `https://api.deepseek.com` | DeepSeek OpenAI 兼容 Base URL |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` | 排序、摘要和偏好分析模型名 |
| `YOUTUBE_API_KEY` | 是 | - | YouTube Data API Key |
| `FEISHU_WEBHOOK_URL` | 否 | - | 群自定义机器人兜底通道，不支持点击反馈 |
| `FEISHU_SEND_STATUS_CARD` | 否 | `false` | 无候选/无推荐时是否推送状态卡；默认关闭，避免重复触发时多一条消息 |
| `YT_COOKIES_FILE` | 否 | - | YouTube cookies 文件路径（yt-dlp 字幕获取用，避免 bot 检测） |
| `MIN_DURATION_MINUTES` | 否 | `3` | 最短视频时长（分钟），过滤 Shorts |
| `TOP_N` | 否 | `3` | 每日推送视频数量 |
| `LOOKBACK_HOURS` | 否 | `24` | 回溯时间窗口（小时） |
| `AIHOT_ENABLED` | 否 | `true` | 是否合并 AI HOT 精选资讯 |
| `AIHOT_TAKE` | 否 | `3` | 每次最多合并的 AI HOT 条数，允许少于该值或 0 条，最大 20 |
| `AIHOT_CANDIDATE_TAKE` | 否 | `30` | AI HOT 二次排序前拉取的候选池大小，最大 100 |
| `AIHOT_MIN_SCORE` | 否 | `0` | AI HOT 最低分数门槛；默认不过滤 |
| `AIHOT_API_BASE` | 否 | `https://aihot.virxact.com` | AI HOT API Base，一般不用改 |
| `HISTORY_MAX_DAYS` | 否 | `30` | 历史记录保留天数（自动清理） |
| `YOUTUBE_UPLOADS_PAGE_SIZE` | 否 | `5` | RSS 兜底时每个频道检查的最新 uploads 数量 |
| `RSS_RETRY_ATTEMPTS` | 否 | `2` | 每个 RSS URL 的请求尝试次数 |
| `RSS_RETRY_DELAY_SECONDS` | 否 | `1` | RSS 重试间隔秒数 |

## 部署

### GitHub Actions（推荐）

1. Fork 本仓库
2. Settings → Secrets → Actions → 添加必填环境变量（FEISHU_APP_ID, FEISHU_APP_SECRET, DEEPSEEK_API_KEY, YOUTUBE_API_KEY），并配置 `FEISHU_CHAT_ID` 或 `FEISHU_USER_ID`；如需覆盖默认模型，可额外配置 `DEEPSEEK_API_BASE`、`DEEPSEEK_MODEL`
3. Actions → YouTube Digest Daily → Run workflow 手动测试
4. 配置外部定时器每天触发 `workflow_dispatch`；不要同时启用 GitHub `schedule`，避免同一天重复发送
5. `history.json`、`feedback.json` 和 `preference_state.json` 自动保存在 `data` 分支，用于跨次去重与持续偏好学习
6. 如需点击反馈，部署 `worker/` 并在飞书开放平台配置卡片回调地址

### 本地运行

```bash
pip install -r requirements.txt

export FEISHU_APP_ID="cli_xxxxx"
export FEISHU_APP_SECRET="xxxxx"
export FEISHU_CHAT_ID="oc_xxxxx"  # 或 FEISHU_USER_ID="ou_xxxxx"
export DEEPSEEK_API_KEY="xxxxx"
export DEEPSEEK_API_BASE="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-v4-flash"
export YOUTUBE_API_KEY="AIzaXxx"
export AIHOT_ENABLED="true"

python main.py
```

## 成本

- **YouTube Data API**：扣免费 quota，不直接按请求扣钱；默认每日 10,000 quota，当前只对 RSS 成功发现的新视频查详情，两个 RSS 源都失败时才额外用 API 兜底
- **DeepSeek API**：按 token 计费（排序、摘要、AI HOT 筛选和偏好分析）
- **飞书 API**：免费
- **GitHub Actions**：公开仓库免费，私有仓库每月 2000 分钟免费额度
