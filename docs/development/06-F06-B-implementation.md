# F06-B 基础交互体验优化 - 实施记录

## 项目概述

F06-B 阶段目标是优化 Nanobot 飞书知识库助手"小文"的交互体验，从功能型工具演进为具备自然交互、过程反馈和智能意图识别的个人知识管理助手。

**开发方式**：分批开发、小范围核验、统一验收

**实施顺序**：B4 → B1 → B3 → B2 → B5 → B6

---

## B4：自动表情回应调整

**实施时间**：2025-01-XX  
**状态**：✅ 已完成

### 目标

- 将默认点赞表情从 THUMBSUP 改为 OK
- 确保空字符串配置时不添加reaction
- 保留显式配置其他表情的能力
- Reaction失败不影响Agent正常处理
- 保留done_emoji原有行为

### 实施内容

#### 1. 修改默认配置

**修改文件**：
- `nanobot/channels/feishu/config.py`
- `nanobot/channels/feishu/manifest.py`

**变更内容**：
```python
# 修改前
react_emoji: str = "THUMBSUP"

# 修改后
react_emoji: str = "OK"
```

**飞书支持的emoji类型**（根据代码注释）：
- THUMBSUP
- OK
- EYES
- DONE
- OnIt
- HEART

#### 2. 添加空字符串检查

**修改文件**：`nanobot/channels/feishu/runtime.py:2780`

**修改内容**：
```python
# 修改前
task = asyncio.create_task(
    self._add_reaction(message_id, self.config.react_emoji)
)
self._background_tasks.add(task)
task.add_done_callback(self._on_background_task_done)
task.add_done_callback(lambda t: self._on_reaction_added(message_id, t))

# 修改后
if self.config.react_emoji:
    task = asyncio.create_task(
        self._add_reaction(message_id, self.config.react_emoji)
    )
    self._background_tasks.add(task)
    task.add_done_callback(self._on_background_task_done)
    task.add_done_callback(lambda t: self._on_reaction_added(message_id, t))
```

**设计原因**：
- 当配置为空字符串时，不创建后台任务，避免无效API调用
- 保持done_emoji的独立控制（已有条件检查，无需修改）

#### 3. 新增测试

**测试文件**：`nanobot/channels/feishu/tests/test_feishu_reaction.py`

**新增测试类**：`TestReactEmojiConfiguration`

**测试用例**：
1. `test_empty_react_emoji_skips_reaction` - 验证空字符串时不添加reaction
2. `test_ok_react_emoji_adds_reaction` - 验证OK表情正常添加

### 测试结果

```bash
# 新增测试
pytest nanobot/channels/feishu/tests/test_feishu_reaction.py::TestReactEmojiConfiguration -v
======================== 2 passed in 10.78s =========================

# 完整reaction测试套件
pytest nanobot/channels/feishu/tests/test_feishu_reaction.py -v
======================== 22 passed in 7.96s =========================

# 完整feishu channel测试
pytest nanobot/channels/feishu/tests/ -v
===================== 196 passed, 2 skipped in 10.77s ==============
```

### 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| nanobot/channels/feishu/config.py | 修改 | 默认值 THUMBSUP → OK |
| nanobot/channels/feishu/manifest.py | 修改 | 默认值 THUMBSUP → OK |
| nanobot/channels/feishu/runtime.py | 修改 | 添加空字符串检查 |
| nanobot/channels/feishu/tests/test_feishu_reaction.py | 新增 | 新增2个测试用例 |

### 技术细节

**Reaction API调用流程**：
```
用户消息到达
    ↓
_on_message() 权限检查通过
    ↓
检查 config.react_emoji 是否非空
    ↓
创建异步任务调用 _add_reaction()
    ↓
后台执行（不阻塞消息处理）
    ↓
API失败时记录警告，返回None（不抛异常）
```

**错误处理机制**：
- `_add_reaction_sync()` 捕获所有异常，返回None
- API失败记录警告日志，不影响消息处理流程
- 后台任务失败通过 `_on_background_task_done` 处理

### 验证要点

- ✅ 默认配置从THUMBSUP改为OK
- ✅ 空字符串配置时不添加reaction
- ✅ 用户可显式配置其他emoji类型
- ✅ Reaction失败不影响消息处理
- ✅ done_emoji行为保持不变
- ✅ 所有现有测试通过
- ✅ 新增测试覆盖配置场景

### 已知问题

无

### 后续依赖

B1（首次欢迎语）不依赖本功能，可独立开发。

---

## B1：首次欢迎语与能力介绍

**实施时间**：2025-01-XX
**状态**：✅ 已完成

### 目标

- 用户首次与小文交互时发送欢迎语
- 欢迎语简洁介绍能力
- 只发送一次，状态持久化
- 区分私聊和群聊
- 不影响正常消息处理

### 实施内容

#### 1. 数据库Schema扩展

**新增表**：`channel_user_state`

**Schema定义** (sqlite_store.py):
```sql
CREATE TABLE IF NOT EXISTS channel_user_state (
    channel_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    welcome_sent INTEGER NOT NULL DEFAULT 0,
    welcome_sent_at TEXT,
    first_interaction_at TEXT NOT NULL,
    last_interaction_at TEXT NOT NULL,
    PRIMARY KEY (channel_name, user_id)
)
```

**设计原因**：
- 按 `(channel_name, user_id)` 唯一键存储
- 跨会话持久化，服务重启后保持
- 支持多channel实例独立跟踪
- 预留 `first_interaction_at` 和 `last_interaction_at` 用于未来统计

#### 2. SqliteSessionStore新增方法

**文件**：`nanobot/session/sqlite_store.py`

**新增方法**：
```python
def check_welcome_sent(channel_name: str, user_id: str) -> bool
def mark_welcome_sent(channel_name: str, user_id: str) -> None
def record_user_interaction(channel_name: str, user_id: str) -> None
```

**Schema Migration**：
- 新增 `_create_channel_user_state_schema()` 函数
- 添加到 `_SCHEMA_MIGRATIONS` 元组（v3）
- 自动执行数据库升级

#### 3. FeishuChannel欢迎语逻辑

**修改文件**：`nanobot/channels/feishu/runtime.py`

**新增成员变量**：
```python
self._sqlite_store = None  # Lazy-initialized
self._welcome_lock = threading.Lock()  # Prevent concurrent welcome sends
```

**新增方法**：
```python
def _get_sqlite_store() -> SqliteSessionStore
async def _check_and_send_welcome(sender_id, chat_id, chat_type) -> None
```

**触发时机**：
- 在 `_on_message()` 中权限检查通过后
- 在添加reaction之前
- 仅在私聊 (`chat_type == "p2p"`) 时触发

**欢迎语文案**：
```
你好，我是小文，很高兴认识你。

我可以帮你：
1. 阅读和理解文件，提炼重点、总结内容
2. 整理知识资料，建立个人知识库
3. 检索已有资料，回答问题

直接发送文件或告诉我你的需求，就可以开始了。
```

#### 4. 异常处理

**策略**：欢迎语失败不影响正常消息处理

**实现**：
```python
# 在 _on_message 中
try:
    await self._check_and_send_welcome(sender_id, chat_id, chat_type)
except Exception:
    self.logger.exception("Error in welcome message flow")

# 在 _check_and_send_welcome 中
except Exception:
    self.logger.exception("Failed to send welcome message to user {}", sender_id)
```

**保护措施**：
- 数据库查询失败 → 记录日志，跳过欢迎
- 消息发送失败 → 记录日志，**不标记为已发送**
- 状态更新失败 → 下次仍会尝试发送

#### 5. 群聊策略

**决策**：群聊不发送欢迎语

**原因**：
- 避免打扰群成员
- 群聊通常是工作场景，不需要欢迎语
- 降低误触发风险

**实现**：
```python
if chat_type != "p2p":
    return
```

### 测试结果

**新增测试文件**：`test_feishu_welcome.py`

**基础测试用例**：
```
TestWelcomeMessage::test_welcome_sent_to_new_user_in_dm            PASSED
TestWelcomeMessage::test_welcome_not_sent_twice                    PASSED
TestWelcomeMessage::test_welcome_not_sent_in_group_chat            PASSED
TestWelcomeMessage::test_welcome_failure_does_not_raise            PASSED
TestWelcomeMessage::test_welcome_marked_only_after_send_success    PASSED

5 passed in 6.78s
```

**并发测试用例（B1-R1新增）**：
```
TestWelcomeMessageConcurrency::test_concurrent_messages_same_user_single_welcome    PASSED
TestWelcomeMessageConcurrency::test_concurrent_messages_different_users             PASSED
TestWelcomeMessageConcurrency::test_many_concurrent_messages_same_user              PASSED
TestWelcomeMessageConcurrency::test_send_failure_allows_retry                       PASSED
TestWelcomeMessageConcurrency::test_concurrent_after_already_sent                   PASSED

5 passed in 10.09s
```

**完整测试套件**：
```
test_feishu_welcome.py:  10 passed in 7.73s
pytest nanobot/channels/feishu/tests/
========================= 206 passed, 2 skipped in 11.26s =========================
```

### 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| nanobot/session/sqlite_store.py | 新增 | 新增表schema和访问方法 (+56行) |
| nanobot/channels/feishu/runtime.py | 新增 | 欢迎语逻辑+并发锁 (+98行净增) |
| nanobot/channels/feishu/tests/test_feishu_welcome.py | 新增 | 10个测试用例 (+322行) |

**代码统计**：
- sqlite_store.py: +56 行
- runtime.py: +98 行（净增，含B1-R1并发修复）
- test_feishu_welcome.py: +322 行（含5个并发测试）
- **总计**: +476 行

### 技术细节

**并发安全**：
- ✅ **已修复（B1-R1）**：按用户ID的异步锁机制
- ✅ `_welcome_user_locks` 字典存储每个用户的锁
- ✅ `_welcome_user_locks_lock` 保护字典本身
- ✅ 同一用户串行处理，不同用户并发处理
- ✅ 数据库 `ON CONFLICT` 作为兜底保护

**调用链**：
```
用户首次私聊消息
    ↓
_on_message() 权限检查通过
    ↓
_check_and_send_welcome()
    ↓
    ├─ check_welcome_sent() → False
    ├─ _send_message_sync() → 发送欢迎语
    └─ mark_welcome_sent() → 标记已发送
    ↓
继续正常消息处理（reaction、内容解析、Agent）
```

**状态转换**：
```
新用户（无记录）
    ↓ 首次消息
检查数据库 → 未发送
    ↓
发送欢迎语
    ↓
标记已发送 (welcome_sent=1, welcome_sent_at=now)
    ↓ 后续消息
检查数据库 → 已发送
    ↓
跳过欢迎语，直接处理消息
```

### 验证要点

- ✅ 新用户首次私聊收到欢迎语
- ✅ 同一用户不重复发送
- ✅ 群聊不发送欢迎语
- ✅ 服务重启后状态保持（持久化）
- ✅ 欢迎语失败不影响消息处理
- ✅ 发送失败时不标记为已发送
- ✅ **并发消息只发送一次（B1-R1已修复）**
- ✅ **不同用户可以并发处理（B1-R1验证）**
- ✅ 所有现有测试通过
- ✅ B4 表情回应功能不受影响

### 已知问题

**已修复（B1-R1）**：
- ✅ 同一用户并发竞态问题已通过按用户锁解决
- ✅ 新增5个并发测试验证修复有效

**已知限制（不在B1范围）**：
- 跨进程/跨机器分布式部署需要分布式锁（当前单机单实例）
- 用户锁字典无限增长（需要LRU清理，留待未来优化）

### 后续依赖

B3（处理状态反馈）不依赖本功能，可独立开发。

---

## 开发规范检查

- ✅ 代码无尾随空格
- ✅ 代码无多余空行
- ✅ 所有测试通过
- ✅ 无破坏性变更
- ✅ 文档记录完整


---

## B3：文件处理状态反馈

**实施时间**：2026-10-30  
**状态**：✅ 已完成

### 目标

为知识库文件处理流程添加关键阶段状态反馈，让用户知道系统正在工作。

### 实施内容

#### 1. 进度回调机制

**修改文件**：
anobot/kb/processor.py

**新增参数**：
`python
def process_file(
    file_id: str,
    kb_store: KnowledgeBaseStore,
    index_manager: IndexManager,
    force: bool = False,
    progress_callback: callable | None = None  # 新增可选参数
) -> ProcessResult:
`

**回调触发点**：
1. 解析完成：callback("parsing", "文件读取完成，正在整理内容...")
2. 分块完成：callback("chunking", "内容整理完成（N块），正在建立索引...")
3. 处理完成：callback("complete", "文件已成功加入知识库")

#### 2. KBUploadTool集成

**修改文件**：
anobot/agent/tools/kb_upload.py

**新增**：
- 构造函数添加 progress_callback 参数
- execute方法中发送初始消息："正在处理文件..."
- 将callback传递给 process_file()

#### 3. FeishuChannel消息发送

**修改文件**：
anobot/channels/feishu/runtime.py

**新增方法**：
`python
async def _send_progress_message(self, user_id: str, message: str)
`

**回调创建**：在 _auto_upload_to_kb() 中创建进度回调闭包
`python
def progress_callback(stage: str, message: str):
    if sender_id and self._loop:
        asyncio.run_coroutine_threadsafe(
            self._send_progress_message(sender_id, message),
            self._loop
        )
`

### 技术细节

**线程安全**：
- process_file() 在线程池执行
- 回调通过 syncio.run_coroutine_threadsafe() 调度到主事件循环
- 不在工作线程中阻塞等待消息发送

**异常隔离**：
- 所有回调调用包装在 try-except 中
- 回调失败仅记录日志，不影响文件处理
- 消息发送失败不传播异常

**向后兼容**：
- progress_callback 为可选参数，默认 None
- 现有调用方无需修改

### 进度消息

用户收到的消息序列：
1. "正在处理文件..."
2. "文件读取完成，正在整理内容..."
3. "内容整理完成（5块），正在建立索引..."
4. "文件已成功加入知识库"

### 测试结果

**新增测试文件**：
anobot/kb/tests/test_processor_progress.py

**测试用例**：
`
test_process_file_signature_has_callback_parameter          PASSED
test_process_file_without_callback                          PASSED
test_progress_callback_invoked_on_parse_complete            PASSED
test_callback_failure_does_not_break_processing             PASSED
test_callback_messages_do_not_expose_internal_details       PASSED

5 passed, 1 warning in 20.59s
`

**回归测试**：
`
nanobot/channels/feishu/tests/: 206 passed, 2 skipped in 18.08s
`

### 修改文件清单

| 文件 | 变更 | 说明 |
|------|------|------|
| nanobot/kb/processor.py | 修改 | 添加progress_callback参数和回调触发 (+16行) |
| nanobot/agent/tools/kb_upload.py | 修改 | 传递callback到process_file (+12行) |
| nanobot/channels/feishu/runtime.py | 修改 | 创建callback和消息发送 (+52行) |
| nanobot/kb/tests/test_processor_progress.py | 新增 | 进度回调测试 (+170行) |

**代码统计**: +250行

### 验证要点

- ✅ process_file 不提供回调时正常工作（向后兼容）
- ✅ 正常处理时关键阶段回调顺序正确
- ✅ 处理失败时不发送成功状态
- ✅ 状态消息发送失败时文件处理仍能正常完成
- ✅ 回调从工作线程安全调度到主事件循环
- ✅ 不同文件的状态消息不会错误关联
- ✅ 原有 KB 文件处理测试通过
- ✅ 消息内容用户友好，不暴露内部细节

### 已知限制

**符合B3设计目标**：
- 无实时进度百分比（仅关键阶段反馈）
- 索引阶段粒度较粗（不报告每个embedding batch）
- 无消息频率限制（4条固定消息）

**潜在优化**（不在B3范围）：
- 使用 CardKit 富媒体卡片
- 添加取消机制
- 实时进度百分比

### 后续依赖

B2（智能问候）和 B5（意图识别）不依赖本功能，可独立开发。

