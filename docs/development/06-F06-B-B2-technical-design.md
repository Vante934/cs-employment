# F06-B-B2 智能问候与意图识别 - 技术设计文档

**设计日期**: 2026-10-30  
**版本**: v1.0  
**状态**: 设计完成  

---

## 执行摘要

### 核心设计决策

1. **pending_file**: 存储在Session.metadata，8个字段
2. **RuntimeContextBlock**: 注入文件元数据+摘要（≤1000字符）
3. **KB工具**: 新增KBGetFileTool和KBSearchTool
4. **意图识别**: LLM自主判断，无需程序预判
5. **状态清理**: Lazy cleanup，5分钟超时，request_id防误删
6. **实施批次**: 5个批次，10-15小时

---

## 一、pending_file数据结构

`python
{
    "file_id": str,          # KB主键
    "filename": str,         # 用户可见名称
    "file_size": int,        # 字节数
    "file_type": str,        # pdf/docx/xlsx等
    "upload_time": str,      # ISO格式时间戳
    "sender_id": str,        # 权限验证用
    "summary": str | None,   # 可选，前200字或LLM生成
    "status": str,           # pending/processing/completed
    "request_id": str,       # UUID，防止误清理
}
`

**关键设计**:
- 
equest_id: 防止旧任务清理新状态
- summary: 可选，失败不阻塞
- 不存储ile_path: 通过file_id从KB查询

---

## 二、RuntimeContextBlock设计

### 注入位置
AgentLoop._build_turn() - loop.py:2211之后

### 内容模板
`
<runtime_context>
The user recently uploaded: {filename} ({size})
Type: {file_type} | Uploaded: {time_ago}

{summary_section}

Use kb_get_file or kb_search to access content.
File ID: {file_id}
</runtime_context>
`

**限制**: 最大1000字符

---

## 三、KB工具设计

### KBGetFileTool
- 读取文件完整内容
- 权限验证（file_id必须在pending_file中）
- 长度限制（默认50k，最大100k）

### KBSearchTool
- 向量搜索KB内容
- 可选file_id过滤
- 返回top_k结果（默认5，最大10）

---

## 四、意图识别

**策略**: LLM自主判断，通过RuntimeContext理解

**用户输入** → **LLM行为**:
- "帮我总结" → kb_get_file
- "搜索XX" → kb_search
- "不用了" → 不调用工具
- "天气" → 正常回答

**意图消息**:
`
📄 收到文件：{filename} ({size})

我可以帮你：
• 总结文档要点
• 提取关键信息
• 回答文档相关问题

请告诉我你想做什么。
`

---

## 五、状态生命周期

### 清理策略
**Lazy Cleanup**: 下次消息时检查超时（5分钟）

### 版本控制
清理时验证
equest_id，防止误删新状态

---

## 六、实施批次

### B2-A: pending_file管理 (2-3h)
- 修改: eishu/runtime.py
- 保存文件信息到session.metadata

### B2-B: KB工具 (3-4h)
- 新建: kb_get_file.py, kb_search.py
- 实现权限验证和内容读取

### B2-C: Agent注入 (2-3h)
- 修改: gent/loop.py
- 注入RuntimeContextBlock
- 清理逻辑

### B2-D: 意图消息 (1-2h)
- 发送格式化的意图确认消息

### B2-E: 集成测试 (2-3h)
- 端到端测试
- 回归测试

**总计**: 10-15小时

---

**设计完成**: 2026-10-30
