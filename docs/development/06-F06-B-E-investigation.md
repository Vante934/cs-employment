# F06-B 后续体验优化：实施前源码核验报告

**调查日期**: 2026-10-30  
**性质**: 只读源码调查（无代码修改，无Git写操作）  
**状态**: ✅ 完成  

---

## 目录

1. 真实架构与调用链
2. 外部方案逐项核验
3. 必须解决的问题
4. 推荐实施架构
5. 实施批次
6. 待确认事项
7. 兼容性影响分析

---

## 一、真实架构与调用链

### 1.1 AgentLoop执行流程（真实源码）

**文件**: `nanobot/agent/loop.py`

**核心流程**:
```
AgentLoop.run() [1520]
  ↓ (消息循环)
bus.consume_inbound() → InboundMessage
  ↓
_dispatch_inbound() [1556]
  ↓ (命令优先)
commands.dispatch_priority() → 处理命令
  ↓ (或消息入队)
_enqueue_session_message() → pending_queues
  ↓ (worker拉取)
_session_worker()
  ↓ (创建run)
AgentRunner.run() [nanobot/agent/runner.py]
  ↓ (LLM调用)
provider.chat_stream_with_retry()
  ↓ (tool_calls检测)
解析tool_calls → 执行工具
  ↓
tool.execute() [1012]
  ↓ (结果返回)
添加tool消息到历史
  ↓ (继续循环或结束)
```

**关键发现**:

✅ **已有Hook系统**
- 文件: `nanobot/agent/hook.py`
- 方法:
  - `before_execute_tools()` [112]
  - `before_execute_tool()` [115]
  - `after_execute_tool()` [124]
  - `on_execute_tool_error()` [134]

✅ **统一工具执行入口**
- 位置: `nanobot/agent/loop.py:1012`
- 代码: `result = await tool.execute(...)`
- 在RequestContext和WorkspaceScope上下文中执行

❌ **无事件广播系统**
- Hook是同步回调，不是异步事件
- 无事件总线或发布订阅
- AgentLoop不向FeishuChannel发送事件

### 1.2 FeishuChannel与AgentLoop交互

**消息入站**:
```
_on_message() [3046]
  ↓
解析消息内容
  ↓
InboundMessage(channel, sender_id, content, media)
  ↓
bus.publish_inbound(msg)
  ↓ (AgentLoop消费)
AgentLoop.consume_inbound()
```

**响应出站**:
```
AgentLoop → OutboundMessage
  ↓
bus.publish_outbound(msg)
  ↓
FeishuChannel.consume_outbound()
  ↓
_stream_send_text_progressive() [2700]
  ↓
创建/更新StreamingCard
```

**关键发现**:

✅ **MessageBus连接** - AgentLoop和FeishuChannel通过MessageBus解耦  
❌ **无Agent状态通知** - AgentLoop不发送中间状态  

### 1.3 StreamingCard调用链

**文件**: `nanobot/channels/feishu/runtime.py`

**流程**:
```
Provider.chat_stream() 产生token
  ↓ (回调)
_on_stream(token) [2819]
  ↓
_stream_send_text_progressive() [2700]
  ↓ (首次)
_create_streaming_card_sync() [2534] → 创建卡片
  ↓ (后续)
_stream_update_text_with_reopen_sync() [2684] → 更新卡片
  ↓ (结束)
_close_streaming_mode_sync() [2666] → 关闭streaming模式
```

**关键数据结构**:
```python
# nanobot/channels/feishu/runtime.py:943
class _StreamBuf:
    card_id: str | None
    text: str
    sequence: int
```

**关键发现**:

✅ **流式卡片支持** - 创建后返回card_id，使用sequence追踪  
✅ **单卡片单任务** - 每个chat_id一个_StreamBuf，无并发风险  
❌ **无状态区/回答区分离** - 卡片只有text字段  
❌ **无缓冲策略** - 每个token直接触发更新  

---

## 二、外部方案逐项核验

### 2.1 方向一：Agent执行事件与状态可视化

#### 同步RunEventBus方案

**核验结果**: ⚠️ **需要调整**

**问题**: 同步EventBus会阻塞Agent

**推荐**:
```python
# 使用fire-and-forget
class RunEventBus:
    def publish_nowait(self, event):
        try:
            self._queue.put_nowait(event)  # ✅ 不阻塞
        except asyncio.QueueFull:
            pass  # 丢弃或记录
```

#### run_id隔离方案

**核验结果**: ✅ **可直接采用**

**当前实现**: 已有stream_key: `f"{chat_id}:{message_id}"`  
**无冲突**: 单用户单session不会并发run  

#### 事件顺序保证

**核验结果**: ✅ **可直接采用**

**当前已有**: StreamingCard的sequence机制 [2684]  

#### 避免阻塞Agent

**核验结果**: ✅ **必须采用**

**实现**: AgentLoop用put_nowait()不阻塞，FeishuChannel独立任务消费  

### 2.2 方向二：图片与图文混合消息

#### InboundMessage扩展

**核验结果**: ⚠️ **需要调整**

**当前结构** (`nanobot/bus/events.py:28-42`):
```python
@dataclass
class InboundMessage:
    content: str  # 纯文本
    media: list[str]  # 文件路径列表
```

**问题**: 无法表示"文字+图片+文字"

**推荐**:
```python
@dataclass
class InboundMessage:
    content: str  # 保留，向后兼容
    media: list[str]  # 保留
    content_blocks: list[ContentBlock] | None = None  # 新增，可选
```

#### 图片顺序保留

**核验结果**: ✅ **可直接采用**

**当前问题** (`nanobot/channels/feishu/runtime.py:400-443`):
```python
# ❌ 分别收集，丢失顺序
texts = []
images = []
```

**改进**: 使用ContentBlock列表保留顺序

#### 不入库检查

**核验结果**: ✅ **当前已隔离**

**当前逻辑** (`nanobot/channels/feishu/runtime.py:3152-3170`):
```python
if self._should_auto_upload(file_path):  # ← 检查文件类型
    # 只有文档类型才入库
```

**结论**: ✅ 图片不会错误入库

### 2.3 方向三：Streaming Card性能

**每token触发更新**: ✅ **确认存在**  
**全量更新**: ✅ **确认，每次发送完整文本**  
**无缓冲**: ❌ **确认缺失**  
**无节流**: ❌ **确认缺失**  
**无重试**: ❌ **确认缺失**  

### 2.4 方向四：工具执行信息泄漏

**核验结果**: ✅ **这是泄漏根因**

**泄漏路径**:
```
工具: 返回 "File: document.pdf\nPart 1 of 3\n[内容]"
  ↓
模型上下文: {"role": "tool", "content": "..."}
  ↓
模型生成: "我已经读取了文件，内容如下：\n[复述工具输出]"
  ↓
用户看到: 模型的复述（包含工具细节）
```

**泄漏类型判定**: **模型主动复述工具内容**

---

## 三、必须解决的问题

### 严重程度：高

**问题1：模型复述工具执行细节** ⭐⭐⭐⭐⭐
- 现象: 用户看到PowerShell命令、文件路径
- 根因: 模型将工具输出复述给用户
- 解决: System Prompt约束、工具返回格式优化

**问题2：无执行状态可视化** ⭐⭐⭐⭐
- 现象: 用户不知道Agent在做什么
- 根因: AgentLoop不发送中间状态
- 解决: 实现事件系统、在卡片显示状态

### 严重程度：中

**问题3：Streaming Card性能** ⭐⭐⭐
- 现象: 每个token触发卡片更新
- 根因: 无缓冲策略
- 解决: 实现50ms或10个token缓冲

**问题4：图片消息不完整** ⭐⭐⭐
- 现象: 纯image消息无占位符
- 根因: content_parts未处理image类型
- 解决: 添加占位符、下载图片

---

## 四、推荐实施架构

### 4.1 执行状态事件系统

**新增模块**:
```
nanobot/agent/events.py  # 事件定义
nanobot/agent/event_bus.py  # 事件总线
```

**事件结构**:
```python
@dataclass
class RunEvent:
    run_id: str
    sequence: int
    event_type: str  # "tool_start", "tool_end", "generating"
    data: dict[str, Any]
```

**数据流**:
```
AgentLoop (tool.execute)
  ↓ fire-and-forget
event_bus.publish_nowait(RunEvent)
  ↓ asyncio.Queue
FeishuChannel.event_consumer()
  ↓ 独立任务
更新StreamingCard状态区
```

### 4.2 图片消息处理

**InboundMessage扩展** (向后兼容):
```python
@dataclass
class ContentBlock:
    type: Literal["text", "image"]
    content: str

@dataclass
class InboundMessage:
    content: str  # 保留
    media: list[str]  # 保留
    content_blocks: list[ContentBlock] | None = None  # 新增
```

### 4.3 Streaming Card缓冲策略

**缓冲逻辑**:
```python
class _StreamBuf:
    pending_text: str = ""
    last_update_time: float = 0

async def _stream_send_text_progressive(...):
    should_update = (
        now - last_update > 0.05  # 50ms
        or len(pending_text) > 100  # 100字符
        or stream_end
    )
```

### 4.4 工具输出过滤

**Prompt约束**:
```
System: 
- 不要复述工具执行的技术细节
- 不要提及文件路径、命令、处理步骤
- 只总结结果和意义
```

---

## 五、实施批次

### 批次1：工具输出过滤（紧急）

**目标**: 防止模型复述工具细节

**涉及文件**:
- system_prompt (Prompt约束)
- nanobot/agent/tools/kb_access.py (优化返回格式)

**具体改动**:
1. System Prompt增加约束
2. kb_read返回格式简化
3. kb_search返回格式简化

**验收标准**:
- 用户不看到文件路径
- 用户不看到技术细节

### 批次2：图片消息完善

**目标**: 支持纯image消息和顺序保留

**涉及文件**:
- nanobot/bus/events.py
- nanobot/channels/feishu/runtime.py

**具体改动**:
1. InboundMessage增加content_blocks
2. _extract_post_content_v2()保留顺序
3. 纯image消息添加占位符

### 批次3：Streaming Card缓冲

**目标**: 减少API调用次数

**涉及文件**:
- nanobot/channels/feishu/runtime.py

**具体改动**:
1. 实现50ms或100字符缓冲
2. 增加性能监控

### 批次4：执行状态事件系统

**目标**: 实时显示Agent执行状态

**涉及文件**:
- nanobot/agent/events.py (新增)
- nanobot/agent/event_bus.py (新增)
- nanobot/agent/loop.py (修改)
- nanobot/channels/feishu/runtime.py (修改)

**具体改动**:
1. 定义RunEvent结构
2. 实现事件总线（asyncio.Queue）
3. AgentLoop发布事件
4. FeishuChannel订阅并更新卡片

---

## 六、待确认事项

### 6.1 飞书卡片能力

❓ **StreamingCard是否支持状态区和回答区分离**

**需确认**: 卡片JSON结构、独立更新、可折叠区域  
**影响**: 批次4的实施方案  

### 6.2 模型配置

❓ **当前实际使用的模型是否支持vision**

**需确认**: Provider类型、模型名称、API端点  
**影响**: 批次5的实施时机  

### 6.3 性能目标

❓ **StreamingCard缓冲参数**

**需确认**: 目标延迟、目标字符数、用户体验  
**影响**: 批次3的参数调优  

---

## 七、兼容性影响分析

### F01-F06 文件处理流程

**影响**: ✅ **无影响**

**理由**: 图片不进入KB，_should_auto_upload()已检查  

### B2-A pending_file

**影响**: ✅ **无影响**

**理由**: pending_file只存file_id  

### B2-B kb_search/kb_read

**影响**: ⚠️ **批次1影响返回格式**

**理由**: 返回格式简化，功能不变  
**回归测试**: 需更新测试断言  

### B2-C Context注入

**影响**: ✅ **无影响**

**理由**: Context注入在session级别  

---

## 八、总结

### 核心发现

1. ✅ 已有Hook系统，但无事件广播
2. ✅ MessageBus解耦，但单向通信
3. ✅ StreamingCard支持，但无缓冲
4. ✅ 工具输出泄漏的根因是模型复述
5. ✅ 图片支持不完整，缺少顺序保留
6. ✅ KB流程已隔离，图片不会错误入库

### 优先级排序

1. **紧急**: 工具输出过滤（批次1）
2. **高**: 执行状态可视化（批次4）
3. **中**: 图片消息完善（批次2）
4. **中**: Streaming Card缓冲（批次3）

---

**调查完成时间**: 2026-10-30  
**报告状态**: ✅ **完成，无代码修改，无Git操作**  
**下一步**: 等待确认后开始批次1实施
