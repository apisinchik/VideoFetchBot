(() => {
  const app = document.getElementById('chat-app');
  if (!app) {
    return;
  }

  const form = document.getElementById('js-analyze-form');
  const stream = document.getElementById('chat-stream');
  const errorBox = document.getElementById('chat-error');
  const csrfInput = form?.querySelector('input[name="csrfmiddlewaretoken"]');
  const urlInput = form?.querySelector('input[name="url"]');
  const submitButton = form?.querySelector('button[type="submit"]');

  const state = {
    currentAnalysis: null,
    pollers: new Map(),
  };

  const analyzeUrl = app.dataset.analyzeUrl;
  const startUrl = app.dataset.startUrl;
  const jobStatusTemplate = app.dataset.jobStatusUrlTemplate || '';

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.hidden = true;
    errorBox.textContent = '';
  }

  function setSubmitting(isSubmitting, label) {
    if (!submitButton) {
      return;
    }
    submitButton.disabled = isSubmitting;
    submitButton.textContent = label || (isSubmitting ? 'Обрабатываю...' : 'Скачать');
  }

  function activateChatLayout() {
    app.classList.add('page--chat');
    stream.classList.remove('chat-stream--empty');
    errorBox.classList.add('error-chat');
  }

  function syncViewportInset() {
    const viewport = window.visualViewport;
    const overlayBottom = viewport
      ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
      : 0;
    document.documentElement.style.setProperty('--chat-viewport-overlay-bottom', `${overlayBottom}px`);
  }

  function statusUrl(jobId) {
    return jobStatusTemplate.replace('999999', String(jobId));
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfInput?.value || '',
      },
      body: JSON.stringify(payload),
      credentials: 'same-origin',
    });

    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }

    if (!response.ok) {
      const err = new Error(data.message || 'Запрос завершился ошибкой.');
      err.payload = data;
      err.status = response.status;
      throw err;
    }
    return data;
  }

  function buildFacts(job) {
    const audioRow = job.audio_name
      ? `
        <div>
          <dt>Озвучка</dt>
          <dd>${escapeHtml(job.audio_name)}</dd>
        </div>`
      : '';

    return `
      <dl class="chat-card__facts">
        <div>
          <dt>Формат</dt>
          <dd>${escapeHtml(job.quality || 'Видео')}</dd>
        </div>
        ${audioRow}
        <div>
          <dt>Стадия</dt>
          <dd class="js-stage-label">${escapeHtml(job.stage_label || job.status_label || '')}</dd>
        </div>
      </dl>`;
  }

  function buildActions(job) {
    if (job.download_ready && job.download_url) {
      return `<a class="chat-action chat-action--primary" href="${escapeHtml(job.download_url)}">Скачать файл</a>`;
    }
    if (job.status === 'failed') {
      return '<button type="button" class="chat-action chat-action--muted" disabled>Не удалось скачать</button>';
    }
    return '<button type="button" class="chat-action chat-action--muted" disabled>Файл еще готовится</button>';
  }

  function renderJobEntry(job) {
    const errorBlock = job.error_message && job.status === 'failed'
      ? `<div class="chat-card__error">${escapeHtml(job.error_message)}</div>`
      : '';
    const progressHidden = !job.can_poll && Number(job.progress || 0) <= 0 ? ' is-hidden' : '';
    const durationBadge = job.duration_text && job.duration_text !== 'Неизвестно'
      ? `<span class="chat-card__duration">${escapeHtml(job.duration_text)}</span>`
      : '';
    const pollAttr = job.can_poll ? 'data-poll-job="1"' : '';

    return `
      <section class="chat-entry" data-job-id="${job.id}" ${pollAttr}>
        <article class="chat-message chat-message--assistant">
          <div class="chat-bubble chat-bubble--assistant js-job-card" data-job-id="${job.id}">
            <div class="chat-card__header">
              <span class="chat-chip chat-chip--${escapeHtml(job.status)}">${escapeHtml(job.status_label)}</span>
              ${durationBadge}
            </div>
            <h2 class="chat-card__title">${escapeHtml(job.title)}</h2>
            ${buildFacts(job)}
            ${errorBlock}
            <div class="job-progress${progressHidden}">
              <div class="job-progress__track">
                <div class="job-progress__bar js-progress-bar" style="width: ${Number(job.progress || 0)}%"></div>
              </div>
              <div class="job-progress__meta">
                <span class="js-progress-value">${Number(job.progress || 0)}%</span>
              </div>
            </div>
            <div class="chat-card__actions">
              ${buildActions(job)}
            </div>
          </div>
        </article>
      </section>`;
  }

  function renderAudioButtons(formatIndex, tracks) {
    return `
      <div class="chat-card__hint">Выберите озвучку.</div>
      <div class="chat-audio-grid">
        ${tracks.map((track) => `
          <button
            type="button"
            class="chat-audio-option"
            data-audio-index="${track.index}"
            data-format-index="${formatIndex}"
          >${escapeHtml(track.name)}</button>`).join('')}
        <button type="button" class="chat-option chat-option--ghost" data-reset-audio="1">Назад</button>
      </div>`;
  }

  function renderFormatButtons(formats) {
    return `
      <div class="chat-option-grid">
        ${formats.map((format) => `
          <button
            type="button"
            class="chat-option"
            data-format-index="${format.format_index}"
            data-kind="${escapeHtml(format.kind)}"
          >${escapeHtml(format.label)}</button>`).join('')}
      </div>`;
  }

  function renderAnalysisEntry(analysis) {
    const audioLine = analysis.total_audio_tracks > 0
      ? `
        <div>
          <dt>Озвучек</dt>
          <dd>${analysis.total_audio_tracks}</dd>
        </div>`
      : '';

    return `
      <section class="chat-entry chat-entry--analysis" data-analysis-entry="1">
        <article class="chat-message chat-message--assistant">
          <div class="chat-bubble chat-bubble--assistant js-analysis-card">
            <div class="chat-card__header">
              <span class="chat-chip chat-chip--running">Готово к выбору</span>
              <span class="chat-card__duration">${escapeHtml(analysis.duration_text)}</span>
            </div>
            <h2 class="chat-card__title">${escapeHtml(analysis.title)}</h2>
            <dl class="chat-card__facts">
              <div>
                <dt>Качеств</dt>
                <dd>${analysis.video_option_count}</dd>
              </div>
              ${audioLine}
              <div>
                <dt>Действие</dt>
                <dd>Выберите формат</dd>
              </div>
            </dl>
            <div class="chat-card__hint">Сначала выберите качество видео или отдельный аудио-режим.</div>
            <div class="js-analysis-actions">
              ${renderFormatButtons(analysis.formats)}
            </div>
          </div>
        </article>
      </section>`;
  }

  function renderPendingEntry(_url) {
    return `
      <section class="chat-entry chat-entry--analysis" data-analysis-entry="1">
        <article class="chat-message chat-message--assistant">
          <div class="chat-bubble chat-bubble--assistant">
            <div class="chat-card__header">
              <span class="chat-chip chat-chip--running">Анализ</span>
            </div>
            <h2 class="chat-card__title">Проверяю ссылку</h2>
            <div class="chat-card__hint">Извлекаю доступные качества, длительность и озвучки.</div>
          </div>
        </article>
      </section>`;
  }

  function removeCurrentAnalysisEntry() {
    stream.querySelector('[data-analysis-entry="1"]')?.remove();
  }

  function replaceOrAppendJob(job, sourceEntry) {
    const html = renderJobEntry(job);
    if (sourceEntry && sourceEntry.isConnected) {
      sourceEntry.outerHTML = html;
    } else {
      stream.insertAdjacentHTML('afterbegin', html);
    }
    const entry = stream.querySelector(`[data-job-id="${job.id}"]`);
    if (job.can_poll) {
      startJobPolling(job.id);
    } else {
      stopJobPolling(job.id);
    }
    return entry;
  }

  function updateJobEntry(job) {
    const current = stream.querySelector(`[data-job-id="${job.id}"]`);
    if (!current) {
      replaceOrAppendJob(job, null);
      return;
    }
    current.outerHTML = renderJobEntry(job);
    if (job.can_poll) {
      startJobPolling(job.id);
    } else {
      stopJobPolling(job.id);
    }
  }

  async function startDownload(formatIndex, audioIndex, entry) {
    clearError();
    try {
      const response = await postJson(startUrl, {
        format_index: formatIndex,
        audio_index: audioIndex,
      });
      if (response.job) {
        replaceOrAppendJob(response.job, entry);
      }
      state.currentAnalysis = null;
    } catch (error) {
      const payload = error.payload || {};
      if (payload.status === 'audio_required' && Array.isArray(payload.audio_tracks)) {
        const card = entry.querySelector('.js-analysis-actions');
        if (card) {
          card.innerHTML = renderAudioButtons(formatIndex, payload.audio_tracks);
        }
        return;
      }
      if (payload.job) {
        replaceOrAppendJob(payload.job, entry);
      }
      showError(payload.message || error.message || 'Не удалось запустить скачивание.');
    }
  }

  async function pollJob(jobId) {
    try {
      const response = await fetch(statusUrl(jobId), {
        credentials: 'same-origin',
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || 'Не удалось получить статус задачи.');
      }
      if (data.job) {
        updateJobEntry(data.job);
      }
    } catch (error) {
      console.error(error);
      stopJobPolling(jobId);
    }
  }

  function startJobPolling(jobId) {
    if (state.pollers.has(jobId)) {
      return;
    }
    const handle = window.setInterval(() => {
      pollJob(jobId);
    }, 2000);
    state.pollers.set(jobId, handle);
    pollJob(jobId);
  }

  function stopJobPolling(jobId) {
    const handle = state.pollers.get(jobId);
    if (handle) {
      window.clearInterval(handle);
      state.pollers.delete(jobId);
    }
  }

  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearError();

    const url = urlInput?.value.trim();
    if (!url) {
      showError('Введите ссылку.');
      return;
    }

    activateChatLayout();
    removeCurrentAnalysisEntry();
    stream.insertAdjacentHTML('afterbegin', renderPendingEntry(url));
    setSubmitting(true, 'Анализирую...');

    try {
      const response = await postJson(analyzeUrl, { url });
      state.currentAnalysis = response.analysis || null;
      removeCurrentAnalysisEntry();
      stream.insertAdjacentHTML('afterbegin', renderAnalysisEntry(response.analysis));
      if (urlInput) {
        urlInput.value = '';
      }
    } catch (error) {
      removeCurrentAnalysisEntry();
      showError(error.payload?.message || error.message || 'Не удалось проанализировать ссылку.');
    } finally {
      setSubmitting(false, 'Скачать');
    }
  });

  stream.addEventListener('click', (event) => {
    const audioButton = event.target.closest('.chat-audio-option[data-audio-index]');
    if (audioButton) {
      const entry = audioButton.closest('[data-analysis-entry="1"]');
      const formatIndex = Number(audioButton.dataset.formatIndex);
      const audioIndex = Number(audioButton.dataset.audioIndex);
      startDownload(formatIndex, audioIndex, entry);
      return;
    }

    const resetButton = event.target.closest('[data-reset-audio]');
    if (resetButton && state.currentAnalysis) {
      const entry = resetButton.closest('[data-analysis-entry="1"]');
      const actions = entry?.querySelector('.js-analysis-actions');
      if (actions) {
        actions.innerHTML = renderFormatButtons(state.currentAnalysis.formats || []);
      }
      return;
    }

    const formatButton = event.target.closest('.chat-option[data-format-index]');
    if (formatButton) {
      const entry = formatButton.closest('[data-analysis-entry="1"]');
      const formatIndex = Number(formatButton.dataset.formatIndex);
      const format = state.currentAnalysis?.formats?.find((item) => Number(item.format_index) === formatIndex);
      if (!format) {
        showError('Данные выбора устарели. Отправьте ссылку заново.');
        return;
      }
      if (format.requires_audio_choice && Array.isArray(format.audio_tracks) && format.audio_tracks.length > 0) {
        const actions = entry?.querySelector('.js-analysis-actions');
        if (actions) {
          actions.innerHTML = renderAudioButtons(formatIndex, format.audio_tracks);
        }
        return;
      }
      startDownload(formatIndex, null, entry);
      return;
    }
  });

  stream.querySelectorAll('[data-poll-job]').forEach((entry) => {
    const jobId = Number(entry.dataset.jobId);
    if (jobId > 0) {
      startJobPolling(jobId);
    }
  });

  syncViewportInset();
  window.addEventListener('resize', syncViewportInset);
  window.visualViewport?.addEventListener('resize', syncViewportInset);
  window.visualViewport?.addEventListener('scroll', syncViewportInset);
})();
