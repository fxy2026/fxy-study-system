#!/bin/bash
# Quartz smart build - only rebuild if vault has changes
LOG=/root/quartz-build.log
STAMP=/tmp/quartz-last-build
APPS_BACKUP=/root/apps-backup

# Log rotation: keep under 100KB
if [ -f "$LOG" ] && [ $(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null) -gt 102400 ]; then
    tail -50 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

# Check if any .md file changed since last build
CHANGED=$(find /root/webdav-vault -name '*.md' -newer $STAMP -print 2>/dev/null | head -1)
if [ -z "$CHANGED" ] && [ -f $STAMP ]; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Build started (trigger: ${CHANGED:-initial})" >> $LOG

# Ensure apps backup exists
mkdir -p $APPS_BACKUP

# Backup apps before build (Quartz clears public/)
if [ -d /root/quartz/public/apps ]; then
    cp -r /root/quartz/public/apps/* $APPS_BACKUP/ 2>/dev/null
fi

cd /root/quartz
npx quartz build -o /root/quartz/public >> $LOG 2>&1
BUILD_EXIT=$?

# Always restore apps (even if build failed)
mkdir -p /root/quartz/public/apps
cp $APPS_BACKUP/* /root/quartz/public/apps/ 2>/dev/null
chmod -R 755 /root/quartz/public/apps

if [ $BUILD_EXIT -eq 0 ]; then
    touch $STAMP
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Build finished (exit: $BUILD_EXIT)" >> $LOG
echo '---' >> $LOG
