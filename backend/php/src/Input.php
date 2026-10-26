<?php
declare(strict_types=1);
namespace DeepLecture;

final class Input
{
    public static function string(mixed $value, string $name, int $max): string
    {
        if (!is_string($value) || !mb_check_encoding($value, 'UTF-8')) {
            throw self::invalid($name, 'Ожидается строка UTF-8');
        }
        $value = preg_replace('/^\s+|\s+$/u', '', $value) ?? '';
        if ($value === '' || mb_strlen($value, 'UTF-8') > $max) {
            throw self::invalid($name, "Допустимая длина: 1–{$max} символов");
        }
        return $value;
    }

    public static function integer(mixed $value, string $name, int $min, int $max, bool $allowString = false): int
    {
        if ($allowString && is_string($value) && preg_match('/^[0-9]{1,10}$/D', $value)) {
            $value = (int) $value;
        }
        if (!is_int($value) || $value < $min || $value > $max) {
            throw self::invalid($name, "Ожидается целое число от {$min} до {$max}");
        }
        return $value;
    }

    public static function invalid(string $field, string $message): ApiError
    {
        return new ApiError(422, 'VALIDATION_ERROR', 'Проверьте поля запроса', ['fields' => [$field => $message]]);
    }

    public static function json(): array
    {
        if (!preg_match('~^application/json(?:\s*;|$)~i', $_SERVER['CONTENT_TYPE'] ?? '')) {
            throw new ApiError(415, 'UNSUPPORTED_MEDIA_TYPE', 'Ожидается application/json');
        }
        if ((int) ($_SERVER['CONTENT_LENGTH'] ?? 0) > 65536) {
            throw new ApiError(413, 'REQUEST_TOO_LARGE', 'Запрос слишком большой');
        }
        $raw = file_get_contents('php://input', false, null, 0, 65537);
        if ($raw === false || strlen($raw) > 65536) {
            throw new ApiError(413, 'REQUEST_TOO_LARGE', 'Запрос слишком большой');
        }
        try {
            $value = json_decode($raw, false, 32, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            throw new ApiError(400, 'INVALID_JSON', 'Некорректный JSON');
        }
        if (!$value instanceof \stdClass) {
            throw self::invalid('body', 'Ожидается JSON-объект');
        }
        return (array) $value;
    }
}
