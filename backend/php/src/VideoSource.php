<?php
declare(strict_types=1);
namespace DeepLecture;

final class VideoSource
{
    public static function canonical(string $url): string
    {
        $p = parse_url($url);
        if ($p === false || !in_array(strtolower($p['scheme'] ?? ''), ['http', 'https'], true)
            || isset($p['user']) || isset($p['pass']) || isset($p['port'])) {
            throw Input::invalid('url', 'Укажите публичную ссылку YouTube или Vimeo');
        }
        $host = strtolower($p['host'] ?? '');
        $path = $p['path'] ?? '';
        $id = null;
        if (in_array($host, ['youtube.com', 'www.youtube.com', 'm.youtube.com'], true)) {
            if ($path === '/watch') {
                parse_str($p['query'] ?? '', $q);
                $id = $q['v'] ?? null;
            } elseif (preg_match('~^/(?:shorts|embed)/([A-Za-z0-9_-]{11})/?$~D', $path, $m)) {
                $id = $m[1];
            }
        } elseif ($host === 'youtu.be' && preg_match('~^/([A-Za-z0-9_-]{11})/?$~D', $path, $m)) {
            $id = $m[1];
        }
        if (is_string($id) && preg_match('~^[A-Za-z0-9_-]{11}$~D', $id)) {
            return 'https://www.youtube.com/watch?v=' . $id;
        }
        if (in_array($host, ['vimeo.com', 'www.vimeo.com', 'player.vimeo.com'], true)
            && preg_match('~^/(?:video/)?([0-9]{1,15})/?$~D', $path, $m)) {
            return 'https://vimeo.com/' . $m[1];
        }
        throw Input::invalid('url', 'Поддерживаются публичные видео YouTube и Vimeo, не плейлисты');
    }
}
