# 按成果和实际影响验收（候选协议）

适用于新生成的 C04 合同、C05 占用、C10 执行及 C06 独立验收。复用唯一任务和原批准合同，不新增任务分类中心、不要求 Boss 手填检查表。验证范围统一见[候选版状态](release-status.md)。

## 怎么选标准

先问“交付什么才对项目有用”，再从实际动作选择检查；研究、文档、软件、运营只是例子，不是限制项目类型的枚举。

- 共同底线：范围和成果符合批准合同、重要结论有来源、实际交付物可打开/读回、标识和版本准确、风险和未确认事项如实披露、独立读证。大纲中的成果可用标准必须逐项核对，不以文件数量替代。
- 修改任何目标状态（含新建或改写交付文件）：保留改前状态与恢复办法；新建文件的“原先不存在”和可恢复撤回也是真实证据，不要求虚构数据库回滚。
- 改可执行行为：检查正常行为及失败/排除情形；可能重复执行的操作检查重复效果；确实接入上下游时检查相应接口/业务读回。
- 高风险操作保持全项证据底线，具体方法按业务解释；不能把支付、删除、批量覆盖、生产开关等改称“研究”来免验。软件测试不适合的不可逆操作应核对批准、预演、补偿/恢复方案和真实结果，无法满足就报告缺口，不能假装可回滚。
- 非高风险任务不适用的项在制包时说明理由，随同一个任务合同进入原有范围化批准，不另开一次“验收方案审批”。执行后发现影响变化时报告合同偏差，只处理变化范围，不自动重审整个项目。

## 程序合同

C04 brief 可选 `acceptancePolicy`，包含：

```json
{
  "effects": {
    "changesState": false,
    "executableBehavior": false,
    "repeatableOperation": false,
    "upstreamDependency": false,
    "downstreamConsumer": false,
    "highRisk": false
  },
  "notApplicable": {
    "rollback": "Only read existing material; no target state is changed.",
    "idempotency": "No repeatable state-changing operation is in scope."
  }
}
```

六个影响标志必须逐项明确，不能从任务标题或模型推断。`notApplicable` 是可豁免项及非空业务理由；没列出的项仍必验。允许声明比实际最低要求更严格的合同，但不能既写“必须做”又写“不适用”。中央核对影响声明是否与 allowedActions、实际对象和证据相符；程序不能从自由文本自动证明声明真实。

最低检查归口在 `scripts/acceptance_policy.py`：`changesState` 保留 beforeSnapshot/rollback，executableBehavior 保留 negativeCase，repeatableOperation 保留 idempotency，上下游标志分别保留对应 readback，highRisk 保留全部。C05 对“不改状态”却申请 WRITE 的请求拒绝；C10/C06 同时核对 C05 批准的 packageDigest，不能把另一版合同套入旧批准。

历史字段名称保留以兼容传输，不强迫每个项目建设 TEST：

| 保留字段 | 在通用任务中的实际含义 |
| --- | --- |
| afterSnapshot / readbackChecks | 实际交付物、当前结果读回 |
| positiveCase / positiveCases | 成果达到批准可用标准的证据 |
| logsAndHistory / logAndHistoryChecks | 来源、版本与必要过程记录；研究不必制造服务日志 |
| testAndObjectIds / testAndObjectRefs | 实际成果、来源或对象的准确标识；没有 TEST 就不编造 TEST 编号 |

四类共同底线不能 N/A。只有 negativeCases、idempotencyChecks、rollbackChecks 对应项整体已声明 N/A 时，C04 相应数组必须为空；其他要求仍给出实际检查方法。preflightSnapshot/rollbackPlan 等文字区在不适用时写真实理由，不写并未发生的动作。

C10 直接携带同一政策；父窗口汇总子 Agent 的 acceptanceCoverage 对不适用项引用理由及核验依据，不凑软件测试。已有覆盖字段仍保留，表示“逐项处理”，不表示“逐项都执行软件测试”；C06 仍独立判断。

## 验收与兼容

C06 十类 evidenceAssessment 仍逐项返回，允许 `NOT_APPLICABLE`，但只能用于该不可变合同预先列出的项。reference 必须引用回传已声明的核验依据，independentlyReadBack 记录是否实际核对豁免理由；“缺证据”“没时间”不算不适用。FAIL 仍为 PARTIAL，缺失或未独立读证仍为 NEEDS_REVIEW，越界仍为 CONFLICT。

rollbackExecutable=false 仅在合法 rollback N/A 时不单独判失败。新合同验收决定绑定 acceptancePolicyDigest，不使用旧的“缺改前快照仍可最终批准”例外。所有正常通过仍为 PASS_PENDING_BOSS_APPROVAL，C06 + Boss 最终批准才能 DONE。

未携带政策的旧任务仍用原全项要求；不重写历史材料、不替旧任务补豁免。新政策不能证明已运行任务已迁移，真实安装、在途升级回退与效率提升仍需后续验收。
