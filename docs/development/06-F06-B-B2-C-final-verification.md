# F06-B-B2-C：编码前最后一轮设计核验报告

**核验日期**: 2026-10-30  
**状态**: ✅ 核验完成  

---

## 执行摘要

### 核心发现

1. ✅ **文件状态定义清晰** - KB status与pending_file.status独立
2. ✅ **身份验证可靠** - RequestContext.sender_id可用
3. ✅ **Provider机制安全** - 顺序调用，返回None不影响其他
4. ⚠️ **需要调整清理策略** - 不能简单标记后就不注入

### 需要调整的设计

1. ❌ **不使用context_injected标记** - 会导致后续对话无法访问文件
2. ✅ **每轮都注入Context** - Agent无状态，需要每轮提供
3. ✅ **不实现主动清理** - 依赖新文件覆盖和Session过期
4. ✅ **简化实现** - 仅在AgentLoop中添加provider方法

---

## 核验1：文件状态定义

### 1.1 KB文件记录status

**代码位置**: `nanobot/kb/store.py:26`, `nanobot/kb/processor.py:22-213`

**KB文件的status值**:
- `"pending"` - 文件刚上传，等待处理 (默认值)
- `"indexed"` - 文件处理成功，已建立索引
- `"failed"` - 文件处理失败

### 1.2 pending_file.status

**代码位置**: `nanobot/channels/feishu/runtime.py:1987`

**pending_file的status值**:
- `"pending"` - 仅此一个值（固定）

### 1.3 状态对应关系

| KB status | pending_file保存? | Context注入? |
|-----------|------------------|-------------|
| pending | ✅ 是 | ✅ 是 |
| indexed | ✅ 是 | ✅ 是 |
| failed | ❌ 否 | ❌ 否 |

**结论**: 
- ✅ KB status=indexed: **允许注入**
- ✅ KB status=pending: **允许注入**
- ✅ KB status=failed: **B2-A已阻止保存**

### 1.4 context_injected字段

**检查结果**: ❌ **不存在**

---

## 核验2：身份验证来源

### 2.1 RequestContext定义

**代码位置**: `nanobot/agent/tools/context.py:32-52`

**关键字段**:
- ✅ `sender_id: str | None` - 存在
- ✅ `session_key: str | None` - 存在
- ✅ `channel: str` - 存在

### 2.2 session_key生成

**代码位置**: `nanobot/channels/feishu/runtime.py:1992, 3262-3264`

**生成规则**:
- 私聊: `feishu:{sender_id}`
- 群聊: `feishu:{chat_id}` 或 `feishu:{chat_id}:{root_id}`

**结论**: ✅ **私聊session_key与sender_id绑定**

### 2.3 身份验证可靠性

**验证方式**: 通过sender_id或session_key

**结论**: ✅ **身份验证可靠**

---

## 核验3：Provider生命周期与并发

### 3.1 调用时机

**代码位置**: `nanobot/agent/loop.py:2210-2211`

**结论**: ✅ **每轮USER消息都会调用**

### 3.2 Provider执行机制

**代码位置**: `nanobot/runtime_context.py:109-117`

**关键点**:
- ✅ 顺序调用 (for loop)
- ✅ Provider返回None不影响其他

### 3.3 context_injected语义分析

**问题**:
```
T1: 用户上传文件 → pending_file保存
T2: 用户提问"总结文档" → 注入Context → 标记context_injected=True
T3: 用户继续"第3章讲什么?" → 检查context_injected → 不注入 ← 问题!
```

**结论**: ❌ **context_injected会破坏多轮对话**

**正确方案**: 
- ✅ 不使用context_injected标记
- ✅ 每轮都注入Context

---

## 核验4：过期策略

### 4.1 当前B2-A实现

**清理机制**: ❌ **不存在**

### 4.2 调整后的策略

**策略1**: 新文件覆盖 - B2-A已实现  
**策略2**: 文件删除清理 - Provider中检查  
**策略3**: 不主动清理 - 简化实现

**B2-C采用**: 策略1+策略2+策略3

---

## 核验5：Provider注册接口

### 5.1 类型定义

```python
RuntimeContextProvider: TypeAlias = Callable[
    ["RequestContext"], Awaitable[RuntimeContextResult]
]
```

### 5.2 注册方式

**位置**: `AgentLoop.__init__()`

**方法**: 直接append到`_runtime_context_providers`

---

## 需要调整的设计

### 调整1：不使用context_injected标记

**原因**: 会破坏多轮对话  
**新方案**: 每轮都注入

### 调整2：不实现主动清理

**原因**: 用户可能需要持续使用  
**新方案**: 依赖新文件覆盖

### 调整3：简化验证逻辑

**新验证**:
1. ✅ pending_file存在
2. ✅ sender_id匹配 (可选)
3. ✅ 文件存在

---

## 最小实现方案

### 实现范围

**修改文件**: 仅`nanobot/agent/loop.py`

### Provider实现概要

```python
async def _pending_file_context_provider(
    self,
    request: RequestContext,
) -> RuntimeContextBlock | None:
    # 1. 读取Session
    # 2. 读取pending_file
    # 3. 验证sender (可选)
    # 4. 验证文件存在
    # 5. 构建RuntimeContextBlock
    # 6. 返回
```

### 注册Provider

```python
# 在__init__()中
self._runtime_context_providers.append(
    self._pending_file_context_provider
)
```

---

## B2-C最终实施范围

### 修改文件

**仅修改**: `nanobot/agent/loop.py`

### 测试文件

**新增**: `tests/test_b2c_context_injection.py`

### 工作量

**预估**: 1-2小时

---

## ✅ 可以开始编码

**准备就绪**:
- [x] 文件状态定义清晰
- [x] 身份验证可靠
- [x] Provider机制理解
- [x] 设计已调整
- [x] 实现方案明确

**调整后的方案**:
- ✅ 不使用context_injected
- ✅ 每轮都注入Context
- ✅ 不实现主动清理
- ✅ 仅在AgentLoop中实现

---

**核验完成时间**: 2026-10-30  
**状态**: ✅ **核验完成，可以立即开始B2-C编码**
