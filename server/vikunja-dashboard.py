#!/usr/bin/env python3
"""Daily Note Manager - morning report / evening report / AI summary, all in one"""
import json, os, sys, datetime, urllib.request, urllib.parse, subprocess

# ============ CONFIG ============
BARK_KEY = "YOUR_BARK_KEY"
VIKUNJA_URL = "http://localhost:8086/api/v1"
VIKUNJA_TOKEN = "YOUR_VIKUNJA_TOKEN"
VAULT_DIR = "/root/webdav-vault"

AI_DIR = os.path.join(VAULT_DIR, 'AI笔记')
COURSE_DIR = os.path.join(VAULT_DIR, '课程')
NOTE_DIR = os.path.join(VAULT_DIR, '每日笔记')
AI_API_URL = 'https://models.sjtu.edu.cn/api/v1/chat/completions'
AI_API_KEY = 'YOUR_AI_API_KEY'
AI_MODEL = 'deepseek-v3.2'

PROJECTS = {2: '期末复习', 3: '作业 & DDL', 4: '生活 & 日常', 5: '技能提升', 6: '长期目标'}
PRIORITY_LABEL = {0: '', 1: '⬜', 2: '🟦', 3: '🟨', 4: '🟧', 5: '🟥'}
SUBJECT_MAP = {
    '高数II': '高数II', '有机化学': '有机化学', '概率统计': '概率统计',
    '大学物理': '大学物理', '分析化学': '分析化学', '普通生物学': '普通生物学',
    'AI基础': '人工智能基础', '习思想': '习思想',
}
EXAM_DATE = datetime.date(2026, 6, 22)

# Per-course exam info: (credit, exam_date or None, known_score, score_type, target)
# score_type: 'midterm' = 期中成绩(期末占比按6:4算), None = 未知
# Update exam dates when announced!
COURSE_EXAMS = {
    '高等数学II':   {'credit': 4, 'exam': None, 'known': 70, 'type': 'midterm', 'target': 85},
    '有机化学':     {'credit': 4, 'exam': None, 'known': None, 'type': None,     'target': 88},
    '概率统计':     {'credit': 3, 'exam': None, 'known': None, 'type': None,     'target': 83},
    '大学物理':     {'credit': 3, 'exam': None, 'known': None, 'type': None,     'target': 85},
    '习思想':       {'credit': 3, 'exam': None, 'known': None, 'type': None,     'target': 80},
    '分析化学':     {'credit': 2, 'exam': None, 'known': None, 'type': None,     'target': 75},
    '普通生物学':   {'credit': 2, 'exam': None, 'known': None, 'type': None,     'target': 80},
    'AI基础':       {'credit': 2, 'exam': None, 'known': None, 'type': None,     'target': 80},
}

WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

# Memos
MEMOS_API = 'http://localhost:8083/api/v1'
MEMOS_TOKEN = 'YOUR_MEMOS_TOKEN'
VISION_MODEL = 'qwen'

# State files
SPACED_FILE = '/root/.spaced-repetition.json'
WEAKNESS_FILE = '/root/.weakness-tracker.json'
CHALLENGE_FILE = '/root/.daily-challenge.json'
STUDY_LOG_FILE = '/root/.study-log.json'
# ================================

TZ = datetime.timezone(datetime.timedelta(hours=8))

def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}')

def parse_date(s):
    if not s or s.startswith('0001'):
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(TZ)
    except:
        return None

def bark_push(title, body, sound='birdsong', group='dashboard'):
    if not BARK_KEY:
        return
    params = urllib.parse.urlencode({'group': group, 'isArchive': 1, 'sound': sound})
    url = f'https://api.day.app/{BARK_KEY}/{urllib.parse.quote(title, safe="")}/{urllib.parse.quote(body, safe="")}?{params}'
    try:
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        log(f'Bark error: {e}')

# ========== Shared: file writing ==========

def write_report(today, suffix, content):
    """Write report to 每日笔记/YYYY-MM-DD/suffix.md"""
    day_dir = os.path.join(NOTE_DIR, str(today))
    os.makedirs(day_dir, exist_ok=True)
    path = os.path.join(day_dir, f'{suffix}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    subprocess.run(['chown', '-R', 'www-data:www-data', NOTE_DIR, AI_DIR], capture_output=True)
    log(f'Written to {path}')

# ========== Task Dedup Helpers ==========

def normalize_title(title):
    """Normalize task title for dedup comparison"""
    import re
    t = re.sub(r'[*_【】\[\]（）()：:，。！!？?"""\'\'·\s]', '', title)
    for w in ['复习', '巩固', '回顾', '背诵', '计算', '整理', '练习', '精做', '完成', '重做']:
        t = t.replace(w, '')
    for subj in ['高数II', '高等数学II', '有机化学', '概率统计', '大学物理', '分析化学', '普通生物学', 'AI基础', '习思想']:
        t = t.replace(subj, '')
    t = t.strip('：:、')
    return t

def load_existing_titles():
    """Load all undone task titles, normalized for dedup"""
    titles = set()
    raw_titles = []
    for pid in PROJECTS:
        for t in vikunja_get(f'/projects/{pid}/tasks'):
            if isinstance(t, dict) and not t.get('done'):
                raw = t.get('title', '')
                raw_titles.append(raw)
                titles.add(normalize_title(raw))
    return titles, raw_titles

def is_task_duplicate(new_title, existing_norms):
    """Check if a task title is similar to any existing task"""
    from difflib import SequenceMatcher
    new_norm = normalize_title(new_title)
    if len(new_norm) < 4:
        return False
    for existing in existing_norms:
        if not existing:
            continue
        # exact normalized match
        if new_norm == existing:
            return True
        # fuzzy match (threshold 0.65 for short Chinese strings)
        shorter = min(len(new_norm), len(existing))
        if shorter >= 4:
            ratio = SequenceMatcher(None, new_norm, existing).ratio()
            if ratio > 0.65:
                return True
        # substring containment
        if len(new_norm) >= 6 and (new_norm in existing or existing in new_norm):
            return True
    return False

# ========== Vikunja helpers ==========

def vikunja_get(path):
    req = urllib.request.Request(VIKUNJA_URL + path, headers={'Authorization': f'Bearer {VIKUNJA_TOKEN}'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f'API error: {e}')
        return []

def vikunja_create_task(title, project_id=2, due_date=None, description=''):
    """Create a task in Vikunja. Default project: 期末复习(2)"""
    task = {'title': title}
    if description:
        task['description'] = description
    if due_date:
        task['due_date'] = due_date.isoformat() + 'T22:00:00+08:00'
    payload = json.dumps(task).encode('utf-8')
    req = urllib.request.Request(
        VIKUNJA_URL + f'/projects/{project_id}/tasks',
        data=payload, method='PUT',
        headers={'Authorization': f'Bearer {VIKUNJA_TOKEN}', 'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        log(f'Task created: {title[:40]}')
        return result
    except Exception as e:
        log(f'Task creation failed: {e}')
        return None

def fetch_all_tasks():
    all_tasks = []
    for pid, pname in PROJECTS.items():
        items = vikunja_get(f'/projects/{pid}/tasks')
        if isinstance(items, list):
            for t in items:
                t['_project'] = pname
                all_tasks.append(t)
    return all_tasks

def categorize(all_tasks, today):
    done_today, overdue, due_today, due_tomorrow = [], [], [], []
    due_this_week, due_this_month = [], []
    week_end = today + datetime.timedelta(days=6 - today.weekday())
    month_end = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)

    for t in all_tasks:
        due = parse_date(t.get('due_date', ''))
        done_at = parse_date(t.get('done_at', ''))
        if t['done']:
            if done_at and done_at.date() == today:
                done_today.append(t)
            continue
        if not due:
            continue
        d = due.date()
        if d < today:
            overdue.append(t)
        elif d == today:
            due_today.append(t)
        elif d == tomorrow:
            due_tomorrow.append(t)
        elif d <= week_end:
            due_this_week.append(t)
        elif d <= month_end:
            due_this_month.append(t)

    for lst in [overdue, due_today, due_tomorrow, due_this_week, due_this_month]:
        lst.sort(key=lambda t: t.get('due_date', ''))
    return done_today, overdue, due_today, due_tomorrow, due_this_week, due_this_month

def fmt_task(t, show_date=True):
    due = parse_date(t.get('due_date', ''))
    pri = PRIORITY_LABEL.get(min(t.get('priority', 0), 5), '')
    date_str = f" ({due.month}/{due.day} {due.hour}:{due.minute:02d})" if due and show_date else ""
    return f"- [ ] {pri} **{t['title']}**{date_str} `{t['_project']}`"

def fmt_task_time(t):
    due = parse_date(t.get('due_date', ''))
    time_str = f" ({due.hour}:{due.minute:02d}截止)" if due else ""
    return f"- [ ] **{t['title']}**{time_str} `{t['_project']}`"

# ========== Task Auto-Rebalance ==========

MAX_DAILY_TASKS = 4  # max review/study tasks per day (excludes hard DDLs)

def vikunja_update_task(task_id, data):
    """Update an existing Vikunja task"""
    payload = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(
        VIKUNJA_URL + f'/tasks/{task_id}',
        data=payload, method='POST',
        headers={'Authorization': f'Bearer {VIKUNJA_TOKEN}', 'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f'Task update failed [{task_id}]: {e}')
        return None

def auto_rebalance_tasks(now, today):
    """Auto-redistribute tasks when a day is overloaded (run before morning report)"""
    all_tasks = fetch_all_tasks()
    undone = [t for t in all_tasks if not t.get('done') and t.get('due_date', '').startswith('0') == False]

    # Group tasks by due date
    by_date = {}
    overdue_review = []
    for t in undone:
        due = parse_date(t.get('due_date', ''))
        if not due:
            continue
        d = due.date()
        if d < today and t.get('_project') == '期末复习':
            overdue_review.append(t)  # collect overdue review tasks
        elif d >= today:
            by_date.setdefault(d, []).append(t)

    moved = 0

    # First: reschedule overdue review tasks to today (they'll be rebalanced below if today is full)
    for t in overdue_review:
        by_date.setdefault(today, []).append(t)
        new_due = today.isoformat() + 'T22:00:00+08:00'
        vikunja_update_task(t.get('id'), {'due_date': new_due})
        log(f'Overdue [{t.get("id")}] {t["title"][:20]} → today')
        moved += 1

    # Then: rebalance overloaded days (only move 期末复习 tasks, not DDLs)
    for d in sorted(by_date.keys()):
        tasks_today = by_date[d]
        review_tasks = [t for t in tasks_today if t.get('_project') == '期末复习']
        ddl_tasks = [t for t in tasks_today if t.get('_project') != '期末复习']

        total = len(review_tasks) + len(ddl_tasks)
        if total <= MAX_DAILY_TASKS:
            continue

        # Sort review tasks: lowest priority first (these get moved)
        review_tasks.sort(key=lambda t: t.get('priority', 0))
        overflow = total - MAX_DAILY_TASKS
        to_move = review_tasks[:overflow]

        for t in to_move:
            new_date = d + datetime.timedelta(days=1)
            placed = False
            for attempt in range(30):  # search up to 30 days
                nd = new_date + datetime.timedelta(days=attempt)
                existing = len(by_date.get(nd, []))
                if existing < MAX_DAILY_TASKS:
                    new_due = nd.isoformat() + 'T22:00:00+08:00'
                    result = vikunja_update_task(t.get('id'), {'due_date': new_due})
                    if result:
                        by_date.setdefault(nd, []).append(t)
                        tasks_today.remove(t)
                        log(f'Rebalanced [{t.get("id")}] {t["title"][:20]} → {nd}')
                        moved += 1
                        placed = True
                    break
            if not placed:
                # Fallback: put at day 31
                nd = d + datetime.timedelta(days=31)
                vikunja_update_task(t.get('id'), {'due_date': nd.isoformat() + 'T22:00:00+08:00'})
                by_date.setdefault(nd, []).append(t)
                tasks_today.remove(t)
                log(f'Rebalanced [{t.get("id")}] {t["title"][:20]} → {nd} (fallback)')
                moved += 1

    if moved:
        log(f'Auto-rebalance: moved {moved} tasks')
        bark_push('📊 任务均衡', f'今日任务过多，已自动推迟 {moved} 项低优先级复习到后续', group='morning')
    return moved


# ========== Canvas DDL Auto-Sync ==========

def canvas_sync_to_vikunja(now, today):
    """Sync Canvas assignments to Vikunja tasks (deduped)"""
    import ssl
    CANVAS_API = 'https://oc.sjtu.edu.cn/api/v1'
    CANVAS_TOKEN = 'YOUR_CANVAS_TOKEN'

    # Fetch Canvas todos
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    canvas_items = []
    for endpoint in ['/users/self/todo?per_page=50', '/users/self/upcoming_events?per_page=30']:
        try:
            req = urllib.request.Request(
                CANVAS_API + endpoint,
                headers={'Authorization': f'Bearer {CANVAS_TOKEN}'}
            )
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            for item in data:
                assignment = item.get('assignment') or item
                name = assignment.get('name') or item.get('title', '')
                due = assignment.get('due_at') or item.get('start_at', '')
                course = ''
                if 'context_name' in item:
                    course = item['context_name']
                elif 'course_id' in assignment:
                    course = f'Course {assignment["course_id"]}'

                if not name or not due:
                    continue
                # Skip already submitted
                if item.get('type') == 'submitting' and assignment.get('has_submitted_submissions'):
                    continue

                canvas_items.append({
                    'name': name,
                    'course': course,
                    'due': due,
                })
        except Exception as e:
            log(f'Canvas fetch failed ({endpoint}): {e}')

    if not canvas_items:
        log('Canvas sync: no items found')
        return 0

    # Dedup against existing Vikunja tasks
    existing_norms, _ = load_existing_titles()
    created = 0
    for item in canvas_items:
        due_dt = parse_date(item['due'])
        if not due_dt or due_dt.date() < today:
            continue  # skip past due

        title = f'{item["course"]}: {item["name"]}'[:50] if item['course'] else item['name'][:50]
        if is_task_duplicate(title, existing_norms):
            continue

        # Create in "作业 & DDL" project (ID 3)
        result = vikunja_create_task(
            title=title,
            project_id=3,
            due_date=due_dt.date(),
            description=f'Canvas 自动同步\n截止: {due_dt.strftime("%Y-%m-%d %H:%M")}'
        )
        if result:
            existing_norms.add(normalize_title(title))
            created += 1

    if created:
        log(f'Canvas sync: created {created} new tasks')
        bark_push('📚 Canvas 同步', f'发现 {created} 个新作业/DDL，已创建任务', group='canvas')
    else:
        log('Canvas sync: no new items')
    return created


# ========== Memos Study Log (#学了) ==========

def load_study_log():
    try:
        with open(STUDY_LOG_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_study_log(data):
    with open(STUDY_LOG_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def memos_study_log(now, today):
    """Parse #学了 memos and log study time"""
    import re as _re
    today_str = today.isoformat()

    # Load processed IDs
    processed_file = '/root/.studylog-processed.json'
    try:
        with open(processed_file, 'r') as f:
            processed = set(json.load(f))
    except:
        processed = set()

    # Fetch memos
    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/memos?pageSize=50',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log(f'Study log memo fetch failed: {e}')
        return

    # Subject aliases for parsing
    SUBJ_ALIAS = {
        '高数': '高数II', '有机': '有机化学', '概统': '概率统计', '大物': '大学物理',
        '分化': '分析化学', '分析': '分析化学', '普生': '普通生物学', 'ai': 'AI基础',
        '习思想': '习思想', '物理': '大学物理', '数学': '高数II', '化学': '有机化学',
        '高数ii': '高数II', '生物': '普通生物学',
    }

    log_entries = load_study_log()
    logged = 0

    for m in data.get('memos', []):
        memo_id = m.get('name', '')
        content = m.get('content', '').strip()
        ct = m.get('createTime', '')

        if memo_id in processed or '#学了' not in content:
            continue

        # Parse: #学了 高数 45min / #学了 有机 1h / #学了 概统 30分钟
        text = content.replace('#学了', '').strip()

        # Extract duration
        duration_min = 0
        dur_match = _re.search(r'(\d+)\s*(min|分钟|分|m)', text, _re.IGNORECASE)
        if dur_match:
            duration_min = int(dur_match.group(1))
        else:
            hr_match = _re.search(r'(\d+(?:\.\d+)?)\s*(h|小时|hr)', text, _re.IGNORECASE)
            if hr_match:
                duration_min = int(float(hr_match.group(1)) * 60)

        if duration_min <= 0:
            duration_min = 30  # default 30min if not specified

        # Extract subject
        subject = '其他'
        text_lower = text.lower()
        for alias, full in SUBJ_ALIAS.items():
            if alias in text_lower:
                subject = full
                break

        # Extract optional note (everything after duration)
        note = _re.sub(r'\d+\s*(min|分钟|分|m|h|小时|hr)\s*', '', text, flags=_re.IGNORECASE).strip()
        for alias in SUBJ_ALIAS:
            note = note.replace(alias, '').strip()
        note = note.strip('，。, ')

        # Parse time
        try:
            ts = datetime.datetime.fromisoformat(ct.replace('Z', '+00:00')).astimezone(TZ)
            date_str = ts.date().isoformat()
        except:
            date_str = today_str

        entry = {
            'date': date_str,
            'subject': subject,
            'duration': duration_min,
            'note': note[:60] if note else '',
            'memo_id': memo_id,
            'timestamp': ct,
        }
        log_entries.append(entry)
        processed.add(memo_id)
        logged += 1

        # Acknowledge in memo
        ack = f'\n\n---\n✅ 已记录：{subject} {duration_min}min'
        new_content = content + ack
        try:
            update_payload = json.dumps({'content': new_content}).encode('utf-8')
            update_req = urllib.request.Request(
                f'{MEMOS_API}/{memo_id}',
                data=update_payload, method='PATCH',
                headers={'Authorization': f'Bearer {MEMOS_TOKEN}', 'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(update_req, timeout=10) as resp:
                resp.read()
        except:
            pass

    if logged:
        save_study_log(log_entries)
        with open(processed_file, 'w') as f:
            json.dump(list(processed)[-500:], f)
        log(f'Study log: recorded {logged} entries')

    return logged


def get_study_summary(date_str):
    """Get study time summary for a specific date"""
    log_entries = load_study_log()
    day_entries = [e for e in log_entries if e.get('date') == date_str]
    if not day_entries:
        return None

    by_subject = {}
    for e in day_entries:
        subj = e.get('subject', '其他')
        by_subject[subj] = by_subject.get(subj, 0) + e.get('duration', 0)

    total = sum(by_subject.values())
    return {'by_subject': by_subject, 'total': total, 'count': len(day_entries)}


# ========== Morning Report ==========

def morning_report(now, today):
    wd = WEEKDAY_NAMES[today.weekday()]
    days_left = (EXAM_DATE - today).days
    week_start = today - datetime.timedelta(days=today.weekday())
    week_end = week_start + datetime.timedelta(days=6)

    # Auto-rebalance before generating report
    auto_rebalance_tasks(now, today)

    # Canvas sync
    canvas_sync_to_vikunja(now, today)

    # Process study logs from Memos
    memos_study_log(now, today)

    # fetch weather
    weather_str = ''
    try:
        from news_fetcher import fetch_weather
        w = fetch_weather()
        if w:
            weather_str = f' | {w["icon"]} {w["temp"]}°C {w["desc"]}'
    except Exception:
        pass

    all_tasks = fetch_all_tasks()
    _, overdue, due_today, due_tomorrow, due_this_week, _ = categorize(all_tasks, today)

    md = f"""---
created: {today}
tags: [早报]
---

# ☀️ 早报

> 📅 {today} {wd} | 距期末 **{days_left}** 天{weather_str} | {now.strftime('%H:%M')}

"""
    # Yesterday's study summary
    yesterday = today - datetime.timedelta(days=1)
    study = get_study_summary(yesterday.isoformat())
    if study:
        md += "### 📖 昨日学习\n\n"
        md += f"> 总计 **{study['total']}** 分钟 ({study['count']}次打卡)\n\n"
        for subj, mins in sorted(study['by_subject'].items(), key=lambda x: -x[1]):
            bar = '█' * (mins // 15) + '░' * max(0, 4 - mins // 15)
            md += f"- {subj}: {mins}min {bar}\n"
        md += "\n"

    # Exam countdown table
    md += "### 📊 期末冲刺\n\n"
    md += "| 科目 | 学分 | 倒计时 | 目标 | 期末需 |\n"
    md += "|------|------|--------|------|--------|\n"
    for name, info in COURSE_EXAMS.items():
        exam_d = info['exam'] or EXAM_DATE
        dleft = (exam_d - today).days
        target = info['target']
        # calculate required final score if midterm is known
        if info['known'] is not None and info['type'] == 'midterm':
            # assume 40% midterm + 60% final
            needed = max(0, (target - 0.4 * info['known']) / 0.6)
            needed_str = f"{needed:.0f}" if needed <= 100 else "❌>100"
        else:
            needed_str = f"≥{target}"
        md += f"| {name} | {info['credit']} | **{dleft}**天 | {target} | {needed_str} |\n"
    md += "\n"

    # AI study advice based on yesterday's unmastered items + today's schedule
    try:
        yesterday = today - datetime.timedelta(days=1)
        review_path = os.path.join(NOTE_DIR, str(yesterday), '学习回顾.md')
        if os.path.exists(review_path):
            with open(review_path, 'r', encoding='utf-8') as f:
                review_content = f.read()
            # extract unmastered section
            import re as _re
            unmastered = ''
            in_sec = False
            for line in review_content.split('\n'):
                if ('未掌握' in line) and '###' in line:
                    in_sec = True
                    continue
                if in_sec and line.startswith('###'):
                    break
                if in_sec:
                    unmastered += line + '\n'
            if unmastered.strip():
                # get today's schedule from SCHEDULE (in chat-server, but we have COURSE_EXAMS)
                today_tasks_str = ', '.join(t['title'] for t in (due_today + due_tomorrow)[:5])
                advice_prompt = f"""你是学习顾问。根据以下信息，给出2-3句简短的今日学习建议：

昨日未掌握的知识点：
{unmastered[:800]}

今日待办任务：{today_tasks_str}
今天是{wd}，距期末{days_left}天。

要求：简洁实用，2-3句话，指出今天应该优先补什么、怎么补。不要用标题格式。"""
                payload = json.dumps({
                    'model': AI_MODEL, 'messages': [{'role': 'user', 'content': advice_prompt}],
                    'max_tokens': 200, 'temperature': 0.7
                }).encode('utf-8')
                req = urllib.request.Request(AI_API_URL, data=payload, headers={
                    'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                advice = result['choices'][0]['message']['content'].strip()
                md += f"### 💡 今日复习建议\n\n> {advice}\n\n"
                log(f'Morning advice generated')
    except Exception as e:
        log(f'Morning advice skipped: {e}')

    if overdue:
        md += f"### ⚠️ 已过期 ({len(overdue)})\n\n"
        for t in overdue:
            due = parse_date(t['due_date'])
            days = (today - due.date()).days
            md += f"- [ ] 🟥 **{t['title']}** (过期{days}天) `{t['_project']}`\n"
        md += "\n"

    if due_today:
        md += f"### ⏰ 今日必做 ({len(due_today)})\n\n"
        for t in due_today:
            md += fmt_task_time(t) + "\n"
        md += "\n"
    else:
        md += "### ⏰ 今日无截止任务\n\n轻松一天，主动推进复习计划吧！\n\n"

    if due_tomorrow:
        md += f"### 📋 明日截止 ({len(due_tomorrow)})\n\n"
        for t in due_tomorrow:
            md += fmt_task(t, show_date=False) + "\n"
        md += "\n"

    if due_this_week:
        md += f"### 📅 本周剩余 ({week_start.month}/{week_start.day}-{week_end.month}/{week_end.day})\n\n"
        for t in due_this_week:
            md += fmt_task(t) + "\n"
        md += "\n"

    # Usage tips (rotate daily)
    tips = [
        '💡 在 Memos 发 `#学了 高数 45min` 记录学习时间，早晚报会展示统计',
        '💡 在 Memos 发 `#查 格林公式` 可以 3 秒检索笔记，不用开电脑',
        '💡 试试 [模拟考试](https://kb.xpy.me/apps/exam.html)，看看你现在能考多少分',
        '💡 打开 [弱点分析](https://kb.xpy.me/apps/weakness.html) 点"覆盖度检查"，看哪些章节没复习过',
        '💡 每科的《期末冲刺精华》已生成，在课程文件夹里，考前翻这一份就够',
        '💡 试试 [AI 题库](https://kb.xpy.me/apps/problems.html) 的复习模式，从 Gemini 对话中自动提取的题目',
        '💡 打开 [学习报告](https://kb.xpy.me/apps/report.html) 看看各科数据可视化',
    ]
    tip_idx = (today - datetime.date(2026, 1, 1)).days % len(tips)
    md += f"\n---\n\n{tips[tip_idx]}\n\n"

    # Bark
    title = f'☀️ 早报 | {wd} | 距期末{days_left}天'
    parts = []
    if overdue: parts.append(f'⚠️过期{len(overdue)}')
    if due_today: parts.append(f'⏰今天{len(due_today)}')
    if due_tomorrow: parts.append(f'📋明天{len(due_tomorrow)}')
    if due_this_week: parts.append(f'📅本周{len(due_this_week)}')
    bark_push(title, ' | '.join(parts) if parts else '今日无紧急任务', group='morning')

    write_report(today, '早报', md)

# ========== Evening Report ==========

def evening_report(now, today):
    wd = WEEKDAY_NAMES[today.weekday()]
    days_left = (EXAM_DATE - today).days
    tomorrow = today + datetime.timedelta(days=1)
    tmr_wd = WEEKDAY_NAMES[tomorrow.weekday()]

    all_tasks = fetch_all_tasks()
    _, overdue, due_today, due_tomorrow, due_this_week, due_this_month = categorize(all_tasks, today)

    all_done = [t for t in all_tasks if t['done'] and parse_date(t.get('done_at', '')) and parse_date(t['done_at']).date() == today]
    undone_today = [t for t in due_today if not t['done']]

    md = f"""---
created: {today}
tags: [晚报]
---

# 🌙 晚报

> 📅 {today} {wd} | 距期末 **{days_left}** 天 | {now.strftime('%H:%M')}

"""
    if all_done:
        md += f"### ✅ 今日完成 ({len(all_done)})\n\n"
        for t in all_done:
            md += f"- [x] ~~{t['title']}~~ `{t['_project']}`\n"
        md += "\n"
    else:
        md += "### ✅ 今日完成\n\n今天没有完成任务，明天加油！\n\n"

    if undone_today:
        md += f"### ❌ 今日未完成 ({len(undone_today)})\n\n"
        for t in undone_today:
            md += f"- [ ] 🟥 **{t['title']}** `{t['_project']}`\n"
        md += "\n"

    if overdue:
        md += f"### ⚠️ 累计过期 ({len(overdue)})\n\n"
        for t in overdue:
            due = parse_date(t['due_date'])
            days = (today - due.date()).days
            md += f"- [ ] 🟥 **{t['title']}** (过期{days}天) `{t['_project']}`\n"
        md += "\n"

    md += f"### 🔮 明日预告 ({tomorrow.month}/{tomorrow.day} {tmr_wd})\n\n"
    if due_tomorrow:
        for t in due_tomorrow:
            md += fmt_task(t, show_date=False) + "\n"
    else:
        md += "明天暂无截止任务。\n"
    md += "\n"

    if due_this_week:
        md += f"### 📅 本周剩余 ({len(due_this_week)})\n\n"
        for t in due_this_week:
            md += fmt_task(t) + "\n"
        md += "\n"

    if due_this_month:
        md += f"### 📆 本月剩余 ({len(due_this_month)})\n\n"
        for t in due_this_month:
            md += fmt_task(t) + "\n"
        md += "\n"

    # Today's study log with weekly comparison
    memos_study_log(now, today)  # process any new #学了 memos
    study = get_study_summary(today.isoformat())
    if study:
        md += "### 📖 今日学习记录\n\n"
        # Weekly comparison
        last_week_same_day = (today - datetime.timedelta(days=7)).isoformat()
        lw_study = get_study_summary(last_week_same_day)
        if lw_study:
            diff = study['total'] - lw_study['total']
            arrow = '📈' if diff > 0 else ('📉' if diff < 0 else '➡️')
            md += f"> 总计 **{study['total']}** 分钟 ({study['count']}次) {arrow} 上周同日 {lw_study['total']}min ({'+' if diff >= 0 else ''}{diff}min)\n\n"
        else:
            md += f"> 总计 **{study['total']}** 分钟 ({study['count']}次打卡)\n\n"
        for subj, mins in sorted(study['by_subject'].items(), key=lambda x: -x[1]):
            bar = '█' * (mins // 15) + '░' * max(0, 4 - mins // 15)
            md += f"- {subj}: {mins}min {bar}\n"
        md += "\n"

    total = len(overdue) + len(undone_today) + len(due_tomorrow) + len(due_this_week) + len(due_this_month)
    md += f"""### 📊 统计

| | 数量 |
|------|------|
| 今日完成 | {len(all_done)} |
| 今日未完成 | {len(undone_today)} |
| 已过期 | {len(overdue)} |
| 明日截止 | {len(due_tomorrow)} |
| 本周剩余 | {len(due_this_week)} |
| 本月剩余 | {len(due_this_month)} |
| **总待办** | **{total}** |

"""

    # News brief
    try:
        from news_fetcher import get_daily_news, format_evening_brief
        news_data = get_daily_news(today.isoformat())
        if news_data:
            md += format_evening_brief(news_data)
    except Exception as e:
        log(f'News section skipped: {e}')

    # Bark
    title = f'🌙 晚报 | {wd} | 距期末{days_left}天'
    parts = []
    if all_done: parts.append(f'✅完成{len(all_done)}')
    if undone_today: parts.append(f'❌未完成{len(undone_today)}')
    if due_tomorrow: parts.append(f'📋明天{len(due_tomorrow)}')
    if overdue: parts.append(f'⚠️过期{len(overdue)}')
    bark_push(title, ' | '.join(parts) if parts else '今日平安', group='evening')

    write_report(today, '晚报', md)

# ========== AI Summary ==========

def fetch_today_memos(today_str):
    """Fetch today's memos from Memos API"""
    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/memos?pageSize=50',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        memos = []
        for m in data.get('memos', []):
            ct = m.get('createTime', '')
            if not ct.startswith(today_str):
                # Memos uses UTC, convert check: also accept yesterday UTC = today CST
                continue
            content = m.get('content', '').strip()
            if not content or content in ('pending', '#test'):
                continue
            attachments = []
            for att in m.get('attachments', []):
                if att.get('type', '').startswith('image/'):
                    attachments.append(att)
            memos.append({
                'content': content,
                'time': ct,
                'attachments': attachments,
            })
        log(f'Memos: {len(memos)} entries today')
        return memos
    except Exception as e:
        log(f'Memos fetch failed: {e}')
        return []

def recognize_memo_images(memos_list):
    """Use qwen Vision to recognize images in memos"""
    import base64
    for memo in memos_list:
        if not memo.get('attachments'):
            continue
        for att in memo['attachments']:
            att_name = att.get('name', '')
            # Download image from Memos
            try:
                img_url = f'{MEMOS_API}/{att_name}'
                img_req = urllib.request.Request(img_url, headers={'Authorization': f'Bearer {MEMOS_TOKEN}'})
                with urllib.request.urlopen(img_req, timeout=30) as resp:
                    img_data = resp.read()
                img_b64 = base64.b64encode(img_data).decode('utf-8')
                mime = att.get('type', 'image/jpeg')

                # Call qwen Vision
                payload = json.dumps({
                    'model': VISION_MODEL,
                    'messages': [{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': '请详细描述这张课件/板书/笔记的内容。提取所有文字、公式（用LaTeX格式）和图表信息。'},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                        ]
                    }],
                    'max_tokens': 1000
                }).encode('utf-8')
                ai_req = urllib.request.Request(AI_API_URL, data=payload, headers={
                    'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
                })
                with urllib.request.urlopen(ai_req, timeout=60) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                ocr_text = result['choices'][0]['message']['content'].strip()
                memo['content'] += f'\n\n[图片识别内容]\n{ocr_text}'
                log(f'Image recognized: {att_name[:30]}')
            except Exception as e:
                log(f'Image recognition failed: {e}')
    return memos_list

def ai_summary(now, today):
    """Generate daily learning review from Memos + AI notes"""
    today_str = today.isoformat()

    # --- 1. Fetch Memos classroom notes ---
    memos_raw = fetch_today_memos(today_str)
    # Also check UTC dates that map to today in CST (UTC+8)
    yesterday_utc = (today - datetime.timedelta(days=1)).isoformat()
    for m in fetch_today_memos(yesterday_utc):
        # check if UTC time falls into today CST
        try:
            utc_time = datetime.datetime.fromisoformat(m['time'].replace('Z', '+00:00'))
            cst_time = utc_time.astimezone(TZ)
            if cst_time.date() == today:
                if m not in memos_raw:
                    memos_raw.append(m)
        except:
            pass

    # Recognize images in memos
    if memos_raw:
        memos_raw = recognize_memo_images(memos_raw)

    memos_content = ""
    if memos_raw:
        memos_content = '\n'.join(f"- [{m['time'][11:16]}] {m['content']}" for m in memos_raw)

    # --- 2. Scan AI notes (Gemini) ---
    notes = []
    for root, dirs, files in os.walk(AI_DIR):
        for fname in files:
            if fname.startswith(today_str) and fname.endswith('.md'):
                fpath = os.path.join(root, fname)
                subject = os.path.basename(root)
                with open(fpath, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                title = fname.replace('.md', '').replace(f'{today_str}_', '')
                rel_path = os.path.relpath(fpath, VAULT_DIR).replace('\\', '/').replace('.md', '')
                parts = file_content.split('---')
                body = '---'.join(parts[2:]) if len(parts) >= 3 else file_content
                if len(body) > 2000:
                    body = body[:2000] + '\n...(truncated)'
                notes.append({'subject': subject, 'title': title, 'body': body.strip(), 'link': rel_path})

    if not notes and not memos_raw:
        log('No AI notes or memos found today, skipping summary')
        return

    log(f'Summary sources: {len(notes)} AI notes, {len(memos_raw)} memos')

    # --- 3. Get course notes for linking ---
    course_notes = {}
    for root, dirs, files in os.walk(COURSE_DIR):
        for fname in files:
            if fname.endswith('.md'):
                rel = os.path.relpath(os.path.join(root, fname), VAULT_DIR)
                link_path = rel.replace('.md', '').replace('\\', '/')
                subject_dir = os.path.basename(os.path.dirname(os.path.join(root, fname)))
                if subject_dir not in course_notes:
                    course_notes[subject_dir] = []
                course_notes[subject_dir].append(link_path)

    note_links_by_subject = {}
    for n in notes:
        note_links_by_subject.setdefault(n['subject'], []).append(n)

    course_links_info = ""
    course_style_samples = ""
    all_subjects = set(n['subject'] for n in notes)
    for subj in all_subjects:
        folder = SUBJECT_MAP.get(subj, subj)
        if folder in course_notes:
            course_links_info += f"\n{subj} 相关课程笔记: " + ", ".join(course_notes[folder])
            # Read first 50 lines of main course note for style reference
            for note_path in course_notes[folder]:
                if any(k in note_path for k in ['反应总结', '期末复习', '公式速查']):
                    full_path = os.path.join(VAULT_DIR, note_path + '.md')
                    if os.path.exists(full_path):
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                lines = f.readlines()[:20]
                            course_style_samples += f"\n===== [{subj}] {os.path.basename(note_path)} 格式参考 =====\n{''.join(lines)}\n"
                        except:
                            pass
                    break

    # --- 4. Build AI prompt ---
    ai_notes_block = ""
    if notes:
        ai_notes_block = '\n'.join(f"===== [{n['subject']}] {n['title']} =====\n{n['body']}\n" for n in notes)

    user_content = ""
    if memos_content:
        user_content += f"【课堂笔记】（用户课间随手记录）\n{memos_content}\n\n"
    if ai_notes_block:
        user_content += f"【AI学习笔记】（Gemini对话，较详细）\n{ai_notes_block}\n"

    style_ref = ""
    if course_style_samples:
        style_ref = f"""

以下是用户现有课程笔记的格式参考（前50行），请在"建议补充"部分严格模仿这个格式：
{course_style_samples}"""

    system_prompt = f"""你是学习回顾助手。以下是用户今天的学习记录，包括课堂随手笔记和AI对话笔记。
请生成一份详细的每日学习回顾。

用户的 Obsidian vault 中有以下课程笔记可供链接：
{course_links_info}
{style_ref}

要求输出以下板块：

### 今日学习概览
- 今天上了哪些课、学了什么
- 课堂笔记和AI笔记覆盖了几个科目

### 知识点详细回顾
按科目分组。对每个知识点：
- 核心概念用1-2句话精确总结
- 保留关键公式（用LaTeX $...$格式）
- 标注掌握程度：✅已掌握 / ⚠️需巩固 / ❌未掌握（根据用户笔记中的措辞判断，如"没搞懂""有点难""不理解"→❌或⚠️）
- 用 > 引用块链接相关课程笔记（[[路径]]格式）

### ❌ 未掌握清单
把所有❌和⚠️的知识点汇总成表格：
| 知识点 | 科目 | 问题 | 建议 | 预估时间 |

### 明日行动
基于未掌握内容，列出明天具体的复习任务（用 - [ ] 格式）。
每条任务必须独立成一行，格式为"- [ ] 科目：简短任务描述（预估时间）"，不要换行拆分。最多4条。

### 📝 建议补充到课程笔记
按科目分组，把今天学到的新知识点整理成可以直接复制粘贴到对应课程笔记的格式。
严格模仿上面提供的课程笔记格式参考。每个知识点包括：
- 反应/定理/公式的标准写法
- 机理要点或证明思路
- 关键注意事项
在每个科目块的开头标注目标文件，如：`→ 复制到 [[课程/有机化学/反应总结]]`

只输出markdown，不要用代码块包裹。用 ### 三级标题。
链接格式示例：[[课程/高数II/ch10_曲线曲面积分]]"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_content}],
        'max_tokens': 3000
    }).encode('utf-8')

    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
    })

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        summary = result['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f'AI summary failed: {e}')
        return

    if summary.startswith('```'):
        summary = '\n'.join(summary.split('\n')[1:])
    if summary.endswith('```'):
        summary = '\n'.join(summary.split('\n')[:-1])

    # --- 5. Build report ---
    subjects = ', '.join(sorted(all_subjects)) if all_subjects else '课堂笔记'
    note_index = ""
    if note_links_by_subject:
        for subj, subj_notes in note_links_by_subject.items():
            note_index += f"#### {subj}\n"
            for n in subj_notes:
                note_index += f"- [[{n['link']}|{n['title']}]]\n"
            folder = SUBJECT_MAP.get(subj, subj)
            if folder in course_notes:
                related = [p for p in course_notes[folder] if any(k in p for k in ['期末复习', '公式速查', '闪卡', '典型题'])]
                if related:
                    note_index += "  相关: " + " · ".join(f"[[{r}]]" for r in related[:4]) + "\n"
            note_index += "\n"

    memo_count = len(memos_raw)
    note_count = len(notes)
    src_parts = []
    if memo_count: src_parts.append(f'课堂笔记 {memo_count} 条')
    if note_count: src_parts.append(f'AI 笔记 {note_count} 篇')
    src_str = ' · '.join(src_parts)

    md = f"""---
created: {today}
tags: [学习回顾]
---

# 📖 学习回顾

> 📅 {today} {WEEKDAY_NAMES[today.weekday()]} | {src_str} | {now.strftime('%H:%M')}

"""
    if note_index:
        md += f"## 今日笔记索引\n\n{note_index}\n"

    md += summary + "\n"

    write_report(today, '学习回顾', md)

    # --- 6. Auto-create "明日行动" as Vikunja tasks (with improved dedup) ---
    import re as _re
    tomorrow = today + datetime.timedelta(days=1)
    existing_norms, _ = load_existing_titles()
    in_action = False
    created_count = 0
    for line in summary.split('\n'):
        if '明日行动' in line and '###' in line:
            in_action = True
            continue
        if in_action and line.startswith('###'):
            break
        if in_action and _re.match(r'^- \[ \]', line.strip()):
            task_text = _re.sub(r'^- \[ \]\s*', '', line.strip())
            # strip markdown formatting
            task_text = _re.sub(r'\*\*(.+?)\*\*', r'\1', task_text)
            task_text = _re.sub(r'\[\[.+?\]\]', '', task_text).strip().rstrip('。，')
            if not task_text or len(task_text) < 8:
                continue
            # dedup: skip if similar task already exists (fuzzy match)
            if is_task_duplicate(task_text, existing_norms):
                log(f'Task skipped (dup): {task_text[:30]}')
                continue
            # split: "科目：短标题" as title, full text as description
            if len(task_text) > 30:
                # find a good cut point
                for sep in ['，', '；', ',', '。']:
                    pos = task_text.find(sep, 5)
                    if 10 < pos < 35:
                        title = task_text[:pos]
                        break
                else:
                    title = task_text[:25]
                desc = task_text
            else:
                title = task_text
                desc = ''
            vikunja_create_task(title, project_id=2, due_date=tomorrow, description=desc)
            created_count += 1
    if created_count:
        log(f'Created {created_count} review tasks for tomorrow')

    # --- 7. Evaluate daily challenge if user replied ---
    proactive_review_evaluate(now, today)

    # Save key points for bedtime review push
    try:
        bedtime_points = []
        for line in summary.split('\n'):
            line_s = line.strip()
            for marker in ['❌', '⚠️']:
                if marker in line_s and len(line_s) > 8:
                    bedtime_points.append(line_s[:80])
                    break
        if bedtime_points:
            with open('/root/.bedtime-review.json', 'w') as f:
                json.dump({'date': today.isoformat(), 'points': bedtime_points[:5]}, f, ensure_ascii=False)
    except:
        pass

    log(f'Learning review done ({src_str})')


# ========== Weekly Report ==========

def load_weaknesses():
    try:
        with open(WEAKNESS_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_weaknesses(data):
    with open(WEAKNESS_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def weekly_report(now, today):
    import re as re_mod
    days_left = (EXAM_DATE - today).days
    week_start = today - datetime.timedelta(days=today.weekday())
    week_end = week_start + datetime.timedelta(days=6)
    week_num = today.isocalendar()[1]

    weekly_subjects = set()
    weekly_weaknesses = []
    focus_total = 0

    for d in range(7):
        day = week_start + datetime.timedelta(days=d)

        # Scan AI学习汇总 for subjects and weaknesses
        summary_path = os.path.join(NOTE_DIR, str(day), '学习回顾.md')
        if os.path.exists(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as f:
                content = f.read()
            m = re_mod.search(r"科目: (.+?)\s*\|", content)
            if m:
                for s in m.group(1).split(', '):
                    weekly_subjects.add(s.strip())
            in_weak = False
            for line in content.split('\n'):
                if ('未掌握' in line or '薄弱点' in line) and '###' in line:
                    in_weak = True
                    continue
                if in_weak and line.startswith('###'):
                    break
                if in_weak and line.strip().startswith(('|', '-', '*')) and '---' not in line and '知识点' not in line:
                    # extract from table rows: | 知识点 | 科目 | 问题 | ...
                    cells = [c.strip() for c in line.split('|') if c.strip()]
                    if len(cells) >= 3:
                        text = f"{cells[0]}({cells[1]}): {cells[2]}"
                    else:
                        text = line.strip().lstrip('-* ').strip()
                    if len(text) > 10:
                        weekly_weaknesses.append({'text': text[:150], 'date': day.isoformat()})

        # Focus stats removed (feature deprecated)

    tracker = load_weaknesses()
    existing = {w['text'][:50] for w in tracker}
    for w in weekly_weaknesses:
        if w['text'][:50] not in existing:
            tracker.append({'text': w['text'], 'found_date': w['date'], 'status': 'open', 'week': week_num})
    save_weaknesses(tracker)

    all_tasks = fetch_all_tasks()
    done_week = [t for t in all_tasks if t['done'] and parse_date(t.get('done_at', ''))
                 and week_start <= parse_date(t['done_at']).date() <= week_end]

    open_weak = [w for w in tracker if w['status'] == 'open']
    addressed_weak = [w for w in tracker if w['status'] == 'addressed']

    md = f'---\ncreated: {today}\ntags: [\u5468\u62a5]\n---\n\n'
    md += f'# \u5468\u62a5 W{week_num} ({week_start.month}/{week_start.day}-{week_end.month}/{week_end.day})\n\n'
    md += f'> \u8ddd\u671f\u672b **{days_left}** \u5929 | {now.strftime("%H:%M")}\n\n'
    md += f'## \u672c\u5468\u6982\u89c8\n\n'
    md += f'- \u5b66\u4e60\u79d1\u76ee: {", ".join(sorted(weekly_subjects)) if weekly_subjects else "\u65e0 AI \u7b14\u8bb0"}\n'
    md += f'- \u5b8c\u6210\u4efb\u52a1: {len(done_week)} \u9879\n'
    md += f'- \u4e13\u6ce8\u65f6\u95f4: {focus_total} \u5206\u949f\n'
    md += f'- \u65b0\u589e薄弱点: {len(weekly_weaknesses)} \u4e2a\n\n'

    if done_week:
        md += f'## \u2705 \u672c\u5468\u5b8c\u6210 ({len(done_week)})\n\n'
        for t in done_week:
            md += f'- ~~{t["title"]}~~ `{t["_project"]}`\n'
        md += '\n'

    if open_weak:
        md += f'## \u26a0\ufe0f \u5f85\u590d\u4e60薄弱点 ({len(open_weak)})\n\n'
        for w in open_weak:
            md += f'- \u274c {w["text"]} *(\u53d1\u73b0\u4e8e {w["found_date"]})*\n'
        md += '\n'

    if addressed_weak:
        md += f'## \u2705 \u5df2\u590d\u4e60 ({len(addressed_weak)})\n\n'
        for w in addressed_weak[-10:]:
            md += f'- \u2705 ~~{w["text"][:80]}~~\n'
        md += '\n'

    week_dir = os.path.join(NOTE_DIR, str(today))
    os.makedirs(week_dir, exist_ok=True)
    note_path = os.path.join(week_dir, f'\u5468\u62a5-W{week_num}.md')
    with open(note_path, 'w', encoding='utf-8') as f:
        f.write(md)
    subprocess.run(['chown', '-R', 'www-data:www-data', NOTE_DIR], capture_output=True)
    log(f'Weekly: W{week_num}, {len(weekly_subjects)} subjects, {len(open_weak)} weak')

    title = f'\U0001f4ca \u5468\u62a5 W{week_num} | \u8ddd\u671f\u672b{days_left}\u5929'
    parts = []
    if weekly_subjects: parts.append(f'{len(weekly_subjects)}\u79d1')
    if done_week: parts.append(f'\u2705{len(done_week)}')
    if focus_total: parts.append(f'\U0001f345{focus_total}min')
    if open_weak: parts.append(f'\u26a0\ufe0f{len(open_weak)}\u8584\u5f31')
    bark_push(title, ' | '.join(parts) if parts else 'no data', group='weekly')


# ========== AI Quiz Generator ==========

QUIZ_PROCESSED_FILE = "/root/.quiz-processed.json"

def load_quiz_processed():
    try:
        with open(QUIZ_PROCESSED_FILE, 'r') as f:
            return set(json.load(f))
    except:
        return set()

def save_quiz_processed(processed):
    # Keep only latest 500 IDs to prevent infinite growth
    data = list(processed)[-500:]
    with open(QUIZ_PROCESSED_FILE, 'w') as f:
        json.dump(data, f)

def quiz_generator(now, today):
    """Scan Memos for #出题 requests, generate practice problems"""
    today_str = today.isoformat()
    processed = load_quiz_processed()

    # fetch all memos and find #出题 requests not yet processed
    memos_list = []
    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/memos?pageSize=50',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        for m in data.get('memos', []):
            memo_id = m.get('name', '')
            content = m.get('content', '')
            if '出题' in content and memo_id not in processed:
                memos_list.append({'id': memo_id, 'content': content})
    except Exception as e:
        log(f'Quiz memo fetch failed: {e}')

    if not memos_list:
        log('No new quiz requests found')
        return

    for memo in memos_list:
        content = memo['content'].replace('#出题', '').replace('#', '').strip()
        if len(content) < 3:
            processed.add(memo['id'])
            continue

        log(f'Generating quiz: {content[:40]}')

        # Read related course notes for context
        course_context = ''
        for subj, folder in SUBJECT_MAP.items():
            if subj in content or folder in content:
                for fname in ['期末复习.md', '公式速查表.md', '典型题型与解法.md']:
                    fpath = os.path.join(COURSE_DIR, folder, fname)
                    if os.path.exists(fpath):
                        with open(fpath, 'r', encoding='utf-8') as f:
                            lines = f.readlines()[:15]
                        course_context += f'\n参考 [{fname}]:\n{"".join(lines)}\n'
                        break
                break

        prompt = f"""你是大学课程出题老师。请根据以下要求出3道练习题。

要求：{content}
{course_context}

规则：
1. 出3道题，难度从易到难（基础→中等→挑战）
2. 每道题格式：题号、题目、空行、然后用折叠块给出详细解答
3. 计算题要有完整步骤，证明题要逻辑严密
4. 公式用 LaTeX $...$ 格式
5. 解答用 Obsidian 折叠语法：
   > [!tip]- 解答
   > 详细步骤...

只输出题目和解答，不要多余的说明。"""

        payload = json.dumps({
            'model': AI_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 2000, 'temperature': 0.5
        }).encode('utf-8')
        req = urllib.request.Request(AI_API_URL, data=payload, headers={
            'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
        })

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            quiz_content = result['choices'][0]['message']['content'].strip()
            if quiz_content.startswith('```'):
                quiz_content = '\n'.join(quiz_content.split('\n')[1:])
            if quiz_content.endswith('```'):
                quiz_content = '\n'.join(quiz_content.split('\n')[:-1])

            # Save to vault
            quiz_md = f"""---
created: {today}
tags: [练习题]
---

# 🎯 练习题：{content[:30]}

> 📅 {today} | AI 自动出题

{quiz_content}
"""
            safe_name = content[:20].replace('/', '_').replace(' ', '_')
            day_dir = os.path.join(NOTE_DIR, today_str)
            os.makedirs(day_dir, exist_ok=True)
            quiz_path = os.path.join(day_dir, f'练习_{safe_name}.md')
            with open(quiz_path, 'w', encoding='utf-8') as f:
                f.write(quiz_md)
            subprocess.run(['chown', '-R', 'www-data:www-data', NOTE_DIR], capture_output=True)

            # Push notification
            bark_push(f'🎯 练习题已生成', f'{content[:30]} — 3道题已就绪', group='quiz')

            log(f'Quiz generated: {quiz_path}')
            processed.add(memo['id'])
        except Exception as e:
            log(f'Quiz generation failed: {e}')

    save_quiz_processed(processed)

# ========== AI Quick Q&A via Memos ==========

QA_PROCESSED_FILE = "/root/.qa-processed.json"

def load_qa_processed():
    try:
        with open(QA_PROCESSED_FILE, 'r') as f:
            return set(json.load(f))
    except:
        return set()

def save_qa_processed(processed):
    data = list(processed)[-500:]
    with open(QA_PROCESSED_FILE, 'w') as f:
        json.dump(data, f)

def memos_qa(now, today):
    """Scan Memos for #问 questions, answer and append to the memo"""
    processed = load_qa_processed()

    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/memos?pageSize=30',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log(f'QA memo fetch failed: {e}')
        return

    answered = 0
    for m in data.get('memos', []):
        memo_id = m.get('name', '')
        content = m.get('content', '')
        if '#问' not in content or memo_id in processed:
            continue
        # skip if already answered (contains AI回复 marker)
        if '🤖' in content:
            processed.add(memo_id)
            continue

        question = content.replace('#问', '').strip()
        if len(question) < 3:
            processed.add(memo_id)
            continue

        log(f'Answering: {question[:40]}')

        prompt = f"""你是一个大学学习助手，用户是上海交大大一生科院学生。请简洁准确地回答以下问题。
要求：
- 直接回答，不要客套
- 关键公式用 LaTeX
- 如果是概念辨析，用对比表格
- 控制在 200 字以内

问题：{question}"""

        payload = json.dumps({
            'model': AI_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 500, 'temperature': 0.3
        }).encode('utf-8')
        ai_req = urllib.request.Request(AI_API_URL, data=payload, headers={
            'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
        })

        try:
            with urllib.request.urlopen(ai_req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            answer = result['choices'][0]['message']['content'].strip()
            if answer.startswith('```'):
                answer = '\n'.join(answer.split('\n')[1:])
            if answer.endswith('```'):
                answer = '\n'.join(answer.split('\n')[:-1])

            # Append answer to the original memo
            new_content = f'{content}\n\n---\n🤖 **AI 回答**\n\n{answer}'
            update_payload = json.dumps({'content': new_content}).encode('utf-8')
            update_req = urllib.request.Request(
                f'{MEMOS_API}/{memo_id}',
                data=update_payload, method='PATCH',
                headers={'Authorization': f'Bearer {MEMOS_TOKEN}', 'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(update_req, timeout=10) as resp:
                resp.read()

            bark_push('💬 问题已回答', f'{question[:30]}', group='quiz')
            log(f'QA answered: {question[:30]}')
            processed.add(memo_id)
            answered += 1
        except Exception as e:
            log(f'QA failed: {e}')

    save_qa_processed(processed)
    if answered:
        log(f'Answered {answered} questions')

# ========== Spaced Repetition for Unmastered Items ==========

def spaced_repetition(now, today):
    """Resurface unmastered items at increasing intervals"""
    today_str = today.isoformat()

    # Load spaced repetition state
    try:
        with open(SPACED_FILE, 'r') as f:
            items = json.load(f)
    except:
        items = []

    # Scan recent 3 days' 学习回顾 for new unmastered items
    import re as _re
    existing_texts = {item['text'][:30] for item in items}
    for delta in range(1, 4):
        scan_day = today - datetime.timedelta(days=delta)
        review_path = os.path.join(NOTE_DIR, str(scan_day), '学习回顾.md')
        if not os.path.exists(review_path):
            continue
        with open(review_path, 'r', encoding='utf-8') as f:
            content = f.read()
        in_table = False
        for line in content.split('\n'):
            if ('未掌握' in line or '❌' in line) and '###' in line:
                in_table = True
                continue
            if in_table and line.startswith('###'):
                break
            if in_table and line.startswith('|') and '---' not in line and '知识点' not in line:
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if len(cells) >= 3:
                    text = f"{cells[0]}（{cells[1]}）"
                    detail = cells[2] if len(cells) > 2 else ''
                    if text[:30] not in existing_texts:
                        items.append({
                            'text': text,
                            'detail': detail,
                            'found': scan_day.isoformat(),
                            'next_review': today_str,  # review today
                            'interval': 1,  # days until next review
                            'status': 'active',
                        })
                        existing_texts.add(text[:30])

    # Find items due for review today
    due_items = [it for it in items if it['status'] == 'active' and it.get('next_review', '') <= today_str]

    if due_items:
        # Create review tasks in Vikunja (with improved dedup)
        existing_norms, _ = load_existing_titles()

        created = 0
        for it in due_items:
            title = f"复习：{it['text'][:25]}"
            if is_task_duplicate(title, existing_norms):
                log(f'Spaced task skipped (dup): {title[:30]}')
                continue
            desc = f"发现于 {it['found']}，第 {it['interval']} 天复习\n{it['detail']}"
            vikunja_create_task(title, project_id=2, due_date=today, description=desc)
            # schedule next review with increasing interval (1→3→7→14)
            next_intervals = {1: 3, 3: 7, 7: 14, 14: 30}
            new_interval = next_intervals.get(it['interval'], it['interval'] * 2)
            it['next_review'] = (today + datetime.timedelta(days=new_interval)).isoformat()
            it['interval'] = new_interval
            created += 1

        if created:
            log(f'Spaced repetition: {created} review tasks created')
    else:
        log(f'Spaced repetition: nothing due today')

    # Clean up old resolved items (>30 days)
    items = [it for it in items if it['status'] == 'active' or
             (today - datetime.date.fromisoformat(it.get('found', today_str))).days < 30]

    with open(SPACED_FILE, 'w') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

# ========== Proactive Daily Review Agent ==========

def proactive_review_generate(now, today):
    """Push daily challenge questions based on recent learning (run at 8:30)"""
    today_str = today.isoformat()

    # 1. Gather knowledge points from last 3 days' 学习回顾 + AI notes
    knowledge_points = []
    for delta in range(1, 4):
        day = today - datetime.timedelta(days=delta)
        day_str = day.isoformat()

        # Read 学习回顾.md for mastery markers
        review_path = os.path.join(NOTE_DIR, day_str, '学习回顾.md')
        if os.path.exists(review_path):
            with open(review_path, 'r', encoding='utf-8') as f:
                content = f.read()
            for line in content.split('\n'):
                line_s = line.strip()
                if any(m in line_s for m in ['⚠️', '❌']):
                    # extract meaningful text (skip table separators)
                    if '---' in line_s or '知识点' in line_s:
                        continue
                    knowledge_points.append({
                        'text': line_s[:120],
                        'date': day_str,
                        'mastery': '❌' if '❌' in line_s else '⚠️',
                    })

        # Scan AI notes from that day
        if os.path.isdir(AI_DIR):
            for root, dirs, files in os.walk(AI_DIR):
                for fname in files:
                    if fname.startswith(day_str) and fname.endswith('.md'):
                        fpath = os.path.join(root, fname)
                        subject = os.path.basename(root)
                        try:
                            with open(fpath, 'r', encoding='utf-8') as f:
                                fc = f.read(3000)
                            # extract summary from frontmatter
                            parts = fc.split('---')
                            if len(parts) >= 3:
                                for fline in parts[1].split('\n'):
                                    if fline.strip().startswith('summary:'):
                                        s = fline.split(':', 1)[1].strip().strip('"\'')
                                        if s:
                                            knowledge_points.append({
                                                'text': f"[{subject}] {s}",
                                                'date': day_str,
                                                'mastery': '⚠️',
                                            })
                        except:
                            pass

    if not knowledge_points:
        log('Review: no knowledge points found, skipping')
        return

    # 2. Prioritize: ❌ > ⚠️, older first
    priority_map = {'❌': 2, '⚠️': 1}
    knowledge_points.sort(key=lambda kp: (-priority_map.get(kp['mastery'], 0), kp['date']))
    selected = knowledge_points[:8]  # take top 8 for AI to pick from

    selected_text = '\n'.join(f"- {kp['text']} (来自{kp['date']}, {kp['mastery']})" for kp in selected)

    # 3. AI generates 3 challenge questions
    prompt = f"""你是学习测试出题官。用户是上海交大大一生科院学生。
根据以下近期学习中标记为"未掌握"或"需巩固"的知识点，生成3道简短测试题。

知识点列表：
{selected_text}

规则：
1. 只出问题，不给答案
2. 题目类型多样：1道填空、1道判断对错、1道简答
3. 每题一行，编号
4. 难度适中，侧重容易遗忘的细节和易错点
5. 数学公式用纯文本表示（如 ∫₀¹ 2x dx, x²+y²=1, ∂f/∂x），不要用 LaTeX $...$ 语法，因为 Memos 不支持渲染
6. 每题末尾用括号标注对应科目

输出格式（严格遵守）：
1. [填空] 题目内容___。（科目）
2. [判断] 题目内容。（科目）
3. [简答] 题目内容？（科目）"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 600, 'temperature': 0.5
    }).encode('utf-8')
    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        questions = result['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f'Review question generation failed: {e}')
        return

    # clean markdown fences
    if questions.startswith('```'):
        questions = '\n'.join(questions.split('\n')[1:])
    if questions.endswith('```'):
        questions = '\n'.join(questions.split('\n')[:-1])

    # 4. Create a Memos entry with #每日挑战
    memo_content = f"#每日挑战 📝 {today_str}\n\n{questions}\n\n---\n💡 回复本条 memo 写下你的答案，今晚 AI 会自动批改！"
    memo_payload = json.dumps({
        'content': memo_content,
        'visibility': 'PRIVATE'
    }).encode('utf-8')
    memo_req = urllib.request.Request(
        f'{MEMOS_API}/memos',
        data=memo_payload, method='POST',
        headers={'Authorization': f'Bearer {MEMOS_TOKEN}', 'Content-Type': 'application/json'}
    )
    memo_id = ''
    try:
        with urllib.request.urlopen(memo_req, timeout=10) as resp:
            created = json.loads(resp.read().decode('utf-8'))
        memo_id = created.get('name', '')
        log(f'Challenge memo created: {memo_id}')
    except Exception as e:
        log(f'Memo creation failed: {e}')
        return  # Don't continue if memo wasn't created

    if not memo_id:
        log('Memo created but no ID returned, aborting')
        return

    # 5. Generate answers (stored privately for evening grading)
    answer_prompt = f"""以下是3道学习测试题，请给出简洁准确的标准答案。

{questions}

每题答案一行，编号对应。关键公式用 LaTeX。"""

    ans_payload = json.dumps({
        'model': AI_MODEL,
        'messages': [{'role': 'user', 'content': answer_prompt}],
        'max_tokens': 500, 'temperature': 0.2
    }).encode('utf-8')
    ans_req = urllib.request.Request(AI_API_URL, data=ans_payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
    })
    answers = ''
    try:
        with urllib.request.urlopen(ans_req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        answers = result['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f'Answer generation failed: {e}')

    # 6. Save challenge state for evening evaluation
    challenge = {
        'date': today_str,
        'memo_id': memo_id,
        'questions': questions,
        'answers': answers,
        'source_points': [kp['text'][:80] for kp in selected[:5]],
        'evaluated': False,
    }
    with open(CHALLENGE_FILE, 'w') as f:
        json.dump(challenge, f, ensure_ascii=False, indent=2)

    # 7. Bark push
    bark_push('📝 每日挑战', f'3道复习题已推送到 Memos，回复即可获得AI批改！', group='challenge')
    log(f'Daily challenge generated from {len(selected)} knowledge points')


def proactive_review_evaluate(now, today):
    """Check if user replied to today's challenge, grade it (called from ai_summary)"""
    today_str = today.isoformat()

    try:
        with open(CHALLENGE_FILE, 'r') as f:
            challenge = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return

    if challenge.get('date') != today_str or challenge.get('evaluated'):
        return

    memo_id = challenge.get('memo_id', '')
    if not memo_id:
        challenge['evaluated'] = True
        with open(CHALLENGE_FILE, 'w') as f:
            json.dump(challenge, f, ensure_ascii=False, indent=2)
        return

    # Fetch the memo to check for user reply
    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/{memo_id}',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            memo_data = json.loads(resp.read().decode('utf-8'))
        current_content = memo_data.get('content', '')
    except Exception as e:
        log(f'Challenge memo fetch failed: {e}')
        return

    # Also check comments on this memo
    user_answer = ''
    try:
        cmt_req = urllib.request.Request(
            f'{MEMOS_API}/{memo_id}/comments',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(cmt_req, timeout=10) as resp:
            comments = json.loads(resp.read().decode('utf-8'))
        if isinstance(comments, list) and comments:
            user_answer = '\n'.join(c.get('content', '') for c in comments if c.get('content'))
    except:
        pass

    # Check if content was edited (user added answer below the divider)
    if not user_answer and '---' in current_content:
        parts = current_content.split('---')
        # original has 2 --- blocks (before questions, after questions for hint)
        # if user added content after that, extract it
        if len(parts) >= 3:
            potential_answer = parts[-1].strip()
            if potential_answer and '💡' not in potential_answer and '🤖' not in potential_answer:
                user_answer = potential_answer

    if not user_answer:
        log('Daily challenge: no reply from user, skipping grading')
        challenge['evaluated'] = True
        challenge['result'] = 'no_reply'
        with open(CHALLENGE_FILE, 'w') as f:
            json.dump(challenge, f, ensure_ascii=False, indent=2)
        return

    # AI grading with reference answers
    grade_prompt = f"""你是学习助手。以下是今天的每日挑战题目、参考答案和用户的回答。
请逐题评判对错并给出简短点评。

题目：
{challenge['questions']}

参考答案：
{challenge.get('answers', '(未生成参考答案，请根据你的知识判断)')}

用户的回答：
{user_answer}

输出格式：
1. ✅/❌ 一句话点评
2. ✅/❌ 一句话点评
3. ✅/❌ 一句话点评

总评：X/3 正确。一句鼓励或建议。"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [{'role': 'user', 'content': grade_prompt}],
        'max_tokens': 400, 'temperature': 0.3
    }).encode('utf-8')
    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
    })

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        grading = result['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f'Grading failed: {e}')
        return

    # clean markdown fences
    if grading.startswith('```'):
        grading = '\n'.join(grading.split('\n')[1:])
    if grading.endswith('```'):
        grading = '\n'.join(grading.split('\n')[:-1])

    # Append grading to the memo
    new_content = current_content + f'\n\n---\n🤖 **AI 批改结果**\n\n{grading}'
    update_payload = json.dumps({'content': new_content}).encode('utf-8')
    update_req = urllib.request.Request(
        f'{MEMOS_API}/{memo_id}',
        data=update_payload, method='PATCH',
        headers={'Authorization': f'Bearer {MEMOS_TOKEN}', 'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(update_req, timeout=10) as resp:
            resp.read()
        log(f'Challenge graded and appended to memo')
    except Exception as e:
        log(f'Failed to update memo with grading: {e}')

    bark_push('📝 挑战批改完成', grading.split('\n')[-1][:60] if grading else '已完成', group='challenge')

    challenge['evaluated'] = True
    challenge['result'] = grading
    with open(CHALLENGE_FILE, 'w') as f:
        json.dump(challenge, f, ensure_ascii=False, indent=2)


# ========== Bedtime Review Push ==========

def bedtime_review(now, today):
    """Push top 3 knowledge points from today's learning review at bedtime"""
    today_str = today.isoformat()
    review_path = os.path.join(NOTE_DIR, today_str, '学习回顾.md')
    if not os.path.exists(review_path):
        log('Bedtime review: no learning review found today')
        return

    with open(review_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract key knowledge points (lines with ✅ ⚠️ ❌ markers)
    points = []
    for line in content.split('\n'):
        line_s = line.strip()
        if not line_s or line_s.startswith('#') or line_s.startswith('|'):
            continue
        for marker in ['❌', '⚠️', '✅']:
            if marker in line_s:
                # Clean up the text
                clean = line_s.replace('- ', '').replace('**', '').strip()
                if len(clean) > 5:
                    points.append((marker, clean[:60]))
                break

    if not points:
        log('Bedtime review: no knowledge points found')
        return

    # Prioritize: ❌ > ⚠️ > ✅, take top 3
    priority = {'❌': 3, '⚠️': 2, '✅': 1}
    points.sort(key=lambda p: -priority.get(p[0], 0))
    top3 = points[:3]

    # Format and push
    msg_lines = [f'{i+1}. {p[1]}' for i, p in enumerate(top3)]
    msg = '\n'.join(msg_lines)

    bark_push('🌙 睡前回顾', msg, sound='silence', group='bedtime')
    log(f'Bedtime review pushed: {len(top3)} points')


# ========== Memos #查 Quick Lookup ==========

def memos_lookup(now, today):
    """Handle #查 tags in Memos — instant note lookup without AI"""
    processed_file = '/root/.lookup-processed.json'
    try:
        with open(processed_file, 'r') as f:
            processed = set(json.load(f))
    except:
        processed = set()

    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/memos?pageSize=30',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log(f'Lookup memo fetch failed: {e}')
        return

    looked_up = 0
    for m in data.get('memos', []):
        memo_id = m.get('name', '')
        content = m.get('content', '')
        if '#查' not in content or memo_id in processed:
            continue
        if '📖' in content:  # already answered
            processed.add(memo_id)
            continue

        query = content.replace('#查', '').strip()
        if len(query) < 2:
            processed.add(memo_id)
            continue

        log(f'Looking up: {query[:30]}')

        # Call searchNotes via HTTP to chat-server
        try:
            search_payload = json.dumps({'query': query}).encode('utf-8')
            search_req = urllib.request.Request(
                'http://127.0.0.1:3457/api/search',
                data=search_payload, method='POST',
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(search_req, timeout=10) as resp:
                search_data = json.loads(resp.read().decode('utf-8'))

            results = search_data.get('results', [])[:3]
            if results:
                reply = '📖 **查询结果**\n\n'
                for i, r in enumerate(results):
                    title = r.get('title', '').replace('\\', '/').split('/')[-1]
                    snippet = r.get('snippet', '')[:150].replace('\n', ' ').strip()
                    reply += f'{i+1}. **{title}**\n   {snippet}\n\n'
            else:
                reply = '📖 未找到相关笔记，试试用 #问 让 AI 回答？'

            # Append to memo
            new_content = content + f'\n\n---\n{reply}'
            update_payload = json.dumps({'content': new_content}).encode('utf-8')
            update_req = urllib.request.Request(
                f'{MEMOS_API}/{memo_id}',
                data=update_payload, method='PATCH',
                headers={'Authorization': f'Bearer {MEMOS_TOKEN}', 'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(update_req, timeout=10) as resp:
                resp.read()

            looked_up += 1
            processed.add(memo_id)
        except Exception as e:
            log(f'Lookup failed: {e}')
            processed.add(memo_id)

    with open(processed_file, 'w') as f:
        json.dump(list(processed)[-500:], f)
    if looked_up:
        log(f'Looked up {looked_up} queries')


# ========== Subject Analysis Agent ==========

def subject_analysis(now, today, subject):
    """Deep analysis of a subject's weaknesses across all data sources"""
    today_str = today.isoformat()
    days_left = (EXAM_DATE - today).days

    # Resolve subject name (support aliases)
    SUBJECT_ALIAS = {
        '高数': '高数II', '有机': '有机化学', '概统': '概率统计', '大物': '大学物理',
        '分化': '分析化学', '普生': '普通生物学', 'AI': 'AI基础',
    }
    resolved = SUBJECT_ALIAS.get(subject, subject)
    full_subject = SUBJECT_MAP.get(resolved, resolved)
    folder_map = {
        '高数II': '高等数学II', '有机化学': '有机化学', '概率统计': '概率统计',
        '大学物理': '大学物理', '分析化学': '分析化学', '普通生物学': '普通生物学',
        'AI基础': '人工智能基础', '习思想': '习思想',
    }
    fc_short = {
        '高数II':'高数','有机化学':'有机','概率统计':'概统','大学物理':'大物',
        '分析化学':'分析化学','普通生物学':'普生','AI基础':'AI基础','习思想':'习思想',
    }

    data_block = ''

    # 1. AI notes
    ai_dir = os.path.join(AI_DIR, full_subject)
    ai_count = 0
    if os.path.isdir(ai_dir):
        ai_files = sorted([f for f in os.listdir(ai_dir) if f.endswith('.md')])[-20:]
        if ai_files:
            data_block += f'\n【AI笔记记录 ({len(ai_files)}篇)】\n'
            for fname in ai_files:
                try:
                    with open(os.path.join(ai_dir, fname), 'r', encoding='utf-8') as f:
                        fc = f.read(500)
                    sm = ''
                    m = fc.split('---')
                    if len(m) >= 3:
                        for line in m[1].split('\n'):
                            if line.strip().startswith('summary:'):
                                sm = line.split(':', 1)[1].strip().strip('"\'')
                    data_block += f'- {fname[:10]} {fname[11:].replace(".md","")}: {sm}\n'
                    ai_count += 1
                except:
                    pass

    # 2. Learning review excerpts (last 14 days)
    review_excerpts = ''
    for delta in range(0, 14):
        day = today - datetime.timedelta(days=delta)
        rp = os.path.join(NOTE_DIR, str(day), '学习回顾.md')
        if os.path.exists(rp):
            try:
                with open(rp, 'r', encoding='utf-8') as f:
                    rc = f.read()
                lines = rc.split('\n')
                in_subj = False
                excerpt = ''
                for line in lines:
                    if ('###' in line or '####' in line) and (full_subject in line or subject in line):
                        in_subj = True
                        excerpt += f'[{day}] '
                        continue
                    if in_subj and line.startswith('###'):
                        break
                    if in_subj:
                        excerpt += line + '\n'
                if excerpt.strip():
                    review_excerpts += excerpt[:400] + '\n'
            except:
                pass
    if review_excerpts:
        data_block += f'\n【学习回顾中该科目的历史记录】\n{review_excerpts[:1500]}\n'

    # 3. Flashcards
    fc_key = fc_short.get(full_subject, full_subject)
    fc_count = 0
    try:
        with open('/root/.flashcards-server.json', 'r') as f:
            all_fc = json.load(f)
        subj_fc = [c for c in all_fc if c.get('s') == fc_key or c.get('s') == full_subject]
        fc_count = len(subj_fc)
        if subj_fc:
            data_block += f'\n【闪卡 ({len(subj_fc)}张)】\n'
            for c in subj_fc[-10:]:
                data_block += f'- Q: {c["q"][:60]} | A: {c["a"][:60]}\n'
    except:
        pass

    # 4. Spaced repetition items
    sp_count = 0
    try:
        with open('/root/.spaced-repetition.json', 'r') as f:
            all_sp = json.load(f)
        subj_sp = [it for it in all_sp if full_subject in it.get('text', '') or subject in it.get('text', '')]
        sp_count = len(subj_sp)
        if subj_sp:
            data_block += f'\n【间隔重复项 ({len(subj_sp)}项)】\n'
            for it in subj_sp:
                data_block += f'- {it["text"]} (发现:{it.get("found","?")}, 间隔:{it.get("interval","?")}天)\n'
    except:
        pass

    # 5. Weakness tracker
    wk_count = 0
    try:
        wk_all = load_weaknesses()
        subj_wk = [w for w in wk_all if full_subject in w.get('text', '') or subject in w.get('text', '')]
        wk_count = len(subj_wk)
        if subj_wk:
            data_block += f'\n【薄弱点追踪 ({len(subj_wk)}项)】\n'
            for w in subj_wk:
                data_block += f'- {w["text"]} (状态:{w.get("status","?")})\n'
    except:
        pass

    # 6. Course notes
    course_ctx = ''
    c_dir = os.path.join(COURSE_DIR, folder_map.get(full_subject, full_subject))
    if os.path.isdir(c_dir):
        for fname in ['期末复习.md', '公式速查表.md', '反应总结.md', '典型题型与解法.md']:
            fp = os.path.join(c_dir, fname)
            if os.path.exists(fp):
                try:
                    with open(fp, 'r', encoding='utf-8') as f:
                        course_ctx += f'\n=== {fname} ===\n{f.read(800)}\n'
                except:
                    pass
    if course_ctx:
        data_block += f'\n【课程笔记参考】\n{course_ctx[:2000]}\n'

    if not data_block.strip():
        log(f'No data found for subject: {subject}')
        md = f'# 弱点分析: {full_subject}\n\n暂无该科目的学习数据。请先通过 Gemini 学习并保存笔记。\n'
        write_report(today, f'弱点分析_{full_subject}', md)
        return

    log(f'Analysis data: {ai_count} AI notes, {fc_count} flashcards, {sp_count} spaced, {wk_count} weaknesses')

    # 7. AI analysis
    system_prompt = f"""你是期末复习分析师。用户是上海交大大一生科院学生，距离期末考试{days_left}天。
请根据以下该科目（{full_subject}）的所有学习数据，进行深度弱点分析。

{data_block}

请输出以下结构（markdown格式）：

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
- 所有数学公式必须用 $...$ 包裹（如 $\\int_0^1 2x\\,dx$），绝对不要用反引号 ` 包裹数学表达式
- 方括号 [...] 在 Obsidian 中是 wiki-link 语法会导致解析错误，数学中请用 $\\left[...\\right]$ 代替（如 $\\left[x^2\\right]_0^1 = 1$）
- 不要在 $...$ 内部嵌套另一个 $...$
- 只输出markdown，不要代码块包裹"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f'请分析我在"{full_subject}"科目的学习情况和弱点。'}
        ],
        'max_tokens': 3000, 'temperature': 0.3
    }).encode('utf-8')
    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
    })

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        analysis = result['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f'Subject analysis failed: {e}')
        return

    if analysis.startswith('```'):
        analysis = '\n'.join(analysis.split('\n')[1:])
    if analysis.endswith('```'):
        analysis = '\n'.join(analysis.split('\n')[:-1])

    # Post-process: convert backtick math to $...$ to prevent Dataview parsing
    import re as _re
    _math_signs = _re.compile(r'[∫∮∑∏√∂∇≤≥±∈∉⊂⊃∪∩∞≈≠∝∀∃^_\\]')
    def _fix_backtick_math(m):
        inner = m.group(1)
        if _math_signs.search(inner):
            return f'${inner}$'
        return m.group(0)
    analysis = _re.sub(r'`([^`]+)`', _fix_backtick_math, analysis)

    # 8. Save report
    md = f"""---
created: {today}
tags: [弱点分析, {full_subject}]
---

# 🎯 弱点分析: {full_subject}

> 📅 {today} | 距期末 **{days_left}** 天 | AI笔记 {ai_count} 篇 · 闪卡 {fc_count} 张 · 间隔重复 {sp_count} 项

{analysis}
"""

    write_report(today, f'弱点分析_{full_subject}', md)
    bark_push(f'🎯 {full_subject} 弱点分析', f'已生成弱点分析报告，{ai_count}篇笔记+{fc_count}张闪卡', group='weakness')
    log(f'Subject analysis done: {full_subject}')


# ========== Batch Problem Extraction ==========

PROBLEMS_FILE = '/root/.ai-problems.json'

def batch_extract_problems(now, today):
    """Scan all AI notes and extract problems (one-time batch operation)"""
    import base64 as _b64

    # Load existing problems to avoid duplicates
    try:
        with open(PROBLEMS_FILE, 'r') as f:
            existing = json.load(f)
    except:
        existing = []
    existing_sources = {p.get('source', '') for p in existing}

    total_extracted = 0

    for subj_dir in sorted(os.listdir(AI_DIR)):
        subj_path = os.path.join(AI_DIR, subj_dir)
        if not os.path.isdir(subj_path) or subj_dir.startswith('.'):
            continue

        md_files = sorted([f for f in os.listdir(subj_path) if f.endswith('.md')])
        for fname in md_files:
            source = f'AI笔记/{subj_dir}/{fname}'
            if source in existing_sources:
                continue

            fpath = os.path.join(subj_path, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except:
                continue

            # Extract conversation text (skip frontmatter)
            parts = content.split('---')
            conv_text = '---'.join(parts[2:]) if len(parts) >= 3 else content

            # Check for image references and try OCR
            import re as _re
            ocr_texts = []
            img_refs = _re.findall(r'!\[\[attachments/([^\]]+)\]\]', content)
            att_dir = os.path.join(subj_path, 'attachments')
            for img_name in img_refs[:3]:  # max 3 images per note
                img_path = os.path.join(att_dir, img_name)
                if not os.path.exists(img_path):
                    continue
                try:
                    with open(img_path, 'rb') as imgf:
                        img_data = _b64.b64encode(imgf.read()).decode('utf-8')
                    ext = img_name.rsplit('.', 1)[-1].lower()
                    mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}.get(ext, 'image/jpeg')

                    ocr_payload = json.dumps({
                        'model': VISION_MODEL,
                        'messages': [{'role': 'user', 'content': [
                            {'type': 'text', 'text': '这是一道考试/作业题目的照片。请完整提取题目文字，包括数学公式（用LaTeX $...$格式）。只输出题目原文，不要解答。'},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_data}'}}
                        ]}],
                        'max_tokens': 800
                    }).encode('utf-8')
                    ocr_req = urllib.request.Request(AI_API_URL, data=ocr_payload, headers={
                        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
                    })
                    with urllib.request.urlopen(ocr_req, timeout=60) as resp:
                        ocr_result = json.loads(resp.read().decode('utf-8'))
                    ocr_text = ocr_result['choices'][0]['message']['content'].strip()
                    if ocr_text and len(ocr_text) > 10:
                        ocr_texts.append(ocr_text)
                        log(f'OCR: {img_name[:30]} -> {ocr_text[:40]}')
                except Exception as e:
                    log(f'OCR failed for {img_name}: {e}')

            # AI extract problems
            user_content = ''
            if ocr_texts:
                user_content += '【图片OCR识别的题目】\n' + '\n---\n'.join(ocr_texts) + '\n\n'
            user_content += '【对话内容】\n' + conv_text[:5000]

            extract_payload = json.dumps({
                'model': AI_MODEL,
                'messages': [
                    {'role': 'system', 'content': '你是题目提取专家。从以下学习对话中提取可复习的题目。用户可能通过图片上传题目（OCR结果已提供），Gemini直接解答不复述原题。综合OCR和对话还原完整题目+解答。返回JSON:{"problems":[{"question":"完整题目","solution":"综合解答","key_insight":"一句话思路","difficulty":"easy|medium|hard","type":"计算|证明|概念|应用","keywords":["关键词"]}]}。如无具体题目返回{"problems":[]}。只返回JSON。'},
                    {'role': 'user', 'content': user_content}
                ],
                'max_tokens': 2000, 'temperature': 0.3
            }).encode('utf-8')
            req = urllib.request.Request(AI_API_URL, data=extract_payload, headers={
                'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
            })

            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                raw = result['choices'][0]['message']['content'].strip()
                if raw.startswith('```'):
                    raw = '\n'.join(raw.split('\n')[1:])
                if raw.endswith('```'):
                    raw = '\n'.join(raw.split('\n')[:-1])

                import re as _re2
                try:
                    parsed = json.loads(raw)
                except:
                    m = _re2.search(r'\{[\s\S]*\}', raw)
                    parsed = json.loads(m.group(0)) if m else {'problems': []}

                problems = parsed.get('problems', [])
                if problems:
                    ts = int(datetime.datetime.now().timestamp() * 1000)
                    date_str = fname[:10] if len(fname) >= 10 else today.isoformat()
                    for i, p in enumerate(problems):
                        existing.append({
                            'id': f'{ts}_{i}',
                            'subject': subj_dir,
                            'source': source,
                            'question': p.get('question', ''),
                            'question_image': img_refs[0] if img_refs else '',
                            'solution': p.get('solution', ''),
                            'key_insight': p.get('key_insight', ''),
                            'difficulty': p.get('difficulty', 'medium'),
                            'type': p.get('type', '计算'),
                            'keywords': p.get('keywords', []),
                            'created': date_str,
                            'mastery': 0,
                            'review_count': 0,
                            'last_reviewed': None,
                        })
                    total_extracted += len(problems)
                    existing_sources.add(source)
                    log(f'Extracted {len(problems)} problems from {source}')
                else:
                    log(f'No problems in {source}')
                    existing_sources.add(source)
            except Exception as e:
                log(f'Extract failed for {source}: {e}')

            # Rate limit: wait between API calls
            import time
            time.sleep(2)

    # Save JSON
    with open(PROBLEMS_FILE, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    # Also write Obsidian vault files (per-subject)
    problems_dir = os.path.join(VAULT_DIR, 'AI题库')
    os.makedirs(problems_dir, exist_ok=True)
    by_subject = {}
    for p in existing:
        by_subject.setdefault(p.get('subject', '其他'), []).append(p)

    for subj, probs in by_subject.items():
        vault_file = os.path.join(problems_dir, f'{subj}.md')
        md = f"""---
tags: [AI题库, {subj}]
---

# {subj} AI 题库

> 从 Gemini 对话自动提取 · 共 {len(probs)} 题 · 持续积累

"""
        for p in probs:
            diff_icon = {'easy': '🟢', 'medium': '🟡', 'hard': '🔴'}.get(p.get('difficulty', ''), '🟡')
            q_short = p.get('question', '')[:40].replace('\n', ' ')
            md += f"## {diff_icon} {q_short}...\n\n"
            md += f"> [!info] 来源\n> [[{p.get('source', '')}]] · {p.get('created', '')} · {p.get('type', '')}\n\n"
            md += f"**题目**\n\n{p.get('question', '')}\n\n"
            md += f"> [!tip]- 解答\n"
            for line in p.get('solution', '').split('\n'):
                md += f"> {line}\n"
            md += "\n"
            if p.get('key_insight'):
                md += f"> [!abstract] 核心思路\n> {p['key_insight']}\n\n"
            kws = p.get('keywords', [])
            if kws:
                md += f"标签: {' '.join('#' + k for k in kws)}\n\n"
            md += "---\n\n"

        with open(vault_file, 'w', encoding='utf-8') as f:
            f.write(md)
        log(f'Vault file: {vault_file} ({len(probs)} problems)')

    subprocess.run(['chown', '-R', 'www-data:www-data', problems_dir, PROBLEMS_FILE], capture_output=True)
    log(f'Batch extraction done: {total_extracted} problems from {len(existing_sources)} notes')


# ========== Fatigue Detection ==========

FATIGUE_KEYWORDS = ['好累', '累了', '学不动', '崩了', '不想学', '太难了', '放弃', '焦虑', '烦', '头疼', '困死', '摆烂', '不行了', '学吐了']
ENCOURAGE_MSGS = [
    '休息一下吧！出去走10分钟，回来效率更高',
    '你已经很努力了，适当休息不是偷懒',
    '喝杯水，做几个深呼吸，然后继续',
    '记住：坚持到考前就是胜利，不需要完美',
    '累了就换个轻松的科目，比如翻翻闪卡',
    '距期末还有时间，不要给自己太大压力',
    '你能意识到疲劳就很好了，休息15分钟再继续',
]

def fatigue_check(now, today):
    """Detect study fatigue from Memos and study log, push encouragement"""
    import random

    # 1. Check Memos for fatigue keywords
    try:
        req = urllib.request.Request(
            f'{MEMOS_API}/memos?pageSize=10',
            headers={'Authorization': f'Bearer {MEMOS_TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        today_str = today.isoformat()
        for m in data.get('memos', []):
            ct = m.get('createTime', '')
            if not ct.startswith(today_str):
                continue
            content = m.get('content', '')
            for kw in FATIGUE_KEYWORDS:
                if kw in content:
                    bark_push('💪 休息一下', random.choice(ENCOURAGE_MSGS), sound='silence', group='care')
                    log(f'Fatigue detected: "{kw}" in memo')
                    return
    except:
        pass

    # 2. Check continuous study time (from study log)
    try:
        logs = load_study_log()
        today_logs = [e for e in logs if e.get('date') == today.isoformat()]
        if len(today_logs) >= 4:
            # 4+ study sessions today
            total = sum(e.get('duration', 0) for e in today_logs)
            if total >= 240:  # 4+ hours
                bark_push('💪 学了够久了', f'今天已学{total}分钟，适当休息效率更高', sound='silence', group='care')
                log(f'Long study detected: {total}min today')
    except:
        pass


# ========== Achievement System ==========

ACHIEVEMENT_FILE = '/root/.achievements.json'

def check_achievements(now, today):
    """Check and push achievement notifications"""
    today_str = today.isoformat()

    try:
        with open(ACHIEVEMENT_FILE, 'r') as f:
            state = json.load(f)
    except:
        state = {'unlocked': [], 'last_check': ''}

    if state.get('last_check') == today_str:
        return  # Already checked today

    new_achievements = []

    # 1. Study streak (consecutive days with study log)
    try:
        logs = load_study_log()
        streak = 0
        for delta in range(0, 30):
            day = (today - datetime.timedelta(days=delta)).isoformat()
            if any(e.get('date') == day for e in logs):
                streak += 1
            else:
                break

        milestones = {3: '🔥 连续学习3天', 7: '🏆 一周不间断', 14: '⭐ 两周坚持', 21: '👑 三周铁人'}
        for days, name in milestones.items():
            if streak >= days and name not in state['unlocked']:
                new_achievements.append(name)
                state['unlocked'].append(name)
    except:
        pass

    # 2. Problem mastery milestones
    try:
        with open(PROBLEMS_FILE, 'r') as f:
            problems = json.load(f)
        mastered = len([p for p in problems if p.get('mastery', 0) >= 80])
        milestones = {5: '📚 掌握5道题', 10: '📚 掌握10道题', 20: '📚 掌握20道题', 30: '📚 题库大师'}
        for count, name in milestones.items():
            if mastered >= count and name not in state['unlocked']:
                new_achievements.append(name)
                state['unlocked'].append(name)
    except:
        pass

    # 3. Flashcard milestones
    try:
        with open('/root/.flashcards-server.json', 'r') as f:
            cards = json.load(f)
        milestones = {50: '🃏 50张闪卡', 100: '🃏 百卡成就', 150: '🃏 闪卡达人'}
        for count, name in milestones.items():
            if len(cards) >= count and name not in state['unlocked']:
                new_achievements.append(name)
                state['unlocked'].append(name)
    except:
        pass

    # 4. Total study time milestones
    try:
        logs = load_study_log()
        total_min = sum(e.get('duration', 0) for e in logs)
        milestones = {300: '⏱️ 累计5小时', 600: '⏱️ 累计10小时', 1200: '⏱️ 累计20小时', 3000: '⏱️ 累计50小时'}
        for mins, name in milestones.items():
            if total_min >= mins and name not in state['unlocked']:
                new_achievements.append(name)
                state['unlocked'].append(name)
    except:
        pass

    # Push new achievements
    if new_achievements:
        msg = '\n'.join(new_achievements)
        bark_push('🎉 成就解锁！', msg, sound='fanfare', group='achievement')
        log(f'Achievements unlocked: {new_achievements}')

    state['last_check'] = today_str
    with open(ACHIEVEMENT_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ========== Compile Exam Essentials ==========

def compile_subject(now, today, subject):
    """Compile all study materials into one exam essentials document per subject"""
    days_left = (EXAM_DATE - today).days
    SUBJECT_ALIAS = {
        '高数': '高数II', '有机': '有机化学', '概统': '概率统计', '大物': '大学物理',
        '分化': '分析化学', '普生': '普通生物学', 'AI': 'AI基础',
    }
    full = SUBJECT_ALIAS.get(subject, SUBJECT_MAP.get(subject, subject))

    # 1. Read course notes
    course_content = ''
    course_dir = os.path.join(COURSE_DIR, full)
    if os.path.isdir(course_dir):
        for fname in sorted(os.listdir(course_dir)):
            if fname.endswith('.md') and fname != '期末冲刺精华.md':
                fpath = os.path.join(course_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        course_content += f'\n\n=== {fname} ===\n' + f.read(3000)
                except:
                    pass

    # 2. Read AI notes review essences
    ai_essences = ''
    ai_dir = os.path.join(AI_DIR, full)
    if os.path.isdir(ai_dir):
        for fname in sorted(os.listdir(ai_dir)):
            if fname.endswith('.md'):
                fpath = os.path.join(ai_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    # Extract 复习精华 section
                    if '## 复习精华' in content:
                        essence = content.split('## 复习精华')[1].split('\n## ')[0]
                        ai_essences += f'\n[{fname[:10]}] {essence[:500]}\n'
                except:
                    pass

    # 3. Read AI problems
    problems_text = ''
    try:
        with open(PROBLEMS_FILE, 'r') as f:
            all_problems = json.load(f)
        subj_problems = [p for p in all_problems if p.get('subject') == full]
        for p in subj_problems[:10]:
            problems_text += f'\n题: {p.get("question","")[:100]}\n解: {p.get("key_insight","")}\n'
    except:
        pass

    # 4. Read weaknesses
    weakness_text = ''
    try:
        wk = load_weaknesses()
        subj_wk = [w for w in wk if full in w.get('text', '')]
        if subj_wk:
            weakness_text = '\n'.join(w['text'] for w in subj_wk)
    except:
        pass

    # 5. Read hard flashcards
    flashcard_text = ''
    fc_short = {'高数II':'高数','有机化学':'有机','概率统计':'概统','大学物理':'大物',
                '分析化学':'分析化学','普通生物学':'普生','AI基础':'AI基础','习思想':'习思想'}
    try:
        with open('/root/.flashcards-server.json', 'r') as f:
            all_fc = json.load(f)
        key = fc_short.get(full, full)
        hard_fc = [c for c in all_fc if c.get('s') == key and c.get('difficulty') in ('hard', 'medium')]
        for c in hard_fc[:15]:
            flashcard_text += f'\nQ: {c["q"][:80]}\nA: {c["a"][:80]}\n'
    except:
        pass

    # 6. AI compile
    source_block = f"""【课程笔记】(截取)
{course_content[:6000]}

【AI对话复习精华】
{ai_essences[:2000]}

【AI题库错题】
{problems_text[:2000]}

【薄弱点追踪】
{weakness_text[:500]}

【高难度闪卡】
{flashcard_text[:1500]}"""

    exam_info = COURSE_EXAMS.get(full.replace('高数II','高等数学II').replace('高数II','高数II'), {})
    target = exam_info.get('target', 80)
    known = exam_info.get('known')
    known_str = f'期中{known}分，' if known else ''

    prompt = f"""你是期末冲刺复习专家。请为《{full}》生成一份考前精华文档。
距期末{days_left}天。{known_str}目标{target}分。

以下是该科目的所有学习资料（课程笔记、AI对话精华、错题、弱点、闪卡）：
{source_block}

请输出一份 Obsidian Markdown 格式的精华文档，包含：

## 公式大全
按章节整理所有核心公式（$...$格式），每个公式附一句话说明使用条件

## 必考题型
列出 5-8 个最可能出现的题型，每个题型：
- 识别特征
- 解题模板（步骤化）
- 一道典型例题

## 易错清单
从错题和弱点中提炼 Top 10 易错点，用 > [!warning] callout 格式

## 弱点专项突破
针对薄弱知识点的专项训练建议（具体到做哪些题、看哪些笔记）

## 考前速记卡
10-15 条"最后一眼"式的速记条目（一行一条，适合考前10分钟翻阅）

格式要求：
- 所有数学公式用 $...$ 包裹
- 方括号用 $\\left[...\\right]$
- 不要用代码块包裹整体输出
- 使用 Obsidian callout: > [!warning], > [!tip]-, > [!abstract]"""

    payload = json.dumps({
        'model': AI_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 4000, 'temperature': 0.3
    }).encode('utf-8')
    req = urllib.request.Request(AI_API_URL, data=payload, headers={
        'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'
    })

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        compiled = result['choices'][0]['message']['content'].strip()
    except Exception as e:
        log(f'Compile failed for {full}: {e}')
        return

    if compiled.startswith('```'):
        compiled = '\n'.join(compiled.split('\n')[1:])
    if compiled.endswith('```'):
        compiled = '\n'.join(compiled.split('\n')[:-1])

    md = f"""---
tags: [期末冲刺, {full}]
created: {today}
---

# {full} 期末冲刺精华

> 距期末 **{days_left}** 天 | {known_str}目标 {target} 分
> 整合来源：课程笔记 + AI 对话精华 + 错题 + 弱点追踪 + 闪卡

{compiled}
"""

    out_path = os.path.join(course_dir, '期末冲刺精华.md')
    os.makedirs(course_dir, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    subprocess.run(['chown', '-R', 'www-data:www-data', course_dir], capture_output=True)
    log(f'Compiled: {out_path}')


# ========== Smart Time Slot Scheduler ==========

SCHEDULE_PY = {
    0: [  # 周一
        ('08:00', '09:40', '高等数学II'),
        ('10:00', '11:40', '大学物理B'),
    ],
    1: [  # 周二
        ('08:00', '09:40', '概率统计'),
        ('10:00', '11:40', '高等数学II'),
        ('12:00', '15:40', '有机化学实验'),
    ],
    2: [  # 周三
        ('08:00', '09:40', '英汉口译'),
        ('12:55', '15:40', '习近平新时代思想'),
        ('16:00', '17:40', '网球'),
    ],
    3: [  # 周四
        ('08:00', '09:40', '大学物理B'),
        ('10:00', '11:40', '概率统计'),
        ('12:00', '15:40', 'ET创新实验室'),
        ('16:00', '17:40', '有机化学'),
    ],
    4: [  # 周五
        ('08:00', '09:40', '分析化学'),
        ('16:00', '17:40', '军事理论'),
        ('18:00', '19:40', '人工智能基础A'),
    ],
    5: [  # 周六
        ('10:00', '11:40', '新时代社会认知实践'),
        ('18:00', '19:40', '大学物理实验'),
    ],
    6: [],  # 周日
}

def smart_timeslot(now, today):
    """Push study recommendation for current free time slot"""
    hour = now.hour
    minute = now.minute
    weekday = today.weekday()  # 0=Monday

    if hour < 7 or hour >= 22:
        return

    courses = SCHEDULE_PY.get(weekday, [])

    # Find current time slot
    current_time = f'{hour:02d}:{minute:02d}'

    # Check if we're in a class right now
    for start, end, name in courses:
        if start <= current_time <= end:
            return  # In class, don't push

    # Find the next class (to know how long the free slot is)
    free_end = '22:00'
    for start, end, name in sorted(courses):
        if start > current_time:
            free_end = start
            break

    free_minutes = (int(free_end[:2]) * 60 + int(free_end[3:])) - (hour * 60 + minute)
    if free_minutes < 20:
        return  # Too short

    # Check if already pushed for this slot today
    state_file = '/root/.timeslot-state.json'
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
    except:
        state = {}

    slot_key = f'{today}_{hour}'
    if state.get(slot_key):
        return  # Already pushed

    # Decide what to study based on priorities
    recommendations = []

    # 1. Check spaced repetition due items
    try:
        with open(SPACED_FILE, 'r') as f:
            sp_items = json.load(f)
        due = [it for it in sp_items if it.get('status') == 'active' and it.get('next_review', '') <= today.isoformat()]
        for it in due[:2]:
            recommendations.append(f"复习: {it['text'][:25]}(间隔重复)")
    except:
        pass

    # 2. Check today's Vikunja tasks
    try:
        today_tasks = []
        for pid in [2, 3]:  # 期末复习 + 作业DDL
            tasks = vikunja_get(f'/projects/{pid}/tasks')
            if isinstance(tasks, list):
                for t in tasks:
                    if not t.get('done'):
                        due = parse_date(t.get('due_date', ''))
                        if due and due.date() == today:
                            today_tasks.append(t)
        today_tasks.sort(key=lambda t: -t.get('priority', 0))
        for t in today_tasks[:2]:
            recommendations.append(f"{t['title'][:30]}")
    except:
        pass

    # 3. If still space, suggest weakness-based review
    if len(recommendations) < 3:
        try:
            wk = load_weaknesses()
            active_wk = [w for w in wk if w.get('status', 'active') == 'active']
            for w in active_wk[:1]:
                recommendations.append(f"巩固弱点: {w['text'][:25]}")
        except:
            pass

    if not recommendations:
        recommendations = ['自由复习时间，按计划推进']

    # Format and push
    free_h = free_minutes // 60
    free_m = free_minutes % 60
    time_str = f'{free_h}h{free_m}min' if free_h > 0 else f'{free_m}min'

    msg_lines = [f'{i+1}. {r}' for i, r in enumerate(recommendations[:3])]
    title = f'📚 {current_time}-{free_end} 空闲({time_str})'
    body = '\n'.join(msg_lines)

    bark_push(title, body, group='timeslot')

    state[slot_key] = True
    # Clean old entries (keep last 3 days)
    cutoff = (today - datetime.timedelta(days=3)).isoformat()
    state = {k: v for k, v in state.items() if k >= cutoff}
    with open(state_file, 'w') as f:
        json.dump(state, f)

    log(f'Timeslot push: {title} | {body[:50]}')


# ========== Main ==========

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'morning'
    now = datetime.datetime.now(TZ)

    # extract mode: batch extract problems from all AI notes
    if mode == 'extract':
        today = now.date()
        batch_extract_problems(now, today)
        log(f'{mode} done')
        return

    # compile mode: generate exam essentials document
    if mode == 'compile':
        today = now.date()
        subject = sys.argv[2] if len(sys.argv) > 2 else None
        if subject == 'all':
            for subj in SUBJECT_MAP:
                log(f'Compiling {subj}...')
                compile_subject(now, today, subj)
                import time; time.sleep(3)
        elif subject:
            compile_subject(now, today, subject)
        else:
            log('Usage: compile <subject|all>')
            sys.exit(1)
        log(f'{mode} done')
        return

    # analyze mode: argv[2] is subject, not date
    if mode == 'analyze':
        today = now.date()
        subject = sys.argv[2] if len(sys.argv) > 2 else None
        if not subject:
            log('Usage: analyze <subject> (e.g., analyze 高数II)')
            sys.exit(1)
        subject_analysis(now, today, subject)
        log(f'{mode} done')
        return

    date_override = sys.argv[2] if len(sys.argv) > 2 else None
    today = datetime.date.fromisoformat(date_override) if date_override else now.date()

    if mode == 'morning':
        morning_report(now, today)
    elif mode == 'evening':
        evening_report(now, today)
    elif mode == 'summary':
        ai_summary(now, today)
    elif mode == 'weekly':
        weekly_report(now, today)
    elif mode == 'news':
        from news_fetcher import run_news
        run_news(today.isoformat())
    elif mode == 'quiz':
        quiz_generator(now, today)
        memos_qa(now, today)
        memos_study_log(now, today)
        memos_lookup(now, today)
        fatigue_check(now, today)
        check_achievements(now, today)
    elif mode == 'bedtime':
        bedtime_review(now, today)
    elif mode == 'spaced':
        spaced_repetition(now, today)
    elif mode == 'review':
        proactive_review_generate(now, today)
    elif mode == 'evaluate':
        proactive_review_evaluate(now, today)
    elif mode == 'timeslot':
        smart_timeslot(now, today)
    else:
        log(f'Unknown mode: {mode}')
        sys.exit(1)

    log(f'{mode} done')

if __name__ == '__main__':
    main()
