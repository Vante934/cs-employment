# F06-B-C：Agent执行过程可视化与流式交互 - 架构调查报告

**调查日期**: 2026-10-30  
**状态**: ✅ 已完成  
**调查人**: AI Assistant

---

## 执行摘要

### 核心发现

1. ✅ **LLM流式输出**: 完整支持，通过on_content_delta回调
2. ✅ **Agent Hook机制**: 完整的生命周期事件系统
3. ✅ **飞书CardKit**: 支持流式更新（已实现）
4. ✅ **取消机制**: 支持任务取消，基于asyncio.CancelledError
5. ⚠️ **工具事件**: 有回调，但需要统一封装

### 技术可行性

**目标功能** → **当前能力** → **差距**

| 功能 | 当前能力 | 差距 | 实施复杂度 |
|------|---------|------|-----------|
| 流式输出 | ✅ 完整支持 | 无 | 低 |
| 执行步骤展示 | ⚠️ Hook分散 | 需要统一事件 | 中 |
| 工具卡片 | ✅ CardKit支持 | 需要格式化 | 中 |
| 实时更新 | ✅ 已实现 | 需要新卡片模板 | 低 |
| 任务中断 | ✅ 支持 | 需要UI触发 | 中 |
| 错误重试 | ✅ 支持 | 需要UI提示 | 低 |

---

## 一、LLM流式输出现状

### 1.1 Provider支持

**位置**: 
anobot/providers/base.py:1080

`python
async def chat_stream(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
    on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> LLMResponse:
`

**关键回调**:
- on_content_delta: 正式回答的增量文本
- on_thinking_delta: 推理过程（CoT，如果provider支持）
- on_tool_call_delta: 工具调用增量

### 1.2 AgentRunner使用

**位置**: 
anobot/agent/runner.py:997-1029

`python
async def _stream(delta: str) -> None:
    _generation_delta(delta)
    if delta:
        context.streamed_content = True
        await _close_native_reasoning()
    await hook.on_stream(context, delta)  # ← 传递给Hook

kwargs.update(
    on_content_delta=_stream,
    on_thinking_delta=_thinking,
    on_tool_call_delta=_provider_tool_event,
)
coro = spec.runtime.provider.chat_stream_with_retry(**kwargs)
`

**结论**: ✅ **流式输出完整支持，可直接使用**

---

## 二、Agent Hook机制

### 2.1 Hook接口

**位置**: 
anobot/agent/hook.py:67

`python
class AgentHook:
    def wants_streaming(self) -> bool  # 是否启用流式
    
    # 生命周期
    async def before_run(self, context: AgentRunHookContext)
    async def after_run(self, context: AgentRunHookContext)
    async def on_error(self, context: AgentRunHookContext)
    
    # 迭代（每轮工具调用）
    async def before_iteration(self, context: AgentHookContext)
    async def after_iteration(self, context: AgentHookContext)
    
    # 流式输出
    async def on_stream(self, context, delta: str)  # ← 正式回答增量
    async def on_stream_end(self, context, resuming: bool)
    
    # 推理过程
    async def emit_reasoning(self, reasoning: str)  # ← CoT内容
    async def emit_reasoning_end(self)
    
    # 工具执行
    async def before_execute_tools(self, context)
    async def before_execute_tool(self, context, tool_call, tool, params)
    async def after_execute_tool(self, context, tool_call, tool, params, result)
    async def on_execute_tool_error(self, context, tool_call, tool, params, error)
`

### 2.2 当前使用

**位置**: 
anobot/agent/runner.py

- ✅ hook.on_stream() - 每个delta调用
- ✅ hook.before_execute_tool() - 工具执行前
- ✅ hook.after_execute_tool() - 工具执行后
- ✅ hook.on_execute_tool_error() - 工具失败

**结论**: ✅ **Hook机制完整，可作为事件来源**

---

## 三、飞书消息能力

### 3.1 CardKit流式更新

**位置**: 
anobot/channels/feishu/runtime.py:2411-2659

**已实现功能**:
`python
def _stream_update_text_sync(self, card_id: str, content: str, sequence: int)
    # 流式更新卡片内容（typewriter效果）

def _stream_update_text_with_reopen_sync(self, card_id: str, content: str, sequence: int)
    # 失败时重新打开并更新

async def send_stream(self, chat_id: str, content: str, message_id: str | None)
    # 发送或更新流式卡片
    # - 首次：创建卡片
    # - 后续：更新卡片内容
    # - 完成：关闭streaming_mode
`

**更新频率限制**: _STREAM_EDIT_INTERVAL = 0.5 (500ms)

**结论**: ✅ **CardKit流式更新已实现，可直接使用**

### 3.2 交互式卡片

**当前使用**: 
- ✅ 流式输出卡片（markdown）
- ❌ 工具执行卡片（未实现）
- ❌ 执行过程面板（未实现）

**飞书SDK支持**:
- ✅ 按钮交互
- ✅ 卡片更新
- ✅ 多卡片布局

**需要新增**: 工具卡片模板、执行面板模板

---

## 四、取消机制

### 4.1 任务取消

**位置**: 
anobot/agent/loop.py:933

`python
async def _cancel_active_tasks(self, key: str) -> int:
    # 取消session的所有活动任务
    tasks = list(self._active_tasks.get(key, ()))
    cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
    # 取消pending消息、子agent、exec会话
    return cancelled + sub_cancelled + exec_cancelled
`

**触发方式**: 
- /stop 命令
- /new 命令（清理旧任务）
- Session删除

**LLM请求取消**: 
`python
# runner.py:1033
try:
    response = await coro
except asyncio.CancelledError:
    _pause_generation()
    await _close_native_reasoning()
    raise
`

**结论**: ✅ **取消机制完整，支持中断**

### 4.2 飞书触发

**当前**: 仅支持命令触发（/stop）

**需要新增**: 
- 卡片按钮触发取消
- 传递message_id到AgentLoop

---

## 五、错误与重试

### 5.1 LLM重试

**位置**: 
anobot/providers/base.py

`python
async def chat_stream_with_retry(**kwargs)
    # 自动重试transient错误
    # - 429 rate limit
    # - 5xx server error
    # - timeout
`

**重试配置**: _CHAT_RETRY_DELAYS = (1, 2, 4)

### 5.2 工具重试

**当前**: ❌ 工具失败不自动重试

**处理**: 返回错误给LLM，由LLM决定

### 5.3 消息发送失败

**位置**: 
anobot/channels/feishu/runtime.py

**处理**: 
- 流式更新失败 → fallback到普通卡片
- 发送失败 → 日志记录，不阻塞

**结论**: ✅ **错误处理完善，可显示重试提示**

---

## 六、事件安全与展示

### 6.1 安全问题

**不可暴露**:
- ❌ 原始CoT（on_thinking_delta）
- ❌ 系统提示词
- ❌ 工具参数中的敏感信息（路径、密钥）
- ❌ 内部错误堆栈

**可以展示**:
- ✅ 工具名称
- ✅ 工具执行状态（运行中/成功/失败）
- ✅ 执行耗时
- ✅ 过滤后的工具参数（如文件名）
- ✅ 用户友好的错误消息

### 6.2 事件类型设计

**推荐事件**:
`python
# 执行阶段
StepStarted(step_name: str)  # "正在分析问题"
StepCompleted(step_name: str)

# 工具调用
ToolStarted(tool_name: str, safe_params: dict, tool_id: str)
ToolCompleted(tool_id: str, duration_ms: int)
ToolFailed(tool_id: str, error_message: str, duration_ms: int)

# 回答生成
AnswerDelta(delta: str)  # 流式输出
AnswerCompleted()

# 任务状态
TaskInterrupted(reason: str)
TaskFailed(error_message: str, can_retry: bool)
TaskCompleted()
`

### 6.3 参数过滤

**实现位置**: 新增 
anobot/agent/event_sanitizer.py

`python
def sanitize_tool_params(tool_name: str, params: dict) -> dict:
    # 按工具类型过滤敏感参数
    # 如: Shell → 隐藏完整命令，只显示摘要
    #     Read → 显示文件名，隐藏路径
`

---

## 七、与现有功能兼容性

### 7.1 B1 欢迎消息

**兼容性**: ✅ 完全兼容

**理由**: 欢迎消息在Agent执行前发送，不冲突

### 7.2 B3 文件处理进度

**当前**: 独立的progress_callback

**选项A**: 保持独立（推荐）
- B3是KB层面的进度（解析、分块、索引）
- 与Agent执行是不同层面
- 继续使用独立回调

**选项B**: 统一到事件系统
- 需要修改B3实现
- 增加复杂度
- 不推荐

**结论**: ✅ **保持B3独立，不冲突**

### 7.3 B4 Reaction

**兼容性**: ✅ 完全兼容

**建议**: 
- 保留文件消息的reaction
- Agent执行消息不添加reaction（已有执行面板）

### 7.4 B2 文件意图交互

**兼容性**: ✅ 完全兼容

**集成点**:
- 意图确认消息 → 普通消息
- Agent处理文件 → 使用执行面板展示工具调用

---

## 八、能力差距分析

### 8.1 已有能力

| 能力 | 位置 | 状态 |
|------|------|------|
| LLM流式输出 | providers/base.py | ✅ 完整 |
| Agent Hook | agent/hook.py | ✅ 完整 |
| CardKit流式更新 | feishu/runtime.py | ✅ 已实现 |
| 任务取消 | agent/loop.py | ✅ 支持 |
| 错误处理 | 各模块 | ✅ 完善 |

### 8.2 缺失能力

| 能力 | 需要实现 | 复杂度 |
|------|---------|--------|
| 统一事件系统 | EventEmitter Hook | 中 |
| 执行面板卡片 | CardKit模板 | 低 |
| 工具卡片 | CardKit模板 | 低 |
| 参数过滤 | Sanitizer | 低 |
| 卡片按钮交互 | 飞书回调处理 | 中 |
| 事件节流 | Throttler | 低 |

### 8.3 需要修改的文件

**新增文件**:
- 
anobot/agent/event_emitter.py - 事件发射器Hook
- 
anobot/agent/event_sanitizer.py - 参数过滤
- 
anobot/channels/feishu/execution_panel.py - 执行面板管理

**修改文件**:
- 
anobot/channels/feishu/runtime.py - 集成事件监听
- 
anobot/agent/loop.py - 注册EventEmitter Hook

---

## 九、推荐实施方案

### 最小可行方案（MVP）

**阶段1: 基础事件系统** (3-4小时)
- 实现EventEmitter Hook
- 监听工具执行事件
- 基础参数过滤

**阶段2: 执行面板** (3-4小时)
- 设计CardKit模板
- 实现面板创建和更新
- 工具卡片展示

**阶段3: 流式回答** (2-3小时)
- 集成on_stream到面板
- 分离执行过程和正式回答

**阶段4: 交互功能** (2-3小时)
- 取消按钮
- 重试提示
- 卡片折叠

**总计**: 10-14小时

### 渐进增强

- P0: 工具执行可视化
- P1: 流式回答分离
- P2: 任务中断
- P3: 执行步骤摘要（需要LLM生成）

---

## 十、技术限制

### 10.1 飞书限制

- ⚠️ 卡片更新频率：建议500ms间隔
- ⚠️ 卡片大小：有限制，不能无限追加
- ⚠️ 按钮交互：需要验证签名

### 10.2 实现限制

- ⚠️ CoT不可见：正确决策，避免暴露内部思维
- ⚠️ 参数过滤：需要逐工具配置
- ⚠️ 高频事件：需要节流

---

## 十一、是否调整B2计划

### 评估

**B2当前状态**: 设计完成，未开始编码

**C对B2影响**: 
- ✅ 不冲突
- ✅ 可增强（文件工具调用可视化）
- ⚠️ 建议B2先完成基础功能，C后集成

**推荐顺序**: 
1. **B2先行**: 实现pending_file、KB工具、Agent注入
2. **C后集成**: B2完成后，添加执行面板

**理由**:
- B2是核心功能，C是体验增强
- 分离实施，降低风险
- B2完成后，C可以用B2作为测试场景

---

## 十二、待确认问题

1. **执行步骤摘要生成**: 由LLM生成 vs 固定模板？
2. **卡片按钮权限**: 飞书回调验证细节？
3. **事件存储**: 是否需要持久化执行历史？
4. **多agent场景**: 子agent的执行过程如何展示？

---

**调查完成**: 2026-10-30  
**结论**: ✅ **技术可行，推荐MVP方案，建议B2先行**
