from dataclasses import asdict, dataclass
from pathlib import Path
import re
import zipfile

from app.core.config import Settings


class ExtractionError(ValueError):
    """Raised when a file cannot be safely or meaningfully parsed."""


@dataclass
class SourceChunk:
    text: str
    location_type: str
    location_label: str
    page_number: int | None
    slide_number: int | None
    paragraph_number: int | None
    section: str | None
    start_offset: int
    end_offset: int

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _split_chunks(text: str, max_chars: int, overlap: int) -> list[tuple[str, int, int]]:
    text = _clean_text(text)
    if not text:
        return []
    if max_chars < 100 or overlap < 0 or overlap >= max_chars:
        raise ValueError("Chunk size must be at least 100 and overlap must be smaller than the chunk size.")
    output: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end), text.rfind("? ", start, end), text.rfind("! ", start, end))
            if boundary >= start + int(max_chars * 0.55):
                end = boundary + (1 if text[boundary] == "\n" else 1)
        piece = text[start:end].strip()
        if piece:
            left_trim = len(text[start:end]) - len(text[start:end].lstrip())
            right_trim = len(text[start:end]) - len(text[start:end].rstrip())
            output.append((piece, start + left_trim, end - right_trim))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return output


def _make_chunks(units: list[tuple[str, str, int | None, str | None]], settings: Settings) -> list[SourceChunk]:
    """units carry (text, location type, location number, section); offsets follow extraction order."""
    chunks: list[SourceChunk] = []
    document_offset = 0
    for text, kind, number, section in units:
        normalized = _clean_text(text)
        if not normalized:
            continue
        for chunk, start, end in _split_chunks(normalized, settings.chunk_size_chars, settings.chunk_overlap_chars):
            if kind == "page":
                label = f"Page {number}"
                page_number, slide_number, paragraph_number = number, None, None
            elif kind == "slide":
                label = f"Slide {number}"
                page_number, slide_number, paragraph_number = None, number, None
            else:
                label = f"Paragraph {number}"
                page_number, slide_number, paragraph_number = None, None, number
            chunks.append(SourceChunk(
                text=chunk,
                location_type=kind,
                location_label=label,
                page_number=page_number,
                slide_number=slide_number,
                paragraph_number=paragraph_number,
                section=section,
                start_offset=document_offset + start,
                end_offset=document_offset + end,
            ))
        document_offset += len(normalized) + 1
    return chunks


def _extract_pdf(path: Path) -> tuple[list[tuple[str, str, int | None, str | None]], int]:
    try:
        import pymupdf
        with pymupdf.open(path) as document:
            units = [(page.get_text("text"), "page", index + 1, None) for index, page in enumerate(document)]
            return units, len(document)
    except Exception as exc:
        raise ExtractionError("Could not read this PDF. It may be encrypted or damaged.") from exc


def _extract_docx(path: Path) -> tuple[list[tuple[str, str, int | None, str | None]], int]:
    try:
        from docx import Document
        document = Document(path)
        units: list[tuple[str, str, int | None, str | None]] = []
        current_section: str | None = None
        paragraph_number = 0
        for paragraph in document.paragraphs:
            paragraph_number += 1
            style_name = paragraph.style.name if paragraph.style else ""
            if style_name.lower().startswith("heading") and paragraph.text.strip():
                current_section = paragraph.text.strip()
            if paragraph.text.strip():
                units.append((paragraph.text, "paragraph", paragraph_number, current_section))
        for table_index, table in enumerate(document.tables, start=1):
            for row_index, row in enumerate(table.rows, start=1):
                cells = [cell.text.strip().replace("\n", " / ") for cell in row.cells]
                row_text = " | ".join(cell for cell in cells if cell)
                if row_text:
                    paragraph_number += 1
                    units.append((row_text, "paragraph", paragraph_number, f"Table {table_index}, row {row_index}"))
        return units, 0
    except Exception as exc:
        raise ExtractionError("Could not read this Word document. It may be damaged or invalid.") from exc


def _extract_pptx(path: Path) -> tuple[list[tuple[str, str, int | None, str | None]], int]:
    try:
        from pptx import Presentation
        presentation = Presentation(path)
        units: list[tuple[str, str, int | None, str | None]] = []
        for slide_number, slide in enumerate(presentation.slides, start=1):
            parts: list[str] = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text.strip():
                    parts.append(shape.text.strip())
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            units.append(("\n".join(parts), "slide", slide_number, None))
        return units, len(presentation.slides)
    except Exception as exc:
        raise ExtractionError("Could not read this PowerPoint file. It may be damaged or invalid.") from exc


def _extract_plain_text(path: Path) -> tuple[list[tuple[str, str, int | None, str | None]], int]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionError("Text and Markdown files must use UTF-8 encoding.") from exc
    if "\x00" in text:
        raise ExtractionError("This file contains binary data and is not a valid text document.")
    units = [(paragraph, "paragraph", index, None) for index, paragraph in enumerate(re.split(r"\n\s*\n", text), start=1)]
    return units, 0


def extract_document(path: Path, extension: str, settings: Settings) -> tuple[list[SourceChunk], int]:
    if extension == ".pdf":
        units, page_count = _extract_pdf(path)
    elif extension == ".docx":
        if not zipfile.is_zipfile(path):
            raise ExtractionError("This file is not a valid DOCX package.")
        units, page_count = _extract_docx(path)
    elif extension == ".pptx":
        if not zipfile.is_zipfile(path):
            raise ExtractionError("This file is not a valid PPTX package.")
        units, page_count = _extract_pptx(path)
    elif extension in {".txt", ".md", ".markdown"}:
        units, page_count = _extract_plain_text(path)
    else:
        raise ExtractionError("This file type is not supported.")
    chunks = _make_chunks(units, settings)
    if not any(chunk.text.strip() for chunk in chunks):
        raise ExtractionError("No text could be extracted. Scanned PDFs require OCR, which is not enabled yet.")
    return chunks, page_count
