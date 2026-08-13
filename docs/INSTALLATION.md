# 安装说明

## Boss 实际怎么安装

前提：电脑已安装 Codex CLI，GitHub 账号已获准访问私有仓库 `indonesialuckymore-code/codex-module-governance`。

```bash
codex plugin marketplace add indonesialuckymore-code/codex-module-governance --ref v0.19.0
codex plugin add codex-module-governance@qianyi-codex-governance
```

安装后新开一个 Codex 任务，说：

> 初始化 Codex 模块施工控制台。先检查依赖和重复建设，不要施工。

也可以显式调用 `$codex-governance-gateway`。插件先让用户选择仓库外私有数据目录；不会把工程总账或业务资料写进 GitHub 仓库。

## 本人本地试用后续开发版

试用尚未发布正式标签的后续开发版时，可以直接从本地产品仓库安装，不需要先上传 GitHub：

```bash
codex plugin marketplace add <本地仓库路径>
codex plugin add codex-module-governance@qianyi-codex-governance
```

安装后必须新开 Codex 任务，再从 `/` 菜单选择“施工大纲规划”或“中央处理器”。把冻结大纲交给中央后，中央应一次显示全部任务、待拍板事项、并行波次和前置关系；若仍逐任务反复询问，说明加载的不是当前施工版。正式分享给其他用户时仍应固定 Git tag。

## 升级

新稳定版发布后，先移除当前程序插件和旧 Marketplace 引用，再按新版本标签重新注册并安装。这个操作只处理程序缓存，不处理仓库外私有总账：

```bash
codex plugin remove codex-module-governance@qianyi-codex-governance
codex plugin marketplace remove qianyi-codex-governance
codex plugin marketplace add indonesialuckymore-code/codex-module-governance --ref <新版本标签>
codex plugin add codex-module-governance@qianyi-codex-governance
```

升级前先保留当前可用版本的 Git commit 或 tag；升级后新开任务测试。程序包和用户私有数据分离，升级不迁移、不覆盖工程总账。`scripts/c13-release-validator.py` 可在隔离目录验证安装、备份、升级、失败保全和回退。

## 回退

推荐按升级步骤把 Marketplace 固定回 `v0.16.0`（上一稳定版）或其他已知可用 tag 后重新安装。若使用 C13 隔离验证器留下的备份，则运行：

```bash
python3 scripts/c13-release-validator.py rollback \
  --installed-plugin <隔离安装目录>/codex-module-governance \
  --backup <备份目录>/codex-module-governance-<旧版本>
```

回退程序不删除、不回退用户工程总账；如新版本改变私有配置 schema，另按版本文档恢复上一份配置副本。

## 初始化和业务边界

1. 把 `config/module-config.example.json` 复制到**仓库外、未被 Git 管理**的私有位置，并填写 `storage.userDataRoot`。
2. 在 Codex 中使用“启动新项目建档”调用；系统先做只读查重。
3. 只有 Boss 明确批准后，才会在该私有目录创建项目启动卡。
4. C03 再从已存在的启动卡建立 Codex 模块详细账本；只有 `codex-module-central` 可写，所有变更都生成不可覆盖回执。
5. C04 只从 C03 的 `PLANNED` 任务生成待 Boss 审阅的私有任务包；生成不等于派发，C05 之前不能创建窗口或占用对象。
6. Boss 审阅后，C05 读取去敏对象范围：冲突任务进入 `BLOCKED`，安全任务预留全部对象并进入 `READY`；两者都不会实际创建窗口或 Agent。
7. 已有任务窗口回传完成信号后，C06 仅在 Boss 允许回传、中央十类证据独立读回且 Boss 最终批准时标记 `DONE`；占用保持不释放。
8. 原任务窗口需要顾问意见时，C07 只把建议交 Boss；Boss 决定后才生成回原窗口的裁定包，且不自动执行。
9. 窗口或中央失联时，C08 冻结现场并保留占用；新中央经 Boss 授权后只恢复治理控制，不能自动恢复业务施工。
10. C09 首次接收大纲时一次生成中央启动图；Boss 集中回答决策并批准后，图中相同范围不再逐任务重复批准。
11. C10 按启动图波次和绿黄红门禁准备派发单；新任务必须绑定 Codex 保存项目，Git 默认标准 worktree。若 worktree 回读项目 ID 为空，对同一任务先交接到项目根目录再交接回来；只有最终回读正确项目归属、标准目录和真实任务 ID 后，才登记 C03 并进入 `IN_PROGRESS`。
12. C11 登记外部 Skill 的固定来源、版本、许可证和权限；不自动安装。
13. C12 接收 Boss 业务语言，区分只看、准备和批准执行，再把标准请求交给 C09 唯一中央。
14. 平台不能自动建窗时，C10 `export-fallback` 只生成可复制任务包；在真实任务 ID 回传前，任务不会被误记为 `IN_PROGRESS`。

项目启动卡不包含任务、窗口、证据或业务数据。C03 总账只保存私有的结构化施工状态和不透明引用；C04 任务包和 C05 占用请求只保存去敏施工合同、对象键及交接引用，不包含真实业务对象。安装完成只说明治理产品可加载，不等于任何真实业务链已上线或验收。
