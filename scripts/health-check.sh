#!/bin/bash
# Health check - runs every 5 minutes, alerts on failure
BARK_KEY="YOUR_BARK_KEY"
STATE="/root/.health-state"
ALERT_COOLDOWN=1800  # 30 min between alerts for same service

alert() {
    local svc="$1" msg="$2"
    local key="${svc}_$(date +%Y%m%d%H)"
    # Check cooldown
    if [ -f "$STATE" ] && grep -q "$key" "$STATE" 2>/dev/null; then
        return
    fi
    curl -s "https://api.day.app/${BARK_KEY}/$(python3 -c "import urllib.parse;print(urllib.parse.quote('⚠️ $svc 异常'))")/$(python3 -c "import urllib.parse;print(urllib.parse.quote('$msg'))")?group=health&sound=alarm&level=timeSensitive" > /dev/null 2>&1
    echo "$key" >> "$STATE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT: $svc - $msg" >> /root/reminder.log
}

# 1. chat-server
if ! curl -s -o /dev/null --max-time 5 http://127.0.0.1:3457/api/health; then
    alert "chat-server" "API 无响应，尝试重启..."
    systemctl restart chat-server
fi

# 2. Nginx
if ! systemctl is-active --quiet nginx; then
    alert "nginx" "Nginx 已停止"
fi

# 3. Vikunja
if ! curl -s -o /dev/null --max-time 5 http://127.0.0.1:8086/api/v1/info; then
    alert "vikunja" "Vikunja 无响应"
fi

# 4. Memos
if ! curl -s -o /dev/null --max-time 5 http://127.0.0.1:8083/api/v1/memos?pageSize=1; then
    alert "memos" "Memos 无响应"
fi

# 5. Cloudflare Tunnel
if ! systemctl is-active --quiet cloudflared; then
    alert "cloudflared" "Tunnel 已断开"
fi

# Clean old state (keep today only)
if [ -f "$STATE" ]; then
    grep "$(date +%Y%m%d)" "$STATE" > "${STATE}.tmp" 2>/dev/null
    mv "${STATE}.tmp" "$STATE" 2>/dev/null
fi
