# 外部等待与交付后反馈（候选实现）

搜索词：record-wait、resolve-wait、record-feedback、externalWaits、deliveryFeedback。

复用 C03 原任务和不可变回执，不新增项目状态中心。Boss 的业务话由中央整理成以下输入，不让 Boss 手填技术表格。验证范围见[候选版状态](release-status.md)；登记等待不等于原生中断。

## 外部等待

当原任务确实依赖员工材料、供应商回复、外部审批或 Boss 决定时，中央记录：

```text
ledger_manager.py ... record-wait --task-id TASK --wait-id WAIT
  --owner-ref OWNER --condition-ref CONDITION --reason-ref REASON
```

引用指向私有业务说明：谁负责、等什么、怎样证明条件满足、为何影响本任务。相同编号相同内容零写入重试；相同编号换条件或负责人拒绝。新的等待用新编号，不能复用已解决等待把历史改回去。

- 任务原状态、完成信号、证据、窗口和占用均保留，不把局部等待写成项目全局硬停。无关任务继续；同一任务有多项等待时逐项解决。
- C05/C10/C06 与新 WRITE 入口拒绝继续受影响工作；C09 重算时将它列入等待，不返回之前准备的开工动作。C14 普通施工消息即使早已排队也不能在等待中投递，纠偏及取消通知保留原有通道；已有完成/反馈材料仍可留证。
- `runtimeInterrupted=false` 表示没有证明原生命令已停。中央须核对在途工作、传递必要指令并取得真实回执，不能把登记等待冒充停止。因等待引发工作方向变化时走原有纠偏协议，不用外部等待绕过版本和通信核验。
- `followupStatus=NOT_SCHEDULED` 是事实，不是已经创建提醒。用户要求定时跟进时使用平台原生自动化，先查已有自动化避免重复。取得真实结果后，用 `bind-wait-followup --task-id TASK --proof PRIVATE_JSON` 绑定字段 waitId、conditionRef、automationId、targetThreadRef（当前中央）、scheduled=true、evidence（reference/file/sha256）。仅返回回执记录 SCHEDULED_RECEIPT_RECORDED，不改变 WAITING。调度偏好仍归平台，不另立调度中心；中央换代须检查原自动化目标并用平台更新，再核对等待记录，不能以旧目标回执承诺新中央已安排。当前不自动迁移平台调度。

恢复时由当前中央实际核对材料、条件和在途影响，在私有目录写 resolution JSON：

```json
{
  "waitId": "wait-material",
  "taskId": "C-example",
  "conditionRef": "condition-material",
  "directionContext": {"revision": 0, "digest": "NONE"},
  "conditionSatisfied": true,
  "executionSafeToResume": true,
  "evidence": {
    "reference": "material-readback",
    "file": "<私有数据目录内实际证据文件>",
    "sha256": "<实际文件字节摘要>"
  }
}
```

运行 `resolve-wait --task-id TASK --resolution PRIVATE_JSON`。有纠偏时 directionContext 必须使用当前版本；不能只给旧读回补新版本号。核验文件必须存在、位于私有目录且摘要相符。账本仅保存不透明引用、摘要、核验者和时间，不存正文或文件路径。摘要只能验证文件绑定，中央仍负责真实业务读证。

解决一项不解除其他等待，也不解除人工纠偏暂停。`resumptionEligible` 只反映本地等待/纠偏条件，仍须重走适用状态、授权、恢复和占用门槛；`runtimeResumed=false` 表示没有执行原生恢复。相同解决请求重试不重复记账，不自动派任务或标 DONE。

## 交付后反馈登记

```text
ledger_manager.py ... record-feedback --task-id ORIGINAL_TASK
  --feedback-id FEEDBACK --validation-id ORIGINAL_VALIDATION
  --issue-ref ISSUE --affected-ref AFFECTED_DELIVERABLE
  [--repair-task-id EXISTING_REPAIR_TASK]
```

先溯源：是已有反馈、已登记的修复任务，还是新问题。命令核对原任务实际 DONE、原 C06 决定和 Boss 最终批准的回执链；绑定原合同、原验收和最终批准摘要，以及已有成果 ID。affected-ref 是受影响交付内容的私有引用，不代表程序已核实问题成立。

登记只表示 `REPORTED_REQUIRES_REVIEW`，不把用户报告直接升级为已确认缺陷。原任务历史 DONE 不改写；C03 摘要通过 deliveryFeedback / currentDeliveryState 同时显示当前待核查问题。已有修复任务可以引用，不自动创建任务、另开窗口或把原任务重做一遍；关联本身不批准新的施工范围。

首次未指定修复任务时，后续使用 `link-feedback-repair --task-id ORIGINAL_TASK --feedback-id FEEDBACK --repair-task-id EXISTING_REPAIR_TASK` 补关联。原报告不改写，追加 repairLink 并生成原账本回执；同一关联重试零写入，不允许静默换成另一任务，不能关联原任务自身或已结束任务。

原修复确已 CANCELLED 后，可运行 `retarget-feedback-repair --task-id ORIGINAL --proof PRIVATE_JSON`。证明含 feedbackId、previousRepairTaskId、repairTaskId、reasonRef、evidence（reference/file/sha256），新修复必须存在且未结束。改绑只追加 repairRetargets，不覆盖原关联；旧修复验收摘要随之失效，新任务仍走 C04/C05/C06/Boss，不直接关闭问题。原修复还在运行时不能改绑。

## 修复验收与关闭

关联不代表批准新施工。复用既有 C04 合同、C05 范围批准及原任务执行路径，合同应明确要纠正的交付内容和原问题的可用标准，不为反馈另建一套验收中心。

C06 修复评审可附非空 `feedbackAssessments` 数组，每个问题一项：

```json
{
  "originalTaskId": "C-original",
  "feedbackId": "feedback-example",
  "feedbackDigest": "<C03 feedback_binding 对原反馈与修复关联的摘要>",
  "evidenceRef": "new-resolution-readback",
  "issueResolved": true,
  "independentlyReadBack": true
}
```

中央从 C03 原记录生成绑定，不让 Boss 填表。摘要不含可变 status/closure，包含原问题、原合同/验收及修复关联；不能拿另一问题、旧关联或无关任务的通过结论替代。evidenceRef 必须已由本次回传声明，且不得复用原验收已使用的证据引用。新引用须指向不可覆盖的新版本证据；不得把旧材料换名冒充修复，程序不能仅凭引用证明业务语义。

C06 独立对照原问题、受影响内容、原可用标准及修复合同读回新成果。问题未解决为 PARTIAL，未独立读回为 NEEDS_REVIEW；一般证据和风险门槛仍全部适用。该逐问题判断随 C06 不可变决定封存，最终批准时再次检查关联和当前纠偏版本。未附此项的普通验收保持兼容，但不能用于关闭反馈。

修复完成 C06 验收且 Boss 最终批准后，当前中央在同一授权流程内执行 `close-feedback --task-id ORIGINAL_TASK --feedback-id FEEDBACK --validation-id REPAIR_VALIDATION`，不再询问“是否记账关闭”。中途断线时查询 C03，使用同一验收编号重试，不能只因修复任务 DONE 手动消掉问题。

命令重新核对 C06 决定/最终批准回执链、修复实际 DONE、对应问题的已解决及独立读回结论和当前方向，之后追加 closure 摘要，状态改为 CLOSED_VERIFIED。相同关闭请求幂等，不重写历史 DONE、不创建任务、不重复派发、不自动解除上下游等待。关闭一项不隐藏其他问题；全部已报告问题关闭时摘要仅表示 REPORTED_ISSUES_CLOSED，不声称整个成果永远无缺陷。发现新问题继续登记新的反馈，保留旧关闭证明。

中央日常汇报必须同时读取等待和反馈，不只看历史 tasks 状态。反馈对上下游的实际影响尚需独立核查：明确需要停下的任务可使用任务局部等待/纠偏，不扩大成无依据的全项目硬停，也不得因原任务曾 DONE 忽略当前问题继续使用可疑成果。

## 当前范围

已做：任务局部等待/证据解除、多等待和纠偏并存、入口及积压消息保护、原交付反馈登记、补关联、逐问题 C06 新证据验收及 Boss 最终批准后的幂等关闭。

候选另已加入真实调度结果的回执绑定、取消后的修复改绑、C03 成果总览及 C08 增量交接。尚未进行真实暂停/恢复、真实定时跟进、平台调度迁移或旧项目运行迁移；业务影响仍须读证核查，不能声称自动识别所有外部条件。源代码测试不代替这些验收。

已存在自动化的目标随中央换代更新后，再对同一 automationId 调用 bind-wait-followup 绑定新中央真实读回；程序追加 followupRetargets，不覆盖原 followupReceipt。读取时以最后一次改绑为当前回执，禁止为解决目标过期直接创建重复自动化。此入口不自行调用平台更新，也不证明自动化永远仍处于启用状态；执行前仍需平台实时回读。
