# Fleet Project 业务 PRD

- 日期：2026-10-30
- 状态：待评审
- 范围：Agent Fleet 的 Project 业务能力，不包含游戏表现层
- 依赖文档：`docs/prds/2026-10-30-fleet-game-presentation-prd.md`

## 1. 背景

当前 Fleet 已有 Agent、Task、LOOP、Thread 与 OUTPUT，但没有把多个 Task 组织成长期工作的 Project。Task 可以完成一次工作，却不能自然回答：

- 一批 Task 为什么属于同一件事？
- 新 Task 如何复用此前已经确认的背景与资料？
- 历次交付在哪里持续积累并被再次引用？
- 人与多个 Agent 如何围绕同一长期目标协作？

DeerFlow 原生已有 Project、成员 Thread、Project instructions、文档架和 Thread 文件聚合能力，但 Fleet 控制面与 Console 尚未接入这些概念。Fleet Task 当前也没有 Project 归属。

## 2. 产品定义

**Project 是一个持续存在的协作空间，让多个 Task 共享长期上下文、资料与交付积累。**

Project 不是放大的 Task，不是一次对话，也不是所有 Agent 共同读写的沙盒目录。

用户心智统一为：

- Agent 是协作者。
- Task 是一次工作，也是一场群聊。
- 人、主 Agent 与 subagent 在 Task 群聊中编排、指导并交付 OUTPUT。
- Project 容纳多个 Task，并保存跨 Task 需要长期保留的内容。

LOOP、Thread、竞争执行、重试与 checkpoint 属于实现层，不要求使用者围绕这些概念组织工作。

## 3. 目标与非目标

### 3.1 目标

1. 让长期工作拥有稳定归属，不再散落为无关联 Task。
2. 让新 Task 能复用 Project 当前有效的背景与共享资料。
3. 让已采纳 OUTPUT 自动形成可追溯的 Project 交付历史。
4. 让人与 Agent 在 Task 群聊内完成编排、指导、交付和验收。
5. 保持无 Project 的直接 Task 仍然可用，不增加简单任务的操作成本。

### 3.2 非目标

- 不设计游戏画面、人物动作或空间布局。
- 不把 Project 实现为共享的可变 sandbox 工作目录。
- 不把全部历史对话无选择地注入后续 Task。
- 不强迫每个 Task 都属于 Project。
- 不在 MVP 中实现跨 Project 共享记忆或自动复制交付。
- 不在本 PRD 中规定数据库表、API 路径或 DeerFlow 改造方案。

## 4. 核心对象与关系

| 对象 | 用户语义 | 关键关系 |
|---|---|---|
| Project | 长期协作空间 | 一个 Project 包含多个 Task、共享资料和交付历史 |
| Task | 一次工作／一场群聊 | 可属于一个 Project，也可以独立存在 |
| Agent | 协作者 | 可参与多个 Project 与多个 Task |
| 人 | 发起人、指导者、执行者或验收者 | 与 Agent 在同一 Task 群聊中协作 |
| OUTPUT | Task 中提交的工作结果 | 被采纳后进入 Project 交付历史 |
| 共享资料 | 后续 Task 可反复引用的长期内容 | 由人明确加入或设为共享 |

Project 参与者默认由其中 Task 的实际参与者形成，MVP 不另建复杂的 Agent 入会／退会流程。

## 5. MVP 功能需求

### 5.1 Project 生命周期

- 人可以创建、重命名、编辑说明、归档和恢复 Project。
- 创建时只需名称；说明与资料可以随后补充。
- 归档后保留全部 Task、资料与交付的只读访问，不再创建新 Task。
- 无 Project 的 Task 继续在现有 Task Pool 中工作。

### 5.2 Project 归属

- 创建 Task 时可选择所属 Project，默认不所属任何 Project。
- Project 中的 Task 列表至少展示：当前状态、参与者、最近进展与待处理事项。
- 一个 Task 最多属于一个 Project。
- MVP 创建后不允许直接改换 Project，避免上下文与交付归属突然变化；跨 Project 移动作为后续能力单独评审。

### 5.3 长期上下文

Project 长期上下文由三类内容组成：

1. **Project 说明**：人维护的稳定背景、目标、约束与协作约定。
2. **共享资料**：人上传或明确设为共享的参考文档、重要结论。
3. **交付索引**：已采纳 OUTPUT 的摘要与来源，供后续 Task 发现和引用。

后续 Task 应获得当前有效的 Project 说明、共享资料索引与交付索引，而不是全部历史消息。具体注入裁剪属于技术设计，但产品必须保证用户能够知道“这个 Task 继承了什么”。

### 5.4 Task 群聊与 OUTPUT

- Task 对用户呈现为一场群聊。
- 人、主 Agent 和 subagent 的编排、指导、进展与结果在该群聊中表达。
- OUTPUT 直接在 Task 群聊中交付；验收、退回和再次交付也留在同一工作上下文中。
- 后台多个执行分支不得要求用户理解其技术分层才能完成任务。

### 5.5 Project 交付积累

- 已采纳 OUTPUT 自动进入所属 Project 的交付历史，无需再次手动归档。
- 未采纳、被退回或已作废的结果仍保留在 Task 内，但不进入 Project 的正式交付历史。
- 每条交付记录可追溯到原 Task、提交者与采纳时间。
- 人可以把重要交付或资料设为共享，使其成为后续 Task 可引用的长期上下文。
- “进入交付历史”与“进入共享上下文”是两个不同动作：前者自动，后者由人明确选择，避免上下文无限膨胀。

### 5.6 资料管理

- Project 支持上传、预览、下载和移除共享资料。
- Task 中的文件可以保存为 Project 资料。
- Project 资料可以被附加到新的 Task 中使用。
- 可以集中浏览 Project 下各 Task 的文件，但“浏览全部文件”不等于“全部自动共享”。
- 删除采用可恢复机制；归档与删除的权限和审计要求一致。

### 5.7 权限与审计

- 只有具备 Project 权限的人可以修改说明、共享资料、归档或删除。
- Agent 只能访问其当前 Task 所属 Project 已提供的上下文与资料，不能遍历其他 Project。
- 说明、共享状态、交付采纳和删除均保留操作者与时间。
- 不因为加入 Project 而扩大 Agent 的工具权限或数据访问范围。

## 6. 关键用户流程

### 6.1 发起长期工作

1. 人创建 Project，写入背景与目标。
2. 上传需要反复引用的资料。
3. 在 Project 中创建 Task，并指定参与者。
4. 参与者在 Task 群聊中协作并提交 OUTPUT。
5. OUTPUT 被采纳后自动进入 Project 交付历史。

### 6.2 延续既有工作

1. 人在同一 Project 中发起新 Task。
2. 新 Task 获得当前 Project 说明、共享资料与交付索引。
3. 参与者引用此前的交付继续工作，无需重新粘贴背景。

### 6.3 独立 Task

1. 人直接从 Task Pool 创建 Task，不选择 Project。
2. Task 按现有方式运行与交付。
3. MVP 不承诺事后并入 Project；如确需该能力，进入后续评审。

## 7. 信息架构

Project 页面至少包含四个视图：

1. **概览**：目标、当前进展、待处理事项与参与者。
2. **Tasks**：项目内 Task 群聊入口与状态。
3. **交付**：已采纳 OUTPUT 的时间线与来源。
4. **资料**：共享资料及上下文使用状态。

Project 说明与共享范围必须清晰可见，避免用户误以为所有历史内容都会自动进入后续 Task。

## 8. MVP 验收标准

1. 可以创建 Project，并在其中连续创建多个 Task。
2. 新 Task 能读取 Project 当前说明与被共享的资料。
3. 修改 Project 说明后，后续新工作使用新说明，既有历史记录不被改写。
4. Task 的已采纳 OUTPUT 自动出现在 Project 交付历史中。
5. 未采纳 OUTPUT 不出现在正式交付历史中。
6. 人可以将重要交付设为共享，并在后续 Task 中引用。
7. 无 Project 的现有 Task 流程不发生变化。
8. Agent 无法访问无关 Project 的说明、资料或交付。
9. 用户不需要理解 LOOP 或 Thread，即可完成 Project 下的协作与交付。

## 9. 后续候选能力

- 既有独立 Task 并入 Project。
- Task 跨 Project 移动或复制交付。
- Project 模板、角色与固定参与者。
- Project 级长期记忆、全文搜索与自动知识提炼。
- 跨 Project 复用资料与交付。
- 更细粒度的上下文预算、引用范围和有效期控制。

## 10. 与游戏 PRD 的接口

业务侧只需向表现层提供稳定语义：Project 状态、Project 内 Task、参与者、最近 OUTPUT、待处理事项与共享资料数量。游戏不得自行发明另一种 Project 生命周期、交付归属或上下文规则。
