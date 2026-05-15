# FXY Study System

**一个 AI 驱动的大学期末复习系统**，基于 Obsidian 知识库 + 自建服务器，覆盖从笔记管理、智能复习到任务调度的完整学习工作流。

> 这个系统是我（上海交通大学大一）在准备期末考试期间，和 Claude 一起从零搭建的。它不是一个概念验证或 demo，而是每天真实在用的工具——早上 7 点自动推送今日任务，课间用 Memos 记笔记，晚上 AI 自动汇总生成学习回顾。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  本地 Obsidian Vault                                        │
│  ├── AI笔记/（Gemini对话自动保存）                            │
│  ├── AI题库/（自动提取的题目+解答）                            │
│  ├── 课程/（8科课程笔记）                                     │
│  └── 每日笔记/（早报/晚报/学习回顾）                           │
│            ↕ Remotely Save（WebDAV 同步）                     │
├─────────────────────────────────────────────────────────────┤
│  2H4G 服务器（ARM64, 2核4GB）                                │
│  ├── chat-server.cjs ── Node.js 后端，17 个 API 端点          │
│  ├── vikunja-dashboard.py ── Python 自动化，12 个 cron 模式   │
│  ├── 11 个 Web 应用 ── 纯 HTML+JS，暗色主题                   │
│  ├── Quartz ── 静态网站生成器（每10分钟自动构建）               │
│  └── Docker ── Vikunja / Memos / Glance / Uptime Kuma / ...  │
├─────────────────────────────────────────────────────────────┤
│  浏览器扩展                                                  │
│  └── Gemini to Obsidian ── 一键保存 AI 对话到 vault           │
└─────────────────────────────────────────────────────────────┘
```

---

## 核心功能

### 1. AI 智能体

| 智能体 | 触发方式 | 功能 |
|--------|---------|------|
| **Smart Note** | 保存 Gemini 对话时 | 自动提取闪卡 + 复习精华 + 重复弱点检测 + 题目提取（含 Vision OCR 识别图片题目）|
| **每日挑战** | 每天 8:30 cron | 从近3天学习回顾中提取弱点 → AI 生成3道测试题 → 推送到 Memos → 晚间 AI 批改 |
| **弱点分析** | 手动或 API | 跨数据源分析（AI笔记 + 闪卡 + 间隔重复 + 任务完成率 + 学习回顾）→ 弱点排名 + 练习题 |
| **知识覆盖度** | 手动或 API | 将课程章节目录与实际学习记录匹配 → 标记 covered/partial/uncovered |
| **GPA 预测** | 自动 | 基于学习数据预测三档期末成绩（保守/中等/乐观）|

### 2. 自动化（每天无需手动操作）

```
07:00  早报
       ├── 任务自动均衡（每天>4项时，低优先级自动推迟）
       ├── Canvas 作业自动同步到 Vikunja
       ├── 学习打卡数据处理
       ├── 期末冲刺表 + 昨日学习统计
       └── AI 复习建议（基于昨日未掌握内容）

07:05  间隔重复（SM-2 算法，扫描近3天学习回顾）

08:30  每日挑战推送（3道题到 Memos）

每30分  Memos 标签处理
         ├── #学了 高数 45min → 记录学习时长
         ├── #出题 积分换元 → AI 自动生成练习题
         ├── #问 SN2机理 → AI 自动回答
         └── #查 格林公式 → 笔记快速检索（3秒）

12:00  每日资讯（校园公告 + 学术新闻 AI 筛选）

22:00  睡前复习推送（今日3个关键知识点）

22:30  晚报 + 学习回顾
       ├── 任务完成统计
       ├── 今日学习时长 + 上周同日对比（📈/📉）
       ├── 知识点掌握度标注（✅⚠️❌）
       ├── 明日行动自动创建（模糊去重）
       └── 每日挑战 AI 批改

周日    周报（薄弱点追踪 + 学习科目汇总）
```

### 3. Memos 标签体系

在 Memos（轻量笔记应用）中发一条消息，系统自动处理：

```
#学了 高数 45min        →  记录学习时长，早晚报展示
#出题 高数格林公式       →  AI 生成3道练习题，保存到 vault
#问 SN2和SN1的区别      →  AI 回答并追加到原 memo
#查 华里士公式          →  3秒内从笔记库检索结果（不走 AI）
```

### 4. Web 应用（11个页面）

| 页面 | 功能 |
|------|------|
| **指挥中心** (index.html) | 期末倒计时 + 今日待办 + 学习时长 + GPA 预测 + 工具入口 |
| **AI 题库** (problems.html) | 从 Gemini 对话自动提取的题目，支持复习模式（答案模糊→点击显示→评分）|
| **弱点分析** (weakness.html) | 按科目 AI 分析 + 知识覆盖度检查 + 一键生成强化计划 |
| **闪卡复习** (flashcards.html) | 250+ 张闪卡，间隔重复算法，手机友好 |
| **每日计划** (planner.html) | AI 自适应计划（注入学习打卡 + 弱点 + 间隔重复数据）|
| **AI 助手** (chat.html) | RAG 笔记检索对话（自动注入弱点上下文）|
| **复习仪表盘** (dashboard.html) | 学习热力图（服务端数据）+ GPA 模拟器 |
| **公式速查** (formulas.html) | 高数/概统/大物/分析化学公式，实时搜索 |
| **错题本** (mistakes.html) | 拍照/LaTeX 录入，复习模式 |
| **专注计时** (focus.html) | 番茄钟，锁屏提醒 |
| **任务智能体** (task-agent.html) | 自然语言创建 Vikunja 任务（"明天交高数作业"→自动解析）|

### 5. Gemini to Obsidian 浏览器扩展

在 Gemini 页面点击保存按钮：
1. 自动抓取对话（含图片、LaTeX、表格）
2. AI 识别科目 + 生成标题和要点
3. 保存为 Obsidian Markdown 笔记
4. 自动生成闪卡 + 复习精华 + 题目提取
5. 如果图片中有题目，Vision OCR 自动识别

### 6. 任务管理

- **自动均衡**: 检测每天任务量，超过4项时低优先级自动推迟到空闲日
- **过期处理**: 过期的复习任务自动推到今天重新分配
- **模糊去重**: normalize + SequenceMatcher，"复习格林公式"和"高数II：格林公式复习"不会重复创建
- **Canvas 同步**: 每天早上自动检查新作业，创建 Vikunja 任务

---

## 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Node.js (原生 http 模块, ~90KB) + Python 3 (~100KB) |
| **AI** | DeepSeek V3.2 (推理/任务) + Qwen (Vision OCR)，通过 OpenAI 兼容 API |
| **前端** | 纯 HTML5 + CSS3 + JavaScript，无框架，暗色主题 |
| **任务管理** | Vikunja (自建 Docker) |
| **速记** | Memos (自建 Docker) |
| **推送** | Bark (iOS，按分组: morning/evening/challenge/quiz 等) |
| **笔记同步** | WebDAV (Remotely Save Obsidian 插件) |
| **静态网站** | Quartz (Obsidian → 网站，每10分钟自动构建) |
| **反向代理** | Nginx + Cloudflare Tunnel (无需公网 IP) |

---

## API 端点 (17个)

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/chat` | POST | RAG 笔记检索对话（含弱点上下文注入）|
| `/api/plan` | POST | AI 每日计划（注入学习打卡+弱点+间隔重复）|
| `/api/smart-note` | POST | 保存 Gemini 对话（触发完整处理链）|
| `/api/canvas` | GET | Canvas 待办作业 |
| `/api/task-agent` | POST | 自然语言 → Vikunja 任务 |
| `/api/weakness` | POST | 按科目弱点分析 |
| `/api/weakness-plan` | POST | 弱点 → 自动创建复习任务 |
| `/api/coverage` | POST | 章节知识覆盖度 |
| `/api/gpa-predict` | GET | GPA 三档预测 |
| `/api/problems` | GET | AI 题库列表 |
| `/api/problems/review` | POST | 更新题目掌握度 |
| `/api/study-stats` | GET | 学习打卡统计 |
| `/api/flashcards` | GET | 闪卡同步 |
| `/api/search` | POST | 笔记全文搜索 |
| `/api/health` | GET | 健康检查 |

---

## 部署指南

### 前置条件
- 一台 Linux 服务器（2核4GB 即可，ARM64/x86 均可）
- Node.js 18+
- Python 3.10+
- Docker（用于 Vikunja、Memos 等）
- Nginx
- 一个域名（可选，Cloudflare Tunnel 可替代公网 IP）
- OpenAI 兼容的 AI API（如校内 API、DeepSeek 官方等）

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/fxy2026/fxy-study-system.git
cd fxy-study-system

# 2. 配置凭据
cp server/config.example.env server/.env
vim server/.env  # 填入你的 API Key、Token 等

# 3. 部署后端
scp server/chat-server.cjs server/vikunja-dashboard.py \
    server/news_fetcher.py server/vikunja-reminder.py root@YOUR_SERVER:/root/

# 4. 配置 systemd 服务
scp scripts/chat-server.service root@YOUR_SERVER:/etc/systemd/system/
ssh root@YOUR_SERVER "systemctl enable --now chat-server"

# 5. 配置 Nginx
scp scripts/nginx-notes.conf root@YOUR_SERVER:/etc/nginx/sites-available/notes
ssh root@YOUR_SERVER "ln -sf /etc/nginx/sites-available/notes /etc/nginx/sites-enabled/ && nginx -s reload"

# 6. 安装 Crontab
ssh root@YOUR_SERVER "crontab scripts/crontab.conf"

# 7. 部署前端
scp -r frontend/apps/* root@YOUR_SERVER:/root/quartz/public/apps/

# 8. 安装浏览器扩展
# Edge/Chrome → 扩展管理 → 开发者模式 → 加载解压缩扩展 → 选 extension/ 目录

# 9. Docker 服务（按需）
# Vikunja: docker run -d -p 8086:3456 vikunja/vikunja
# Memos:   docker run -d -p 8083:5230 neosmemo/memos
```

### 需要修改的硬编码

代码中有一些需要根据你的情况修改的地方：

| 位置 | 内容 | 说明 |
|------|------|------|
| `chat-server.cjs` 第276-307行 | `SCHEDULE` 对象 | 替换为你的课表 |
| `chat-server.cjs` 第357-366行 | 科目优先级列表 | 替换为你的课程 |
| `vikunja-dashboard.py` 第30-39行 | `COURSE_EXAMS` | 替换为你的课程信息 |
| `vikunja-dashboard.py` 第20-24行 | `SUBJECT_MAP` | 替换为你的科目映射 |
| `flashcards.html` | `CARDS` 数组 | 替换为你的闪卡内容 |
| `formulas.html` | 公式数据 | 替换为你的科目公式 |

---

## 文件结构

```
server/
  chat-server.cjs         # Node.js API 服务器（17个端点，~90KB）
  vikunja-dashboard.py     # Python 自动化（12个模式，~100KB）
  news_fetcher.py          # 学术新闻聚合器
  vikunja-reminder.py      # 任务到期 Bark 推送
  config.example.env       # 凭据模板

frontend/apps/
  index.html               # 学习指挥中心（实时数据面板）
  problems.html            # AI 题库（复习模式 + KaTeX 渲染）
  weakness.html            # 弱点分析 + 覆盖度 + 强化计划
  flashcards.html           # 间隔重复闪卡
  planner.html             # AI 每日计划
  chat.html                # RAG 笔记对话
  dashboard.html           # 学习热力图 + GPA 模拟
  formulas.html            # 公式速查
  mistakes.html            # 在线错题本
  focus.html               # 番茄钟计时
  task-agent.html          # 自然语言任务创建

extension/
  manifest.json            # Chrome MV3 扩展
  content.js               # Gemini 页面内容提取
  popup.html/js            # 扩展弹窗

scripts/
  quartz-build.sh          # Quartz 自动构建脚本
  crontab.conf             # 完整 cron 配置
  nginx-notes.conf         # Nginx 站点配置
  chat-server.service      # Systemd 单元文件
```

---

## 踩坑记录

给想复刻这个系统的人一些提醒：

1. **Obsidian Dataview 冲突**: AI 输出的 `[x^2]` 会被 Dataview 当作 inline field 解析报错。所有数学必须在 `$...$` 内，方括号用 `$\left[...\right]$`
2. **Remotely Save 路径嵌套**: 插件会同步到 `/dav/myself/` 路径，Nginx 需要 `^~` 前缀匹配
3. **Service Worker 陷阱**: SW 不能拦截 `chrome-extension://` 和外部 CDN 请求，必须只处理同源
4. **Quartz 构建覆盖**: 每次构建会清空 `public/`，必须在构建脚本中 backup + restore `apps/` 目录
5. **ARM64 Docker**: 不是所有镜像都支持 ARM64，选型时注意（如 Flame 就不支持）
6. **AI 任务重复**: `ai_summary` 和 `spaced_repetition` 都会创建任务，必须用模糊去重（normalize + SequenceMatcher）
7. **时区**: `planner.html` 的日期函数必须用 `getFullYear()/getMonth()/getDate()`（本地时间），不能用 `toISOString()`（UTC）
8. **Memos 不渲染 LaTeX**: 推送到 Memos 的内容（如每日挑战）要用 Unicode 纯文本表示数学

---

## 致谢

这个系统的每一行代码都是和 [Claude](https://claude.ai/) 一起写的。从最初的"我想把 Obsidian 笔记同步到服务器"到现在的 17 个 API + 12 个自动化流程 + 11 个 Web 应用，经历了无数次迭代。

如果你也是在期末前焦虑地寻找学习工具的大学生——希望这个项目能给你一些启发。不过说实话，最有效的学习方法还是坐下来老老实实做题。这个系统只是让"做题之外的一切"变得更自动化。

---

## License

MIT
