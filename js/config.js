window.APP_CONFIG = {
  USE_MOCK: true,                 // false — работать с реальным API
  API_BASE: '/api/v1',            // при отдельном порту: 'http://localhost:8080/api/v1' (нужен CORS)
  POLL_MS: 2000,                  // опрос статуса лекции
  MAX_FILE_BYTES: 500 * 1024 * 1024,
  // Короткий тестовый ролик для демо-режима (нужен интернет).
  // Можно положить свой файл в assets/sample.mp4 и указать 'assets/sample.mp4'.
  MOCK_VIDEO_URL: 'https://www.w3schools.com/html/mov_bbb.mp4'
};