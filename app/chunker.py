import re
from typing import List, Dict, Any
from app.config import CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS

class TextChunker:
    @staticmethod
    def chunk_page(
        book_title: str,
        filename: str,
        page_num: int,
        page_text: str,
        chunk_size: int = CHUNK_SIZE_CHARS,
        chunk_overlap: int = CHUNK_OVERLAP_CHARS
    ) -> List[Dict[str, Any]]:
        """
        Split a page into manageable chunks with overlap while preserving book title and page number.
        If the page fits in chunk_size, keeps it as a single chunk.
        """
        text = page_text.strip()
        if not text:
            return []
        
        if chunk_size < 1 or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("Require chunk_size > chunk_overlap >= 0")
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                # Choose candidate end boundaries by priority: paragraph, line,
                # sentence, word, hard character. Priority applies in existing
                # legal half-window and must keep size bound.
                minimum = start + max(chunk_overlap + 1, chunk_size // 2)
                window = text[minimum:end]
                if window:
                    chosen = None
                    # Priority 1: Paragraph boundary (\n\n+)
                    paras = list(re.finditer(r"\n\n+", window))
                    if paras:
                        chosen = minimum + paras[-1].end()
                    else:
                        # Priority 2: Line boundary (\n)
                        # Avoid breaking inside markdown table rows
                        lines = list(re.finditer(r"\n", window))
                        if lines:
                            non_table = []
                            for m in lines:
                                pos = minimum + m.start()
                                b_start = text.rfind("\n", 0, pos)
                                b_line = text[0 if b_start == -1 else b_start + 1 : pos].strip()
                                a_end = text.find("\n", pos + 1)
                                a_line = text[pos + 1 : len(text) if a_end == -1 else a_end].strip()
                                if not (b_line.startswith("|") and a_line.startswith("|")):
                                    non_table.append(m)
                            if non_table:
                                chosen = minimum + non_table[-1].end()
                            else:
                                chosen = minimum + lines[-1].end()
                        else:
                            # Priority 3: Sentence boundary
                            sentences = list(re.finditer(r"[.!?。]\s+", window))
                            if sentences:
                                chosen = minimum + sentences[-1].end()
                            else:
                                # Priority 4: Word boundary
                                words = list(re.finditer(r"\s+", window))
                                if words:
                                    chosen = minimum + words[-1].end()
                    # Priority 5 is falling back to hard character (end)
                    if chosen is not None:
                        end = chosen
            chunk_text = text[start:end]
            chunks.append({
                "book_title": book_title, "filename": filename,
                "page_num": page_num, "chunk_index": len(chunks),
                "text": chunk_text, "char_count": len(chunk_text),
                "char_start": start, "char_end": end,
            })
            if end == len(text):
                break
            start = end - chunk_overlap
        return chunks
