<?php
declare(strict_types=1);
namespace DeepLecture;

final class PythonClient
{
    public function request(string $path, ?array $payload = null): array
    {
        $url = rtrim(Config::env('PYTHON_API_URL', 'http://search:8000'), '/') . $path;
        $curl = curl_init($url);
        if ($curl === false) {
            throw new ApiError(503, 'SEARCH_UNAVAILABLE', 'Сервис поиска недоступен');
        }
        $body = '';
        curl_setopt_array($curl, [
            CURLOPT_CONNECTTIMEOUT_MS => 2000,
            CURLOPT_TIMEOUT_MS => 10000,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
            CURLOPT_WRITEFUNCTION => static function ($handle, string $chunk) use (&$body): int {
                if (strlen($body) + strlen($chunk) > 1048576) {
                    return 0;
                }
                $body .= $chunk;
                return strlen($chunk);
            },
            CURLOPT_HTTPHEADER => ['Accept: application/json', 'Content-Type: application/json'],
        ]);
        if ($payload !== null) {
            curl_setopt($curl, CURLOPT_POST, true);
            curl_setopt($curl, CURLOPT_POSTFIELDS, json_encode($payload, JSON_THROW_ON_ERROR));
        }
        curl_exec($curl);
        $error = curl_errno($curl);
        $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
        curl_close($curl);
        if ($error === CURLE_OPERATION_TIMEDOUT) {
            throw new ApiError(504, 'SEARCH_TIMEOUT', 'Превышено время ожидания поиска');
        }
        if ($error !== 0) {
            throw new ApiError(503, 'SEARCH_UNAVAILABLE', 'Сервис поиска временно недоступен');
        }
        if (in_array($status, [404, 409], true)) {
            throw new ApiError($status, $status === 404 ? 'LECTURE_NOT_FOUND' : 'LECTURE_NOT_READY', $status === 404 ? 'Лекция не найдена' : 'Индекс лекции ещё не готов');
        }
        if ($status === 503) {
            throw new ApiError(503, 'SEARCH_UNAVAILABLE', 'Сервис поиска временно недоступен');
        }
        if ($status !== 200) {
            throw new ApiError(502, 'SEARCH_INVALID_RESPONSE', 'Некорректный ответ сервиса поиска');
        }
        try {
            $decoded = json_decode($body, true, 32, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            throw new ApiError(502, 'SEARCH_INVALID_RESPONSE', 'Некорректный ответ сервиса поиска');
        }
        if (!is_array($decoded) || !array_key_exists('data', $decoded)) {
            throw new ApiError(502, 'SEARCH_INVALID_RESPONSE', 'Некорректный ответ сервиса поиска');
        }
        return $decoded;
    }
}
