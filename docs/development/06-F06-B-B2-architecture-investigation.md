# F06-B-B2 智能问候与意图识别 - 架构调查报告

**调查时间**: 2026-10-30  
**状态**: 🔍 进行中

---

## 一、调查目标

### B2 预期流程

```
用户发送文件
    ↓
识别文件并理解基本内容
    ↓
向用户介绍文件并询问处理意图
    ↓
用户选择需求
    ↓
Agent 执行对应任务并反馈结果
```

### 关键调查点

1. ✅ **文件消息如何进入Agent**
2. 🔍 **文件解析结果如何传递**
3. 🔍 **用户的下一条消息如何关联到待处理文件**
4. 🔍 **多用户并发时如何避免意图串线**

---

## 二、文件消息如何进入Agent（已调查）

### 2.1 完整调用链

```
用户发送文件到飞书
    ↓
FeishuChannel._on_message() 【runtime.py:2750】
    ├─ 权限检查
    ├─ 消息去重
    ├─ 发送欢迎语（B1）
    └─ 添加reaction（B4）
    ↓
解析消息内容 【runtime.py:2940-2990】
    ├─ msg_type == "file"
    │   ├─ _download_and_save_media()
    │   │   └─ 下载到 data/media/feishu/
    │   │
    │   └─ _auto_upload_to_kb(file_path, sender_id) 【B3自动上传】
    │       ├─ 创建 progress_callback
    │       ├─ KBUploadTool.execute()
    │       │   ├─ 文件验证
    │       │   ├─ 检查重复
    │       │   ├─ 复制到KB存储
    │       │   ├─ 创建数据库记录
    │       │   └─ process_file() 【工作线程】
    │       │       ├─ parse_file() → 解析内容
    │       │       ├─ chunk_file() → 分块
    │       │       └─ build_index() → 建立索引
    │       │
    │       └─ 返回结果字符串：
    │           "✅ File added to knowledge base successfully"
    │           或
    │           "ℹ️ File already in knowledge base"
    │
    ├─ content_text = f"{content_text}\n{kb_result}"
    └─ content_parts.append(content_text)
    ↓
构建content字符串 【runtime.py:3030】
    content = "\n".join(content_parts)
    # 例如：
    # "[file: /path/to/test.pdf]
    #  ✅ File added to knowledge base successfully"
    ↓
创建session_key 【runtime.py:3045】
    if chat_type == "p2p":
        session_key = None  # 私聊不override，使用默认
    elif chat_type == "group":
        if config.topic_isolation:
            session_key = f"{self.name}:{chat_id}:{root_id or message_id}"
        else:
            session_key = f"{self.name}:{chat_id}"
    ↓
调用 _handle_message() 【runtime.py:3055】
    await self._handle_message(
        sender_id=sender_id,
        chat_id=reply_to,
        content=content,  # ← 包含 [file: ...] 和 KB结果
        media=media_paths,  # ← ["/path/to/file.pdf"]
        metadata={
            "message_id": message_id,
            "chat_type": chat_type,
            "msg_type": msg_type,
            ...
        },
        session_key=session_key,
        is_dm=chat_type == "p2p"
    )
    ↓
BaseChannel._handle_message() 【base.py:261】
    ├─ 权限检查
    └─ 创建 InboundMessage
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,  # ← "[file: ...]\n✅ File added..."
            media=media or [],  # ← ["/path/to/file.pdf"]
            metadata=meta,
            session_key_override=session_key,
            ...
        )
    ↓
MessageBus.publish_inbound(msg) 【queue.py:38】
    await self.inbound.put(msg)
    ↓
AgentLoop.run() 【loop.py:1409】
    while self._running:
        msg = await self.bus.consume_inbound()
        await self._dispatch_inbound(msg)
            ↓
        _dispatch_inbound() 【loop.py:1446】
            ├─ 检查命令（/new, /stop等）
            ├─ 获取session_key
            └─ _enqueue_session_message(msg)
                ↓
            _process_session_queue() 【loop.py:某处】
                ├─ 获取或创建Session
                ├─ 构建Agent context
                └─ 调用 LLM（传递content和media）
                    ↓
                Agent 看到：
                    content: "[file: /path/to/test.pdf]\n✅ File added to knowledge base successfully"
                    media: ["/path/to/file.pdf"]
```

### 2.2 关键发现

#### 当前行为（B3已实现）

**文件上传时**：
1. 文件自动入库
2. 发送进度消息（4条）
3. Agent收到的content包含：
   - `[file: /path/to/file.pdf]`
   - KB处理结果（成功/重复/失败）
4. Agent会回复用户（基于LLM生成）

**问题**：
- ✅ 文件已自动入库
- ✅ 进度已反馈
- ❌ **但Agent不知道用户的意图**（想做什么）
- ❌ **Agent没有主动询问用户需求**
- ❌ **用户需要明确说"帮我总结这个文件"**

#### B2需要改变的行为

**文件上传时**：
1. 文件仍然自动入库
2. 发送进度消息（保留）
3. **但不立即让Agent处理完整对话**
4. **而是先询问用户意图**：
   ```
   📄 收到文件：产品需求文档.pdf (245KB)
   
   我可以帮你：
   1. 📝 总结文档要点
   2. 🔍 提取关键信息
   3. ❓ 回答文档相关问题
   4. 📊 分析文档结构
   
   请告诉我你想做什么？
   ```
5. **等待用户回复**
6. **用户下一条消息关联到这个文件**
7. **Agent执行对应任务**

---

## 三、文件解析结果如何传递（调查中）

### 3.1 当前状态

**文件内容存储**：
- **原始文件**: `data/media/feishu/file_messageId.ext`
- **KB存储**: `data/kb/files/hash_first8.ext`
- **数据库**: `kb_files` 表（file_id, filename, file_path, status）
- **解析文本**: `kb_chunks` 表（chunk_index=0存储完整文本）
- **分块**: `kb_chunks` 表（chunk_index>=1存储各分块）
- **向量**: FAISS索引文件 `data/kb/index/*.faiss`

**Agent如何访问文件内容**：

目前Agent通过两种方式：

1. **media路径**（InboundMessage.media）
   - Agent可以访问原始文件路径
   - 但通常不直接读取原始文件

2. **KB检索工具**（KBSearchTool）
   - Agent使用工具搜索相关内容
   - 通过向量检索获取相关片段
   - 用户需要明确提问才触发

### 3.2 B2需要的能力

**文件上传时立即获取概要**：

```python
# 伪代码
async def on_file_uploaded(file_id: str, file_path: str):
    # 1. 获取解析后的文本（从kb_chunks chunk_index=0）
    full_text = kb_store.get_parsed_text(file_id)
    
    # 2. 生成简短摘要（前500字或LLM生成）
    summary = generate_summary(full_text[:500])
    
    # 3. 识别文件类型和可能的意图
    file_type = identify_file_type(filename, full_text)
    suggestions = suggest_actions(file_type)
    
    # 4. 构建问候消息
    greeting = format_greeting(filename, file_size, summary, suggestions)
    
    # 5. 保存文件上下文到session
    session.set_pending_file(file_id, file_path, summary)
    
    # 6. 发送问候，等待用户回复
    send_message(greeting)
```

### 3.3 调查问题

**Q1**: 文件解析完成后，如何通知"文件已就绪"？

**当前流程**：
```
KBUploadTool.execute() (async)
    → asyncio.to_thread(process_file, ...)
    → 返回 ProcessResult
    → 返回给 _auto_upload_to_kb()
    → 返回结果字符串
    → 拼接到 content
    → 发送给 Agent
```

**B2需求**：
- 文件解析完成后，不要立即让Agent处理
- 而是触发"问候+意图识别"流程
- 需要插入一个中间层

**可能方案**：

**方案A**: 在`_auto_upload_to_kb()`中拦截
```python
async def _auto_upload_to_kb(self, file_path, sender_id):
    result = await self._kb_tool.execute(file_path)
    
    if result.success:  # 文件入库成功
        file_id = extract_file_id(result)
        
        # B2: 触发意图识别流程
        await self._send_file_greeting(sender_id, file_id, file_path)
        
        # 不返回KB结果字符串，避免Agent处理
        return None  # 或返回特殊标记
    else:
        return f"⚠️ KB upload failed: {result.error}"
```

**方案B**: 在Agent层面拦截
```python
# 在 AgentLoop._dispatch_inbound() 中
if msg.msg_type == "file" and kb_upload_just_completed(msg):
    # 不进入正常Agent处理
    await self._handle_file_greeting(msg)
    return
```

**方案C**: 使用事件系统
```python
# KBUploadTool完成后发布事件
bus.publish_event(FileIndexedEvent(file_id=file_id, ...))

# FeishuChannel订阅事件
async def on_file_indexed(event: FileIndexedEvent):
    await self._send_file_greeting(event)
```

---

## 四、用户的下一条消息如何关联到待处理文件（✅ 已调查）

### 4.1 Session机制 ✅

**Session结构已确认**：

```python
@dataclass
class Session:
    key: str  # "feishu:ou_xxx" (私聊) 或 "feishu:chat_id:message_id" (群聊topic)
    messages: list[dict[str, Any]]  # 对话历史
    metadata: dict[str, Any]  # ← 可扩展，用于存储pending_file
    # ...
```

**metadata读写**：

```python
# 读取
metadata = await session_state.read_metadata(key)
# 返回: {"key": "feishu:ou_xxx", "metadata": {...}}

# 修改（原子化）
await session_state.mutate_metadata(
    key,
    lambda meta: meta.update({"pending_file": file_info}) or None
)
```

**并发安全**：
- ✅ `threading.RLock()` 保护内存缓存
- ✅ SQLite事务保护持久化
- ✅ 单writer线程池（串行写入）
- ✅ generation机制防止冲突

### 4.2 上下文关联策略 ✅ 已确定

**策略：Session metadata存储pending_file**

```python
# 文件上传后，写入session metadata
session.metadata["pending_file"] = {
    "file_id": "34d9a15c-...",
    "file_path": "/data/kb/files/hash_12345678.pdf",
    "filename": "产品需求文档.pdf",
    "file_size": 245678,
    "upload_time": "2026-10-30T15:30:00",
    "summary": "这是一个产品需求文档，包含...",  # 可选
}

# 用户下一条消息
user: "帮我总结一下"

# Agent处理时，从session metadata读取pending_file
pending_file = session.metadata.get("pending_file")
if pending_file:
    # 构建context，包含文件信息
    context = {
        "file_id": pending_file["file_id"],
        "filename": pending_file["filename"],
        ...
    }
    # 调用Agent处理
    
# 处理完成后清理
session.metadata["pending_file"] = None
```

**优点**：
- ✅ 天然的用户隔离（不同session）
- ✅ 并发安全（Session机制保证）
- ✅ 持久化（重启不丢失）
- ✅ 简单直接

### 4.3 Agent Context注入点

**需要调查**：Agent如何获取session metadata并注入到LLM context

**预期位置**：
- `nanobot/agent/loop.py` - session获取
- `nanobot/agent/runner.py` - context构建
- 可能需要在构建prompt时添加system message

### 4.4 意图识别后的清理

**清理时机**：
1. **用户回复意图后** - Agent处理完成，清理pending_file
2. **超时清理** - 5分钟无回复，自动清理
3. **新文件上传** - 覆盖旧的pending_file

**实现**：
```python
# Agent处理完成后
await session_state.mutate_metadata(
    session_key,
    lambda meta: meta.pop("pending_file", None) or None
)
```

**边界情况处理**：

| 场景 | 处理方式 |
|------|---------|
| 用户上传文件但不回复 | 超时清理（5分钟） |
| 用户快速上传多个文件 | 覆盖pending_file，只保留最新 |
| 用户在群聊上传文件 | **待定**：群聊是否支持意图识别 |
| 服务重启 | pending_file持久化，不丢失 |

---

## 五、多用户并发时如何避免意图串线（✅ 已确认）

### 5.1 当前并发隔离机制 ✅

**Session Key隔离**：

```python
# 私聊 (p2p)
session_key = None  # 使用默认："{channel}:{sender_id}"
# 实际: "feishu:ou_abc123"

# 群聊 (group) - topic_isolation=True
session_key = f"{channel}:{chat_id}:{root_id or message_id}"
# 实际: "feishu:oc_xyz789:om_msg456"

# 群聊 (group) - topic_isolation=False
session_key = f"{channel}:{chat_id}"
# 实际: "feishu:oc_xyz789"
```

**隔离级别验证**：
- ✅ 不同用户的私聊：完全隔离（不同session key）
- ✅ 同一用户的私聊：共享session（相同session key）
- ✅ 群聊topic：根据配置隔离
- ✅ **pending_file存储在session metadata中 → 天然隔离**

### 5.2 并发场景分析 ✅

#### 场景1: 同一用户快速上传多个文件

```
T1: 用户上传 file1.pdf
    → session.metadata["pending_file"] = {file_id: "1", ...}
    → 发送问候1
    
T2: 用户上传 file2.pdf（在回复问候1之前）
    → session.metadata["pending_file"] = {file_id: "2", ...}  # 覆盖
    → 发送问候2
    
T3: 用户："帮我总结"
    → 总结 file2.pdf（最新的）
```

**策略：只保留最新的pending_file**
- ✅ 简单直接
- ✅ 符合用户预期（最新的文件）
- ⚠️ 可选优化：提示用户"覆盖了之前的文件"

#### 场景2: 不同用户同时上传 ✅

```
用户A (session_key="feishu:ou_aaa")
    → session_A.metadata["pending_file"] = fileA
    
用户B (session_key="feishu:ou_bbb")
    → session_B.metadata["pending_file"] = fileB
    
用户A："帮我总结" → 总结 fileA ✅
用户B："帮我总结" → 总结 fileB ✅
```

**结论：天然隔离，无串线风险**

#### 场景3: 群聊中多人上传

**问题：群聊session_key相同**

```
群聊G (session_key="feishu:oc_xxx")

用户A上传 fileA.pdf
    → session.metadata["pending_file"] = {file_id: "A", sender: "ou_aaa"}
    
用户B上传 fileB.pdf
    → session.metadata["pending_file"] = {file_id: "B", sender: "ou_bbb"}  # 覆盖
    
用户A："帮我总结"
    → 可能总结 fileB（错误！）
```

**解决方案**：

**方案A：群聊不支持文件意图识别（推荐）**
- ✅ 简单，无风险
- ✅ 群聊通常用于讨论，不太需要文件意图识别
- ✅ 文件仍然自动入库，可以通过KBSearchTool检索

**方案B：按用户维护pending_files字典**
```python
session.metadata["pending_files"] = {
    "ou_aaa": {"file_id": "A", ...},
    "ou_bbb": {"file_id": "B", ...}
}

# 用户回复时，根据sender_id查找
pending_file = session.metadata["pending_files"].get(sender_id)
```
- ⚠️ 复杂
- ⚠️ 清理机制更复杂

**方案C：群聊使用topic_isolation**
```python
# 每个文件消息创建独立的topic
session_key = f"feishu:{chat_id}:{file_message_id}"
```
- ⚠️ 改变现有配置
- ⚠️ 可能不符合用户习惯

**决策：采用方案A（群聊不支持）**

### 5.3 并发安全检查清单 ✅

| 检查项 | 状态 | 说明 |
|--------|------|------|
| pending_file存储位置 | ✅ | session.metadata |
| pending_file的并发访问保护 | ✅ | Session机制自带锁 |
| 用户快速上传多个文件 | ✅ | 覆盖，保留最新 |
| 不同用户并发上传 | ✅ | 不同session，天然隔离 |
| 群聊场景 | ✅ | 不支持意图识别 |
| pending_file超时清理 | 🔄 | 需要实现 |
| B1欢迎语和B2交互 | 🔄 | 需要验证 |

### 5.4 并发安全总结

**✅ 无并发风险**：
- Session机制提供天然隔离
- metadata读写有锁保护
- 不同用户不会串线

**⚠️ 需要注意**：
- 同一用户快速上传：只保留最新
- 群聊场景：不支持意图识别
- 超时清理：需要实现定时任务

---

## 六、待深入调查的模块

### 6.1 Session管理 ✅ 已调查

**文件**：
- `nanobot/session/manager.py` - Session类定义
- `nanobot/session/state.py` - SessionState（读写操作）
- `nanobot/session/sqlite_store.py` - 持久化存储

**Session结构**：
```python
@dataclass
class Session:
    key: str  # namespace:identity (如 "feishu:ou_xxx")
    messages: list[dict[str, Any]]  # 对话历史
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]  # ← 可扩展的元数据字典
    last_consolidated: int
    provider_state: ProviderConversationState | None
    policy: SessionPolicy
    # ... 其他内部字段
```

**metadata读写方式**：

1. **读取metadata**：
```python
# 同步读取（带缓存）
metadata = session_state.peek_metadata(key)

# 异步读取（从存储）
metadata_dict = await session_state.read_metadata(key)
# 返回: {"key": key, "metadata": {...}}
```

2. **修改metadata**：
```python
# 原子化修改
result, updated_metadata = await session_state.mutate_metadata(
    key,
    lambda meta: meta.update({"pending_file": {...}}) or True,
    create_if_missing=False
)
```

3. **打开session并附加metadata**：
```python
generation, created = await session_state.open_with_metadata(
    key,
    updates={"pending_file": {...}},
    expected_generation=None
)
```

**并发安全机制**：
- ✅ 使用 `threading.RLock()` 保护内存缓存
- ✅ SQLite事务保护持久化
- ✅ 单独的writer线程池（max_workers=1）
- ✅ 多个reader线程池（并发读取）
- ✅ generation机制防止冲突

**生命周期**：
- Session按需创建（首次消息）
- 内存缓存（LRU）
- 持久化到SQLite
- 可以显式删除或归档

### 6.2 Agent Context构建

**文件**：
- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/context.py`（如果存在）

**调查内容**：
- [ ] Agent如何获取context
- [ ] conversation history的构建
- [ ] 如何注入额外的context（如pending_file）
- [ ] Tool调用的上下文传递

### 6.3 KB工具集成

**文件**：
- `nanobot/agent/tools/kb_search.py`
- `nanobot/agent/tools/kb_upload.py`

**调查内容**：
- [ ] KBSearchTool的工作机制
- [ ] 如何将file_id传递给tool
- [ ] Tool如何访问kb_store
- [ ] Tool result如何返回给Agent

---

## 七、初步架构设计方向

### 7.1 核心流程改造

**当前**：
```
文件上传 → 自动入库 → 拼接content → Agent处理
```

**B2目标**：
```
文件上传 
    → 自动入库
    → 生成摘要
    → 发送问候+意图选项
    → 保存pending_file到session
    → 等待用户回复
    ↓
用户回复意图
    → 从session获取pending_file
    → 构建Agent context（包含文件上下文）
    → Agent执行任务
    → 清理pending_file
```

### 7.2 关键设计点

1. **何时触发意图识别**
   - 在 `_auto_upload_to_kb()` 返回后
   - 检查文件是否成功入库
   - 如果成功，触发意图识别流程

2. **如何阻止Agent立即处理**
   - 不将文件上传消息发送给Agent
   - 或发送一个特殊标记，Agent识别后跳过

3. **如何存储pending_file**
   - 使用session metadata
   - 结构：`{"file_id", "file_path", "summary", "upload_time"}`

4. **如何关联用户下一条消息**
   - 检查session metadata中的pending_file
   - 如果存在，注入到Agent context

5. **如何清理pending_file**
   - Agent处理完成后清理
   - 或设置超时（如5分钟）

6. **群聊如何处理**
   - 选项A：群聊不支持意图识别（简单）
   - 选项B：按用户维护pending_files字典

### 7.3 实施优先级

**P0 - 必需**：
- [ ] 调查Session metadata读写机制
- [ ] 设计pending_file存储结构
- [ ] 实现意图识别消息发送
- [ ] 实现用户回复关联逻辑
- [ ] 私聊场景验证

**P1 - 重要**：
- [ ] 文件摘要生成
- [ ] 多文件并发处理
- [ ] pending_file超时清理

**P2 - 可选**：
- [ ] 群聊场景支持
- [ ] 意图识别UI优化（交互式按钮）
- [ ] 更智能的意图推荐

---

## 八、下一步行动

### 第一阶段：架构调查 ✅ 已完成

1. ✅ **调查文件消息如何进入Agent**
   - 完整调用链已明确
   - content和media传递机制已清楚

2. ✅ **调查Session机制**
   - Session.metadata读写方式已验证
   - 并发安全机制已确认
   - 隔离级别已验证

3. ✅ **调查并发安全性**
   - 不同用户隔离机制已确认
   - 同用户多文件策略已确定
   - 群聊场景决策已做出

### 第二阶段：Agent Context调查 🔄 进行中

4. **调查Agent Context构建** ⏭️ 下一步
   - [ ] 阅读 `nanobot/agent/loop.py` 中的session使用
   - [ ] 阅读 `nanobot/agent/runner.py` 中的context构建
   - [ ] 确定pending_file注入点
   - [ ] 验证如何传递到LLM prompt

5. **调查KB工具集成**
   - [ ] 查看KBSearchTool如何工作
   - [ ] 确定如何传递file_id到tool
   - [ ] 验证tool如何访问kb_store

### 第三阶段：详细设计

6. **设计意图识别消息格式**
   - [ ] 消息模板设计
   - [ ] 意图选项设计
   - [ ] 文件摘要生成策略

7. **设计用户回复识别逻辑**
   - [ ] 意图识别的规则
   - [ ] 模糊匹配策略
   - [ ] 错误处理

8. **设计pending_file数据结构**
   ```python
   {
       "file_id": str,
       "file_path": str,
       "filename": str,
       "file_size": int,
       "file_type": str,
       "upload_time": str,
       "summary": str | None,
       "sender_id": str,  # 用于验证
   }
   ```

9. **设计清理机制**
   - [ ] 处理后立即清理
   - [ ] 超时清理（5分钟）
   - [ ] 新文件覆盖旧文件

### 第四阶段：技术方案文档

10. **编写B2技术方案**
    - [ ] 详细的实施步骤
    - [ ] 代码修改点清单
    - [ ] 数据流图
    - [ ] 时序图

11. **编写测试计划**
    - [ ] 单元测试用例
    - [ ] 集成测试场景
    - [ ] 边界情况测试
    - [ ] 并发测试

### 第五阶段：编码实现

12. **实现核心功能**
    - [ ] 修改_auto_upload_to_kb()
    - [ ] 实现_send_file_greeting()
    - [ ] 实现pending_file读写
    - [ ] 实现Agent context注入
    - [ ] 实现清理机制

13. **编写测试**
    - [ ] 私聊场景测试
    - [ ] 多文件场景测试
    - [ ] 并发测试
    - [ ] 超时测试

14. **验证与调优**
    - [ ] 手工测试
    - [ ] 性能测试
    - [ ] 用户体验验证

---

## 十、当前状态

**阶段**: 🔄 第二阶段（Agent Context调查）

**进度**: 
- ✅ 第一阶段：架构调查（100%）
- 🔄 第二阶段：Agent Context调查（0%）
- ⏸️ 第三阶段：详细设计（未开始）
- ⏸️ 第四阶段：技术方案（未开始）
- ⏸️ 第五阶段：编码实现（未开始）

**下一步**: 调查Agent Context构建机制

**预计时间**: 
- Agent Context调查：1-2小时
- 详细设计：2-3小时
- 技术方案文档：2-3小时
- 编码实现：4-6小时
- 测试验证：2-3小时

**总计**: 约12-18小时

---

## 十一、架构调查文档版本

**版本**: v1.1  
**最后更新**: 2026-10-30  
**状态**: 第一阶段完成  
**调查人**: AI Assistant

### 变更历史

- v1.0 (2026-10-30): 初始版本，确定调查目标
- v1.1 (2026-10-30): 完成第一阶段调查
  - ✅ 文件消息进入Agent路径
  - ✅ Session机制验证
  - ✅ 并发安全性分析
  - ✅ 关键设计决策

### 下一版本计划

- v1.2: Agent Context调查完成
- v1.3: 详细设计完成
- v2.0: 技术方案文档完成

---

## 九、架构调查总结

### 已确认 ✅

✅ **文件消息进入Agent的完整路径已清晰**
- 从FeishuChannel到MessageBus到AgentLoop
- content包含文件路径和KB结果
- media包含原始文件路径
- 完整调用链已文档化

✅ **Session metadata机制已验证**
- Session.metadata是dict，可自由扩展
- 提供原子化的读写API（mutate_metadata）
- 并发安全（RLock + SQLite事务）
- 持久化，重启不丢失

✅ **并发隔离机制已确认**
- 不同用户私聊：不同session key，天然隔离
- 同一用户多文件：覆盖策略，保留最新
- 群聊场景：不支持意图识别（避免复杂性）

✅ **B3实现不影响B2开发**
- 进度消息独立
- KB自动上传可保留
- 可在KB上传完成后插入意图识别

### 待实施 🔄

🔄 **文件解析结果的传递**
- 需要在KB上传完成后获取file_id
- 从kb_chunks读取解析文本或生成摘要
- 构建意图识别消息

🔄 **Agent context注入机制**
- 需要调查Agent如何获取session metadata
- 确定在哪里注入pending_file信息
- 可能需要修改prompt构建逻辑

🔄 **意图识别消息的发送**
- 在_auto_upload_to_kb()中拦截
- 发送格式化的问候消息
- 保存pending_file到session metadata

🔄 **用户回复的处理**
- 识别用户意图
- 从session获取pending_file
- 注入到Agent context
- 执行任务后清理

🔄 **超时清理机制**
- 定时任务或lazy清理
- 5分钟无回复自动清理

### 风险已评估 ⚠️

⚠️ **与B1欢迎语的交互**
- 文件上传时不应触发欢迎语（用户已经在对话中）
- B1逻辑已处理（只在首次交互触发）
- **结论**：无冲突

⚠️ **与B4 reaction的交互**
- 文件消息是否添加reaction？
- **建议**：保留reaction（用户体验连贯）

⚠️ **性能影响**
- 生成文件摘要可能耗时
- **缓解**：使用前N字符或异步生成
- **评估**：影响可控（秒级）

### 关键设计决策 ✅

| 决策点 | 选择 | 理由 |
|--------|------|------|
| pending_file存储 | session.metadata | 天然隔离，并发安全，持久化 |
| 多文件处理 | 覆盖，保留最新 | 简单，符合预期 |
| 群聊支持 | 不支持意图识别 | 避免复杂性，群聊场景需求低 |
| 文件摘要 | 前500字或简单提取 | 快速响应，避免LLM调用 |
| 清理机制 | 处理后清理+超时清理 | 双重保险 |
| Agent注入 | system message或context | 待验证具体实现 |

---

**调查人**: AI Assistant  
**调查日期**: 2026-10-30  
**状态**: 🔍 **第一阶段完成，继续深入Session机制**

---

## 附录：关键代码位置

| 模块 | 文件 | 关键函数/类 |
|------|------|------------|
| 飞书消息接收 | `nanobot/channels/feishu/runtime.py` | `_on_message()` |
| 文件下载 | `nanobot/channels/feishu/runtime.py` | `_download_and_save_media()` |
| KB自动上传 | `nanobot/channels/feishu/runtime.py` | `_auto_upload_to_kb()` |
| 消息路由 | `nanobot/channels/base.py` | `_handle_message()` |
| 消息总线 | `nanobot/bus/queue.py` | `MessageBus` |
| Agent循环 | `nanobot/agent/loop.py` | `AgentLoop.run()` |
| 消息分发 | `nanobot/agent/loop.py` | `_dispatch_inbound()` |
| KB工具 | `nanobot/agent/tools/kb_upload.py` | `KBUploadTool` |
| KB处理器 | `nanobot/kb/processor.py` | `process_file()` |
| Session管理 | `nanobot/session/manager.py` | `SessionManager` |
| Session存储 | `nanobot/session/sqlite_store.py` | `SqliteSessionStore` |
