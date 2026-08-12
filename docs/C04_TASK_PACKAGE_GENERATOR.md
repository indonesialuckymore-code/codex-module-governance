# C04 施工任务包生成器

## 它解决什么问题

C04 把 Boss + Fable 5 已规划、并已登记进 C03 的 Codex 任务，变成一份可审阅的标准施工合同。它解决的是“以后有任务时，每次都重新想如何派工”的问题。

它不是派工器：不会创建任务窗口、子 Agent、对象占用、TEST 或业务写入。

## 它在架构中的位置

```text
Boss + Fable 5 的全局规划
        ↓
C03：Codex 中央登记 PLANNED 任务
        ↓
C04：生成 DRAFT_REQUIRES_BOSS_REVIEW 任务包
        ↓
Boss 审阅任务包
        ↓
C05：对象占用与冲突检查
        ↓
后续才可能派发任务窗口
```

## 输入与输出

输入由两部分组成：

1. C03 中状态为 `PLANNED` 的 Codex 任务。
2. 仓库外私有目录内的 C04 brief。brief 不得包含客户资料、真实日志、绝对路径、Cookie、密钥或真实业务对象。

输出包含窗口建议、任务边界、必读材料、实时核查、允许动作、禁区、快照、执行顺序、正反例/幂等/回滚验收、硬停、交付和回传规则。

输出固定为 `DRAFT_REQUIRES_BOSS_REVIEW`，且以下值固定为 `false`：

- `dispatchAllowed`
- `taskWindowCreated`
- `subAgentCreated`
- `objectClaimCreated`
- `testCreationAllowed`
- `businessWriteAllowed`

## 必经门槛

- C03 回执链必须完整。
- C03 不得有未解除的硬停。
- 任务必须仍是 `PLANNED`；已经 `READY`、施工中或验收中的任务不能重新生成新包。
- 只有 `codex-module-central` 可以正式生成并写入草案。
- C05 必须在 Boss 审阅之后检查对象占用；C04 不得代替 C05 放行派发。

## 私有目录结构

```text
<private-data-root>/
├── briefs/                              # Boss/Fable 提供的去敏 brief
│   └── <task-id>.json
└── task-packages/
    └── <project-id>/drafts/<package-id>/
        ├── task-package.json
        └── receipts/
            └── receipt-000000-generate.json
```

以上文件禁止进入产品 GitHub 仓库。

## 验收标准

1. 只读预演能显示建议窗口和所有边界，但不创建目录或任务包。
2. 正式生成只创建草案、不可覆盖回执和私有目录。
3. 生成后 C03 原任务仍为 `PLANNED`，账本 revision 不变。
4. 非模块中央、非 `PLANNED` 任务、账本硬停或回执异常均会被拒绝。
5. brief 含绝对路径、Cookie、密钥或类似敏感值时被拒绝。
6. 篡改任务包回执后，校验必须失败。

## 当前边界

C04 只生成草案，不记录对象占用、不处理冲突、不启动窗口，也不进入真实业务施工。C05 已接续占用和冲突，C06 已接续独立验收，C07 已接续顾问阻塞，C08 已接续失联冻结与恢复；实际派发仍待 C09/C10。

## 调用顺序

```bash
python3 plugins/codex-module-governance/scripts/task_package_generator.py \
  --data-root "/用户选择的私有目录" \
  --project-id "approved-project-id" \
  generate --package-id "c04-task-001" --task-id "C-04" \
  --brief "/用户选择的私有目录/briefs/C-04.json" --dry-run
```

命令中的 `generate` 必须放在 `--project-id` 之后、任务包参数之前。`--apply` 只生成待审草案，仍不会派发。
