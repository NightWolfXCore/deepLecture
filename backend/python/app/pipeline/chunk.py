"""Tokenizer-aware passage windows retaining links to original ASR segments."""
from uuid import uuid4
from .errors import PipelineError


def build_chunks(segments, tokenizer, max_tokens=384, overlap_tokens=64):
    if max_tokens < 16 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("Invalid chunk sizes")
    limit = min(max_tokens, getattr(tokenizer, "model_max_length", 512), 512)
    def encode(text):
        return tokenizer.encode(text, add_special_tokens=False)
    # Include E5 prefix and model special tokens in the upper bound.
    special = tokenizer.num_special_tokens_to_add(pair=False)
    capacity = limit - len(encode("passage: ")) - special - 2
    units = []
    for segment in segments:
        tokens = encode(segment["text"])
        if len(tokens) <= capacity:
            if tokens:
                units.append((segment, segment["text"]))
        else:
            for offset in range(0, len(tokens), capacity):
                text = tokenizer.decode(tokens[offset:offset+capacity], skip_special_tokens=True).strip()
                if text:
                    units.append((segment, text))
    result = []
    start = 0
    while start < len(units):
        end = start
        texts = []
        while end < len(units):
            candidate = " ".join(texts + [units[end][1]])
            if len(encode("passage: " + candidate)) + special > limit:
                break
            texts.append(units[end][1])
            end += 1
        if end == start:
            raise PipelineError("CHUNK_TOO_LONG", "Не удалось разбить текст по лимиту модели")
        first, last = units[start][0], units[end-1][0]
        result.append(dict(id=str(uuid4()), position=len(result), start_ms=first["start_ms"],
                           end_ms=last["end_ms"], text=" ".join(texts),
                           first_segment_id=first["id"], last_segment_id=last["id"]))
        if end == len(units):
            break
        next_start = end
        # Whole-segment overlap where possible, always make forward progress.
        while next_start > start + 1:
            tail = " ".join(x[1] for x in units[next_start-1:end])
            if len(encode(tail)) > overlap_tokens:
                break
            next_start -= 1
        start = next_start
    if not result:
        raise PipelineError("TRANSCRIPT_EMPTY", "Текст лекции пуст")
    return result
