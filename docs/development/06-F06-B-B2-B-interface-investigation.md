# F06-B-B2-B：KB文件工具 - 接口调查报告

**调查日期**: 2026-10-30  
**状态**: ✅ 调查完成  

---

## 执行摘要

### 关键发现

1. ❌ **IndexManager.search()不支持file_id过滤** - 需要手动过滤
2. ❌ **get_parsed_text()不支持分块** - 返回完整文本
3. ✅ **Tool可获取RequestContext** - 通过current_request_context()
4. ✅ **KB文件有sender_id字段** - 可用于权限验证
5. ✅ **ToolResult.error()处理错误** - 返回友好消息
6. ✅ **自动发现多个Tool类** - 同一模块可定义多个
7. ✅ **SessionManager可通过ToolContext获取** - ctx.sessions

### 需要调整的实现方案

基于调查结果，原设计方案需要以下调整：

1. **kb_search**: 需要手动过滤file_id
2. **kb_read**: 需要实现分块逻辑（get_parsed_text返回完整文本）
3. **权限验证**: 可以使用file_record.sender_id验证

---

## 调查1：IndexManager.search()接口

### 1.1 方法签名

**文件**: `nanobot/kb/index_manager.py:535-540`

```python
def search(
    self,
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.0
) -> SearchResponse:
```

### 1.2 参数

- `query`: 查询文本（最多10000字符）
- `top_k`: 返回结果数量（1-100，默认5）
- `min_similarity`: 最小相似度阈值（-1.0到1.0，默认0.0）

### 1.3 关键发现

❌ **不支持file_id参数**

**搜索流程**:
1. 嵌入查询
2. 向量检索（全局）
3. 批量获取chunks（从所有文件）
4. 返回结果

**结论**: **需要手动过滤结果中的file_id**

### 1.4 SearchResult结构

**文件**: `nanobot/kb/index_manager.py:30-45`

```python
@dataclass
class SearchResult:
    # Chunk information
    chunk_id: str
    chunk_text: str
    chunk_index: int
    similarity: float  # [-1.0, 1.0]
    
    # File metadata
    file_id: str
    filename: str
    file_type: str
    
    # Optional source
    source_page: int | None = None
    source_section: str | None = None
```

**可用字段**: ✅ chunk_text, similarity, file_id, chunk_index, source_page

---

## 调查2：get_parsed_text()接口

### 2.1 方法签名

**文件**: `nanobot/kb/store.py:214-232`

```python
def get_parsed_text(self, file_id: str) -> str | None:
    """Get parsed full text for a file."""
```

### 2.2 实现

```python
def _select() -> str | None:
    with self._store.transaction(write=False) as db:
        cursor = db.execute(
            "SELECT chunk_text FROM kb_chunks WHERE file_id = ? AND chunk_index = 0",
            (file_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
```

### 2.3 关键发现

❌ **仅返回chunk_index=0的文本**

**问题**: 
- chunk_index=0存储的是什么？完整文本还是第一个chunk？
- 如果是完整文本，可能非常大
- 如果是第一个chunk，则不完整

**需要验证**: chunk_index=0的实际内容

**结论**: **需要自己实现分块读取逻辑**

---

## 调查3：KB文件schema

### 3.1 kb_files表结构

**文件**: `nanobot/session/sqlite_store.py:80-100`

```sql
CREATE TABLE IF NOT EXISTS kb_files (
    file_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    feishu_message_id TEXT,
    feishu_file_key TEXT,
    sender_id TEXT,  -- ← 有此字段！
    status TEXT NOT NULL,
    error_message TEXT,
    summary TEXT,
    keywords TEXT,
    page_count INTEGER,
    word_count INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    indexed_at TEXT,
    UNIQUE(file_hash)
)
```

### 3.2 关键发现

✅ **kb_files表有sender_id字段**

**用途**: 文件归属验证

**验证流程**:
```python
file_record = kb_store.get_file(file_id)
if file_record["sender_id"] != request.sender_id:
    return ToolResult.error("Permission denied")
```

---

## 调查4：Tool获取RequestContext

### 4.1 current_request_context()

**文件**: `nanobot/agent/tools/context.py:76-77`

```python
def current_request_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()
```

### 4.2 使用示例

**文件**: `nanobot/agent/tools/session_messages.py:188-192`

```python
async def execute(self, ...):
    request = current_request_context()
    if request is None or not request.session_key:
        return ToolResult.error("Error: session context is unavailable")
    # ... use request.session_key, request.sender_id
```

### 4.3 RequestContext内容

```python
@dataclass(frozen=True)
class RequestContext:
    channel: str
    chat_id: str
    message_id: str | None = None
    session_key: str | None = None
    sender_id: str | None = None  # ← 可用
    metadata: dict[str, Any]
    # ...
```

### 4.4 关键发现

✅ **Tool可以通过current_request_context()获取**

**可用信息**:
- `request.session_key` - 用于读取Session
- `request.sender_id` - 用于权限验证

---

## 调查5：Tool获取SessionManager

### 5.1 ToolContext

**文件**: `nanobot/agent/tools/context.py:92-102`

```python
@dataclass
class ToolContext:
    config: ToolsConfig
    workspace: str
    sessions: SessionManager | None = None  # ← 可用
    # ...
```

### 5.2 Tool.create()

**文件**: `nanobot/agent/tools/base.py:275-277`

```python
@classmethod
def create(cls, ctx: ToolContext) -> Tool:
    return cls()
```

### 5.3 关键发现

✅ **SessionManager通过ToolContext传入**

**获取方式**:
```python
class KBSearchTool(Tool):
    def __init__(self, sessions: SessionManager):
        self._sessions = sessions
    
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(sessions=ctx.sessions)
```

---

## 调查6：ToolResult错误处理

### 6.1 ToolResult定义

**文件**: `nanobot/agent/tools/base.py:199-212`

```python
class ToolResult(str):
    """String-compatible tool output with structured status."""
    
    is_error: bool
    
    def __new__(cls, content: str, *, is_error: bool = False):
        obj = str.__new__(cls, content)
        obj.is_error = is_error
        return obj
    
    @classmethod
    def error(cls, content: str) -> ToolResult:
        return cls(content, is_error=True)
```

### 6.2 使用示例

**文件**: `nanobot/agent/tools/session_messages.py:191`

```python
if request is None or not request.session_key:
    return ToolResult.error("Error: session context is unavailable")
```

### 6.3 关键发现

✅ **使用ToolResult.error(message)返回友好错误**

**特点**:
- 继承自str，可直接作为字符串
- is_error标记错误状态
- Agent能看到错误消息

---

## 调查7：Tool自动发现

### 7.1 发现机制

**文件**: `nanobot/agent/tools/loader.py:52-64`

```python
for attr_name in dir(module):
    attr = getattr(module, attr_name)
    if (
        isinstance(attr, type)
        and issubclass(attr, Tool)
        and attr is not Tool
        and not attr_name.startswith("_")
        and not getattr(attr, "__abstractmethods__", None)
        and getattr(attr, "_plugin_discoverable", True)
        and id(attr) not in seen
    ):
        results.append(attr)
```

### 7.2 关键发现

✅ **同一模块中的多个Tool类会自动发现**

**条件**:
- 继承自Tool
- 非抽象类
- 不以_开头
- _plugin_discoverable=True（默认）

**结论**: **可以在kb_access.py中定义KBSearchTool和KBReadTool**

---

## 实现方案调整

### 调整1：kb_search需要手动过滤

**原因**: IndexManager.search()不支持file_id参数

**实现**:
```python
# 1. 全局搜索
response = index_manager.search(query, top_k=5)

# 2. 手动过滤file_id
filtered = [r for r in response.results if r.file_id == target_file_id]

# 3. 返回结果
return filtered[:5]  # 最多5条
```

**问题**: 如果目标文件相关结果<5条，可能top_k需要增大

**解决**: 先用top_k=20搜索，过滤后取前5条

### 调整2：kb_read需要实现分块

**原因**: get_parsed_text()返回完整文本

**需要验证**: chunk_index=0存储的是什么

**实现方案A**: 如果chunk_index=0是完整文本
```python
# 1. 获取完整文本
full_text = kb_store.get_parsed_text(file_id)

# 2. 分块
chunk_size = 10000
start = chunk_index * chunk_size
end = start + chunk_size
chunk = full_text[start:end]

# 3. 返回
return {
    "content": chunk,
    "chunk_index": chunk_index,
    "total_chunks": len(full_text) // chunk_size + 1,
    "has_more": end < len(full_text),
}
```

**实现方案B**: 如果chunk_index=0只是第一个chunk
```python
# 需要查询所有chunks
# SELECT chunk_text FROM kb_chunks 
# WHERE file_id = ? 
# ORDER BY chunk_index
# 然后拼接
```

**推荐**: 先验证chunk_index=0的内容，再决定实现

### 调整3：权限验证使用sender_id

**实现**:
```python
# 1. 从Session读取pending_file
# 2. 验证pending_file.sender_id == request.sender_id
# 3. 从KB读取file_record
# 4. 验证file_record.sender_id == request.sender_id  # 双重验证
```

---

## 最终实施方案

### 文件组织

```
nanobot/agent/tools/
└── kb_access.py
    ├── KBSearchTool
    └── KBReadTool
```

### KBSearchTool实现

```python
class KBSearchTool(Tool):
    def __init__(self, sessions: SessionManager):
        self._sessions = sessions
    
    @property
    def name(self) -> str:
        return "kb_search"
    
    @property
    def description(self) -> str:
        return "Search the uploaded file for relevant content"
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                }
            },
            "required": ["query"]
        }
    
    async def execute(self, query: str) -> str:
        # 1. Get RequestContext
        request = current_request_context()
        if not request or not request.session_key:
            return ToolResult.error("Error: No session context")
        
        # 2. Get pending_file
        session = await self._sessions.state.get(request.session_key)
        pending_file = session.metadata.get("pending_file")
        if not pending_file:
            return ToolResult.error("Error: No file uploaded")
        
        # 3. Validate sender
        if pending_file.get("sender_id") != request.sender_id:
            return ToolResult.error("Error: Permission denied")
        
        file_id = pending_file["file_id"]
        
        # 4. Verify file exists and status
        kb_store = KnowledgeBaseStore(self._sessions.get_sqlite_store())
        file_record = kb_store.get_file(file_id)
        if not file_record:
            return ToolResult.error("Error: File not found")
        if file_record["status"] == "failed":
            return ToolResult.error("Error: File processing failed")
        if file_record["status"] == "pending":
            return ToolResult.error("The file is still being processed. Please wait.")
        
        # 5. Double-check sender_id
        if file_record.get("sender_id") != request.sender_id:
            return ToolResult.error("Error: Permission denied")
        
        # 6. Search
        index_manager = IndexManager(kb_store)
        response = index_manager.search(query, top_k=20)
        
        # 7. Filter by file_id
        results = [r for r in response.results if r.file_id == file_id][:5]
        
        # 8. Format results
        if not results:
            return "No relevant content found in the file."
        
        output = []
        for i, r in enumerate(results, 1):
            output.append(f"[{i}] {r.chunk_text}")
            if r.source_page:
                output.append(f"   (Page {r.source_page})")
        
        return "\n\n".join(output)
```

### KBReadTool实现

**待验证chunk_index=0内容后实现**

---

## 下一步行动

### 必须先验证

**问题**: chunk_index=0存储的是什么？

**验证方法**: 查看processor.py如何保存chunk_index=0

**验证后**: 确定kb_read的实现方式

### 然后编码

1. 创建kb_access.py
2. 实现KBSearchTool
3. 验证chunk_index=0
4. 实现KBReadTool
5. 测试

---

**调查完成时间**: 2026-10-30  
**状态**: ✅ **调查完成，等待chunk_index=0验证后开始编码**
