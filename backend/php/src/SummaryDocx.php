<?php
declare(strict_types=1);
namespace DeepLecture;

/** Small, dependency-free OOXML writer. All document text is escaped. */
final class SummaryDocx
{
    private static function xml(string $text): string
    {
        return htmlspecialchars(preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F]/u', '', $text) ?? '', ENT_XML1 | ENT_QUOTES, 'UTF-8');
    }

    private static function paragraph(string $text, string $style = 'Normal'): string
    {
        $runs = '';
        foreach (preg_split('/(\*\*[^*]+\*\*)/u', $text, -1, PREG_SPLIT_DELIM_CAPTURE) as $part) {
            $bold = str_starts_with($part, '**') && str_ends_with($part, '**') && strlen($part) >= 4;
            $runs .= '<w:r>' . ($bold ? '<w:rPr><w:b/></w:rPr>' : '') . '<w:t xml:space="preserve">' . self::xml($bold ? substr($part, 2, -2) : $part) . '</w:t></w:r>';
        }
        return '<w:p><w:pPr><w:pStyle w:val="' . $style . '"/></w:pPr>' . $runs . '</w:p>';
    }

    public static function create(string $title, array $summary): string
    {
        $body = self::paragraph($title, 'Title') . self::paragraph('Учебный конспект · deepLecture', 'Subtitle') . self::paragraph('ИИ может ошибаться — проверяйте определения, формулы и выводы по материалам лекции.');
        foreach (preg_split('/\R/u', $summary['text']) as $line) {
            $line = trim($line);
            if ($line === '') continue;
            if (preg_match('/^(#{1,3})\s+(.+)$/u', $line, $m)) {
                $body .= self::paragraph($m[2], strlen($m[1]) === 1 ? 'Heading1' : 'Heading2');
            } elseif (preg_match('/^[-*]\s+(.+)$/u', $line, $m)) {
                $body .= self::paragraph('• ' . $m[1], 'ListParagraph');
            } else {
                $body .= self::paragraph($line);
            }
        }
        if ($summary['chapters']) {
            $body .= self::paragraph('Навигация по лекции', 'Heading1');
            foreach ($summary['chapters'] as $chapter) {
                $seconds = intdiv((int) $chapter['start_ms'], 1000);
                $time = sprintf('%02d:%02d:%02d', intdiv($seconds, 3600), intdiv($seconds % 3600, 60), $seconds % 60);
                $body .= self::paragraph($time . '  ' . $chapter['title'], $chapter['level'] === 2 ? 'ListParagraph' : 'Normal');
            }
        }
        $ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
        $document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="' . $ns . '"><w:body>' . $body . '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>';
        $styles = '<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="' . $ns . '"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="23"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>';
        foreach (['Normal'=>['Обычный',23,false], 'Title'=>['Заголовок',40,true], 'Subtitle'=>['Подзаголовок',24,false], 'Heading1'=>['Заголовок 1',30,true], 'Heading2'=>['Заголовок 2',26,true], 'ListParagraph'=>['Список',23,false]] as $id=>$style) {
            $heading = str_starts_with($id, 'Heading');
            $styles .= '<w:style w:type="paragraph" w:styleId="' . $id . '"' . ($id === 'Normal' ? ' w:default="1"' : '') . '><w:name w:val="' . $style[0] . '"/>' . ($id !== 'Normal' ? '<w:basedOn w:val="Normal"/>' : '') . '<w:pPr>' . ($heading ? '<w:keepNext/><w:outlineLvl w:val="' . ($id === 'Heading1' ? '0' : '1') . '"/>' : '') . ($id === 'ListParagraph' ? '<w:ind w:left="360"/>' : '') . '</w:pPr><w:rPr><w:sz w:val="' . $style[1] . '"/>' . ($style[2] ? '<w:b/><w:color w:val="433878"/>' : '') . '</w:rPr></w:style>';
        }
        $types = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>';
        $rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>';
        $docRels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>';
        return self::zip(['[Content_Types].xml'=>$types, '_rels/.rels'=>$rels, 'word/document.xml'=>$document, 'word/styles.xml'=>$styles . '</w:styles>', 'word/_rels/document.xml.rels'=>$docRels]);
    }

    private static function zip(array $files): string
    {
        $data = $central = '';
        foreach ($files as $name => $content) {
            $size = strlen($content); $crc = crc32($content); $offset = strlen($data); $length = strlen($name);
            $data .= pack('VvvvvvVVVvv', 0x04034b50, 20, 0, 0, 0, 33, $crc, $size, $size, $length, 0) . $name . $content;
            $central .= pack('VvvvvvvVVVvvvvvVV', 0x02014b50, 20, 20, 0, 0, 0, 33, $crc, $size, $size, $length, 0, 0, 0, 0, 0, $offset) . $name;
        }
        return $data . $central . pack('VvvvvVVv', 0x06054b50, 0, 0, count($files), count($files), strlen($central), strlen($data), 0);
    }
}
