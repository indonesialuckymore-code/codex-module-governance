# 中央、裁定与任务窗口连续性协议

## 目标

聊天窗口可以更换，但项目角色、任务身份和回传通道不能跟着丢失。私有数据目录中的角色路由、交接包、C03 总账和任务事件箱是事实源；聊天标题和压缩摘要只是界面信息。

## 两个逻辑角色

- `CURRENT_CENTRAL`：唯一当前中央。可派工、验收回传、确认任务事件并通过 C03 更新状态。
- `CURRENT_ADJUDICATION`：唯一当前裁定窗口。只分析 Boss 不能直接裁定的问题；不能派工、写总账或验收。

每个角色带代际 `G1/G2/...`。同一角色只能有一个 `ACTIVE` 窗口，旧代际只读转发。

## 项目首次启动

首次调用“中央工作台”的当前原生任务就是 `CURRENT_CENTRAL G1`。`calling-thread-ref` 与 `central-thread-ref` 必须完全相同；初始化不得另建一个中央窗口。

```bash
python3 plugins/codex-module-governance/scripts/role_continuity_controller.py \
  --data-root <私有目录> --project-id <项目ID> \
  --writer-id codex-module-central initialize \
  --calling-thread-ref <当前调用任务ID> \
  --central-thread-ref <当前中央任务ID> \
  --runtime-project-id <Codex保存项目ID> \
  --execution-map-ref <中央启动图引用>
```

路由激活后，任何写 C03 的命令都必须携带当前原生中央任务 ID：

```bash
python3 plugins/codex-module-governance/scripts/ledger_manager.py \
  --data-root <私有目录> --project-id <项目ID> \
  --writer-id codex-module-central \
  --caller-thread-ref <CURRENT_CENTRAL任务ID> <具体变更命令>
```

缺少 `caller-thread-ref`、旧中央 ID 或任务窗口 ID 都必须硬停。这不依赖聊天标题判断，而是与 C08 当前角色路由机械对账。

## 第一次需要中央裁定

1. 以 `BOOTSTRAP_ADJUDICATION_FROM_CENTRAL` 生成交接包。
2. 从当前中央任务创建一个新任务，使它取得当时中央上下文。
3. 新任务读回交接包摘要无误后，登记为 `CURRENT_ADJUDICATION G1`。
4. 后续裁定直接发送当前裁定窗口，只追加该裁定请求；不再复制中央。

裁定完成后，原任务窗口收到完整 Boss 指令；中央只读 `central-impact-receipt.json`。该摘要不能包含顾问全文、Boss 理由或讨论过程。

## 计划换代

1. 为 `CURRENT_CENTRAL` 或 `CURRENT_ADJUDICATION` 生成 `REPLACE_ACTIVE_ROLE` 交接包。
2. 新任务读取交接包、当前总账、未处理事件和未完裁定引用。
3. 校验交接包摘要；普通总账/事件变化按下方增量更新，不覆盖旧包。源角色已换代时拒绝继续原交接。
4. 激活新代际后，旧任务只读转发。任何写入都必须解析当前逻辑角色。

复制聊天只用于让新角色快速理解，不是续接证据。即使模型已经多次压缩上下文，新任务也以交接包和 C03 为准。

## 任务窗口回传

任务窗口标题固定为 `任务ID｜业务名称｜G代际`。普通进度事件与施工回传分开：

1. 普通进度、阻塞、裁定和子 Agent 补派，写入目标为 `CURRENT_CENTRAL` 的事件文件，再发送聊天提醒。
2. 施工回传先封存 C06 回传包，再写入 C08 自动回传收件箱；只有取得 `returnTicketId`、`eventId`、回传摘要哈希和 `TASK_EVENT_QUEUED` 投递回执才成立。
3. 当前中央对成立票据只读摘要，自动写入 C03 完成信号并将其预留给唯一隔离验收槽；中央不读取施工原文。
4. 独立验收窗口按票据读回证据，向中央仅回传验收 ID、结论和回执；Boss 只在例外或最终 `DONE` 批准时介入。

`ACKNOWLEDGED` 仅说明中央取得了队列索引；不是验收通过，也不是 `DONE`。`LOCAL_REPLY_ONLY` 或 `OUTBOX_PENDING` 的聊天内容一律不能被当作回传。

旧中央、裁定窗口和标题不匹配的任务都不能确认事件。中央换代期间产生的事件保留在私有事件箱，由新中央继续读取。

交接包生成后、继任者激活前如果新增或确认了任务事件，旧包不能直接激活；先生成关联更新包并补读，不能让新中央先激活再靠聊天补差额。

## 候选增量交接与重试

当前中央使用 `refresh-handover --handover-id BASE --revision-id NEXT --current-thread-ref CURRENT`。命令在 C08 与 C03 锁内读取现状，复用原角色、交接理由、上下文引用及目标代际，仅产生不可覆盖的新包和回执，不创建原生任务、不切换角色、不派发任务。原包保留。

- 无变化返回 HANDOVER_ALREADY_CURRENT 和原包编号，不创建空更新。相同更新编号重试零写入；snapshotCurrent=false 表示它后来又过时，需以更新包为基线另取新编号，不覆盖同名包。
- 新包 delta 绑定基线编号和摘要，列出变化的账本分区、新增/移出待处理事件；移出只是索引变化，必须核对确认凭据，不能推断验收完成。旧包没有分区摘要时保守列出所有分区，不假装知道精确差异。
- ledgerSnapshot.taskContinuity 携带成果关联、纠偏版本与核验、外部等待和反馈记录；该字段与 sectionDigests 是阅读投影，不另立状态源。其他详细材料仍从原 C03/C08/C14 读取。
- 新中央实际补读变化及其影响后，使用新包编号和摘要提交激活；激活仍要求最新总账、路由和事件快照一致。此过程不得自动解除等待、纠偏、恢复或验收门槛。
- 激活必须保持原保存项目，不能借换窗迁到另一项目；继任任务不得同时占用另一活动角色。正常选择其他模型仍允许。原生项目/模型/权限读回必须实际获取，JSON 声明本身不证明原生环境。
- 激活检查与路由切换持有 C08 和 C03 锁，防止核验后被并发账本写入穿过。路由是唯一生效点；之后若激活结果文件缺失，可从已验证路由回执及 activationBinding 精确恢复，不再增加代际、不覆盖冲突材料。尚未提交路由时旧中央仍可用；路由已提交但结果缺失时，新中央已生效，不能误说旧中央仍有写权。

候选已加入下方原生动作握手、C14 摘要、最多 8 次关联更新、路由提交前后恢复与历史结果补全。超过更新上限时不丢弃消息、不创建第二个继任者；保留当前中央和原继任窗口，报告无法取得稳定快照，待状态稳定后在明确核对原关联的情况下恢复。源码回执测试不证明真实平台已经完成建窗、回读或打开。

## 一句话交接的原生执行顺序

Boss 明确要求“交接中央到新窗口”即为本次同项目换窗的操作授权，不再逐步询问是否制包、建窗或打开。身份/范围改变另处理。以下全部由中央执行，Boss 不手工填 JSON。

1. 先依照[模型选择协议](../runtime-model-policy.md)实际读回源窗口模型，再制包并运行 `prepare-successor --handover-id ID --current-thread-ref CURRENT --authorization-ref AUTH`；只有 Boss 为继任者另选模型时加 `--model`。只有首次 `CREATE_SUCCESSOR_ONCE` 才调用返回的原生 fork_thread；保留工具真实返回。fork 只复制已完成历史，不保证继承模型。取得真实继任 ID 后，按 `successorFirstTurn.arguments` 显式设置首轮消息模型，另补当前交接要求、私有包引用与激活前只读边界；随后核实真实运行模型。源模型未知时先补读，不靠默认值启动；后续消息不再强制覆盖模型。
2. 不确定创建是否成功时返回 `RECONCILE_EXISTING_SUCCESSOR`：查询原调用结果及任务清单，不再次 fork。同一交接及全部更新包只能绑定一个继任窗口；不要按标题猜任务 ID。
3. 取得真实线程 ID、同一保存项目、实际模型和原生权限读回，运行 `record-successor --handover-id ID --current-thread-ref CURRENT --proof PRIVATE_JSON`。证明字段：creationKey、sourceThreadRef、successorThreadRef、runtimeProjectId、model、permissionProfile、evidence。evidence 为真实平台结果私有文件的 reference/file/sha256；只封存引用和摘要，不写路径进回执。只有 clientThreadId 时仍等待创建完成，不把它当 threadId。
4. 用原生 send_message_to_thread 将交接包引用和补读要求交给已绑定继任者；它不写 C03、不派工，只读项目目标、大纲、已批准范围、成果、待办、在途任务、纠偏、等待、反馈、C08 事件及 C14 消息。需要等待时用原生 wait_threads；不靠标题或“知道了”判断已接管。
5. 继任者实际核查后运行 `record-handover-readback --handover-id ID --current-thread-ref SUCCESSOR --proof PRIVATE_JSON`。字段：handoverPackageDigest、successorThreadRef、communicationDigest、checks、evidence。checks 的 GOAL_AND_OUTLINE、APPROVAL_AND_SCOPE、OUTCOMES_AND_TASKS、WAITS_AND_CORRECTIONS、DELIVERY_FEEDBACK、EVENTS_AND_MESSAGES、NEXT_ACTION 必须都已实际检查。证据文件必须是实际检查结果，不能制造“全 true”。
6. 状态变化则由仍有效的当前中央 refresh-handover，同一个继任者补读，再提交最新包的证明；原生消息/账本只要变化就不能用旧快照激活。证据就绪后复用 activate-successor。启用此握手的交接无法跳过继任读回；历史手工交接保持旧合同兼容。
7. 接管成功后调用原生 navigate_to_codex_page 打开新中央；真实导航成功后运行 `record-handover-opened`，证明字段 successorThreadRef、opened=true、evidence。只有相同交接包已经激活才返回 CENTRAL_HANDOVER_COMPLETE。导航失败只重试导航/留证，不回滚路由或再建中央。旧中央只能完成这项已授权界面动作及留证，不能继续 C03 写入。

所有命令共用 C08 的 data-root/project-id/writer-id 参数。原生工具不可用、真实权限读回缺失或创建结果不能确定时保留现场并报告具体缺口，不把模拟回执、配置意图或制包成功说成真实交接完成。新原生回执有内容摘要校验；进程锁仅在确认原进程已不存在时恢复遗留标记。

主动计划换代不会自动修改 C03 `recovery` 状态，身份正常的任务窗口可以继续施工。只有真实失联已进入冻结恢复流程时，才按 C08 恢复总闸暂停并另取 Boss 恢复批准。

## 硬停

- 同一角色出现两个当前窗口；
- 交接包与当前路由或 C03 摘要不一致；
- 任务 ID、唯一标题、窗口代际任一不一致；
- 旧中央尝试派工、验收、确认事件或写账；
- 首次初始化时调用窗口与拟登记中央窗口不是同一原生任务；
- 事件声称来自未登记窗口；
- 把聊天送达当成任务状态已经更新。

任务身份不一致默认硬停该任务并保留其占用；只有 C08 已记录中央/项目失联冻结，或总账完整性无法证明时，才升级为项目级恢复总闸。
