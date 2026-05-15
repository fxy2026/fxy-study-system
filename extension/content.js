// Gemini to Obsidian - Content Script
(function () {
  'use strict';

  const CONFIG = {
    API_URL: 'https://kb.xpy.me/api/smart-note',
    SELECTORS: {
      CHAT_CONTAINER: '[data-test-id="chat-history-container"]',
      CONVERSATION_TURN: 'div.conversation-container',
      USER_QUERY: 'user-query',
      USER_QUERY_TEXT: '.query-text .query-text-line',
      MODEL_RESPONSE: 'model-response',
      MODEL_RESPONSE_CONTENT: 'message-content .markdown',
      CONVERSATION_TITLE: '[data-test-id="conversation-title"]',
      MATH_BLOCK: '.math-block[data-math]',
      MATH_INLINE: '.math-inline[data-math]',
    },
    SUBJECTS: [
      { id: 'auto', name: 'AI 自动识别' },
      { id: '高数II', name: '高数 II' },
      { id: '有机化学', name: '有机化学' },
      { id: '概率统计', name: '概率统计' },
      { id: '大学物理', name: '大学物理' },
      { id: '分析化学', name: '分析化学' },
      { id: '普通生物学', name: '普通生物学' },
      { id: 'AI基础', name: 'AI 基础' },
      { id: '习思想', name: '习思想' },
      { id: '其他', name: '其他' },
    ],
  };

  // ========== Markdown Converter ==========
  function createConverter() {
    if (typeof TurndownService === 'undefined') return null;
    const svc = new TurndownService({
      headingStyle: 'atx',
      codeBlockStyle: 'fenced',
      bulletListMarker: '-',
    });

    // Math block: $$...$$
    svc.addRule('mathBlock', {
      filter: (node) =>
        node.nodeType === 1 && node.matches && node.matches(CONFIG.SELECTORS.MATH_BLOCK),
      replacement: (_content, node) => {
        const latex = node.getAttribute('data-math') || '';
        return `\n\n$$\n${latex}\n$$\n\n`;
      },
    });

    // Math inline: $...$
    svc.addRule('mathInline', {
      filter: (node) =>
        node.nodeType === 1 && node.matches && node.matches(CONFIG.SELECTORS.MATH_INLINE),
      replacement: (_content, node) => {
        const latex = node.getAttribute('data-math') || '';
        return `$${latex}$`;
      },
    });

    // Images
    svc.addRule('images', {
      filter: 'img',
      replacement: (_content, node) => {
        const src = node.getAttribute('src') || '';
        const alt = node.getAttribute('alt') || 'image';
        if (src.startsWith('data:')) return `![${alt}](embedded-image)`;
        return `![${alt}](${src})`;
      },
    });

    // Code blocks - preserve language
    svc.addRule('codeBlock', {
      filter: (node) => node.nodeName === 'PRE',
      replacement: (_content, node) => {
        const code = node.querySelector('code');
        if (!code) return `\n\`\`\`\n${node.textContent}\n\`\`\`\n`;
        const lang = (code.className.match(/language-(\w+)/) || [])[1] || '';
        return `\n\`\`\`${lang}\n${code.textContent}\n\`\`\`\n`;
      },
    });

    return svc;
  }

  // ========== Pre-process: convert tables in DOM before Turndown ==========
  function convertTableToMarkdown(tableEl) {
    const rows = tableEl.querySelectorAll('tr');
    if (rows.length === 0) return '';
    const lines = [];
    rows.forEach((row, ri) => {
      const cells = row.querySelectorAll('th, td');
      const cellTexts = [];
      cells.forEach((cell) => {
        // Handle math inside cells
        let t = '';
        cell.childNodes.forEach((cn) => {
          if (cn.nodeType === 3) {
            t += cn.textContent;
          } else if (cn.nodeType === 1) {
            if (cn.getAttribute && cn.getAttribute('data-math')) {
              const latex = cn.getAttribute('data-math');
              t += cn.classList.contains('math-block') ? `$$${latex}$$` : `$${latex}$`;
            } else if (cn.tagName === 'STRONG' || cn.tagName === 'B') {
              t += `**${cn.textContent}**`;
            } else if (cn.tagName === 'EM' || cn.tagName === 'I') {
              t += `*${cn.textContent}*`;
            } else if (cn.tagName === 'CODE') {
              t += `\`${cn.textContent}\``;
            } else {
              // Recurse into nested elements
              t += cn.textContent;
            }
          }
        });
        cellTexts.push(t.trim().replace(/\|/g, '\\|').replace(/\n+/g, ' '));
      });
      lines.push(`| ${cellTexts.join(' | ')} |`);
      if (ri === 0) {
        lines.push(`| ${cellTexts.map(() => '---').join(' | ')} |`);
      }
    });
    return '\n\n' + lines.join('\n') + '\n\n';
  }

  function preProcessElement(el) {
    // Clone so we don't mutate the original page DOM
    const clone = el.cloneNode(true);
    // Replace all tables with pre-rendered markdown text nodes
    clone.querySelectorAll('table').forEach((table) => {
      const md = convertTableToMarkdown(table);
      const placeholder = document.createElement('pre');
      placeholder.setAttribute('data-gto-table', 'true');
      placeholder.textContent = md;
      table.replaceWith(placeholder);
    });
    return clone;
  }

  // ========== Extract Conversation ==========
  function extractConversation() {
    const turns = document.querySelectorAll(CONFIG.SELECTORS.CONVERSATION_TURN);
    const messages = [];
    const images = [];

    turns.forEach((turn, idx) => {
      // User query
      const userEl = turn.querySelector(CONFIG.SELECTORS.USER_QUERY);
      if (userEl) {
        const textEl = userEl.querySelector(CONFIG.SELECTORS.USER_QUERY_TEXT);
        const text = textEl ? textEl.textContent.trim() : '';

        // Check for user-uploaded images (search broadly in the turn container)
        const imgRefs = [];
        const searchAreas = [userEl, turn];
        const seenSrcs = new Set();
        for (const area of searchAreas) {
          area.querySelectorAll('img').forEach((img) => {
            const src = img.src || img.getAttribute('data-src') || '';
            if (!src || seenSrcs.has(src)) return;
            // Skip avatars, icons, small UI elements
            if (src.includes('avatar') || src.includes('icon') || src.includes('favicon')) return;
            if (img.width < 30 && img.height < 30 && img.naturalWidth < 30) return;
            seenSrcs.add(src);
            const imgId = `user_img_${idx}_${imgRefs.length}`;
            imgRefs.push(imgId);
            images.push({ id: imgId, src: src, type: 'user_upload' });
          });
          if (imgRefs.length > 0) break; // Found images, no need to search wider
        }

        if (text || imgRefs.length > 0) {
          messages.push({ role: 'user', content: text, images: imgRefs });
        }
      }

      // Model response
      const modelEl = turn.querySelector(CONFIG.SELECTORS.MODEL_RESPONSE);
      if (modelEl) {
        const contentEl = modelEl.querySelector(CONFIG.SELECTORS.MODEL_RESPONSE_CONTENT);
        if (contentEl) {
          const processed = preProcessElement(contentEl);
          const converter = createConverter();
          let md;
          if (converter) {
            // Add rule to preserve our pre-converted tables
            converter.addRule('gtoTable', {
              filter: (node) => node.nodeName === 'PRE' && node.getAttribute('data-gto-table'),
              replacement: (_c, node) => node.textContent,
            });
            md = converter.turndown(processed);
          } else {
            md = fallbackExtract(processed);
          }
          // Clean citation markers
          md = md.replace(/\[cite_start\]/g, '').replace(/\[cite:\d+(?:,\d+)*\]/g, '');
          messages.push({ role: 'model', content: md.trim() });
        }
      }
    });

    // Get conversation title
    const titleEl = document.querySelector(CONFIG.SELECTORS.CONVERSATION_TITLE);
    const title = titleEl ? titleEl.textContent.trim() : '';

    return { title, messages, images };
  }

  // Fallback if TurndownService not available
  function fallbackExtract(el) {
    let text = '';
    el.childNodes.forEach((node) => {
      if (node.nodeType === 3) {
        text += node.textContent;
      } else if (node.nodeType === 1) {
        const tag = node.tagName;
        if (node.matches && node.matches(CONFIG.SELECTORS.MATH_BLOCK)) {
          text += `\n\n$$\n${node.getAttribute('data-math') || ''}\n$$\n\n`;
        } else if (node.matches && node.matches(CONFIG.SELECTORS.MATH_INLINE)) {
          text += `$${node.getAttribute('data-math') || ''}$`;
        } else if (tag === 'PRE') {
          const code = node.querySelector('code');
          const lang = code ? (code.className.match(/language-(\w+)/) || [])[1] || '' : '';
          text += `\n\`\`\`${lang}\n${(code || node).textContent}\n\`\`\`\n`;
        } else if (tag === 'TABLE') {
          const rows = node.querySelectorAll('tr');
          const lines = [];
          rows.forEach((row, ri) => {
            const cells = Array.from(row.querySelectorAll('th, td'));
            lines.push(`| ${cells.map(c => c.textContent.trim().replace(/\|/g, '\\|').replace(/\n/g, ' ')).join(' | ')} |`);
            if (ri === 0) lines.push(`| ${cells.map(() => '---').join(' | ')} |`);
          });
          text += `\n${lines.join('\n')}\n`;
        } else if (tag === 'H1') text += `\n# ${node.textContent}\n`;
        else if (tag === 'H2') text += `\n## ${node.textContent}\n`;
        else if (tag === 'H3') text += `\n### ${node.textContent}\n`;
        else if (tag === 'LI') text += `- ${node.textContent}\n`;
        else if (tag === 'P') text += `\n${fallbackExtract(node)}\n`;
        else if (tag === 'STRONG' || tag === 'B') text += `**${node.textContent}**`;
        else if (tag === 'EM' || tag === 'I') text += `*${node.textContent}*`;
        else if (tag === 'CODE') text += `\`${node.textContent}\``;
        else text += fallbackExtract(node);
      }
    });
    return text;
  }

  // Convert image to base64 - try multiple methods to bypass CORS
  async function imgToBase64(src) {
    // Method 1: Draw to canvas (works for same-origin and CORS-enabled images)
    try {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      const loaded = await new Promise((resolve, reject) => {
        img.onload = () => resolve(true);
        img.onerror = () => reject();
        img.src = src;
      });
      if (loaded) {
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        canvas.getContext('2d').drawImage(img, 0, 0);
        return canvas.toDataURL('image/png');
      }
    } catch {}

    // Method 2: Find the img element on page and draw it directly (no CORS needed for page elements)
    try {
      const pageImg = document.querySelector(`img[src="${src}"]`);
      if (pageImg && pageImg.naturalWidth > 0) {
        const canvas = document.createElement('canvas');
        canvas.width = pageImg.naturalWidth;
        canvas.height = pageImg.naturalHeight;
        canvas.getContext('2d').drawImage(pageImg, 0, 0);
        return canvas.toDataURL('image/png');
      }
    } catch {}

    // Method 3: Fetch as blob
    try {
      const res = await fetch(src);
      const blob = await res.blob();
      return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result);
        reader.readAsDataURL(blob);
      });
    } catch {}

    // Method 4: If it's a blob URL, try direct fetch
    if (src.startsWith('blob:')) {
      try {
        const res = await fetch(src);
        const blob = await res.blob();
        return new Promise((resolve) => {
          const reader = new FileReader();
          reader.onloadend = () => resolve(reader.result);
          reader.readAsDataURL(blob);
        });
      } catch {}
    }

    // Method 5: Fetch via background service worker (bypasses CORS)
    try {
      const resp = await chrome.runtime.sendMessage({ type: 'fetchImage', url: src });
      if (resp && resp.success) return resp.data;
    } catch {}

    return null;
  }

  // ========== UI ==========
  function createUI() {
    // FAB button
    const fab = document.createElement('button');
    fab.id = 'gto-fab';
    fab.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/><rect x="3" y="3" width="18" height="18" rx="3"/></svg>`;
    fab.title = 'Save to Obsidian';
    document.body.appendChild(fab);

    // Panel
    const panel = document.createElement('div');
    panel.id = 'gto-panel';
    panel.innerHTML = `
      <h3>Save to Obsidian</h3>
      <label>Subject</label>
      <select id="gto-subject">
        ${CONFIG.SUBJECTS.map((s) => `<option value="${s.id}">${s.name}</option>`).join('')}
      </select>
      <label>Custom Title (optional)</label>
      <input type="text" id="gto-title" placeholder="Leave empty for auto title" />
      <label>Additional Tags (comma separated)</label>
      <input type="text" id="gto-tags" placeholder="e.g. exam,ch3" />
      <label>Save Range</label>
      <select id="gto-range">
        <option value="all">Entire Conversation</option>
        <option value="last">Last Q&A Only</option>
        <option value="last3">Last 3 Q&A</option>
      </select>
      <div class="gto-btn-row">
        <button class="gto-btn gto-secondary" id="gto-cancel">Cancel</button>
        <button class="gto-btn gto-primary" id="gto-save">Save</button>
      </div>
      <div id="gto-status"></div>
    `;
    document.body.appendChild(panel);

    // Events
    fab.addEventListener('click', () => panel.classList.toggle('show'));
    document.getElementById('gto-cancel').addEventListener('click', () =>
      panel.classList.remove('show')
    );
    document.getElementById('gto-save').addEventListener('click', handleSave);
  }

  // ========== Save Logic ==========
  async function handleSave() {
    const status = document.getElementById('gto-status');
    const saveBtn = document.getElementById('gto-save');
    status.className = 'loading';
    status.textContent = 'Extracting...';
    saveBtn.disabled = true;

    try {
      const data = extractConversation();
      if (data.messages.length === 0) {
        throw new Error('No conversation content found');
      }

      // Apply range filter
      const range = document.getElementById('gto-range').value;
      if (range !== 'all') {
        const n = range === 'last' ? 1 : 3;
        // Group into Q&A pairs
        const pairs = [];
        let current = null;
        data.messages.forEach((m) => {
          if (m.role === 'user') {
            current = { user: m, model: null };
            pairs.push(current);
          } else if (m.role === 'model' && current) {
            current.model = m;
          }
        });
        const selected = pairs.slice(-n);
        data.messages = selected.flatMap((p) =>
          [p.user, p.model].filter(Boolean)
        );
        // Filter images
        const keepImgs = new Set(data.messages.flatMap((m) => m.images || []));
        data.images = data.images.filter((img) => keepImgs.has(img.id));
      }

      status.textContent = 'Processing images...';

      // Convert images to base64
      const imageData = [];
      for (const img of data.images) {
        const b64 = await imgToBase64(img.src);
        if (b64) {
          imageData.push({ id: img.id, data: b64, type: img.type });
        }
      }

      status.textContent = 'Sending to server...';

      const subject = document.getElementById('gto-subject').value;
      const customTitle = document.getElementById('gto-title').value.trim();
      const tags = document
        .getElementById('gto-tags')
        .value.split(',')
        .map((t) => t.trim())
        .filter(Boolean);

      const payload = {
        title: customTitle || data.title,
        subject: subject,
        messages: data.messages,
        images: imageData,
        tags: tags,
        source: 'gemini',
        url: window.location.href,
      };

      const res = await fetch(CONFIG.API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`Server error: ${err}`);
      }

      const result = await res.json();
      status.className = 'success';
      status.textContent = `Saved: ${result.path}`;

      setTimeout(() => {
        document.getElementById('gto-panel').classList.remove('show');
        status.textContent = '';
      }, 3000);
    } catch (err) {
      status.className = 'error';
      status.textContent = err.message;
    } finally {
      saveBtn.disabled = false;
    }
  }

  // ========== Init ==========
  // Wait for page to be ready
  function init() {
    if (document.getElementById('gto-fab')) return;
    createUI();
  }

  // Gemini is SPA, observe for navigation
  if (document.readyState === 'complete') {
    init();
  } else {
    window.addEventListener('load', init);
  }

  // Re-inject on SPA navigation
  const observer = new MutationObserver(() => {
    if (!document.getElementById('gto-fab')) init();
  });
  observer.observe(document.body, { childList: true, subtree: false });
})();
