# F06-B-B2-B2：kb_search文件范围检索 - 定点调查报告

**调查日期**: 2026-10-30  
**状态**: ✅ 调查完成  

---

## 执行摘要

### 关键发现

1. ✅ **FAISS使用IndexFlatIP** - 全局扁平索引，无内置过滤
2. ✅ **chunk_id编码file_id** - 格式：`{file_id}_chunk_{index}`
3. ✅ **有chunk_ids_matching()** - 可按正则匹配chunk_id
4. ✅ **映射关系清晰** - chunk_id ↔ faiss_id ↔ vector
5. ❌ **无FAISS metadata filtering** - 需要在应用层实现

### 推荐方案

**方案C（推荐）**: 在IndexManager.search()增加file_id参数，使用chunk_ids_matching()预过滤

**优点**:
- ✅ 不扫描全局向量
- ✅ 保证文件内Top-K正确
- ✅ 不改变现有搜索行为
- ✅ 性能开销小

---

## 调查1：FAISS索引结构

### 1.1 向量索引类型

**文件**: `nanobot/kb/index.py:69-76`

```python
def _new_index(self):
    """Build an empty ID-mapped inner-product index."""
    faiss = self._load_faiss()
    return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimension))
```

**索引类型**: 
- `IndexFlatIP` - 内积（Inner Product）扁平索引
- `IndexIDMap2` - 支持显式ID映射和删除

**特点**:
- ❌ 无分区（partitioning）
- ❌ 无元数据过滤（metadata filtering）
- ✅ 精确搜索（不是近似）
- ✅ 支持按ID删除

### 1.2 向量组织方式

**存储结构**:
```python
self._index = None  # FAISS index
self._chunk_to_faiss: dict[str, int] = {}  # chunk_id -> FAISS ID
self._faiss_to_chunk: dict[int, str] = {}  # FAISS ID -> chunk_id
```

**映射关系**:
```
chunk_id (file123_chunk_5) 
  → faiss_id (内部序号，如42)
  → vector (在FAISS中的实际向量)
```

---

## 调查2：chunk_id命名规则

### 2.1 chunk_id格式

**观察到的模式** (从delete_file_index):

```python
# nanobot/kb/index_manager.py:695
pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
```

**格式**: `{file_id}_chunk_{chunk_index}`

**示例**:
- file_id: `abc-123-def`
- chunk 0: `abc-123-def_chunk_0`
- chunk 5: `abc-123-def_chunk_5`

### 2.2 chunk_ids_matching()

**文件**: `nanobot/kb/index.py:454-465`

```python
def chunk_ids_matching(self, pattern: str) -> list[str]:
    """List chunk IDs matching the given regex pattern."""
    import re
    compiled = re.compile(pattern)
    return [cid for cid in self._chunk_to_faiss.keys() if compiled.match(cid)]
```

**功能**: 从内存中快速筛选chunk_id

**性能**: O(N)，但N是总chunk数，通常不大

---

## 调查3：搜索流程

### 3.1 当前搜索流程

**文件**: `nanobot/kb/index_manager.py:565-680`

```
1. 参数验证
2. 嵌入查询 → query_vector
3. FAISS搜索 → vector_results (所有文件)
4. 应用min_similarity过滤
5. 批量查询chunks → get_chunks_by_ids()
6. 构建SearchResult（包含file_id）
7. 返回
```

**关键点**: 
- FAISS返回全局Top-K
- 在步骤6才知道file_id
- 无法在FAISS层面过滤

### 3.2 get_chunks_by_ids()

**文件**: `nanobot/kb/store.py:428-471`

**SQL查询**:
```sql
SELECT
    c.chunk_id, c.file_id, c.chunk_text, c.chunk_index,
    c.source_page, c.source_section,
    c.embedding_model, c.vector_index_id,
    f.filename, f.file_type
FROM kb_chunks c
JOIN kb_files f ON c.file_id = f.file_id
WHERE c.chunk_id IN (?, ?, ...)
```

**返回**: `dict[chunk_id -> chunk_record]`

---

## 调查4：现有file_id相关操作

### 4.1 delete_file_index()

**文件**: `nanobot/kb/index_manager.py:682-707`

```python
def delete_file_index(self, file_id: str) -> bool:
    """Delete index entries for a file."""
    with self._index_lock:
        pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
        chunk_ids = self.vector_index.chunk_ids_matching(pattern)
        
        if not chunk_ids:
            return False
        
        for chunk_id in chunk_ids:
            self.vector_index.remove(chunk_id)
        
        return True
```

**关键**: 使用chunk_ids_matching()找到所有该文件的chunks

---

## 推荐实施方案

### 方案C：应用层预过滤（推荐）

#### 实现思路

**在IndexManager.search()中**:

```python
def search(
    self,
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    file_id: str | None = None  # ← 新增参数
) -> SearchResponse:
    # ... 参数验证 ...
    
    # 如果指定file_id，预过滤
    if file_id is not None:
        # 1. 找到该文件的所有chunk_ids
        pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
        target_chunk_ids = self.vector_index.chunk_ids_matching(pattern)
        
        if not target_chunk_ids:
            # 文件没有chunks
            return SearchResponse(
                success=True,
                results=[],
                candidate_count=0,
                returned_count=0
            )
        
        # 2. 获取这些chunks的faiss_ids
        target_faiss_ids = [
            self.vector_index._chunk_to_faiss[cid] 
            for cid in target_chunk_ids
        ]
        
        # 3. 为这些faiss_ids创建临时IDSelector
        # FAISS的IDSelectorBatch可以限定搜索范围
        faiss = self.vector_index._load_faiss()
        id_selector = faiss.IDSelectorBatch(
            np.array(target_faiss_ids, dtype=np.int64)
        )
        
        # 4. 使用search_preassigned或自己实现过滤
        # 因为IndexIDMap2不直接支持selector，需要手动实现
        # 方案：对target_chunk_ids的向量计算相似度，排序
        
        # 实际实现见下文
    else:
        # 原有全局搜索
        # ... 现有逻辑 ...
```

#### 详细实现

**步骤1**: 预过滤chunk_ids
```python
pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
target_chunk_ids = self.vector_index.chunk_ids_matching(pattern)
```

**步骤2**: 计算这些chunks的相似度
```python
# 对每个chunk，计算与query_vector的相似度
similarities = []
for chunk_id in target_chunk_ids:
    faiss_id = self.vector_index._chunk_to_faiss[chunk_id]
    # 从FAISS获取该向量
    vector = self._get_vector_by_faiss_id(faiss_id)
    # 计算内积（已归一化，内积=余弦相似度）
    similarity = float(np.dot(query_vector, vector))
    similarities.append((chunk_id, similarity))

# 排序
similarities.sort(key=lambda x: x[1], reverse=True)

# 取Top-K
top_chunk_ids = [cid for cid, sim in similarities[:top_k]]
```

**问题**: FAISS IndexFlatIP不直接暴露向量获取接口

**解决**: 两个方案
- 方案A: 从SQLite kb_chunks读取embedding
- 方案B: 重新构建一个只包含target_chunks的临时FAISS索引

#### 最简单可行方案

**直接搜索全局，然后过滤并重新排序**:

```python
def search(
    self,
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    file_id: str | None = None
) -> SearchResponse:
    # ... 参数验证 ...
    
    # 如果指定file_id，需要更多候选
    search_k = top_k
    if file_id is not None:
        # 预估需要搜索的数量
        # 假设目标文件占10%，需要搜索10倍
        search_k = min(top_k * 10, 100)
    
    # ... 嵌入查询 ...
    
    # 搜索
    vector_results = self.vector_index.search(query_vector, top_k=search_k)
    
    # ... 应用min_similarity过滤 ...
    
    # 批量查询chunks
    chunks_by_id = self.kb_store.get_chunks_by_ids(chunk_ids)
    
    # 如果指定file_id，过滤结果
    if file_id is not None:
        filtered_results = []
        for vector_result in filtered_results:
            chunk = chunks_by_id.get(vector_result.chunk_id)
            if chunk and chunk["file_id"] == file_id:
                # 构建SearchResult
                filtered_results.append(...)
        
        # 限制为top_k
        results = filtered_results[:top_k]
    else:
        # 原有逻辑
        results = ...
    
    return SearchResponse(...)
```

**问题**: 
- ❌ 如果其他文件相似度很高，可能占满search_k
- ❌ 无法保证文件内Top-K正确

---

## 推荐最终方案

### 方案：直接查询文件chunks并计算相似度

**优点**:
- ✅ 保证文件内Top-K绝对正确
- ✅ 不受其他文件影响
- ✅ 实现简单清晰
- ✅ 不改变全局搜索

**实现**:

```python
def search(
    self,
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    file_id: str | None = None
) -> SearchResponse:
    # 参数验证...
    
    # 嵌入查询
    query_vector = self.embedder.embed(query, normalize=True)
    
    if file_id is not None:
        # 文件范围搜索
        return self._search_file_scope(
            query_vector, file_id, top_k, min_similarity
        )
    else:
        # 全局搜索（原有逻辑）
        return self._search_global(query_vector, top_k, min_similarity)

def _search_file_scope(
    self,
    query_vector: np.ndarray,
    file_id: str,
    top_k: int,
    min_similarity: float
) -> SearchResponse:
    """Search within a specific file."""
    # 1. 找到文件的所有chunk_ids
    pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
    target_chunk_ids = self.vector_index.chunk_ids_matching(pattern)
    
    if not target_chunk_ids:
        return SearchResponse(
            success=True,
            results=[],
            candidate_count=0,
            returned_count=0
        )
    
    # 2. 获取这些chunks的向量并计算相似度
    similarities = []
    for chunk_id in target_chunk_ids:
        faiss_id = self.vector_index._chunk_to_faiss.get(chunk_id)
        if faiss_id is None:
            continue
        
        # 从FAISS获取向量
        # 方法：通过reconstruct(faiss_id)
        try:
            vector = self._index.reconstruct(int(faiss_id))
            similarity = float(np.dot(query_vector, vector))
            
            if similarity >= min_similarity:
                similarities.append((chunk_id, similarity))
        except Exception:
            continue
    
    # 3. 排序并取Top-K
    similarities.sort(key=lambda x: x[1], reverse=True)
    top_chunk_ids = [(cid, sim) for cid, sim in similarities[:top_k]]
    
    # 4. 批量查询chunk数据
    chunk_ids = [cid for cid, _ in top_chunk_ids]
    chunks_by_id = self.kb_store.get_chunks_by_ids(chunk_ids)
    
    # 5. 构建SearchResult
    results = []
    for chunk_id, similarity in top_chunk_ids:
        chunk = chunks_by_id.get(chunk_id)
        if chunk:
            results.append(SearchResult(
                chunk_id=chunk_id,
                chunk_text=chunk["chunk_text"],
                chunk_index=chunk["chunk_index"],
                similarity=similarity,
                file_id=chunk["file_id"],
                filename=chunk["filename"],
                file_type=chunk["file_type"],
                source_page=chunk.get("source_page"),
                source_section=chunk.get("source_section")
            ))
    
    return SearchResponse(
        success=True,
        results=results,
        candidate_count=len(similarities),
        returned_count=len(results)
    )
```

**关键API**: `self._index.reconstruct(faiss_id)`

**验证reconstruct是否可用**...

---

## 调查5：FAISS reconstruct()支持

**IndexFlatIP**: ✅ 支持reconstruct()

**IndexIDMap2**: ✅ 支持reconstruct()（转发到内部索引）

**结论**: 可以使用reconstruct()获取向量

---

## 最终推荐方案总结

### 实现方式

**在IndexManager.search()增加可选参数**:
- `file_id: str | None = None`

**实现逻辑**:
1. file_id=None: 原有全局搜索（零改动）
2. file_id!=None: 文件范围搜索
   - 使用chunk_ids_matching()找到文件所有chunks
   - 对每个chunk使用reconstruct()获取向量
   - 计算与query_vector的相似度
   - 排序取Top-K
   - 返回结果

### 性能分析

**文件范围搜索复杂度**:
- chunk_ids_matching(): O(总chunks数) ~1000-10000
- reconstruct()循环: O(文件chunks数) ~10-100
- 相似度计算: O(文件chunks数 * 维度) ~10-100 * 1536
- 排序: O(文件chunks数 * log(文件chunks数))

**预期性能**:
- 单文件通常<100个chunks
- 计算100个向量相似度 < 1ms
- 远快于全局FAISS搜索

### 对现有系统的影响

✅ **零影响**:
- 不传file_id: 完全等同现有逻辑
- 不改变FAISS索引结构
- 不影响F04/F05
- 向后兼容

---

**调查完成时间**: 2026-10-30  
**状态**: ✅ **调查完成，推荐方案清晰，等待确认**
