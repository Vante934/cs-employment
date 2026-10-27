"""Knowledge base file parser and text extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.config.paths import get_runtime_subdir
from nanobot.kb.store import KnowledgeBaseStore
from nanobot.utils.document import extract_text


@dataclass
class ParseResult:
    """Result of file parsing operation."""
    
    success: bool
    text: str | None
    error: str | None
    page_count: int | None
    word_count: int | None
    metadata: dict[str, Any] | None = None


def parse_file(
    file_id: str,
    kb_store: KnowledgeBaseStore,
    force: bool = False
) -> ParseResult:
    """Parse a knowledge base file and extract text.
    
    Args:
        file_id: File ID in kb_files table
        kb_store: Knowledge base store instance
        force: Force re-parsing even if already parsed
    
    Returns:
        ParseResult with success status, extracted text, and metadata
    """
    # 1. Get file record
    record = kb_store.get_file(file_id)
    if not record:
        return ParseResult(
            success=False,
            text=None,
            error=f"File not found: {file_id}",
            page_count=None,
            word_count=None
        )
    
    # 2. Check if already parsed
    status = record.get("status")
    if status in ("parsed", "parsing") and not force:
        return ParseResult(
            success=True,
            text=None,
            error=None,
            page_count=record.get("page_count"),
            word_count=record.get("word_count"),
            metadata={"skipped": True, "reason": "already_parsed"}
        )
    
    # 3. Validate file path
    file_path_str = record.get("file_path")
    if not file_path_str:
        return ParseResult(
            success=False,
            text=None,
            error="File path not found in record",
            page_count=None,
            word_count=None
        )
    
    file_path = Path(file_path_str)
    
    # 4. Security validation
    validation_error = _validate_file_path(file_path)
    if validation_error:
        return ParseResult(
            success=False,
            text=None,
            error=validation_error,
            page_count=None,
            word_count=None
        )
    
    # 5. Extract text using existing framework
    try:
        extracted = extract_text(file_path)
    except Exception as e:
        return ParseResult(
            success=False,
            text=None,
            error=f"Extraction exception: {e}",
            page_count=None,
            word_count=None
        )
    
    # 6. Process extraction result
    if extracted is None:
        # Unsupported format
        return ParseResult(
            success=False,
            text=None,
            error="Unsupported file format",
            page_count=None,
            word_count=None
        )
    
    if isinstance(extracted, str) and extracted.startswith("[error:"):
        # Extraction failed
        return ParseResult(
            success=False,
            text=None,
            error=extracted,
            page_count=None,
            word_count=None
        )
    
    # 7. Success - calculate metadata
    text = extracted if isinstance(extracted, str) else ""
    word_count = len(text)
    
    # Try to get page count for PDF
    page_count = None
    file_type = record.get("file_type", "").lower()
    if file_type == "pdf":
        page_count = _extract_pdf_page_count(file_path)
    
    return ParseResult(
        success=True,
        text=text,
        error=None,
        page_count=page_count,
        word_count=word_count
    )


def _validate_file_path(file_path: Path) -> str | None:
    """Validate file path for security and existence.
    
    Returns:
        Error message if invalid, None if valid
    """
    # Check existence
    if not file_path.exists():
        return f"File does not exist: {file_path}"
    
    # Must be a regular file
    if not file_path.is_file():
        return f"Path is not a regular file: {file_path}"
    
    # Check for symlinks
    if file_path.is_symlink():
        return "Symbolic links are not allowed"
    
    # Validate within KB directory
    try:
        kb_files_dir = get_runtime_subdir("kb") / "files"
        file_path.resolve().relative_to(kb_files_dir.resolve())
    except ValueError:
        return f"File path outside KB directory: {file_path}"
    
    return None


def _extract_pdf_page_count(file_path: Path) -> int | None:
    """Extract page count from PDF file.
    
    Returns:
        Page count or None if unavailable
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path, strict=False)
        return len(reader.pages)
    except Exception:
        return None
