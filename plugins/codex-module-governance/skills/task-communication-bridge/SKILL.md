---
name: task-communication-bridge
description: 在 Codex 唯一中央与已登记任务窗口之间，以持久信封、原生任务消息、投递回执和收件确认完成双向通信。用于“中央消息没有送到任务窗口”“任务回传中央没有收到”“避免假回传”“重试中央或任务窗口通信”“确认中央/任务窗口是否真的收到指令或回传”等请求；不维护第二套任务状态、不传施工原文、不代替 C06 验收或 Boss 最终批准。
---

# 任务双向通信桥

只把“谁需要处理哪个私有票据”送到正确 Codex 任务；详细任务状态仍只在 C03，回传验收仍只走 C06 + Boss。

## 两条固定链

```text
任务窗口 -> C08 回传票据/事件 -> C14 信封 -> 原生 send_message_to_thread -> 中央确认 -> C08/C06
中央 -> C14 指令信封 -> 原生 send_message_to_thread -> 任务窗口确认 -> 执行结果信封 -> 中央确认
```

消息正文只能包含 `messageId`、项目和摘要哈希；施工原文、客户数据、链接、密钥、Cookie、绝对路径均留在私有材料，不发给中央聊天。

## 任务窗口回传中央

1. 先按 C06/C08 封存回传并执行 `submit-return`。没有 `TASK_EVENT_QUEUED`、票据、事件和摘要哈希，先报告 `LOCAL_REPLY_ONLY / OUTBOX_PENDING`。
2. 在私有目录写 `C14_TASK_TO_CENTRAL_REQUEST`，再运行 `enqueue-task-to-central`。它会验证 C08 的回传票据、事件、任务身份、窗口代际和逻辑中央路由。
3. 运行 `prepare-delivery`。仅当返回 `READY_FOR_RUNTIME_MESSAGE_DELIVERY` 时，调用 Codex 原生 `send_message_to_thread`，目标必须使用返回的 `targetThreadRef`，正文必须原样使用 `outboundPrompt`。
4. 原生发送成功后，把工具结果的私有摘要写成 `C14_RUNTIME_DELIVERY_RECEIPT`，运行 `record-delivery`。得到 `RUNTIME_MESSAGE_DELIVERED_AWAITING_ACKNOWLEDGEMENT` 前，不得说“中央收到”。
5. 中央收到提醒后运行 `list-incoming`、读信封并 `acknowledge`。只有 `MESSAGE_ACKNOWLEDGED` 才能称为“中央已收到票据”；中央随后只按 C08/C06 处理摘要，不读取施工原文。

## 中央下达任务窗口指令

任务有[外部等待](../../references/task-followthrough.md)时，C14 普通施工指令暂停投递，包括已有积压；纠偏和取消通知仍按原协议核验。不要把等待登记当成原生中断证明，也不要把反馈登记当成已通知修复窗口。

遇到 `DIRECTION_CORRECTION`，先读[纠偏候选协议](../../references/task-corrections.md)。使用 C03 生成的当前版本请求；不得将被取代的纠偏改编号重发或静默转给新代际。APPLIED 与结果消息确认仍不解锁任务，须由当前中央按证据核验。解除暂停后，旧版本积压施工消息仍不能发送；新消息绑定当前版本。验证范围见[候选版状态](../../references/release-status.md)。

1. 中央必须仍是 `CURRENT_CENTRAL`，且只向 C03 已登记、当前承接该任务的窗口发送。
2. 写 `C14_CENTRAL_TO_TASK_REQUEST`；指令正文保留在私有 `commandRef`，信封只保留编号、摘要哈希和任务身份。运行 `enqueue-central-command`。
3. 中央依次运行 `prepare-delivery` -> 原生 `send_message_to_thread` -> `record-delivery`。`DELIVERED` 仅代表平台已接受发送，不代表窗口执行。
4. 任务窗口先 `acknowledge`，读私有 `commandRef` 后施工。它不能把指令确认当成任务完成。
5. 任务窗口以 `record-command-result` 写 `APPLIED`、`REFUSED` 或 `BLOCKED`，并对返回的 `resultNotificationMessageId` 重复“准备 -> 原生发送 -> 回执”；中央确认结果消息。涉及任务状态、阻塞或正式交付的事项仍需另走 C08/C06。

## 重试、换代与假回传

- 原生发送失败：记录 `FAILED` 投递回执，消息保持 `QUEUED`；重新 `prepare-delivery`，它会解析最新 `CURRENT_CENTRAL`，不向旧中央盲发。
- 发送成功但登记调用中断：用同一份原生投递回执重试 `record-delivery`，已登记则零写入返回；不要为了补登记再次调用原生发送。同一编号、不同内容会拒绝覆盖。
- 执行结果已封存但通知写入中断：用原来的结果请求重试 `record-command-result`，补齐缺失的通知信封。已有完整通知不重建；只有封存回执时，核验后按回执恢复，不覆盖原结果或回执。冲突、残损证据需保留现场处理，不能重写伪造成功。
- 恢复通知只表示重新具备投递条件；仍须 `prepare-delivery` -> 原生发送 -> 投递回执 -> 中央确认。`APPLIED` 是任务窗口报告，不代表中央已经收到，也不代表独立验证了纠偏生效。
- 中央换代：旧中央不能确认；未确认消息由新 `CURRENT_CENTRAL` 确认。同一任务窗口不需要重开。
- 没有 C14 原生投递回执：只能称 `QUEUED`，不能称“已通知”。
- 没有收件方 `MESSAGE_ACKNOWLEDGED`：只能称 `DELIVERED`，不能称“对方已收到”。
- `ACKNOWLEDGED`、`APPLIED` 和 `PASS_PENDING_BOSS_APPROVAL` 都不是 `DONE`。

## 永久边界

- 不创建任务窗口、不改业务系统、不改 C03 任务状态、不派子 Agent。
- 不把 C14 当作第二中央或第二工程总账；它只保存不可覆盖的通信回执。
- 不因一条聊天文字、截图或模型自报而跳过投递/确认回执。
- 收件线程、窗口、任务 ID、代际或路由不匹配时硬停；不得把消息转发给相似名称的任务。
- 模型规则统一引用 `../../references/runtime-model-policy.md`：新任务默认 Terra，可修改；续办保留当前选择，中央模型自由。通信不强制切换模型。
