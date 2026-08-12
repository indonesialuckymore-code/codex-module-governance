---
name: external-skill-adapter-controller
description: 登记和审查 Codex 模块使用的外部可选 Skill，固定来源与版本、检查许可证和权限、管理替代关系，并在缺失或不兼容时输出安全降级；不自动安装或调用 Skill，也不允许替换改变中央任务、状态、证据和授权协议。Use when Boss 要固定外部 Skill、寻找替代 Skill、审查来源/许可证/权限、处理 Skill 缺失或切换版本。
---

# 外部 Skill 登记与适配

1. 先记录能力槽和输入输出合同，不先安装候选 Skill。
2. 必须登记来源、固定版本或 commit、许可证证据、权限、替代关系和降级方案。
3. 核心能力缺失或不兼容时阻断依赖施工；可选能力缺失时只关闭该功能。
4. 同一能力槽只有一个 `ACTIVE` Skill；替换必须显式指向旧 Skill。
5. 适配协议固定为 `codex-governance-protocol-v1`。外部 Skill 不得改变中央任务、状态机、证据合同和 Boss 授权。
6. C11 只登记和解析，不自动安装、不自动调用；安装动作必须另获 Boss 批准，并遵守 C13 已验证的隔离安装、版本固定和回退合同。
