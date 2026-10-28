<?php
declare(strict_types=1);
namespace DeepLecture;

use PDO;

final class App
{
    public function __construct(private readonly Database $db = new Database(), private readonly PythonClient $python = new PythonClient()) {}

    /** @return array{int,array} */
    public function dispatch(string $method, string $path): array
    {
        if ($path === '/api/v1/health' && $method === 'GET') {
            return [200, ['data' => ['status' => 'ok']]];
        }
        if ($path === '/api/v1/ready' && $method === 'GET') {
            try {
                $this->db->query('SELECT 1');
                $this->python->request('/internal/v1/ready');
            } catch (\Throwable) {
                throw new ApiError(503, 'DEPENDENCY_UNAVAILABLE', 'Зависимости приложения ещё не готовы');
            }
            return [200, ['data' => ['status' => 'ok']]];
        }
        if ($path === '/api/v1/lectures') {
            return match ($method) {
                'GET' => [200, $this->catalog()],
                'POST' => [202, ['data' => $this->upload()]],
                default => throw new ApiError(405, 'METHOD_NOT_ALLOWED', 'Метод не поддерживается'),
            };
        }
        if ($path === '/api/v1/lectures/import') {
            if ($method !== 'POST') throw new ApiError(405, 'METHOD_NOT_ALLOWED', 'Метод не поддерживается');
            return [202, ['data' => $this->importVideo(Input::json())]];
        }
        if ($path === '/api/v1/search') {
            if ($method !== 'POST') {
                throw new ApiError(405, 'METHOD_NOT_ALLOWED', 'Метод не поддерживается');
            }
            return [200, $this->search(Input::json())];
        }
        if (preg_match('~^/api/v1/lectures/([0-9a-f-]+)(?:/(transcript|summary|retry|terms|chat))?$~iD', $path, $matches)) {
            $id = strtolower($matches[1]);
            if (!Config::isUuid($id)) {
                throw new ApiError(404, 'LECTURE_NOT_FOUND', 'Лекция не найдена');
            }
            $action = $matches[2] ?? '';
            if ($method !== (in_array($action, ['retry', 'chat'], true) ? 'POST' : 'GET')) {
                throw new ApiError(405, 'METHOD_NOT_ALLOWED', 'Метод не поддерживается');
            }
            return match ($action) {
                '' => [200, ['data' => $this->card($id)]],
                'transcript' => [200, ['data' => $this->transcript($id)]],
                'summary' => [200, ['data' => $this->summary($id)]],
                'retry' => [202, ['data' => $this->retry($id)]],
                'terms' => [200, ['data' => $this->terms($id)]],
                'chat' => [200, $this->chat($id, Input::json())],
            };
        }
        throw new ApiError(404, 'NOT_FOUND', 'Ресурс не найден');
    }

    private function lecture(string $id, bool $lock = false): array
    {
        $row = $this->db->query('SELECT * FROM lectures WHERE id = ?' . ($lock ? ' FOR UPDATE' : ''), [$id])->fetch();
        if (!$row) {
            throw new ApiError(404, 'LECTURE_NOT_FOUND', 'Лекция не найдена');
        }
        return $row;
    }

    private function latestJob(string $id): ?array
    {
        return $this->db->query('SELECT * FROM processing_jobs WHERE lecture_id = ? ORDER BY created_at DESC, id DESC LIMIT 1', [$id])->fetch() ?: null;
    }

    private function catalog(): array
    {
        $page = Input::integer($_GET['page'] ?? 1, 'page', 1, 1000000, true);
        $perPage = Input::integer($_GET['per_page'] ?? 20, 'per_page', 1, 100, true);
        return $this->db->transaction(function () use ($page, $perPage): array {
            $total = (int) $this->db->query('SELECT COUNT(*) FROM lectures')->fetchColumn();
            $query = $this->db->pdo()->prepare('SELECT id, title, status, duration_ms, created_at FROM lectures ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?');
            $query->bindValue(1, $perPage, PDO::PARAM_INT);
            $query->bindValue(2, ($page - 1) * $perPage, PDO::PARAM_INT);
            $query->execute();
            $rows = $query->fetchAll();
            foreach ($rows as &$row) {
                $row['duration_ms'] = $row['duration_ms'] === null ? null : (int) $row['duration_ms'];
                $row['created_at'] = Config::timestamp($row['created_at']);
            }
            return ['data' => $rows, 'meta' => ['page' => $page, 'per_page' => $perPage, 'total' => $total]];
        });
    }

    private function card(string $id): array
    {
        return $this->db->transaction(function () use ($id): array {
            $row = $this->lecture($id);
            $job = $this->latestJob($id);
            $active = (bool) $this->db->query("SELECT 1 FROM processing_jobs WHERE lecture_id = ? AND status IN ('queued', 'running') LIMIT 1", [$id])->fetchColumn();
            // Never expose a malformed storage path, even if a database row was manually edited.
            $key = $row['storage_key'];
            if (!preg_match('~^videos/[0-9a-f-]{36}\.(mp4|webm)$~D', $key)) {
                throw new ApiError(500, 'INVALID_MEDIA_REFERENCE', 'Ошибка ссылки на видео');
            }
            return [
                'id' => $row['id'], 'title' => $row['title'], 'status' => $row['status'],
                'duration_ms' => $row['duration_ms'] === null ? null : (int) $row['duration_ms'],
                'media_url' => (int) $row['size_bytes'] > 0 ? '/media/' . $key : null,
                'availability' => ['transcript' => $row['transcript_ready_at'] !== null, 'search' => $row['index_ready_at'] !== null, 'summary' => $row['summary_ready_at'] !== null, 'terms' => $row['transcript_ready_at'] !== null],
                'processing' => [
                    'job_id' => $job['id'] ?? null,
                    'failed_stage' => $job['failed_stage'] ?? null,
                    'error_code' => $job['error_code'] ?? null,
                    'error_message' => $job['error_message'] ?? null,
                    'can_retry' => $row['status'] === 'failed' && !$active,
                ],
                'created_at' => Config::timestamp($row['created_at']),
            ];
        });
    }

    private function upload(): array
    {
        if (!preg_match('~^multipart/form-data(?:\s*;|$)~i', $_SERVER['CONTENT_TYPE'] ?? '')) {
            throw new ApiError(415, 'UNSUPPORTED_MEDIA_TYPE', 'Ожидается multipart/form-data');
        }
        // PHP empties both superglobals if post_max_size is exceeded.
        if ((int) ($_SERVER['CONTENT_LENGTH'] ?? 0) > Config::maxUpload() + 1048576) {
            throw new ApiError(413, 'FILE_TOO_LARGE', 'Видео превышает допустимый размер');
        }
        $title = Input::string($_POST['title'] ?? null, 'title', 200);
        $file = $_FILES['file'] ?? null;
        if (!is_array($file) || !isset($file['error']) || !is_int($file['error'])) {
            throw Input::invalid('file', 'Выберите видео');
        }
        if (in_array($file['error'], [UPLOAD_ERR_INI_SIZE, UPLOAD_ERR_FORM_SIZE], true)) {
            throw new ApiError(413, 'FILE_TOO_LARGE', 'Видео превышает допустимый размер');
        }
        if ($file['error'] !== UPLOAD_ERR_OK) {
            throw Input::invalid('file', 'Не удалось загрузить файл целиком');
        }
        $tmp = $file['tmp_name'] ?? null;
        if (!is_string($tmp) || !is_uploaded_file($tmp)) {
            throw Input::invalid('file', 'Некорректная загрузка');
        }
        $size = filesize($tmp);
        if ($size === false || $size <= 0) {
            throw Input::invalid('file', 'Файл пуст');
        }
        if ($size > Config::maxUpload()) {
            throw new ApiError(413, 'FILE_TOO_LARGE', 'Видео превышает допустимый размер');
        }
        $name = $file['name'] ?? '';
        if (!is_string($name) || !mb_check_encoding($name, 'UTF-8')) {
            throw Input::invalid('file', 'Некорректное имя файла');
        }
        $name = basename(str_replace('\\', '/', $name));
        $extension = strtolower(pathinfo($name, PATHINFO_EXTENSION));
        $mime = (new \finfo(FILEINFO_MIME_TYPE))->file($tmp);
        $allowed = ['mp4' => ['video/mp4', 'application/mp4'], 'webm' => ['video/webm', 'video/x-matroska', 'application/webm']];
        if (!isset($allowed[$extension]) || !in_array($mime, $allowed[$extension], true)) {
            throw new ApiError(415, 'UNSUPPORTED_VIDEO_FORMAT', 'Поддерживаются видео MP4 и WebM');
        }
        $id = Config::uuid();
        $jobId = Config::uuid();
        $key = 'videos/' . $id . '.' . $extension;
        $dir = Config::storage() . '/videos';
        if (!is_dir($dir) && !mkdir($dir, 0755, true) && !is_dir($dir)) {
            throw new ApiError(503, 'STORAGE_UNAVAILABLE', 'Хранилище недоступно');
        }
        $destination = Config::storage() . '/' . $key;
        if (!move_uploaded_file($tmp, $destination)) {
            throw new ApiError(503, 'STORAGE_UNAVAILABLE', 'Не удалось сохранить видео');
        }
        // These files are public media served by Nginx's distinct runtime UID.
        chmod($destination, 0644);
        try {
            $this->db->transaction(function () use ($id, $title, $name, $key, $mime, $size, $jobId): void {
                $this->db->query("INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes,status,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))", [$id, $title, mb_substr($name, 0, 255), $key, $mime, $size]);
                $this->insertJob($id, $jobId);
            });
        } catch (\Throwable $e) {
            unlink($destination);
            throw $e;
        }
        return ['id' => $id, 'title' => $title, 'status' => 'queued', 'job_id' => $jobId];
    }

    private function importVideo(array $body): array
    {
        $title = Input::string($body['title'] ?? null, 'title', 200);
        $url = VideoSource::canonical(Input::string($body['url'] ?? null, 'url', 2000));
        $id = Config::uuid();
        $jobId = Config::uuid();
        $dir = Config::storage() . '/imports';
        if (!is_dir($dir) && !mkdir($dir, 0755, true) && !is_dir($dir)) throw new ApiError(503, 'STORAGE_UNAVAILABLE', 'Хранилище недоступно');
        $manifest = $dir . '/' . $id . '.json';
        if (file_put_contents($manifest, json_encode(['url' => $url], JSON_THROW_ON_ERROR), LOCK_EX) === false) throw new ApiError(503, 'STORAGE_UNAVAILABLE', 'Хранилище недоступно');
        chmod($manifest, 0644);
        try {
            $this->db->transaction(function () use ($id, $jobId, $title): void {
                $this->db->query("INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes,status,created_at,updated_at) VALUES (?,?,?,?,'video/mp4',0,'queued',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))", [$id, $title, $id . '.mp4', 'videos/' . $id . '.mp4']);
                $this->insertJob($id, $jobId);
            });
        } catch (\Throwable $e) {
            unlink($manifest);
            throw $e;
        }
        return ['id' => $id, 'title' => $title, 'status' => 'queued', 'job_id' => $jobId];
    }

    private function terms(string $id): array
    {
        try {
            $transcript = $this->transcript($id);
        } catch (ApiError $e) {
            if ($e->errorCode === 'TRANSCRIPT_NOT_READY') throw new ApiError(409, 'TERMS_NOT_READY', 'Транскрипт для терминов ещё не готов');
            throw $e;
        }
        return ['lecture_id' => $id, 'terms' => Terms::extract($transcript['segments'])];
    }

    private function chat(string $id, array $body): array
    {
        $question = Input::string($body['question'] ?? null, 'question', 1000);
        $history = $body['history'] ?? [];
        if (!is_array($history) || !array_is_list($history) || count($history) > 6) throw Input::invalid('history', 'Допускается до шести сообщений');
        foreach ($history as $message) {
            if (!is_array($message) || !in_array($message['role'] ?? null, ['user', 'assistant'], true)) throw Input::invalid('history', 'Некорректная история');
            Input::string($message['content'] ?? null, 'history', 4000);
        }
        if ($this->lecture($id)['index_ready_at'] === null) throw new ApiError(409, 'LECTURE_NOT_READY', 'Индекс лекции ещё не готов');
        set_time_limit(290);
        return $this->python->request('/internal/v1/chat', ['lecture_id' => $id, 'question' => $question, 'history' => $history], 280000);
    }

    private function insertJob(string $lectureId, string $jobId): void
    {
        $this->db->query("INSERT INTO processing_jobs (id,lecture_id,status,stage,attempt,created_at,updated_at) VALUES (?,?,'queued','queued',0,UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))", [$jobId, $lectureId]);
    }

    private function transcript(string $id): array
    {
        return $this->db->transaction(function () use ($id): array {
            $lecture = $this->lecture($id);
            if ($lecture['transcript_ready_at'] === null) {
                throw new ApiError(409, 'TRANSCRIPT_NOT_READY', 'Транскрипт ещё не готов');
            }
            $rows = $this->db->query('SELECT id,position,start_ms,end_ms,text FROM transcript_segments WHERE lecture_id=? ORDER BY position', [$id])->fetchAll();
            foreach ($rows as &$row) {
                foreach (['position', 'start_ms', 'end_ms'] as $field) {
                    $row[$field] = (int) $row[$field];
                }
            }
            return ['lecture_id' => $id, 'segments' => $rows];
        });
    }

    private function summary(string $id): array
    {
        return $this->db->transaction(function () use ($id): array {
            $lecture = $this->lecture($id);
            if ($lecture['summary_ready_at'] === null) {
                $job = $this->latestJob($id);
                if (($job['failed_stage'] ?? null) === 'summarizing' && ($job['status'] ?? null) === 'failed') {
                    throw new ApiError(409, 'SUMMARY_FAILED', 'Не удалось создать конспект');
                }
                throw new ApiError(409, 'SUMMARY_NOT_READY', 'Конспект ещё не готов');
            }
            $text = $this->db->query('SELECT text FROM lecture_summaries WHERE lecture_id=?', [$id])->fetchColumn();
            if ($text === false) {
                throw new ApiError(500, 'SUMMARY_INCONSISTENT', 'Конспект временно недоступен');
            }
            $rows = $this->db->query('SELECT c.id,c.position,c.title,c.level,c.start_segment_id,s.start_ms FROM lecture_chapters c JOIN transcript_segments s ON s.id=c.start_segment_id AND s.lecture_id=c.lecture_id WHERE c.lecture_id=? ORDER BY c.position', [$id])->fetchAll();
            foreach ($rows as &$row) {
                foreach (['position', 'level', 'start_ms'] as $field) {
                    $row[$field] = (int) $row[$field];
                }
            }
            return ['lecture_id' => $id, 'text' => $text, 'chapters' => $rows];
        });
    }

    private function retry(string $id): array
    {
        return $this->db->transaction(function () use ($id): array {
            $lecture = $this->lecture($id, true);
            $active = $this->db->query("SELECT id FROM processing_jobs WHERE lecture_id=? AND status IN ('queued','running') LIMIT 1 FOR UPDATE", [$id])->fetch();
            if ($active) {
                throw new ApiError(409, 'PROCESSING_ALREADY_ACTIVE', 'Обработка уже запущена');
            }
            if ($lecture['status'] !== 'failed') {
                throw new ApiError(409, 'RETRY_NOT_ALLOWED', 'Повтор доступен только после ошибки');
            }
            $this->db->query('DELETE FROM lecture_chapters WHERE lecture_id=?', [$id]);
            $this->db->query('DELETE FROM lecture_summaries WHERE lecture_id=?', [$id]);
            $this->db->query('DELETE e FROM chunk_embeddings e JOIN search_chunks c ON c.id=e.chunk_id WHERE c.lecture_id=?', [$id]);
            $this->db->query('DELETE FROM search_chunks WHERE lecture_id=?', [$id]);
            $this->db->query('DELETE FROM transcript_segments WHERE lecture_id=?', [$id]);
            $this->db->query("UPDATE lectures SET status='queued',transcript_ready_at=NULL,index_ready_at=NULL,summary_ready_at=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=?", [$id]);
            $jobId = Config::uuid();
            $this->insertJob($id, $jobId);
            return ['id' => $id, 'status' => 'queued', 'job_id' => $jobId];
        });
    }

    private function search(array $body): array
    {
        if (array_key_exists('lecture_id', $body) && !Config::isUuid($body['lecture_id'])) {
            throw Input::invalid('lecture_id', 'Ожидается UUID лекции');
        }
        $id = isset($body['lecture_id']) ? strtolower($body['lecture_id']) : null;
        $query = Input::string($body['query'] ?? null, 'query', 1000);
        $limit = Input::integer($body['limit'] ?? 5, 'limit', 1, 20);
        if ($id !== null && $this->lecture($id)['index_ready_at'] === null) {
            throw new ApiError(409, 'LECTURE_NOT_READY', 'Индекс лекции ещё не готов');
        }
        $payload = ['query' => $query, 'limit' => $limit];
        if ($id !== null) $payload['lecture_id'] = $id;
        $answer = $this->python->request('/internal/v1/search', $payload);
        $hits = $answer['data'];
        if (!is_array($hits) || !array_is_list($hits) || count($hits) > $limit) {
            throw new ApiError(502, 'SEARCH_INVALID_RESPONSE', 'Некорректный ответ сервиса поиска');
        }
        $seen = [];
        foreach ($hits as $hit) {
            if (!is_array($hit) || !Config::isUuid($hit['chunk_id'] ?? null) || !isset($hit['score']) || (!is_int($hit['score']) && !is_float($hit['score'])) || !is_finite((float) $hit['score']) || abs($hit['score']) > 1.000001 || isset($seen[$hit['chunk_id']])) {
                throw new ApiError(502, 'SEARCH_INVALID_RESPONSE', 'Некорректный ответ сервиса поиска');
            }
            $seen[$hit['chunk_id']] = true;
        }
        return $this->db->transaction(function () use ($id, $query, $hits): array {
            $lecture = $id !== null ? $this->lecture($id) : null;
            if ($lecture !== null && $lecture['index_ready_at'] === null) {
                throw new ApiError(409, 'LECTURE_NOT_READY', 'Индекс лекции ещё не готов');
            }
            $chunks = [];
            if ($hits !== []) {
                $ids = array_column($hits, 'chunk_id');
                $placeholders = implode(',', array_fill(0, count($ids), '?'));
                $where = $id !== null ? ' AND c.lecture_id=?' : '';
                foreach ($this->db->query("SELECT c.id,c.lecture_id,c.text,c.start_ms,c.end_ms,l.title FROM search_chunks c JOIN lectures l ON l.id=c.lecture_id WHERE l.index_ready_at IS NOT NULL AND c.id IN ({$placeholders})" . $where, $id !== null ? [...$ids, $id] : $ids)->fetchAll() as $row) {
                    $chunks[$row['id']] = $row;
                }
                if (count($chunks) !== count($hits)) {
                    throw new ApiError(409, 'LECTURE_NOT_READY', 'Индекс лекции изменился, повторите поиск');
                }
            }
            $results = [];
            foreach ($hits as $hit) {
                $row = $chunks[$hit['chunk_id']];
                $start = (int) $row['start_ms'];
                $results[] = [
                    'chunk_id' => $row['id'], 'lecture_id' => $row['lecture_id'], 'lecture_title' => $row['title'],
                    'text' => $row['text'], 'start_ms' => $start, 'end_ms' => (int) $row['end_ms'],
                    'score' => max(-1.0, min(1.0, (float) $hit['score'])),
                    'playback_url' => '/?lecture=' . $row['lecture_id'] . '&t=' . rtrim(rtrim(number_format($start / 1000, 3, '.', ''), '0'), '.'),
                ];
            }
            // Sort defensively instead of trusting upstream ordering.
            usort($results, static fn (array $a, array $b): int => ($b['score'] <=> $a['score']) ?: ($a['start_ms'] <=> $b['start_ms']) ?: strcmp($a['chunk_id'], $b['chunk_id']));
            return ['data' => $results, 'meta' => ['query' => $query, 'count' => count($results)]];
        });
    }
}
