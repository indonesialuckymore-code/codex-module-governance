# 大纲成果交接协议（本轮候选源码）

规划维护设计，C03 维护执行事实，任务包携带相关成果的不可变快照；不建立第二份任务台账。模型、窗口和阶段不是成果身份。

## 最小结构

交接采用私有 JSON，`recordType=OUTLINE_HANDOFF`、`handoffSchemaVersion=0.23.0`。全项目大纲另存私有文件，交接中的 `outline.sha256` 必须对应真实文件。版本表示交接数据结构，不表示插件已经发布到 0.23.0。

- `projectId` 与 C03 项目一致；`outline` 包含 `version`、`sha256`、`status=FROZEN_NEEDS_REGISTRATION`。
- `bossApproval.reference` 指向已存在的、对象和范围明确的批准证据；字符串校验不能代替核对真实批准。
- `goals[]`：`goalId`、`description`。每项目至少一个目标，目标须有成果覆盖。
- `outcomes[]`：`outcomeId`、`goalId`、`title`、`executionOwner`、`completionBoundary`、`acceptanceCriteria[]`。验收项有 `criterionId`、`description`，说明何种证据证明成果可用。
- `candidateTasks[]`：`candidateKey`、`status=NOT_REGISTERED`、`executionOwner`、`codexCentralRegistration`、`outcomeIds[]`、`dependsOn[]`。依赖引用候选键，不允许悬空或循环。候选不是正式派工数量承诺。
- `centralRegistrationScope.policy=CODEX_ONLY`；`includeCandidateKeys`、`excludeCandidateKeys` 不重叠且完整覆盖候选。只登记责任归属 CODEX 的候选及成果。
- 执行归属枚举 `CODEX/CLAUDE/HUMAN/EXTERNAL`。板块、阶段、收件人、外部等待等可作为附加设计信息，不强制层级；附加字段不是已实现的调度功能。

## 中央实际操作

先核对完整设计、批准依据、已有成果与任务，确认没有第二套设计事实源。数据根目录、输入文件和大纲文件均使用本项目仓库外私有位置；下列占位参数由已核验信息替换：

```sh
python3 scripts/ledger_manager.py --data-root PRIVATE_ROOT --project-id PROJECT_ID \
  --writer-id codex-module-central --caller-thread-ref CURRENT_THREAD \
  import-outline --handoff PRIVATE_HANDOFF_JSON --outline-file PRIVATE_OUTLINE \
  --boss-approval-ref APPROVAL_REF
```

同一交接重复导入不增加总账版本；不同交接拒绝直接替换，并报告 `OUTLINE_UPDATE_REQUIRES_RECONCILIATION`。改版使用下面的显式版本迁移，不直接改账本。

导入不会创建、取消、改名或重写任何任务。中央判断有用的交付边界后，用原有 `add-task` 命令增加一个或多个 `--outcome-id`；允许一任务承接多个相关成果，也允许同一成果由必要的多个任务推进，不强制“一候选一任务”。任务必须引用 Codex 批准候选覆盖的成果。

已导入此协议的项目，新登记任务必须有成果关联。未采用协议的旧项目继续原方式；旧任务即便在导入后也保留原记录，不自动改写历史。C04 给新关联任务的任务包附上目标、成果、验收与大纲摘要指纹。

## 大纲改版与保留既有工作

使用 `revise-outline`，参数同 import-outline，另加 `--expected-digest CURRENT_DIGEST --change-id CHANGE`。新版本与新批准引用必须明确；同编号同输入幂等，旧摘要不能覆盖新版。历史合同保存在原 C03 outlineHistory，版本影响在 outlineRevisions；不是第二份任务总账。

改目标、成果、验收、边界或责任归属时，相关任务追加 outlineImpacts，暂停其后续治理放行，不改原任务状态。未受影响任务继续使用自己的原合同，旧成果和证据不删除。范围信息无法精确归属时保守标影响，须人工核查；不能从程序比较 JSON 宣称已理解所有自然语言变化。

实质改变任务交付边界时，保留原 DONE，或通过既有取消流程停止旧施工；按新版登记承接任务并复用有效成果，不从零重做。`reconcile-outline --task-id OLD --proof PRIVATE_JSON` 的证明字段为 changeId、replacementTaskIds、oldExecutionStopped=true、evidence（reference/file/sha256）。程序要求旧任务确为 DONE/CANCELLED、新任务属于当前合同且覆盖仍需交付的成果，随后记录关联。移出 Codex 范围的成果可没有替代任务，但必须有真实停止及归属证据。证明需要中央实际核查，不能凭布尔声明冒充停止。

纯优先级/内部步骤变化不改业务大纲、不取消重建任务，走既有执行安排或局部纠偏。当前在途合同不支持无停顿原地改写；按以上有记录的承接处理。

## 成果验收与业务总览

C04 合同携带目标、成果和具体 criterionId。采用此协议的任务，C06 评审必须提供完整 outcomeAssessments：outcomeId、criterionId、reference、verdict（PASS/FAIL/MISSING）、independentlyReadBack。不得遗漏、重复、引用其他成果的标准，证据须已在回传声明。不适用质量检查不能免掉成果的可用标准。失败为 PARTIAL，缺证/未读回为 NEEDS_REVIEW；通过仍须 Boss 最终批准。

C03 read-summary 的 businessProgress 从原账本和不可变 C06 最终批准生成，展示目标、成果、缺失标准、等待、纠偏与反馈。不用任务数替代进展；历史 DONE 出现未闭环反馈时当前成果显示 NEEDS_REVIEW。旧版本成果只有内容和目标相同、批准链可验证时才能复用。efficiency=NOT_MEASURED 直到另有真实比较，不能编造节时百分比。

## 当前实现边界

验证范围见[候选版状态](release-status.md)，不表示已迁移生产项目。成果总览不绕过 C06/Boss，不自动识别所有业务影响，也未实现依据部分验收成果提前解除整任务前置依赖。未获批准的自动最终 DONE 不启用。
