# F06-B-B2 实施计划

**版本**: v1.0  
**日期**: 2026-10-30

---

## 实施批次

### B2-A: pending_file管理
**时间**: 2-3小时  
**文件**: 
anobot/channels/feishu/runtime.py

**任务**:
1. 提取file_id from KB result
2. 获取文件信息（filename, size, type）
3. 生成摘要（可选，简单实现）
4. 构建pending_file dict
5. 保存到session.metadata
6. 新增_save_pending_file_to_session()

**测试**:
- metadata正确保存
- request_id唯一
- 并发覆盖正确

---

### B2-B: KB工具实现
**时间**: 3-4小时  
**文件**: 
- 
anobot/agent/tools/kb_get_file.py (新建)
- 
anobot/agent/tools/kb_search.py (新建)

**任务**:
1. KBGetFileTool
   - 参数: file_id, max_length
   - 权限验证
   - 读取kb_store.get_parsed_text()
   - 长度限制和截断
2. KBSearchTool
   - 参数: query, file_id, top_k
   - 调用index_manager.search()
   - 格式化结果
3. 工具注册

**依赖**: ToolContext需传递sender_id和session_key

**测试**:
- 正确读取文件
- 权限验证
- 搜索准确性

---

### B2-C: Agent注入
**时间**: 2-3小时  
**文件**: 
anobot/agent/loop.py

**任务**:
1. _build_turn()中【loop.py:2211后】:
   - 读取pending_file
   - 检查超时（lazy cleanup）
   - 构建RuntimeContextBlock
   - 追加到runtime_context_blocks
2. 新增_build_pending_file_context()
3. 清理逻辑:
   - Agent完成后清理
   - 验证request_id

**测试**:
- Context正确注入
- 超时清理
- 无误清理

---

### B2-D: 意图消息
**时间**: 1-2小时  
**文件**: 
anobot/channels/feishu/runtime.py

**任务**:
1. _send_file_greeting(sender_id, file_info)
2. 格式化消息
3. 调用_send_message_sync

**测试**:
- 消息发送成功
- 格式正确

---

### B2-E: 集成测试
**时间**: 2-3小时

**测试**:
- 完整流程测试
- 多轮对话
- 并发场景
- 异常恢复
- B1/B3/B4回归

---

## 依赖关系

`
B2-A → B2-D (意图消息依赖文件信息)
B2-B 独立
B2-C 依赖 B2-A（需要pending_file结构）
B2-E 依赖 B2-A/B/C/D
`

**推荐顺序**: A → D → B → C → E

---

## 关键代码位置

| 任务 | 文件 | 位置 |
|------|------|------|
| 保存pending_file | runtime.py | _auto_upload_to_kb |
| 发送意图消息 | runtime.py | _send_file_greeting |
| 注入Context | loop.py | _build_turn:2211 |
| 清理pending_file | loop.py | _persist_turn |
| KB Get Tool | kb_get_file.py | 新建 |
| KB Search Tool | kb_search.py | 新建 |

---

**总工作量**: 10-15小时
