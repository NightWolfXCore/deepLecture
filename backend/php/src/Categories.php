<?php
declare(strict_types=1);
namespace DeepLecture;
final class Categories
{
    public const VALUES = ['mathematics','russian','physics','informatics','chemistry','biology','history','geography','literature','foreign_languages','other'];
    public static function validate(mixed $value): string
    {
        if (!is_string($value) || !in_array($value, self::VALUES, true)) throw Input::invalid('category', 'Выберите категорию лекции');
        return $value;
    }
}
