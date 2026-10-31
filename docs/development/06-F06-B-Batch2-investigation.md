# F06-B Batch-2：文件能力边界与多文件上下文 - 调查报告

**调查日期**: 2026-10-30  
**性质**: 只读源码调查  
**状态**: ✅ 完成  

---

## 一、关键发现总结

### 1.1 已证实的严重问题

#### 问题1：欢迎消息格式错误（230001根因）⭐⭐⭐⭐⭐

**代码位置**: `nanobot/channels/feishu/runtime.py:2289`

```python
# ❌ 错误：直接传入纯文本
self._send_message_sync(rid_type, sender_id, "text", welcome_text)

# ✅ 正确：应该传入JSON字符串
content = json.dumps({"text": welcome_text}, ensure_ascii=False)
self._send_message_sync(rid_type, sender_id, "text", content)
```

**飞书API期望**: `content`必须是JSON字符串 `"{\"text\":\"消息内容\"}"`

**影响**: 欢迎消息发送失败，返回230001错误

#### 问题2：新文件失败后不清理旧pending_file ⭐⭐⭐⭐⭐

**代码位置**: `nanobot/channels/feishu/runtime.py:3171-3193`

**时序示例**:
```
T1: 用户发送file1.pdf → pending_file = {file_id: abc}
T2: 用户发送file2.pptx → 格式不支持，返回None
    ❌ pending_file仍然 = {file_id: abc}（未清理）
T3: 用户问"分析这个文件"
T4: Agent分析file1，不是file2 ❌
```

**根因**: `_save_pending_file_to_session()`只在成功时调用，失败时无清理

**影响**: 用户期望与实际行为不一致，Agent回答错误文件

#### 问题3：不支持格式静默跳过 ⭐⭐⭐⭐

**代码位置**: `nanobot/channels/feishu/runtime.py:2095-2097`

```python
SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}
if ext not in SUPPORTED_EXTS:
    return None  # ← 静默返回，无日志，无用户通知
```

**实际行为**:
- PPTX被下载到媒体目录（占用空间）
- 无控制台日志
- 无用户反馈
- 消息仍发送给Agent

**影响**: 用户不知道文件未处理

#### 问题4：read_file可以绕过KB权限 ⭐⭐⭐⭐

**代码位置**: `nanobot/agent/tools/filesystem.py:264`

**实际发生**:
```
Tool call: read_file({
    "path": "C:\\Users\\...\\media\\feishu\\文件.pdf",
    "pages": "1-10"
})
```

**分析**:
- ✅ read_file可以读取媒体目录
- ❌ read_file不检查pending_file
- ❌ 绕过KB权限验证

**影响**: 安全隐患，可能访问其他用户文件

#### 问题5：欢迎消息缺少能力边界说明 ⭐⭐⭐

**当前内容**:
```
你好，我是小文，很高兴认识你。

我可以帮你：
1. 阅读和理解文件，提炼重点、总结内容
2. 整理知识资料，建立个人知识库
3. 检索已有资料，回答问题

直接发送文件或告诉我你的需求，就可以开始了。
```

**缺失信息**:
- ❌ 未说明支持格式（PDF、DOCX、TXT、MD、XLSX）
- ❌ 未说明文件大小限制
- ❌ 未说明不支持PPTX、DOC、图片

---

## 二、文件处理完整调用链

```
[飞书文件消息]
  ↓
_on_message() [3046]
  ↓ msg_type == "file"
_download_and_save_media() [1847]
  ↓ 下载到本地（❌ 无大小检查）
_auto_upload_to_kb() [2086]
  ↓ 检查扩展名
if ext in {".pdf", ".docx", ".txt", ".md", ".xlsx"} ✅
  ↓
KBUploadTool.execute()
  ↓ 成功返回file_id
_extract_file_id() [1913]
  ↓
_save_pending_file_to_session() [1928]
  ↓ 原子更新
metadata["pending_file"] = {...}
  ↓
_send_file_greeting() [2034]
  ↓ 阻断Agent流程
return
  ↓
[用户提问]
  ↓
Context注入pending_file
  ↓
kb_search/kb_read(file_id)
  ↓
Agent回答
```

**PPTX链路**:
```
PPTX到达
  ↓
下载成功 ✅
  ↓
_auto_upload_to_kb()
  ↓
ext = ".pptx" ❌ 不在SUPPORTED_EXTS
  ↓
return None（静默跳过）
  ↓
❌ pending_file未更新（保留旧值）
  ↓
消息发送给Agent
```

---

## 三、文件能力边界

### 3.1 实际支持格式

**代码**: `runtime.py:2095`

```python
SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}
```

**已证实**:
- ✅ PDF、DOCX、TXT、Markdown、XLSX
- ❌ PPTX、DOC、图片

### 3.2 文件大小限制

**❌ 关键发现：飞书侧无50MiB检查**

- `_download_and_save_media()`: 无大小检查
- `_auto_upload_to_kb()`: 无大小检查
- `read_file`: 有100MiB限制

**结论**: 135.14MB的PPTX会被下载（占用空间），但因格式不支持被跳过

---

## 四、下一阶段实施方案

### 批次2-A：欢迎消息修复（紧急）⭐⭐⭐⭐⭐

**目标**: 修复230001错误 + 增加能力说明

**修改1**: 修复JSON格式
```python
welcome_text = """..."""
content = json.dumps({"text": welcome_text}, ensure_ascii=False)
await loop.run_in_executor(None, self._send_message_sync, rid_type, sender_id, "text", content)
```

**修改2**: 增加能力边界
```python
welcome_text = """你好，我是小文，很高兴认识你。

我可以帮你：
1. 阅读和理解文件，提炼重点、总结内容
2. 整理知识资料，建立个人知识库
3. 检索已有资料，回答问题

📄 支持格式：PDF、Word (DOCX)、TXT、Markdown、Excel (XLSX)
📏 文件大小：单个文件最大 50 MB
⚠️ 暂不支持：PPT (PPTX)、旧版Word (DOC)、图片内容识别

直接发送文件或告诉我你的需求，就可以开始了。"""
```

**涉及文件**: `nanobot/channels/feishu/runtime.py`

### 批次2-B：不支持格式反馈（高）⭐⭐⭐⭐

**目标**: PPTX等不支持格式给用户明确反馈

```python
async def _auto_upload_to_kb(self, file_path, sender_id=None):
    ext = path.suffix.lower()
    
    SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}
    if ext not in SUPPORTED_EXTS:
        self.logger.info("Unsupported file format: {}", ext)
        
        format_name = {
            ".pptx": "PowerPoint (PPTX)",
            ".doc": "旧版Word (DOC)",
            ".jpg": "图片", ".png": "图片",
        }.get(ext, f"{ext}格式")
        
        return f"⚠️ 暂不支持{format_name}文件。\n\n支持的格式：PDF、Word (DOCX)、TXT、Markdown、Excel (XLSX)"
```

**涉及文件**: `nanobot/channels/feishu/runtime.py`

### 批次2-C：pending_file清理（严重）⭐⭐⭐⭐⭐

**目标**: 新文件失败时清理旧pending_file

```python
if kb_result:
    result_str = str(kb_result)
    is_error = result_str.startswith("⚠️") or "failed" in result_str.lower()
    
    if is_error:
        # ✅ 清理旧pending_file
        await self._clear_pending_file(sender_id, chat_type)
    else:
        file_id = self._extract_file_id(result_str)
        if file_id:
            saved = await self._save_pending_file_to_session(...)
```

**新增函数**:
```python
async def _clear_pending_file(self, sender_id: str, chat_type: str) -> bool:
    if chat_type != "p2p":
        return False
    
    try:
        session_key = f"{self.name}:{sender_id}"
        
        def clear_meta(meta: dict[str, Any]) -> None:
            if "pending_file" in meta:
                del meta["pending_file"]
            return None
        
        await sessions.state.mutate_metadata(session_key, clear_meta, create_if_missing=False)
        return True
    except Exception as e:
        self.logger.exception("Failed to clear pending_file: {}", e)
        return False
```

**依赖**: 批次2-B

**涉及文件**: `nanobot/channels/feishu/runtime.py`

### 批次2-D：read_file权限约束（高）⭐⭐⭐⭐

**目标**: 限制read_file对媒体目录的访问

```python
class ReadFileTool(_FsTool):
    async def execute(self, path: str | None = None, **kwargs):
        if path and self._is_media_dir_path(path):
            return ToolResult.error(
                "Error: Cannot directly read files from media directory. "
                "Please use kb_search or kb_read for uploaded files."
            )
        # 原有逻辑...
    
    def _is_media_dir_path(self, path: str) -> bool:
        try:
            from nanobot.config.paths import get_media_dir
            media_dir = get_media_dir("feishu")
            file_path = Path(path).resolve()
            return media_dir in file_path.parents or file_path == media_dir
        except Exception:
            return False
```

**涉及文件**: `nanobot/agent/tools/filesystem.py`

### 批次2-E：文件大小限制（低）⭐⭐

**目标**: 在飞书侧增加50MiB检查

```python
async def _download_and_save_media(self, msg_type, content_json, message_id):
    # 下载逻辑...
    
    if data and filename:
        file_size = len(data)
        MAX_FILE_SIZE = 50 * 1024 * 1024
        
        if file_size > MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            return None, f"⚠️ 文件过大 ({size_mb:.1f} MB)，最大支持 50 MB"
        
        # 保存文件...
```

**依赖**: 批次2-C

**涉及文件**: `nanobot/channels/feishu/runtime.py`

---

## 五、实施优先级

```
1. 紧急：批次2-A（欢迎消息）→ 立即实施
2. 高：批次2-B（格式反馈）→ 立即实施
3. 严重：批次2-C（pending_file清理）→ 等待2-B
4. 高：批次2-D（read_file权限）→ 并行实施
5. 低：批次2-E（大小限制）→ 可选
```

---

## 六、测试方案（关键场景）

1. ✅ 欢迎消息无230001错误
2. ✅ PPTX上传后用户收到明确反馈
3. ✅ PPTX上传后pending_file被清理
4. ✅ 第二个成功文件正确替换第一个
5. ✅ read_file无法访问媒体目录
6. ✅ 60MB文件被拒绝（批次2-E）

---

**调查完成**: 2026-10-30  
**状态**: ✅ **完成，无代码修改**  
**下一步**: 等待确认后实施批次2-A
