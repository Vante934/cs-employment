# F06-B-B2-D：文件意图确认消息 - 定点调查报告

**调查日期**: 2026-10-30  
**状态**: ✅ 调查完成  
**调查人**: AI Assistant

---

## 执行摘要

### 核心发现

1. ✅ **消息链路明确** - _on_message → content拼接 → _handle_message → MessageBus
2. ⚠️ **阻断点确定** - 需要在content拼接后、_handle_message调用前处理
3. ✅ **B3不受影响** - 进度消息通过progress_callback独立发送
4. 🎯 **关键修改点** - runtime.py:3119行，content构建完成后

### 推荐方案

**方案A**: 在私聊文件消息时，阻止content进入Agent
- 不追加kb_result到content
- 发送意图确认消息
- 用户回复后才进入正常Agent流程

---

## 一、当前文件消息处理链路

### 1.1 完整调用链

```
用户发送文件 (飞书)
    ↓
FeishuChannel._on_message_sync() [WebSocket线程]
    ↓
asyncio.run_coroutine_threadsafe(_on_message())
    ↓
_on_message() [runtime.py:3050+]
    ├─ B1: 检查并发送欢迎语
    ├─ B4: 添加Reaction
    ├─ 解析消息内容
    └─ 处理文件 (msg_type == "file")
        ↓
    _download_and_save_media()
        ├─ 下载文件
        └─ 返回 file_path, content_text
            ↓
    _auto_upload_to_kb(file_path, sender_id)
        ├─ B3: KBUploadTool.execute()
        │   └─ progress_callback → 发送4条进度消息
        └─ 返回 kb_result (成功/重复/失败消息)
            ↓
    【B2-A】提取file_id，保存pending_file
        ↓
    【当前】content_text = f"{content_text}\n{kb_result}"  ← 关键点！
        ↓
    content_parts.append(content_text)
        ↓
    content = "\n".join(content_parts)  [runtime.py:3162]
        ↓
    await self._handle_message(
        sender_id=sender_id,
        chat_id=reply_to,
        content=content,  ← kb_result已包含在content中
        media=media_paths,
        ...
    )  [runtime.py:3189-3203]
        ↓
    BaseChannel._handle_message() [base.py:261]
        ├─ 权限检查
        └─ 构建InboundMessage
            ↓
    await self.bus.publish_inbound(msg)  [base.py:335]
        ↓
    MessageBus → AgentLoop → LLM处理
```

### 1.2 关键代码位置

**文件处理** (runtime.py:3095-3119):
```python
if msg_type == "file":
    kb_result = await self._auto_upload_to_kb(file_path, sender_id)
    
    # B2-A: Extract file_id and save pending_file
    if kb_result:
        result_str = str(kb_result)
        file_id = self._extract_file_id(result_str)
        
        if file_id:
            await self._save_pending_file_to_session(
                sender_id=sender_id,
                file_id=file_id,
                chat_type=chat_type,
            )
        
        # 【关键】Append KB result to content
        content_text = f"{content_text}\n{kb_result}"  # ← 问题所在
```

**content构建** (runtime.py:3162):
```python
content = "\n".join(content_parts) if content_parts else ""
```

**发送到Agent** (runtime.py:3189-3203):
```python
await self._handle_message(
    sender_id=sender_id,
    chat_id=reply_to,
    content=content,  # ← kb_result在这里
    media=media_paths,
    ...
)
```

---

## 二、B3进度反馈机制

### 2.1 B3实现方式

**代码位置**: runtime.py:1957-1969

```python
# Create progress callback for this upload
def progress_callback(stage: str, message: str):
    """Progress callback called from worker thread."""
    if sender_id and self._loop:
        try:
            asyncio.run_coroutine_threadsafe(
                self._send_progress_message(sender_id, message),
                self._loop
            )
        except Exception as e:
            self.logger.warning(f"Failed to send progress message: {e}")
```

**传递给KBUploadTool** (runtime.py:1974-1979):
```python
kb_tool = KBUploadTool(
    kb_store=self._kb_store,
    media_dir=media_dir,
    kb_files_dir=kb_files_dir,
    index_manager=self._index_manager,
    progress_callback=progress_callback if sender_id else None,
)
```

**KBUploadTool调用** (kb_upload.py:281-295):
```python
if self._progress_callback:
    try:
        self._progress_callback("start", "正在处理文件...")
    except Exception:
        pass

process_result = await asyncio.to_thread(
    process_file,
    file_id,
    self._kb_store,
    index_manager,
    False,
    self._progress_callback  # ← 传递给processor
)
```

### 2.2 B3独立性

**关键发现**: ✅ **B3完全独立**

- 进度消息通过`progress_callback`直接发送
- 不依赖`content`或`_handle_message`
- 不通过MessageBus
- 与`kb_result`返回值无关

**结论**: ✅ **修改content处理不会影响B3**

---

## 三、阻断KB结果进入Agent的方案

### 3.1 问题分析

**当前问题**:
```python
content_text = f"{content_text}\n{kb_result}"
```

**结果**:
- `content`包含KB结果（如"✅ File added to knowledge base successfully"）
- Agent收到这个content作为用户问题
- Agent可能回答"好的，文件已添加"等无意义回复

**预期**:
- 用户发送文件后，不应立即进入Agent
- 应先发送意图确认消息
- 等待用户回复后，再进入Agent流程

### 3.2 方案A：条件性阻断content（推荐）

**修改位置**: runtime.py:3104-3119

**实现**:
```python
if msg_type == "file":
    kb_result = await self._auto_upload_to_kb(file_path, sender_id)
    
    # B2-A: Extract file_id and save pending_file
    if kb_result:
        result_str = str(kb_result)
        file_id = self._extract_file_id(result_str)
        
        if file_id and chat_type == "p2p":
            # B2-D: 私聊文件上传成功
            await self._save_pending_file_to_session(...)
            
            # 发送意图确认消息
            await self._send_file_greeting(sender_id, file_id)
            
            # 不追加KB结果到content，直接返回（不进入Agent）
            return
        
        # 群聊或file_id提取失败：保持原有行为
        if file_id:
            await self._save_pending_file_to_session(...)
        
        content_text = f"{content_text}\n{kb_result}"
```

**优点**:
- ✅ 精确阻断文件消息进入Agent
- ✅ 不影响群聊
- ✅ 不影响file_id提取失败的情况
- ✅ 逻辑清晰

**缺点**:
- ⚠️ 需要early return，改变控制流

### 3.3 方案B：在_handle_message前判断

**修改位置**: runtime.py:3162后

**实现**:
```python
content = "\n".join(content_parts) if content_parts else ""

# B2-D: Check if this is a file upload that needs intent confirmation
if msg_type == "file" and chat_type == "p2p" and has_pending_file:
    # 已发送意图确认消息，不进入Agent
    return

if not content and not media_paths:
    return

await self._handle_message(...)
```

**优点**:
- ✅ 统一的阻断点
- ✅ 不破坏content构建逻辑

**缺点**:
- ⚠️ 需要检查pending_file状态
- ⚠️ msg_type需要传递到这里

### 3.4 方案对比

| 维度 | 方案A | 方案B |
|------|-------|-------|
| 修改位置 | 文件处理块内 | content构建后 |
| 复杂度 | 低 | 中 |
| 清晰度 | 高 | 中 |
| 维护性 | 好 | 一般 |

**推荐**: ✅ **方案A**

---

## 四、意图确认消息设计

### 4.1 消息内容

**基础版本**:
```
📄 收到文件：产品需求文档.pdf (245 KB)

我可以帮你：
• 总结文档要点
• 提取关键信息
• 回答文档相关问题
• 分析文档内容

请告诉我你想做什么，或者直接提问。
```

**有摘要版本**:
```
📄 收到文件：产品需求文档.pdf (245 KB)

📝 内容预览：
这是一份关于移动应用产品需求的文档，包含用户画像、功能列表、技术架构等章节...

我可以帮你：
• 总结文档要点
• 提取关键信息
• 回答文档相关问题
• 分析文档内容

请告诉我你想做什么，或者直接提问。
```

### 4.2 发送时机

**时机**: 文件上传成功且pending_file保存成功后

**位置**: runtime.py:3104-3119

**顺序**:
1. KB上传成功 (B3进度消息已发送)
2. 提取file_id
3. 保存pending_file
4. **发送意图确认消息** ← B2-D
5. return（不进入Agent）

### 4.3 实现方法

**新增方法**: `_send_file_greeting()`

```python
async def _send_file_greeting(
    self,
    sender_id: str,
    file_info: dict[str, Any],
) -> None:
    """Send file greeting message after upload.
    
    Args:
        sender_id: Feishu sender ID
        file_info: File information dict or file_id
    """
    # 如果传入file_id，从KB查询
    if isinstance(file_info, str):
        file_id = file_info
        if self._kb_store:
            file_record = self._kb_store.get_file(file_id)
            if file_record:
                file_info = file_record
            else:
                return
        else:
            return
    
    # 构建消息
    filename = file_info.get("filename", "文件")
    file_size = file_info.get("file_size", 0)
    size_kb = file_size / 1024
    
    if size_kb < 1024:
        size_str = f"{size_kb:.1f} KB"
    else:
        size_str = f"{size_kb / 1024:.1f} MB"
    
    message = f"""📄 收到文件：{filename} ({size_str})

我可以帮你：
• 总结文档要点
• 提取关键信息
• 回答文档相关问题
• 分析文档内容

请告诉我你想做什么，或者直接提问。"""
    
    # 发送消息
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._send_message_sync,
            "open_id",
            sender_id,
            "text",
            message,
        )
    except Exception as e:
        self.logger.warning("Failed to send file greeting: {}", e)
```

---

## 五、发送失败处理

### 5.1 场景分析

**场景1**: 意图消息发送失败

**当前状态**:
- pending_file已保存 ✅
- KB文件已入库 ✅
- 意图消息未送达 ❌

**用户体验**:
- 用户看到B3进度消息
- 用户没有看到意图确认
- 用户可能主动提问

**处理方式**: 
- 不阻断消息流程
- pending_file保留
- 用户后续消息时，Agent通过B2-C的RuntimeContext知道有文件

**实现**:
```python
try:
    await self._send_file_greeting(sender_id, file_id)
    return  # 成功发送，阻断进入Agent
except Exception as e:
    self.logger.warning("File greeting failed, fallback to normal flow: {}", e)
    # 失败：继续原有流程（不return）
```

### 5.2 fallback策略

**策略**: 发送失败时，允许消息正常流入Agent

**理由**:
1. pending_file已保存，Agent可通过B2-C获取
2. 用户收到B3进度消息，知道文件已处理
3. 比完全静默更好

**代码**:
```python
greeting_sent = False
try:
    await self._send_file_greeting(sender_id, file_id)
    greeting_sent = True
except Exception as e:
    self.logger.warning("File greeting failed: {}", e)

if greeting_sent:
    return  # 成功，不进入Agent
else:
    # 失败，继续原有流程
    content_text = f"{content_text}\n{kb_result}"
```

---

## 六、同用户新文件处理

### 6.1 场景

```
T1: 用户上传file1.pdf
    → pending_file = {file_id: "1", filename: "file1.pdf"}
    → 意图消息: "收到文件：file1.pdf"

T2: 用户上传file2.docx (未回复file1)
    → pending_file = {file_id: "2", filename: "file2.docx"}  # 覆盖
    → 意图消息: "收到文件：file2.docx"
```

### 6.2 是否需要特殊提示？

**选项A**: 不提示
```
📄 收到文件：file2.docx (100 KB)

我可以帮你：
...
```

**选项B**: 提示覆盖
```
📄 收到新文件：file2.docx (100 KB)
（已替换之前的file1.pdf）

我可以帮你：
...
```

**推荐**: **选项A** (B2第一版)

**理由**:
1. 简化实现
2. 用户一般不会频繁切换文件
3. 可在后续版本优化

---

## 七、群聊处理

### 7.1 群聊场景

**B2第一版决策**: ❌ 不支持群聊文件意图交互

**行为**:
- 群聊上传文件 → KB入库（B3进度） → ❌ 不保存pending_file → ❌ 不发送意图消息
- KB结果仍追加到content → 进入Agent

**代码**:
```python
if file_id and chat_type == "p2p":
    await self._save_pending_file_to_session(...)
    await self._send_file_greeting(sender_id, file_id)
    return  # 阻断

# 群聊：继续原有流程
if file_id:
    await self._save_pending_file_to_session(...)  # 会因chat_type跳过

content_text = f"{content_text}\n{kb_result}"
```

---

## 八、与B2-C/B2-B的关系

### 8.1 B2-C (Agent Context注入)

**依赖**: B2-D完成后

**关系**:
- B2-D: 发送意图消息，等待用户回复
- B2-C: 用户回复时，注入RuntimeContextBlock

**无冲突**: ✅ 不同阶段

### 8.2 B2-B (KB工具)

**依赖**: 独立

**关系**:
- B2-D: 意图确认
- B2-B: 工具实现

**无冲突**: ✅ 独立批次

---

## 九、实施方案总结

### 9.1 核心修改

**文件**: `nanobot/channels/feishu/runtime.py`

**修改点1**: 新增`_send_file_greeting()` 方法

**修改点2**: 修改文件处理逻辑 (行3104-3119)
```python
if msg_type == "file":
    kb_result = await self._auto_upload_to_kb(file_path, sender_id)
    
    if kb_result:
        result_str = str(kb_result)
        file_id = self._extract_file_id(result_str)
        
        if file_id:
            await self._save_pending_file_to_session(
                sender_id=sender_id,
                file_id=file_id,
                chat_type=chat_type,
            )
            
            # B2-D: 私聊发送意图消息
            if chat_type == "p2p":
                try:
                    await self._send_file_greeting(sender_id, file_id)
                    return  # 成功，不进入Agent
                except Exception as e:
                    self.logger.warning("File greeting failed, fallback: {}", e)
        
        # 群聊或发送失败：保持原有行为
        content_text = f"{content_text}\n{kb_result}"
```

### 9.2 测试要点

1. ✅ 私聊文件上传后不进入Agent
2. ✅ 意图确认消息正确发送
3. ✅ B3进度消息不受影响
4. ✅ 群聊保持原有行为
5. ✅ 意图消息发送失败时fallback
6. ✅ B1欢迎语不受影响
7. ✅ B4 Reaction不受影响

### 9.3 工作量估算

**预估**: 1-2小时

**分解**:
- 实现_send_file_greeting(): 0.5h
- 修改文件处理逻辑: 0.5h
- 测试: 0.5-1h

---

## 十、风险与限制

### 10.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 意图消息发送失败 | 中 | 中 | fallback到原有流程 |
| 破坏B3进度 | 低 | 高 | B3独立，无影响 |
| 群聊误阻断 | 低 | 中 | chat_type检查 |
| early return副作用 | 低 | 中 | 充分测试 |

### 10.2 限制

1. **群聊不支持** - 设计决策
2. **无覆盖提示** - 第一版简化
3. **发送失败fallback** - 用户体验可能困惑

---

## 十一、调查结论

### 11.1 技术可行性

✅ **完全可行**

**理由**:
- 消息链路清晰
- 阻断点明确
- B3完全独立
- 修改范围可控

### 11.2 实施方案

✅ **方案A: 条件性阻断content**

**修改**:
1. 新增`_send_file_greeting()`
2. 修改文件处理逻辑，私聊时early return

### 11.3 下一步

✅ **可以进入编码实施**

**准备**:
- 调查完整
- 方案明确
- 风险可控
- 测试清晰

---

**调查完成时间**: 2026-10-30  
**调查人**: AI Assistant  
**状态**: ✅ **调查完成，可以进入B2-D编码阶段**
