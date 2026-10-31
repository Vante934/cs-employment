# F06-B-B2-B2：文件范围检索实施前最终核验报告

**核验日期**: 2026-10-30  
**状态**: ✅ 核验完成  

---

## 执行摘要

### 核验结论

✅ **所有核验项通过，可以开始编码**

**关键发现**:
1. ✅ Embedding维度：**384维**（不是1536维）
2. ✅ 文档和查询向量均归一化
3. ✅ IndexFlatIP内积直接等于余弦相似度
4. ✅ get_vector()已存在且可靠
5. ✅ 映射关系在save/load中正确维护
6. ✅ chunk_ids_matching()可靠
7. ✅ 性能可接受

---

## 核验1：Embedding维度（优先级最高）

### 1.1 实际使用的模型

**文件**: `nanobot/kb/embedder.py:13-16`

```python
DEFAULT_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Dimensions: 384
# Languages: 50+ including English and Chinese
# Size: ~420MB on first download
```

**模型名称**: `paraphrase-multilingual-MiniLM-L12-v2`  
**实际维度**: **384**  
**语言支持**: 50+种语言（包括中英文）

### 1.2 维度确定方式

**文件**: `nanobot/kb/embedder.py:67-77`

```python
@property
def dimension(self) -> int:
    """Get embedding dimension by probing the model."""
    if self._dimension is not None:
        return self._dimension
    
    model = self._load_model()
    test_vec = model.encode("test", normalize_embeddings=True)
    self._dimension = test_vec.shape[0] if test_vec.ndim == 1 else test_vec.shape[1]
    return self._dimension
```

**IndexManager使用** (`index_manager.py:104`):
```python
dimension = self.embedder.dimension
index = VectorIndex(dimension, self.index_dir)
```

**结论**: ✅ **FAISS索引维度自动从Embedder获取（384维）**

### 1.3 为什么之前估算错误？

**原因**: 误认为使用OpenAI text-embedding-3-small（1536维）

**实际**: 使用sentence-transformers的多语言MiniLM模型（384维）

**影响**: 性能估算需要基于384维重新计算（见核验5）

### 1.4 文档与查询向量来源

**文档向量生成** (`index_manager.py:233, 389`):
```python
embedding = self.embedder.embed(chunk_text, normalize=True)
```

**查询向量生成** (`index_manager.py:608`):
```python
query_vector = self.embedder.embed(query, normalize=True)
```

**结论**: ✅ **文档和查询使用同一Embedder和同一模型**

---

## 核验2：相似度计算一致性

### 2.1 向量归一化

**文档向量** (`index_manager.py:233`):
```python
embedding = self.embedder.embed(chunk_text, normalize=True)
```

**查询向量** (`index_manager.py:608`):
```python
query_vector = self.embedder.embed(query, normalize=True)
```

**Embedder实现** (`embedder.py:101`):
```python
embedding = model.encode(
    text,
    normalize_embeddings=normalize,  # L2归一化
    convert_to_numpy=True
)
```

**结论**: ✅ **所有向量均L2归一化（||v|| = 1）**

### 2.2 FAISS内积与余弦相似度

**FAISS索引类型** (`index.py:73`):
```python
return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimension))
```

**IndexFlatIP**: Inner Product（内积）索引

**数学关系**:
```
cos(θ) = (A·B) / (||A|| ||B||)
当||A|| = ||B|| = 1时:
cos(θ) = A·B (内积)
```

**VectorIndex.search()实现** (`index.py:183-185`):
```python
# For normalized vectors with IndexFlatIP, distance = inner product = cosine similarity
# Cosine similarity range: [-1.0, 1.0], higher is more similar
similarity = float(dist)
```

**结论**: ✅ **IndexFlatIP的distance直接等于余弦相似度，无需转换**

### 2.3 min_similarity判断

**IndexManager._search_locked()** (`index_manager.py:617-621`):
```python
# Filter by min_similarity
filtered_results = []
for vector_result in vector_results:
    if vector_result.similarity >= min_similarity:
        filtered_results.append(vector_result)
```

**结论**: ✅ **min_similarity直接比较，可完全复用**

### 2.4 新旧搜索一致性

**原有搜索流程**:
1. FAISS搜索 → distances
2. similarity = float(dist)
3. 过滤 >= min_similarity
4. 返回

**文件范围搜索流程**:
1. get_vector() → vectors
2. similarity = np.dot(query_vector, vector)
3. 过滤 >= min_similarity
4. 排序取Top-K
5. 返回

**数学等价性**:
- FAISS内积 = np.dot(归一化向量A, 归一化向量B)
- 两者计算结果完全相同

**结论**: ✅ **新旧搜索在相同候选下结果完全一致**

---

## 核验3：向量映射可靠性

### 3.1 映射维护

**初始化** (`index.py:48-50`):
```python
self._chunk_to_faiss: dict[str, int] = {}  # chunk_id -> FAISS ID
self._faiss_to_chunk: dict[int, str] = {}  # FAISS ID -> chunk_id
self._next_faiss_id = 0
```

**添加向量** (`index.py:99-107`):
```python
def add(self, chunk_id: str, vector: np.ndarray) -> int:
    faiss_id = self._next_faiss_id
    self._index.add_with_ids(vector_2d, np.array([faiss_id], dtype=np.int64))
    
    # Update mappings
    self._chunk_to_faiss[chunk_id] = faiss_id
    self._faiss_to_chunk[faiss_id] = chunk_id
    self._next_faiss_id += 1
```

**删除向量** (`index.py:198-213`):
```python
def remove(self, chunk_id: str) -> bool:
    faiss_id = self._chunk_to_faiss.get(chunk_id)
    if faiss_id is None:
        return False
    
    if self._index is not None:
        removed = self._index.remove_ids(np.array([faiss_id], dtype=np.int64))
    
    del self._chunk_to_faiss[chunk_id]
    del self._faiss_to_chunk[faiss_id]
```

**结论**: ✅ **映射在add/remove时同步维护**

### 3.2 持久化

**save()** (`index.py:269-281`):
```python
mapping_data = {
    "format": INDEX_FORMAT,
    "index_file": index_file,
    "next_faiss_id": self._next_faiss_id,
    "chunk_to_faiss": self._chunk_to_faiss,
}
# 原子写入mapping.json
```

**load()** (`index.py:315-330`):
```python
mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
chunk_to_faiss, next_faiss_id = self._parse_mapping(mapping_data)
# 重建_faiss_to_chunk
faiss_to_chunk = {v: k for k, v in chunk_to_faiss.items()}
```

**结论**: ✅ **映射正确持久化和加载**

### 3.3 reconstruct()可靠性

**get_vector()实现** (`index.py:229-234`):
```python
def get_vector(self, chunk_id: str) -> np.ndarray | None:
    """Get a copy of a stored vector for rollback operations."""
    faiss_id = self.get_faiss_id(chunk_id)
    if faiss_id is None or self._index is None:
        return None
    return np.asarray(self._index.reconstruct(faiss_id), dtype=np.float32).copy()
```

**IndexIDMap2.reconstruct()**:
- ✅ 支持reconstruct()
- ✅ 转发到内部IndexFlatIP
- ✅ IndexFlatIP存储原始向量，支持reconstruct()

**结论**: ✅ **reconstruct()可靠，get_vector()已存在且正确**

### 3.4 并发访问

**IndexManager中的锁** (`index_manager.py:98, 564`):
```python
self._index_lock = threading.Lock()

def search(...):
    with self._index_lock:
        return self._search_locked(...)
```

**VectorIndex操作**:
- add(): 在IndexManager.build_index_for_file()中调用（有锁）
- remove(): 在IndexManager.delete_file_index()中调用（有锁）
- save(): 在IndexManager操作完成后调用（有锁）
- get_vector(): 只读操作，无需写锁

**结论**: ✅ **现有锁机制足够保护get_vector()的并发读取**

---

## 核验4：文件范围匹配准确性

### 4.1 chunk_ids_matching()实现

**文件**: `nanobot/kb/index.py:454-465`

```python
def chunk_ids_matching(self, pattern: str) -> list[str]:
    """List chunk IDs matching the given regex pattern.
    
    Args:
        pattern: Regular expression pattern (e.g., r"^file123_chunk_\\d+$")
    
    Returns:
        List of matching chunk IDs
    """
    import re
    compiled = re.compile(pattern)
    return [cid for cid in self._chunk_to_faiss.keys() if compiled.match(cid)]
```

**特点**:
- 遍历内存中所有chunk_id
- 使用正则匹配
- 返回匹配的chunk_id列表

**结论**: ✅ **实现简单可靠**

### 4.2 正则转义

**delete_file_index()使用** (`index_manager.py:695`):
```python
pattern = f"^{re.escape(file_id)}_chunk_\\d+$"
```

**re.escape()作用**:
- 转义特殊字符（如`.`, `*`, `+`, `?`, `[`, `]`等）
- 确保file_id中的特殊字符被当作字面量

**结论**: ✅ **必须使用re.escape()，已有先例**

### 4.3 匹配准确性

**chunk_id格式**: `{file_id}_chunk_{index}`

**正则模式**: `^{re.escape(file_id)}_chunk_\\d+$`

**示例**:
- file_id: `abc-123.def`
- 匹配: `abc-123.def_chunk_0`, `abc-123.def_chunk_5`
- 不匹配: `abc-123.def`, `other_chunk_5`, `abc-123.def_chunk_`

**结论**: ✅ **正则模式精确匹配指定file_id的chunks**

### 4.4 是否需要SQLite验证

**分析**:
- chunk_id由系统生成，格式固定
- FAISS索引中的chunk_id与SQLite一致
- 正则匹配已足够准确

**结论**: ✅ **无需额外SQLite验证，正则匹配足够**

---

## 核验5：性能估算修正

### 5.1 基于384维重新估算

**文件范围搜索步骤**:

1. **chunk_ids_matching()**:
   - 遍历所有chunk_id: O(总chunks数)
   - 正则匹配: O(chunk_id长度)
   - 假设10000个总chunks，单次遍历~0.1ms

2. **get_vector()循环**:
   - 假设单文件100个chunks
   - 每次reconstruct(): O(1)，FAISS直接访问
   - 100次 × ~0.01ms = 1ms

3. **相似度计算**:
   - np.dot(384维向量, 384维向量)
   - 单次: ~0.001ms
   - 100次 × 0.001ms = 0.1ms

4. **排序**:
   - 100个元素排序: O(100 * log(100)) ≈ 664比较
   - ~0.01ms

**总计**: ~1.2ms（单文件100 chunks）

### 5.2 与全局搜索对比

**全局FAISS搜索** (10000 chunks, top_k=5):
- FAISS IndexFlatIP: O(N * D) = 10000 * 384
- 内积计算: ~1-2ms
- 排序: O(N * log(K))
- 总计: ~2-3ms

**文件范围搜索** (100 chunks):
- 总计: ~1.2ms

**结论**: ✅ **文件范围搜索比全局搜索更快**

### 5.3 是否需要缓存

**分析**:
- 单次搜索<2ms，性能已足够
- 查询向量每次不同，无法缓存
- 文件chunks在索引后不变，但get_vector()已很快
- 增加缓存反而增加复杂度

**结论**: ✅ **无需缓存，性能已足够**

### 5.4 单机单用户场景

**当前场景**:
- 单用户私聊
- 单文件上传
- 典型文件<50页，约50-100 chunks

**性能评估**:
- 1.2ms搜索延迟
- 加上embedding查询(~50ms)
- 总延迟<100ms

**结论**: ✅ **性能对单机单用户场景足够**

---

## 最终结论

### 所有核验项通过

| 核验项 | 状态 | 关键发现 |
|--------|------|---------|
| Embedding维度 | ✅ | 384维（MiniLM-L12-v2） |
| 归一化 | ✅ | 文档和查询均归一化 |
| 相似度计算 | ✅ | 内积=余弦相似度 |
| get_vector() | ✅ | 已存在且可靠 |
| 映射维护 | ✅ | save/load正确维护 |
| 文件匹配 | ✅ | 正则匹配准确 |
| 性能 | ✅ | <2ms，足够快 |

### 无阻断性问题

❌ **无任何阻断性问题**

### 可以开始编码

✅ **方案可行，立即开始B2-B2编码**

---

## 实施建议

### 修改文件

1. **nanobot/kb/index_manager.py**
   - search()增加file_id参数
   - 实现_search_file_scope()

2. **nanobot/agent/tools/kb_access.py**
   - KBSearchTool.execute()调用search(file_id=...)

### 无需修改

- ❌ nanobot/kb/index.py (get_vector()已存在)
- ❌ FAISS索引结构
- ❌ 映射维护逻辑

### 实施步骤

1. 修改IndexManager.search()
2. 实现_search_file_scope()
3. 修改KBSearchTool
4. 添加测试
5. 回归测试

---

**核验完成时间**: 2026-10-30  
**状态**: ✅ **核验完成，可以立即开始B2-B2编码实施**
