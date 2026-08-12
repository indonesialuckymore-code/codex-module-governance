# 中央启动图协议

中央首次接收冻结施工大纲时使用。它是“批准范围和依赖图”，不是第二套工程总账；任务实时状态仍只读取和写入 C03。

## 1. 输出顺序

先用一屏业务摘要告诉 Boss：共有多少 Codex 任务、多少项可并行、多少项等待前置、多少项需要拍板、是否存在冲突。随后按以下顺序展示：

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

- `NEW`：新 Terra 窗口，第 1 次承接；
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

- Git 项目：创建任务时使用 `target.type=project`、保存的 `projectId` 和 `environment.type=worktree`。
- 非 Git 项目：使用 `target.type=project` 和 `environment.type=local`。
- 不允许 `projectless`，不允许中央自行命名其他物理目录。
- 创建后回读的 `projectId` 必须一致；Git worktree 目录必须是 Codex 标准 worktree，非 Git 本地目录必须等于保存项目路径。
- 若 Git worktree 路径正确但回读 `projectId` 为空，C10 对同一任务执行一次原生往返交接：先到保存项目根目录完成项目绑定，再回到原 worktree，最后重新核对项目 ID、目录和最终任务 ID。修复失败保持 `NEEDS_REVIEW`。

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

此批准只覆盖图中列明的任务、对象、动作、风险和波次。以下任一变化会使受影响范围失效并重新交 Boss：新增任务、扩大写入对象、唯一写入方变化、风险升级、依赖变化、项目绑定变化、出现 UNKNOWN/CONFLICT、删除/批量覆盖/生产上线/付款/外发或重大权限变化未被逐项列明。

## 6.1 自动续派机器文件

Boss 批准后的运行版必须保存为私有 JSON，并符合 `schemas/codex-approved-execution-map.schema.json`。`scopeDigest` 是对 `executionPlan` 采用排序键、无多余空格的 UTF-8 JSON 计算 SHA-256；C05/C10 输入也必须引用同一个 `bossApprovalRef`、`scopeId`、`scopeDigest` 和完整 `taskIds`。

```json
{
  "executionMapSchemaVersion": "0.16.0",
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

每份 C05 `windowReview` 还必须记录 `contextCompatibility` 与证据引用：明确兼容才可复用为第 2 项；不兼容或未评估时新开 Terra；存在唯一旧窗口但兼容性为 `UNKNOWN` 时只阻塞该任务。C05 在决定第 2 项复用时原子预留唯一承接槽，避免同一批两个任务同时抢到同一旧窗口。

## 7. 派发结果

中央一次汇报：成功创建/复用的任务、真实任务 ID、所在项目、运行目录、并行波次、仍在等待的前置和失败项。只有真实 ID、正确项目归属和 C10 确认都成功时，任务才进入 `IN_PROGRESS`；创建卡片或可复制提示词不算成功。
