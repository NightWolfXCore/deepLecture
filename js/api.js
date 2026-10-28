(() => {
  'use strict';
  const CFG = window.APP_CONFIG;

  class ApiError extends Error {
    constructor(message, { status = 0, code = 'UNKNOWN', details = {}, requestId = null } = {}) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.details = details;
      this.requestId = requestId;
    }
  }

  const DEFAULT_MESSAGES = {
    400: 'Некорректный запрос',
    404: 'Не найдено',
    409: 'Действие сейчас недоступно',
    413: 'Файл слишком большой (максимум 500 МБ)',
    415: 'Неподдерживаемый формат файла',
    422: 'Проверьте заполнение полей',
    502: 'Сервер вернул некорректный ответ',
    503: 'Сервис временно недоступен',
    504: 'Сервер отвечает слишком долго'
  };

  function toError(status, body) {
    const er = body && body.error;
    return new ApiError(er?.message || DEFAULT_MESSAGES[status] || 'Внутренняя ошибка сервера', {
      status,
      code: er?.code || 'HTTP_' + status,
      details: er?.details || {},
      requestId: body?.request_id || null
    });
  }

  async function request(path, { method = 'GET', json, signal } = {}) {
    let res;
    try {
      res = await fetch(CFG.API_BASE + path, {
        method,
        headers: { Accept: 'application/json', ...(json ? { 'Content-Type': 'application/json' } : {}) },
        body: json ? JSON.stringify(json) : undefined,
        signal
      });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      throw new ApiError('Нет связи с сервером', { code: 'NETWORK' });
    }
    let body = null;
    try { body = await res.json(); } catch {  }
    if (!res.ok) throw toError(res.status, body);
    if (!body || !('data' in body)) throw new ApiError('Сервер вернул неожиданный ответ', { status: res.status, code: 'BAD_RESPONSE' });
    return body;
  }

  function uploadLecture(title, file, onProgress, category = 'other') {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append('title', title);
      fd.append('category', category);
      fd.append('file', file);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', CFG.API_BASE + '/lectures');
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onerror = () => reject(new ApiError('Нет связи с сервером', { code: 'NETWORK' }));
      xhr.onload = () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText); } catch {  }
        if (xhr.status >= 200 && xhr.status < 300 && body && body.data) resolve(body);
        else reject(toError(xhr.status, body));
      };
      xhr.send(fd);
    });
  }

  window.ApiError = ApiError;
  window.RealApi = {
    uploadLecture,
    importLecture: (title, url, category = 'other') => request('/lectures/import', { method: 'POST', json: { title, url, category } }),
    listLectures: (page, perPage, category = '') => request(`/lectures?page=${page}&per_page=${perPage}${category ? '&category=' + encodeURIComponent(category) : ''}`),
    setCategory: (id, category) => request(`/lectures/${encodeURIComponent(id)}`, { method: 'PATCH', json: {category} }),
    getLecture: (id) => request(`/lectures/${encodeURIComponent(id)}`),
    getTranscript: (id) => request(`/lectures/${encodeURIComponent(id)}/transcript`),
    regenerateSummary: (id) => request(`/lectures/${encodeURIComponent(id)}/summary/regenerate`, {method:'POST'}),
    getSummary: (id) => request(`/lectures/${encodeURIComponent(id)}/summary`),
    search: (payload, signal) => request('/search', { method: 'POST', json: payload, signal }),
    retry: (id) => request(`/lectures/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
    getTerms: (id) => request(`/lectures/${encodeURIComponent(id)}/terms`),
    chat: (id, payload, signal) => request(`/lectures/${encodeURIComponent(id)}/chat`, { method: 'POST', json: payload, signal })
  };
})();
