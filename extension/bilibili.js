// Bilibili Video Course Tracker
// Auto-detect course videos, track playback progress, sync to study system

(function() {
  'use strict';
  const API = 'https://kb.xpy.me/api/video-progress';
  const SAVE_INTERVAL = 30000;
  const SUBJECTS = ['高数II','有机化学','概率统计','大学物理','分析化学','普通生物学','AI基础','习思想'];
  const KEYWORDS = {
    '分析化学': ['分析化学','滴定','酸碱平衡','配位','氧化还原','李静红','沉淀滴定','分光光度'],
    '高数II': ['高等数学','微积分','高数','数学分析','多元函数','重积分','级数'],
    '有机化学': ['有机化学','有机反应','有机合成','烷烃','烯烃','芳烃'],
    '概率统计': ['概率论','数理统计','概率统计','随机变量','假设检验'],
    '大学物理': ['大学物理','普通物理','力学','电磁学','光学','热力学'],
    '普通生物学': ['普通生物','细胞生物','分子生物','遗传学','进化'],
    'AI基础': ['人工智能','机器学习','深度学习','神经网络'],
    '习思想': ['习近平','新时代','思想政治','马克思'],
  };

  let tracking = false;
  let subject = '';
  let saveTimer = null;
  let floatEl = null;

  // --- Utils ---
  function getBV() {
    const m = location.pathname.match(/\/video\/(BV\w+)/);
    return m ? m[1] : '';
  }

  function getEpisode() {
    const urlP = new URLSearchParams(location.search).get('p');
    if (urlP) return parseInt(urlP);
    const el = document.querySelector('.bpx-player-ctrl-eplist-menu-item.bpx-state-active, .list-box .on, .cur-page');
    if (el) { const m = el.textContent.match(/(\d+)/); if (m) return parseInt(m[1]); }
    return 1;
  }

  function getProgress() {
    const v = document.querySelector('video');
    if (v && v.duration > 0) return Math.round(v.currentTime / v.duration * 100);
    return 0;
  }

  function getTotalEpisodes() {
    const items = document.querySelectorAll('.bpx-player-ctrl-eplist-menu-item, .list-box li');
    return items.length > 1 ? items.length : 0;
  }

  function detectSubject() {
    const title = (document.title + ' ' + (document.querySelector('.video-title, h1')?.textContent || '')).toLowerCase();
    for (const [subj, kws] of Object.entries(KEYWORDS)) {
      for (const kw of kws) {
        if (title.includes(kw.toLowerCase())) return subj;
      }
    }
    return '';
  }

  // --- UI ---
  function createPromptCard(detectedSubject) {
    const card = document.createElement('div');
    card.id = 'study-tracker-prompt';
    card.innerHTML = `
      <div style="font-weight:700;font-size:14px;margin-bottom:8px">📚 检测到课程视频</div>
      <div style="margin-bottom:10px">
        <select id="st-subject" style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid #555;background:#2a2a3e;color:#e0e0e0;font-size:13px">
          ${SUBJECTS.map(s => `<option value="${s}" ${s === detectedSubject ? 'selected' : ''}>${s}</option>`).join('')}
          <option value="_custom">其他（手动输入）</option>
        </select>
      </div>
      <div style="display:flex;gap:8px">
        <button id="st-start" style="flex:1;padding:8px;background:#2a9d8f;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px">开始追踪</button>
        <button id="st-skip" style="flex:1;padding:8px;background:#444;color:#aaa;border:none;border-radius:8px;cursor:pointer;font-size:13px">忽略</button>
      </div>
    `;
    Object.assign(card.style, {
      position: 'fixed', bottom: '20px', right: '20px', zIndex: '99999',
      background: '#1a1a2e', border: '1px solid #4361ee', borderRadius: '14px',
      padding: '16px', width: '240px', boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
      color: '#e0e0e0', fontFamily: '-apple-system, "Noto Sans SC", sans-serif'
    });
    document.body.appendChild(card);

    document.getElementById('st-start').onclick = () => {
      const sel = document.getElementById('st-subject');
      let subj = sel.value;
      if (subj === '_custom') {
        subj = prompt('输入科目名称：') || '';
        if (!subj) return;
      }
      subject = subj;
      localStorage.setItem('st_course_' + getBV(), subj);
      localStorage.setItem('st_track_' + getBV(), 'true');
      card.remove();
      startTracking();
    };

    document.getElementById('st-skip').onclick = () => {
      localStorage.setItem('st_track_' + getBV(), 'false');
      card.remove();
    };
  }

  function createFloatBubble() {
    floatEl = document.createElement('div');
    floatEl.id = 'study-tracker-float';
    Object.assign(floatEl.style, {
      position: 'fixed', bottom: '20px', right: '20px', zIndex: '99999',
      background: '#1a1a2e', border: '1px solid #2a9d8f', borderRadius: '10px',
      padding: '8px 14px', color: '#e0e0e0', fontSize: '13px',
      fontFamily: '-apple-system, "Noto Sans SC", sans-serif',
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)', cursor: 'pointer',
      transition: 'all 0.2s', userSelect: 'none'
    });
    floatEl.title = '点击暂停追踪';
    floatEl.onclick = () => {
      if (tracking) {
        stopTracking();
        floatEl.style.borderColor = '#e76f51';
        floatEl.innerHTML = '⏸ 已暂停 <span style="font-size:11px;color:#888">点击继续</span>';
        floatEl.onclick = () => { startTracking(); };
      }
    };
    document.body.appendChild(floatEl);
    updateFloat();
  }

  function updateFloat() {
    if (!floatEl) return;
    const ep = getEpisode();
    const pct = getProgress();
    const total = getTotalEpisodes();
    const totalStr = total > 0 ? `/${total}` : '';
    floatEl.innerHTML = `📺 P${ep}${totalStr} · <b>${pct}%</b> · ${subject}`;
    floatEl.style.borderColor = '#2a9d8f';
  }

  function showToast(msg) {
    const t = document.createElement('div');
    Object.assign(t.style, {
      position: 'fixed', top: '20px', right: '20px', zIndex: '99999',
      background: '#2a9d8f', color: '#fff', padding: '10px 18px',
      borderRadius: '10px', fontSize: '13px', boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
      transition: 'opacity 0.5s', fontFamily: '-apple-system, "Noto Sans SC", sans-serif'
    });
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 500); }, 3000);
  }

  // --- Tracking ---
  function saveProgress() {
    if (!tracking || !subject) return;
    const ep = getEpisode();
    const pct = getProgress();
    const total = getTotalEpisodes();

    fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject, episode: ep, progress_pct: pct,
        total: total || undefined,
        url: location.href,
        title: document.title
      })
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        updateFloat();
        if (data.just_completed) {
          showToast(`✅ P${ep} 已完成！(${data.completed}/${data.total})`);
        }
      }
    }).catch(() => {});
  }

  function startTracking() {
    tracking = true;
    if (!floatEl) createFloatBubble();
    updateFloat();
    saveProgress(); // immediate save
    saveTimer = setInterval(saveProgress, SAVE_INTERVAL);

    // Monitor episode changes (SPA navigation)
    let lastEp = getEpisode();
    setInterval(() => {
      const ep = getEpisode();
      if (ep !== lastEp) {
        lastEp = ep;
        saveProgress();
        updateFloat();
      }
    }, 3000);

    console.log('[Study Tracker] Tracking started:', subject);
  }

  function stopTracking() {
    tracking = false;
    if (saveTimer) { clearInterval(saveTimer); saveTimer = null; }
    saveProgress(); // final save
  }

  // Save on page leave
  window.addEventListener('beforeunload', () => {
    if (tracking) {
      const ep = getEpisode();
      const pct = getProgress();
      const total = getTotalEpisodes();
      // Use sendBeacon for reliable delivery on page close
      const blob = new Blob([JSON.stringify({
        subject, episode: ep, progress_pct: pct,
        total: total || undefined, url: location.href
      })], { type: 'application/json' });
      navigator.sendBeacon(API, blob);
    }
  });

  // --- Init ---
  function init() {
    const bv = getBV();
    if (!bv) return;

    // Check if previously decided
    const trackPref = localStorage.getItem('st_track_' + bv);
    const savedSubject = localStorage.getItem('st_course_' + bv);

    if (trackPref === 'false') return; // User said skip

    if (trackPref === 'true' && savedSubject) {
      // Previously tracked, auto-resume
      subject = savedSubject;
      startTracking();
      return;
    }

    // First time seeing this video - detect and ask
    const detected = detectSubject();
    if (detected) {
      // Auto-detected as course, show prompt
      setTimeout(() => createPromptCard(detected), 2000);
    }
    // If not detected, don't bother user (they can manually use Memos #看课)
  }

  // Wait for page to load
  if (document.readyState === 'complete') {
    setTimeout(init, 1500);
  } else {
    window.addEventListener('load', () => setTimeout(init, 1500));
  }
})();
