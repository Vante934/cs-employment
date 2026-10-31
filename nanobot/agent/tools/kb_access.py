"""KB file access tools for Agent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import current_request_context
from nanobot.kb.store import KnowledgeBaseStore

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.session.manager import SessionManager


class KBSearchTool(Tool):
    """Search the uploaded file for relevant content."""
    
    def __init__(self, sessions: SessionManager):
        self._sessions = sessions
    
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise ValueError("SessionManager is required for KBSearchTool")
        return cls(sessions=ctx.sessions)
    
    @property
    def name(self) -> str:
        return "kb_search"
    
    @property
    def description(self) -> str:
        return "Search the uploaded file for relevant content based on a query"
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant content in the file"
                }
            },
            "required": ["query"]
        }
    
    @property
    def read_only(self) -> bool:
        return True
    
    async def execute(self, query: str, **kwargs: Any) -> str:
        """Execute kb_search."""
        # Validate query
        if not query or not query.strip():
            return ToolResult.error("Error: Search query cannot be empty")
        
        # Get and validate context
        validation_result = await self._validate_file_access()
        if isinstance(validation_result, ToolResult):
            return validation_result
        
        file_id, file_record = validation_result
        
        # Search within the current file
        try:
            from nanobot.kb.index_manager import IndexManager
            
            kb_store = KnowledgeBaseStore(self._sessions.get_sqlite_store())
            index_manager = IndexManager(kb_store)
            
            # Search with file_id scope
            response = index_manager.search(
                query=query,
                top_k=5,
                file_id=file_id
            )
            
            if not response.success:
                error_msg = response.error_message or "Search failed"
                return ToolResult.error(f"Error: {error_msg}")
            
            if not response.results:
                return "No relevant content found in the file for your query."
            
            # Format results (simplified to avoid exposing internal details)
            output_lines = []
            for i, result in enumerate(response.results, 1):
                output_lines.append(result.chunk_text)
                if result.source_page:
                    output_lines.append(f"(Page {result.source_page})")
                if i < len(response.results):
                    output_lines.append("")  # blank line between results
            
            return "\n".join(output_lines)
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("KB search failed")
            return ToolResult.error("Error: Failed to search the file")
    
    async def _validate_file_access(self) -> tuple[str, dict[str, Any]] | ToolResult:
        """Validate file access and return (file_id, file_record) or error.
        
        Returns:
            tuple[str, dict]: (file_id, file_record) if validation succeeds
            ToolResult: Error message if validation fails
        """
        # 1. Get RequestContext
        request = current_request_context()
        if request is None:
            return ToolResult.error("Error: Session context is unavailable")
        
        # 2. Check session_key
        if not request.session_key:
            return ToolResult.error("Error: No session available")
        
        # 3. Check sender_id
        if not request.sender_id:
            return ToolResult.error("Error: User identity is unavailable")
        
        # 4. Get Session
        try:
            session = await self._sessions.state.get(request.session_key)
            if not session:
                return ToolResult.error("Error: Session not found")
        except Exception:
            return ToolResult.error("Error: Failed to access session")
        
        # 5. Get pending_file
        pending_file = session.metadata.get("pending_file")
        if not pending_file or not isinstance(pending_file, dict):
            return ToolResult.error("Error: No file has been uploaded. Please upload a file first.")
        
        # 6. Check file_id
        file_id = pending_file.get("file_id")
        if not file_id:
            return ToolResult.error("Error: Invalid file information")
        
        # 7. Verify pending_file.sender_id
        pending_sender = pending_file.get("sender_id")
        if not pending_sender or pending_sender != request.sender_id:
            return ToolResult.error("Error: Permission denied")
        
        # 8. Get KB file record
        try:
            kb_store = KnowledgeBaseStore(self._sessions.get_sqlite_store())
            file_record = kb_store.get_file(file_id)
        except Exception:
            return ToolResult.error("Error: Failed to access knowledge base")
        
        if not file_record:
            return ToolResult.error("Error: File not found")
        
        # 9. Verify file_record.sender_id
        file_sender = file_record.get("sender_id")
        if file_sender and file_sender != request.sender_id:
            return ToolResult.error("Error: Permission denied")
        
        # 10. Check file status
        file_status = file_record.get("status", "unknown")
        if file_status == "failed":
            return ToolResult.error("Error: File processing failed")
        if file_status == "pending":
            return ToolResult.error("The file is still being processed. Please wait and try again.")
        if file_status != "indexed":
            return ToolResult.error(f"Error: File is not ready (status: {file_status})")
        
        return file_id, file_record


class KBReadTool(Tool):
    """Read the uploaded file content with pagination support."""
    
    def __init__(self, sessions: SessionManager):
        self._sessions = sessions
    
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise ValueError("SessionManager is required for KBReadTool")
        return cls(sessions=ctx.sessions)
    
    @property
    def name(self) -> str:
        return "kb_read"
    
    @property
    def description(self) -> str:
        return "Read the uploaded file content. Use part_index to read different parts of large files."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "part_index": {
                    "type": "integer",
                    "description": "Part index to read (0 for first part, 1 for second, etc.)",
                    "default": 0
                }
            }
        }
    
    @property
    def read_only(self) -> bool:
        return True
    
    async def execute(self, part_index: int = 0, **kwargs: Any) -> str:
        """Execute kb_read."""
        # Validate part_index
        if part_index < 0:
            return ToolResult.error("Error: part_index must be non-negative")
        
        # Get and validate context
        validation_result = await self._validate_file_access()
        if isinstance(validation_result, ToolResult):
            return validation_result
        
        file_id, file_record = validation_result
        
        # Read full text from chunk_index=0
        try:
            from nanobot.kb.store import KnowledgeBaseStore
            
            kb_store = KnowledgeBaseStore(self._sessions.get_sqlite_store())
            
            # Get chunk_index=0 (full text)
            chunks = kb_store.get_chunks(file_id, start_index=0)
            
            if not chunks:
                return ToolResult.error("Error: File content not found")
            
            full_text_chunk = None
            for chunk in chunks:
                if chunk.get("chunk_index") == 0:
                    full_text_chunk = chunk
                    break
            
            if not full_text_chunk:
                return ToolResult.error("Error: Full text not available")
            
            full_text = full_text_chunk.get("chunk_text", "")
            if not full_text:
                return "The file is empty."
            
            # Calculate pagination
            PART_SIZE = 10000
            total_parts = (len(full_text) + PART_SIZE - 1) // PART_SIZE
            
            # Validate part_index
            if part_index >= total_parts:
                return ToolResult.error(
                    f"Error: part_index {part_index} out of range. "
                    f"File has {total_parts} part(s) (0-{total_parts-1})"
                )
            
            # Extract part
            start_pos = part_index * PART_SIZE
            end_pos = min(start_pos + PART_SIZE, len(full_text))
            part_text = full_text[start_pos:end_pos]
            
            # Format result (simplified to avoid exposing internal details)
            filename = file_record.get("filename", "document")
            
            # Only show pagination info if there are multiple parts
            if total_parts > 1:
                header = f"Content from {filename} (Part {part_index + 1} of {total_parts}):"
            else:
                header = f"Content from {filename}:"
            
            result_lines = [header, "", part_text]
            
            # Only suggest next part if not the last part
            if part_index < total_parts - 1:
                result_lines.append("")
                result_lines.append(f"(There are more parts. Use part_index={part_index + 1} to continue reading.)")
            
            return "\n".join(result_lines)
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("KB read failed")
            return ToolResult.error("Error: Failed to read the file")
    
    async def _validate_file_access(self) -> tuple[str, dict[str, Any]] | ToolResult:
        """Validate file access and return (file_id, file_record) or error.
        
        Returns:
            tuple[str, dict]: (file_id, file_record) if validation succeeds
            ToolResult: Error message if validation fails
        """
        # 1. Get RequestContext
        request = current_request_context()
        if request is None:
            return ToolResult.error("Error: Session context is unavailable")
        
        # 2. Check session_key
        if not request.session_key:
            return ToolResult.error("Error: No session available")
        
        # 3. Check sender_id
        if not request.sender_id:
            return ToolResult.error("Error: User identity is unavailable")
        
        # 4. Get Session
        try:
            session = await self._sessions.state.get(request.session_key)
            if not session:
                return ToolResult.error("Error: Session not found")
        except Exception:
            return ToolResult.error("Error: Failed to access session")
        
        # 5. Get pending_file
        pending_file = session.metadata.get("pending_file")
        if not pending_file or not isinstance(pending_file, dict):
            return ToolResult.error("Error: No file has been uploaded. Please upload a file first.")
        
        # 6. Check file_id
        file_id = pending_file.get("file_id")
        if not file_id:
            return ToolResult.error("Error: Invalid file information")
        
        # 7. Verify pending_file.sender_id
        pending_sender = pending_file.get("sender_id")
        if not pending_sender or pending_sender != request.sender_id:
            return ToolResult.error("Error: Permission denied")
        
        # 8. Get KB file record
        try:
            kb_store = KnowledgeBaseStore(self._sessions.get_sqlite_store())
            file_record = kb_store.get_file(file_id)
        except Exception:
            return ToolResult.error("Error: Failed to access knowledge base")
        
        if not file_record:
            return ToolResult.error("Error: File not found")
        
        # 9. Verify file_record.sender_id
        file_sender = file_record.get("sender_id")
        if file_sender and file_sender != request.sender_id:
            return ToolResult.error("Error: Permission denied")
        
        # 10. Check file status
        file_status = file_record.get("status", "unknown")
        if file_status == "failed":
            return ToolResult.error("Error: File processing failed")
        if file_status == "pending":
            return ToolResult.error("The file is still being processed. Please wait and try again.")
        if file_status != "indexed":
            return ToolResult.error(f"Error: File is not ready (status: {file_status})")
        
        return file_id, file_record
