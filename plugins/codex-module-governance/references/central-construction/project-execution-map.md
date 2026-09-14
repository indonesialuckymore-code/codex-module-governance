# 中央启动图协议

中央首次接收冻结施工大纲时使用。它是“批准范围和依赖图”，不是第二套工程总账；任务实时状态仍只读取和写入 C03。

## 1. 输出顺序

先用一屏业务摘要说明本期成果、近期必要任务、等待与待拍板事项；远期保持轮廓，不为填表提前拆碎。以下清单只覆盖本期拟批准范围：

1. 需要 Boss 一次拍板的全部事项；
2. 全部任务与业务结果；
3. 并行波次和前置关系；
4. 项目绑定与新旧窗口方案；
5. 授权范围、失效条件和验收方法。

## 2. 决策清单

每项只保留真正需要 Boss 决定的业务问题：

| 决策号 | 问题 | 选项及影响 | 建议 | 阻塞哪些任务 |
|---|---|---|---|---|
| D-01 |  |  |  |  |

不要询问可以只读核实的文件位置、当前状态、工具是否存在或对象真实 ID。所有决策集中展示，允许 Boss 用 `D-01=A，D-02=B` 一次回答。

## 3. 任务总表

| 正式任务建议 | 大纲候选 | 业务结果 | 当前状态 | 前置任务 | 写入/冲突对象 | 窗口 | 子 Agent | 波次 |
|---|---|---|---|---|---|---|---|---|
| C-001 | P-01 |  | `PLANNED` | 无 |  | NEW | 0–3 | wave-01 |

要求：

- 归属非 Codex 的任务只保留引用，不登记 C03。
- 同一写入对象、同一事实源、同一生产开关或同一不可隔离外部资源不得在同一波。
- 共享非独占只读可并行；隔离写入只有 C05 证明对象不重叠后才可并行。
- 依赖必须是有向无环图；循环依赖标 `CONFLICT`，不得靠随意排序掩盖。
- 每个任务仍保留独立任务包、回滚和验收；Boss 不必逐包重复批准相同范围。

## 4. 波次总表

| 波次 | 可同时派发 | 放行条件 | 等待对象 | 若失败 |
|---|---|---|---|---|
| wave-01 | C-001、C-002 | 启动图获批 + C05 合格 | 无 | 仅冻结受影响任务，不阻断无关任务 |
| wave-02 | C-003 | C-001 经 C06 + Boss 确认为 DONE，且 C05 复核 | C-001 | 保持 WAITING |

完成一个任务后只重算其下游和共享对象，不把全项目重新串行。

前置任务合法 `DONE` 后，C06 的 `successorDispatch` 是中央继续运行的持久触发。中央直接放行当前所有满足依赖与 C05 的任务，不再询问 Boss 是否继续；若没有合格任务，只报告等待的明确前置或变化项。

## 4.1 窗口承接计划

启动图的“窗口”列是中央建议，不是永久绑定。每次真实派发前由中央依据最新总账重新判断：

- `NEW`：新任务窗口（默认 Terra，可修改），第 1 次承接；
- `REUSE:<windowId>`：该窗口已完成第 1 项，本任务是第 2 次且最后一次承接；
- 一个窗口绝不同时运行两项任务，也绝不承接第 3 项；
- 第 2 项完成后窗口退役，后续任务必须新开或选择另一条只完成过 1 项的窗口。

## 5. Codex 项目绑定

中央必须通过当前 Codex 项目清单记录：

```yaml
runtimeProject:
  codexProjectId: ""
  projectPath: ""
  isGitRepository: true
  defaultEnvironment: WORKTREE
```

- Git 项目：新任务直接使用 `target.type=project`、保存的 `projectId` 和 `environment.type=worktree`。
- 非 Git 项目：使用 `target.type=project` 和 `environment.type=local`。
- 不允许 `projectless`，不允许中央自行命名其他物理目录。
- 项目归属统一遵守[项目归属证据协议](../project-association.md)：直接字段缺失可以核验真实创建关联和 Git 关系，不能伪填直接读回、反复新建或因缺字段强制往返。
- Git 最终目录必须属于同一保存项目的标准 worktree；非 Git 本地目录必须等于保存项目路径。历史已封存 local 引导计划保留原合同与真实引导/最终引用，不因新默认改变而重写。

## 5.1 任务权限与业务授权分离

默认完全访问的 C10 运行确认回传：

```yaml
permissionControl:
  permissionClass: FULL_ACCESS
  profile: "full-access"
  method: PERMISSION_PROFILE_READBACK
  evidenceRef: ""
  writableRoots: []
  governanceDataRootAccess: NOT_RESTRICTED
```

- `FULL_ACCESS` 是默认运行方式；Boss 可按任务主动选择 Codex 原生 `WORKTREE_SCOPED/:workspace` 高隔离模式。系统不得创建或依赖自定义权限 Profile。
- 受限模式仍必须列出覆盖当前运行目录的精确 `writableRoots`，且中央私有治理目录为 `DENIED`。
- 完全访问表示平台技术上未隔离中央私有目录，不得虚写 `DENIED`；它不扩大任务包、对象占用或危险操作授权。任务窗口仍不得直接写 C03，违规通过差异、日志和独立验收追责。

## 6. 范围化批准

最终图必须保存到仓库外私有数据目录，保留上一版，不得写入产品仓库；随后计算唯一 `planId` 和 SHA-256 摘要，并列出：

```yaml
authorizationScope:
  type: EXECUTION_MAP
  scopeId: "central-plan-001"
  scopeDigest: ""
  wavePolicy: RELEASE_WHEN_DEPENDENCIES_DONE
  taskIds: []
  excludedActions: []
```

建议批准语句：`批准中央启动图 central-plan-001（摘要前 12 位），按图登记并派发；后续波次满足前置和 C05 后继续放行。`

此批准只覆盖图中列明的任务、对象、动作、风险和必要前置。业务范围、必要依赖、唯一写入方、风险或项目绑定的实质变化仍须处理受影响批准；仅调整候选执行顺序，或增删未开工任务的纯排程等待，按下述版本协议保留原批准，不要求整图重批。出现 UNKNOWN/CONFLICT 或未授权的删除、批量覆盖、上线、付款、外发、重大权限变化，不能以“只是重排”为由放行。

## 6.1 自动续派机器文件

Boss 批准后的运行版必须保存为私有 JSON，并符合 `schemas/codex-approved-execution-map.schema.json`。`scopeDigest` 是对 `executionPlan` 采用排序键、无多余空格的 UTF-8 JSON 计算 SHA-256；C05/C10 输入也必须引用同一个 `bossApprovalRef`、`scopeId`、`scopeDigest` 和完整 `taskIds`。

C04 对每个任务生成的仍是 `DRAFT_REQUIRES_BOSS_REVIEW` 合同，因为 C04 自身不能派发。若它完全位于已批准启动图范围内，同一 `bossApprovalRef` 由 C05 审查并由 C10 再次机械核对，不得逐包重复请 Boss 批准。任务窗口执行 C10 派发单内嵌的 `taskPackage`，不依赖额外“最终包路径”。

```json
{
  "executionMapSchemaVersion": "0.17.0",
  "recordType": "C09_APPROVED_EXECUTION_MAP",
  "planId": "central-plan-001",
  "projectId": "example-project",
  "authorization": {
    "scopeId": "central-plan-001",
    "scopeDigest": "<64位sha256>",
    "bossApprovalRef": "boss-approval-central-plan-001"
  },
  "executionPlan": {
    "tasks": [
      {
        "taskId": "C-001",
        "dependencies": [],
        "packageId": "c001-package-001",
        "c05ReviewId": "c05-review-c001",
        "c05ReviewPath": "<私有数据目录内绝对路径>",
        "c10RequestPath": "<私有数据目录内绝对路径>"
      }
    ]
  }
}
```

每份 C05 `windowReview` 还必须记录 `contextCompatibility` 与证据引用：明确兼容才可复用为第 2 项；不兼容或未评估时新开任务（默认 Terra，可修改）；存在唯一旧窗口但兼容性为 `UNKNOWN` 时只阻塞该任务。C05 在决定第 2 项复用时原子预留唯一承接槽，避免同一批两个任务同时抢到同一旧窗口。

## 7. 派发结果

中央一次汇报：成功创建/复用的任务、真实任务 ID、所在项目、运行目录、并行波次、仍在等待的前置和失败项。只有真实 ID、正确项目归属和 C10 确认都成功时，任务才进入 `IN_PROGRESS`；创建卡片或可复制提示词不算成功。

## 8. 执行安排改版（候选源码已实现的有限范围）

搜索词：`revise-plan`、`approvedPlan`、`schedulingDependencies`、`executionPlanRevisions`。

本能力只调整已批准任务的先后与纯排程等待，不等于任务拆分/合并、设计版本迁移或运行中纠偏已全部实现。首次采用时读取真实批准范围和当前任务，不能把原先承诺给 Boss 的业务前置伪改为“可选排程”。

- `approvedPlan` 保留原获批安排，`authorization.scopeDigest` 仍校验这份原批准依据；原始 `0.17.0` 文件保留不改。
- 新的 `0.23.0` 是数据结构版本，不是已发布插件版本。`executionPlan` 可以重排原任务列表；每项可增添 `schedulingDependencies`，仅表示额外的排程等待。原 `dependencies` 是业务必需前置，不能通过此命令删除或改变。
- 任务集合、任务包、复核 ID、输入定位等不变。增删排程等待只适用于 `PLANNED` 任务；`READY` 或运行中任务须等后续协调纠偏功能，不可手改记录或假装已通知。
- 不强制新增阶段或波次；排程依赖必须引用同图任务，且与业务依赖合起来不能成环。排程等待当前同样以被依赖任务 `DONE` 为满足条件；部分成果提前可用的独立验收机制尚待实现。

中央在已授权推进范围内自动生成私有 `arrangement.json`（形如 `{"tasks": [...]}`），从当前完整任务列表调整顺序或增加排程字段；保留理由引用后运行：

```sh
python3 plugins/codex-module-governance/scripts/central_construction_controller.py \
  --data-root PRIVATE_ROOT revise-plan --project-id PROJECT_ID \
  --execution-map CURRENT_PRIVATE_MAP --arrangement PRIVATE_ARRANGEMENT_JSON \
  --revision-id REVISION_ID --reason-ref REASON_REF \
  --writer-id codex-module-central --caller-thread-ref CURRENT_CENTRAL_ID
```

中央已获执行组织授权时不为该技术命令重复请 Boss 批准；只看或讨论不是执行授权。`reasonRef` 引用已确认纠偏或中央获授权的安排依据，不得编造用户决定。

### 成功、失败与继续推进

- C03 `executionPlanRevisions` 保存唯一当前版本及不可覆盖历史，任务状态不复制、不改写。私有导出 JSON 只是投影，不能建立第二个计划状态台账。
- `EXECUTION_ARRANGEMENT_REVISED` 才表示新版本已登记并导出。`IDEMPOTENT_EXECUTION_ARRANGEMENT` 为同一请求重试，`NO_EXECUTION_ARRANGEMENT_CHANGE` 不制造多余版本。
- `PLAN_RECORDED_EXPORT_PENDING` 表示账本已记，但导出失败；不能说已派发。排除明确的本机导出故障后，使用同一原输入和 revision ID 恢复导出，不重复记账、不覆盖冲突文件。
- 回执的 `ledgerWritePerformed` 与 `artifactWritePerformed` 分别表示是否改账和是否写出文件；幂等重试补导出可能有文件写入，但不能据此误认为重复记账。
- 后续 `continue-successors` 使用返回的新版 artifact；旧版会被拒绝，已准备的任务根据真实 C03 状态继续去重，不因计划文件改版全部重新派发。
- C09 改版与续派共用非阻塞进程锁（当前 macOS/Unix 运行环境），进程退出由系统释放。遇到 `C09_PLAN_OPERATION_IN_PROGRESS` 等正在执行的操作完成后有限重试，不删除锁文件冒充解锁。
- C05 在私有账本锁内重核当前安排，C10 准备也复核；不能绕过中央入口忽略新等待。旧项目未采用新版本时保留原格式与处理方式。
- 原批准范围扩大、任务集合变化、必要依赖变化、包内容变更、运行中纠偏、成果验收边界变化和旧版业务设计迁移不由这个命令代办；保留原授权，不伪造“自动批准”。
