<?php
declare(strict_types=1);
namespace DeepLecture;

final class Download
{
    public function __construct(public readonly string $content, public readonly string $filename) {}
}
