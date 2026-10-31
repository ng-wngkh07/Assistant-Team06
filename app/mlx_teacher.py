"""Generate candidate supervision with the selected local model; not training approval."""

import argparse
import json
import re
from pathlib import Path

from app.trained_client import TrainedModelClient
from app.config import DEFAULT_CHAT_MODEL
from app.bilingual_terms import translation_guidance, translation_is_plausible
from app.prepare_v5 import prepare
from app.training_data import detect_language, format_context, pair_language, SOURCE_RE


class MLXTeacher:
    def __init__(self):
        self.client = TrainedModelClient(DEFAULT_CHAT_MODEL)

    def answer(self, system, user, max_tokens=900):
        return self.client.chat_complete(
            messages=[{"role": "user", "content": user}],
            system_prompt=system, max_tokens=max_tokens,
        ).strip()

    def translate(self, text):
        for attempt in range(2):
            user = "Translate the following English passage into Vietnamese:\n\n" + text
            if attempt:
                user = "Translate the complete passage into natural Vietnamese. " + translation_guidance(text) + "\n\n" + text
            translated = self.answer(
                "You are an English-to-Vietnamese translator. Translate into Vietnamese (tiếng Việt), "
                "preserving the meaning. Do not complete cut-off sentences. Return only the translated passage.", user)
            if translation_is_plausible(text, translated) and len(translated) >= 0.45 * len(text):
                return translated
        raise ValueError("MLX translation failed language/terminology checks: " + translated)

    def __call__(self, pair, *, model=None, reasoning_focus=""):
        translations = {s["id"]: self.translate(s["text"]) for s in pair["sources"] if detect_language(s["text"]) == "en"}
        system = ("Bạn là trợ lý học thuật tâm lý học. Chỉ dùng các đoạn được cung cấp, "
                  "không thêm nghiên cứu, tên người, số liệu hoặc cơ chế ngoài nguồn. "
                  "Giải thích ngắn gọn kết luận và giới hạn. Không chẩn đoán cá nhân. "
                  "Chỉ xuất JSON, không in suy nghĩ hay lời dẫn.")
        prompt = (format_context(pair, translations) + "\n\nTạo một câu hỏi cần thông tin từ cả S1 và S2, "
                  "và câu trả lời 70–150 từ bằng tiếng Việt. Trọng tâm: " + reasoning_focus + ". "
                  "Phân biệt điều được nêu với điều chưa thể kết luận. Đặt [S1] và [S2] ở cuối các ý có nguồn; "
                  "không dùng mã nguồn làm chủ ngữ. "
                  'Trả JSON {"items":[{"question":"...","answer":"..."}]}.')
        for attempt in range(2):
            content = self.answer(system, prompt + ("\nBắt buộc câu trả lời chứa cả [S1] và [S2]." if attempt else ""), 800)
            try:
                data = json.loads(content[content.index("{"):content.rindex("}")+1])
                item = data["items"][0]
                item["answer"] = re.sub(r"\[\s*S1\s*[,;]\s*S2\s*\]", "[S1] [S2]", item["answer"], flags=re.I)
                if len(item["question"]) < 15 or len(item["answer"]) < 80 or set(SOURCE_RE.findall(item["answer"])) != {"1", "2"}:
                    raise ValueError("MLX answer does not reference both sources: " + content)
                return {"language": pair_language(pair), "translations": translations, "items": [item]}
            except (ValueError, KeyError, IndexError, TypeError):
                if attempt:
                    raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, teacher=DEFAULT_CHAT_MODEL, teacher_fn=MLXTeacher()), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
