"""Knowledge base file upload tool."""

import hashlib
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.kb.store import KnowledgeBaseStore


# Supported file extensions
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}

# Default max file size: 50 MiB
DEFAULT_MAX_FILE_SIZE_MB = 50


class KBUploadTool(Tool):
    """Upload a file to the knowledge base."""
    
    config_key = "kb"
    
    def __init__(
        self,
        kb_store: KnowledgeBaseStore,
        media_dir: Path,
        kb_files_dir: Path,
        max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB,
    ):
        self._kb_store = kb_store
        self._media_dir = media_dir.resolve()
        self._kb_files_dir = kb_files_dir
        self._max_file_size_bytes = max_file_size_mb * 1024 * 1024
    
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        from nanobot.config.paths import get_media_dir, get_runtime_subdir
        from nanobot.session.manager import SessionManager
        
        # Get KB store from session manager
        sessions: SessionManager = ctx.sessions
        kb_store = KnowledgeBaseStore(sessions._store)
        
        # Media directory (where feishu downloads files)
        media_dir = get_media_dir("feishu")
        
        # KB files directory
        kb_files_dir = get_runtime_subdir("kb") / "files"
        kb_files_dir.mkdir(parents=True, exist_ok=True)
        
        return cls(
            kb_store=kb_store,
            media_dir=media_dir,
            kb_files_dir=kb_files_dir,
        )
    
    @property
    def name(self) -> str:
        return "kb_upload"
    
    @property
    def description(self) -> str:
        return (
            "Upload a file to the knowledge base for future retrieval. "
            "Extract the file path from [file: ...] markers in user messages. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}. "
            f"Max size: {self._max_file_size_bytes // (1024 * 1024)} MB."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            file_path=StringSchema(
                description="Absolute path to the file (from [file: ...] marker in user message)"
            ),
        )
    
    def _validate_path(self, file_path: str) -> tuple[Path, str | None]:
        """Validate file path for security and existence.
        
        Returns:
            (resolved_path, error_message)
            If error_message is not None, path is invalid.
        """
        try:
            path = Path(file_path)
        except (ValueError, OSError) as e:
            return None, f"Invalid path: {e}"
        
        # Resolve to absolute path
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as e:
            return None, f"File not found or inaccessible: {e}"
        
        # Must be a regular file (not directory, symlink, etc)
        if not resolved.is_file():
            return None, "Path must be a regular file"
        
        # Check if it's a symlink (even after resolve)
        if path.is_symlink():
            return None, "Symbolic links are not allowed"
        
        # Must be within trusted media directory
        try:
            resolved.relative_to(self._media_dir)
        except ValueError:
            return None, f"File must be within media directory: {self._media_dir}"
        
        return resolved, None
    
    def _validate_file_type(self, path: Path) -> str | None:
        """Validate file extension.
        
        Returns:
            Error message if invalid, None if valid.
        """
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return (
                f"Unsupported file type: {ext}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        return None
    
    def _validate_file_size(self, path: Path) -> str | None:
        """Validate file size.
        
        Returns:
            Error message if too large, None if valid.
        """
        size = path.stat().st_size
        if size > self._max_file_size_bytes:
            max_mb = self._max_file_size_bytes // (1024 * 1024)
            actual_mb = size / (1024 * 1024)
            return f"File too large: {actual_mb:.1f} MB (max: {max_mb} MB)"
        return None
    
    def _compute_hash(self, path: Path) -> str:
        """Compute SHA256 hash of file."""
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def _copy_to_kb_storage(self, source: Path, file_hash: str) -> Path:
        """Copy file to KB storage directory.
        
        Uses hash as filename to avoid conflicts and duplicates.
        """
        ext = source.suffix.lower()
        dest = self._kb_files_dir / f"{file_hash}{ext}"
        
        # If file already exists with same hash, no need to copy
        if dest.exists():
            return dest
        
        # Copy file
        import shutil
        shutil.copy2(source, dest)
        return dest
    
    async def execute(self, file_path: str, **context) -> str | ToolResult:
        """Execute file upload to knowledge base."""
        
        # 1. Validate path
        resolved, error = self._validate_path(file_path)
        if error:
            return ToolResult.error(error)
        
        # 2. Validate file type
        error = self._validate_file_type(resolved)
        if error:
            return ToolResult.error(error)
        
        # 3. Validate file size
        error = self._validate_file_size(resolved)
        if error:
            return ToolResult.error(error)
        
        # 4. Compute file hash
        try:
            file_hash = self._compute_hash(resolved)
        except OSError as e:
            return ToolResult.error(f"Failed to read file: {e}")
        
        # 5. Check for duplicates
        if self._kb_store.file_exists(file_hash):
            existing = self._kb_store.get_file_by_hash(file_hash)
            return (
                f"File already exists in knowledge base: {existing['filename']} "
                f"(ID: {existing['file_id']}, uploaded: {existing['created_at']})"
            )
        
        # 6. Copy to KB storage
        try:
            kb_file_path = self._copy_to_kb_storage(resolved, file_hash)
        except OSError as e:
            return ToolResult.error(f"Failed to copy file to storage: {e}")
        
        # 7. Create database record
        try:
            file_id = self._kb_store.create_file_record(
                filename=resolved.name,
                file_type=resolved.suffix[1:].lower(),  # Remove leading '.'
                file_size=resolved.stat().st_size,
                file_path=str(kb_file_path),
                file_hash=file_hash,
                status="pending",
            )
        except Exception as e:
            # Rollback: delete copied file if DB insert fails
            try:
                kb_file_path.unlink(missing_ok=True)
            except:
                pass
            return ToolResult.error(f"Failed to register file in database: {e}")
        
        return (
            f"File uploaded successfully!\n"
            f"Filename: {resolved.name}\n"
            f"Size: {resolved.stat().st_size / 1024:.1f} KB\n"
            f"Type: {resolved.suffix[1:].upper()}\n"
            f"ID: {file_id}\n"
            f"Status: pending (will be processed shortly)"
        )
