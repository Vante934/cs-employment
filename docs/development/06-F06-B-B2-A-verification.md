# F06-B-B2-A：pending_file管理 - 定点核验报告

**核验日期**: 2026-10-30  
**状态**: ✅ 核验完成  
**核验人**: AI Assistant

---

## 执行摘要

### 核心发现

1. ✅ **file_id可提取** - 从返回字符串解析
2. ✅ **KBStore接口完整** - get_file()可获取元数据
3. ✅ **mutate_metadata支持** - 原子更新，并发安全
4. ✅ **Session Key明确** - 私聊使用默认生成
5. ⚠️ **返回格式非结构化** - 需要解析字符串提取file_id

### 推荐实施方式

**方案A**: 解析返回字符串（推荐）
- 从"ID: {file_id}"提取
- 调用kb_store.get_file(file_id)获取元数据
- 无需修改KBUploadTool

**方案B**: 修改KBUploadTool返回结构化数据
- 破坏现有工具接口
- 不推荐

---

## 一、文件入库结果核验

### 1.1 _auto_upload_to_kb() 实际代码

**位置**: 
anobot/channels/feishu/runtime.py:1909-2007

**实际返回值**:
`python
# 成功情况
"✅ File added to knowledge base successfully"
"ℹ️ File already in knowledge base"
"✅ File uploaded to knowledge base"

# 失败情况
"⚠️ Knowledge base upload failed: {error}"
"⚠️ KB upload error: {exception}"
`

**关键发现**: ❌ **当前实现不返回file_id**

### 1.2 KBUploadTool.execute() 实际返回

**位置**: 
anobot/agent/tools/kb_upload.py:214-322

**返回格式**:

**成功**:
`python
return (
    f"File uploaded and indexed successfully!
"
    f"Filename: {resolved.name}
"
    f"Size: {resolved.stat().st_size / 1024:.1f} KB
"
    f"Type: {resolved.suffix[1:].upper()}
"
    f"ID: {file_id}
"                           # ← file_id在这里
    f"Chunks: {process_result.chunk_count}
"
    f"Indexed: {process_result.indexed_count}
"
    f"Status: {process_result.final_status}"
)
`

**重复文件**:
`python
return (
    f"File already exists in knowledge base: {existing['filename']} "
    f"(ID: {existing['file_id']}, uploaded: {existing['created_at']})"  # ← file_id
)
`

**失败**:
`python
return ToolResult.error(
    f"File uploaded but processing failed: {process_result.error}
"
    f"File ID: {file_id}
"                     # ← file_id仍存在
    ...
)
`

**关键发现**: ✅ **file_id存在于返回字符串中**

### 1.3 提取file_id的方法

**正则表达式**:
`python
import re

# 方法1: 精确匹配
match = re.search(r'ID:\s*([a-f0-9\-]+)', result_str)
if match:
    file_id = match.group(1)

# 方法2: 多种格式兼容
match = re.search(r'(?:ID|file_id):\s*([a-f0-9\-]+)', result_str, re.IGNORECASE)
`

### 1.4 文件元数据获取

**来源**: KBUploadTool已经创建了数据库记录

**字段** (kb_upload.py:257-264):
- ilename: resolved.name
- ile_type: resolved.suffix[1:].lower()
- ile_size: resolved.stat().st_size
- ile_path: str(kb_file_path)
- ile_hash: file_hash
- status: "pending" → 处理后变为"indexed"或"failed"

**获取方式**: 通过KBStore.get_file(file_id)

---

## 二、KBStore查询接口核验

### 2.1 核心方法

**位置**: 
anobot/kb/store.py

**get_file(file_id)**:
`python
def get_file(self, file_id: str) -> dict[str, Any] | None:
    # 返回完整文件记录
    # 包含: file_id, filename, file_type, file_size, 
    #      file_path, file_hash, status, created_at, updated_at
`
**位置**: store.py:53-64

**get_file_by_hash(file_hash)**:
`python
def get_file_by_hash(self, file_hash: str) -> dict[str, Any] | None:
    # 用于检测重复文件
`
**位置**: store.py:66-77

**file_exists(file_hash)**:
`python
def file_exists(self, file_hash: str) -> bool:
    # 快速检查文件是否存在
`

**get_parsed_text(file_id)**:
`python
def get_parsed_text(self, file_id: str) -> str | None:
    # 获取完整解析文本（从chunk_index=0读取）
    # 用于后续B2-B的KB Get File Tool
`
**位置**: store.py:214-232

### 2.2 返回的文件记录结构

**从kb_files表**:
`python
{
    "file_id": str,          # UUID
    "filename": str,         # 原始文件名
    "file_type": str,        # pdf, docx, txt等
    "file_size": int,        # 字节数
    "file_path": str,        # KB存储路径
    "file_hash": str,        # SHA256
    "status": str,           # pending/indexed/failed
    "created_at": str,       # ISO时间戳
    "updated_at": str,       # ISO时间戳
}
`

### 2.3 可复用接口

✅ **get_file(file_id)** - 获取文件元数据  
✅ **get_parsed_text(file_id)** - 获取完整文本（B2-B用）  
✅ **file_exists(file_hash)** - 检查重复（已在KBUploadTool中使用）

**结论**: ✅ **无需新增KBStore方法**

---

## 三、Session Metadata更新核验

### 3.1 mutate_metadata接口

**位置**: 
anobot/session/state.py:573-610

**签名**:
`python
async def mutate_metadata(
    self,
    key: str,
    change: Callable[[dict[str, Any]], _T],
    *,
    expected_generation: str | None = None,
    create_if_missing: bool = False,
) -> tuple[_T, dict[str, Any]]:
`

**参数说明**:
- key: session_key
- change: 修改函数，接收metadata dict，返回任意值
- expected_generation: 乐观锁（可选）
- create_if_missing: 是否创建不存在的session

**返回**:
- (change函数返回值, 完整metadata)

### 3.2 使用示例

**保存pending_file**:
`python
def save_pending_file(meta: dict[str, Any]) -> None:
    meta["pending_file"] = {
        "file_id": file_id,
        "filename": filename,
        "file_size": file_size,
        "file_type": file_type,
        "upload_time": datetime.now().isoformat(),
        "sender_id": sender_id,
        "summary": summary,
        "status": "pending",
        "request_id": str(uuid.uuid4()),
    }
    return None  # 或返回任意值

result, full_meta = await session_state.mutate_metadata(
    session_key,
    save_pending_file,
    create_if_missing=True  # 如果session不存在，创建它
)
`

### 3.3 原子性保证

**实现细节** (state.py:582-600):
`python
def commit():
    def write():
        session = self._load(key, history=False)
        if session is None:
            if not create_if_missing:
                raise SessionConflictError("session does not exist")
            session = Session(key=key)
        
        result = change(session.metadata)  # 在事务内修改
        
        if header_only:
            self._store.replace_metadata(key, session.metadata)
        else:
            self._commit(session)
        
        return session, result, header_only
    
    return self._store.run_write(write)  # ← SQLite事务
`

**并发安全**: ✅ **通过SQLite事务保证原子性**

### 3.4 metadata字段冲突检查

**现有字段** (从B1/B3/agent/loop.py):
- _greeted: B1欢迎语标记
- _last_usage: Agent使用统计
- REQUEST_ORIGIN_METADATA_KEY: 请求来源
- _runtime_checkpoint: Runtime检查点
- _pending_user_turn: 待处理标记

**结论**: ✅ **pending_file不冲突**

---

## 四、Session Key和用户身份核验

### 4.1 Session Key生成

**位置**: 
anobot/channels/feishu/runtime.py:3048-3053

**私聊**:
`python
if chat_type == "p2p":
    session_key = None  # 使用默认生成
`

**默认生成方式** (base.py:261-280):
`python
# _handle_message处理session_key=None时
# MessageBus会使用默认格式: "{channel_name}:{sender_id}"
# 对于Feishu: "feishu:{sender_id}"
`

**群聊**:
`python
if chat_type == "group":
    if self.config.topic_isolation:
        session_key = f"{self.name}:{chat_id}:{root_id or message_id}"
    else:
        session_key = f"{self.name}:{chat_id}"
`

### 4.2 sender_id来源

**位置**: runtime.py:2996-3015

`python
# 从飞书事件中提取
sender_id = message.get("sender", {}).get("sender_id", {}).get("open_id")
`

**格式**: "ou_xxxxx" (飞书Open ID)

### 4.3 _auto_upload_to_kb()的参数

**当前签名** (runtime.py:1909):
`python
async def _auto_upload_to_kb(
    self, 
    file_path: str, 
    sender_id: str | None = None
) -> str | None:
`

**调用位置**: runtime.py中下载文件后调用

**可获取的信息**:
- ✅ ile_path: 文件本地路径
- ✅ sender_id: 发送者Open ID
- ⚠️ session_key: 不直接可用

### 4.4 获取session_key的方法

**方案A**: 在_auto_upload_to_kb()中构造
`python
# 私聊场景
session_key = f"{self.name}:{sender_id}"  # "feishu:{ou_xxx}"
`

**方案B**: 传递chat_type参数
`python
async def _auto_upload_to_kb(
    self, 
    file_path: str, 
    sender_id: str | None = None,
    chat_type: str | None = None,  # 新增
) -> str | None:
    if chat_type == "p2p":
        session_key = f"{self.name}:{sender_id}"
    else:
        # 群聊场景：B2第一版不支持
        return None
`

**推荐**: ✅ **方案A**（更简单，符合默认行为）

### 4.5 群聊场景

**B2第一版决策**: ❌ **不支持群聊文件意图交互**

**理由**:
- 群聊session_key包含chat_id或message_id
- 文件上传时无法准确获取最终的session_key
- 避免复杂性

**实现**: 检查chat_type，群聊时跳过pending_file保存

---

## 五、B2-C接口兼容性核验

### 5.1 AgentLoop读取pending_file

**未来代码位置**: 
anobot/agent/loop.py:2211后

**接口需求**:
`python
# B2-C会添加
pending_file = session.metadata.get("pending_file")
if pending_file:
    file_context = self._build_pending_file_context(pending_file)
    ctx.runtime_context_blocks.append(file_context)
`

**兼容性**: ✅ **完全兼容**
- session.metadata是标准dict
- 读取操作不影响B2-A

### 5.2 RuntimeContextBlock注入

**设计** (B2技术设计):
`python
from nanobot.runtime_context import RuntimeContextBlock

def _build_pending_file_context(self, pending_file: dict) -> RuntimeContextBlock:
    content = f"""
The user recently uploaded: {pending_file['filename']}
Type: {pending_file['file_type']}
Size: {pending_file['file_size'] / 1024:.1f} KB
Uploaded: {time_ago(pending_file['upload_time'])}

{summary_section}

Use kb_get_file or kb_search tools.
File ID: {pending_file['file_id']}
"""
    return RuntimeContextBlock(source="pending_file", content=content)
`

**兼容性**: ✅ **完全兼容**
- B2-C独立实现此方法
- 不影响B2-A

### 5.3 与C的Agent Hook冲突

**C的事件系统** (根据C架构调查):
- 新增EventEmitter Hook
- 监听工具执行事件
- 发送执行面板卡片

**B2-C的Context注入**:
- 在_build_turn()中注入RuntimeContextBlock
- 不使用Hook机制

**冲突评估**: ✅ **无冲突**
- B2-C修改_build_turn()
- C修改Hook注册
- 两者在不同代码位置

### 5.4 推荐隔离策略

**B2-A**: 仅保存pending_file到metadata  
**B2-C**: 仅注入RuntimeContextBlock  
**C**: 仅实现事件可视化

**接口约定**:
- B2-A定义pending_file数据结构
- B2-C读取pending_file
- C不修改pending_file

---

## 六、发现的问题与风险

### 6.1 问题清单

**P1 - 高优先级**:

1. ⚠️ **file_id需要解析字符串**
   - 风险: 返回格式变化会导致解析失败
   - 缓解: 使用健壮的正则表达式

2. ⚠️ **_auto_upload_to_kb()无法获取session_key**
   - 风险: 需要在方法内构造
   - 缓解: 使用默认格式"{self.name}:{sender_id}"

3. ⚠️ **群聊场景复杂**
   - 风险: session_key不确定
   - 缓解: B2第一版不支持群聊

**P2 - 中优先级**:

4. ℹ️ **摘要生成未实现**
   - 决策: 第一版使用前N字符
   - 后续: 可考虑LLM生成

5. ℹ️ **文件删除后pending_file残留**
   - 决策: Lazy cleanup处理
   - 实现: B2-C阶段

**P3 - 低优先级**:

6. ℹ️ **重复文件的pending_file**
   - 决策: 使用existing file_id
   - 兼容: 用户体验一致

### 6.2 风险评估

| 风险 | 影响 | 概率 | 缓解 |
|------|------|------|------|
| 解析file_id失败 | 高 | 低 | 健壮正则+测试 |
| session_key错误 | 高 | 低 | 使用默认格式 |
| 群聊混乱 | 中 | 中 | 不支持群聊 |
| metadata冲突 | 中 | 低 | 命名检查 |
| 摘要生成失败 | 低 | 中 | 可选字段 |

---

## 七、推荐实施方式

### 7.1 核心流程

`python
# runtime.py:_auto_upload_to_kb()后新增

async def _save_pending_file_to_session(
    self,
    sender_id: str,
    file_id: str,
    filename: str,
    file_size: int,
    file_type: str,
) -> None:
    """Save pending file info to session metadata."""
    
    # 构造session_key（私聊）
    session_key = f"{self.name}:{sender_id}"
    
    # 生成简要摘要（前200字）
    summary = None
    try:
        if self._kb_store:
            parsed_text = self._kb_store.get_parsed_text(file_id)
            if parsed_text:
                summary = parsed_text[:200].strip()
                if len(parsed_text) > 200:
                    summary += "..."
    except Exception:
        pass  # 摘要失败不阻塞
    
    # 构建pending_file
    pending_file = {
        "file_id": file_id,
        "filename": filename,
        "file_size": file_size,
        "file_type": file_type,
        "upload_time": datetime.now(timezone.utc).isoformat(),
        "sender_id": sender_id,
        "summary": summary,
        "status": "pending",
        "request_id": str(uuid.uuid4()),
    }
    
    # 原子更新metadata
    def save_to_meta(meta: dict[str, Any]) -> None:
        meta["pending_file"] = pending_file
    
    # 获取SessionState
    from nanobot.session.manager import SessionManager
    from nanobot.config.loader import load_config
    
    config = load_config()
    sessions = SessionManager(config.workspace_path)
    
    await sessions.state.mutate_metadata(
        session_key,
        save_to_meta,
        create_if_missing=True,
    )
`

### 7.2 修改_auto_upload_to_kb()

**修改点** (runtime.py:1985-1990):

`python
# 当前代码
result_str = str(result)
if "ID:" in result_str:
    return "✅ File added to knowledge base successfully"

# 修改为
result_str = str(result)
if "ID:" in result_str:
    # 提取file_id
    match = re.search(r'ID:\s*([a-f0-9\-]+)', result_str)
    if match:
        file_id = match.group(1)
        
        # 获取文件元数据
        file_record = self._kb_store.get_file(file_id)
        if file_record:
            # 保存pending_file
            await self._save_pending_file_to_session(
                sender_id=sender_id,
                file_id=file_id,
                filename=file_record["filename"],
                file_size=file_record["file_size"],
                file_type=file_record["file_type"],
            )
    
    return None  # 不返回KB消息，避免Agent立即处理
`

### 7.3 检查chat_type（避免群聊）

**调用_auto_upload_to_kb()的位置需要传递chat_type**:

`python
# runtime.py:下载文件后
if auto_upload_result and chat_type == "p2p":
    # 仅私聊触发B2流程
    pass
`

---

## 八、实施检查清单

### 8.1 代码修改

- [ ] 新增_save_pending_file_to_session()方法
- [ ] 修改_auto_upload_to_kb()提取file_id
- [ ] 添加正则表达式导入(import re)
- [ ] 添加datetime, timezone, uuid导入
- [ ] 检查chat_type限制私聊

### 8.2 测试用例

- [ ] 文件上传成功后pending_file保存
- [ ] file_id正确提取
- [ ] request_id唯一性
- [ ] 摘要生成（成功和失败）
- [ ] 群聊场景跳过
- [ ] Session不存在时创建
- [ ] 并发上传覆盖正确

### 8.3 兼容性验证

- [ ] B1欢迎语不受影响
- [ ] B3进度消息正常
- [ ] B4 Reaction正常
- [ ] 普通文件下载不受影响
- [ ] 非支持格式文件不触发

---

## 九、与其他批次的接口

### B2-D: 意图消息

**依赖**: 读取pending_file

**接口**:
`python
pending_file = session.metadata.get("pending_file")
if pending_file:
    await self._send_file_greeting(sender_id, pending_file)
`

**实施**: B2-D批次

### B2-B: KB工具

**依赖**: pending_file中的file_id

**接口**:
`python
# KB Get File Tool会读取
pending_file = session.metadata.get("pending_file")
if pending_file and pending_file["file_id"] == requested_file_id:
    # 权限验证通过
`

**实施**: B2-B批次

### B2-C: Agent注入

**依赖**: 读取pending_file

**接口**:
`python
# AgentLoop._build_turn()
pending_file = session.metadata.get("pending_file")
if pending_file and pending_file["status"] == "pending":
    context_block = self._build_pending_file_context(pending_file)
    ctx.runtime_context_blocks.append(context_block)
`

**实施**: B2-C批次

---

## 十、最终结论

### 技术可行性

✅ **高度可行**

**理由**:
- file_id可从返回字符串提取
- KBStore接口完整
- mutate_metadata支持原子更新
- session_key可构造
- 无接口冲突

### 实施风险

✅ **低风险**

**缓解措施**:
- 健壮的file_id解析
- 仅支持私聊
- 摘要可选
- 充分测试

### 工作量评估

**预估**: 2-3小时

**分解**:
- 实现_save_pending_file_to_session: 1h
- 修改_auto_upload_to_kb: 0.5h
- chat_type检查: 0.5h
- 单元测试: 1h

### 推荐方案

**✅ 采用方案A: 解析返回字符串**

**不推荐方案B**: 修改KBUploadTool返回结构
- 破坏现有工具接口
- 影响Agent工具调用
- 增加复杂度

---

**核验完成时间**: 2026-10-30  
**核验人**: AI Assistant  
**状态**: ✅ **核验通过，可以进入第二阶段编码**
