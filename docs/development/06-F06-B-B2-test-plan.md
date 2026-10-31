# F06-B-B2 测试计划

**版本**: v1.0  
**日期**: 2026-10-30

---

## 测试策略

### 单元测试
- 每个批次完成后立即测试
- 覆盖核心逻辑和边界条件

### 集成测试
- B2-E阶段执行
- 覆盖完整流程

### 回归测试
- 确保B1/B3/B4不受影响

---

## B2-A: pending_file管理测试

### 单元测试

**测试1: pending_file正确保存**
`python
async def test_save_pending_file():
    # 上传文件
    # 验证session.metadata["pending_file"]存在
    # 验证字段完整性
`

**测试2: request_id唯一性**
`python
def test_request_id_unique():
    # 连续上传两个文件
    # 验证request_id不同
`

**测试3: 文件覆盖**
`python
async def test_file_overwrite():
    # 上传文件1
    # 上传文件2
    # 验证pending_file为文件2
`

---

## B2-B: KB工具测试

### 单元测试

**测试4: kb_get_file读取文件**
`python
async def test_kb_get_file():
    # 创建测试文件
    # 调用工具
    # 验证返回内容正确
`

**测试5: 权限验证**
`python
async def test_kb_get_file_permission():
    # 尝试访问不在pending_file中的file_id
    # 验证返回错误
`

**测试6: 长度限制**
`python
async def test_kb_get_file_length_limit():
    # 创建大文件
    # 验证返回内容被截断
`

**测试7: kb_search搜索**
`python
async def test_kb_search():
    # 创建测试文件并索引
    # 搜索关键词
    # 验证返回相关结果
`

---

## B2-C: Agent注入测试

### 单元测试

**测试8: RuntimeContext注入**
`python
async def test_runtime_context_injection():
    # 设置pending_file
    # 发送消息
    # 验证context包含文件信息
`

**测试9: 超时清理**
`python
async def test_timeout_cleanup():
    # 设置pending_file，时间为6分钟前
    # 发送消息
    # 验证pending_file被清理
`

**测试10: request_id防误清理**
`python
async def test_request_id_protection():
    # 上传文件1，保存request_id_1
    # 模拟Agent开始处理
    # 上传文件2（覆盖）
    # 模拟Agent完成，尝试用request_id_1清理
    # 验证pending_file未被清理
`

---

## B2-D: 意图消息测试

### 单元测试

**测试11: 消息发送**
`python
async def test_file_greeting_sent():
    # 上传文件
    # 验证意图消息发送
    # 验证消息格式正确
`

**测试12: 发送失败不阻塞**
`python
async def test_greeting_failure_non_blocking():
    # Mock发送失败
    # 验证pending_file仍保存
`

---

## B2-E: 集成测试

### 端到端测试

**测试13: 完整流程**
`python
async def test_full_workflow():
    # 1. 用户上传文件
    # 2. 验证进度消息（B3）
    # 3. 验证意图消息（B2）
    # 4. 验证pending_file保存
    # 5. 用户:"帮我总结"
    # 6. 验证Agent调用kb_get_file
    # 7. 验证返回总结
    # 8. 验证pending_file清理
`

**测试14: 多轮对话**
`python
async def test_multi_turn():
    # 1. 上传文件
    # 2. 用户:"帮我总结"
    # 3. Agent返回总结
    # 4. 用户:"提取关键数据"
    # 5. 验证Agent仍能访问文件
`

### 并发测试

**测试15: 同用户多文件**
`python
async def test_concurrent_files_same_user():
    # 快速上传文件1和文件2
    # 验证pending_file为文件2
`

**测试16: 不同用户**
`python
async def test_concurrent_different_users():
    # 用户A上传文件A
    # 用户B上传文件B
    # 验证隔离
`

### 异常测试

**测试17: 文件删除**
`python
async def test_file_deleted():
    # 上传文件
    # 手动删除KB中的文件
    # 用户:"总结"
    # 验证错误提示
`

**测试18: 服务重启**
`python
async def test_service_restart():
    # 上传文件
    # 模拟重启
    # 3分钟后用户发消息
    # 验证pending_file恢复
`

**测试19: Agent失败**
`python
async def test_agent_failure():
    # 上传文件
    # Mock kb_get_file失败
    # 验证pending_file保留
    # 用户重试
    # 验证成功
`

---

## 回归测试

### B1回归

**测试20: 欢迎语不受影响**
`python
async def test_welcome_message_unchanged():
    # 新用户首次交互
    # 验证欢迎语正确发送
`

### B3回归

**测试21: 进度消息保留**
`python
async def test_progress_messages_unchanged():
    # 上传文件
    # 验证4条进度消息
`

### B4回归

**测试22: Reaction保留**
`python
async def test_reaction_unchanged():
    # 上传文件
    # 验证reaction添加
`

---

## 飞书真实测试

### 手工测试清单

- [ ] 上传PDF文件
- [ ] 验证进度消息
- [ ] 验证意图消息
- [ ] 回复"帮我总结"
- [ ] 验证总结内容
- [ ] 上传第二个文件
- [ ] 验证覆盖行为
- [ ] 回复无关问题
- [ ] 验证Agent正常回答
- [ ] 等待6分钟
- [ ] 发送消息
- [ ] 验证提示重新上传

---

## 测试覆盖目标

- **单元测试**: 80%+
- **集成测试**: 核心流程100%
- **回归测试**: B1/B3/B4全覆盖
- **手工测试**: 关键场景验证

---

**测试工作量**: 2-3小时
