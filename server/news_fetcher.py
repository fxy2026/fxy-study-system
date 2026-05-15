#!/usr/bin/env python3
"""Daily News Fetcher - campus notices + academic/AI news, with caching and AI summaries"""
import json, os, sys, datetime, re, urllib.request, urllib.parse, subprocess

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from bs4 import BeautifulSoup
import feedparser

# ============ CONFIG ============
VAULT_DIR = "/root/webdav-vault"
NOTE_DIR = os.path.join(VAULT_DIR, '每日笔记')
CACHE_DIR = "/root/.news-cache"

AI_API_URL = 'https://models.sjtu.edu.cn/api/v1/chat/completions'
AI_API_KEY = 'YOUR_AI_API_KEY'
AI_MODEL = 'deepseek-v3.2'

BARK_KEY = "YOUR_BARK_KEY"

CANVAS_API = 'https://oc.sjtu.edu.cn/api/v1'
CANVAS_TOKEN = 'YOUR_CANVAS_TOKEN'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

RSS_FEEDS = {
    'Nature': 'https://www.nature.com/nature.rss',
    'Nature Biotech': 'https://www.nature.com/nbt.rss',
    'Science': 'https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science',
    # Cell RSS blocked (403), skip for now
    'arXiv q-bio': 'https://export.arxiv.org/rss/q-bio',
    'arXiv cs.AI': 'https://export.arxiv.org/rss/cs.AI',
}

WEATHER_ICONS = {
    'Clear': '☀️', 'Sunny': '☀️',
    'Partly Cloudy': '⛅', 'Partly cloudy': '⛅',
    'Cloudy': '☁️', 'Overcast': '☁️',
    'Light rain': '🌦️', 'Moderate rain': '🌧️', 'Heavy rain': '🌧️',
    'Rain': '🌧️', 'Patchy rain nearby': '🌦️', 'Patchy rain possible': '🌦️',
    'Light drizzle': '🌦️', 'Thunderstorm': '⛈️',
    'Fog': '🌫️', 'Mist': '🌫️', 'Haze': '🌫️',
}

TZ = datetime.timezone(datetime.timedelta(hours=8))
# ================================

def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}')

def bark_push(title, body, sound='birdsong'):
    if not BARK_KEY:
        return
    params = urllib.parse.urlencode({'group': 'news', 'isArchive': 1, 'sound': sound})
    url = f'https://api.day.app/{BARK_KEY}/{urllib.parse.quote(title, safe="")}/{urllib.parse.quote(body, safe="")}?{params}'
    try:
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        log(f'Bark error: {e}')

# ========== Cache ==========

def load_cache(today_str):
    path = os.path.join(CACHE_DIR, f'{today_str}.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_cache(today_str, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f'{today_str}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        today = datetime.date.fromisoformat(today_str)
        for fname in os.listdir(CACHE_DIR):
            if fname.endswith('.json'):
                try:
                    fdate = datetime.date.fromisoformat(fname.replace('.json', ''))
                    if (today - fdate).days > 7:
                        os.remove(os.path.join(CACHE_DIR, fname))
                except ValueError:
                    pass
    except Exception:
        pass

# ========== Campus Notices ==========

def fetch_jwc_notices(limit=8):
    """Fetch SJTU Academic Affairs Office notices"""
    url = 'https://jwc.sjtu.edu.cn'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, 'html.parser')
        notices = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if href.startswith('info/') and text and len(text) > 4:
                notices.append({
                    'title': text,
                    'link': f'https://jwc.sjtu.edu.cn/{href}',
                })
                if len(notices) >= limit:
                    break
        log(f'JWC: {len(notices)}')
        return notices
    except Exception as e:
        log(f'JWC failed: {e}')
        return []

def fetch_life_notices(limit=8):
    """Fetch School of Life Sciences notices"""
    url = 'https://life.sjtu.edu.cn/Data/List/tz'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, 'html.parser')
        notices = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if not text or len(text) < 5:
                continue
            if href.startswith('/Data/View/'):
                link = f'https://life.sjtu.edu.cn{href}'
            elif 'mp.weixin' in href:
                link = href
            else:
                continue
            if any(n['title'] == text for n in notices):
                continue
            notices.append({'title': text, 'link': link})
            if len(notices) >= limit:
                break
        log(f'Life: {len(notices)}')
        return notices
    except Exception as e:
        log(f'Life failed: {e}')
        return []

def fetch_canvas_announcements(days=7):
    """Fetch recent course announcements from Canvas LMS"""
    try:
        # get active courses
        r = requests.get(f'{CANVAS_API}/courses', params={'per_page': 30, 'enrollment_state': 'active'},
                         headers={'Authorization': f'Bearer {CANVAS_TOKEN}'}, timeout=15, verify=False)
        courses = r.json() if r.status_code == 200 else []
        if not courses:
            log('Canvas: no courses')
            return []

        # build context_codes as list of tuples (repeated keys)
        params = [('per_page', '15')]
        for c in courses:
            if isinstance(c, dict) and 'id' in c:
                params.append(('context_codes[]', f'course_{c["id"]}'))

        r2 = requests.get(f'{CANVAS_API}/announcements', params=params,
                          headers={'Authorization': f'Bearer {CANVAS_TOKEN}'}, timeout=15, verify=False)
        if r2.status_code != 200:
            log(f'Canvas announcements: {r2.status_code}')
            return []

        # map course_id -> name
        course_map = {f'course_{c["id"]}': c.get('name', '') for c in courses if isinstance(c, dict)}

        cutoff = (datetime.datetime.now(TZ) - datetime.timedelta(days=days)).isoformat()
        announcements = []
        for ann in r2.json():
            posted = ann.get('posted_at', '')
            if posted < cutoff:
                continue
            ctx = ann.get('context_code', '')
            course_name = course_map.get(ctx, ctx)
            announcements.append({
                'title': ann.get('title', ''),
                'course': course_name,
                'link': ann.get('html_url', ''),
                'date': posted[:10],
            })
        log(f'Canvas: {len(announcements)}')
        return announcements
    except Exception as e:
        log(f'Canvas failed: {e}')
        return []

def fetch_sjtu_news(limit=5):
    """Fetch SJTU main news (要闻 + 科研成果)"""
    url = 'https://news.sjtu.edu.cn'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, 'html.parser')
        notices = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if not text or len(text) < 10:
                continue
            # only 交大要闻 and 交大智慧(科研)
            if not (href.startswith('/jdyw/') or href.startswith('/jdzh/')):
                continue
            # clean: title + body are merged in <a>, extract just the title
            text = re.sub(r'^\d{4}年\d{2}月\d{2}日', '', text).strip()
            # cut at body start indicators
            for pat in [r'(?<=[^\d])\d{1,2}月\d{1,2}日', r'2026年', r'2025年', r'\.\.\.']:
                m = re.search(pat, text)
                if m and m.start() > 8:
                    text = text[:m.start()].rstrip('，。、（( ')
                    break
            if len(text) > 30:
                text = text[:30]
            if not text or len(text) < 5 or text in seen:
                continue
            seen.add(text)
            link = f'https://news.sjtu.edu.cn{href}' if href.startswith('/') else href
            cat = '科研' if '/jdzh/' in href else '要闻'
            notices.append({'title': text, 'link': link, 'category': cat})
            if len(notices) >= limit:
                break
        log(f'SJTU News: {len(notices)}')
        return notices
    except Exception as e:
        log(f'SJTU News failed: {e}')
        return []

# ========== Chinese Science News ==========

def fetch_bioon_news(limit=8):
    """Fetch bioon.com (生物谷) life science news"""
    url = 'https://www.bioon.com'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, 'html.parser')
        entries = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if not text or len(text) < 15 or 'bioon.com/article/' not in href:
                continue
            if text in seen:
                continue
            seen.add(text)
            entries.append({
                'source': '生物谷',
                'title': text,
                'link': href if href.startswith('http') else f'https:{href}',
                'summary': '',
            })
            if len(entries) >= limit:
                break
        log(f'Bioon: {len(entries)}')
        return entries
    except Exception as e:
        log(f'Bioon failed: {e}')
        return []

def fetch_sciencenet_news(limit=8):
    """Fetch sciencenet.cn (科学网) news"""
    url = 'https://news.sciencenet.cn/'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, 'html.parser')
        entries = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if not text or len(text) < 15:
                continue
            if 'htmlnews' not in href and 'htmlpaper' not in href:
                continue
            if text in seen:
                continue
            seen.add(text)
            link = href if href.startswith('http') else f'https://news.sciencenet.cn{href}'
            entries.append({
                'source': '科学网',
                'title': text,
                'link': link,
                'summary': '',
            })
            if len(entries) >= limit:
                break
        log(f'ScienceNet: {len(entries)}')
        return entries
    except Exception as e:
        log(f'ScienceNet failed: {e}')
        return []

# ========== Weather ==========

def fetch_weather():
    """Fetch Shanghai weather from wttr.in"""
    try:
        r = requests.get('https://wttr.in/Shanghai?format=j1', headers={'User-Agent': 'curl/7.0'}, timeout=10)
        if not r.text or r.text.strip() == '':
            raise ValueError('Empty weather response')
        data = r.json()
        curr = data['current_condition'][0]
        desc = curr['weatherDesc'][0]['value']
        icon = WEATHER_ICONS.get(desc, '🌡️')
        forecast = []
        for day in data.get('weather', [])[:3]:
            date = day.get('date', '')
            hi = day.get('maxtempC', '?')
            lo = day.get('mintempC', '?')
            d = day.get('hourly', [{}])[4].get('weatherDesc', [{}])[0].get('value', '') if len(day.get('hourly', [])) > 4 else ''
            d_icon = WEATHER_ICONS.get(d, '')
            forecast.append({'date': date, 'hi': hi, 'lo': lo, 'desc': d, 'icon': d_icon})
        weather = {
            'temp': curr['temp_C'],
            'feels': curr['FeelsLikeC'],
            'desc': desc,
            'icon': icon,
            'humidity': curr.get('humidity', ''),
            'forecast': forecast,
        }
        log(f'Weather: {weather["temp"]}°C {desc}')
        return weather
    except Exception as e:
        log(f'Weather failed: {e}')
        return None

# ========== RSS Feeds ==========

def fetch_rss(name, url, limit=10):
    """Fetch entries from an RSS feed"""
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        entries = []
        for e in feed.entries[:limit]:
            summary = e.get('summary', '')
            summary = re.sub(r'<[^>]+>', '', summary).strip()
            if len(summary) > 500:
                summary = summary[:500] + '...'
            entries.append({
                'source': name,
                'title': e.get('title', '').strip(),
                'link': e.get('link', ''),
                'summary': summary,
            })
        log(f'RSS [{name}]: {len(entries)}')
        return entries
    except Exception as e:
        log(f'RSS [{name}] failed: {e}')
        return []

def fetch_all_rss():
    """Fetch all RSS feeds, each source isolated"""
    all_entries = []
    for name, url in RSS_FEEDS.items():
        entries = fetch_rss(name, url)
        all_entries.extend(entries)
    return all_entries

# ========== AI Summary ==========

def ai_summarize_news(entries):
    """Use DeepSeek to filter and summarize academic entries in one batch call.
    Returns list of {source, title, chinese_title, summary, link}"""
    if not entries:
        return []

    lines = []
    for i, e in enumerate(entries):
        lines.append(f"[{i}] [{e['source']}] {e['title']}")
        if e['summary']:
            lines.append(f"    {e['summary'][:300]}")
    text_block = '\n'.join(lines)

    prompt = f"""以下是今天的学术论文/新闻列表，来自多个来源（Nature、Science、Cell、arXiv、生物谷、科学网等）。
部分条目是中文，部分是英文。

请完成以下任务：
1. 筛选出与以下领域相关的重要内容（最多选12条）：
   - 生命科学（分子生物学、遗传学、生物化学、细胞生物学、生态学等）
   - 生物信息学 / 计算生物学
   - AI for Science（AI在生物/医药/材料等领域的应用）
   - AI/机器学习的重大突破（通用性进展）
   - 科研政策、基金、学术圈重要动态
2. 对每条：
   - 英文条目：输出中文翻译标题 + 一句话中文摘要（30字以内）
   - 中文条目：保留原标题 + 提炼一句话摘要（30字以内）

请严格输出 JSON 数组格式，每个元素：
{{"index": 原始编号, "chinese_title": "中文标题", "summary": "一句话摘要"}}

如果没有任何相关内容，输出空数组 []

条目列表：
{text_block}"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [
            {'role': 'system', 'content': '你是学术新闻筛选助手。只输出JSON，不要任何其他文字。'},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': 2500,
        'temperature': 0.3,
    }).encode('utf-8')

    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}',
        'Content-Type': 'application/json',
    })

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        content = result['choices'][0]['message']['content'].strip()
        if content.startswith('```'):
            content = '\n'.join(content.split('\n')[1:])
        if content.endswith('```'):
            content = '\n'.join(content.split('\n')[:-1])
        content = content.strip()
        selected = json.loads(content)

        summarized = []
        for item in selected:
            idx = item.get('index', -1)
            if 0 <= idx < len(entries):
                e = entries[idx]
                summarized.append({
                    'source': e['source'],
                    'title': e['title'],
                    'chinese_title': item.get('chinese_title', e['title']),
                    'summary': item.get('summary', ''),
                    'link': e['link'],
                })
        log(f'AI summary: {len(summarized)}/{len(entries)}')
        return summarized
    except Exception as e:
        log(f'AI summary failed: {e}')
        return [
            {
                'source': e['source'],
                'title': e['title'],
                'chinese_title': e['title'],
                'summary': '',
                'link': e['link'],
            }
            for e in entries[:5]
        ]

def ai_filter_notices(jwc, life):
    """Use AI to filter campus notices relevant to a freshman life-science student"""
    all_notices = []
    for n in jwc:
        all_notices.append(f"[教务处] {n['title']}")
    for n in life:
        all_notices.append(f"[生科院] {n['title']}")
    if not all_notices:
        return jwc, life

    text = '\n'.join(f"[{i}] {t}" for i, t in enumerate(all_notices))
    prompt = f"""以下是上海交通大学的校园公告列表。
请筛选出与以下学生直接相关的公告（输出相关公告的编号列表）：

学生背景：大一，生命科学技术学院，目标生物信息学方向。

相关标准：
- 与本科生选课、考试、成绩、转专业直接相关
- 与大一学生可以参加的活动、竞赛、暑期项目相关
- 与生科院本科生直接相关的通知
- 排除：研究生相关、毕业生相关、教师相关、寒假值班表、过时信息

只输出 JSON 数组，包含相关公告的编号，如 [0, 2, 5]。如果都不相关则输出 []。

公告列表：
{text}"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [
            {'role': 'system', 'content': '只输出JSON数组，不要其他文字。'},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': 200,
        'temperature': 0.1,
    }).encode('utf-8')

    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}',
        'Content-Type': 'application/json',
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        content = result['choices'][0]['message']['content'].strip()
        if content.startswith('```'):
            content = '\n'.join(content.split('\n')[1:])
        if content.endswith('```'):
            content = '\n'.join(content.split('\n')[:-1])
        indices = set(json.loads(content.strip()))
        jwc_len = len(jwc)
        filtered_jwc = [n for i, n in enumerate(jwc) if i in indices]
        filtered_life = [n for i, n in enumerate(life) if (i + jwc_len) in indices]
        log(f'Notice filter: {len(filtered_jwc)+len(filtered_life)}/{len(jwc)+len(life)} relevant')
        return filtered_jwc, filtered_life
    except Exception as e:
        log(f'Notice filter failed: {e}')
        return jwc, life

# ========== Formatting ==========

def format_evening_brief(data):
    """Generate brief section for evening report"""
    today = data.get('date', '')
    jwc_count = len(data.get('jwc', []))
    life_count = len(data.get('life', []))
    canvas_count = len(data.get('canvas', []))
    news_count = len(data.get('ai_news', []))

    total = jwc_count + life_count + canvas_count + news_count
    if total == 0:
        return ''

    md = '\n---\n\n'
    md += '### 📰 今日资讯\n\n'

    parts = []
    if canvas_count: parts.append(f'课程公告{canvas_count}')
    if jwc_count: parts.append(f'教务处{jwc_count}')
    if life_count: parts.append(f'生科院{life_count}')
    if news_count: parts.append(f'学术{news_count}')
    md += ' · '.join(parts) + ' 条\n\n'

    # top canvas announcements first (most actionable)
    for ann in data.get('canvas', [])[:2]:
        md += f'- 📚 **{ann["course"]}**: {ann["title"]}\n'

    # relevant campus notices
    for n in (data.get('jwc', [])[:2] + data.get('life', [])[:2]):
        md += f'- [{n["title"][:40]}]({n["link"]})\n'

    # academic news only if present (Sunday)
    if data.get('ai_news'):
        for n in data['ai_news'][:2]:
            label = n.get('source', '')
            title = n.get('chinese_title', n.get('title', ''))
            md += f'- **{label}**: {title[:50]}\n'

    md += f'\n> 完整版 → [[每日笔记/{today}/每日资讯]]\n\n'
    return md

def format_full_report(data):
    """Generate full 每日资讯.md content"""
    today = data.get('date', '')
    now_str = data.get('time', '')

    md = f"""---
created: {today}
tags: [每日资讯]
---

# 📰 每日资讯

> 📅 {today} | {now_str} 自动生成（已去除与前日重复内容）

"""

    # ---- Campus section ----
    md += '## 📢 校园动态\n\n'

    # Canvas announcements
    canvas = data.get('canvas', [])
    if canvas:
        md += '### 📚 课程公告\n\n'
        for ann in canvas:
            md += f'- **{ann["course"]}** [{ann["date"]}]: [{ann["title"]}]({ann["link"]})\n'
        md += '\n'

    # JWC
    jwc = data.get('jwc', [])
    if jwc:
        md += '### 教务处\n\n'
        for n in jwc:
            md += f'- [{n["title"]}]({n["link"]})\n'
        md += '\n'
    else:
        md += '### 教务处\n\n暂无新通知\n\n'

    # Life
    life = data.get('life', [])
    if life:
        md += '### 生科院\n\n'
        for n in life:
            md += f'- [{n["title"]}]({n["link"]})\n'
        md += '\n'
    else:
        md += '### 生科院\n\n暂无新通知\n\n'

    # SJTU News
    sjtu_news = data.get('sjtu_news', [])
    if sjtu_news:
        md += '### 交大要闻\n\n'
        for n in sjtu_news:
            tag = f' `{n["category"]}`' if n.get('category') else ''
            md += f'- [{n["title"]}]({n["link"]}){tag}\n'
        md += '\n'

    # ---- Academic section ----
    ai_news = data.get('ai_news', [])
    if ai_news:
        by_source = {}
        for n in ai_news:
            by_source.setdefault(n['source'], []).append(n)

        md += '## 🔬 学术前沿 & AI 动态\n\n'
        for source, items in by_source.items():
            md += f'### {source}\n\n'
            for n in items:
                ct = n.get('chinese_title', n.get('title', ''))
                summary = n.get('summary', '')
                link = n.get('link', '')
                md += f'- **[{ct}]({link})**'
                if summary:
                    md += f'\n  {summary}'
                md += '\n'
            md += '\n'
    else:
        md += '## 🔬 学术前沿 & AI 动态\n\n*学术资讯每周日更新*\n\n'

    # Weather
    weather = data.get('weather')
    if weather and weather.get('forecast'):
        md += '## 🌤️ 天气\n\n'
        md += f'当前: {weather["icon"]} {weather["temp"]}°C (体感{weather["feels"]}°C) {weather["desc"]}\n\n'
        md += '| 日期 | 天气 | 温度 |\n|------|------|------|\n'
        for f in weather['forecast']:
            md += f'| {f["date"]} | {f["icon"]} {f["desc"]} | {f["lo"]}~{f["hi"]}°C |\n'
        md += '\n'

    return md

# ========== Main Entry ==========

def get_daily_news(today_str):
    """Main entry: fetch all sources (with cache), return structured data"""
    cached = load_cache(today_str)
    if cached:
        log(f'Using cached news for {today_str}')
        return cached

    log(f'Fetching news for {today_str}...')

    # load yesterday's cache for dedup
    yesterday = (datetime.date.fromisoformat(today_str) - datetime.timedelta(days=1)).isoformat()
    prev = load_cache(yesterday)
    prev_links = set()
    if prev:
        for key in ('jwc', 'life', 'ai_news', 'sjtu_news'):
            for item in prev.get(key, []):
                prev_links.add(item.get('link', ''))
        for ann in prev.get('canvas', []):
            prev_links.add(ann.get('link', ''))
        log(f'Dedup: {len(prev_links)} links from yesterday')

    # ---- Fetch campus sources (daily) ----
    jwc_raw = [n for n in fetch_jwc_notices() if n['link'] not in prev_links]
    life_raw = [n for n in fetch_life_notices() if n['link'] not in prev_links]
    canvas = [a for a in fetch_canvas_announcements() if a['link'] not in prev_links]
    sjtu_news = [n for n in fetch_sjtu_news() if n['link'] not in prev_links]

    # AI filter: keep only notices relevant to a freshman life-science student
    jwc, life = ai_filter_notices(jwc_raw, life_raw)

    # ---- Academic news: only on Sundays (weekly digest) ----
    today_date = datetime.date.fromisoformat(today_str)
    is_sunday = today_date.weekday() == 6
    ai_news = []
    if is_sunday:
        rss_entries = [e for e in fetch_all_rss() if e['link'] not in prev_links]
        bioon = [e for e in fetch_bioon_news() if e['link'] not in prev_links]
        sciencenet = [e for e in fetch_sciencenet_news() if e['link'] not in prev_links]
        all_academic = rss_entries + bioon + sciencenet
        ai_news = ai_summarize_news(all_academic)
    else:
        log('Academic news skipped (not Sunday)')

    # Weather (no dedup needed)
    weather = fetch_weather()

    now = datetime.datetime.now(TZ)
    data = {
        'date': today_str,
        'time': now.strftime('%H:%M'),
        'jwc': jwc,
        'life': life,
        'canvas': canvas,
        'sjtu_news': sjtu_news,
        'ai_news': ai_news,
        'weather': weather,
    }

    save_cache(today_str, data)
    return data

def generate_news_report(today_str, data):
    """Write the full 每日资讯.md report"""
    day_dir = os.path.join(NOTE_DIR, today_str)
    os.makedirs(day_dir, exist_ok=True)
    md = format_full_report(data)
    path = os.path.join(day_dir, '每日资讯.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md)
    subprocess.run(['chown', '-R', 'www-data:www-data', NOTE_DIR], capture_output=True)
    log(f'Report written: {path}')

def run_news(today_str=None):
    """Standalone entry: fetch, generate report, push notification"""
    if not today_str:
        today_str = datetime.datetime.now(TZ).strftime('%Y-%m-%d')

    data = get_daily_news(today_str)
    generate_news_report(today_str, data)

    # Bark push
    parts = []
    for key, label in [('jwc', '教务处'), ('life', '生科院'), ('canvas', '课程'), ('ai_news', '学术')]:
        count = len(data.get(key, []))
        if count:
            parts.append(f'{label}{count}')

    top_news = ''
    if data.get('canvas'):
        top = data['canvas'][0]
        top_news = f'\n📚 {top["course"]}: {top["title"][:30]}'
    elif data.get('ai_news'):
        top = data['ai_news'][0]
        top_news = f'\n📌 {top.get("chinese_title", top.get("title", ""))[:40]}'

    weather = data.get('weather')
    weather_str = f' | {weather["icon"]}{weather["temp"]}°C' if weather else ''

    bark_push(
        f'📰 每日资讯 | {today_str}{weather_str}',
        ' · '.join(parts) + top_news if parts else '今日暂无资讯'
    )
    return data

if __name__ == '__main__':
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_news(date_arg)
