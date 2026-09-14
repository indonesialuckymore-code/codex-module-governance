---
name: task-window-dispatch-controller
description: 在 Boss 对单项任务或中央启动图作出范围化批准且 C05 占用验收通过后，按并行波次准备和批量派发 Codex 任务窗口及最多 3 个一级子 Agent；任务窗口可在原范围内主动补派独立子 Agent，但必须经过结构化回传、父窗口读证和质量闸门，不能牺牲最终质量。绑定 Codex 保存项目；模型默认 Terra 可修改，续办保留当前模型；任务权限默认原生完全访问，工作区受限为可选高隔离模式，只有运行时返回正确项目归属、真实任务 ID、模型与实际权限回执后才登记 C03。Use when 中央要派发一个或一批任务、放行后续依赖波次、复用旧窗口、创建或补派一级子 Agent、修复新任务掉进“最近”而非项目分组、登记真实运行结果或汇总子 Agent 回传。
---

# 任务窗口与一级子 Agent 调度

## 业务规则

- 绿：全部对象都是非独占只读，可并行。
- 黄：包含已由 C05 独占隔离的写入，可派发但严格限制在任务包范围内。
- 红：重叠写入、未知跨模块状态、未满足依赖、失联冻结或总账硬停，禁止派发。

## 两段式派发

有人工纠偏时先读[纠偏候选协议](../../references/task-corrections.md)。仅在生效核验释放恢复资格后继续；旧派发/补派单不能复用，当前单中的 `directionContext`、`directionInstructionRef` 及原生动作 `requiredDirectionContext`/`requiredDirectionInstructionRef` 必须传入执行上下文，与原批准任务包一起阅读。旧做法只在被当前指令替代的部分失效，不能因此扩大范围或重做全部成果。即使中央返回已准备任务，原生动作执行前仍须读回当前执行保护与版本；验证边界见[候选版状态](../../references/release-status.md)。

1. `prepare` 验证 C04、C05、C03、Boss 批准和恢复状态，只生成不可覆盖派发单。
2. 派发单固定任务身份：总账/任务包使用 `任务ID｜业务名称`，Codex 窗口使用 `任务ID｜业务名称｜G代际`。
3. Codex 运行时按派发单完整标题及模型选择创建新任务、复用任务或委派一级子 Agent。派发前读取[模型选择与真实运行记录](../../references/runtime-model-policy.md)：新任务默认 Terra，明确指定可改；续办未要求换模型时省略原生消息的 `model` 参数并读回实际选择。
4. 只有所有要求的运行时对象都成功、标题/任务 ID/代际完全匹配、返回真实 ID，并同时回传模型控制和实际权限记录后，`confirm` 才登记窗口和子 Agent，并把任务从 `READY` 推进为 `IN_PROGRESS`。默认接受 `FULL_ACCESS/full-access`；Boss 主动选择高隔离时接受 Codex 原生 `WORKTREE_SCOPED/:workspace`。不得创建或要求自定义权限 Profile。
5. 缺少模型/权限读回、权限 class/profile 不匹配或部分创建，都不得更新总账；非 Terra 本身不是失败。受限模式若写根包含中央私有目录仍须拒绝；完全访问明确记录 `governanceDataRootAccess=NOT_RESTRICTED`，不把系统能力冒充业务授权。
6. 平台不能自动创建任务时，`export-fallback` 提供手工建窗包；采用所选模型并保留模型、权限证据，以 `MANUAL_UI_MODEL_SELECTION_EVIDENCE` 和 `MANUAL_UI_PERMISSION_EVIDENCE` 完成确认。旧 Terra 人工证据只用于真实 Terra 历史兼容。

## 权限门禁

1. 模型读回记录“实际由什么模型施工”，权限读回记录“平台允许碰到哪里”；业务范围另行核验。
2. 任务窗口默认 `FULL_ACCESS`；`WORKTREE_SCOPED` 为 Boss 可选的高隔离模式。缺证据、class/profile 不匹配或只有提示词承诺仍禁止进入 `IN_PROGRESS`。
3. 权限回执必须同时包含 `permissionClass`、`profile`、`method`、`evidenceRef`、`writableRoots` 和 `governanceDataRootAccess`。完全访问使用空 `writableRoots` 与 `NOT_RESTRICTED`；受限模式使用非空绝对写根与 `DENIED`。
4. 一级子 Agent 默认继承父任务窗口的实际权限；回执必须引用父窗口运行身份，且 class、profile、写根和治理目录访问状态完全一致。若运行时能提供独立读回，也可用独立权限回执。
5. 旧窗口即使有模型凭据，只要没有完整权限边界凭据，就不得承接第二项任务；允许完成当前任务，但下一项应新开合格窗口。
6. Codex 权限 Profile 仍属运行时能力；C10 只验收真实读回/界面证据，不把配置文件文本当成已生效事实。

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
- C05 `bossReview.reference` 必须与 C10 `bossApprovalRef` 完全相同。C04 任务包仍是不可单独派发的草案；实际施工合同由 C10 派发单内嵌给任务窗口，不要另找不存在的“最终任务包”。
- 同一波所有任务先分别通过 C04、C05 和 `prepare`，再批量调用运行时；某个任务失败不把其他已真实成功且无冲突的任务伪装成失败，但失败项不得写入 `IN_PROGRESS`。
- 后续波次复用原启动图批准，但仍要验证前置任务已由 C06 + Boss 确认为 `DONE`，并重新运行 C05。
- 已采用执行安排改版时，使用 C03 当前版本及[启动图协议第 8 节](../../references/central-construction/project-execution-map.md)。`scopeDigest` 仍指向原批准依据，不能改成新安排摘要；C10 复核当前等待条件，不能用旧入口绕过。仅执行排序改版不要求把所有原派发重新做一遍。
- C06 返回 `successorDispatch.required=true` 时，中央直接准备并真实派发所有新近合格任务；范围未变化时不再向 Boss 请求“下一项”批准。

## 项目归属门禁

1. 派发前先读取 Codex 保存项目清单，用保存路径匹配唯一项目 ID。
2. 派发前必须读取唯一[项目归属证据协议](../../references/project-association.md)。新 Git 任务默认直接使用 `project + worktree`，非 Git 使用 `project + local`。不为填充界面项目编号强制往返交接。
3. 已准备的历史 `LOCAL_BOOTSTRAP_TO_WORKTREE` 派发单仍按原单确认，不改写旧单；新单的 `initialTarget` 和 `finalTarget` 相同。
4. 最终回读优先直接项目编号。字段缺失时仅在原生创建关联、实际任务读回及保存项目/Git 关系完整交叉核验后继续。保留原始空值与独立 `verifiedProjectId`，不能假称平台已直接返回编号或侧栏分组已修好。
5. 证据必须保存工具原文、摘要和逐字段提取映射；程序验证实际目录关系，而不是相信同名目录。直接编号矛盾、证据缺失或关系不符仍拒绝，不循环创建更多窗口。
6. 模型、权限、完整标题、真实任务 ID 与任务占用仍分别验收；项目核验通过不能替代这些条件。

“同一项目”指绑定同一个 Codex 保存项目；侧栏分组是单独的界面投影。Git 并行任务使用不同 worktree 物理目录，这是本地文件隔离，不是另建项目。

## 模型证据与身份分离

新建默认、显式选择、续办保留、手工降级及历史证据兼容统一使用[模型选择与真实运行记录](../../references/runtime-model-policy.md)。C03 的 `model`、`runtimeModel` 和模型回执应一致反映实际选择；未知模型不是已确认状态。模型可用性由平台判断，C06 独立验收、Boss 最终批准及对象占用不因切换模型改变。

## 原生任务动作

- 新开任务用 `codex_app__create_thread`；复用用 `codex_app__send_message_to_thread`；按派发单选择或保留模型并回读任务，不把默认值强制覆盖到续办消息。
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

父窗口不得把多个回复直接拼接成结论。所有子 Agent 回传齐全后，按[按成果和实际影响验收](../../references/adaptive-acceptance.md)逐项覆盖同一任务合同：适用项读回实际证据，不适用项引用预定理由和核验依据，不能伪造测试。读回关键证据、处理子 Agent 间冲突、确认未越界或出现未知写入后，提交 `record-parent-quality-review`。只有该质量闸门通过，父窗口才能申请 C06；C06 和 Boss 最终批准仍是整项 `DONE` 的唯一通道。

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
- 仅有标准 worktree 路径不等于项目归属成功；应用 `projectId` 为空时必须按唯一证据协议核验，不能凭目录同名放行。
- 可复制任务包也不等于运行时已成功，不得伪造任务或 Agent ID。
- 任务卡显示的模型不能单独证明完成派发；须取得真实任务、项目、模型和权限回执，但不因用户选择 Sol 或其他模型拒绝开工。
- 任务窗口不得绕过任务包、对象占用和硬停条件。
- 失联后保留占用并转 C08，不自动重派。
