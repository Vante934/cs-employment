# F06-B-B2 第二阶段：Agent Context 架构调查报告

**调查日期**: 2026-10-30  
**状态**: ✅ 已完成  
**调查人**: AI Assistant

---

## 执行摘要

### 核心发现

1. ✅ **Session.metadata 完全可用** - 可扩展、并发安全、持久化
2. ✅ **注入点明确** - AgentLoop._build_turn() 通过 RuntimeContextBlock
3. ✅ **技术路径清晰** - 小幅修改AgentLoop，利用现有机制
4. ⚠️ **需要新增KB工具** - KB Search Tool 和 KB Get File Tool
5. ⚠️ **需要超时清理** - pending_file可能长期残留

### 关键结论

**Q1: 是否能够利用现有 Session.metadata 实现 B2？**  
✅ **是的，完全可以**

**Q2: 是否需要修改 AgentLoop？**  
✅ **是的，需要小幅修改**（约+30行代码）

**Q3: 最合适的 Context 注入位置在哪里？**  
AgentLoop._build_turn() 【loop.py:2211】

**Q4: 是否可以复用现有文件处理工具？**  
⚠️ **部分可以，需要新增KB Search/Get工具**

**Q5: 当前设计还存在哪些技术阻塞？**  
⚠️ **超时清理机制和KB工具缺失**

### 实施评估

- **技术风险**: ✅ 低
- **实施复杂度**: 🔵 中等
- **预估工作量**: 8-12小时
- **向后兼容**: ✅ 是
- **可以进入第三阶段**: ✅ **是**

---

## 一、调查范围

1. ✅ Session 加载与读取
2. ✅ Agent Context 构建
3. ✅ Session Metadata 注入能力
4. ✅ Tool 调用机制
5. ✅ 多轮对话状态管理

---

## 二、Agent 消息处理完整调用链

### 从 InboundMessage 到 LLM 请求

`
MessageBus.consume_inbound()
    ↓
AgentLoop.run() 【loop.py:1409】
    ↓
AgentLoop._dispatch_inbound(msg) 【loop.py:1446】
    ↓
AgentLoop._enqueue_session_message(msg) 【loop.py:1533】
    ↓
AgentLoop._run_session_queue() 【loop.py:1555】
    session = sessions.state.peek_header(session_key)
    await self._dispatch_one(msg, pending)
    ↓
AgentLoop._dispatch_one() 【loop.py:1615】
    await sessions.state.read(session_key)
    response = await self._process_message(msg, ...)
    ↓
AgentLoop._process_message() 【loop.py:1820】
    # 【关键】获取完整Session
    session = await sessions.state.get(key)  【loop.py:1852】
    
    # 创建TurnContext
    ctx = TurnContext(
        msg=msg,
        session=session,  # ← Session对象在这里
        ...
    )
    
    # 执行多个stage
    await self._run_turn_stage(ctx, "build", self._build_turn)  【关键】
    ↓
AgentLoop._build_turn(ctx) 【loop.py:2168】
    session = ctx.require_session()
    
    # 准备历史
    ctx.history = await runner.prepare_prompt(session.get_history)
    
    # 【关键】解析runtime context
    ctx.runtime_context_blocks = await self._resolve_runtime_context_for_turn(ctx)
    【loop.py:2211】
    
    # 【关键】构建当前消息
    current_message = self.context.build_current_message(
        ctx.msg.content,
        media=ctx.msg.media,
        runtime_context_blocks=ctx.runtime_context_blocks  # ← 注入点！
    ) 【loop.py:2217】
    ↓
AgentContext.build_current_message() 【context.py:298】
    content = build_user_content(message, media)
    blocks = list(runtime_context_blocks or [])
    
    # 【关键】合并runtime context
    merged, meta = append_runtime_context(content, blocks)
    
    return {"role": "user", "content": merged}
`

### 关键发现

**Session获取**: wait sessions.state.get(key) 【loop.py:1852】  
**Context注入点**: 
untime_context_blocks 参数 【loop.py:2211, 2220】  
**Session可见性**: ctx.session.metadata 在整个TurnContext中可访问

---

## 三、Session 与 Context 关系

### 概念区分

| 概念 | 类型 | 位置 | 作用 |
|------|------|------|------|
| Session | Session | session/manager.py:137 | 完整会话状态 |
| Session.metadata | dict[str, Any] | Session字段 | **可扩展元数据** |
| TurnContext | TurnContext | gent/types.py | 单次处理上下文 |
| RuntimeContextBlock | RuntimeContextBlock | 
untime_context.py:25 | **LLM prompt注入** |

### RuntimeContextBlock 定义

`python
# nanobot/runtime_context.py:25
@dataclass
class RuntimeContextBlock:
    source: str     # 标识来源
    content: str    # 追加到user消息的内容
`

### 数据流

`
Session.metadata
    ↓ (加载)
TurnContext.session.metadata
    ↓ (读取并构建)
RuntimeContextBlock
    ↓ (注入)
LLM Prompt的user消息
`

---

## 四、pending_file 注入方案

### 方案A：直接在 _build_turn 中注入（推荐）

**位置**: AgentLoop._build_turn(ctx) 【loop.py:2211之后】

**实施**:
`python
# 在 loop.py:2211 之后添加
if ctx.kind is TurnKind.USER:
    ctx.runtime_context_blocks = await self._resolve_runtime_context_for_turn(ctx)
    
    # 【新增】检查pending_file
    pending_file = session.metadata.get("pending_file")
    if pending_file:
        file_block = self._build_file_context_block(pending_file)
        if file_block:
            ctx.runtime_context_blocks.append(file_block)
`

**优点**:
- ✅ 修改点集中
- ✅ 利用现有RuntimeContextBlock机制
- ✅ 不影响其他逻辑

**缺点**:
- ⚠️ 需要修改AgentLoop核心

**复杂度**: 中等（+30行）

### 方案B：自定义Provider（不可行）

**问题**: RuntimeContextProvider无法访问Session  
**结论**: ❌ 需要大幅修改架构

### 方案对比

| 维度 | 方案A | 方案B |
|------|-------|-------|
| 修改范围 | AgentLoop | RequestContext架构 |
| 复杂度 | 中 | 高 |
| 可行性 | ✅ | ❌ |

**推荐**: **方案A**

---

## 五、Tool 调用机制

### 现有KB工具

**已有**:
- ✅ KBUploadTool (kb_upload.py)

**不存在**:
- ❌ KB Search Tool
- ❌ KB Get File Tool
- ❌ KB Summarize Tool

### 需要新增

1. **KB Search Tool**
   - 输入：query
   - 输出：相关片段
   - 实现：调用 IndexManager.search()

2. **KB Get File Tool**
   - 输入：file_id
   - 输出：完整文本
   - 实现：调用 KBStore.get_parsed_text()

---

## 六、多轮对话场景

| 场景 | 现有能力 | 需要新增 | 状态 |
|------|---------|---------|------|
| 没有立即回复 | 持久化 | 超时清理 | ⚠️ |
| "帮我总结" | 完整 | - | ✅ |
| "提取数据" | 完整 | - | ✅ |
| 再发第二个文件 | metadata修改 | 覆盖逻辑 | ✅ |
| 无关问题 | Agent判断 | 超时清理 | ⚠️ |
| 任务失败 | 异常处理 | - | ✅ |
| 执行中再发消息 | 消息队列 | - | ✅ |
| 服务重启 | 持久化 | - | ✅ |

---

## 七、调查文件清单

| 文件 | 关键行号 |
|------|---------|
| 
anobot/agent/loop.py | 1409-2650 |
| 
anobot/agent/context.py | 298-320 |
| 
anobot/runtime_context.py | 25-150 |
| 
anobot/session/manager.py | 137-170 |
| 
anobot/session/state.py | 36-850 |

---

## 八、最终结论

### 可以进入第三阶段？

✅ **是的，可以进入详细设计阶段**

**理由**:
- 技术路径明确
- 修改点已确定
- 风险已评估
- 工作量已预估

### 技术阻塞

仅2个需要解决：
1. ⚠️ 超时清理机制
2. ⚠️ KB工具缺失

**非阻塞**，可在实施阶段解决

### 预估工作量

- AgentLoop修改: 2-3小时
- KB工具实现: 3-4小时
- 超时清理: 1-2小时
- 测试: 2-3小时
- **总计**: 8-12小时

---

**调查完成**: 2026-10-30  
**版本**: v1.0  
**状态**: ✅ **第二阶段完成**
