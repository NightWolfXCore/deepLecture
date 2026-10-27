(() => {
  'use strict';
  const CFG = window.APP_CONFIG;
  const API = CFG.USE_MOCK ? window.MockApi : window.RealApi;
  const $app = document.getElementById('app');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let cleanup = null;

  const STAGES = ['queued', 'extracting', 'transcribing', 'indexing', 'summarizing', 'ready'];
  const STATUS = {
    queued: { label: 'В очереди', cls: 'text-bg-secondary' },
    extracting: { label: 'Извлечение аудио', cls: 'text-bg-info' },
    transcribing: { label: 'Распознавание речи', cls: 'text-bg-info' },
    indexing: { label: 'Построение индекса', cls: 'text-bg-info' },
    summarizing: { label: 'Создание конспекта', cls: 'text-bg-info' },
    ready: { label: 'Готово', cls: 'text-bg-success' },
    failed: { label: 'Ошибка', cls: 'text-bg-danger' }
  };
  const isFinal = (s) => s === 'ready' || s === 'failed';

  /* ---------- утилиты ---------- */
  // Создание DOM. Тексты всегда идут через textContent / текстовые узлы — никакого innerHTML.
  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const spinner = () => h('div', { class: 'text-center py-4' },
    h('div', { class: 'spinner-border text-primary', role: 'status' }, h('span', { class: 'visually-hidden' }, 'Загрузка…')));
  const note = (text) => h('p', { class: 'text-body-secondary mb-0' }, text);
  const alertBox = (kind, ...content) => h('div', { class: `alert alert-${kind} mb-0`, role: 'alert' }, content);
  const errMsg = (e) => (e && e.message) || 'Что-то пошло не так';

  function formatTime(ms) {
    const total = Math.floor(ms / 1000);
    const hh = Math.floor(total / 3600), mm = Math.floor((total % 3600) / 60), ss = total % 60;
    const p = (n) => String(n).padStart(2, '0');
    return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${mm}:${p(ss)}`;
  }
  const formatDate = (iso) => new Date(iso).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' });
  const lectureHref = (id) => '?lecture=' + encodeURIComponent(id);

  function toast(msg, kind = 'danger') {
    const box = document.getElementById('toasts');
    const t = h('div', { class: `toast align-items-center text-bg-${kind} border-0`, role: 'alert' },
      h('div', { class: 'd-flex' },
        h('div', { class: 'toast-body' }, msg),
        h('button', { type: 'button', class: 'btn-close btn-close-white me-2 m-auto', 'data-bs-dismiss': 'toast', 'aria-label': 'Закрыть' })));
    box.append(t);
    t.addEventListener('hidden.bs.toast', () => t.remove());
    new bootstrap.Toast(t, { delay: 5000 }).show();
  }

  function findIdx(arr, ms) { // последний элемент со start_ms <= ms
    let lo = 0, hi = arr.length - 1, res = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid].start_ms <= ms) { res = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return res;
  }
  function scrollInto(container, el) {
    if (!container || !el || !container.offsetParent) return;
    container.scrollTo({
      top: el.offsetTop - (container.clientHeight - el.offsetHeight) / 2,
      behavior: reduceMotion ? 'auto' : 'smooth'
    });
  }

  /* ---------- роутинг ---------- */
  function navigate(url) { history.pushState({}, '', url); render(); window.scrollTo(0, 0); }
  function render() {
    if (cleanup) { cleanup(); cleanup = null; }
    const params = new URLSearchParams(location.search);
    const id = params.get('lecture');
    const t = parseFloat(params.get('t'));
    if (id) lectureView(id, Number.isFinite(t) && t >= 0 ? t : null);
    else catalogView();
  }
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-nav]');
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(a.getAttribute('href'));
  });
  window.addEventListener('popstate', render);

  /* =========================================================
     Экран 1: загрузка и каталог
     ========================================================= */
  function catalogView() {
    document.title = 'Лекторий — поиск по видеолекциям';
    $app.replaceChildren();
    const S = { page: 1, alive: true, timer: null };
    const PER = 10;

    const titleInput = h('input', { class: 'form-control', id: 'f-title', maxlength: '200', placeholder: 'Например: Теорема Пифагора' });
    const titleErr = h('div', { class: 'invalid-feedback' });
    const fileInput = h('input', { class: 'form-control', id: 'f-file', type: 'file', accept: '.mp4,.webm,video/mp4,video/webm' });
    const fileErr = h('div', { class: 'invalid-feedback' });
    const bar = h('div', { class: 'progress-bar progress-bar-striped progress-bar-animated', role: 'progressbar', style: 'width:0%' });
    const progress = h('div', { class: 'progress mt-3 d-none' }, bar);
    const formAlert = h('div', { class: 'mt-3' });
    const submitBtn = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Загрузить лекцию');

    const form = h('form', { class: 'panel p-3 p-md-4 mb-4', novalidate: true, onSubmit: onSubmit },
      h('h2', { class: 'h5 mb-3' }, 'Новая лекция'),
      h('div', { class: 'row g-3' },
        h('div', { class: 'col-md-6' }, h('label', { class: 'form-label', for: 'f-title' }, 'Название'), titleInput, titleErr),
        h('div', { class: 'col-md-6' }, h('label', { class: 'form-label', for: 'f-file' }, 'Видео (MP4 или WebM, до 500 МБ)'), fileInput, fileErr)),
      progress, formAlert,
      h('div', { class: 'mt-3' }, submitBtn));

    const listBox = h('div');
    $app.append(
      h('h1', { class: 'h3 mb-1' }, 'Видеолекции'),
      h('p', { class: 'text-body-secondary mb-4' }, 'Загрузите запись — через несколько минут по ней можно будет искать по смыслу.'),
      form,
      h('h2', { class: 'h5 mb-3' }, 'Все лекции'),
      listBox);

    function setFieldErr(input, errEl, msg) {
      input.classList.toggle('is-invalid', !!msg);
      errEl.textContent = msg || '';
    }
    function showFieldErrors(f) {
      setFieldErr(titleInput, titleErr, f.title);
      setFieldErr(fileInput, fileErr, f.file);
    }

    async function onSubmit(e) {
      e.preventDefault();
      formAlert.replaceChildren();
      const title = titleInput.value.trim();
      const file = fileInput.files[0];
      const f = {};
      if (!title) f.title = 'Введите название';
      else if (title.length > 200) f.title = 'Не больше 200 символов';
      if (!file) f.file = 'Выберите видеофайл';
      else if (!/\.(mp4|webm)$/i.test(file.name) && !['video/mp4', 'video/webm'].includes(file.type)) f.file = 'Нужен файл MP4 или WebM';
      else if (file.size > CFG.MAX_FILE_BYTES) f.file = 'Файл больше 500 МБ';
      showFieldErrors(f);
      if (Object.keys(f).length) return;

      submitBtn.disabled = true;
      progress.classList.remove('d-none');
      try {
        const res = await API.uploadLecture(title, file, (p) => { bar.style.width = Math.round(p * 100) + '%'; });
        if (!S.alive) return;
        navigate(lectureHref(res.data.id));
      } catch (err) {
        if (!S.alive) return;
        progress.classList.add('d-none');
        bar.style.width = '0%';
        submitBtn.disabled = false;
        const fields = err.details && err.details.fields;
        if (fields) showFieldErrors(fields);
        formAlert.replaceChildren(alertBox('danger', errMsg(err)));
      }
    }

    function renderList(res) {
      const { data, meta } = res;
      if (!data.length) {
        listBox.replaceChildren(note('Лекций пока нет. Загрузите первую запись выше.'));
        return;
      }
      const list = h('div', { class: 'list-group' }, data.map((l) => {
        const st = STATUS[l.status] || STATUS.queued;
        return h('a', { class: 'list-group-item list-group-item-action d-flex flex-wrap align-items-center gap-2 gap-md-3', href: lectureHref(l.id), 'data-nav': true },
          h('span', { class: 'fw-medium flex-grow-1' }, l.title),
          h('span', { class: 'small text-body-secondary tnum' }, l.duration_ms ? formatTime(l.duration_ms) : ''),
          h('span', { class: 'small text-body-secondary' }, formatDate(l.created_at)),
          h('span', { class: `badge ${st.cls}` }, st.label));
      }));
      const pages = Math.max(1, Math.ceil(meta.total / meta.per_page));
      const pager = pages > 1 ? h('div', { class: 'd-flex align-items-center justify-content-between mt-3' },
        h('button', { class: 'btn btn-outline-secondary btn-sm', type: 'button', disabled: S.page <= 1, onClick: () => { S.page--; load(false); } }, 'Назад'),
        h('span', { class: 'small text-body-secondary' }, `Страница ${S.page} из ${pages}`),
        h('button', { class: 'btn btn-outline-secondary btn-sm', type: 'button', disabled: S.page >= pages, onClick: () => { S.page++; load(false); } }, 'Вперёд')) : null;
      listBox.replaceChildren(list, pager);
    }

    function schedule(again) {
      clearTimeout(S.timer);
      if (again && S.alive) S.timer = setTimeout(() => load(true), 5000);
    }
    async function load(silent) {
      if (!silent) listBox.replaceChildren(spinner());
      try {
        const res = await API.listLectures(S.page, PER);
        if (!S.alive) return;
        renderList(res);
        schedule(res.data.some((l) => !isFinal(l.status)));
      } catch (err) {
        if (!S.alive) return;
        if (!silent) {
          listBox.replaceChildren(alertBox('warning', `${errMsg(err)}. `,
            h('button', { class: 'btn btn-sm btn-outline-secondary ms-2', type: 'button', onClick: () => load(false) }, 'Повторить')));
        }
        schedule(true);
      }
    }
    load(false);
    cleanup = () => { S.alive = false; clearTimeout(S.timer); };
  }

  /* =========================================================
     Экран 2: лекция
     ========================================================= */
  function lectureView(id, startT) {
    $app.replaceChildren();
    const S = {
      alive: true, lecture: null, pollTimer: null, delay: CFG.POLL_MS,
      segments: [], segEls: [], segList: null, activeSeg: -1,
      chapters: [], chapEls: [], activeChap: -1,
      tr: null, sm: null,            // null | 'loading' | 'done' | 'error' | 'failed'
      searchSeq: 0, searchCtl: null, mediaSet: false
    };

    /* --- элементы --- */
    const titleEl = h('h1', { class: 'h3 mb-0' }, 'Загрузка…');
    const badge = h('span', { class: 'badge text-bg-secondary' });
    const metaEl = h('div', { class: 'text-body-secondary small tnum mt-1' });
    const netBox = h('div', { class: 'mb-3' });
    const bannerBox = h('div', { class: 'mb-3' });

    const video = h('video', { class: 'player', controls: true, preload: 'metadata', playsinline: true });
    const videoMsg = h('div', { class: 'mt-2' });
    const SUB_HINT = 'Субтитры появятся при воспроизведении, когда будет готов транскрипт.';
    const subtitle = h('p', { class: 'subtitle', 'aria-live': 'off' }, SUB_HINT);

    const summaryBox = h('div');
    const chaptersBox = h('div');

    const qInput = h('input', { class: 'form-control', id: 'q', type: 'search', maxlength: '1000', placeholder: 'Например: как связаны стороны треугольника', disabled: true });
    const qBtn = h('button', { class: 'btn btn-primary', type: 'submit', disabled: true }, 'Найти');
    const searchHint = h('div', { class: 'form-text' });
    const resultsBox = h('div', { class: 'mt-3', 'aria-live': 'polite' });
    const transcriptBox = h('div');

    const searchForm = h('form', { role: 'search', onSubmit: doSearch },
      h('label', { class: 'form-label', for: 'q' }, 'Что найти в этой лекции'),
      h('div', { class: 'input-group' }, qInput, qBtn),
      searchHint);

    const tabs = h('ul', { class: 'nav nav-tabs px-3 pt-2', role: 'tablist' },
      h('li', { class: 'nav-item', role: 'presentation' },
        h('button', { class: 'nav-link active', id: 'tab-search', type: 'button', role: 'tab', 'data-bs-toggle': 'tab', 'data-bs-target': '#pane-search', 'aria-controls': 'pane-search', 'aria-selected': 'true' }, 'Поиск')),
      h('li', { class: 'nav-item', role: 'presentation' },
        h('button', { class: 'nav-link', id: 'tab-transcript', type: 'button', role: 'tab', 'data-bs-toggle': 'tab', 'data-bs-target': '#pane-transcript', 'aria-controls': 'pane-transcript', 'aria-selected': 'false' }, 'Транскрипт')));
    const panes = h('div', { class: 'tab-content p-3' },
      h('div', { class: 'tab-pane fade show active', id: 'pane-search', role: 'tabpanel', 'aria-labelledby': 'tab-search' }, searchForm, resultsBox),
      h('div', { class: 'tab-pane fade', id: 'pane-transcript', role: 'tabpanel', 'aria-labelledby': 'tab-transcript' }, transcriptBox));
    tabs.addEventListener('shown.bs.tab', () => { if (S.activeSeg >= 0) scrollInto(S.segList, S.segEls[S.activeSeg]); });

    $app.append(
      h('a', { href: './', 'data-nav': true, class: 'small d-inline-block mb-3' }, '← Все лекции'),
      h('div', { class: 'mb-3' }, h('div', { class: 'd-flex flex-wrap align-items-center gap-2' }, titleEl, badge), metaEl),
      netBox, bannerBox,
      h('div', { class: 'row g-4' },
        h('div', { class: 'col-lg-7' },
          video, videoMsg, subtitle,
          h('section', { class: 'panel p-3 p-md-4 mt-4' },
            h('h2', { class: 'h5 mb-3' }, 'Конспект'), summaryBox,
            h('h2', { class: 'h5 mt-4 mb-2' }, 'Оглавление'), chaptersBox)),
        h('div', { class: 'col-lg-5' },
          h('div', { class: 'panel side-sticky' }, tabs, panes))));

    /* --- видео --- */
    function seek(ms) {
      const go = () => { video.currentTime = ms / 1000; const p = video.play(); if (p && p.catch) p.catch(() => {}); };
      if (video.readyState >= 1) go(); else video.addEventListener('loadedmetadata', go, { once: true });
      if (window.matchMedia('(max-width: 991.98px)').matches) video.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
    }
    if (startT != null) {
      video.addEventListener('loadedmetadata', () => {
        video.currentTime = Math.min(startT, video.duration || startT);
        const p = video.play(); if (p && p.catch) p.catch(() => {}); // браузер может запретить автозапуск
      }, { once: true });
    }
    video.addEventListener('error', () => { videoMsg.replaceChildren(alertBox('warning', 'Не удалось загрузить видео. Обновите страницу или попробуйте позже.')); });
    ['timeupdate', 'seeked', 'loadedmetadata'].forEach((ev) => video.addEventListener(ev, updateActive));

    function setSubtitle(idx) {
      if (idx >= 0) { subtitle.textContent = S.segments[idx].text; subtitle.classList.add('is-live'); }
      else { subtitle.textContent = S.tr === 'done' ? '' : SUB_HINT; subtitle.classList.remove('is-live'); }
    }
    function updateActive() {
      const ms = video.currentTime * 1000;
      if (S.segments.length) {
        const i = findIdx(S.segments, ms);
        const idx = i >= 0 && ms < S.segments[i].end_ms ? i : -1; // в паузах подсветки нет
        if (idx !== S.activeSeg) {
          const prev = S.segEls[S.activeSeg];
          if (prev) { prev.classList.remove('active'); prev.removeAttribute('aria-current'); }
          S.activeSeg = idx;
          if (idx >= 0) {
            const el = S.segEls[idx];
            el.classList.add('active'); el.setAttribute('aria-current', 'true');
            scrollInto(S.segList, el);
          }
          setSubtitle(idx);
        }
      }
      if (S.chapters.length) {
        const idx = findIdx(S.chapters, ms);
        if (idx !== S.activeChap) {
          S.chapEls[S.activeChap]?.classList.remove('active');
          S.activeChap = idx;
          S.chapEls[idx]?.classList.add('active');
        }
      }
    }

    /* --- карточка лекции и опрос --- */
    function applyLecture(d) {
      S.lecture = d;
      document.title = d.title + ' — Лекторий';
      titleEl.textContent = d.title;
      const st = STATUS[d.status] || STATUS.queued;
      badge.className = 'badge ' + st.cls;
      badge.textContent = st.label;
      metaEl.replaceChildren(
        d.duration_ms ? h('span', { class: 'me-3' }, 'Длительность ' + formatTime(d.duration_ms)) : null,
        h('span', null, 'Загружено ' + formatDate(d.created_at)));

      if (d.media_url && !S.mediaSet) { video.src = d.media_url; S.mediaSet = true; }
      renderBanner(d);

      const av = d.availability || {};
      setSearchEnabled(!!av.search, d);
      if (av.transcript && S.tr === null) loadTranscript();
      if (av.summary && S.sm === null) loadSummary();
      placeholders(d, av);
    }

    function renderBanner(d) {
      bannerBox.replaceChildren();
      if (d.status === 'ready') return;
      if (d.status === 'failed') {
        const p = d.processing || {};
        const stage = STATUS[p.failed_stage] && STATUS[p.failed_stage].label;
        const box = alertBox('danger',
          h('div', { class: 'fw-medium' }, stage ? `Обработка остановилась на этапе «${stage}»` : 'Обработка остановилась'),
          p.error_message ? h('div', { class: 'small mt-1' }, p.error_message) : null,
          h('div', { class: 'small mt-1' }, 'То, что успело обработаться, остаётся доступным.'));
        if (p.can_retry) {
          const btn = h('button', { class: 'btn btn-sm btn-outline-danger mt-2', type: 'button', onClick: () => retry(btn) }, 'Повторить обработку');
          box.append(btn);
        }
        bannerBox.append(box);
        return;
      }
      const idx = Math.max(0, STAGES.indexOf(d.status));
      const pct = Math.round(((idx + 1) / STAGES.length) * 100);
      bannerBox.append(h('div', { class: 'panel p-3' },
        h('div', { class: 'd-flex justify-content-between mb-2' },
          h('span', { class: 'fw-medium' }, (STATUS[d.status] || STATUS.queued).label + '…'),
          h('span', { class: 'small text-body-secondary tnum' }, pct + '%')),
        h('div', { class: 'progress', role: 'progressbar', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 },
          h('div', { class: 'progress-bar progress-bar-striped progress-bar-animated', style: `width:${pct}%` })),
        h('div', { class: 'small text-body-secondary mt-2' }, 'Транскрипт, поиск и конспект появятся по мере готовности. Страницу можно не обновлять.')));
    }

    function setSearchEnabled(on, d) {
      qInput.disabled = !on; qBtn.disabled = !on;
      searchHint.textContent = on ? 'Ищем по смыслу, а не только по совпадению слов.'
        : d.status === 'failed' ? 'Поиск недоступен: индекс не был построен.'
        : 'Поиск включится, когда лекция будет проиндексирована.';
    }

    function placeholders(d, av) {
      const failed = d.status === 'failed';
      const fs = d.processing && d.processing.failed_stage;
      if (!av.transcript && S.tr === null) {
        transcriptBox.replaceChildren(note(failed ? 'Транскрипт недоступен: обработка остановилась.' : 'Транскрипт появится после распознавания речи.'));
      }
      if (!av.summary && S.sm === null) {
        summaryBox.replaceChildren(note(failed
          ? (fs === 'summarizing' ? 'Конспект создать не удалось. Поиск и транскрипт при этом работают.' : 'Конспект недоступен: обработка остановилась.')
          : 'Конспект появится в конце обработки.'));
        chaptersBox.replaceChildren(note('Оглавление появится вместе с конспектом.'));
      }
    }

    function schedulePoll(ms) { clearTimeout(S.pollTimer); S.pollTimer = setTimeout(poll, ms); }
    async function poll() {
      if (!S.alive) return;
      let reqFailed = false;
      try {
        const res = await API.getLecture(id);
        if (!S.alive) return;
        S.delay = CFG.POLL_MS;
        netBox.replaceChildren();
        applyLecture(res.data);
      } catch (err) {
        if (!S.alive) return;
        if (err.status === 404) return showNotFound();
        reqFailed = true;
        S.delay = Math.min(S.delay * 2, 15000); // не долбим сервер при сбое
        netBox.replaceChildren(alertBox('warning', `${errMsg(err)}. Повторяем попытку…`));
      }
      if (reqFailed || !(S.lecture && isFinal(S.lecture.status))) schedulePoll(S.delay);
    }
    function showNotFound() {
      S.alive = false;
      $app.replaceChildren(alertBox('warning', 'Лекция не найдена. '), h('a', { href: './', 'data-nav': true, class: 'd-inline-block mt-3' }, '← Все лекции'));
    }

    async function retry(btn) {
      btn.disabled = true;
      try {
        await API.retry(id);
        resetResults();
        schedulePoll(0);
      } catch (err) {
        btn.disabled = false;
        toast(err.code === 'PROCESSING_ALREADY_ACTIVE' ? 'Обработка уже запущена' : errMsg(err));
        if (err.code === 'PROCESSING_ALREADY_ACTIVE' || err.code === 'RETRY_NOT_ALLOWED') schedulePoll(0);
      }
    }
    function resetResults() {
      if (S.searchCtl) S.searchCtl.abort();
      S.searchSeq++;
      S.tr = null; S.sm = null;
      S.segments = []; S.segEls = []; S.segList = null; S.activeSeg = -1;
      S.chapters = []; S.chapEls = []; S.activeChap = -1;
      transcriptBox.replaceChildren(); summaryBox.replaceChildren(); chaptersBox.replaceChildren(); resultsBox.replaceChildren();
      setSubtitle(-1);
    }

    /* --- транскрипт --- */
    function loadError(err, fn) {
      return alertBox('warning', `${errMsg(err)}. `,
        h('button', { class: 'btn btn-sm btn-outline-secondary ms-2', type: 'button', onClick: fn }, 'Повторить'));
    }
    async function loadTranscript() {
      S.tr = 'loading';
      transcriptBox.replaceChildren(spinner());
      try {
        const res = await API.getTranscript(id);
        if (!S.alive) return;
        S.segments = res.data.segments;
        renderTranscript();
        S.tr = 'done';
        updateActive();
      } catch (err) {
        if (!S.alive) return;
        if (err.code === 'TRANSCRIPT_NOT_READY') { S.tr = null; return; } // опрос повторит
        S.tr = 'error';
        transcriptBox.replaceChildren(loadError(err, loadTranscript));
      }
    }
    function renderTranscript() {
      S.segEls = S.segments.map((s) => h('button', { class: 'seg', type: 'button', onClick: () => seek(s.start_ms) },
        h('span', { class: 't tnum' }, formatTime(s.start_ms)),
        h('span', null, s.text)));
      S.segList = h('div', { class: 'scroll-list' }, S.segEls);
      S.activeSeg = -1;
      transcriptBox.replaceChildren(S.segList);
    }

    /* --- конспект и оглавление --- */
    async function loadSummary() {
      S.sm = 'loading';
      summaryBox.replaceChildren(spinner());
      try {
        const res = await API.getSummary(id);
        if (!S.alive) return;
        summaryBox.replaceChildren(h('p', { class: 'summary-text mb-0' }, res.data.text));
        S.chapters = res.data.chapters || [];
        S.chapEls = S.chapters.map((c) => h('button', { class: `chapter level-${c.level}`, type: 'button', onClick: () => seek(c.start_ms) },
          h('span', { class: 't tnum' }, formatTime(c.start_ms)),
          h('span', null, c.title)));
        chaptersBox.replaceChildren(S.chapEls.length ? h('div', null, S.chapEls) : note('Оглавление пусто.'));
        S.activeChap = -1;
        S.sm = 'done';
        updateActive();
      } catch (err) {
        if (!S.alive) return;
        if (err.code === 'SUMMARY_NOT_READY') { S.sm = null; return; }
        if (err.code === 'SUMMARY_FAILED') {
          S.sm = 'failed';
          summaryBox.replaceChildren(note('Конспект создать не удалось. Поиск и транскрипт при этом работают.'));
          chaptersBox.replaceChildren(note('Оглавление недоступно.'));
          return;
        }
        S.sm = 'error';
        summaryBox.replaceChildren(loadError(err, loadSummary));
      }
    }

    /* --- поиск --- */
    async function doSearch(e) {
      e.preventDefault();
      const q = qInput.value.trim();
      if (!q) { resultsBox.replaceChildren(note('Введите запрос.')); return; }
      if (S.searchCtl) S.searchCtl.abort();            // старый запрос не должен перебить новый
      const ctl = new AbortController();
      S.searchCtl = ctl;
      const seq = ++S.searchSeq;
      resultsBox.replaceChildren(spinner());
      try {
        const res = await API.search({ lecture_id: id, query: q, limit: 8 }, ctl.signal);
        if (!S.alive || seq !== S.searchSeq) return;
        renderResults(res.data);
      } catch (err) {
        if (err.name === 'AbortError' || !S.alive || seq !== S.searchSeq) return;
        const text = err.code === 'LECTURE_NOT_READY' ? 'Индекс лекции ещё не готов или обновляется. Попробуйте чуть позже.'
          : err.code === 'SEARCH_UNAVAILABLE' ? 'Сервис поиска временно недоступен. Попробуйте чуть позже.'
          : err.status === 504 ? 'Поиск отвечает слишком долго. Попробуйте ещё раз.'
          : errMsg(err);
        resultsBox.replaceChildren(alertBox('warning', text));
      }
    }
    function renderResults(items) {
      if (!items.length) {
        resultsBox.replaceChildren(note('По этому запросу ничего не найдено. Попробуйте переформулировать.'));
        return;
      }
      const list = h('div', { class: 'list-group' }, items.map((it) =>
        h('a', { class: 'list-group-item list-group-item-action result', href: it.playback_url, onClick: (ev) => { ev.preventDefault(); seek(it.start_ms); } },
          h('div', { class: 'd-flex justify-content-between gap-2' },
            h('span', { class: 'fw-medium tnum' }, `${formatTime(it.start_ms)}–${formatTime(it.end_ms)}`),
            h('span', { class: 'small text-body-secondary tnum', title: 'Косинусное сходство, от −1 до 1' }, 'сходство ' + Number(it.score).toFixed(2))),
          h('div', { class: 'mt-1' }, it.text))));
      resultsBox.replaceChildren(
        h('div', { class: 'small text-body-secondary mb-2' }, 'Найдено фрагментов: ' + items.length),
        list,
        h('div', { class: 'form-text' }, 'Показаны самые близкие по смыслу фрагменты. Сверяйте их с видео.'));
    }

    /* --- старт --- */
    poll();
    cleanup = () => {
      S.alive = false;
      clearTimeout(S.pollTimer);
      if (S.searchCtl) S.searchCtl.abort();
      video.pause(); video.removeAttribute('src'); video.load(); // прекращаем подгрузку видео
    };
  }

  /* ---------- запуск ---------- */
  if (CFG.USE_MOCK) document.getElementById('mock-badge').classList.remove('d-none');
  render();
})();