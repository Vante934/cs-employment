# 功能验收标准

本文档定义每个功能的详细验收条件和测试方法。

## F01: 飞书文件接收

### 验收条件

#### AC1.1: 支持的文件格式可以被接收
**前置条件**：
- 飞书机器人已配置并运行
- 用户已添加机器人

**测试步骤**：
1. 用户通过飞书发送 test.pdf（< 10MB）
2. 观察机器人响应

**预期结果**：
- ✅ 机器人回复："文件接收成功，正在处理... (ID: xxx)"
- ✅ 文件保存到 `~/.nanobot/workspace/kb/files/<file_id>.pdf`
- ✅ kb_files 表有对应记录
- ✅ status = 'pending'

**测试数据**：
- test.pdf（正常 PDF，5MB）
- test.docx（正常 DOCX，2MB）
- test.txt（UTF-8 文本，100KB）
- test.md（Markdown，50KB）
- test.xlsx（Excel，1MB）

#### AC1.2: 不支持的文件格式有明确提示
**测试步骤**：
1. 用户发送 test.exe
2. 用户发送 test.zip
3. 用户发送 test.mp4

**预期结果**：
- ✅ 机器人回复："不支持的文件格式: .exe，目前支持 PDF、DOCX、TXT、Markdown、XLSX"
- ✅ 不创建 kb_files 记录
- ✅ 不下载文件

#### AC1.3: 文件大小超限有提示
**测试步骤**：
1. 用户发送 large.pdf（100MB，超过限制 50MB）

**预期结果**：
- ✅ 机器人回复："文件过大 (104857600 bytes)，当前限制为 50MB"
- ✅ 不创建记录

#### AC1.4: 重复文件被识别
**测试步骤**：
1. 用户发送 test.pdf（第一次）
2. 等待接收成功
3. 用户再次发送相同的 test.pdf（内容完全相同）

**预期结果**：
- ✅ 第一次：正常接收
- ✅ 第二次：机器人回复："文件已存在: test.pdf (ID: xxx)"
- ✅ 不创建新的 kb_files 记录
- ✅ file_hash 匹配到已有记录

**注意**：相同文件名但内容不同应视为不同文件。

#### AC1.5: 文件元数据正确记录
**验证方法**：查询数据库

```sql
SELECT * FROM kb_files WHERE file_id = '<test_file_id>';
```

**预期字段**：
- ✅ file_id: UUID 格式
- ✅ filename: 原始文件名
- ✅ file_type: 正确的格式（pdf/docx/txt/md/xlsx）
- ✅ file_size: 正确的字节数
- ✅ file_path: 实际存储路径
- ✅ file_hash: 64 字符 SHA256
- ✅ feishu_message_id: 飞书消息 ID
- ✅ feishu_file_key: 飞书文件 Key
- ✅ sender_id: 发送者 ID
- ✅ status: 'pending'
- ✅ created_at: ISO 8601 格式
- ✅ updated_at: ISO 8601 格式

#### AC1.6: 下载失败有错误反馈
**模拟方法**：
- 断网后发送文件
- 或发送失效的文件链接

**预期结果**：
- ✅ 机器人回复："文件下载失败: <错误原因>"
- ✅ 不创建 kb_files 记录（或标记为 failed）

### 测试清单

| 测试用例 | 状态 | 执行日期 | 执行人 | 备注 |
|---------|------|---------|--------|------|
| AC1.1: PDF 接收 | ⏸️ 待测试 | - | - | |
| AC1.1: DOCX 接收 | ⏸️ 待测试 | - | - | |
| AC1.1: TXT 接收 | ⏸️ 待测试 | - | - | |
| AC1.1: MD 接收 | ⏸️ 待测试 | - | - | |
| AC1.1: XLSX 接收 | ⏸️ 待测试 | - | - | |
| AC1.2: 不支持格式 | ⏸️ 待测试 | - | - | |
| AC1.3: 文件超限 | ⏸️ 待测试 | - | - | |
| AC1.4: 重复文件 | ⏸️ 待测试 | - | - | |
| AC1.5: 元数据验证 | ⏸️ 待测试 | - | - | |
| AC1.6: 下载失败 | ⏸️ 待测试 | - | - | |

---

## F02: 多格式文件解析

### 验收条件

#### AC2.1: PDF 文件能被解析
**测试文件**：test-samples/test.pdf（3页，包含标题和正文）

**验证步骤**：
1. 上传文件并等待解析完成
2. 查询数据库

```sql
SELECT status, summary, page_count, word_count 
FROM kb_files 
WHERE file_id = '<file_id>';

SELECT COUNT(*) as chunk_count 
FROM kb_chunks 
WHERE file_id = '<file_id>';
```

**预期结果**：
- ✅ status = 'parsed' 或 'indexed'
- ✅ summary 不为空（LLM 生成的摘要）
- ✅ keywords 不为空（JSON 数组）
- ✅ page_count = 3
- ✅ word_count > 0
- ✅ chunk_count > 0（至少有切片）
- ✅ 每个 chunk 的 source_page 有值

**内容验证**：
```sql
SELECT chunk_text, source_page 
FROM kb_chunks 
WHERE file_id = '<file_id>' 
ORDER BY chunk_index 
LIMIT 3;
```
- ✅ chunk_text 包含实际提取的文本
- ✅ source_page 与实际页码对应

#### AC2.2: DOCX 文件能被解析
**测试文件**：test-samples/test.docx（包含标题、段落、表格）

**预期结果**：
- ✅ status = 'parsed' 或 'indexed'
- ✅ 正文内容被提取
- ✅ 表格内容被提取（以文本形式）
- ✅ 切片正确生成

**注意**：首期不要求完美还原表格格式，但表格文本应可读。

#### AC2.3: TXT 文件能被解析
**测试文件**：test-samples/test.txt（UTF-8 编码，多段落）

**预期结果**：
- ✅ status = 'parsed' 或 'indexed'
- ✅ 全文内容正确读取
- ✅ 中文、英文、特殊字符正确处理

#### AC2.4: Markdown 文件能被解析
**测试文件**：test-samples/test.md（包含标题、代码块、列表）

**预期结果**：
- ✅ status = 'parsed' 或 'indexed'
- ✅ Markdown 语法可选保留或转换
- ✅ 内容可读

#### AC2.5: XLSX 文件能被解析
**测试文件**：test-samples/test.xlsx（3个工作表，包含表头和数据）

**预期结果**：
- ✅ status = 'parsed' 或 'indexed'
- ✅ 每个工作表的数据被提取
- ✅ kb_chunks 的 source_section 记录工作表名

**验证**：
```sql
SELECT DISTINCT source_section 
FROM kb_chunks 
WHERE file_id = '<file_id>';
```
应包含所有工作表名。

#### AC2.6: 损坏文件不导致服务崩溃
**测试文件**：
- corrupted.pdf（损坏的 PDF）
- invalid.docx（无效的 DOCX）

**预期结果**：
- ✅ status = 'failed'
- ✅ error_message 记录错误原因
- ✅ 机器人回复："文件解析失败: <错误原因>"
- ✅ 服务继续运行，可处理其他文件

#### AC2.7: 空文件处理正确
**测试文件**：empty.txt（0 字节）

**预期结果**：
- ✅ status = 'failed' 或特殊处理
- ✅ 明确提示："文件为空，无法解析"

#### AC2.8: 文本切片逻辑正确
**验证方法**：
1. 上传一个已知内容的文本文件（1000 字符）
2. 查询切片

```sql
SELECT chunk_index, LENGTH(chunk_text) as length 
FROM kb_chunks 
WHERE file_id = '<file_id>' 
ORDER BY chunk_index;
```

**预期结果**：
- ✅ 每个 chunk 长度约 500 字符（±50）
- ✅ chunk_index 连续（0, 1, 2, ...）
- ✅ 最后一个 chunk 可能较短
- ✅ 相邻 chunk 有约 50 字符重叠

#### AC2.9: 摘要和关键词生成
**验证方法**：
```sql
SELECT summary, keywords 
FROM kb_files 
WHERE file_id = '<file_id>';
```

**预期结果**：
- ✅ summary 是简洁的文件内容概括（< 200 字）
- ✅ keywords 是 JSON 数组，包含 3-5 个关键词
- ✅ 内容与文件实际主题相关

### 测试清单

| 测试用例 | 状态 | 执行日期 | 执行人 | 备注 |
|---------|------|---------|--------|------|
| AC2.1: PDF 解析 | ⏸️ 待测试 | - | - | |
| AC2.2: DOCX 解析 | ⏸️ 待测试 | - | - | |
| AC2.3: TXT 解析 | ⏸️ 待测试 | - | - | |
| AC2.4: MD 解析 | ⏸️ 待测试 | - | - | |
| AC2.5: XLSX 解析 | ⏸️ 待测试 | - | - | |
| AC2.6: 损坏文件 | ⏸️ 待测试 | - | - | |
| AC2.7: 空文件 | ⏸️ 待测试 | - | - | |
| AC2.8: 切片逻辑 | ⏸️ 待测试 | - | - | |
| AC2.9: 摘要关键词 | ⏸️ 待测试 | - | - | |

---

## F03: 本地知识库存储与索引

### 验收条件

#### AC3.1: 向量索引成功创建
**验证步骤**：
1. 上传并解析文件
2. 等待索引完成
3. 检查文件系统

**预期文件**：
- ✅ `~/.nanobot/workspace/kb/vector_index.faiss` 存在
- ✅ `~/.nanobot/workspace/kb/chunk_mapping.json` 存在
- ✅ `~/.nanobot/workspace/kb/embeddings_meta.json` 存在

**数据库验证**：
```sql
SELECT status, indexed_at 
FROM kb_files 
WHERE file_id = '<file_id>';

SELECT COUNT(*) 
FROM kb_chunks 
WHERE file_id = '<file_id>' 
  AND vector_index_id IS NOT NULL;
```

**预期结果**：
- ✅ status = 'indexed'
- ✅ indexed_at 有时间戳
- ✅ 所有 chunk 的 vector_index_id 不为空

#### AC3.2: 文件、切片、索引关联正确
**验证步骤**：
```sql
SELECT 
    f.filename,
    c.chunk_index,
    c.vector_index_id,
    LENGTH(c.chunk_text) as text_length
FROM kb_files f
JOIN kb_chunks c ON f.file_id = c.file_id
WHERE f.file_id = '<file_id>'
ORDER BY c.chunk_index
LIMIT 5;
```

**预期结果**：
- ✅ 每个 chunk 有对应的 vector_index_id
- ✅ vector_index_id 唯一且递增
- ✅ chunk_index 连续
- ✅ 可以从文件追溯到切片和索引

#### AC3.3: 服务重启后数据可恢复
**测试步骤**：
1. 上传并索引文件 A
2. 记录 file_id 和 chunk 数量
3. 停止 Nanobot 服务
4. 重启 Nanobot 服务
5. 查询文件 A

**预期结果**：
- ✅ kb_files 记录仍存在
- ✅ kb_chunks 记录仍存在
- ✅ FAISS 索引文件仍存在
- ✅ 可以对文件 A 进行检索（验证索引有效）

#### AC3.4: 重复文件按规则处理
**测试步骤**：
1. 上传 test.pdf（内容 A）
2. 修改 test.pdf 的内容（内容 B）
3. 再次上传 test.pdf

**预期结果**：
- ✅ 第一次：正常入库
- ✅ 第二次：因为哈希不同，视为新文件
- ✅ 两个文件都在知识库中
- ✅ 文件名可以相同，file_id 不同

**同内容测试**：
1. 上传 test1.pdf（内容 A）
2. 复制为 test2.pdf（内容仍为 A）
3. 上传 test2.pdf

**预期结果**：
- ✅ 哈希相同，识别为重复
- ✅ 提示："文件已存在: test1.pdf"
- ✅ 不创建新记录

#### AC3.5: 存储失败不留下错误记录
**模拟方法**：
- 磁盘满（模拟）
- 权限问题（模拟）

**预期结果**：
- ✅ 事务回滚，不创建半成品记录
- ✅ 或明确标记为 'failed'
- ✅ 用户收到明确错误提示

### 测试清单

| 测试用例 | 状态 | 执行日期 | 执行人 | 备注 |
|---------|------|---------|--------|------|
| AC3.1: 索引创建 | ⏸️ 待测试 | - | - | |
| AC3.2: 关联正确 | ⏸️ 待测试 | - | - | |
| AC3.3: 重启恢复 | ⏸️ 待测试 | - | - | |
| AC3.4: 重复处理 | ⏸️ 待测试 | - | - | |
| AC3.5: 存储失败 | ⏸️ 待测试 | - | - | |

---

## F04: 知识检索与问答

### 验收条件

#### AC4.1: 能找到相关已入库资料
**前置条件**：
- 已入库文件 "Python教程.pdf"，包含装饰器章节

**测试步骤**：
1. 用户问："Python 的装饰器是什么？"
2. 观察机器人响应

**预期结果**：
- ✅ LLM 调用 kb_search Tool
- ✅ kb_search 返回相关切片（包含"装饰器"内容）
- ✅ LLM 生成的回答基于检索结果
- ✅ 回答中引用来源："根据 Python教程.pdf 第X页..."

#### AC4.2: 回答中的来源真实存在
**验证方法**：
- 解析机器人回答中的来源信息
- 查询数据库验证

```sql
SELECT f.filename, c.source_page, c.chunk_text
FROM kb_files f
JOIN kb_chunks c ON f.file_id = c.file_id
WHERE f.filename = 'Python教程.pdf' 
  AND c.source_page = <回答中提到的页码>;
```

**预期结果**：
- ✅ 文件确实存在
- ✅ 页码确实存在
- ✅ 该页内容与回答相关

#### AC4.3: 无相关资料时不伪造来源
**测试步骤**：
1. 用户问："量子计算的原理是什么？"（假设未入库相关文件）
2. 观察机器人响应

**预期结果**：
- ✅ kb_search 返回空或低相关度结果
- ✅ LLM 回答："知识库中未找到相关资料。以下是我的一般知识回答..."
- ✅ 明确区分知识库答案和一般知识

#### AC4.4: 未入库文件不会误认为已存在
**测试步骤**：
1. 用户问："根据 XX报告.pdf，今年的趋势是什么？"
2. 但 XX报告.pdf 未入库

**预期结果**：
- ✅ 机器人回复："知识库中没有 XX报告.pdf，请先上传文件"
- ✅ 或："我在知识库中未找到该文件"

#### AC4.5: 检索异常有错误处理
**模拟方法**：
- 删除 vector_index.faiss 文件
- 或损坏 chunk_mapping.json

**预期结果**：
- ✅ 不崩溃
- ✅ 返回："检索服务暂时不可用，请稍后重试"
- ✅ 记录错误日志

#### AC4.6: 支持后续追问
**测试步骤**：
1. 用户问："Python 装饰器是什么？"（第一次检索）
2. 机器人回答
3. 用户追问："能举个例子吗？"

**预期结果**：
- ✅ LLM 理解上下文
- ✅ 再次调用 kb_search 或基于已有结果
- ✅ 回答延续前一个话题

### 测试清单

| 测试用例 | 状态 | 执行日期 | 执行人 | 备注 |
|---------|------|---------|--------|------|
| AC4.1: 找到资料 | ⏸️ 待测试 | - | - | |
| AC4.2: 来源真实 | ⏸️ 待测试 | - | - | |
| AC4.3: 无资料不伪造 | ⏸️ 待测试 | - | - | |
| AC4.4: 未入库识别 | ⏸️ 待测试 | - | - | |
| AC4.5: 异常处理 | ⏸️ 待测试 | - | - | |
| AC4.6: 后续追问 | ⏸️ 待测试 | - | - | |

---

## F05: 基础知识库管理

### 验收条件

#### AC5.1: 文件列表与数据库一致
**测试步骤**：
1. 用户："列出知识库文件"
2. 机器人调用 kb_list Tool
3. 对比数据库记录

```sql
SELECT file_id, filename, status, created_at 
FROM kb_files 
ORDER BY created_at DESC;
```

**预期结果**：
- ✅ 返回的文件列表与数据库一致
- ✅ 显示文件名、状态、上传时间
- ✅ 状态图标正确（✅ indexed / ⏳ parsing / ❌ failed）

#### AC5.2: 删除后内容不再参与检索
**测试步骤**：
1. 上传并索引 test.pdf
2. 验证可以检索到相关内容
3. 删除 test.pdf
4. 再次检索相同问题

**预期结果**：
- ✅ 删除前：检索到结果
- ✅ 删除后：检索不到结果（或相关度大幅下降）
- ✅ kb_files 记录被删除
- ✅ kb_chunks 记录被级联删除
- ✅ 本地文件被删除

#### AC5.3: 删除失败不错误报告成功
**模拟方法**：
- 文件已被手动删除（文件系统不一致）
- 或文件被占用

**预期结果**：
- ✅ 尝试删除操作
- ✅ 如果文件系统删除失败，提示："文件删除部分失败，数据库记录已清理"
- ✅ 或回滚整个操作，提示失败原因

#### AC5.4: 重试不创建重复记录
**测试步骤**：
1. 上传文件，解析失败（status='failed'）
2. 用户："重试文件 <file_id>"
3. 观察数据库

**预期结果**：
- ✅ 不创建新的 kb_files 记录
- ✅ 更新现有记录的 status 为 'pending'
- ✅ 重新触发解析任务
- ✅ 成功后 status 更新为 'indexed'

#### AC5.5: 操作结果通过飞书反馈
**测试步骤**：
1. 各种管理操作（列表、删除、重试）
2. 观察飞书机器人响应

**预期结果**：
- ✅ 每个操作有明确反馈
- ✅ 成功：显示操作结果
- ✅ 失败：显示失败原因
- ✅ 响应时间 < 5 秒

### 测试清单

| 测试用例 | 状态 | 执行日期 | 执行人 | 备注 |
|---------|------|---------|--------|------|
| AC5.1: 列表一致 | ⏸️ 待测试 | - | - | |
| AC5.2: 删除检索 | ⏸️ 待测试 | - | - | |
| AC5.3: 删除失败 | ⏸️ 待测试 | - | - | |
| AC5.4: 重试不重复 | ⏸️ 待测试 | - | - | |
| AC5.5: 飞书反馈 | ⏸️ 待测试 | - | - | |

---

## 集成测试场景

### 场景 1: 完整的文件上传到问答流程
**步骤**：
1. 用户发送 Python教程.pdf
2. 等待处理完成
3. 用户问："什么是列表推导式？"
4. 验证回答和来源

**预期**：
- ✅ 全流程顺畅
- ✅ 每个阶段有反馈
- ✅ 最终答案准确且有来源

### 场景 2: 多文件混合检索
**步骤**：
1. 上传 Python教程.pdf、Java教程.pdf
2. 用户问："Python 和 Java 的语法有什么区别？"

**预期**：
- ✅ 同时从两个文件检索
- ✅ 回答综合两个来源
- ✅ 明确标注各来源

### 场景 3: 错误恢复
**步骤**：
1. 上传损坏的 broken.pdf（解析失败）
2. 用户查看列表，看到失败状态
3. 上传正确的 fixed.pdf
4. 删除 broken.pdf

**预期**：
- ✅ 失败不影响后续操作
- ✅ 可以继续使用系统
- ✅ 清理操作有效

---

*文档版本*: v0.1  
*最后更新*: 2025-01-XX  
*状态*: 草案
