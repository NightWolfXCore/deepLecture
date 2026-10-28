// Backend integration override; the frontend participant's files stay untouched.
window.APP_CONFIG = {
  USE_MOCK: false,
  API_BASE: '/api/v1',
  POLL_MS: 2000,
  MAX_FILE_BYTES: 500 * 1024 * 1024,
  MOCK_VIDEO_URL: ''
};
