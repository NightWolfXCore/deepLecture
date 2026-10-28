(() => {
  'use strict';
  const CFG = window.APP_CONFIG;
  const E = (status, code, message, details) => new ApiError(message, { status, code, details: details || {} });
  const STAGES = ['queued', 'extracting', 'transcribing', 'indexing', 'summarizing', 'ready'];
  const DURATION = 10000;

  const sleep = (ms, signal) => new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    if (signal) signal.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
  });
  const latency = (signal) => sleep(250 + Math.random() * 300, signal);

  const SEGMENTS = [
    'Сегодня разберём теорему Пифагора.',
    'Квадрат гипотенузы равен сумме квадратов катетов.',
    'Гипотенуза — это сторона, лежащая напротив прямого угла.',
    'Рассмотрим пример: катеты три и четыре, гипотенуза равна пяти.',
    'Подведём итог и решим домашнюю задачу.'
  ].map((text, i) => ({ id: 'seg-' + i, position: i, start_ms: i * 2000, end_ms: (i + 1) * 2000, text }));

  const CHAPTERS = [
    { id: 'ch-0', position: 0, title: 'Введение', level: 1, start_segment_id: 'seg-0', start_ms: 0 },
    { id: 'ch-1', position: 1, title: 'Формулировка теоремы', level: 1, start_segment_id: 'seg-1', start_ms: 2000 },
    { id: 'ch-2', position: 2, title: 'Что такое гипотенуза', level: 2, start_segment_id: 'seg-2', start_ms: 4000 },
    { id: 'ch-3', position: 3, title: 'Пример и итоги', level: 1, start_segment_id: 'seg-3', start_ms: 6000 }
  ];
  const TERMS = [
    { id: 't-0', term: 'Теорема Пифагора', definition: 'Утверждение о связи сторон прямоугольного треугольника.', mentions: [{ start_ms: 0 }, { start_ms: 2000 }] },
    { id: 't-1', term: 'Гипотенуза', definition: 'Сторона прямоугольного треугольника, лежащая напротив прямого угла.', mentions: [{ start_ms: 2000 }, { start_ms: 4000 }, { start_ms: 6000 }] },
    { id: 't-2', term: 'Катеты', definition: 'Две стороны прямоугольного треугольника, образующие прямой угол.', mentions: [{ start_ms: 2000 }, { start_ms: 6000 }] }
  ];
  const SUMMARY = 'В лекции разобрана теорема Пифагора: квадрат гипотенузы равен сумме квадратов катетов.\n\nПоказан числовой пример с катетами 3 и 4 и сформулировано домашнее задание.';

  const now = Date.now();
  const db = [
    { id: 'mock-ready', title: 'Теорема Пифагора', mode: 'ready', created: now - 86400000 * 2 },
    { id: 'mock-summarizing', title: 'Введение в алгоритмы', mode: 'progress', startedAt: now - 17000, created: now - 3600000 },
    { id: 'mock-failed', title: 'Основы термодинамики', mode: 'failed', created: now - 86400000 },
    { id: 'mock-queued', title: 'История Древнего Рима', mode: 'progress', startedAt: now, created: now - 60000 }
  ];

  function stageOf(l) {
    if (l.mode === 'ready') return 'ready';
    if (l.mode === 'failed') return 'failed';
    return STAGES[Math.min(5, Math.floor((Date.now() - l.startedAt) / 4000))];
  }
  function availabilityOf(l) {
    const st = stageOf(l);
    if (st === 'failed') return { transcript: true, search: true, summary: false, terms: false };
    const i = STAGES.indexOf(st);
    return { transcript: i >= 3, search: i >= 4, summary: i >= 5, terms: i >= 5 };
  }
  function find(id) {
    const l = db.find((x) => x.id === id);
    if (!l) throw E(404, 'LECTURE_NOT_FOUND', 'Лекция не найдена');
    return l;
  }
  const iso = (ms) => new Date(ms).toISOString().replace(/\.\d+Z$/, 'Z');

  function card(l) {
    const st = stageOf(l);
    const failed = st === 'failed';
    return {
      id: l.id, title: l.title, status: st,
      duration_ms: STAGES.indexOf(st) >= 2 || failed ? DURATION : null,
      media_url: CFG.MOCK_VIDEO_URL,
      availability: availabilityOf(l),
      processing: {
        job_id: 'job-' + l.id,
        failed_stage: failed ? 'summarizing' : null,
        error_code: failed ? 'SUMMARY_LLM_ERROR' : null,
        error_message: failed ? 'Не удалось получить ответ от модели конспектов' : null,
        can_retry: failed
      },
      created_at: iso(l.created)
    };
  }

  function createLecture(title) {
    const l = { id: 'mock-' + Date.now(), title, mode: 'progress', startedAt: Date.now(), created: Date.now() };
    db.unshift(l);
    return { data: { id: l.id, title, status: 'queued', job_id: 'job-' + l.id } };
  }

  const words = (s) => (s.toLowerCase().match(/[а-яa-z0-9ё]{4,}/g) || []).map((w) => w.slice(0, 5));

  window.MockApi = {
    async uploadLecture(title, file, onProgress) {
      for (let p = 0; p <= 1; p += 0.25) { onProgress && onProgress(p); await sleep(200); }
      if (!/\.(mp4|webm)$/i.test(file.name)) throw E(415, 'UNSUPPORTED_MEDIA_TYPE', 'Поддерживаются только MP4 и WebM');
      if (file.size > CFG.MAX_FILE_BYTES) throw E(413, 'FILE_TOO_LARGE', 'Файл слишком большой (максимум 500 МБ)');
      return createLecture(title);
    },
    async importLecture(title, url) {
      await latency();
      let host = '';
      try { host = new URL(url).hostname; } catch {  }
      if (!/^(?:[\w-]+\.)?(?:youtube\.com|youtu\.be|vimeo\.com)$/i.test(host)) {
        throw E(422, 'VALIDATION_ERROR', 'Проверьте заполнение полей', { fields: { url: 'Поддерживаются только ссылки YouTube и Vimeo' } });
      }
      return createLecture(title);
    },
    async listLectures(page, perPage) {
      await latency();
      const sorted = [...db].sort((a, b) => b.created - a.created);
      const slice = sorted.slice((page - 1) * perPage, page * perPage).map((l) => {
        const c = card(l);
        return { id: c.id, title: c.title, status: c.status, duration_ms: c.duration_ms, created_at: c.created_at };
      });
      return { data: slice, meta: { page, per_page: perPage, total: db.length } };
    },
    async getLecture(id) { await latency(); return { data: card(find(id)) }; },
    async getTranscript(id) {
      await latency();
      if (!availabilityOf(find(id)).transcript) throw E(409, 'TRANSCRIPT_NOT_READY', 'Транскрипт ещё не готов');
      return { data: { lecture_id: id, segments: SEGMENTS } };
    },
    async getSummary(id) {
      await latency();
      const l = find(id);
      if (stageOf(l) === 'failed') throw E(409, 'SUMMARY_FAILED', 'Не удалось создать конспект');
      if (!availabilityOf(l).summary) throw E(409, 'SUMMARY_NOT_READY', 'Конспект ещё готовится');
      return { data: { lecture_id: id, text: SUMMARY, chapters: CHAPTERS } };
    },
    async search({ lecture_id, query, limit }, signal) {
      await sleep(500 + Math.random() * 600, signal);
      let pool;
      if (lecture_id) {
        const l = find(lecture_id);
        if (!availabilityOf(l).search) throw E(409, 'LECTURE_NOT_READY', 'Индекс лекции ещё не готов');
        pool = [l];
      } else {
        pool = db.filter((l) => availabilityOf(l).search);
      }
      if (/ошибк/i.test(query)) throw E(503, 'SEARCH_UNAVAILABLE', 'Сервис поиска временно недоступен');
      const q = words(query);
      const items = [];
      for (const l of pool) {
        for (const s of SEGMENTS) {
          const hit = words(s.text).filter((w) => q.includes(w)).length;
          if (!hit) continue;
          items.push({
            chunk_id: `chunk-${l.id}-${s.id}`, lecture_id: l.id, lecture_title: l.title, text: s.text,
            start_ms: s.start_ms, end_ms: s.end_ms,
            score: Math.min(0.95, 0.55 + hit * 0.15),
            playback_url: `/?lecture=${l.id}&t=${s.start_ms / 1000}`
          });
        }
      }
      items.sort((a, b) => b.score - a.score || a.start_ms - b.start_ms);
      const data = items.slice(0, limit || 5);
      return { data, meta: { query, count: data.length } };
    },
    async chat(id, { question }, signal) {
      await sleep(900 + Math.random() * 700, signal);
      const l = find(id);
      if (!availabilityOf(l).search) throw E(409, 'LECTURE_NOT_READY', 'Лекция ещё обрабатывается');
      if (/ошибк/i.test(question)) throw E(503, 'CHAT_UNAVAILABLE', 'Помощник временно недоступен');
      const q = words(question);
      const hits = SEGMENTS
        .map((s) => ({ s, hit: words(s.text).filter((w) => q.includes(w)).length }))
        .filter((x) => x.hit > 0)
        .sort((a, b) => b.hit - a.hit || a.s.start_ms - b.s.start_ms)
        .slice(0, 2);
      if (!hits.length) return { data: { answer: 'В этой лекции я не нашёл ответа на этот вопрос.', sources: [] } };
      return {
        data: {
          answer: hits.map((x) => x.s.text).join(' '),
          sources: hits.map((x) => ({ chunk_id: 'chunk-' + x.s.id, start_ms: x.s.start_ms, end_ms: x.s.end_ms, text: x.s.text }))
        }
      };
    },
    async getTerms(id) {
      await latency();
      const l = find(id);
      if (!availabilityOf(l).terms) throw E(409, 'TERMS_NOT_READY', 'Термины ещё готовятся');
      return { data: { lecture_id: id, terms: TERMS } };
    },
    async retry(id) {
      await latency();
      const l = find(id);
      if (stageOf(l) !== 'failed') throw E(409, 'RETRY_NOT_ALLOWED', 'Повтор возможен только для лекции с ошибкой');
      l.mode = 'progress';
      l.startedAt = Date.now();
      return { data: { id, status: 'queued', job_id: 'job-' + id + '-2' } };
    }
  };
})();