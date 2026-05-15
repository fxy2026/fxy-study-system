// Universal Web Clipper for Obsidian Study System
// Works on any webpage: select text → right-click → save, or full article clip

(function() {
  'use strict';
  if (window.__clipperLoaded) return;
  window.__clipperLoaded = true;

  const API = 'https://kb.xpy.me/api/clip';
  const SUBJECTS = ['高数II','有机化学','概率统计','大学物理','分析化学','普通生物学','AI基础','习思想','其他'];

  // Listen for messages from background script
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === 'clip-selection') {
      const sel = window.getSelection().toString().trim();
      if (sel) showClipDialog(sel, 'selection');
      else showClipDialog(null, 'full');
    } else if (msg.action === 'clip-full') {
      showClipDialog(null, 'full');
    }
  });

  function extractArticle() {
    // Try to get clean article content
    // 1. Specific site selectors
    const host = location.hostname;
    let article = null;

    if (host.includes('csdn.net')) {
      article = document.querySelector('#content_views, .article_content, .blog-content-box');
    } else if (host.includes('zhihu.com')) {
      article = document.querySelector('.Post-RichText, .RichContent-inner, .AnswerItem-body');
    } else if (host.includes('oc.sjtu.edu.cn')) {
      article = document.querySelector('.show-content, #content, .user_content');
    } else if (host.includes('jianshu.com')) {
      article = document.querySelector('.article, ._2rhmJa');
    }

    // 2. Generic article detection
    if (!article) {
      article = document.querySelector('article, [role="main"], .post-content, .entry-content, .article-body, main');
    }

    // 3. Fallback: body
    if (!article) article = document.body;

    // Clean up: remove scripts, styles, nav, ads
    const clone = article.cloneNode(true);
    clone.querySelectorAll('script, style, nav, .sidebar, .comment, .ad, .toolbar, .social-share, .recommend, .related').forEach(el => el.remove());

    return clone.innerHTML;
  }

  function htmlToMarkdown(html) {
    // Simple HTML → Markdown conversion
    let md = html;
    // Headers
    md = md.replace(/<h1[^>]*>(.*?)<\/h1>/gi, '\n# $1\n');
    md = md.replace(/<h2[^>]*>(.*?)<\/h2>/gi, '\n## $1\n');
    md = md.replace(/<h3[^>]*>(.*?)<\/h3>/gi, '\n### $1\n');
    md = md.replace(/<h4[^>]*>(.*?)<\/h4>/gi, '\n#### $1\n');
    // Bold, italic
    md = md.replace(/<strong[^>]*>(.*?)<\/strong>/gi, '**$1**');
    md = md.replace(/<b[^>]*>(.*?)<\/b>/gi, '**$1**');
    md = md.replace(/<em[^>]*>(.*?)<\/em>/gi, '*$1*');
    md = md.replace(/<i[^>]*>(.*?)<\/i>/gi, '*$1*');
    // Code
    md = md.replace(/<code[^>]*>(.*?)<\/code>/gi, '`$1`');
    md = md.replace(/<pre[^>]*>(.*?)<\/pre>/gis, '\n```\n$1\n```\n');
    // Links
    md = md.replace(/<a[^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/gi, '[$2]($1)');
    // Images
    md = md.replace(/<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*\/?>/gi, '![$2]($1)');
    md = md.replace(/<img[^>]*src="([^"]*)"[^>]*\/?>/gi, '![]($1)');
    // Lists
    md = md.replace(/<li[^>]*>(.*?)<\/li>/gi, '- $1\n');
    md = md.replace(/<\/?[uo]l[^>]*>/gi, '\n');
    // Paragraphs, breaks
    md = md.replace(/<br\s*\/?>/gi, '\n');
    md = md.replace(/<p[^>]*>(.*?)<\/p>/gis, '\n$1\n');
    md = md.replace(/<blockquote[^>]*>(.*?)<\/blockquote>/gis, '\n> $1\n');
    // Table basic
    md = md.replace(/<tr[^>]*>(.*?)<\/tr>/gis, '|$1|\n');
    md = md.replace(/<t[hd][^>]*>(.*?)<\/t[hd]>/gi, ' $1 |');
    // Strip remaining tags
    md = md.replace(/<[^>]+>/g, '');
    // Clean up whitespace
    md = md.replace(/\n{3,}/g, '\n\n');
    md = md.replace(/&nbsp;/g, ' ');
    md = md.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    return md.trim();
  }

  function showClipDialog(selection, mode) {
    // Remove existing dialog
    const old = document.getElementById('obsidian-clipper-dialog');
    if (old) old.remove();

    const content = mode === 'selection' ? selection : htmlToMarkdown(extractArticle());
    const preview = content.slice(0, 150) + (content.length > 150 ? '...' : '');

    const dialog = document.createElement('div');
    dialog.id = 'obsidian-clipper-dialog';
    dialog.innerHTML = `
      <div style="font-weight:700;font-size:15px;margin-bottom:10px">📋 保存到 Obsidian</div>
      <div style="font-size:12px;color:#aaa;margin-bottom:10px;max-height:60px;overflow:hidden">${preview}</div>
      <div style="margin-bottom:8px">
        <label style="font-size:12px;color:#aaa">科目</label>
        <select id="clip-subject" style="width:100%;padding:6px;border-radius:6px;border:1px solid #555;background:#2a2a3e;color:#e0e0e0;font-size:13px;margin-top:2px">
          <option value="auto">自动检测</option>
          ${SUBJECTS.map(s => `<option value="${s}">${s}</option>`).join('')}
        </select>
      </div>
      <div style="margin-bottom:10px;font-size:12px">
        <label style="display:flex;align-items:center;gap:4px;margin:4px 0;cursor:pointer">
          <input type="checkbox" id="clip-summary" checked> AI 生成摘要
        </label>
        <label style="display:flex;align-items:center;gap:4px;margin:4px 0;cursor:pointer">
          <input type="checkbox" id="clip-flashcards"> 生成闪卡
        </label>
        <label style="display:flex;align-items:center;gap:4px;margin:4px 0;cursor:pointer">
          <input type="checkbox" id="clip-links" checked> 关联已有笔记
        </label>
      </div>
      <div style="display:flex;gap:8px">
        <button id="clip-save" style="flex:1;padding:8px;background:#2a9d8f;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px">保存</button>
        <button id="clip-cancel" style="flex:1;padding:8px;background:#444;color:#aaa;border:none;border-radius:8px;cursor:pointer;font-size:13px">取消</button>
      </div>
      <div id="clip-status" style="margin-top:8px;font-size:12px;text-align:center"></div>
    `;

    Object.assign(dialog.style, {
      position: 'fixed', bottom: '20px', right: '20px', zIndex: '99999',
      background: '#1a1a2e', border: '1px solid #4361ee', borderRadius: '14px',
      padding: '16px', width: '280px', boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
      color: '#e0e0e0', fontFamily: '-apple-system, "Noto Sans SC", sans-serif'
    });

    document.body.appendChild(dialog);

    document.getElementById('clip-cancel').onclick = () => dialog.remove();
    document.getElementById('clip-save').onclick = async () => {
      const btn = document.getElementById('clip-save');
      const status = document.getElementById('clip-status');
      btn.disabled = true;
      btn.textContent = '保存中...';
      status.textContent = 'AI 正在处理...';
      status.style.color = '#7c8cf8';

      try {
        const resp = await fetch(API, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: location.href,
            title: document.title,
            content: mode === 'full' ? content : undefined,
            selection: mode === 'selection' ? content : undefined,
            subject: document.getElementById('clip-subject').value,
            options: {
              summary: document.getElementById('clip-summary').checked,
              flashcards: document.getElementById('clip-flashcards').checked,
              link_notes: document.getElementById('clip-links').checked
            }
          })
        });
        const data = await resp.json();
        if (data.success) {
          status.textContent = `✅ 已保存 → ${data.subject}`;
          status.style.color = '#2a9d8f';
          btn.textContent = '已保存';
          // Save to recent clips
          const recent = JSON.parse(localStorage.getItem('clip_recent') || '[]');
          recent.unshift({ title: document.title.slice(0, 30), subject: data.subject, date: new Date().toISOString().slice(0, 10) });
          localStorage.setItem('clip_recent', JSON.stringify(recent.slice(0, 10)));
          setTimeout(() => dialog.remove(), 2000);
        } else {
          throw new Error(data.error || 'Unknown error');
        }
      } catch(e) {
        status.textContent = '❌ ' + e.message;
        status.style.color = '#e76f51';
        btn.disabled = false;
        btn.textContent = '重试';
      }
    };
  }
})();
