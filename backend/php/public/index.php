<?php
declare(strict_types=1);

require dirname(__DIR__) . '/src/bootstrap.php';

use DeepLecture\ApiError;
use DeepLecture\App;
use DeepLecture\Config;

ini_set('display_errors', '0');
$requestId = Config::uuid();
header('Content-Type: application/json; charset=utf-8');
header('X-Request-ID: ' . $requestId);
header('X-Content-Type-Options: nosniff');
header('Cache-Control: no-store');
try {
    $path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
    if (!is_string($path)) {
        throw new ApiError(400, 'INVALID_URI', 'Некорректный адрес');
    }
    [$status, $body] = (new App())->dispatch($_SERVER['REQUEST_METHOD'] ?? 'GET', $path);
    http_response_code($status);
    if ($body instanceof \DeepLecture\Download) {
        header('Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document');
        header('Content-Disposition: attachment; filename="' . $body->filename . '"');
        header('Content-Length: ' . strlen($body->content));
        echo $body->content;
        exit;
    }
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    if (!$error instanceof ApiError) {
        // Do not log raw database/upstream messages, which can contain secrets.
        error_log('request=' . $requestId . ' exception=' . get_class($error)
            . ($error instanceof PDOException ? ' db_errno=' . (int) ($error->errorInfo[1] ?? 0) : ''));
        $error = new ApiError(500, 'INTERNAL_ERROR', 'Внутренняя ошибка сервера');
    }
    http_response_code($error->status);
    echo json_encode(['error' => ['code' => $error->errorCode, 'message' => $error->getMessage(), 'details' => $error->details === [] ? new stdClass() : $error->details], 'request_id' => $requestId], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
}
