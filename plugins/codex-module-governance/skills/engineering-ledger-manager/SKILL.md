---
name: engineering-ledger-manager
description: 维护 Codex 模块唯一详细工程总账：登记任务、窗口、一级子 Agent、对象占用、证据引用、裁定和 Skill 状态，并校验回执与状态转换。用于“查看或更新 Codex 总账”“登记任务/窗口/对象占用”“接收完成信号”“检查总账完整性”等请求；不管理 Claude 明细、不替 Fable 制定全局计划、不直接施工业务。
---

# 工程总账管理器

这是 C03 的唯一详细账本入口。它只记录 Codex 模块内部的施工事实；Boss + Fable 5 仍掌握全局施工大纲、优先级、模块归属和跨模块交接。

## 写入前检查

1. 确认项目已由 C02 建档，且私有数据目录位于所有 Git 工作树之外。
2. 确认当前写入者是 `codex-module-central`。Boss、Fable、Claude 中央、任务窗口和子 Agent 都不能直接改账本。
3. 若 C08 已建立角色路由，还必须读取当前原生任务 ID，并与唯一 `CURRENT_CENTRAL` 路由完全匹配。正确 writer 名称但缺少/冒用 `callerThreadRef` 仍必须拒绝。
4. 先读取当前总账和不可变回执，检查同编号、对象占用、任务状态和硬停项。
5. 若请求来自跨模块交接，只记录交接引用和需要 Boss 的结论，不复制 Claude 的详细任务状态。

## 可登记的事实

- Boss + Fable 5 已规划的 Codex 任务。
- 新版大纲可依照[大纲成果交接协议](../../references/outline-outcomes.md)导入已批准的目标与成果，再为新任务登记成果关联。导入不自动生成候选对应的任务，也不重写历史任务。规划归属可以是 Boss 指定的总设计师，不要求固定模型。
- 大纲改版使用同一协议的 revise-outline 与 reconcile-outline，保留 outlineHistory 和受影响任务的承接关系；read-summary 的 businessProgress 直接由本账本和 C06 决定派生，不另建成果状态表。
- 每项任务只认一个不可变身份：`任务ID｜业务名称`。任务窗口在其后增加代际 `｜G1`、`｜G2`；业务名称不能脱离任务 ID 单独传播。
- 任务窗口与最多 3 个一级子 Agent 的登记信息。
- 登记模型时遵守[模型选择与真实运行记录](../../references/runtime-model-policy.md)：记录实际选择，模型不是身份；非默认模型手工登记须同时提供模型和证据，不覆盖历史回执。
- 每个窗口保存稳定窗口身份、当前任务和承接历史。窗口最多两条串行任务记录：第 1 项 `DONE` 后为 `AVAILABLE_FOR_REUSE`，第 2 项 `DONE` 后为 `RETIRED`；不得登记第 3 项。
- 对象占用声明；发生重叠时保留两份声明，标记 `CONFLICT` 和硬停。
- 证据、裁定和 Skill 的不透明引用与状态，不保存真实文件、日志或密钥。
- 施工回传的完成信号；相同信号只回报“已接收”，不重复推进状态。
- 执行安排改版的唯一当前摘要及历史由 C09 `revise-plan` 经 C03 标准回执记录在 `executionPlanRevisions`；不得手改。具体规则见[启动图协议第 8 节](../../references/central-construction/project-execution-map.md)。原批准、任务和旧版本不覆盖。

## 状态边界

涉及等材料/外部审批或交付后问题时，读取[外部等待与交付后反馈](../../references/task-followthrough.md)。复用原任务，记录明确责任与恢复条件；不冒充已设提醒或已中断运行。历史 DONE 与当前反馈同时呈现，可补关联已有修复；只有对应原问题的新 C06 验收及 Boss 最终批准后，才以 close-feedback 留证关闭。不手改状态，不另开一次记账审批。

涉及运行中人工纠偏时，先读[纠偏候选协议](../../references/task-corrections.md)。C03 记录连续版本、局部暂停及核验；C14 回执派生通信进度。只有当前中央实际读回并登记绑定当前版本的生效证据后才释放恢复资格，不把 APPLIED 当验收。当前仍仅限候选隔离验证，不得直接在真实项目启用或手改解除暂停。

- 本 Skill 可以把施工任务记录为 `PLANNED`、`READY`、`IN_PROGRESS`、`NEEDS_REVIEW`、`PARTIAL`、`CONFLICT`、`BLOCKED` 或撤销状态。
- `DONE` 必须等待 C06 独立验收和 Boss 批准；C03 不得自行写入。
- C06 写入 `DONE` 时同步结束该任务的活动子 Agent、清空窗口当前任务，并按承接次数把窗口转为可复用或退役。
- 本账本没有原生写前拦截。对象冲突是账本硬停信号，不是对外部系统的技术锁。

## 常用命令

先验证账本：

```bash
python3 plugins/codex-module-governance/scripts/ledger_manager.py \
  --data-root "/用户选择的私有目录" \
  --project-id "approved-project-id" verify
```

登记已规划任务：

```bash
python3 plugins/codex-module-governance/scripts/ledger_manager.py \
  --data-root "/用户选择的私有目录" \
  --project-id "approved-project-id" \
  --writer-id "codex-module-central" \
  --caller-thread-ref "CURRENT_CENTRAL原生任务ID" \
  add-task --task-id "C-07" --title "任务标题" \
  --business-goal "已获批准的业务目标" --plan-ref "plan-ref-001"
```

只有模块中央在 Boss 既定授权范围内才可运行写入命令。总账本身不授权施工、不创建 TEST，也不替代 C04–C08。

## 永久边界

- 不建立跨模块详细总账，不写 Claude 模块任务明细。
- 不允许窗口或子 Agent 直接更新总账；它们只能把结果回给父窗口，再经 Boss 和模块中央处理。
- 不接受真实业务数据、客户资料、Cookie、账号、密钥或实际证据文件正文。用于运行定位的私有输入路径只保留于私有计划/运行快照，不向公共产品仓库或业务摘要复制；它不是业务文件正文的存储许可。
- 不覆盖既有回执；每一次账本变更都生成一份不可覆盖的私有回执。
- 发现索引不一致、回执校验失败、并发账本锁或对象冲突时，停止推进并报告 `CONFLICT` / `REFUSED`。
- 运行时任务标题、任务包身份或回传任务 ID 与总账不一致时，报告 `TASK_IDENTITY_MISMATCH / NEEDS_REVIEW`，不得把任务推进为 `IN_PROGRESS`。
