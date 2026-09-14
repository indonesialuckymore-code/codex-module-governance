# 模型选择与真实运行记录

模型是执行选择，不是中央或任务身份。平台决定可用模型；插件不维护模型白名单，不因正常换模型撤销任务、批准或对象占用。

- 中央沿用 Boss 当前选择。C08 初始化可传 `--model` 记录实际读回；未提供时记为 `UNVERIFIED`，不伪称 Sol。
- 当前中央切换模型后，运行 `role_continuity_controller.py record-model --current-thread-ref <当前中央ID> --runtime-project-id <已登记项目ID> --model <实际模型> --evidence-ref <当前中央ID>` 更新观察。命令不切换代际、不修改业务任务或批准；其他窗口不得冒用。观察只在模型发生变化或缺少证据时更新。
- 新任务未指定模型时默认 `gpt-5.6-terra`；明确指定时在 C10 请求顶层传 `model`。一级子 Agent 可在各自规格中传 `model`，缺省同样默认 Terra。原生调用必须遵守当前平台的模型参数和委派限制。
- 复用任务未要求切换时，原生 `send_message_to_thread` 省略 `model` 和其他未要求修改的运行设置。C10 的 `requiredModel=null`、`preserveCurrentModel=true` 表示保留，不能把 null 作为显式模型参数发送。随后读回实际模型并确认。
- 中央/裁定 fork 换窗不等于原窗口续办：平台可能为继任窗口使用默认模型。准备交接前实际读回源窗口模型，必要时 record-model 更新观察；prepare-successor 返回 successorFirstTurn，第一条发给真实继任 ID 的原生消息显式传入其中 model，再补交接包引用与只读核验要求。Boss 明确为继任者另选模型时可传 --model；没有变更要求不得自选。requiresSourceModelReadback=true 时先补真实读回，不发送无模型的首轮消息、不假填 Terra。后续续办省略 model，不能在用户后来换模型后再次覆盖。真实运行模型仍须读回，动作参数不等于生效证据。
- 模型回执记录真实模型、方法和运行对象引用。`RUNTIME_MODEL_READBACK` 绑定真实任务 ID；原生指定模型的回执也须绑定对应任务/Agent。不能仅凭提示词或模型自报。
- 用户在派发准备后主动换模型时，接受可信运行读回中的实际选择，不因与默认值不同拒绝。任务身份、项目、权限及占用仍分别核验。
- 手工建窗是平台不可用时的降级，仍需先生成 C10 fallback 包。采用 `MANUAL_UI_MODEL_SELECTION_EVIDENCE`；旧 `MANUAL_UI_TERRA_SELECTION_EVIDENCE` 只兼容历史真实 Terra 证据，不可拿它证明其他模型。
- C03 手工登记非默认模型需同时传 `--runtime-model` 与 `--runtime-model-evidence-ref`。缺运行证据不得伪写成已确认模型；既有历史回执不覆盖。

默认完全访问与模型选择独立。`disabled` 仅作为平台明确返回的“权限限制关闭”别名，对应 `FULL_ACCESS`、空写根和 `NOT_RESTRICTED`；不能把未知权限归为完全访问，不创建自定义 Profile。
