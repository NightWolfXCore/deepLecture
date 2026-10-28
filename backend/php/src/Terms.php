<?php
declare(strict_types=1);
namespace DeepLecture;

/** Extractive frequency/co-occurrence keywords; definitions are transcript excerpts. */
final class Terms
{
    public static function extract(array $segments): array
    {
        $stop = array_fill_keys(explode(' ', 'это этой этого этот эти как что чтобы для или при под над без после перед через между есть был были будет быть можно нужно надо также уже ещё еще очень более менее только всего мы вы они она оно его её их наш ваш нас вас мне мне нам вам себе себя свой который которая которые которых потому поэтому тогда когда если так вот здесь там где кто чем тем том этом этих the and that this with from into are was were will have has not for you your our its about then than also'), true);
        $terms = [];
        foreach ($segments as $seg) {
            $text = mb_strtolower($seg['text']);
            preg_match_all('/[\p{L}][\p{L}\p{N}-]{2,}/u', $text, $m, PREG_OFFSET_CAPTURE);
            $previous = null;
            $previousEnd = 0;
            foreach ($m[0] as [$word, $offset]) {
                if (isset($stop[$word]) || mb_strlen($word) < 4 || mb_strlen($word) > 40) {
                    $previous = null;
                    continue;
                }
                $candidates = [$word];
                if ($previous !== null && preg_match('/^\s+$/u', substr($text, $previousEnd, $offset - $previousEnd))) $candidates[] = $previous . ' ' . $word;
                foreach ($candidates as $term) {
                    if (!isset($terms[$term]) && count($terms) >= 50000) continue;
                    if (!isset($terms[$term])) $terms[$term] = ['term' => $term, 'count' => 0, 'definition' => mb_substr($seg['text'], 0, 700), 'mentions' => []];
                    $terms[$term]['count']++;
                    if (count($terms[$term]['mentions']) < 8) {
                        $terms[$term]['mentions'][$seg['id']] = ['segment_id' => $seg['id'], 'start_ms' => (int) $seg['start_ms'], 'end_ms' => (int) $seg['end_ms']];
                    }
                }
                $previous = $word;
                $previousEnd = $offset + strlen($word);
            }
        }
        $result = array_values($terms);
        usort($result, static fn ($a, $b) => (($b['count'] * (str_contains($b['term'], ' ') ? 1.6 : 1)) <=> ($a['count'] * (str_contains($a['term'], ' ') ? 1.6 : 1))) ?: strcmp($a['term'], $b['term']));
        return array_map(static function ($term) {
            $term['mentions'] = array_values($term['mentions']);
            return $term;
        }, array_slice($result, 0, 15));
    }
}
