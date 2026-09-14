# 项目归属证据协议

这是 C10 项目归属的唯一规则入口。目的：任务进入 Boss 选择的保存项目，同时不因应用缺少显示字段而强制往返交接。权限、模型、范围和独立验收另行核验。

## 原生创建与历史兼容

- 保存项目清单按真实路径匹配唯一项目 ID。Git 默认 `project + worktree`，非 Git `project + local`；不用 `projectless` 或自定义目录代替。
- 新单方法为 `DIRECT`。已封存 `LOCAL_BOOTSTRAP_TO_WORKTREE` 单继续执行原计划，保留实际 local/final 引用。它是历史兼容，不是新派发默认步骤。
- 配置 schema 保留接受旧 `LOCAL_BOOTSTRAP_TO_WORKTREE` 值，不要求用户迁移本机配置才能升级；新产品示例及新 `prepare` 默认 `DIRECT_PROJECT_WORKTREE` / `DIRECT`。旧配置值不重写已经封存的计划。
- 原生返回排队中的 `clientThreadId` 不是实际任务 ID。须从真实原生创建完成结果或应用记录取得 client→thread 关联，并保存原文；取得之前只保留同一候选，不重建任务。

## 项目编号直接读回

`taskWindow.runtimeProjectId` 始终记录应用真实读回。非空时必须与派发单的 `codexProjectId` 相同；不一致立即拒绝，任何补充证据都不能覆盖矛盾。

直接字段缺失可保留 `null` 或原始空字符串。仅 Git 标准 worktree 可使用下面的交叉证据路径；非 Git 缺字段目前仍需补充真实直接读回。

## 交叉证据格式

确认的 `taskWindow.projectAssociationEvidence`：

```json
{
  "method": "NATIVE_CREATION_AND_GIT_COMMON_DIR",
  "creation": {"file": "<私有绝对路径>", "sha256": "<文件摘要>", "source": {"file": "<工具原文 JSON>", "sha256": "<原文摘要>"}, "extraction": {"target": "/request/target", "threadId": "/response/threadId"}},
  "threadReadback": {"file": "<私有绝对路径>", "sha256": "<文件摘要>", "source": {"file": "<工具原文 JSON>", "sha256": "<原文摘要>"}, "extraction": {"threadId": "/thread/id", "projectId": "/thread/projectId", "cwd": "/thread/cwd"}},
  "savedProject": {"file": "<私有绝对路径>", "sha256": "<文件摘要>", "source": {"file": "<工具原文 JSON>", "sha256": "<原文摘要>"}, "extraction": {"projectId": "/projects/0/id", "path": "/projects/0/path", "isGitRepository": "/projects/0/isGitRepository"}}
}
```

JSON Pointer 必须按本次工具实际返回结构填写；上面只是结构例子，不是固定平台字段路径。三份提取文件分别为：

- `creation`：`target`（实际请求中的完整 project/environment 目标）、`threadId`（真实最终任务编号）。异步创建时保存真实创建请求/结果及完成关联的原文集合，用对应 Pointer 提取，不能将 client ID 当 thread ID。
- `threadReadback`：`threadId`、`projectId`、`cwd`。`projectId` 若实际返回为空必须保留空值。若原文完全省略该字段，可仅对这个字段使用 `{"pointer":"/thread/projectId","absentAsNull":true}`，程序须看到真实存在的父对象和缺失的末级字段，才接受归一化的 null。原文保持不变，映射记录“字段缺失”与“返回 null”的区别；不得在工具原文补写字段。
- `savedProject`：`projectId`、`path`、`isGitRepository`，必须来自保存项目清单，不从目录名称推断。

所有文件须在私有 data root 内。执行窗口从真实工具调用中保留请求、结果及操作关联，不粘贴模型自报作为原生观察。程序验证每份文件和原文的 SHA-256，并逐字段核对 JSON Pointer 提取一致性，然后核对项目、任务、目标环境和目录。

程序还实际运行本机 Git：保存根与任务根必须各自是真实仓库顶层；二者 `git-common-dir` 完全相同；任务必须是独立 worktree，不是主目录的别名。仅目录同名、字符串包含 `.codex/worktrees`、或两份文字声明一致均不能证明 Git 归属。文件不可读、符号链接循环、路径消失、Git 超时或冲突按结构化失败处理，不写 C03。

## 回执与信任边界

- C03 和派发确认同时保留原始 `runtimeProjectId`、核验后的 `verifiedProjectId`、证据来源方法与文件摘要。`directProjectReadbackState` 区分 `ABSENT`（末级字段缺失）、`NULL`（显式 null）、`EMPTY`（空字符串）、`VALUE`（实际编号）。续办仍读回实际任务并重新验证；不得把历史推导写成新的直接读回。
- 核验通过证明已提供的原生观察一致且本机 Git 属于同一项目；不能独立证明被恶意伪造的工具原文是真实平台签名。原文真实性由执行窗口的实际工具调用和独立读证负责。
- 交叉证据不证明侧栏显示已恢复，记录 `sidebarVisibility=UNVERIFIED`。侧栏只是展示，不能替代项目归属，也不因侧栏未显示自动重建任务。
- 单元测试全部使用明确标注的虚构原生观察和临时真实 Git 仓库，不得汇报成真实应用派发验收。
