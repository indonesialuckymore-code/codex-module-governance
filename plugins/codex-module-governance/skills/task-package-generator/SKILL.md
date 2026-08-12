---
name: task-package-generator
description: 把 Boss 与 Fable 5 已规划、已登记到 Codex 模块账本的任务，生成一份可审阅、不可派发的标准施工任务包。用于“生成任务包”“把规划变成施工包”“先出任务包不要派发”等请求；不制定全局计划、不创建任务窗口或 Agent、不占用对象、不创建 TEST，也不执行任何业务施工。
---

# 施工任务包生成器

将已经登记为 `PLANNED` 的 Codex 模块任务，整理成一份供 Boss 审阅的施工合同。任务包不是派工单：它不创建窗口、子 Agent、对象占用、TEST 或业务写入权限。

## 生成前门槛

1. 读取 Boss + Fable 5 已确定的任务规划；Fable 只提供全局路线，不写 Codex 细账。
2. 确认任务已由 `codex-module-central` 登记在 C03，状态为 `PLANNED`。
3. 校验 C03 当前账本和回执链；账本不完整、存在未解释硬停或任务不在 `PLANNED` 时，停止生成。
4. 将任务范围写成不含密钥、Cookie、客户资料、绝对路径或真实证据文件的结构化 brief。

## 必须生成的内容

- 窗口建议（新开或沿用）及理由；这只是建议，不会创建窗口。
- 业务目标、任务范围、必读材料与实时核查对象。
- 允许动作、绝对禁区、施工前快照和执行顺序。
- 正例、反例、幂等、回滚、日志和上下游读回等验收要求。
- 硬停条件、回滚方案、交付清单及“先向 Boss 回报”的回传规则。
- 明确的 C05 前置：对象占用与冲突检查通过前，任务包不得派发。

## 状态与边界

- 输出状态固定为 `DRAFT_REQUIRES_BOSS_REVIEW`。
- 任务包写到仓库外的私有目录，并附带不可覆盖的生成回执。
- `dispatchAllowed`、`taskWindowCreated`、`subAgentCreated`、`objectClaimCreated`、`testCreationAllowed` 和 `businessWriteAllowed` 始终为 `false`。
- Boss 批准任务包后，仍须由 C05 检查对象占用与冲突；C04 不得把任务推进为 `READY` 或 `IN_PROGRESS`。

## 常用命令

先预演，不写入：

```bash
python3 plugins/codex-module-governance/scripts/task_package_generator.py \
  --data-root "/用户选择的私有目录" \
  --project-id "approved-project-id" \
  generate --package-id "c04-task-001" --task-id "C-04" \
  --brief "/私有brief.json" --dry-run
```

只有 Boss 明确批准生成该草案后，才使用 `--apply`。即使生成成功，也只得到待审任务包，不会派发或施工。

## 永久边界

- 不创建、复用或派发 Codex 任务窗口；窗口选择只是建议。
- 不写 C03 任务状态，不写 Claude 细任务，也不让 Fable 成为第三个详细任务中心。
- 不在 C05 前声明对象可写或可并行。
- 不把任务包、生成回执或 brief 放入产品 GitHub 仓库。
