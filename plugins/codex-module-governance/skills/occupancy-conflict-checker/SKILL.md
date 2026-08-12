---
name: occupancy-conflict-checker
description: 检查经 Boss 审阅的 Codex 待审任务包是否与当前任务窗口、子 Agent 或其他任务争用同一文件、表、字段、视图、workflow、服务、配置、生产开关、事实源、账号、外部入口或隔离范围。用于“检查任务冲突”“判断能否并行”“决定开新窗口还是沿用旧窗口”“预留施工对象”等请求；只复用 C03 唯一占用总账，不派发窗口、不创建 Agent、不创建 TEST、不执行业务写入。
---

# 占用与冲突检查器

读取 C04 待审任务包、Boss 审阅引用和 C03 当前占用，生成可追溯的 C05 结论。把“可进入派发审批”与“已经派发”严格分开。

## 检查顺序

1. 校验 C04 任务包和不可覆盖回执。
2. 校验 C03 总账和回执链，确认任务仍为 `PLANNED`。
3. 确认 Boss 已审阅任务包及本次对象范围，依赖已经明确满足。
4. 检查窗口建议：当前任务已有唯一活动窗口时沿用本次承接；已完成旧窗口只有 `assignmentCount=1`、`currentTaskId=null`、状态为 `AVAILABLE_FOR_REUSE` 时才能作为第 2 次且最后一次承接；达到 2 次或已退役时禁止复用。
5. 比较每个对象的 `objectKey` 和 `conflictKey`。非排他只读可以并行；任一写入或排他资源重叠时，后启动任务硬停。
6. 先预演。只有显式批准应用后，才把整组占用一次性写入 C03，并把任务推进到 `READY`；任何一项冲突时不得部分占用。

## 资源范围

覆盖文件、表、字段、视图、workflow、服务、配置、生产开关、唯一事实源、账号、外部入口、TEST 隔离范围、日志范围和回滚范围。用不透明标识，不写绝对路径、链接、客户资料、财务数据、Cookie 或密钥。

## 结论含义

- `ELIGIBLE_FOR_DISPATCH_APPROVAL`：占用已安全预留，任务进入 `READY`；尚未创建窗口或 Agent。
- `WAITING_*`：Boss 审阅、依赖或可复用窗口条件不足；不写总账。
- `CONFLICT_HARD_STOP`：现有施工方与候选任务争用资源；保留现有占用，候选任务进入 `BLOCKED`，不写入候选占用。

## 永久边界

- 只让 `codex-module-central` 写 C03；Fable、Claude 中央、任务窗口和子 Agent 不能直接占用。
- 不建立第二套占用账本；C05 决定文件只是 C03 写入的不可覆盖凭证。
- 不把读回消息、任务窗口声明或跨模块情报当作写入授权。
- `dispatchExecuted`、`taskWindowCreated`、`subAgentCreated`、`testCreationAllowed` 和 `businessWriteAllowed` 始终为 `false`。
- C10 必须复用本检查器，不得另建子 Agent 锁。
- 多个旧窗口都可复用时返回中央选择，不要求 Boss 裁定；中央无法证明上下文适配时应改为新开。
