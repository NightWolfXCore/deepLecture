<?php
declare(strict_types=1);
require dirname(__DIR__, 3) . '/backend/php/src/bootstrap.php';

use DeepLecture\ApiError;
use DeepLecture\App;
use DeepLecture\Config;
use DeepLecture\Input;
use DeepLecture\VideoSource;
use DeepLecture\Terms;

$count = 0;
function check(bool $condition, string $label): void {
    global $count;
    if (!$condition) { throw new RuntimeException('FAILED: ' . $label); }
    $count++;
}
function rejects(callable $fn, string $code): void {
    try { $fn(); } catch (ApiError $e) { check($e->errorCode === $code, $code); return; }
    throw new RuntimeException('Expected ' . $code);
}

check(Config::isUuid(Config::uuid()), 'generated UUID is valid');
check(!Config::isUuid('../secret'), 'path cannot be UUID');
check(!Config::isUuid([]), 'array cannot be UUID');
check(Input::string("  лекция \n", 'title', 200) === 'лекция', 'unicode trim');
rejects(fn () => Input::string(['x'], 'title', 200), 'VALIDATION_ERROR');
rejects(fn () => Input::string(str_repeat('я', 201), 'title', 200), 'VALIDATION_ERROR');
rejects(fn () => Input::string("\xff", 'title', 200), 'VALIDATION_ERROR');
check(Input::integer('20', 'page', 1, 100, true) === 20, 'query integer');
rejects(fn () => Input::integer(true, 'limit', 1, 20), 'VALIDATION_ERROR');
rejects(fn () => Input::integer('5', 'limit', 1, 20), 'VALIDATION_ERROR');
rejects(fn () => Input::integer('1e2', 'page', 1, 100, true), 'VALIDATION_ERROR');
rejects(fn () => Input::integer(0, 'limit', 1, 20), 'VALIDATION_ERROR');
check(Config::timestamp('2026-10-26 08:00:00.123') === '2026-10-26T08:00:00.123Z', 'UTC timestamp');
$app = new App();
check($app->dispatch('GET', '/api/v1/health') === [200, ['data' => ['status' => 'ok']]], 'health does not need database');
rejects(fn () => $app->dispatch('GET', '/api/v1/search'), 'METHOD_NOT_ALLOWED');
rejects(fn () => $app->dispatch('GET', '/api/v1/nope'), 'NOT_FOUND');
rejects(fn () => $app->dispatch('GET', '/api/v1/lectures/bad'), 'LECTURE_NOT_FOUND');
check(VideoSource::canonical('https://youtu.be/abcdefghijk?t=12') === 'https://www.youtube.com/watch?v=abcdefghijk', 'YouTube URL normalized');
check(VideoSource::canonical('https://player.vimeo.com/video/1234567') === 'https://vimeo.com/1234567', 'Vimeo URL normalized');
foreach (['http://127.0.0.1/a', 'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://u:p@youtube.com/watch?v=abcdefghijk', 'https://youtube.com:443/watch?v=abcdefghijk', 'https://youtube.com/playlist?list=abc', 'file:///tmp/video.mp4'] as $url) {
    rejects(fn () => VideoSource::canonical($url), 'VALIDATION_ERROR');
}
$terms = Terms::extract([['id' => 'segment', 'text' => 'Теорема Пифагора. Теорема Пифагора связывает стороны.', 'start_ms' => 12000, 'end_ms' => 18000]]);
check(count($terms) > 0, 'terms extracted');
check($terms[0]['mentions'][0]['start_ms'] === 12000, 'term anchored to transcript');
check(str_contains($terms[0]['definition'], 'Теорема Пифагора'), 'definition is source excerpt');
check(count(array_filter($terms, fn ($t) => !str_contains('теорема пифагора. теорема пифагора связывает стороны.', $t['term']))) === 0, 'term phrases do not cross sentence boundaries');
echo "PHP unit: {$count} checks passed\n";
