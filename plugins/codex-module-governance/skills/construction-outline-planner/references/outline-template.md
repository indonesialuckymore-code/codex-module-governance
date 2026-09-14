# 施工大纲标准模板

按项目实际填写。未知内容写 `UNKNOWN`，不要猜测；不适用内容写 `N/A + 原因`，不要直接删除关键门槛。

以成果和使用效果组织，不以任务数量衡量进展。以下是可裁剪模板，模块和阶段可省略；小项目可只有一个目标、一个成果和一个执行任务。近期细化，远期保留轮廓。验收和恢复要求按风险设计，不适用项说明原因；运行端尚未支持的新策略不能仅靠填写模板视为生效。

## 0. 文档控制

- 项目标识：
- 项目名称：
- 大纲版本：`v0.1`
- 状态：`DRAFT`
- `PLANNING_OWNER`：
- Boss：
- 私有保存位置：
- 依据材料截止时间：
- 上一版本与变更原因：

## 1. 一页业务说明

- 当前问题：
- 最终要得到的业务结果：
- 人工填什么：
- 系统算什么或执行什么：
- 结果下一步流向哪里：
- 成功的业务判定：

### 目标与可用成果

| 目标 ID 与业务目标 | 成果 ID 与交付物 | 给谁用 | 可复核的可用标准 | 够用即可交付的边界 | 复用与缺口 |
|---|---|---|---|---|---|
| goal-001 | outcome-001 |  | criterion-001： |  |  |

成果可以是系统能力、业务指南、可供决定的研究结论或经核验的运营结果，不限于一种项目。

## 2. 范围和禁区

### 本期包含

### 本期明确不做

### 不得触碰

- 真实数据：
- 生产系统：
- 权限与账号：
- 外发、付款或删除：
- 其他：

## 3. 权威材料与证据等级

| 来源 | 截止时间/版本 | 证据等级 | 能证明什么 | 不能证明什么 |
|---|---|---|---|---|
|  |  | 文档/静态/实时/动态 |  |  |

## 4. 现有机制溯源

| 机制或模块 | 主归口 | 状态 | 当前写入方 | 复用/修改/废弃 | 证据 | 风险 |
|---|---|---|---|---|---|---|
|  |  | `UNKNOWN` |  |  |  |  |

明确列出旧版、半成品、历史补丁、废弃但仍被引用的机制，以及重复建设风险。

## 5. 单一事实源与业务数据流

| 业务事实 | 人工入口 | 系统计算/处理 | 唯一保存位置 | 唯一写入方 | 下游去向 | 读回方法 |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |

## 6. 模块和责任边界（需要时使用）

| 模块 | 业务作用 | 输入 | 输出 | 施工归属 | 详细状态归口 | 禁止事项 | 与其他模块交集 |
|---|---|---|---|---|---|---|---|
|  |  |  |  | Codex/Claude/人工/外部 |  |  |  |

复杂交集需要时用简图表示；简单项目用一句责任归属即可。不能确认的依赖标 `UNKNOWN`。

## 7. 施工阶段和依赖（需要时使用）

| 阶段 | 业务目标 | 前置依赖 | 可并行条件 | 完成门槛 | 未通过时后果 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 8. 候选任务清单（尚未登记）

每项都标 `NOT_REGISTERED`，冻结后仍须由中央查重和重新编号。

候选数量不是正式任务数量承诺。调查、下载、翻译、编写和补证默认留在成果任务内部；另拆必须说明独立交付、责任、授权或实际并行/冲突理由。不为凑齐模板创建任务。

### P-01：候选任务名称

- 状态：`NOT_REGISTERED`
- 执行归属：`CODEX / CLAUDE / HUMAN / EXTERNAL`
- 详细状态归口：
- 是否允许 Codex 中央登记：`YES / NO`
- 业务结果：
- 对应成果 ID：
- 独立拆分理由（只有一项时可省略）：
- 够用即可交付的边界：
- 原因与影响环节：
- 前置依赖：
- 必读材料：
- 允许读取：
- 允许修改：
- 绝对禁止：
- 唯一写入方：
- 冲突对象：
- 改前快照：
- 执行与回滚责任：
- 正例：
- 反例：
- 幂等/重复触发：
- 上下游读回：
- 交付物和证据：
- 硬停条件：
- 新任务/旧任务建议：
- 执行载体建议（仅影响设计时填写，模型可调整）：
- 调查或尝试停止条件：
- 外部等待的责任人和恢复条件：
- 当前风险和缺口：

## 9. 验收总方案

- 静态检查：
- 动态正例：
- 动态反例：
- 幂等：
- 回滚演练：
- 记录历史与日志：
- 上下游反向追溯：
- 保护范围比对：
- 独立验收人：
- Boss 最终判断：

## 10. 风险与硬停

| 风险 | 等级 | 触发信号 | 硬停动作 | 恢复前置 | 裁定人 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 11. Boss 决定记录

| 决定编号 | 问题 | 可选方案 | Boss 决定 | 影响 | 证据引用 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 12. 未确认事项

- `UNKNOWN`：
- `CONFLICT`：
- 待补实时证据：
- 外部依赖或待选 Skill：
- 以后才做：

## 13. 冻结检查

- [ ] Boss 已明确批准唯一版本
- [ ] 重大范围、模块归属和优先级已确认
- [ ] 历史版本已保留
- [ ] 所有目标有成果覆盖，候选任务引用成果，成果有可用标准和停止边界
- [ ] 涉及写入的任务有恢复责任，不适用验收项有理由
- [ ] 隐私材料未进入公开产品仓库
- [ ] 文件 SHA-256 已记录或标为 `PENDING_FILE_WRITE`
- [ ] 状态为 `FROZEN / NEEDS_REGISTRATION`，未写成 `DONE`

## 14. `OUTLINE_HANDOFF`

```yaml
recordType: OUTLINE_HANDOFF
handoffSchemaVersion: "0.23.0"
projectId: ""
designRole: PROJECT_CHIEF_DESIGNER
outline:
  title: ""
  version: ""
  status: FROZEN_NEEDS_REGISTRATION
  path: ""
  sha256: ""
planningOwner: ""
bossApproval:
  reference: ""
  approvedAt: ""
scopeSummary: []
outOfScope: []
modules: []
goals:
  - goalId: goal-001
    description: "填写最终业务目标"
outcomes:
  - outcomeId: outcome-001
    goalId: goal-001
    title: "填写可交付成果"
    executionOwner: CODEX
    completionBoundary: "填写何时已经够用，哪些优化本期不做"
    acceptanceCriteria:
      - criterionId: criterion-001
        description: "填写可复核的使用效果及证据"
centralRegistrationScope:
  policy: CODEX_ONLY
  includeCandidateKeys: [P-01]
  excludeCandidateKeys: []
candidateTasks:
  - candidateKey: P-01
    status: NOT_REGISTERED
    executionOwner: CODEX
    codexCentralRegistration: true
    outcomeIds: [outcome-001]
    dependsOn: []
    suggestedFirst: false
knownOccupancy: []
singleWriterFacts: []
conflicts: []
hardStops: []
unknowns: []
pendingEvidence: []
nextCentralAction: "读取完整项目设计，只查重和登记 CODEX 范围；核验最新工程总账、重新编号，不改写总设计，不机械派发"
```

此 YAML 仅供阅读；正式交接输出同结构私有 JSON，由中央调用 C03 `import-outline` 校验。项目 ID、批准引用、版本及真实文件 SHA-256 必须填写，空占位不能登记。具体使用及未实现边界见[成果交接协议](../../../references/outline-outcomes.md)。

## 15. 工程状态摘要

- 本版新增：
- 本版修改：
- 本版复用：
- 本版废弃：
- 仍未完成：
- 存在冲突：
- 需要 Boss 确认：
- 建议中央首先审查：
