# Codex Module Governance

一个私有的 Codex 多任务施工治理产品骨架。它的目标不是替 Boss 或 Fable 5 制定全系统计划，而是让 Codex 模块能够在明确授权下管理项目、任务窗口、证据、冲突和恢复。

## 当前阶段

当前版本是 `0.13.0`，已完成 C01–C13，包括自然语言前台、唯一中央路由器、工程总账、任务包、占用、验收、裁定、恢复、任务调度、外部 Skill 登记与适配，以及私有 GitHub Marketplace 安装、升级和回退验收。

- Codex 插件清单与本仓库 marketplace；
- 私有数据隔离、版本和发布规则；
- 用户配置样例与 schema；
- C02 新项目查重、私有建档与启动卡校验；
- C03 模块任务、窗口、子 Agent、对象占用、证据引用和不可覆盖回执；
- C04 从已规划任务生成的待 Boss 审阅施工任务包；
- C05 经 Boss 审阅后的整包占用预留、冲突硬停和新旧窗口建议；
- C06 施工回传授权、十类证据独立读回、Boss 最终 `DONE` 门槛；
- C07 原窗口阻塞、顾问建议、Boss 决定与原窗口回传边界；
- C08 失联冻结、中央续读、恢复决定和受控占用释放；
- C09 唯一中央路由器和 C02–C11 确定性路由；
- C10 绿黄红派发、真实运行确认、最多 3 个一级子 Agent及父窗口统一汇总；
- C11 外部 Skill 来源、版本、许可证、权限、替代与缺失降级；
- C12 面向 Boss 的自然语言入口、只看/准备/批准执行边界及含糊批准保护；
- C13 干净安装、升级、失败保全、回退和手工建窗降级；
- C01–C13 结构与发布自检脚本。

它现在可以由有权访问私有仓库的用户从本人 GitHub 注册 Marketplace、安装插件并新开 Codex 任务使用。它仍不具备外部系统原生写前锁，不直接修改业务系统，也不会把“安装成功”当作具体业务链路验收成功。

## 已确认的运行规则

| 事项 | 第一版规则 |
|---|---|
| 全系统统筹 | Boss + Fable 5 |
| Codex 模块中央默认模型 | `gpt-5.6-sol` |
| 任务窗口 / 一级子 Agent 默认模型 | `gpt-5.6-terra` |
| 一级子 Agent 并发上限 | 每个父任务窗口最多 3 个 |
| 仓库策略 | 私有；不发布真实业务数据或用户总账 |
| 当前写前拦截能力 | 未实现；不得宣称已存在 |

## 快速检查

```bash
./scripts/verify-c03.sh
./scripts/verify-c04.sh
./scripts/verify-c05.sh
./scripts/verify-c06.sh
./scripts/verify-c07.sh
./scripts/verify-c08.sh
./scripts/verify-c09.sh
./scripts/verify-c10.sh
./scripts/verify-c11.sh
./scripts/verify-c12.sh
./scripts/verify-c13.sh
```

通过代表 C01–C13 产品包、干净安装和回退合同已验证，不代表任何具体业务链路已经验收。

## 私有数据边界

不要把下列内容放进本仓库：客户、账务、施工证据、TEST 数据、账号、Cookie、密钥、绝对路径或用户实际工程总账。详见 [私有数据边界](docs/PRIVATE_DATA_BOUNDARY.md)。

## 安装入口

先按 [安装说明](docs/INSTALLATION.md) 注册本人 GitHub Marketplace，再安装 `codex-module-governance@qianyi-codex-governance`。私有仓库用户必须先获得 GitHub 访问权限；安装或升级后需要新开 Codex 任务加载新版能力。
