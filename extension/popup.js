document.addEventListener('DOMContentLoaded', () => {
  const urlInput = document.getElementById('apiUrl');
  const status = document.getElementById('status');

  chrome.storage.sync.get(['apiUrl'], (data) => {
    urlInput.value = data.apiUrl || 'https://kb.xpy.me/api/smart-note';
  });

  document.getElementById('saveBtn').addEventListener('click', () => {
    const url = urlInput.value.trim();
    if (!url) {
      status.textContent = 'URL cannot be empty';
      status.style.color = '#f87171';
      return;
    }
    chrome.storage.sync.set({ apiUrl: url }, () => {
      status.textContent = 'Saved!';
      status.style.color = '#4ade80';
      setTimeout(() => (status.textContent = ''), 2000);
    });
  });
});
