# C09 中央施工控制器

## Boss 得到什么

现在 Codex 模块有且只有一个中央。C00 `construction-outline-planner` 在独立规划窗口与 Boss 形成大纲，不经过中央、不写总账；大纲冻结后，C09 一次读完整大纲和最新总账，形成包含全部 Codex 任务、Boss 决策、依赖链和并行波次的中央启动图。Boss 集中拍板并批准启动图后，中央连续调用 C03/C04/C05/C10，批量派发当前波次，不再逐任务重复确认相同范围。

这不是第三套任务中心。启动图只固定范围、依赖和批准摘要；详细任务状态仍只保存在 C03。

## 处理顺序

1. 确认请求来自 Boss，并识别项目与意图。
2. 验证能力登记表中只有一个 `PLANNER`、一个 `CENTRAL`；C00 只能产出 `NOT_REGISTERED` 候选，C12 必须是无状态 `INTERFACE`。
3. 状态读回必须显示 `automaticSuccessorDispatchAvailable=true` 和 `twoTaskWindowLifecycleEnforced=true`：前者表示合法 `DONE` 后中央按已批准启动图继续派发，后者表示同一窗口最多承接两项串行任务。
4. 新项目走 C02；已有项目必须先读 C03 的已验证总账。
5. 若项目处于失联冻结，任何普通动作都先转 C08。
6. 首次运营一次列出全部任务、集中决策和波次；日常单项请求仍选择 C03–C11 中一个主能力。
7. Boss 对启动图的明确批准可覆盖图中列明的 C03 登记、C04 任务包、C05 占用检查和 C10 波次派发；下游机械门禁仍不得跳过。
8. 前置任务经 C06 + Boss 确认为 `DONE` 后，中央自动放行通过 C05 的后续任务，不再询问是否继续；范围或风险变化会使受影响批准失效。
9. C09 必须实际消费 C06 的不可变续派触发，核对启动图摘要后逐项运行 C05 和 C10 准备；一项被阻塞只进入 `blockedTasks/failedTasks`，其他无关合格任务继续准备。

自动续派命令：

```bash
python3 plugins/codex-module-governance/scripts/central_construction_controller.py \
  --data-root <私有数据目录> \
  --caller-thread-ref <CURRENT_CENTRAL原生任务ID> \
  continue-successors \
  --project-id <项目ID> \
  --validation-id <刚完成任务的验收ID> \
  --execution-map <私有目录内的获批启动图JSON> \
  --writer-id codex-module-central
```

返回 `READY_FOR_BATCH_RUNTIME_DISPATCH` 只表示本批 C05 门禁和 C10 派发单已经准备好。中央还必须据 `preparedTasks` 发起真实 Codex 运行，并将真实任务/窗口 ID 交 C10 确认；在此之前不得记为 `IN_PROGRESS`。`waitingTasks` 只放尚有前置的任务，已经 `DONE`、正在执行、已准备待运行、被门禁阻止和输入失败分别进入独立清单，避免把不同业务状态混成“等待”。每次重入都读实时总账；既有派发单保持幂等，曾阻塞的任务解除后会生成下一份不可覆盖的 `successor-batch-rNNNN.json`，不会被第一份批次回执永久挡住。

## 当前明确不能做的事

- 不修改真实业务系统、页面、数据库或自动化。
- 不代替 Boss 做最终裁定。
- 不建立跨模块详细总账。
- 不替 C00 制定或改写施工大纲，也不把 C00 临时候选键当作正式任务编号。
- 不把 C10 派发计划冒充成真实运行成功；只有获得运行时 ID 才登记总账。
- 不逐任务重复索要同一启动图已经明确覆盖的批准，也不把范围化批准扩展到图外任务。

## 模型和子 Agent 规则

- 中央：`gpt-5.6-sol`。
- 任务窗口与一级子 Agent：`gpt-5.6-terra`。
- 每个父任务窗口最多 3 个一级子 Agent。
- 禁止子 Agent 再派下级 Agent。

## 验收

运行：

```bash
./scripts/verify-c09.sh
```

合格结果必须同时证明：

- 唯一中央数量为 1；
- C00 只有一个且角色为 `PLANNER`，没有运行脚本和总账写入权；
- C02–C11 均能被正确选择，C12 只负责翻译；
- 失联状态优先转 C08；
- 未授权写入被拒绝；
- C10 派发必须继续经过 Boss、C05 和真实运行确认；
- 启动图必须一次展示全部 Boss 决策、任务、依赖和并行波次；
- 范围化批准只能用于摘要和任务清单完全匹配的波次；
- 路由前后工程总账摘要不变；
- C01–C08 原有测试全部无回归。
- 合法 C06 触发能实际生成并行后续任务的 C10 派发单；兼容任务复用旧窗口，不兼容任务新开 Terra 窗口；单项 C05 阻塞不连带阻断其他任务；重复消费同一触发幂等。
