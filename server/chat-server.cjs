// FXY 知识库智能助手 - 基于 SJTU 本地大模型 API
// 功能：RAG 检索笔记内容 + 多模型对话
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

const PORT = 3457;
const API_BASE = 'https://models.sjtu.edu.cn/api/v1/chat/completions';
const API_KEY = 'YOUR_AI_API_KEY';
const VAULT_DIR = '/root/webdav-vault';
const CANVAS_BASE = 'https://oc.sjtu.edu.cn/api/v1';
const CANVAS_TOKEN = 'YOUR_CANVAS_TOKEN';
let canvasCache = { data: null, ts: 0 };
const CANVAS_TTL = 15 * 60 * 1000;

function fetchCanvasJSON(apiPath) {
  return new Promise((resolve) => {
    const url = new URL(CANVAS_BASE + apiPath);
    https.get({ hostname: url.hostname, path: url.pathname + url.search, headers: { Authorization: 'Bearer ' + CANVAS_TOKEN } }, (r) => {
      let d = ''; r.on('data', c => d += c);
      r.on('end', () => { try { resolve(JSON.parse(d)); } catch(e) { resolve([]); } });
    }).on('error', () => resolve([]));
  });
}

async function getCanvasData() {
  if (canvasCache.data && Date.now() - canvasCache.ts < CANVAS_TTL) return canvasCache.data;
  try {
    const [todo, upcoming] = await Promise.all([fetchCanvasJSON('/users/self/todo?per_page=50'), fetchCanvasJSON('/users/self/upcoming_events?per_page=30')]);
    const assignments = [], seen = new Set();
    if (Array.isArray(todo)) for (const t of todo) {
      const a = t.assignment || {}, key = a.name || t.title || '';
      if (seen.has(key)) continue; seen.add(key);
      assignments.push({ course: t.context_name || '', name: key, due: a.due_at || '', points: a.points_possible || 0, submitted: false, url: a.html_url || '' });
    }
    if (Array.isArray(upcoming)) for (const e of upcoming) {
      const a = e.assignment || {}, key = e.title || a.name || '';
      if (seen.has(key)) continue; seen.add(key);
      assignments.push({ course: e.context_name || '', name: key, due: a.due_at || e.end_at || '', points: a.points_possible || 0, submitted: true, url: e.html_url || '' });
    }
    assignments.sort((a, b) => { if (!a.due) return 1; if (!b.due) return -1; return new Date(a.due) - new Date(b.due); });
    canvasCache = { data: assignments, ts: Date.now() };
    console.log('Canvas: ' + assignments.length + ' items');
    return assignments;
  } catch (e) { return canvasCache.data || []; }
}

function formatCanvasForPrompt(items) {
  if (!items || !items.length) return '';
  const now = new Date();
  return items.map(a => {
    if (!a.due) return null;
    const due = new Date(a.due), diff = Math.ceil((due - now) / 86400000);
    const ds = (due.getMonth()+1) + '/' + due.getDate();
    let u = diff < 0 ? '已过期!' : diff === 0 ? '今天截止!' : diff === 1 ? '明天截止' : diff + '天后';
    return '- ' + a.course + ' [' + a.name + '] ' + ds + '截止 (' + u + ')' + (a.submitted ? '' : ' 未提交');
  }).filter(Boolean).join('\n');
}


// ========== 笔记索引 ==========
let noteIndex = []; // {path, title, content, keywords}
let lastIndexTime = 0; // track last index build time

function vaultHasChanges() {
  let maxMtime = 0;
  walkDir(VAULT_DIR, (filePath) => {
    if (!filePath.endsWith('.md')) return;
    try {
      const mt = fs.statSync(filePath).mtimeMs;
      if (mt > maxMtime) maxMtime = mt;
    } catch (e) {}
  });
  return maxMtime > lastIndexTime;
}

function indexNotes(force) {
  if (!force && lastIndexTime > 0 && !vaultHasChanges()) return;
  noteIndex = [];
  walkDir(VAULT_DIR, (filePath) => {
    if (!filePath.endsWith('.md')) return;
    const rel = path.relative(VAULT_DIR, filePath);
    if (rel.startsWith('.obsidian') || rel.startsWith('web-apps') || rel.startsWith('myself')) return;
    try {
      const content = fs.readFileSync(filePath, 'utf-8');
      const title = rel.replace(/\.md$/, '');
      noteIndex.push({ path: rel, title, content });
    } catch (e) {}
  });
  lastIndexTime = Date.now();
  console.log(`Indexed ${noteIndex.length} notes`);
}

function walkDir(dir, cb) {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) walkDir(full, cb);
      else cb(full);
    }
  } catch (e) {}
}

// 简单关键词搜索，返回最相关的笔记片段
function searchNotes(query, topK = 8) {
  // 中文分词：将连续中文拆成2字词组 + 保留原始空格分隔的词
  const raw = query.toLowerCase().replace(/[?？！!。，、：:（）()""''·\n]/g, ' ');
  const spaceWords = raw.split(/\s+/).filter(w => w.length >= 2);
  // 中文bigram分词
  const chineseBigrams = [];
  for (const w of spaceWords) {
    if (/[\u4e00-\u9fff]/.test(w) && w.length > 2) {
      for (let i = 0; i < w.length - 1; i++) {
        chineseBigrams.push(w.slice(i, i + 2));
      }
    }
  }
  const keywords = [...new Set([...spaceWords, ...chineseBigrams])].filter(w => w.length >= 2);

  if (keywords.length === 0) return [];

  const scored = noteIndex.map(note => {
    const text = (note.title + ' ' + note.content).toLowerCase();
    let score = 0;
    for (const kw of keywords) {
      const idx = text.indexOf(kw);
      if (idx >= 0) {
        score += 10;
        // title match bonus
        if (note.title.toLowerCase().includes(kw)) score += 20;
        // callout match bonus (> [!warning], > [!tip], > [!important])
        const calloutRegex = new RegExp('> \\[!(warning|tip|important|danger)\\].*' + kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
        if (calloutRegex.test(note.content)) score += 30;
        // 复习精华 section bonus
        if (note.content.includes('## 复习精华') && note.content.split('## 复习精华')[1]?.toLowerCase().includes(kw)) score += 25;
        // count occurrences (max 5)
        const matches = text.split(kw).length - 1;
        score += Math.min(matches, 5) * 2;
      }
    }
    return { ...note, score };
  }).filter(n => n.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);

  // 截取相关片段（每个笔记最多1500字）
  return scored.map(note => {
    let snippet = note.content;
    if (snippet.length > 1500) {
      // 找到第一个关键词出现的位置，截取上下文
      const lower = snippet.toLowerCase();
      let bestIdx = 0;
      for (const kw of keywords) {
        const idx = lower.indexOf(kw);
        if (idx >= 0) { bestIdx = idx; break; }
      }
      const start = Math.max(0, bestIdx - 200);
      snippet = (start > 0 ? '...' : '') + snippet.slice(start, start + 1500) + '...';
    }
    return { title: note.title, snippet };
  });
}

// ========== API 代理 ==========
function callModel(messages, model, stream, res) {
  const body = JSON.stringify({
    model,
    messages,
    stream,
    max_tokens: 4096,
    temperature: 0.7
  });

  const url = new URL(API_BASE);
  const options = {
    hostname: url.hostname,
    path: url.pathname,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${API_KEY}`,
      'Accept': stream ? 'text/event-stream' : 'application/json'
    }
  };

  const req = https.request(options, (apiRes) => {
    if (stream) {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*'
      });
      apiRes.pipe(res);
    } else {
      let data = '';
      apiRes.on('data', chunk => data += chunk);
      apiRes.on('end', () => {
        res.writeHead(apiRes.statusCode, {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*'
        });
        res.end(data);
      });
    }
  });

  req.on('error', (e) => {
    res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: e.message }));
  });

  req.setTimeout(120000);
  req.write(body);
  req.end();
}

// ========== HTTP 服务 ==========
const server = http.createServer((req, res) => {
  // CORS
  if (req.method === 'OPTIONS') {
    res.writeHead(200, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST,GET,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    });
    res.end();
    return;
  }

  // 聊天 API（支持 stream=true/false）
  if (req.method === 'POST' && req.url === '/api/chat') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        const { message, model = 'deepseek-chat', history = [], useRAG = true, stream = true } = JSON.parse(body);

        let ragResults = [];
        if (useRAG) {
          ragResults = searchNotes(message);
        }

        let systemPrompt = `你是 FXY 的私人知识库AI助手。你已经被集成到 FXY 的 Obsidian 笔记系统中，你可以直接检索和读取 FXY 的所有笔记文件。

重要：你确实能够读取 FXY 的笔记！系统已经自动搜索了相关笔记内容并提供给你。不要说你无法访问笔记——你已经拿到了。

当前日期：${new Date().toISOString().split('T')[0]}
期末考试日期：2026-06-22（约第17周）
FXY 是上海交通大学大一学生，生物信息方向，正在准备期末考试。
笔记库共有 ${noteIndex.length} 篇文档。`;

        // Inject weakness context based on detected subject
        const SUBJ_DETECT = {'高数':'高数II','有机':'有机化学','概统':'概率统计','大物':'大学物理',
          '分化':'分析化学','分析化学':'分析化学','普生':'普通生物学','物理':'大学物理',
          '数学':'高数II','化学':'有机化学','生物':'普通生物学','积分':'高数II',
          '微分':'高数II','概率':'概率统计','统计':'概率统计','高数':'高数II'};
        let detectedSubject = '';
        const msgLower = message.toLowerCase();
        for (const [kw, subj] of Object.entries(SUBJ_DETECT)) {
          if (msgLower.includes(kw)) { detectedSubject = subj; break; }
        }
        if (detectedSubject) {
          try {
            let weakCtx = '';
            // Read weakness tracker
            const wkFile = '/root/.weakness-tracker.json';
            if (fs.existsSync(wkFile)) {
              const wk = JSON.parse(fs.readFileSync(wkFile, 'utf-8'));
              const subjWk = wk.filter(w => (w.status === 'active' || !w.status) && (w.text.includes(detectedSubject)));
              if (subjWk.length > 0) {
                weakCtx += `\n\n【FXY在${detectedSubject}的已知薄弱点】\n`;
                subjWk.slice(0, 5).forEach(w => { weakCtx += `- ${w.text}\n`; });
              }
            }
            // Read spaced repetition
            const spFile = '/root/.spaced-repetition.json';
            if (fs.existsSync(spFile)) {
              const sp = JSON.parse(fs.readFileSync(spFile, 'utf-8'));
              const subjSp = sp.filter(it => it.status === 'active' && it.text.includes(detectedSubject));
              if (subjSp.length > 0) {
                weakCtx += `\n【${detectedSubject}的间隔重复待复习项】\n`;
                subjSp.slice(0, 5).forEach(it => { weakCtx += `- ${it.text}（间隔${it.interval}天）\n`; });
              }
            }
            if (weakCtx) {
              systemPrompt += weakCtx;
              systemPrompt += `\n请在回答时优先关注这些薄弱点，帮助 FXY 巩固。`;
            }
          } catch(e) {}
        }

        if (ragResults.length > 0) {
          systemPrompt += `\n\n【系统已检索到 ${ragResults.length} 篇相关笔记，内容如下】\n`;
          ragResults.forEach(r => {
            systemPrompt += `\n=== 📄 ${r.title} ===\n${r.snippet}\n`;
          });
          systemPrompt += `\n请优先基于以上笔记内容来回答 FXY 的问题。引用笔记中的具体内容时请标注来源文件名。`;
        } else if (useRAG) {
          systemPrompt += `\n\n（未找到与本次提问直接相关的笔记，请基于你的知识回答）`;
        }

        const messages = [
          { role: 'system', content: systemPrompt },
          ...history.slice(-10),
          { role: 'user', content: message }
        ];

        callModel(messages, model, stream, res);
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // ========== 课表数据 ==========
  const SCHEDULE = {
    '周一': [
      {time:'08:00-09:40',course:'高等数学II',loc:'理院114'},
      {time:'10:00-11:40',course:'大学物理B',loc:'物理院1-106'},
    ],
    '周二': [
      {time:'08:00-09:40',course:'概率统计',loc:'理院114'},
      {time:'10:00-11:40',course:'高等数学II',loc:'理院114'},
      {time:'12:00-15:40',course:'有机化学实验',loc:'化学楼A'},
    ],
    '周三': [
      {time:'08:00-09:40',course:'英汉口译',loc:'外语院4-408'},
      {time:'12:55-15:40',course:'习近平新时代思想',loc:'理院313'},
      {time:'16:00-17:40',course:'网球',loc:'体育场'},
    ],
    '周四': [
      {time:'08:00-09:40',course:'大学物理B',loc:'物理院1-106'},
      {time:'10:00-11:40',course:'概率统计',loc:'理院114'},
      {time:'12:00-15:40',course:'ET创新实验室',loc:'校外'},
      {time:'16:00-17:40',course:'有机化学',loc:'理院203'},
    ],
    '周五': [
      {time:'08:00-09:40',course:'分析化学',loc:'理院111'},
      {time:'16:00-17:40',course:'军事理论',loc:'理院103'},
      {time:'18:00-19:40',course:'人工智能基础A',loc:'物理院102'},
    ],
    '周六': [
      {time:'10:00-11:40',course:'新时代社会认知实践',loc:''},
      {time:'18:00-19:40',course:'大学物理实验',loc:'校外'},
    ],
    '周日': [],
  };

  // 每日计划生成 API — 生成 Obsidian 每日笔记格式
  if (req.method === 'POST' && req.url === '/api/plan') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        const { date, feedback, existingNote, model = 'deepseek-chat' } = JSON.parse(body);

        const today = date || new Date().toISOString().split('T')[0];
        const examDate = new Date('2026-06-22');
        const now = new Date(today + 'T12:00:00');
        const daysLeft = Math.ceil((examDate - now) / 86400000);
        const wdIdx = now.getDay();
        const weekday = ['周日','周一','周二','周三','周四','周五','周六'][wdIdx];
        const weekdayCN = ['星期日','星期一','星期二','星期三','星期四','星期五','星期六'][wdIdx];
        const todayCourses = SCHEDULE[weekday] || [];

        // 搜索复习进度
        const progressNotes = searchNotes('复习进度 冲刺 本周 DDL 作业', 3);
        let context = '';
        progressNotes.forEach(r => { context += r.snippet.slice(0, 300) + '\n'; });

        let userMsg = '';
        if (feedback && existingNote) {
          userMsg = `我已经有一份今天（${today} ${weekdayCN}）的每日笔记，请根据我的反馈在原有内容的基础上进行修改。

【重要】请保留原笔记的整体结构和大部分内容，只根据反馈调整需要改的部分。不要完全重写，要像编辑一样局部修改。已经勾选完成的任务（[x]）必须保留。

我的反馈：${feedback}

当前笔记内容：
${existingNote}

请输出修改后的完整笔记（保持相同的 Markdown 格式）。`;
        } else {
          userMsg = `请为 ${today}（${weekdayCN}）生成完整的每日笔记。距期末还有 ${daysLeft} 天。`;
        }

        const courseList = todayCourses.map(c => `${c.time} ${c.course}（${c.loc}）`).join('\n');
        const canvasItems = await getCanvasData();
        const canvasInfo = formatCanvasForPrompt(canvasItems);

        // Inject study log data (yesterday's learning time per subject)
        let studyLogInfo = '';
        try {
          const studyLogFile = '/root/.study-log.json';
          if (fs.existsSync(studyLogFile)) {
            const logs = JSON.parse(fs.readFileSync(studyLogFile, 'utf-8'));
            const yesterday = new Date(now - 86400000);
            const yStr = yesterday.getFullYear() + '-' + String(yesterday.getMonth()+1).padStart(2,'0') + '-' + String(yesterday.getDate()).padStart(2,'0');
            const yLogs = logs.filter(e => e.date === yStr);
            if (yLogs.length > 0) {
              const bySubj = {};
              yLogs.forEach(e => { bySubj[e.subject] = (bySubj[e.subject] || 0) + e.duration; });
              const total = Object.values(bySubj).reduce((a,b) => a+b, 0);
              studyLogInfo = `## 昨日实际学习时长（共${total}分钟）\n`;
              Object.entries(bySubj).sort((a,b) => b[1]-a[1]).forEach(([s,m]) => {
                studyLogInfo += `- ${s}: ${m}分钟\n`;
              });
              studyLogInfo += '\n';
            }
          }
        } catch(e) {}

        // Inject weakness data
        let weaknessInfo = '';
        try {
          const wkFile = '/root/.weakness-tracker.json';
          if (fs.existsSync(wkFile)) {
            const wk = JSON.parse(fs.readFileSync(wkFile, 'utf-8'));
            const active = wk.filter(w => w.status === 'active' || !w.status);
            if (active.length > 0) {
              weaknessInfo = '## 当前薄弱知识点\n';
              active.slice(0, 8).forEach(w => {
                weaknessInfo += `- ${w.text}（发现于${w.found_date || w.found || '?'}）\n`;
              });
              weaknessInfo += '\n请在计划中优先安排这些薄弱点的复习。\n\n';
            }
          }
        } catch(e) {}

        // Inject spaced repetition items due today
        let spacedInfo = '';
        try {
          const spFile = '/root/.spaced-repetition.json';
          if (fs.existsSync(spFile)) {
            const items = JSON.parse(fs.readFileSync(spFile, 'utf-8'));
            const due = items.filter(it => it.status === 'active' && it.next_review <= today);
            if (due.length > 0) {
              spacedInfo = '## 今日待复习（间隔重复）\n';
              due.slice(0, 5).forEach(it => {
                spacedInfo += `- ${it.text}（第${it.interval}天复习）\n`;
              });
              spacedInfo += '\n';
            }
          }
        } catch(e) {}

        const systemPrompt = `你是 FXY 的AI学习规划师。请根据课表和学习情况，生成 Obsidian 每日笔记。

## 今日课表（${today} ${weekdayCN}）
${courseList || '今天没有课（可以全天自习）'}

## 科目优先级与目标
1. 高等数学II（4学分，期中70分，目标期末85+）— 最高优先，翻盘关键
2. 有机化学（4学分，目标85-90）— 高优先
3. 概率统计（3学分，目标80-85）— 高优先
4. 大学物理（3学分，目标85-90）— 中优先
5. 习近平新时代思想（3学分，目标80-85）— 中优先
6. 分析化学（2学分，目标75+）— 低优先
7. 普通生物学（2学分）— 考前背诵
8. 人工智能基础（2学分）— 低优先
9. 军事理论（2学分）— 考前1-2天突击

${canvasInfo ? "## Canvas 待办作业（自动同步）\n" + canvasInfo + "\n\n" : ""}## 固定习惯
- 晚自习开头：高数计算练习 30min
- 睡前：概率统计/分析化学公式速览 15min
- 课间碎片：翻有机化学反应卡片
- 每天完成闪卡复习（Ctrl+P → Review）
- 周三/周日有网管值班（4小时，可背书）

${studyLogInfo}${weaknessInfo}${spacedInfo}${context ? '## 最近复习进度\n' + context : ''}

## 输出要求
请严格按以下 Markdown 格式输出，不要输出任何其他内容（不要代码块标记）：

---
date: ${today}
week: ${Math.ceil((now - new Date('2026-02-16')) / 604800000)}
day: ${weekdayCN}
tags: [日记]
---

# ${today} ${weekdayCN}

## 今日目标（最多3个）
- [ ] （根据课表、DDL紧急程度和优先级，设定具体目标，例如"完成高数第10章Green公式习题"）
- [ ]
- [ ]

## 建议时间安排

（根据今天有没有课，给出粗略的时间分配建议。只列科目和大致时间段，不要填学习内容。例如：
- 上午：高数 2h
- 下午：有机 2h + 大物作业 1h
- 晚自习：概统 1.5h）

## 作业/DDL（Canvas 自动同步）
（根据 Canvas 待办数据，列出所有未完成作业及截止日期，标注紧急程度，已过期的标红）

## 学习记录

| 时间段 | 科目 | 内容 | 时长 | 效果(1-5) |
|--------|------|------|------|-----------|
| | | | | |

**今日总学习时长**：h

## 闪卡复习
- [ ] 完成今日闪卡复习（Ctrl+P → Review）

## 今日收获
>

## 明日预告
（简要提示明天有什么课、有没有即将到期的DDL）

---
[[HOME|返回主页]] | [[规划/6周冲刺计划|冲刺计划]]

注意：
- 今日目标要非常具体（具体到章节、题目数量），不要写笼统的"复习高数"
- 学习记录表留空！这是用户自己填的，绝对不要替用户预填任何学习内容
- 建议时间安排只给大方向，不要细到每小时
- 根据DDL紧急程度调整优先级，快过期的要优先处理
- 周三/周日考虑网管值班4小时（可背普生/军理）`;

        const messages = [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userMsg }
        ];

        callModel(messages, model, false, res);
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // 保存每日笔记到 WebDAV 目录
  if (req.method === 'POST' && req.url === '/api/save-note') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const { date, content } = JSON.parse(body);
        const dir = path.join(VAULT_DIR, '每日笔记', date);
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
        const filePath = path.join(dir, '每日笔记.md');
        fs.writeFileSync(filePath, content, 'utf-8');
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ ok: true, path: filePath }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // 读取每日笔记
  if (req.method === 'POST' && req.url === '/api/read-note') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const { date } = JSON.parse(body);
        const filePath = path.join(VAULT_DIR, '每日笔记', date, '每日笔记.md');
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf-8');
          res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ exists: true, content }));
        } else {
          res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ exists: false }));
        }
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // 搜索笔记 API
  if (req.method === 'POST' && req.url === '/api/search') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      const { query } = JSON.parse(body);
      const results = searchNotes(query, 10);
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ results }));
    });
    return;
  }

  // 重建索引
  if (req.method === 'POST' && req.url === '/api/reindex') {
    indexNotes(true);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, count: noteIndex.length }));
    return;
  }

  // Canvas assignments API
  if (req.method === 'GET' && req.url === '/api/canvas') {
    getCanvasData().then(data => {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ assignments: data }));
    });
    return;
  }


  // ========== 任务智能体 API ==========
  const VIKUNJA_API = 'http://localhost:8086/api/v1';
  const VIKUNJA_TOKEN = 'YOUR_VIKUNJA_TOKEN';
  const VIKUNJA_PROJECTS = {
    2: '\u671f\u672b\u590d\u4e60',
    3: '\u4f5c\u4e1a & DDL',
    4: '\u751f\u6d3b & \u65e5\u5e38',
    5: '\u6280\u80fd\u63d0\u5347',
    6: '\u957f\u671f\u76ee\u6807'
  };

  function vikunjaRequest(method, apiPath, data) {
    return new Promise((resolve, reject) => {
      const url = new URL(VIKUNJA_API + apiPath);
      const body = data ? JSON.stringify(data) : null;
      const opts = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method,
        headers: {
          'Authorization': 'Bearer ' + VIKUNJA_TOKEN,
          'Content-Type': 'application/json'
        }
      };
      const r = http.request(opts, (resp) => {
        let d = '';
        resp.on('data', c => d += c);
        resp.on('end', () => {
          try { resolve(JSON.parse(d)); } catch(e) { resolve(d); }
        });
      });
      r.on('error', reject);
      if (body) r.write(body);
      r.end();
    });
  }

  function callModelJSON(messages, model) {
    return new Promise((resolve, reject) => {
      const body = JSON.stringify({
        model: model || 'deepseek-v3.2',
        messages,
        stream: false,
        max_tokens: 2048,
        temperature: 0.3
      });
      const url = new URL(API_BASE);
      const opts = {
        hostname: url.hostname,
        path: url.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + API_KEY
        }
      };
      const r = https.request(opts, (resp) => {
        let d = '';
        resp.on('data', c => d += c);
        resp.on('end', () => {
          try {
            const parsed = JSON.parse(d);
            const content = parsed.choices && parsed.choices[0] && parsed.choices[0].message ? parsed.choices[0].message.content : '';
            resolve(content);
          } catch(e) { reject(e); }
        });
      });
      r.on('error', reject);
      r.setTimeout(60000);
      r.write(body);
      r.end();
    });
  }

  if (req.method === 'POST' && req.url === '/api/task-agent') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { message, history } = JSON.parse(body);
        if (!message) {
          res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ error: 'message required' }));
          return;
        }

        const today = new Date();
        const todayStr = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');
        const dayNames = ['\u65e5','\u4e00','\u4e8c','\u4e09','\u56db','\u4e94','\u516d'];
        const todayDay = '\u5468' + dayNames[today.getDay()];

        const projectList = Object.entries(VIKUNJA_PROJECTS).map(([id, name]) => '  ' + id + ': ' + name).join('\n');

        const systemPrompt = '\u4f60\u662f FXY \u7684\u4efb\u52a1\u7ba1\u7406\u667a\u80fd\u52a9\u624b\u3002\u4f60\u7684\u5de5\u4f5c\u662f\u7406\u89e3\u7528\u6237\u7684\u81ea\u7136\u8bed\u8a00\u8f93\u5165\uff0c\u5c06\u5176\u8f6c\u5316\u4e3a\u7ed3\u6784\u5316\u7684\u4efb\u52a1\uff0c\u7136\u540e\u521b\u5efa\u5230 Vikunja \u4efb\u52a1\u7ba1\u7406\u7cfb\u7edf\u4e2d\u3002\n\n\u5f53\u524d\u65e5\u671f\uff1a' + todayStr + '\uff08' + todayDay + '\uff09\n\u7528\u6237\u80cc\u666f\uff1a\u4e0a\u6d77\u4ea4\u901a\u5927\u5b66\u5927\u4e00\uff0c\u751f\u547d\u79d1\u5b66\u6280\u672f\u5b66\u9662\uff0c8\u95e8\u8bfe\u7a0b\u671f\u672b\u590d\u4e60\u4e2d\uff0c\u671f\u672b\u8003\u8bd5 2026-06-22 \u5f00\u59cb\u3002\n\n\u53ef\u7528\u9879\u76ee\u6a21\u5757\uff1a\n' + projectList + '\n\n\u4f60\u5fc5\u987b\u4e25\u683c\u6309\u4ee5\u4e0b JSON \u683c\u5f0f\u56de\u590d\uff0c\u4e0d\u8981\u8f93\u51fa\u4efb\u4f55\u5176\u4ed6\u5185\u5bb9\uff1a\n{\n  "thinking": "\u4f60\u7684\u5206\u6790\u8fc7\u7a0b\uff08\u7b80\u77ed\uff09",\n  "tasks": [\n    {\n      "project_id": \u6570\u5b57,\n      "title": "\u4efb\u52a1\u6807\u9898\uff08\u7b80\u6d01\uff09",\n      "description": "\u4efb\u52a1\u63cf\u8ff0/\u5907\u6ce8",\n      "priority": 1-5,\n      "due_date": "YYYY-MM-DDTHH:mm:00+08:00 \u6216 null"\n    }\n  ],\n  "reply": "\u7528\u81ea\u7136\u8bed\u8a00\u56de\u590d\u7528\u6237\uff0c\u786e\u8ba4\u5c06\u8981\u521b\u5efa\u7684\u4efb\u52a1"\n}\n\n\u89c4\u5219\uff1a\n1. \u5982\u679c\u7528\u6237\u63d0\u5230\u201c\u660e\u5929\u201d\uff0c\u57fa\u4e8e\u5f53\u524d\u65e5\u671f\u8ba1\u7b97\n2. \u5982\u679c\u7528\u6237\u63d0\u5230\u201c\u4e0b\u5468X\u201d\uff0c\u8ba1\u7b97\u5230\u4e0b\u4e00\u4e2a\u5468X\u7684\u65e5\u671f\n3. \u5982\u679c\u7528\u6237\u6ca1\u8bf4\u622a\u6b62\u65e5\u671f\uff0c\u6839\u636e\u4efb\u52a1\u6027\u8d28\u5408\u7406\u63a8\u65ad\uff0c\u6216\u8bbe\u4e3a null\n4. \u4f18\u5148\u7ea7\uff1a\u7d27\u6025\u91cd\u8981=5\uff0c\u91cd\u8981=4\uff0c\u4e00\u822c=3\uff0c\u4e0d\u6025=2\uff0c\u968f\u4fbf=1\n5. \u4e00\u53e5\u8bdd\u53ef\u80fd\u5305\u542b\u591a\u4e2a\u4efb\u52a1\uff0c\u8981\u62c6\u5206\n6. \u6839\u636e\u4efb\u52a1\u5185\u5bb9\u81ea\u52a8\u9009\u62e9\u6700\u5408\u9002\u7684\u9879\u76ee\u6a21\u5757\n7. reply \u5b57\u6bb5\u8981\u53cb\u597d\u7b80\u6d01\uff0c\u544a\u8bc9\u7528\u6237\u4f60\u521b\u5efa\u4e86\u4ec0\u4e48\n8. \u53ea\u8f93\u51fa JSON\uff0c\u4e0d\u8981\u8f93\u51fa markdown \u4ee3\u7801\u5757\u6807\u8bb0';

        const msgs = [{ role: 'system', content: systemPrompt }];
        if (history && Array.isArray(history)) {
          for (const h of history.slice(-6)) {
            msgs.push({ role: h.role, content: h.content });
          }
        }
        msgs.push({ role: 'user', content: message });

        const raw = await callModelJSON(msgs, 'deepseek-v3.2');

        // Clean output
        let cleaned = raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
        cleaned = cleaned.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();

        let parsed;
        try {
          parsed = JSON.parse(cleaned);
        } catch(e) {
          const m = cleaned.match(/\{[\s\S]*\}/);
          if (m) parsed = JSON.parse(m[0]);
          else throw new Error('AI parse error: ' + cleaned.slice(0, 200));
        }

        const results = [];
        if (parsed.tasks && Array.isArray(parsed.tasks)) {
          for (const t of parsed.tasks) {
            const taskData = {
              title: t.title,
              description: t.description || '',
              priority: Math.min(5, Math.max(1, t.priority || 3))
            };
            if (t.due_date) taskData.due_date = t.due_date;

            const pid = t.project_id || 4;
            const created = await vikunjaRequest('PUT', '/projects/' + pid + '/tasks', taskData);
            results.push({
              id: created.id,
              title: t.title,
              project: VIKUNJA_PROJECTS[pid] || 'unknown',
              priority: t.priority,
              due_date: t.due_date
            });
          }
        }

        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({
          reply: parsed.reply || '\u4efb\u52a1\u5df2\u521b\u5efa',
          tasks: results,
          thinking: parsed.thinking || ''
        }));

      } catch(e) {
        console.error('task-agent error:', e);
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // Task projects overview
  if (req.method === 'GET' && req.url === '/api/task-projects') {
    (async () => {
      try {
        const projects = await Promise.all(
          Object.entries(VIKUNJA_PROJECTS).map(async ([id, name]) => {
            const tasks = await vikunjaRequest('GET', '/projects/' + id + '/tasks', null);
            const pending = Array.isArray(tasks) ? tasks.filter(t => !t.done).length : 0;
            const total = Array.isArray(tasks) ? tasks.length : 0;
            return { id: Number(id), name, pending, total };
          })
        );
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ projects }));
      } catch(e) {
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    })();
    return;
  }


  // Smart Note - save Gemini conversations to Obsidian
  if (req.method === 'POST' && req.url === '/api/smart-note') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const data = JSON.parse(body);
        const { title, subject, messages, images, tags, source, url } = data;

        if (!messages || messages.length === 0) {
          res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ error: 'No messages provided' }));
          return;
        }

        // Build conversation text for AI processing
        let convText = '';
        messages.forEach(m => {
          const prefix = m.role === 'user' ? '**\u6211\u7684\u63d0\u95ee:** ' : '**Gemini:** ';
          convText += prefix + m.content + '\n\n';
        });

        // Determine subject
        let finalSubject = subject;
        if (!subject || subject === 'auto') {
          const detectPrompt = [
            { role: 'system', content: '\u4f60\u662f\u4e00\u4e2a\u5b66\u79d1\u5206\u7c7b\u5668\u3002\u6839\u636e\u5bf9\u8bdd\u5185\u5bb9\uff0c\u8fd4\u56de\u6700\u5339\u914d\u7684\u5b66\u79d1\u540d\u79f0\u3002\u53ea\u8fd4\u56de\u4ee5\u4e0b\u4e4b\u4e00\uff1a\u9ad8\u6570II\u3001\u6709\u673a\u5316\u5b66\u3001\u6982\u7387\u7edf\u8ba1\u3001\u5927\u5b66\u7269\u7406\u3001\u5206\u6790\u5316\u5b66\u3001\u666e\u901a\u751f\u7269\u5b66\u3001AI\u57fa\u7840\u3001\u4e60\u601d\u60f3\u3001\u5176\u4ed6\u3002\u53ea\u8fd4\u56de\u5b66\u79d1\u540d\uff0c\u4e0d\u8981\u5176\u4ed6\u5185\u5bb9\u3002' },
            { role: 'user', content: convText.slice(0, 2000) }
          ];
          try {
            finalSubject = (await callModelJSON(detectPrompt, 'deepseek-v3.2')).trim().replace(/["\u3001\u3002]/g, '');
          } catch(e) {
            finalSubject = '\u5176\u4ed6';
          }
        }

        // Ask AI to generate title and organize
        const organizePrompt = [
          { role: 'system', content: '\u4f60\u662f\u7b14\u8bb0\u6574\u7406\u52a9\u624b\u3002\u6839\u636e\u4ee5\u4e0b\u5bf9\u8bdd\u5185\u5bb9\uff0c\u8fd4\u56de JSON\uff1a{"title": "\u7b80\u77ed\u6807\u9898(\u4e2d\u6587,15\u5b57\u5185)", "summary": "\u4e00\u53e5\u8bdd\u6458\u8981", "key_points": ["\u8981\u70b91", "\u8981\u70b92"]}\u3002\u53ea\u8fd4\u56de JSON\uff0c\u4e0d\u8981\u5176\u4ed6\u5185\u5bb9\u3002' },
          { role: 'user', content: convText.slice(0, 3000) }
        ];
        let noteTitle = title || '';
        let summary = '';
        let keyPoints = [];
        try {
          const organized = await callModelJSON(organizePrompt, 'deepseek-v3.2');
          const parsed = JSON.parse(organized.replace(/```json\n?/g, '').replace(/```/g, '').trim());
          if (!noteTitle) noteTitle = parsed.title || 'Gemini Note';
          summary = parsed.summary || '';
          keyPoints = parsed.key_points || [];
        } catch(e) {
          if (!noteTitle) noteTitle = 'Gemini Note';
        }

        // Create directory
        const subjectDir = path.join(VAULT_DIR, 'AI\u7b14\u8bb0', finalSubject);
        if (!fs.existsSync(subjectDir)) {
          fs.mkdirSync(subjectDir, { recursive: true });
        }

        // Save images
        const imgDir = path.join(subjectDir, 'attachments');
        if (images && images.length > 0) {
          if (!fs.existsSync(imgDir)) fs.mkdirSync(imgDir, { recursive: true });
        }
        const imgMap = {};
        if (images) {
          for (const img of images) {
            const match = img.data.match(/^data:image\/(\w+);base64,(.+)$/);
            if (match) {
              const ext = match[1] === 'jpeg' ? 'jpg' : match[1];
              const now = new Date();
              const ts = now.getFullYear() + String(now.getMonth()+1).padStart(2,'0') + String(now.getDate()).padStart(2,'0') + '_' + String(now.getHours()).padStart(2,'0') + String(now.getMinutes()).padStart(2,'0') + String(now.getSeconds()).padStart(2,'0');
              const fname = `${img.id}_${ts}.${ext}`;
              const fpath = path.join(imgDir, fname);
              fs.writeFileSync(fpath, Buffer.from(match[2], 'base64'));
              imgMap[img.id] = `attachments/${fname}`;
            }
          }
        }

        // Build markdown
        const now = new Date();
        const dateStr = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0');
        const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');
        const allTags = ['AI\u7b14\u8bb0', finalSubject, ...(tags || [])];

        let md = '---\n';
        md += `source: ${source || 'gemini'}\n`;
        md += `subject: ${finalSubject}\n`;
        md += `created: ${dateStr} ${timeStr}\n`;
        md += `tags: [${allTags.join(', ')}]\n`;
        if (summary) md += `summary: "${summary}"\n`;
        md += '---\n\n';
        md += `# ${noteTitle}\n\n`;

        if (keyPoints.length > 0) {
          md += '## \u8981\u70b9\n\n';
          keyPoints.forEach(p => { md += `- ${p}\n`; });
          md += '\n';
        }

        md += '## \u5bf9\u8bdd\u8bb0\u5f55\n\n';
        messages.forEach(m => {
          if (m.role === 'user') {
            md += '### \ud83d\udcac \u63d0\u95ee\n\n';
            let content = m.content;
            if (m.images) {
              m.images.forEach(imgId => {
                if (imgMap[imgId]) {
                  content += `\n\n![[${imgMap[imgId]}]]`;
                }
              });
            }
            md += content + '\n\n';
          } else {
            md += '### \ud83e\udd16 Gemini\n\n';
            md += m.content + '\n\n';
          }
        });

        if (url) md += `---\n*Source: [Gemini](${url})*\n`;

        // Write file
        const safeTitle = noteTitle.replace(/[\\/:*?"<>|]/g, '_').slice(0, 50);
        const fileName = `${dateStr}_${safeTitle}.md`;
        const filePath = path.join(subjectDir, fileName);
        fs.writeFileSync(filePath, md, "utf-8");
        // Fix permissions for WebDAV (nginx runs as www-data)
        const { exec } = require("child_process");
        exec("chown -R www-data:www-data " + JSON.stringify(path.join(VAULT_DIR, "AI笔记")), () => {});

        console.log(`Smart note saved: ${filePath}`);

        // Auto-generate flashcards (improved: with dedup + course context + difficulty)
        (async () => {
          try {
            const FLASHCARD_FILE = '/root/.flashcards-server.json';
            const SUBJECT_MAP_FC = {
              '高数II':'高数','有机化学':'有机','概率统计':'概统',
              '大学物理':'大物','分析化学':'分析化学','普通生物学':'普生',
              '习思想':'习思想','AI基础':'AI基础','其他':'其他'
            };
            const fcSubject = SUBJECT_MAP_FC[finalSubject] || finalSubject;

            // Read existing flashcards for dedup
            let existing = [];
            try { existing = JSON.parse(fs.readFileSync(FLASHCARD_FILE, 'utf-8')); } catch(e) {}
            const existingQs = existing.filter(c => c.s === fcSubject).map(c => c.q).slice(-50);
            const existingSample = existingQs.length > 0
              ? '\n已有闪卡（避免重复）:\n' + existingQs.map(q => '- ' + q).join('\n')
              : '';

            // Read course notes for context
            let courseCtx = '';
            const courseFolder = { '高数II':'高数II','有机化学':'有机化学','概率统计':'概率统计','大学物理':'大学物理','分析化学':'分析化学','普通生物学':'普通生物学','AI基础':'人工智能基础','习思想':'习思想' };
            const cDir = path.join(VAULT_DIR, '课程', courseFolder[finalSubject] || finalSubject);
            if (fs.existsSync(cDir)) {
              for (const cf of ['期末复习.md', '公式速查表.md']) {
                const cfp = path.join(cDir, cf);
                if (fs.existsSync(cfp)) {
                  courseCtx += '\n课程笔记参考(' + cf + '):\n' + fs.readFileSync(cfp, 'utf-8').slice(0, 800) + '\n';
                  break;
                }
              }
            }

            const fcPrompt = [
              { role: 'system', content: `你是一个高质量闪卡生成器。从以下学习笔记中提取4-6个重要知识点，生成问答闪卡。
${existingSample}
${courseCtx}
要求：
1. 不要与已有闪卡重复
2. 问题要具体、可考（不要泛泛之问如"什么是XXX"）
3. 答案保留关键公式（LaTeX格式）
4. 至少包含1道易错点辨析题
5. 标注难度

返回JSON数组: [{"q":"问题","a":"答案","difficulty":"easy|medium|hard"}]
只返回JSON数组。` },
              { role: 'user', content: convText.slice(0, 4000) }
            ];
            const fcRaw = await callModelJSON(fcPrompt, 'deepseek-v3.2');
            let fcCleaned = fcRaw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
            let fcCards;
            try { fcCards = JSON.parse(fcCleaned); }
            catch(e) { const m = fcCleaned.match(/\[.*\]/s); if(m) fcCards = JSON.parse(m[0]); else throw e; }
            if (Array.isArray(fcCards) && fcCards.length > 0) {
              const newCards = fcCards.map((c, i) => ({
                id: Date.now() + '_' + i,
                s: fcSubject,
                q: c.q,
                a: c.a,
                difficulty: c.difficulty || 'medium',
                source: 'AI笔记/' + finalSubject + '/' + fileName,
                created: new Date().toISOString(),
                synced: false
              }));
              existing.push(...newCards);
              fs.writeFileSync(FLASHCARD_FILE, JSON.stringify(existing, null, 2));
              console.log('Generated ' + newCards.length + ' flashcards from ' + fileName);
            }
          } catch(e) { console.error('Flashcard gen error:', e.message); }
        })();

        // Auto-generate review essence + repeat weakness detection
        (async () => {
          try {
            // --- Review Essence ---
            const courseFolder = { '高数II':'高数II','有机化学':'有机化学','概率统计':'概率统计','大学物理':'大学物理','分析化学':'分析化学','普通生物学':'普通生物学','AI基础':'人工智能基础','习思想':'习思想' };
            let courseCtx = '';
            const cDir = path.join(VAULT_DIR, '课程', courseFolder[finalSubject] || finalSubject);
            if (fs.existsSync(cDir)) {
              for (const cf of ['期末复习.md', '公式速查表.md', '反应总结.md']) {
                const cfp = path.join(cDir, cf);
                if (fs.existsSync(cfp)) {
                  courseCtx += fs.readFileSync(cfp, 'utf-8').slice(0, 1000) + '\n';
                }
              }
            }

            const essencePrompt = [
              { role: 'system', content: `你是复习精华提取器。从以下学习对话中提取期末复习要点。
${courseCtx ? '【该科目课程笔记参考】\n' + courseCtx : ''}

返回JSON:
{
  "core_formulas": ["公式1（含LaTeX $...$）", "公式2"],
  "common_mistakes": ["易错点1", "易错点2"],
  "exam_patterns": ["常见考法/题型1", "常见考法2"],
  "one_liner": "一句话核心总结"
}
只返回JSON。` },
              { role: 'user', content: convText.slice(0, 4000) }
            ];
            const essRaw = await callModelJSON(essencePrompt, 'deepseek-v3.2');
            let essCleaned = essRaw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
            let essence;
            try { essence = JSON.parse(essCleaned); }
            catch(e) { const m = essCleaned.match(/\{[\s\S]*\}/); if(m) essence = JSON.parse(m[0]); else throw e; }

            // Append 复习精华 section to the saved note
            let essenceMd = '\n\n## 复习精华\n\n';
            essenceMd += `> ${essence.one_liner || ''}\n\n`;
            if (essence.core_formulas && essence.core_formulas.length > 0) {
              essenceMd += '### 核心公式\n';
              essence.core_formulas.forEach(f => { essenceMd += `- ${f}\n`; });
              essenceMd += '\n';
            }
            if (essence.common_mistakes && essence.common_mistakes.length > 0) {
              essenceMd += '### 易错点\n';
              essence.common_mistakes.forEach(f => { essenceMd += `- ⚠️ ${f}\n`; });
              essenceMd += '\n';
            }
            if (essence.exam_patterns && essence.exam_patterns.length > 0) {
              essenceMd += '### 常见考法\n';
              essence.exam_patterns.forEach(f => { essenceMd += `- 🎯 ${f}\n`; });
              essenceMd += '\n';
            }

            fs.appendFileSync(filePath, essenceMd, 'utf-8');
            console.log('Review essence appended to ' + fileName);

            // --- Repeat Weakness Detection ---
            // Scan same subject's notes from last 14 days
            const recentSummaries = [];
            if (fs.existsSync(subjectDir)) {
              const files = fs.readdirSync(subjectDir).filter(f => f.endsWith('.md') && f !== fileName);
              const twoWeeksAgo = new Date(Date.now() - 14 * 86400000);
              const cutoff = twoWeeksAgo.getFullYear() + '-' + String(twoWeeksAgo.getMonth()+1).padStart(2,'0') + '-' + String(twoWeeksAgo.getDate()).padStart(2,'0');
              for (const f of files) {
                if (f.slice(0, 10) >= cutoff) {
                  try {
                    const fc = fs.readFileSync(path.join(subjectDir, f), 'utf-8').slice(0, 500);
                    const sm = fc.match(/summary:\s*"(.+?)"/);
                    if (sm) recentSummaries.push({ file: f.replace('.md',''), summary: sm[1] });
                  } catch(e) {}
                }
              }
            }

            if (recentSummaries.length > 0 && summary) {
              const overlapPrompt = [
                { role: 'system', content: `判断最新笔记摘要与近期笔记是否有主题重叠。
如果重叠，返回: {"overlap":true,"with":["重叠笔记标题"],"warning":"你近两周第N次问关于XXX的问题，这可能是反复弱点"}
如果无重叠: {"overlap":false}
只返回JSON。` },
                { role: 'user', content: `最新摘要: ${summary}\n\n近期笔记:\n${recentSummaries.map(s => `- ${s.file}: ${s.summary}`).join('\n')}` }
              ];
              const ovRaw = await callModelJSON(overlapPrompt, 'deepseek-v3.2');
              let ovCleaned = ovRaw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
              try {
                let overlap;
                try { overlap = JSON.parse(ovCleaned); }
                catch(e) { const m = ovCleaned.match(/\{[\s\S]*\}/); if(m) overlap = JSON.parse(m[0]); else throw e; }

                if (overlap && overlap.overlap) {
                  const warning = overlap.warning || '检测到重复主题';
                  // Append warning callout to note
                  const warningMd = `\n> [!warning] 重复弱点预警\n> ${warning}\n> 相关笔记: ${(overlap.with || []).join(', ')}\n`;
                  fs.appendFileSync(filePath, warningMd, 'utf-8');
                  console.log('Repeat weakness detected: ' + warning);

                  // Bark push
                  const BARK_KEY = 'YOUR_BARK_KEY';
                  const bTitle = encodeURIComponent('⚠️ 重复弱点');
                  const bBody = encodeURIComponent(warning.slice(0, 80));
                  https.get(`https://api.day.app/${BARK_KEY}/${bTitle}/${bBody}?sound=calypso`, () => {});
                }
              } catch(e) { console.error('Overlap check parse error:', e.message); }
            }
          } catch(e) { console.error('Review essence error:', e.message); }
        })();

        // Auto-extract problems from conversation (with Vision OCR for images)
        (async () => {
          try {
            const PROBLEMS_FILE = '/root/.ai-problems.json';

            // Step 1: OCR images if present (题目可能在图片中)
            let ocrTexts = [];
            if (images && images.length > 0) {
              for (const img of images) {
                if (!img.data || !img.data.startsWith('data:image')) continue;
                try {
                  const visionBody = JSON.stringify({
                    model: 'qwen',
                    messages: [{
                      role: 'user',
                      content: [
                        {type: 'text', text: '这是一道考试/作业题目的照片。请完整提取题目中的所有文字，包括数学公式（用LaTeX $...$格式）。只输出题目原文，不要解答。如果图片模糊，尽力识别。'},
                        {type: 'image_url', image_url: {url: img.data}}
                      ]
                    }],
                    max_tokens: 800
                  });
                  const vUrl = new URL(API_BASE);
                  const vResp = await new Promise((resolve, reject) => {
                    const r = https.request({
                      hostname: vUrl.hostname, path: vUrl.pathname, method: 'POST',
                      headers: {'Content-Type':'application/json','Authorization':'Bearer '+API_KEY,'Content-Length':Buffer.byteLength(visionBody)}
                    }, resp => { let d=''; resp.on('data',c=>d+=c); resp.on('end',()=>resolve(d)); });
                    r.on('error', reject);
                    r.setTimeout(60000, () => { r.destroy(); reject(new Error('vision timeout')); });
                    r.write(visionBody); r.end();
                  });
                  const vData = JSON.parse(vResp);
                  const ocrText = vData.choices?.[0]?.message?.content?.trim();
                  if (ocrText && ocrText.length > 10) {
                    ocrTexts.push(ocrText);
                    console.log('OCR extracted: ' + ocrText.slice(0, 60));
                  }
                } catch(e) { console.error('Vision OCR error:', e.message); }
              }
            }

            // Step 2: AI extract problems
            const userContent =
              (ocrTexts.length > 0 ? '【图片OCR识别的题目文字】\n' + ocrTexts.join('\n---\n') + '\n\n' : '') +
              '【对话内容】\n' + convText.slice(0, 5000);

            const extractPrompt = [
              { role: 'system', content: `你是题目提取专家。从以下学习对话中提取所有可复习的题目。

用户可能通过图片上传题目（OCR识别结果已提供），Gemini直接给出解答但不复述原题。
你需要：
1. 从OCR文字中还原完整题目（如果有）
2. 从Gemini的多轮回答中综合出完整解答（不要省略关键步骤）
3. 提炼核心解题思路

重要格式要求：
- 所有数学公式必须用 $...$ 包裹（如 $\\int_0^1 f(x)\\,dx$, $\\frac{\\partial f}{\\partial x}$）
- 绝对不要用 Unicode 数学符号（如 ∫₀¹, x², √），必须用 LaTeX
- 方括号用 $\\left[...\\right]$

返回JSON:
{"problems":[{"question":"完整题目（LaTeX公式用$...$）","solution":"综合解答（LaTeX公式用$...$）","key_insight":"一句话解题思路","difficulty":"easy|medium|hard","type":"计算|证明|概念|应用","keywords":["关键词"]}]}
如果对话中没有具体题目（只是概念讨论），返回 {"problems":[]}
只返回JSON。` },
              { role: 'user', content: userContent }
            ];

            const raw = await callModelJSON(extractPrompt, 'deepseek-v3.2');
            let cleaned = raw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
            let result;
            try { result = JSON.parse(cleaned); }
            catch(e) { const m = cleaned.match(/\{[\s\S]*\}/); if(m) result = JSON.parse(m[0]); else throw e; }

            const problems = result.problems || [];
            if (problems.length === 0) {
              console.log('No problems extracted from ' + fileName);
              return;
            }

            // Step 3: Save to problems file
            let existing = [];
            try { existing = JSON.parse(fs.readFileSync(PROBLEMS_FILE, 'utf-8')); } catch(e) {}

            const now = Date.now();
            const newProblems = problems.map((p, i) => ({
              id: now + '_' + i,
              subject: finalSubject,
              source: 'AI笔记/' + finalSubject + '/' + fileName,
              question: p.question || '',
              question_image: (images && images.length > 0) ? imgMap[images[0]?.id] || '' : '',
              solution: p.solution || '',
              key_insight: p.key_insight || '',
              difficulty: p.difficulty || 'medium',
              type: p.type || '计算',
              keywords: p.keywords || [],
              created: new Date().toISOString().split('T')[0],
              mastery: 0,
              review_count: 0,
              last_reviewed: null
            }));

            existing.push(...newProblems);
            fs.writeFileSync(PROBLEMS_FILE, JSON.stringify(existing, null, 2));
            console.log('Extracted ' + newProblems.length + ' problems from ' + fileName);
          } catch(e) { console.error('Problem extraction error:', e.message); }
        })();

        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({
          success: true,
          path: `AI\u7b14\u8bb0/${finalSubject}/${fileName}`,
          title: noteTitle,
          subject: finalSubject
        }));

      } catch(e) {
        console.error('smart-note error:', e);
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }




  // ========== Focus Log (Pomodoro) ==========
  const FOCUS_LOG = '/root/.focus-log.json';

  if (req.method === 'POST' && req.url === '/api/focus-log') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const entry = JSON.parse(body);
        let log = [];
        try { log = JSON.parse(fs.readFileSync(FOCUS_LOG, 'utf-8')); } catch(e) {}
        log.push({ subject: entry.subject, duration: entry.duration, timestamp: entry.timestamp || new Date().toISOString() });
        fs.writeFileSync(FOCUS_LOG, JSON.stringify(log, null, 2));
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ ok: true, total: log.length }));
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'GET' && req.url.startsWith('/api/focus-log')) {
    try {
      let log = [];
      try { log = JSON.parse(fs.readFileSync(FOCUS_LOG, 'utf-8')); } catch(e) {}
      const url = new URL(req.url, 'http://localhost');
      const range = url.searchParams.get('range') || 'today';
      const now = new Date();
      const todayStr = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0');

      let filtered;
      if (range === 'today') {
        filtered = log.filter(e => e.timestamp && e.timestamp.startsWith(todayStr));
      } else if (range === 'week') {
        const weekAgo = new Date(now - 7 * 86400000).toISOString().slice(0, 10);
        filtered = log.filter(e => e.timestamp && e.timestamp.slice(0, 10) >= weekAgo);
      } else {
        filtered = log;
      }
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ entries: filtered }));
    } catch(e) {
      res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }


  // Focus timer - schedule Bark push for when pomodoro completes (works when phone locked)
  if (req.method === 'POST' && req.url === '/api/focus-timer') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const { subject, duration } = JSON.parse(body);
        const BARK_KEY = 'YOUR_BARK_KEY';
        const ms = (duration || 25) * 60 * 1000;
        setTimeout(() => {
          const title = encodeURIComponent('\u{1F345} ' + (duration || 25) + '\u5206\u949F\u4E13\u6CE8\u5B8C\u6210\uFF01');
          const body = encodeURIComponent(subject + ' - \u4F11\u606F\u4E00\u4E0B\u5427');
          const url = 'https://api.day.app/' + BARK_KEY + '/' + title + '/' + body + '?sound=calypso&level=timeSensitive';
          https.get(url, () => {}).on('error', () => {});
          console.log('Bark timer push: ' + subject);
        }, ms);
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ ok: true, notify_in: duration + 'min' }));
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }


  // Get AI-generated flashcards (for flashcards.html to sync)
  if (req.method === 'GET' && req.url.startsWith('/api/flashcards')) {
    try {
      const FLASHCARD_FILE = '/root/.flashcards-server.json';
      let cards = [];
      try { cards = JSON.parse(fs.readFileSync(FLASHCARD_FILE, 'utf-8')); } catch(e) {}
      const unsynced = cards.filter(c => !c.synced);
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ cards: unsynced, total: cards.length }));
    } catch(e) {
      res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  // Mark flashcards as synced
  if (req.method === 'POST' && req.url === '/api/flashcards/ack') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const { ids } = JSON.parse(body);
        const FLASHCARD_FILE = '/root/.flashcards-server.json';
        let cards = [];
        try { cards = JSON.parse(fs.readFileSync(FLASHCARD_FILE, 'utf-8')); } catch(e) {}
        const idSet = new Set(ids);
        cards.forEach(c => { if (idSet.has(c.id)) c.synced = true; });
        fs.writeFileSync(FLASHCARD_FILE, JSON.stringify(cards, null, 2));
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ ok: true, synced: ids.length }));
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // Subject Weakness Analysis Agent
  if (req.method === 'POST' && req.url === '/api/weakness') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { subject } = JSON.parse(body);
        if (!subject) {
          res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ error: 'subject required' }));
          return;
        }

        const SUBJECT_FULL = {
          '高数':'高数II','有机':'有机化学','概统':'概率统计','大物':'大学物理',
          '分化':'分析化学','普生':'普通生物学','AI':'AI基础','习思想':'习思想',
          '高数II':'高数II','有机化学':'有机化学','概率统计':'概率统计',
          '大学物理':'大学物理','分析化学':'分析化学','普通生物学':'普通生物学',
          'AI基础':'AI基础'
        };
        const COURSE_FOLDER = {
          '高数II':'高数II','有机化学':'有机化学','概率统计':'概率统计',
          '大学物理':'大学物理','分析化学':'分析化学','普通生物学':'普通生物学',
          'AI基础':'人工智能基础','习思想':'习思想'
        };
        const FC_SHORT = {
          '高数II':'高数','有机化学':'有机','概率统计':'概统','大学物理':'大物',
          '分析化学':'分析化学','普通生物学':'普生','AI基础':'AI基础','习思想':'习思想'
        };

        const fullSubject = SUBJECT_FULL[subject] || subject;
        const daysLeft = Math.ceil((new Date('2026-06-22') - new Date()) / 86400000);

        // 1. Gather AI notes for this subject
        const aiDir = path.join(VAULT_DIR, 'AI笔记', fullSubject);
        let aiNotes = [];
        if (fs.existsSync(aiDir)) {
          const files = fs.readdirSync(aiDir).filter(f => f.endsWith('.md') && !f.startsWith('.')).sort();
          for (const f of files.slice(-20)) {
            try {
              const content = fs.readFileSync(path.join(aiDir, f), 'utf-8');
              const sm = content.match(/summary:\s*"(.+?)"/);
              const kpMatch = content.match(/## 要点\n\n([\s\S]*?)\n\n##/);
              aiNotes.push({
                title: f.replace('.md', ''),
                summary: sm ? sm[1] : '',
                keyPoints: kpMatch ? kpMatch[1].slice(0, 150) : '',
                date: f.slice(0, 10)
              });
            } catch(e) {}
          }
        }

        // 2. Search related notes via RAG
        const ragResults = searchNotes(fullSubject + ' ' + subject, 5);

        // 3. Read flashcards for this subject
        const FLASHCARD_FILE = '/root/.flashcards-server.json';
        const fcShort = FC_SHORT[fullSubject] || fullSubject;
        let flashcards = [];
        try {
          const allCards = JSON.parse(fs.readFileSync(FLASHCARD_FILE, 'utf-8'));
          flashcards = allCards.filter(c => c.s === fcShort || c.s === fullSubject);
        } catch(e) {}

        // 4. Read spaced repetition state
        let spacedItems = [];
        try {
          const all = JSON.parse(fs.readFileSync('/root/.spaced-repetition.json', 'utf-8'));
          spacedItems = all.filter(it => it.text.includes(fullSubject) || it.text.includes(subject));
        } catch(e) {}

        // 5. Read weakness tracker
        let weaknesses = [];
        try {
          const all = JSON.parse(fs.readFileSync('/root/.weakness-tracker.json', 'utf-8'));
          weaknesses = all.filter(w => w.text.includes(fullSubject) || w.text.includes(subject));
        } catch(e) {}

        // 5b. Vikunja task completion quality for this subject
        let taskStats = { total: 0, done: 0, overdue_done: 0 };
        try {
          const vikunjaReq = (apiPath) => new Promise((resolve, reject) => {
            const opts = { hostname: '127.0.0.1', port: 8086, path: '/api/v1' + apiPath, headers: { 'Authorization': 'Bearer YOUR_VIKUNJA_TOKEN' } };
            require('http').get(opts, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => resolve(JSON.parse(d))); }).on('error', reject);
          });
          const tasks = await vikunjaReq('/projects/2/tasks?per_page=100');
          if (Array.isArray(tasks)) {
            const subjectTasks = tasks.filter(t => t.title && (t.title.includes(fullSubject) || t.title.includes(subject) || t.title.includes(fcShort)));
            taskStats.total = subjectTasks.length;
            taskStats.done = subjectTasks.filter(t => t.done).length;
            taskStats.overdue_done = subjectTasks.filter(t => {
              if (!t.done || !t.done_at || !t.due_date) return false;
              return new Date(t.done_at) > new Date(t.due_date);
            }).length;
          }
        } catch(e) {}

        // 6. Scan recent 学习回顾 for subject-specific sections
        let reviewExcerpts = '';
        const noteDir = path.join(VAULT_DIR, '每日笔记');
        if (fs.existsSync(noteDir)) {
          const dateDirs = fs.readdirSync(noteDir)
            .filter(d => d.match(/^\d{4}-\d{2}-\d{2}$/))
            .sort().reverse().slice(0, 14);
          for (const dd of dateDirs) {
            const rp = path.join(noteDir, dd, '学习回顾.md');
            if (fs.existsSync(rp)) {
              try {
                const rc = fs.readFileSync(rp, 'utf-8');
                const lines = rc.split('\n');
                let inSubj = false, excerpt = '';
                for (const line of lines) {
                  if (line.match(/^#{3,4}/) && (line.includes(fullSubject) || line.includes(subject))) {
                    inSubj = true;
                    excerpt += `[${dd}] `;
                    continue;
                  }
                  if (inSubj && line.match(/^#{3,4}/)) break;
                  if (inSubj) excerpt += line + '\n';
                }
                if (excerpt.trim()) reviewExcerpts += excerpt.slice(0, 400) + '\n';
              } catch(e) {}
            }
          }
        }

        // 7. Read course notes
        let courseContext = '';
        const cDir = path.join(VAULT_DIR, '课程', COURSE_FOLDER[fullSubject] || fullSubject);
        if (fs.existsSync(cDir)) {
          for (const fname of ['期末复习.md', '公式速查表.md', '典型题型与解法.md', '反应总结.md']) {
            const fp = path.join(cDir, fname);
            if (fs.existsSync(fp)) {
              courseContext += `\n=== ${fname} ===\n${fs.readFileSync(fp, 'utf-8').slice(0, 800)}\n`;
            }
          }
        }

        // 8. Build comprehensive AI prompt
        let dataBlock = '';
        if (ragResults && ragResults.length > 0) {
          dataBlock += `\n【笔记库检索结果 (${ragResults.length}条)】\n`;
          ragResults.forEach(r => {
            dataBlock += `- ${r.title}: ${(r.content || '').slice(0, 150)}\n`;
          });
        }
        if (aiNotes.length > 0) {
          dataBlock += `\n【AI笔记记录 (${aiNotes.length}篇)】\n`;
          aiNotes.forEach(n => {
            dataBlock += `- ${n.date} ${n.title}: ${n.summary}${n.keyPoints ? ' | 要点: ' + n.keyPoints : ''}\n`;
          });
        }
        if (reviewExcerpts) {
          dataBlock += `\n【学习回顾中该科目的历史记录】\n${reviewExcerpts.slice(0, 1500)}\n`;
        }
        if (flashcards.length > 0) {
          dataBlock += `\n【闪卡 (${flashcards.length}张)】\n`;
          flashcards.slice(-10).forEach(c => {
            dataBlock += `- Q: ${c.q.slice(0, 60)} | A: ${c.a.slice(0, 60)}\n`;
          });
        }
        if (spacedItems.length > 0) {
          dataBlock += `\n【间隔重复项 (${spacedItems.length}项，均为未完全掌握)】\n`;
          spacedItems.forEach(it => {
            dataBlock += `- ${it.text} (发现:${it.found}, 间隔:${it.interval}天)\n`;
          });
        }
        if (weaknesses.length > 0) {
          dataBlock += `\n【薄弱点追踪 (${weaknesses.length}项)】\n`;
          weaknesses.forEach(w => {
            dataBlock += `- ${w.text} (状态:${w.status})\n`;
          });
        }
        if (courseContext) {
          dataBlock += `\n【课程笔记参考】\n${courseContext.slice(0, 2000)}\n`;
        }
        if (taskStats.total > 0) {
          const onTimeRate = taskStats.done > 0 ? Math.round((1 - taskStats.overdue_done / taskStats.done) * 100) : 0;
          dataBlock += `\n【Vikunja 任务完成情况】\n`;
          dataBlock += `- 该科目总任务: ${taskStats.total}, 已完成: ${taskStats.done}, 逾期完成: ${taskStats.overdue_done}\n`;
          dataBlock += `- 按时完成率: ${onTimeRate}%\n`;
        }

        const analysisPrompt = [
          { role: 'system', content: `你是期末复习分析师。用户是上海交大大一生科院学生，距离期末考试${daysLeft}天。
请根据以下该科目（${fullSubject}）的所有学习数据，进行深度弱点分析。

${dataBlock}

请输出以下结构（markdown格式，不要代码块包裹）：

### 🔍 弱点分析
按主题分组，每个弱点标注：
- 出现频率（在多少份笔记/闪卡中出现）
- 严重程度：🔴高危（反复出错）/ 🟡中等（偶尔遗忘）/ 🟢已巩固
- 具体表现（引用数据来源）

### 📋 优先复习清单（Top 5）
| 排名 | 知识点 | 原因 | 建议复习方式 | 预估时间 |
|------|--------|------|-------------|---------|

### 🎯 针对性练习（3道）
针对Top弱点出3道练习题（易、中、难各1道），用Obsidian折叠语法写解答：
> [!tip]- 解答
> 详细步骤...

### 📊 科目掌握度评估
- 整体掌握百分比估算
- 最强 vs 最弱的知识模块
- 距离目标分数的差距分析

重要格式要求：
- 所有数学公式必须用 $...$ 包裹（如 $\\\\int_0^1 2x\\\\,dx$），绝对不要用反引号包裹数学表达式
- 方括号 [...] 在 Obsidian 中是特殊语法会导致解析错误，数学中请用 $\\\\left[...\\\\right]$ 代替
- 不要在 $...$ 内部嵌套另一个 $...$` },
          { role: 'user', content: `请分析我在"${fullSubject}"科目的学习情况和弱点。` }
        ];

        const analysis = await callModelJSON(analysisPrompt, 'deepseek-v3.2');
        let cleaned = analysis;
        if (cleaned.startsWith('```')) cleaned = cleaned.split('\n').slice(1).join('\n');
        if (cleaned.endsWith('```')) cleaned = cleaned.split('\n').slice(0, -1).join('\n');
        // Post-process: convert backtick math to $...$ to prevent Dataview parsing
        const mathSigns = /[∫∮∑∏√∂∇≤≥±∈∉⊂⊃∪∩∞≈≠∝∀∃^_\\]/;
        cleaned = cleaned.replace(/`([^`]+)`/g, (m, inner) => mathSigns.test(inner) ? `$${inner}$` : m);

        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({
          subject: fullSubject,
          analysis: cleaned,
          stats: {
            ai_notes: aiNotes.length,
            flashcards: flashcards.length,
            spaced_items: spacedItems.length,
            weaknesses: weaknesses.length,
            review_days: reviewExcerpts ? reviewExcerpts.split('[20').length - 1 : 0,
            tasks_total: taskStats.total,
            tasks_done: taskStats.done,
            tasks_ontime_rate: taskStats.done > 0 ? Math.round((1 - taskStats.overdue_done / taskStats.done) * 100) : 0
          }
        }));

      } catch(e) {
        console.error('weakness analysis error:', e);
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // Problems API (AI-extracted problem bank)
  if (req.url.startsWith('/api/problems')) {
    const PROBLEMS_FILE = '/root/.ai-problems.json';

    // GET /api/problems?subject=高数
    if (req.method === 'GET' && (req.url === '/api/problems' || req.url.startsWith('/api/problems?'))) {
      try {
        let problems = [];
        try { problems = JSON.parse(fs.readFileSync(PROBLEMS_FILE, 'utf-8')); } catch(e) {}

        // Filter by subject if specified
        const urlObj = new URL(req.url, 'http://localhost');
        const subjectFilter = urlObj.searchParams.get('subject');
        if (subjectFilter) {
          problems = problems.filter(p => p.subject && (p.subject.includes(subjectFilter) || subjectFilter.includes(p.subject)));
        }

        // Stats
        const total = problems.length;
        const mastered = problems.filter(p => p.mastery >= 80).length;
        const reviewing = problems.filter(p => p.mastery > 0 && p.mastery < 80).length;

        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ problems, stats: { total, mastered, reviewing, unreviewed: total - mastered - reviewing } }));
      } catch(e) {
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
      return;
    }

    // POST /api/problems/review — update mastery
    if (req.method === 'POST' && req.url === '/api/problems/review') {
      let body = '';
      req.on('data', c => body += c);
      req.on('end', () => {
        try {
          const { id, mastery } = JSON.parse(body);
          let problems = [];
          try { problems = JSON.parse(fs.readFileSync(PROBLEMS_FILE, 'utf-8')); } catch(e) {}

          const prob = problems.find(p => p.id === id);
          if (prob) {
            prob.mastery = Math.min(100, Math.max(0, mastery));
            prob.review_count = (prob.review_count || 0) + 1;
            prob.last_reviewed = new Date().toISOString();
            fs.writeFileSync(PROBLEMS_FILE, JSON.stringify(problems, null, 2));
          }

          res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ ok: true }));
        } catch(e) {
          res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ error: e.message }));
        }
      });
      return;
    }
  }

  // Study stats API (for dashboard)
  if (req.method === 'GET' && req.url.startsWith('/api/study-stats')) {
    try {
      const STUDY_LOG = '/root/.study-log.json';
      let logs = [];
      if (fs.existsSync(STUDY_LOG)) {
        logs = JSON.parse(fs.readFileSync(STUDY_LOG, 'utf-8'));
      }
      // Aggregate by date and subject
      const byDate = {};
      logs.forEach(e => {
        if (!byDate[e.date]) byDate[e.date] = {};
        byDate[e.date][e.subject] = (byDate[e.date][e.subject] || 0) + e.duration;
      });
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ logs: byDate, total_entries: logs.length }));
    } catch(e) {
      res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  // Weakness plan - auto-create Vikunja tasks from weakness analysis
  if (req.method === 'POST' && req.url === '/api/weakness-plan') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { subject, analysis } = JSON.parse(body);
        if (!subject || !analysis) {
          res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ error: 'subject and analysis required' }));
          return;
        }

        // Ask AI to extract actionable tasks from the analysis
        const planPrompt = [
          { role: 'system', content: `你是任务规划助手。根据以下弱点分析报告，生成3-5个具体可执行的复习任务。

返回JSON数组: [{"title":"任务标题(简短)","description":"具体做什么","priority":3,"days":2}]
- title: 简洁（20字以内），格式为"科目：具体行动"
- description: 具体步骤说明
- priority: 1-5（5最高）
- days: 建议几天内完成

只返回JSON数组，不要其他内容。` },
          { role: 'user', content: `科目：${subject}\n\n弱点分析：\n${analysis.slice(0, 2000)}` }
        ];

        const taskRaw = await callModelJSON(planPrompt, 'deepseek-v3.2');
        let cleaned = taskRaw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
        let tasks;
        try { tasks = JSON.parse(cleaned); }
        catch(e) { const m = cleaned.match(/\[[\s\S]*\]/); if(m) tasks = JSON.parse(m[0]); else throw e; }

        if (!Array.isArray(tasks)) throw new Error('Invalid task format');

        // Create tasks in Vikunja
        const created = [];
        for (const t of tasks.slice(0, 5)) {
          const dueDate = new Date();
          dueDate.setDate(dueDate.getDate() + (t.days || 3));
          const due = dueDate.toISOString().split('T')[0] + 'T22:00:00+08:00';

          const taskData = JSON.stringify({
            title: t.title.slice(0, 50),
            description: t.description || '',
            priority: Math.min(5, Math.max(1, t.priority || 3)),
            due_date: due
          });

          try {
            const opts = {
              hostname: '127.0.0.1', port: 8086,
              path: '/api/v1/projects/2/tasks',
              method: 'PUT',
              headers: {
                'Authorization': 'Bearer YOUR_VIKUNJA_TOKEN',
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(taskData)
              }
            };
            await new Promise((resolve, reject) => {
              const r = require('http').request(opts, resp => {
                let d = ''; resp.on('data', c => d += c);
                resp.on('end', () => { created.push({ title: t.title, due: due.split('T')[0] }); resolve(); });
              });
              r.on('error', reject);
              r.write(taskData);
              r.end();
            });
          } catch(e) { console.error('Task creation failed:', e.message); }
        }

        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ created, count: created.length }));
      } catch(e) {
        console.error('weakness-plan error:', e);
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // Knowledge Coverage Check
  if (req.method === 'POST' && req.url === '/api/coverage') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { subject } = JSON.parse(body);
        if (!subject) { res.writeHead(400); res.end('subject required'); return; }

        const SUBJECT_FULL = {
          '高数':'高数II','有机':'有机化学','概统':'概率统计','大物':'大学物理',
          '分化':'分析化学','普生':'普通生物学','高数II':'高数II','有机化学':'有机化学',
          '概率统计':'概率统计','大学物理':'大学物理','分析化学':'分析化学'
        };
        const COURSE_FOLDER = {
          '高数II':'高数II','有机化学':'有机化学','概率统计':'概率统计',
          '大学物理':'大学物理','分析化学':'分析化学','普通生物学':'普通生物学',
          'AI基础':'人工智能基础','习思想':'习思想'
        };
        const fullSubject = SUBJECT_FULL[subject] || subject;
        const courseFolder = COURSE_FOLDER[fullSubject] || fullSubject;

        // 1. Extract chapter/topic list from course notes
        const courseDir = path.join(VAULT_DIR, '课程', courseFolder);
        let chapters = [];
        let courseContent = '';
        if (fs.existsSync(courseDir)) {
          // Read 期末复习.md or list all .md files as chapters
          const reviewFile = path.join(courseDir, '期末复习.md');
          if (fs.existsSync(reviewFile)) {
            courseContent = fs.readFileSync(reviewFile, 'utf-8');
            // Extract only ## headers as chapters (not ### sub-sections), cap at 20
            courseContent.split('\n').forEach(line => {
              const m = line.match(/^## (.+)/);
              if (m && m[1].length > 2 && !m[1].includes('参考') && !m[1].includes('说明') && !m[1].includes('资料') && chapters.length < 20) {
                chapters.push(m[1].trim());
              }
            });
          }
          // Also add chapter files as topics
          if (chapters.length < 3) {
            const files = fs.readdirSync(courseDir).filter(f => f.endsWith('.md') && f !== '期末复习.md');
            files.forEach(f => {
              const name = f.replace('.md', '').replace(/^ch\d+_/, '');
              if (name.length > 2) chapters.push(name);
            });
          }
        }

        if (chapters.length === 0) {
          res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
          res.end(JSON.stringify({ subject: fullSubject, error: 'no_chapters', chapters: [], coverage: 0 }));
          return;
        }

        // 2. Gather all study evidence
        let evidence = '';

        // AI notes titles and summaries
        const aiDir = path.join(VAULT_DIR, 'AI笔记', fullSubject);
        if (fs.existsSync(aiDir)) {
          const files = fs.readdirSync(aiDir).filter(f => f.endsWith('.md'));
          files.forEach(f => {
            try {
              const c = fs.readFileSync(path.join(aiDir, f), 'utf-8').slice(0, 300);
              const sm = c.match(/summary:\s*"(.+?)"/);
              evidence += f.replace('.md', '') + ' ' + (sm ? sm[1] : '') + '\n';
            } catch(e) {}
          });
        }

        // Flashcards
        const FC_SHORT = {'高数II':'高数','有机化学':'有机','概率统计':'概统','大学物理':'大物','分析化学':'分析化学','普通生物学':'普生'};
        try {
          const allCards = JSON.parse(fs.readFileSync('/root/.flashcards-server.json', 'utf-8'));
          const fcShort = FC_SHORT[fullSubject] || fullSubject;
          allCards.filter(c => c.s === fcShort || c.s === fullSubject).forEach(c => {
            evidence += c.q + '\n';
          });
        } catch(e) {}

        // Learning review mentions
        const noteDir = path.join(VAULT_DIR, '每日笔记');
        if (fs.existsSync(noteDir)) {
          fs.readdirSync(noteDir).filter(d => d.match(/^\d{4}-\d{2}-\d{2}$/)).sort().reverse().slice(0, 21).forEach(dd => {
            const rp = path.join(noteDir, dd, '学习回顾.md');
            if (fs.existsSync(rp)) {
              try {
                const rc = fs.readFileSync(rp, 'utf-8');
                if (rc.includes(fullSubject) || rc.includes(subject)) {
                  // Extract subject-related lines
                  rc.split('\n').forEach(line => {
                    if (line.includes(fullSubject) || line.includes(subject)) evidence += line + '\n';
                  });
                }
              } catch(e) {}
            }
          });
        }

        // 3. AI matching
        const coveragePrompt = [
          { role: 'system', content: `你是考试覆盖度分析师。以下是某科目的章节/知识点清单和学生的学习证据（笔记标题、闪卡问题、学习回顾提及）。
请判断每个章节的覆盖情况，返回JSON：
{
  "chapters": [
    {"name": "章节名", "status": "covered|partial|uncovered", "evidence": "简短证据说明"}
  ],
  "coverage_pct": 数字(0-100),
  "uncovered_warning": ["未覆盖的关键章节1", ...]
}
只返回JSON。` },
          { role: 'user', content: `科目：${fullSubject}\n\n章节清单：\n${chapters.map((c,i) => `${i+1}. ${c}`).join('\n')}\n\n学习证据：\n${evidence.slice(0, 2000)}` }
        ];

        const raw = await callModelJSON(coveragePrompt, 'deepseek-v3.2');
        let cleaned = raw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
        let result;
        try { result = JSON.parse(cleaned); }
        catch(e) { const m = cleaned.match(/\{[\s\S]*\}/); if(m) result = JSON.parse(m[0]); else throw e; }

        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({
          subject: fullSubject,
          total_chapters: chapters.length,
          ...result
        }));
      } catch(e) {
        console.error('coverage error:', e);
        res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // GPA Prediction
  if (req.method === 'GET' && req.url === '/api/gpa-predict') {
    (async () => { try {
      const COURSE_EXAMS = {
        '高等数学II':   {credit: 4, known: 70, type: 'midterm', target: 85},
        '有机化学':     {credit: 4, known: null, type: null, target: 88},
        '概率统计':     {credit: 3, known: null, type: null, target: 83},
        '大学物理':     {credit: 3, known: null, type: null, target: 85},
        '习思想':       {credit: 3, known: null, type: null, target: 80},
        '分析化学':     {credit: 2, known: null, type: null, target: 75},
        '普通生物学':   {credit: 2, known: null, type: null, target: 80},
        'AI基础':       {credit: 2, known: null, type: null, target: 80},
      };
      const FC_SHORT = {'高数II':'高数','有机化学':'有机','概率统计':'概统','大学物理':'大物','分析化学':'分析化学','普通生物学':'普生','AI基础':'AI基础','习思想':'习思想'};

      // Gather per-subject data
      let studyLog = {};
      try {
        const logs = JSON.parse(fs.readFileSync('/root/.study-log.json', 'utf-8'));
        logs.forEach(e => { studyLog[e.subject] = (studyLog[e.subject] || 0) + e.duration; });
      } catch(e) {}

      let flashcardCounts = {};
      try {
        const cards = JSON.parse(fs.readFileSync('/root/.flashcards-server.json', 'utf-8'));
        cards.forEach(c => { flashcardCounts[c.s] = (flashcardCounts[c.s] || 0) + 1; });
      } catch(e) {}

      let weaknessCounts = {};
      try {
        const wk = JSON.parse(fs.readFileSync('/root/.weakness-tracker.json', 'utf-8'));
        wk.filter(w => w.status === 'active' || !w.status).forEach(w => {
          for (const [name] of Object.entries(COURSE_EXAMS)) {
            if (w.text.includes(name)) { weaknessCounts[name] = (weaknessCounts[name] || 0) + 1; break; }
          }
        });
      } catch(e) {}

      const daysLeft = Math.ceil((new Date('2026-06-22') - new Date()) / 86400000);

      // Build AI prompt for prediction
      let dataStr = '';
      for (const [name, info] of Object.entries(COURSE_EXAMS)) {
        const short = FC_SHORT[name] || name;
        const studyMin = studyLog[name] || studyLog[short] || 0;
        const fcCount = flashcardCounts[short] || flashcardCounts[name] || 0;
        const wkCount = weaknessCounts[name] || 0;
        dataStr += `- ${name}(${info.credit}学分): 已学${studyMin}min, ${fcCount}张闪卡, ${wkCount}个弱点`;
        if (info.known) dataStr += `, 期中${info.known}分`;
        dataStr += `, 目标${info.target}\n`;
      }

      const predPrompt = [
        { role: 'system', content: `你是GPA预测分析师。根据以下学习数据，为每门课预测期末成绩（保守/中等/乐观三档）。
距期末${daysLeft}天。学生是SJTU大一。

${dataStr}

返回JSON:
{
  "predictions": [
    {"course":"课名","credit":学分,"conservative":分数,"moderate":分数,"optimistic":分数}
  ],
  "gpa": {"conservative":数字,"moderate":数字,"optimistic":数字},
  "advice": "一句话建议"
}
GPA计算：4.0制（90+=4.0, 85-89=3.7, 80-84=3.3, 75-79=3.0, 70-74=2.7, 60-69=2.0, <60=0）
只返回JSON。` },
        { role: 'user', content: '请预测我的期末成绩和GPA。' }
      ];

      const predRaw = await callModelJSON(predPrompt, 'deepseek-v3.2');
      let predCleaned = predRaw.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
      let prediction;
      try { prediction = JSON.parse(predCleaned); }
      catch(e) { const m = predCleaned.match(/\{[\s\S]*\}/); if(m) prediction = JSON.parse(m[0]); else throw e; }

      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify(prediction));
    } catch(e) {
      console.error('gpa-predict error:', e);
      res.writeHead(500, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(JSON.stringify({ error: e.message }));
    } })();
    return;
  }

  // Health check
  if (req.url === "/api/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, notes: noteIndex.length, uptime: process.uptime() | 0 }));
    return;
  }

  res.writeHead(404);
  res.end('not found');
});

// 启动
indexNotes(true);
// 每5分钟检查变化，有变化才重建
setInterval(() => indexNotes(false), 5 * 60 * 1000);

server.listen(PORT, '127.0.0.1', () => {
  console.log(`Chat server on 127.0.0.1:${PORT}`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM received, closing...');
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 5000);
});
