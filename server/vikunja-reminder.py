#!/usr/bin/env python3
"""Vikunja Task Reminder - push overdue/upcoming tasks to iPhone via Bark"""
import json, os, sys, datetime, urllib.request, urllib.parse

# ============ CONFIG ============
BARK_KEY = "YOUR_BARK_KEY"  # Bark app device key
VIKUNJA_URL = "http://localhost:8086/api/v1"
VIKUNJA_TOKEN = "YOUR_VIKUNJA_TOKEN"
STATE_FILE = "/root/.reminder-state.json"
LOG = "/root/reminder.log"
QUIET_START, QUIET_END = 23, 7  # no push between 23:00-07:00

PROJECTS = {2: '期末复习', 3: '作业&DDL', 4: '生活&日常', 5: '技能提升', 6: '长期目标'}
# ================================

def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}')

def rotate_log():
    """Keep reminder.log under 100KB"""
    try:
        if os.path.getsize(LOG) > 102400:
            with open(LOG, 'r') as f:
                lines = f.readlines()
            with open(LOG, 'w') as f:
                f.writelines(lines[-50:])
    except:
        pass

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def vikunja_get(path):
    req = urllib.request.Request(
        VIKUNJA_URL + path,
        headers={'Authorization': f'Bearer {VIKUNJA_TOKEN}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f'API error {path}: {e}')
        return []

def bark_push(title, body, group='vikunja', level='active', sound='minuet'):
    """Send push notification via Bark"""
    if BARK_KEY == 'REPLACE_ME':
        log(f'[DRY RUN] {title}: {body}')
        return
    params = urllib.parse.urlencode({
        'group': group,
        'level': level,
        'sound': sound,
        'isArchive': 1
    })
    safe_title = urllib.parse.quote(title, safe='')
    safe_body = urllib.parse.quote(body, safe='')
    url = f'https://api.day.app/{BARK_KEY}/{safe_title}/{safe_body}?{params}'
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get('code') == 200:
                log(f'Pushed: {title}')
            else:
                log(f'Bark error: {result}')
    except Exception as e:
        log(f'Bark failed: {e}')

TZ = datetime.timezone(datetime.timedelta(hours=8))

def parse_due(due_str):
    try:
        return datetime.datetime.fromisoformat(due_str.replace('Z', '+00:00')).astimezone(TZ)
    except:
        return None

def main():
    rotate_log()
    now = datetime.datetime.now(TZ)
    hour = now.hour

    # Quiet hours check
    if hour >= QUIET_START or hour < QUIET_END:
        return

    # Fetch all undone tasks with due dates
    tasks = []
    for pid, pname in PROJECTS.items():
        items = vikunja_get(f'/projects/{pid}/tasks')
        if not isinstance(items, list):
            continue
        for t in items:
            if t.get('done'):
                continue
            due = t.get('due_date', '')
            if not due or due.startswith('0001'):
                continue
            due_local = parse_due(due)
            if not due_local:
                continue
            tasks.append({
                'id': t['id'],
                'title': t['title'],
                'due_local': due_local,
                'priority': t.get('priority', 3),
                'project': pname
            })

    if not tasks:
        return

    state = load_state()
    today = now.strftime('%Y-%m-%d')
    pushed = 0

    # === Phase 1: urgent individual pushes (overdue + <=15min) ===
    urgent, due_today, due_tomorrow, due_soon = [], [], [], []

    for t in tasks:
        diff = (t['due_local'].date() - now.date()).days
        minutes_left = (t['due_local'] - now).total_seconds() / 60
        tid = str(t['id'])

        if minutes_left < 0:
            # Overdue: push once per day per task
            overdue_key = f'{tid}_overdue'
            if state.get(overdue_key) != today:
                overdue_days = max(1, -diff)
                bark_push(
                    f'⚠️ 已过期！{t["title"]}',
                    f'{t["project"]} | 过期{overdue_days}天',
                    level='timeSensitive', sound='alarm'
                )
                state[overdue_key] = today
                pushed += 1
            urgent.append(t)
        elif minutes_left <= 15:
            # Due within 15 min: push once ever
            urgent_key = f'{tid}_urgent'
            if not state.get(urgent_key):
                mins = int(minutes_left)
                bark_push(
                    f'🚨 {mins}分钟后截止！{t["title"]}',
                    f'{t["project"]}',
                    level='timeSensitive', sound='alarm'
                )
                state[urgent_key] = today
                pushed += 1
            urgent.append(t)
        elif diff == 0:
            due_today.append(t)
        elif diff == 1:
            due_tomorrow.append(t)
        elif diff <= 3:
            due_soon.append(t)

    # === Phase 2: summary pushes (once per day) ===
    summary_key = f'summary_{today}'
    if not state.get(summary_key):
        # Today summary
        if due_today:
            due_today.sort(key=lambda t: t['due_local'])
            lines = [f'{t["title"]} ({t["due_local"].hour}:{t["due_local"].minute:02d})' for t in due_today]
            bark_push(
                f'⏰ 今日截止 ({len(due_today)})',
                '\n'.join(lines),
                level='timeSensitive', sound='bell'
            )
            pushed += 1

        # Tomorrow summary
        if due_tomorrow:
            lines = [t['title'] for t in due_tomorrow]
            bark_push(
                f'📋 明日截止 ({len(due_tomorrow)})',
                '\n'.join(lines)
            )
            pushed += 1

        # 3-day summary
        if due_soon:
            lines = [f'{t["title"]} ({(t["due_local"].date() - now.date()).days}天后)' for t in due_soon]
            bark_push(
                f'📅 近期待办 ({len(due_soon)})',
                '\n'.join(lines)
            )
            pushed += 1

        state[summary_key] = True

    save_state(state)
    log(f'Pushed {pushed} (overdue:{len(urgent)} today:{len(due_today)} tmr:{len(due_tomorrow)} soon:{len(due_soon)})')

if __name__ == '__main__':
    main()
