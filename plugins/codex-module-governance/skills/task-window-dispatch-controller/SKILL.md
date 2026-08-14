---
name: task-window-dispatch-controller
description: 在 Boss 对单项任务或中央启动图作出范围化批准且 C05 占用验收通过后，按并行波次准备和批量派发 Codex 任务窗口及最多 3 个一级子 Agent；任务窗口可在原范围内主动补派独立子 Agent，但必须经过结构化回传、父窗口读证和质量闸门，不能牺牲最终质量。强制绑定 Codex 保存项目、gpt-5.6-terra 和工作区受限权限，只有运行时返回正确项目归属、真实任务 ID、模型与权限控制回执后才登记 C03。Use when 中央要派发一个或一批任务、放行后续依赖波次、复用旧窗口、创建或补派一级子 Agent、修复新任务掉进“最近”而非项目分组、登记真实运行结果或汇总子 Agent 回传。
---

# 任务窗口与一级子 Agent 调度

## 业务规则

- 绿：全部对象都是非独占只读，可并行。
- 黄：包含已由 C05 独占隔离的写入，可派发但严格限制在任务包范围内。
- 红：重叠写入、未知跨模块状态、未满足依赖、失联冻结或总账硬停，禁止派发。

## 两段式派发

1. `prepare` 验证 C04、C05、C03、Boss 批准和恢复状态，只生成不可覆盖派发单。
2. 派发单固定任务身份：总账/任务包使用 `任务ID｜业务名称`，Codex 窗口使用 `任务ID｜业务名称｜G代际`。
3. Codex 运行时按派发单的完整标题创建新任务、发送到旧任务或创建一级子 Agent。新任务必须调用原生 `create_thread` 且明确传入 `model: "gpt-5.6-terra"`；复用旧窗口必须调用原生 `send_message_to_thread` 且明确传入同一 `model`；一级子 Agent 必须用原生 `spawn_agent` 且明确传入同一 `model`。只在提示词里写“请使用 Terra”不算派发。
4. 只有所有要求的运行时对象都成功、标题/任务 ID/代际完全匹配、返回真实 ID，并同时回传模型控制和权限控制记录后，`confirm` 才登记窗口和子 Agent，并把任务从 `READY` 推进为 `IN_PROGRESS`。权限必须为 `WORKTREE_SCOPED`，允许 `:workspace`、`qianyi-task-terra` 或兼容旧运行时的 `workspace-write`；完整访问一律拒绝。
5. Sol、Luna、缺少模型/权限控制记录、方法不匹配、`danger-full-access` 或部分创建，都不得更新总账；报告对应的 `C10_* / NEEDS_REVIEW`。已产生的孤立对象应交 C08 冻结或收回，不能冒充合格窗口。
6. 如果当前 Codex 表面不能自动创建任务，`export-fallback` 只生成可复制任务包交 Boss 手工建窗；Boss 必须先在模型菜单选择 **5.6 Terra**，把权限设为工作区受限并关闭完整访问，保留两项证据，随后分别以 `MANUAL_UI_TERRA_SELECTION_EVIDENCE` 和 `MANUAL_UI_PERMISSION_EVIDENCE` 完成 C10 确认。没有该手工包与证据，手工窗口不得登记。

## 权限门禁

1. Terra 管“由什么模型施工”，权限门禁管“它可以碰到哪里”；两者缺一不可。
2. 任务窗口必须是 `WORKTREE_SCOPED`。`danger-full-access`、`full-access`、缺证据或只有提示词承诺均禁止进入 `IN_PROGRESS`。
3. 一级子 Agent 默认继承父任务窗口的受限权限；回执必须引用父窗口运行身份，且 profile 完全一致。若运行时能提供独立读回，也可用独立权限回执。
4. 旧窗口即使有 Terra 凭据，只要没有权限凭据，就不得承接第二项任务；允许完成当前任务，但下一项应新开合格窗口。
5. Codex 权限 Profile 仍属运行时能力；C10 只验收真实读回/界面证据，不把配置文件文本当成已生效事实。

## 窗口两任务上限

- 窗口是稳定运行身份，当前任务是窗口内的一次承接记录；C03 保存 `assignmentHistory`，避免第 2 项任务被误判成旧任务身份。
- 新窗口登记第 1 次承接。只有第 1 项已由 C06 + Boss 记为 `DONE`、窗口为 `AVAILABLE_FOR_REUSE` 且没有当前任务时，C10 才接受第 2 次承接。
- C05 对第 2 次承接使用 `RESERVED_FOR_REUSE + reservedForTaskId + reuseReservationId` 原子占住唯一剩余名额；C10 准备和确认都必须核对同一预留，其他任务不得抢占。
- 运行时派发失败时保留该预留，允许重试同一不可覆盖派发单。若中央决定放弃，不得静默清除；以预留任务 ID 和窗口 ID 转 C08，由 Boss 控制恢复或取消。
- 第 2 次派发必须把旧任务标题改成新任务的完整 `任务ID｜业务名称｜G代际` 并回读确认；只发送消息但标题仍旧，按身份不符停止。
- 第 2 项完成后窗口为 `RETIRED`。`assignmentCount >= 2`、`currentTaskId` 非空或状态不是 `AVAILABLE_FOR_REUSE` 时，一律拒绝复用。
- 复用窗口内不得并发两项任务；旧任务的一级子 Agent 必须在 `DONE` 时结束，才能为第 2 项重新计算最多 3 个一级子 Agent。

## 中央启动图批量授权

- C10 接受 `SINGLE_TASK` 或 `EXECUTION_MAP` 两种批准范围。
- `EXECUTION_MAP` 必须包含启动图 ID、SHA-256、当前波次和全部获批任务 ID；当前任务不在清单中就拒绝。
- 同一波所有任务先分别通过 C04、C05 和 `prepare`，再批量调用运行时；某个任务失败不把其他已真实成功且无冲突的任务伪装成失败，但失败项不得写入 `IN_PROGRESS`。
- 后续波次复用原启动图批准，但仍要验证前置任务已由 C06 + Boss 确认为 `DONE`，并重新运行 C05。
- C06 返回 `successorDispatch.required=true` 时，中央直接准备并真实派发所有新近合格任务；范围未变化时不再向 Boss 请求“下一项”批准。

## 项目归属门禁

1. 派发前先读取 Codex 保存项目清单，用保存路径匹配唯一项目 ID。
2. Git 项目默认用 `project + worktree`；非 Git 项目用 `project + local`。禁止 `projectless` 和中央自造任务目录。
3. 派发单必须记录 `runtimeTarget`，运行时严格照此创建，不得另传自定义目录。
4. 创建后回读真实 `projectId`、工作目录和运行环境：Git 必须是 Codex 标准 worktree 且目录名与项目一致；本地模式必须等于保存项目路径。
5. Git worktree 目录正确但 `projectId` 为空时，不要重新创建平行任务：对同一任务执行一次原生交接到保存项目根目录，回读正确项目 ID 后再交接回原 worktree，最后再次回读项目 ID 和目录。
6. 往返交接后的最终任务必须同时满足正确 `projectId` 和标准 worktree 路径；私有确认中保留初始、项目本地和最终任务引用，C03 只登记最终任务引用及修复方式。
7. 项目或目录仍不匹配时，不写 C03、不推进任务，报告 `PROJECT_ASSOCIATION_MISMATCH / NEEDS_REVIEW`。

“同一项目”指 Codex 左栏归入同一个项目。Git 并行任务仍使用不同 worktree 物理目录，这是本地文件隔离，不是跑到另一个项目。

## Terra 模型门禁

1. `windowAction.model` 与每个 `subAgents[].model` 固定为 `gpt-5.6-terra`；没有“按用户当前默认模型”的回退。
2. 创建、复用和补派均必须走对应的原生模型参数，不能靠任务窗口在聊天中自行切换。
3. C10 只会把已记录 `model`、`runtimeModel` 和 `modelEnforcement` 的 Terra 窗口写入工程总账。旧版或手工直登、但没有这三项证据的窗口为 `UNVERIFIED`，不得作为第二项任务的复用窗口。
4. 新窗口如未成功指定 Terra，中央保持任务 `READY`；不得以“先用 Sol 做、之后再换”为理由开始施工。复用窗口如没有 Terra 证据，C05 改选新窗口或等待明确裁定。
5. 模型不是业务验收质量的替代品。Terra 只保证派工的一致模型；C06 独立验收、Boss 最终裁定、对象占用和真实读回规则不变。

## 原生任务动作

- 新开任务用 `codex_app__create_thread`；复用用 `codex_app__send_message_to_thread`；两者都必须传 Terra 并回读任务。
- 第二次承接后用 `codex_app__set_thread_title` 改成新的完整标题并回读；标题只是界面投影，不是任务状态。
- 中央用 `codex_app__wait_threads` 按最多 8 个一组等待，不逐个轮询；完成后仍须读取 C08/C14 票据。
- 任务窗口不置顶；当前中央和当前裁定可置顶。只有 C06 + Boss 已 `DONE` 且窗口已 `RETIRED` 才允许归档。

## 子 Agent：主动提速，但父窗口独自对质量负责

任务窗口在三次固定检查点主动判断，而不是等 Boss 想起再派：完整阅读任务包后的`开始前`、施工中发现新且可隔离材料的`发现时`、提交验收前的`验收前`。

可以派 0–3 个一级 Agent，按真实独立工作量取最小数量：

- `LARGE_MATERIALS`：大量只读材料可拆开，派资料/证据核对。
- `INDEPENDENT_SCOPE`：已批准范围内有不重叠对象、目录或步骤，派隔离施工或资料整理。
- `INDEPENDENT_VERIFICATION`：需要独立复核正反例、幂等、回滚或读回，派验证者。
- `CROSS_CHECK`：发现事实不一致，派只读反证核对。

小任务、必须串行的步骤、共享写入对象、范围不明、外部权限不明或会改变唯一写入方时，派 `0`；不得以“多开 Agent”代替澄清或占用检查。初始派发可带 Agent；运行中新增只可走 `prepare-append → confirm-append`，并同时满足：仍是同一已批准任务、`withinApprovedScope=true`、`sharedWriteRisk=false`、活跃任务窗口、总数不超过 3。它不需要重复向 Boss 索要同一范围批准，但不满足任一条件就停止并交中央/Boss。

每个子 Agent 默认 `gpt-5.6-terra`、只能一级、只执行父窗口分配的独立范围；其模式只能是 `READ_ONLY`、`ISOLATED_WORK` 或 `INDEPENDENT_VALIDATION`。不得下派孙 Agent、改 C03、改任务范围、绕过 C05 或宣布 `DONE`。

子 Agent 回传只能是 `NEEDS_REVIEW`、`PARTIAL` 或 `BLOCKED`，并强制包含：

```text
【负责范围】
【已确认事实】
【完成内容】
【证据引用】
【未确认事项】
【与任务包偏差】
【风险和冲突】
【建议父窗口动作】
【状态】NEEDS_REVIEW / PARTIAL / BLOCKED
```

父窗口不得把多个回复直接拼接成结论。所有子 Agent 回传齐全后，父窗口必须：逐项覆盖任务包的正例、反例、幂等、回滚、日志/历史和读回验收；读回关键证据；逐项处理子 Agent 间冲突；确认没有越出原范围或出现未知写入；再提交 `record-parent-quality-review`。只有该质量闸门通过，父窗口才能申请 C06；C06 和 Boss 最终批准仍是整项 `DONE` 的唯一通道。

## 向中央回传

- 任务窗口始终把事件目标写成 `CURRENT_CENTRAL`，不得保存某一代中央聊天 ID 作为永久收件人。
- 进度、阻塞、裁定和补派仍先写 `IN_PROGRESS`、`BLOCKED_FOR_DECISION`、`DECISION_APPLIED` 或 `SUB_AGENT_APPEND_REQUEST` 私有事件；补派事件必须引用私有 `C10_SUB_AGENT_APPEND_REQUEST` 文件，中央读回后才可执行 `prepare-append`。
- 施工回传不得只写 `READY_FOR_VALIDATION` 或发送“已回传”文字。先封存 `C06_TASK_WINDOW_HANDBACK`，再调用 C08 `submit-return`。只有返回同时含 `returnTicketId`、`eventId`、`handbackDigest`、`deliveryReceiptId` 和 `TASK_EVENT_QUEUED` 的回执，才能说“已进入持久中央收件箱”。
- 接着必须调用 C14：`enqueue-task-to-central → prepare-delivery → Codex 原生 send_message_to_thread → record-delivery`。只有当前中央以 C14 `MESSAGE_ACKNOWLEDGED` 确认，才能说“中央已收到”；C14 消息正文只能发票据指针，任务窗口不得把施工原文粘给中央。
- 缺少 C08 回执时只能报告 `LOCAL_REPLY_ONLY` 或 `OUTBOX_PENDING`；缺 C14 原生投递回执时是 `QUEUED`，缺中央确认时是 `DELIVERED`。可以重试 C14，不得用聊天文字补发替代。

## 永久边界

- C10 调度 Codex 任务，不直接修改业务系统。
- 派发单不等于运行时已成功。
- 任务卡出现在“最近”或自定义目录，不等于已正确归入项目。
- 仅有标准 worktree 路径也不等于项目归属成功；应用回读的 `projectId` 为空仍须修复或硬停。
- 可复制任务包也不等于运行时已成功，不得伪造任务或 Agent ID。
- 任务卡底部当前显示 Sol、或没有 Terra 模型控制记录，均不等于已按任务包派发；不得开始施工或向中央发送“已开始”。
- 任务窗口不得绕过任务包、对象占用和硬停条件。
- 失联后保留占用并转 C08，不自动重派。
