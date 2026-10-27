"""Knowledge base data access layer."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4

from nanobot.session.sqlite_store import SqliteSessionStore


class KnowledgeBaseStore:
    """Knowledge base storage using SqliteSessionStore's connection and transaction."""
    
    def __init__(self, sqlite_store: SqliteSessionStore):
        self._store = sqlite_store
    
    def create_file_record(
        self,
        filename: str,
        file_type: str,
        file_size: int,
        file_path: str,
        file_hash: str,
        status: str = "pending",
        **kwargs: Any
    ) -> str:
        """Create a file record and return file_id."""
        file_id = str(uuid4())
        now = datetime.now().isoformat()
        
        def _insert() -> str:
            with self._store.transaction() as db:
                db.execute(
                    """INSERT INTO kb_files (
                        file_id, filename, file_type, file_size, file_path, file_hash,
                        status, created_at, updated_at,
                        feishu_message_id, feishu_file_key, sender_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        file_id, filename, file_type, file_size, file_path, file_hash,
                        status, now, now,
                        kwargs.get("feishu_message_id"),
                        kwargs.get("feishu_file_key"),
                        kwargs.get("sender_id"),
                    )
                )
            return file_id
        
        return self._store.run_write(_insert)
    
    def get_file(self, file_id: str) -> dict[str, Any] | None:
        """Get a file record by file_id."""
        def _select() -> dict[str, Any] | None:
            with self._store.transaction(write=False) as db:
                cursor = db.execute(
                    "SELECT * FROM kb_files WHERE file_id = ?",
                    (file_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        
        return self._store.run_read(_select)
    
    def get_file_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        """Get a file record by file_hash."""
        def _select() -> dict[str, Any] | None:
            with self._store.transaction(write=False) as db:
                cursor = db.execute(
                    "SELECT * FROM kb_files WHERE file_hash = ?",
                    (file_hash,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        
        return self._store.run_read(_select)
    
    def file_exists(self, file_hash: str) -> bool:
        """Check if a file with the given hash exists."""
        def _check() -> bool:
            with self._store.transaction(write=False) as db:
                cursor = db.execute(
                    "SELECT 1 FROM kb_files WHERE file_hash = ? LIMIT 1",
                    (file_hash,)
                )
                return cursor.fetchone() is not None
        
        return self._store.run_read(_check)
    
    def update_file_status(
        self,
        file_id: str,
        status: str,
        error_message: str | None = None,
        **kwargs: Any
    ) -> None:
        """Update file status and optional fields."""
        now = datetime.now().isoformat()
        
        def _update() -> None:
            with self._store.transaction() as db:
                set_clauses = ["status = ?", "updated_at = ?"]
                params: list[Any] = [status, now]
                
                if error_message is not None:
                    set_clauses.append("error_message = ?")
                    params.append(error_message)
                
                for key in ("summary", "keywords", "page_count", "word_count", "indexed_at"):
                    if key in kwargs:
                        set_clauses.append(f"{key} = ?")
                        params.append(kwargs[key])
                
                params.append(file_id)
                
                db.execute(
                    f"UPDATE kb_files SET {', '.join(set_clauses)} WHERE file_id = ?",
                    params
                )
        
        self._store.run_write(_update)
    
    def list_files(self, status: str | None = None) -> list[dict[str, Any]]:
        """List all files, optionally filtered by status."""
        def _select() -> list[dict[str, Any]]:
            with self._store.transaction(write=False) as db:
                if status:
                    cursor = db.execute(
                        "SELECT * FROM kb_files WHERE status = ? ORDER BY created_at DESC",
                        (status,)
                    )
                else:
                    cursor = db.execute(
                        "SELECT * FROM kb_files ORDER BY created_at DESC"
                    )
                return [dict(row) for row in cursor.fetchall()]
        
        return self._store.run_read(_select)
    
    def delete_file(self, file_id: str) -> None:
        """Delete a file record (cascades to chunks).
        
        Note: This only deletes database records. If using vector index,
        caller must also call IndexManager.delete_file_index() to clean up vectors.
        """
        def _delete() -> None:
            with self._store.transaction() as db:
                db.execute("DELETE FROM kb_files WHERE file_id = ?", (file_id,))
        
        self._store.run_write(_delete)
    
    def save_parsed_result(
        self,
        file_id: str,
        text: str,
        page_count: int | None = None,
        word_count: int | None = None
    ) -> None:
        """Save parsed text and update file status atomically.
        
        Stores full text as chunk_index=0 (reserved for complete extracted text).
        Updates file status to 'parsed' in the same transaction.
        
        Args:
            file_id: File ID
            text: Extracted text
            page_count: Number of pages (PDF/DOCX)
            word_count: Character count
        """
        now = datetime.now().isoformat()
        chunk_id = f"{file_id}_full"
        
        def _save() -> None:
            with self._store.transaction() as db:
                # 1. Delete existing full-text chunk if exists (for force re-parse)
                db.execute(
                    "DELETE FROM kb_chunks WHERE file_id = ? AND chunk_index = 0",
                    (file_id,)
                )
                
                # 2. Insert new full-text chunk
                db.execute(
                    """INSERT INTO kb_chunks (
                        chunk_id, file_id, chunk_text, chunk_index, created_at
                    ) VALUES (?, ?, ?, 0, ?)""",
                    (chunk_id, file_id, text, now)
                )
                
                # 3. Update file status and metadata
                set_clauses = ["status = ?", "updated_at = ?"]
                params: list[Any] = ["parsed", now]
                
                if page_count is not None:
                    set_clauses.append("page_count = ?")
                    params.append(page_count)
                
                if word_count is not None:
                    set_clauses.append("word_count = ?")
                    params.append(word_count)
                
                # Clear error_message on success
                set_clauses.append("error_message = NULL")
                
                params.append(file_id)
                
                db.execute(
                    f"UPDATE kb_files SET {', '.join(set_clauses)} WHERE file_id = ?",
                    params
                )
        
        self._store.run_write(_save)
    
    def get_parsed_text(self, file_id: str) -> str | None:
        """Get parsed full text for a file.
        
        Args:
            file_id: File ID
        
        Returns:
            Parsed text or None if not found
        """
        def _select() -> str | None:
            with self._store.transaction(write=False) as db:
                cursor = db.execute(
                    "SELECT chunk_text FROM kb_chunks WHERE file_id = ? AND chunk_index = 0",
                    (file_id,)
                )
                row = cursor.fetchone()
                return row[0] if row else None
        
        return self._store.run_read(_select)
    
    def count_chunks(self, file_id: str, start_index: int = 1) -> int:
        """Count chunks for a file starting from given index.
        
        Args:
            file_id: File ID
            start_index: Starting chunk index (default 1, excluding full text at 0)
        
        Returns:
            Number of chunks
        """
        def _count() -> int:
            with self._store.transaction(write=False) as db:
                cursor = db.execute(
                    "SELECT COUNT(*) FROM kb_chunks WHERE file_id = ? AND chunk_index >= ?",
                    (file_id, start_index)
                )
                return cursor.fetchone()[0]
        
        return self._store.run_read(_count)
    
    def save_chunks(
        self,
        file_id: str,
        chunks: list[str],
        replace_existing: bool = False
    ) -> None:
        """Save text chunks to database.
        
        Chunks are stored starting at chunk_index=1.
        chunk_index=0 is reserved for full text and will NOT be deleted.
        
        Args:
            file_id: File ID
            chunks: List of text chunks
            replace_existing: If True, delete old chunks before inserting new ones
        """
        now = datetime.now().isoformat()
        
        def _save() -> None:
            with self._store.transaction() as db:
                if replace_existing:
                    # Delete old chunks (chunk_index >= 1 only, preserve full text at 0)
                    db.execute(
                        "DELETE FROM kb_chunks WHERE file_id = ? AND chunk_index >= 1",
                        (file_id,)
                    )
                
                # Insert new chunks starting at index 1
                for idx, chunk_text in enumerate(chunks, start=1):
                    chunk_id = f"{file_id}_chunk_{idx}"
                    db.execute(
                        """INSERT INTO kb_chunks (
                            chunk_id, file_id, chunk_text, chunk_index,
                            source_page, source_section, embedding_model, vector_index_id,
                            created_at
                        ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)""",
                        (chunk_id, file_id, chunk_text, idx, now)
                    )
        
        self._store.run_write(_save)
    
    def get_chunks(self, file_id: str, start_index: int = 1) -> list[dict[str, Any]]:
        """Get chunks for a file.
        
        Args:
            file_id: File ID
            start_index: Starting chunk index (default 1, excluding full text at 0)
        
        Returns:
            List of chunk records
        """
        def _select() -> list[dict[str, Any]]:
            with self._store.transaction(write=False) as db:
                cursor = db.execute(
                    """SELECT chunk_id, chunk_text, chunk_index, source_page, source_section,
                       embedding_model, vector_index_id, created_at
                       FROM kb_chunks
                       WHERE file_id = ? AND chunk_index >= ?
                       ORDER BY chunk_index""",
                    (file_id, start_index)
                )
                return [dict(row) for row in cursor.fetchall()]
        
        return self._store.run_read(_select)
    
    def update_chunk_embedding(
        self,
        chunk_id: str,
        embedding_model: str,
        vector_index_id: int
    ) -> None:
        """Update chunk with embedding metadata.
        
        Args:
            chunk_id: Chunk ID
            embedding_model: Model name used for embedding
            vector_index_id: FAISS internal vector ID
        """
        def _update() -> None:
            with self._store.transaction() as db:
                db.execute(
                    """UPDATE kb_chunks
                       SET embedding_model = ?, vector_index_id = ?
                       WHERE chunk_id = ?""",
                    (embedding_model, vector_index_id, chunk_id)
                )
        
        self._store.run_write(_update)
    
    def clear_chunk_embeddings(self, file_id: str) -> None:
        """Clear embedding metadata for all chunks of a file.
        
        Used when rebuilding index or when file is re-chunked.
        
        Args:
            file_id: File ID
        """
        def _clear() -> None:
            with self._store.transaction() as db:
                db.execute(
                    """UPDATE kb_chunks
                       SET embedding_model = NULL, vector_index_id = NULL
                       WHERE file_id = ? AND chunk_index >= 1""",
                    (file_id,)
                )
        
        self._store.run_write(_clear)
    
    def clear_chunk_embedding(self, chunk_id: str) -> None:
        """Clear embedding metadata for one chunk."""
        def _clear() -> None:
            with self._store.transaction() as db:
                db.execute(
                    """UPDATE kb_chunks
                       SET embedding_model = NULL, vector_index_id = NULL
                       WHERE chunk_id = ?""",
                    (chunk_id,)
                )
        
        self._store.run_write(_clear)
    
    def get_all_indexed_chunks(self, embedding_model: str | None = None) -> list[dict[str, Any]]:
        """Get all chunks with embeddings.
        
        Args:
            embedding_model: Filter by model name (None = all indexed chunks)
        
        Returns:
            List of chunk records
        """
        def _select() -> list[dict[str, Any]]:
            with self._store.transaction(write=False) as db:
                if embedding_model:
                    cursor = db.execute(
                        """SELECT chunk_id, file_id, chunk_text, chunk_index,
                           embedding_model, vector_index_id
                           FROM kb_chunks
                           WHERE chunk_index >= 1 AND embedding_model = ?
                           ORDER BY file_id, chunk_index""",
                        (embedding_model,)
                    )
                else:
                    cursor = db.execute(
                        """SELECT chunk_id, file_id, chunk_text, chunk_index,
                           embedding_model, vector_index_id
                           FROM kb_chunks
                           WHERE chunk_index >= 1 AND embedding_model IS NOT NULL
                           ORDER BY file_id, chunk_index"""
                    )
                return [dict(row) for row in cursor.fetchall()]
        
        return self._store.run_read(_select)
