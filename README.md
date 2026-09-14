# Codex Module Governance

一个私有的 Codex 多任务施工治理产品骨架。它让 Boss 先通过唯一总设计师窗口形成整个项目施工大纲，再由 Codex 唯一中央在明确授权下接管大纲中归属 Codex 的项目、任务窗口、证据、冲突和恢复。

## 当前阶段

本地候选为 `0.23.0-rc.1`，已安装试用并完成部分隔离原生验证，尚未作为稳定版发布。已验证能力与未验证范围统一见[候选版状态](plugins/codex-module-governance/references/release-status.md)。唯一中央与 C03 保持不变；候选包含模型自由、原生权限、通用成果关联、大纲版本影响、局部纠偏、等待和反馈、适配验收、成果总览及中央换代回执。不把安装或模拟通过当作生产业务验收。

- Codex 插件清单与本仓库 marketplace；
- C00 从当前任务已有内容续接、逐项向 Boss 澄清、冻结大纲并生成中央交接摘要；
- 私有数据隔离、版本和发布规则；
- 用户配置样例与 schema；
- C02 新项目查重、私有建档与启动卡校验；
- C03 模块任务、窗口、子 Agent、对象占用、证据引用和不可覆盖回执；
- C03 写入在 C08 路由激活后还必须带当前原生中央任务 ID，与唯一 `CURRENT_CENTRAL` 匹配；
- C04 从已规划任务生成的待 Boss 审阅施工任务包；
- C05 经 Boss 审阅后的整包占用预留、冲突硬停和新旧窗口建议；
- C06/C08 回传票据、自动入箱、单槽独立验收、按成果及实际影响读证、Boss 最终 `DONE` 门槛及不可变续派触发；旧合同保留原验收标准；
- C07 原窗口阻塞、顾问建议、Boss 决定与原窗口回传边界；
- C08 失联冻结、中央/裁定计划换代、逻辑角色路由、持久任务事件箱、恢复决定和受控占用释放；
- C09 唯一中央读完整大纲，集中列出 Boss 决策、本期成果及近期任务、必要依赖；远期保持轮廓，不强制提前细拆；
- C10 复用中央启动图批准，按波次批量派发，强制绑定保存项目并核对 `任务ID｜业务名称｜G代际`；任务窗口默认使用原生完全访问，Codex 原生 `:workspace` 受限权限仅在 Boss 主动选择时使用，两种模式都须真实读回；系统不再创建或依赖自定义权限 Profile；同一窗口最多承接两项串行任务，第二项完成后强制退役；任务窗口可在原范围内主动补派 0–3 个子 Agent，但必须结构化回传、父窗口读证和质量闸门后才可进入 C06；
- C11 外部 Skill 来源、版本、许可证、权限、替代与缺失降级；
- C12 面向 Boss 的自然语言入口、只看/准备/批准执行边界及含糊批准保护；
- C13 干净安装、升级、失败保全、回退和手工建窗降级；
- C14 中央/任务窗口的原生消息投递、收件确认、指令结果回执与中央换代重路由；
- C01–C14 结构与发布自检脚本。

它现在可以由有权访问私有仓库的用户从本人 GitHub 注册 Marketplace、安装插件并新开 Codex 任务使用。它仍不具备外部系统原生写前锁，不直接修改业务系统，也不会把“安装成功”当作具体业务链路验收成功。

## 已确认的运行规则

| 事项 | 第一版规则 |
|---|---|
| 施工大纲规划 | 整个项目的总设计；Boss + 唯一获授权总设计师，同一版本不得并写 |
| Codex 规划窗口默认模型 | 保留当前选择；获授权新建且未指定时默认 `gpt-5.6-terra` |
| Codex 模块中央模型 | 沿用 Boss 当前选择，可修改；不以模型作为角色身份 |
| 任务窗口 / 一级子 Agent 运行模型 | 默认 `gpt-5.6-terra`，可明确修改；续办保留当前模型，缺真实模型证据仍不能确认运行 |
| 任务窗口 / 一级子 Agent 运行权限 | 默认原生完全访问；Boss 可主动选择原生 `:workspace` 高隔离模式；不创建自定义权限 Profile；实际权限和运行身份必须读回 |
| 一级子 Agent 并发上限 | 每个父任务窗口最多 3 个 |
| 中央启动方式 | 集中确认本期成果和近期任务；同一批准覆盖图中列明的后续波次，远期不强制细拆 |
| `DONE` 后续派 | 中央自动重算并派发批准图内所有新近合格任务，不再问“是否继续” |
| 单窗口任务上限 | 最多 2 项、严格串行；第 2 项完成后退役，禁止第 3 项 |
| 中央与裁定换代 | 逻辑角色不变；交接包 + 私有事件箱续接，旧窗口只读 |
| 中央与任务窗口通信 | C14 原生消息 + 持久信封；`DELIVERED` 不等于收到，必须 `ACKNOWLEDGED` |
| 裁定上下文 | 首次从中央复制，后续留在唯一裁定窗口；中央只收影响摘要 |
| 任务名称 | 总账/任务包：`任务ID｜业务名称`；窗口：`任务ID｜业务名称｜G代际` |
| 新任务项目归属 | 必须绑定 Codex 保存项目；Git 任务默认直接创建标准 worktree。项目编号缺失时按[项目归属证据协议](plugins/codex-module-governance/references/project-association.md)核对真实创建、任务读回和 Git 关系；矛盾即停，不反复重建。历史已准备的 local 引导派发保留兼容 |
| 任务窗口业务边界 | 完全访问不扩大任务包授权；任务仍受对象占用、危险操作审批和改前改后验收约束；选择受限模式时才强制精确写根和治理目录 `DENIED` |
| 仓库策略 | 私有；不发布真实业务数据或用户总账 |
| 当前写前拦截能力 | 未实现；不得宣称已存在 |

## 快速检查

开发验证环境先安装 `requirements-dev.txt`；正式检查必须执行真实 JSON Schema 验证，缺依赖不能跳过。新增模型行为测试通过现有命令入口运行，不能只检查 Skill 字样。

```bash
./scripts/verify-c13.sh
python3 -m unittest discover -s plugins/codex-module-governance/scripts -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_schema_contracts.py'
```

逐站脚本已串联，不必把 C03–C12 再逐个重跑；全量单测另覆盖新增场景和 C14。通过不等于生产业务验收；真实旧包的隔离升级回退须为 C13 smoke 提供 `--previous-source`，未提供时相应结果为 false，不以总标题 PASS 替代。

本轮三项连续性修订的工程状态见 [三项运行问题优化回执](docs/THREE_ISSUE_OPTIMIZATION_REPORT.md)。

自动续派与窗口两任务上限见 [自动续派与窗口生命周期优化回执](docs/AUTO_SUCCESSOR_AND_WINDOW_LIFECYCLE_REPORT.md)。

Codex 原生任务控制与权限优化见 [v0.22 原生运行优化回执](docs/NATIVE_RUNTIME_PERMISSION_OPTIMIZATION_REPORT.md)。

`0.22.1` 的新项目启动修复、兼容边界和验收结果见 [新项目启动修复回执](docs/NEW_PROJECT_STARTUP_REPAIR_REPORT.md)。

## 私有数据边界

不要把下列内容放进本仓库：客户、账务、施工证据、TEST 数据、账号、Cookie、密钥、绝对路径或用户实际工程总账。详见 [私有数据边界](docs/PRIVATE_DATA_BOUNDARY.md)。

## 安装入口

默认使用 GitHub `main` 中的新版 `0.23.0-rc.1`，不再默认安装旧标签 `v0.22.0`。Boss 已批准将候选作为默认使用版本，保留候选版本号及未验证边界，不冒充所有环境均已验收。私有仓库用户必须先获得 GitHub 访问权限；详见[安装说明](docs/INSTALLATION.md)。

```bash
codex plugin marketplace add indonesialuckymore-code/codex-module-governance --ref main
codex plugin add codex-module-governance@qianyi-codex-governance
```

安装或升级后新开 Codex 任务加载新版能力；已有项目通过受控中央交接接续。旧标签仅作历史保留，不建议直接降级。
