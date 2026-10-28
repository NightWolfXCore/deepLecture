// Exercise the actual frontend API client served by Nginx against PHP/MySQL.
// No browser/DOM/video, uploads or real ML inference are covered by this check.
import assert from 'node:assert/strict';
import vm from 'node:vm';

const base = new URL(process.env.TEST_API_URL || 'http://127.0.0.1:18080');
async function read(path) {
  const response = await fetch(new URL(path, base), {signal: AbortSignal.timeout(10000)});
  assert.equal(response.status, 200, `GET ${path}`);
  return response.text();
}
assert.match(await read('/'), /js\/api\.js/);
const context = vm.createContext({
  window: {},
  fetch: (path, options) => fetch(new URL(path, base), {...options, signal: AbortSignal.timeout(10000)})
});
vm.runInContext(await read('/js/config.js'), context);
assert.equal(context.window.APP_CONFIG.USE_MOCK, false);
assert.equal(context.window.APP_CONFIG.API_BASE, '/api/v1');
vm.runInContext(await read('/js/api.js'), context);
const api = context.window.RealApi;
const catalog = await api.listLectures(1, 20);
assert.ok(Array.isArray(catalog.data));
assert.ok(Number.isInteger(catalog.meta.total));
assert.equal(catalog.meta.page, 1);
const missing = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
for (const call of [() => api.getLecture(missing), () => api.getTranscript(missing),
                    () => api.getSummary(missing),
                    () => api.search({lecture_id: missing, query: 'Теорема Пифагора', limit: 5})]) {
  await assert.rejects(call, error => error.status === 404 && error.code === 'LECTURE_NOT_FOUND' && !!error.requestId);
}
console.log('Frontend RealApi → PHP/MySQL smoke OK; browser/video and real ML remain untested.');
