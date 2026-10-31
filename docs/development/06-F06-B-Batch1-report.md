# F06-B 批次1：工具输出安全优化 - 完成报告

**实施日期**: 2026-10-30  
**批次**: Batch-1（工具输出安全优化）  
**状态**: ✅ 完成  

---

## 执行摘要

### 完成内容

1. ✅ 优化kb_search返回格式 - 移除"[Result X]"标记
2. ✅ 优化kb_read返回格式 - 简化分页信息展示
3. ✅ System Prompt约束 - 增加工具输出处理规则
4. ✅ 24个B2-B测试通过
5. ✅ 21个B2-C回归测试通过
6. ✅ 无尾随空格

---

## 一、问题分析

### 1.1 泄漏根因（调查报告确认）

**泄漏路径**:
```
工具返回: "File: document.pdf\nPart 1 of 3\n[内容]"
  ↓
模型上下文: {"role": "tool", "content": "..."}
  ↓
模型生成: "我已读取文件document.pdf第1部分，共3部分..."
  ↓
用户看到: 内部分页信息、文件处理细节
```

**判定**: 模型主动复述工具输出中的技术细节

### 1.2 解决策略

**两层防护**:
1. **工具层**: 简化返回格式，减少技术细节
2. **Prompt层**: 约束模型不要复述内部信息

---

## 二、具体修改

### 2.1 kb_search返回格式优化

**文件**: `nanobot/agent/tools/kb_access.py`

**修改前**:
```python
output_lines = []
for i, result in enumerate(response.results, 1):
    output_lines.append(f"[Result {i}]")  # ← 技术标记
    output_lines.append(result.chunk_text)
    if result.source_page:
        output_lines.append(f"(Page {result.source_page})")
    output_lines.append("")  # blank line

return "\n".join(output_lines).strip()
```

**修改后**:
```python
output_lines = []
for i, result in enumerate(response.results, 1):
    output_lines.append(result.chunk_text)  # ✅ 直接返回内容
    if result.source_page:
        output_lines.append(f"(Page {result.source_page})")  # ✅ 保留页码
    if i < len(response.results):
        output_lines.append("")  # blank line between results

return "\n".join(output_lines)
```

**改进**:
- ✅ 移除"[Result X]"技术标记
- ✅ 保留chunk_text（完整内容）
- ✅ 保留source_page（必要的来源信息）
- ✅ 保留结果间空行（可读性）

**不影响**:
- 搜索范围
- 相似度排序
- Top-K数量
- 权限验证

### 2.2 kb_read返回格式优化

**文件**: `nanobot/agent/tools/kb_access.py`

**修改前**:
```python
filename = file_record.get("filename", "file")
result_lines = [
    f"File: {filename}",  # ← 技术标记
    f"Part {part_index + 1} of {total_parts}",  # ← 技术标记
    "",
    part_text
]

if part_index < total_parts - 1:
    result_lines.append("")
    result_lines.append(f"(Use part_index={part_index + 1} to read the next part)")  # ← 工具参数

return "\n".join(result_lines)
```

**修改后**:
```python
filename = file_record.get("filename", "document")

# Only show pagination info if there are multiple parts
if total_parts > 1:
    header = f"Content from {filename} (Part {part_index + 1} of {total_parts}):"  # ✅ 自然语言
else:
    header = f"Content from {filename}:"  # ✅ 单部分不显示分页

result_lines = [header, "", part_text]

# Only suggest next part if not the last part
if part_index < total_parts - 1:
    result_lines.append("")
    result_lines.append(f"(There are more parts. Use part_index={part_index + 1} to continue reading.)")  # ✅ 自然描述

return "\n".join(result_lines)
```

**改进**:
- ✅ 使用自然语言描述："Content from {filename}"
- ✅ 单部分文件不显示分页信息
- ✅ 多部分时自然提示："There are more parts"
- ✅ 保留part_text（完整正文）
- ✅ 保留filename（必要的来源信息）

**不影响**:
- part_index功能
- 10000字符分段
- 分页计算
- 权限验证

### 2.3 System Prompt约束

**文件**: `nanobot/templates/agent/tool_contract.md`

**新增章节**:
```markdown
## Tool Output Handling

- Tool calls and their results are internal execution details.
- Do not reproduce raw tool outputs, internal commands, temporary file paths, or system execution details in your response to the user.
- When presenting file content obtained through tools, focus on the actual content and its meaning, not on the retrieval mechanism.
- If a tool returns technical metadata (like "Part X of Y" or internal paths), extract and use only the essential information the user needs.
- When users ask about file contents, provide the information naturally without mentioning internal tools, parameters, or execution steps.
- Do not fabricate or modify tool results — use them accurately while presenting them in a user-friendly way.
```

**约束原则**:
1. ✅ 工具调用是内部细节
2. ✅ 不复述原始输出、命令、路径
3. ✅ 关注内容和意义，不关注检索机制
4. ✅ 提取必要信息，忽略技术元数据
5. ✅ 自然呈现内容，不提及工具执行
6. ✅ 准确使用结果，不编造（诚实原则）

**不影响**:
- 工具执行逻辑
- 工具返回结构
- 其他System Prompt规则

---

## 三、测试结果

### 3.1 B2-B专项测试

**测试文件**: `tests/test_b2b_kb_tools.py`

**结果**: ✅ 24/24通过 (100%)

**测试覆盖**:
- 工具基础结构: 8/8 ✅
- 权限验证: 3/3 ✅
- 文件范围搜索: 7/7 ✅
- kb_read功能: 6/6 ✅

**关键验证**:
- kb_search搜索功能正常
- kb_read分页功能正常
- 权限验证未破坏
- Top-K排序正确
- 10000字符分段正确

### 3.2 B2-C回归测试

**测试文件**: `tests/test_b2c_context_injection.py`

**结果**: ✅ 21/21通过 (100%)

**测试覆盖**:
- Context注入: 4/4 ✅
- 身份隔离: 4/4 ✅
- 多轮行为: 3/3 ✅
- 状态清理: 3/3 ✅
- KB状态处理: 3/3 ✅
- 异常隔离: 3/3 ✅
- 普通对话: 1/1 ✅

**兼容性确认**:
- Session Context注入正常
- pending_file机制正常
- 用户隔离正常
- 状态管理正常

### 3.3 测试覆盖场景

✅ **场景1**: kb_search正常搜索，模型能根据结果回答  
✅ **场景2**: kb_read正常读取，模型能总结内容  
✅ **场景3**: 用户要求提取具体数字，回答准确（内容完整）  
✅ **场景4**: 用户要求整理内容，回答完整（正文未删减）  
⚠️ **场景5**: 工具返回包含路径，用户回答不泄漏（需端到端验证）  
⚠️ **场景6**: 工具异常，用户看不到堆栈（需端到端验证）  
✅ **场景7**: 用户询问文件内容，不错误屏蔽（内容保留）  
✅ **场景8**: B2-B权限和分页功能正常  

**说明**:
- 场景1-4、7-8: 单元测试已覆盖
- 场景5-6: 需要飞书端到端测试验证模型行为

---

## 四、修改范围

### 4.1 修改文件清单

**文件1**: `nanobot/agent/tools/kb_access.py` (新文件)
- kb_search返回格式优化（移除"[Result X]"）
- kb_read返回格式优化（自然语言描述）

**文件2**: `nanobot/templates/agent/tool_contract.md` (+9行)
- 新增"Tool Output Handling"章节
- 约束模型不复述工具细节

### 4.2 未修改文件

✅ **不修改**:
- `nanobot/agent/loop.py` - AgentLoop
- `nanobot/agent/runner.py` - AgentRunner
- `nanobot/agent/tools/registry.py` - ToolRegistry
- `nanobot/providers/` - Provider层
- `nanobot/kb/` - KB处理逻辑

### 4.3 代码质量

**尾随空格**: ✅ 无  
**语法检查**: ✅ 通过  
**Linter**: ✅ 无新增错误  

**git diff --check**: ✅ 通过（仅CRLF警告）

---

## 五、兼容性影响

### 5.1 F01-F06 文件处理流程

**影响**: ✅ 无影响

**理由**: 不涉及文件解析、分块、索引

### 5.2 B2-A pending_file

**影响**: ✅ 无影响

**理由**: 不修改Session存储和pending_file机制

### 5.3 B2-B kb_search/kb_read

**影响**: ⚠️ **返回格式变化**

**理由**: 
- 功能不变：搜索、分页、权限完全一致
- 格式变化：移除技术标记，使用自然语言
- 内容不变：正文完整保留，来源信息保留

**测试影响**: 
- 如果有测试断言具体输出格式，需要更新
- 当前B2-B测试无此断言，全部通过

### 5.4 B2-C Context注入

**影响**: ✅ 无影响

**理由**: Context注入在Session级别，不依赖工具返回格式

### 5.5 B2-D 文件意图确认

**影响**: ✅ 无影响

**理由**: 意图确认在文件上传阶段，不涉及kb工具

---

## 六、预期效果

### 6.1 工具返回对比

**kb_search (Before)**:
```
[Result 1]
Python是一种高级编程语言...
(Page 5)

[Result 2]
面向对象编程的核心概念...

[Result 3]
...
```

**kb_search (After)**:
```
Python是一种高级编程语言...
(Page 5)

面向对象编程的核心概念...

...
```

**kb_read (Before)**:
```
File: document.pdf
Part 1 of 3

[正文内容...]

(Use part_index=1 to read the next part)
```

**kb_read (After)**:
```
Content from document.pdf (Part 1 of 3):

[正文内容...]

(There are more parts. Use part_index=1 to continue reading.)
```

### 6.2 用户可见对比

**场景**: 用户问"这个文件讲了什么？"

**Before (可能泄漏)**:
```
我读取了文件document.pdf的第1部分（共3部分）。
[Result 1]显示...
[Result 2]显示...
使用part_index=1可以读取下一部分。
```

**After (优化后)**:
```
根据文件内容，这个文档主要讲了...
具体包括...
```

### 6.3 System Prompt约束效果

**Before**: 模型可能直接复述工具输出  
**After**: 模型被约束提取必要信息，自然呈现  

**注意**: Prompt约束不是100%保证，仍需端到端测试验证

---

## 七、已知限制

### 7.1 Prompt约束不是绝对保证

**说明**: System Prompt是软约束，模型可能仍会复述部分细节

**缓解**: 
- 工具层已减少技术细节
- 双层防护降低泄漏风险

**验证**: 需要飞书端到端测试

### 7.2 未实现最终输出过滤

**决策**: 不在本批次实现

**理由**:
1. 避免误删用户主动提供的内容
2. 避免破坏正常文件内容回答
3. Prompt约束+工具优化已覆盖主要场景

**后续**: 如确需输出过滤，应单独批次实现

### 7.3 异常信息处理

**当前状态**: 工具内部异常返回`ToolResult.error()`

**示例**:
```python
return ToolResult.error("Error: Failed to search the file")
```

**说明**: 已经是用户友好的错误信息，不泄漏堆栈

---

## 八、后续建议

### 8.1 端到端验证（必须）

**测试场景**:
1. 飞书上传文件并搜索
2. 观察Agent回答是否包含"[Result X]"
3. 观察Agent回答是否包含"Part X of Y"
4. 观察Agent回答是否包含内部路径

**验收标准**:
- 用户看不到技术标记
- 用户看不到内部路径
- 用户能正常获取文件内容

### 8.2 监控和反馈

**监控指标**:
- 用户投诉"看到奇怪的技术信息"
- 用户反馈"回答不自然"
- 用户反馈"文件内容不完整"

**调整策略**:
- 如仍有泄漏：强化Prompt或增加输出过滤
- 如回答不自然：调整格式化语言
- 如内容不完整：回退修改

### 8.3 潜在增强（可选）

**方向1**: 结构化ToolResult
```python
@dataclass
class ToolResult:
    content: str  # 完整输出（给模型）
    user_summary: str | None  # 用户友好摘要（可选）
```

**方向2**: 输出后处理
- 在OutboundMessage发送前过滤敏感信息
- 需要精确设计避免误伤

**方向3**: 工具返回格式标准化
- 定义统一的工具输出格式规范
- 自动化去除技术细节

---

## 九、检查清单

- [x] kb_search返回格式优化
- [x] kb_read返回格式优化
- [x] System Prompt约束增加
- [x] 24个B2-B测试通过
- [x] 21个B2-C回归测试通过
- [x] 无尾随空格
- [x] 不破坏F01-F06功能
- [x] 不破坏B2-A pending_file
- [x] 不破坏B2-C Context注入
- [x] 不破坏B2-D 文件意图
- [x] 保留文件正文完整性
- [x] 保留必要来源信息
- [x] 不扩大修改范围
- [x] 不重构AgentLoop
- [x] 不修改Provider

---

**完成时间**: 2026-10-30  
**状态**: ✅ **批次1完成，等待端到端验证**

---

## 总结

### 核心改进

1. **工具层**: 简化返回格式，移除技术标记
2. **Prompt层**: 约束模型不复述工具细节
3. **双层防护**: 降低信息泄漏风险

### 质量保证

- ✅ 45个测试全部通过
- ✅ 无破坏性修改
- ✅ 代码质量合格
- ✅ 向后兼容

### 下一步

**立即**: 飞书端到端验证  
**后续**: 根据反馈调整策略  

工具输出安全优化基础工作完成！
