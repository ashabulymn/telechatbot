from pathlib import Path
from typing import Optional

TEXT_MIMES = {
    "text/plain", "text/markdown", "text/csv", "text/html",
    "application/json", "application/xml", "text/xml",
}

class FileParser:
    """Best-effort local extraction for common document/text attachments.

    Provider-specific binary processing remains the responsibility of provider
    adapters. Extraction is intentionally bounded to avoid huge prompts.
    """

    def __init__(self, max_chars: int = 30000):
        self.max_chars = max_chars

    def extract(self, path: str, mime_type: Optional[str], filename: Optional[str]) -> Optional[str]:
        suffix = Path(filename or path).suffix.lower()
        if mime_type in TEXT_MIMES or suffix in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".log"}:
            return self._read_text(path)
        if mime_type == "application/pdf" or suffix == ".pdf":
            return self._pdf(path)
        if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or suffix == ".docx":
            return self._docx(path)
        return None

    def _read_text(self, path: str) -> str:
        data = Path(path).read_bytes()
        return data.decode("utf-8", errors="replace")[: self.max_chars]

    def _pdf(self, path: str) -> Optional[str]:
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            return text[: self.max_chars] or None
        except Exception:
            return None

    def _docx(self, path: str) -> Optional[str]:
        try:
            from docx import Document
            text = "\n".join(p.text for p in Document(path).paragraphs)
            return text[: self.max_chars] or None
        except Exception:
            return None
