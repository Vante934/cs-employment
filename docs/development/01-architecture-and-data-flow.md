# 架构设计与数据流

## 一、整体架构

### 1.1 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        飞书用户                              │
└───────────────────────┬─────────────────────────────────────┘
                        │ 文件消息 / 查询消息
                        ↓
┌─────────────────────────────────────────────────────────────┐
│              飞书 Channel（复用 Nanobot）                     │
│         channels/feishu/runtime.py                          │
└───────────────────────┬─────────────────────────────────────┘
                        │ InboundMessage
                        ↓
┌─────────────────────────────────────────────────────────────┐
│              MessageBus（复用 Nanobot）                       │
│         bus/queue.py: publish_inbound()                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────────┐
│              AgentLoop（复用 Nanobot）                        │
│         agent/loop.py: process_direct()                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────────┐
│              LLM Provider（复用 Nanobot）                     │
│         providers/base.py                                   │
│         → 决策调用哪个 Tool                                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────────┐
│              ToolRegistry（复用 Nanobot）                     │
│         agent/tools/registry.py                             │
│         → 路由到知识库工具                                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ↓
        ┌───────────────┴───────────────┬─────────────────┐
        │                               │                 │
        ↓                               ↓                 ↓
┌───────────────┐          ┌──────────────────┐  ┌──────────────┐
│  kb_upload    │          │   kb_search      │  │  kb_list     │
│  Tool         │          │   Tool           │  │  kb_delete   │
│  （新增）      │          │   （新增）        │  │  Tool（新增） │
└───────┬───────┘          └────────┬─────────┘  └──────┬───────┘
        │                           │                    │
        ↓                           ↓                    ↓
┌─────────────────────────────────────────────────────────────┐
│              知识库子系统（新增）                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │  KnowledgeBaseStore                              │      │
│  │  - SQLite 数据访问层                              │      │
│  │  - 文件记录、切片记录、配置管理                     │      │
│  └──────────────────────────────────────────────────┘      │
│  ┌──────────────────────────────────────────────────┐      │
│  │  FileParser                                      │      │
│  │  - PDF/DOCX/TXT/MD/XLSX 解析器                   │      │
│  │  - 文本切片逻辑                                    │      │
│  └──────────────────────────────────────────────────┘      │
│  ┌──────────────────────────────────────────────────┐      │
│  │  VectorIndex                                     │      │
│  │  - FAISS 索引管理                                 │      │
│  │  - sentence-transformers 向量化                   │      │
│  └──────────────────────────────────────────────────┘      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────────┐
│              持久化层                                         │
│  - SQLite: 元数据（~/.nanobot/session.db 扩展）              │
│  - 文件系统: 原始文件（~/.nanobot/workspace/kb/files/）       │
│  - FAISS: 向量索引（~/.nanobot/workspace/kb/vector_index）   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 复用 Nanobot 的核心能力

| Nanobot 组件 | 源码路径 | 复用方式 | 验证状态 |
|-------------|---------|---------|---------|
| 飞书 Channel | channels/feishu/runtime.py | 直接复用消息接收 | ✅ 已证实存在 |
| MessageBus | bus/queue.py | 消息分发机制 | ✅ 已证实存在 |
| AgentLoop | agent/loop.py | Agent 核心逻辑 | ✅ 已证实存在 |
| SessionManager | session/manager.py | 会话管理 | ✅ 已证实存在 |
| SQLite Store | session/sqlite_store.py | 数据持久化 | ✅ 已证实存在 |
| LLM Provider | providers/base.py | 模型调用和重试 | ✅ 已证实存在 |
| ToolRegistry | agent/tools/registry.py | 工具注册机制 | ✅ 已证实存在 |
| Tool Base | agent/tools/base.py | Tool 基类 | ✅ 已证实存在 |

### 1.3 需要新增的模块

```
nanobot/
├── kb/                          # 新增：知识库子系统
│   ├── __init__.py
│   ├── store.py                 # KnowledgeBaseStore
│   ├── parser.py                # FileParser 和各格式解析器
│   ├── vector_index.py          # VectorIndex
│   └── types.py                 # 类型定义
│
└── agent/tools/                 # 扩展：新增知识库工具
    ├── kb_upload.py             # 文件接收工具
    ├── kb_search.py             # 检索工具
    ├── kb_list.py               # 列表工具
    └── kb_delete.py             # 删除工具
```

## 二、数据模型设计

### 2.1 数据库 Schema（扩展 SQLite）

**新增表 1: kb_files（文件元数据表）**

```sql
CREATE TABLE kb_files (
    -- 主键
    file_id TEXT PRIMARY KEY,           -- UUID
    
    -- 文件基本信息
    filename TEXT NOT NULL,             -- 原始文件名
    file_type TEXT NOT NULL,            -- pdf/docx/txt/md/xlsx
    file_size INTEGER NOT NULL,         -- 字节数
    file_path TEXT NOT NULL,            -- 本地存储路径
    file_hash TEXT NOT NULL,            -- SHA256 哈希（用于去重）
    
    -- 飞书来源信息
    feishu_message_id TEXT,             -- 飞书消息 ID
    feishu_file_key TEXT,               -- 飞书文件 Key
    sender_id TEXT,                     -- 发送者 ID
    
    -- 处理状态
    status TEXT NOT NULL,               -- pending/parsing/indexed/failed
    error_message TEXT,                 -- 错误信息（如有）
    
    -- 解析结果摘要
    summary TEXT,                       -- LLM 生成的文件摘要
    keywords TEXT,                      -- JSON array: ["关键词1", "关键词2"]
    page_count INTEGER,                 -- 页数（如适用）
    word_count INTEGER,                 -- 字数
    
    -- 时间戳
    created_at TEXT NOT NULL,           -- ISO 8601 格式
    updated_at TEXT NOT NULL,           -- ISO 8601 格式
    indexed_at TEXT,                    -- 索引完成时间
    
    -- 约束
    UNIQUE(file_hash)                   -- 防止重复文件
);

-- 索引
CREATE INDEX idx_kb_files_status ON kb_files(status);
CREATE INDEX idx_kb_files_created_at ON kb_files(created_at DESC);
CREATE INDEX idx_kb_files_file_hash ON kb_files(file_hash);
```

**新增表 2: kb_chunks（文本切片表）**

```sql
CREATE TABLE kb_chunks (
    -- 主键
    chunk_id TEXT PRIMARY KEY,          -- UUID
    file_id TEXT NOT NULL,              -- 外键关联 kb_files
    
    -- 切片内容
    chunk_text TEXT NOT NULL,           -- 实际文本内容
    chunk_index INTEGER NOT NULL,       -- 在文件中的顺序（0-based）
    
    -- 来源信息
    source_page INTEGER,                -- 页码（PDF/DOCX）
    source_section TEXT,                -- 章节名/工作表名
    
    -- 向量索引
    embedding_model TEXT,               -- 使用的向量化模型名称
    vector_index_id INTEGER,            -- 在 FAISS 索引中的 ID
    
    -- 时间戳
    created_at TEXT NOT NULL,           -- ISO 8601 格式
    
    -- 外键约束
    FOREIGN KEY (file_id) REFERENCES kb_files(file_id) ON DELETE CASCADE,
    
    -- 唯一约束
    UNIQUE(file_id, chunk_index)
);

-- 索引
CREATE INDEX idx_kb_chunks_file_id ON kb_chunks(file_id);
CREATE INDEX idx_kb_chunks_vector_index_id ON kb_chunks(vector_index_id);
```

**新增表 3: kb_config（知识库配置表）**

```sql
CREATE TABLE kb_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 初始配置
INSERT INTO kb_config VALUES 
    ('embedding_model', 'all-MiniLM-L6-v2', datetime('now')),
    ('chunk_size', '500', datetime('now')),
    ('chunk_overlap', '50', datetime('now')),
    ('max_file_size_mb', '50', datetime('now'));
```

### 2.2 文件系统结构

```
~/.nanobot/                          # Nanobot 工作目录
├── session.db                       # SQLite 数据库（扩展）
├── workspace/
│   └── kb/                          # 知识库目录（新增）
│       ├── files/                   # 原始文件存储
│       │   ├── <file_id_1>.pdf
│       │   ├── <file_id_2>.docx
│       │   └── ...
│       ├── vector_index.faiss       # FAISS 向量索引
│       ├── chunk_mapping.json       # chunk_id → vector_id 映射
│       └── embeddings_meta.json     # 向量化模型配置
```

### 2.3 向量索引结构

**chunk_mapping.json** 格式：
```json
{
  "0": "chunk_uuid_1",
  "1": "chunk_uuid_2",
  "2": "chunk_uuid_3"
}
```

**embeddings_meta.json** 格式：
```json
{
  "model_name": "all-MiniLM-L6-v2",
  "embedding_dim": 384,
  "total_chunks": 150,
  "created_at": "2025-01-15T10:30:00Z",
  "updated_at": "2025-01-15T14:20:00Z"
}
```

## 三、核心数据流

### 3.1 文件接收流程（F01）

```
用户发送文件消息
    ↓
飞书 Channel 接收消息
    ↓
MessageBus 分发到 AgentLoop
    ↓
LLM 决策调用 kb_upload Tool
    ↓
kb_upload.execute():
    1. 校验文件类型和大小
    2. 下载文件到本地
    3. 计算 SHA256 哈希
    4. 检查重复（查询 file_hash）
    5. 创建 kb_files 记录（status='pending'）
    6. 触发异步解析任务
    7. 返回接收成功消息
    ↓
返回用户："文件接收成功，正在处理..."
```

### 3.2 文件解析流程（F02）

```
异步解析任务启动
    ↓
FileParser.parse_file(file_id):
    1. 查询 kb_files 记录
    2. 根据 file_type 选择解析器
    3. 解析文件提取文本
    4. 调用 LLM 生成摘要和关键词
    5. 文本切片（chunk_size=500, overlap=50）
    6. 批量插入 kb_chunks 记录
    7. 更新 kb_files.status='parsed'
    ↓
触发向量化任务
```

### 3.3 向量索引流程（F03）

```
向量化任务启动
    ↓
VectorIndex.add_chunks(file_id):
    1. 查询该文件的所有 kb_chunks
    2. 批量向量化（sentence-transformers）
    3. 添加到 FAISS 索引
    4. 更新 kb_chunks.vector_index_id
    5. 保存 FAISS 索引到磁盘
    6. 更新 chunk_mapping.json
    7. 更新 kb_files.status='indexed'
    ↓
通知用户："文件已入库，可以开始检索"
```

### 3.4 知识检索流程（F04）

```
用户发送问题
    ↓
飞书 Channel → MessageBus → AgentLoop
    ↓
LLM 决策调用 kb_search Tool
    ↓
kb_search.execute(query):
    1. 向量化问题（sentence-transformers）
    2. FAISS 搜索 top_k=5
    3. 根据 vector_index_id 查询 kb_chunks
    4. 关联查询 kb_files 获取文件信息
    5. 格式化结果（包含来源信息）
    6. 返回结构化结果
    ↓
LLM 基于检索结果生成回答
    ↓
返回用户：带来源标注的回答
```

### 3.5 知识库管理流程（F05）

**列表查询**：
```
用户："列出知识库文件"
    ↓
kb_list Tool → 查询 kb_files → 返回列表
```

**删除文件**：
```
用户："删除文件 XXX"
    ↓
kb_delete.execute(file_id):
    1. 查询 kb_chunks 获取 vector_index_id 列表
    2. 从 FAISS 删除向量（标记为无效）
    3. 删除 kb_files（CASCADE 删除 kb_chunks）
    4. 删除本地文件
    5. 返回删除成功
```

## 四、关键技术决策

### 4.1 向量检索方案：FAISS

**选择理由**：
- ✅ 成熟稳定，生产环境验证
- ✅ 本地化部署，无需外部服务
- ✅ 高性能（百万级向量毫秒级检索）
- ✅ 支持索引持久化
- ✅ 社区活跃，文档完善

**替代方案**：ChromaDB
- ❌ 依赖更多，体积较大
- ❌ 作为数据库而非纯索引库，偏重
- ✅ 但更易用，内置元数据管理

**决策**：首期使用 FAISS，如遇易用性问题可切换到 ChromaDB

### 4.2 文本向量化：sentence-transformers

**模型选择**：all-MiniLM-L6-v2

**理由**：
- ✅ 开源，无需 API Key
- ✅ 轻量（80MB），快速（CPU 可用）
- ✅ 中英文支持良好
- ✅ 384 维向量，存储友好

**替代方案**：
- OpenAI Embeddings：成本高，需联网
- 更大模型（all-mpnet-base-v2）：更准确但慢

### 4.3 文件解析库

| 格式 | 选用库 | 理由 |
|------|--------|------|
| PDF | PyPDF2 | 纯 Python，无系统依赖，足够基础场景 |
| DOCX | python-docx | 官方推荐，稳定 |
| XLSX | openpyxl | 不依赖 xlrd，支持 .xlsx |
| TXT/MD | 内置 open() | 无需额外依赖 |

**注意**：PyPDF2 不支持扫描件 OCR，这在首期明确不包含。

### 4.4 文本切片策略

**参数**：
- chunk_size: 500 字符
- overlap: 50 字符

**理由**：
- 500 字符约 1-2 段落，语义完整性好
- 50 字符重叠避免关键信息被切断
- 向量化模型输入限制通常 512 tokens，500 字符安全

**可调整性**：
- 保存在 kb_config 表，可动态调整
- 调整后需重新解析文件

### 4.5 文件去重策略

**方案**：SHA256 哈希 + UNIQUE 约束

**逻辑**：
- 同一文件多次上传：返回已存在，不创建新记录
- 不同文件同名：允许，因为哈希不同

**未来增强**：
- 内容相似度去重（余弦相似度 > 0.95）
- 增量更新（文件版本管理）

### 4.6 异步任务执行

**方案**：asyncio.create_task()

**理由**：
- ✅ 简单，无需额外依赖
- ✅ 不阻塞用户交互
- ✅ 与 Nanobot 异步架构一致

**局限性**：
- ❌ 进程重启任务丢失
- ❌ 无任务队列和重试

**未来增强**：
- 使用 Nanobot 的 Cron 服务（已证实存在）
- 持久化任务队列

## 五、错误处理策略

### 5.1 文件下载失败
- 重试 3 次（指数退避）
- 仍失败：标记 status='failed'，记录 error_message
- 用户可手动重试

### 5.2 文件解析失败
- 捕获异常，不崩溃服务
- 标记 status='failed'，记录 error_message
- 损坏文件：提示用户检查文件
- 不支持的子格式：明确说明限制

### 5.3 向量化失败
- 重试 1 次
- 仍失败：保留 parsed 状态，提示用户
- 用户可手动重试向量化

### 5.4 检索失败
- 返回空结果，不抛错
- LLM 基于一般知识回答（明确告知非知识库来源）

## 六、性能考虑

### 6.1 初期规模假设
- 文件数：< 1000
- 总切片数：< 50,000
- 向量索引大小：< 100 MB
- 单次检索：< 100ms（CPU）

### 6.2 性能瓶颈识别
- 文件解析：同步阻塞（异步化缓解）
- 向量化：CPU 密集（批量处理缓解）
- FAISS 检索：在规模下足够快

### 6.3 优化预留
- FAISS GPU 版本（需 CUDA）
- 向量化批处理（batch_size=32）
- 索引分片（文件类型分索引）

---

*文档版本*: v0.1  
*最后更新*: 2025-01-XX  
*状态*: 草案
