#!/usr/bin/env python3
"""C12 natural-language gateway. It translates Boss requests and delegates routing to C09."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from central_construction_controller import CentralRoutingError, load_registry, route as central_route
from initialize_project import C02Error, PROJECT_ID_PATTERN, load_data_root
from ledger_manager import LedgerError, load_ledger


SCHEMA_VERSION = "0.12.0"
CENTRAL_REQUEST_VERSION = "0.12.0"
MAX_UTTERANCE_LENGTH = 1200
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|secret|cookie|password|passwd|authorization\s*:|"
    r"密钥|密码|令牌|验证码)"
)

CONTEXT_KEYS = ("taskId", "packageId", "validationId", "recoveryCaseId", "windowId")
CONTEXT_REQUIREMENTS = {
    "GENERATE_TASK_PACKAGE": "taskId",
    "REQUEST_TASK_CANCELLATION": "taskId",
    "CHECK_OCCUPANCY": "packageId",
    "VALIDATE_HANDBACK": "packageId",
    "FINALIZE_TASK": "validationId",
    "REQUEST_ADVISORY": "taskId",
    "RECORD_BOSS_ADJUDICATION": "taskId",
    "TAKEOVER_CONTROL": "recoveryCaseId",
    "RECORD_RECOVERY_DECISION": "recoveryCaseId",
    "RELEASE_OCCUPANCY": "recoveryCaseId",
    "DISPATCH_TASK_WINDOW": "packageId",
    "DISPATCH_SUB_AGENT": "windowId",
}

# Put specific intents before broad intents. A request matching multiple independent
# intents is deliberately returned to Boss instead of being guessed.
INTENT_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("FINALIZE_TASK", (r"(?:最终批准|批准|确认).*(?:done|完成)", r"(?:done|完成).*(?:最终批准|确认)")),
    ("DISPATCH_SUB_AGENT", (r"(?:派|开|创建|启动).*(?:子\s*agent|子智能体|子代理)",)),
    ("DISPATCH_TASK_WINDOW", (r"批准派发", r"派发任务", r"开始施工", r"(?:开|创建|启动).*任务窗口")),
    ("GENERATE_TASK_PACKAGE", (r"(?:生成|准备|出|制作).*任务包", r"任务包.*(?:生成|准备|制作)")),
    ("CHECK_OCCUPANCY", (r"(?:检查|判断|核对).*(?:占用|冲突)", r"(?:新开|沿用|复用).*窗口", r"窗口.*(?:新开|沿用|复用)")),
    ("VALIDATE_HANDBACK", (r"验收.*回传", r"回传.*验收", r"独立复核", r"任务完成了", r"完成信号")),
    ("RECORD_BOSS_ADJUDICATION", (r"(?:记录|发送|返回).*(?:我的决定|裁定结果)", r"(?:我的决定|裁定结果).*(?:任务窗口|原窗口)")),
    ("REQUEST_ADVISORY", (r"中央后台", r"顾问意见", r"不能裁定", r"无法裁定", r"需要建议")),
    ("RELEASE_OCCUPANCY", (r"释放.*占用", r"占用.*释放")),
    ("TAKEOVER_CONTROL", (r"接管.*(?:中央|控制|现场)", r"中央.*接管")),
    ("RECORD_RECOVERY_DECISION", (r"保持冻结", r"准备恢复")),
    ("FREEZE_DISCONNECTION", (r"失联", r"断联", r"冻结.*(?:窗口|任务|现场|中央)")),
    ("RESUME_BUSINESS_EXECUTION", (r"恢复施工", r"继续施工", r"恢复业务执行")),
    ("REGISTER_EXTERNAL_SKILL", (r"(?:固定|登记|替换|启用).*(?:skill|技能)", r"(?:skill|技能).*(?:固定|登记|替换|启用)")),
    ("RESOLVE_EXTERNAL_SKILL", (r"(?:缺失|缺少|检查|解析).*(?:skill|技能).*(?:可用|替代|降级)?", r"(?:skill|技能).*(?:缺失|缺少|不可用)")),
    ("INITIALIZE_PROJECT", (r"(?:新建|创建|启动).*项目", r"项目.*建档", r"新项目")),
    ("INITIALIZE_LEDGER", (r"(?:初始化|建立|创建).*(?:工程总账|项目总账|详细总账)", r"(?:工程总账|项目总账|详细总账).*初始化")),
    ("REQUEST_TASK_CANCELLATION", (r"取消任务", r"撤销任务", r"停止这个任务")),
    ("REGISTER_GOVERNANCE_STATE", (r"登记任务", r"更新总账", r"登记状态", r"记录.*完成信号")),
    ("READ_LEDGER", (r"(?:查看|读取|检查|打开).*(?:总账|总盘|进度|状态)", r"(?:下一步|下一个任务|候选任务|当前任务)", r"(?:总账|总盘|进度).*怎么样")),
]

READ_ONLY_MARKERS = ("只看", "仅查看", "不要执行", "先别执行", "不操作", "read only", "read-only")
APPROVAL_MARKERS = ("批准", "同意执行", "确认执行", "可以执行", "apply")
PLAIN_APPROVAL_MARKERS = {"批准", "同意", "可以", "ok", "好的"}
TERMINAL_TASK_STATUSES = {"DONE", "CANCELLED"}


class GatewayError(Exception):
    """Safe refusal without downstream invocation."""


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def require_reference(value: Optional[str], label: str) -> Optional[str]:
    if value is None:
        return None
    candidate = value.strip()
    if not REFERENCE_PATTERN.fullmatch(candidate):
        raise GatewayError(f"C12_{label}_INVALID")
    return candidate


def identify_intents(text: str) -> List[str]:
    matches: List[str] = []
    for intent, patterns in INTENT_PATTERNS:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            matches.append(intent)
    # "生成任务包，不要派发" describes the C04 boundary; it is not a second dispatch intent.
    if "GENERATE_TASK_PACKAGE" in matches and "DISPATCH_TASK_WINDOW" in matches and re.search(r"(?:不要|不).*派发", text):
        matches.remove("DISPATCH_TASK_WINDOW")
    # A completion signal is a C06 handback intent, not a generic C03 state update.
    if "VALIDATE_HANDBACK" in matches and "REGISTER_GOVERNANCE_STATE" in matches:
        matches.remove("REGISTER_GOVERNANCE_STATE")
    return matches


def select_mode(intent: str, text: str) -> Tuple[Optional[str], Optional[str]]:
    read_only = any(marker in text for marker in READ_ONLY_MARKERS)
    approved = any(marker in text for marker in APPROVAL_MARKERS)
    if read_only and approved:
        return None, "C12_READ_ONLY_AND_APPROVAL_CONFLICT"
    if intent in {"READ_LEDGER", "RESOLVE_EXTERNAL_SKILL"}:
        return "READ_ONLY", None
    if intent in {"FREEZE_DISCONNECTION", "VALIDATE_HANDBACK"}:
        return "APPLY", None
    if read_only:
        return "READ_ONLY", None
    if approved:
        return "APPLY", None
    return "PREPARE", None


def load_project_index(data_root: Path) -> List[Dict[str, Any]]:
    path = data_root / "project-registry" / "projects-index.json"
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise GatewayError("C12_PROJECT_INDEX_INVALID")
    if value.get("recordType") != "C02_PROJECT_STARTUP_INDEX" or not isinstance(value.get("projects"), list):
        raise GatewayError("C12_PROJECT_INDEX_INVALID")
    return value["projects"]


def resolve_project(data_root: Path, explicit: Optional[str], intent: str) -> Tuple[Optional[str], List[str]]:
    if explicit:
        candidate = explicit.strip()
        if not PROJECT_ID_PATTERN.fullmatch(candidate):
            raise GatewayError("C12_PROJECT_ID_INVALID")
        return candidate, []
    if intent == "INITIALIZE_PROJECT":
        return None, ["请告诉我新项目的项目编号；编号使用小写字母、数字和连字符。"]
    projects = load_project_index(data_root)
    if len(projects) == 1 and isinstance(projects[0], dict):
        candidate = projects[0].get("projectId")
        if isinstance(candidate, str) and PROJECT_ID_PATTERN.fullmatch(candidate):
            return candidate, []
    if not projects:
        return None, ["目前没有可识别的项目。请先说“启动新项目”，并提供项目编号。"]
    return None, ["当前有多个项目。请告诉我要处理的项目编号。"]


def candidate_directories(data_root: Path, project_id: str, category: str) -> List[str]:
    roots = {
        "packageId": data_root / "task-packages" / project_id / "drafts",
        "validationId": data_root / "handover-validations" / project_id,
        "recoveryCaseId": data_root / "recovery-cases" / project_id,
    }
    root = roots[category]
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and REFERENCE_PATTERN.fullmatch(path.name))


def infer_context(data_root: Path, project_id: str, intent: str, supplied: Dict[str, Optional[str]]) -> Tuple[Dict[str, Optional[str]], List[str]]:
    context = {key: require_reference(supplied.get(key), key.upper()) for key in CONTEXT_KEYS}
    inferred: List[str] = []
    required = CONTEXT_REQUIREMENTS.get(intent)
    if required is None or context[required] is not None:
        return context, inferred
    try:
        ledger = load_ledger(data_root, project_id)
    except LedgerError:
        ledger = None
    candidates: List[str] = []
    if required == "taskId" and ledger:
        tasks = ledger.get("tasks", {})
        if intent == "GENERATE_TASK_PACKAGE":
            candidates = sorted(task_id for task_id, task in tasks.items() if task.get("status") == "PLANNED")
        elif intent == "REQUEST_TASK_CANCELLATION":
            candidates = sorted(task_id for task_id, task in tasks.items() if task.get("status") not in TERMINAL_TASK_STATUSES)
        else:
            candidates = sorted(task_id for task_id, task in tasks.items() if task.get("status") not in TERMINAL_TASK_STATUSES)
    elif required == "windowId" and ledger:
        candidates = sorted(window_id for window_id, window in ledger.get("windows", {}).items() if window.get("status") not in {"CLOSED", "CANCELLED"})
    elif required == "recoveryCaseId" and ledger:
        active = ledger.get("recovery", {}).get("activeCaseId")
        if isinstance(active, str) and REFERENCE_PATTERN.fullmatch(active):
            candidates = [active]
        else:
            candidates = candidate_directories(data_root, project_id, required)
    else:
        candidates = candidate_directories(data_root, project_id, required)
    if len(candidates) == 1:
        context[required] = candidates[0]
        inferred.append(required)
    return context, inferred


def question_for(reference: str) -> str:
    names = {
        "taskId": "任务编号",
        "packageId": "任务包编号",
        "validationId": "验收编号",
        "recoveryCaseId": "恢复事件编号",
        "windowId": "任务窗口编号",
    }
    return f"请告诉我要处理的{names[reference]}。"


def public_base(text_digest: str) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C12_NATURAL_LANGUAGE_GATEWAY_DECISION",
        "utteranceDigest": text_digest,
        "boundaries": {
            "rawUtteranceStored": False,
            "gatewayStateStored": False,
            "ledgerWritePerformed": False,
            "downstreamExecutionPerformed": False,
            "businessWritePerformed": False,
            "dispatchPerformed": False,
            "c09RemainsSoleCentral": True,
        },
    }


def needs_input(base: Dict[str, Any], questions: List[str], reason: str, intent: Optional[str] = None) -> Dict[str, Any]:
    return {
        **base,
        "status": "NEEDS_BOSS_INPUT",
        "reason": reason,
        "intent": intent,
        "questions": questions,
        "routingRequest": None,
        "routingDecision": None,
    }


def interpret(data_root: Path, utterance: str, args: argparse.Namespace) -> Dict[str, Any]:
    text = normalize(utterance)
    if not text or len(text) > MAX_UTTERANCE_LENGTH:
        raise GatewayError("C12_UTTERANCE_INVALID")
    text_digest = digest_text(text)
    base = public_base(text_digest)
    if SENSITIVE_PATTERN.search(text):
        return needs_input(base, ["请删除密码、密钥、Cookie、令牌或验证码后，只描述业务动作。"], "C12_SENSITIVE_DETAIL_DETECTED")
    intents = identify_intents(text)
    if not intents:
        if text in PLAIN_APPROVAL_MARKERS:
            return needs_input(base, ["请说明批准的是哪个项目、任务或待执行动作。"], "C12_APPROVAL_TARGET_AMBIGUOUS")
        return needs_input(base, ["请说明您要查看、准备还是批准执行哪一项治理动作。"], "C12_INTENT_NOT_CLEAR")
    unique_intents = list(dict.fromkeys(intents))
    if len(unique_intents) != 1:
        return needs_input(base, ["这句话包含多个动作，请一次只指定一个动作。"], "C12_MULTIPLE_INTENTS_DETECTED")
    intent = unique_intents[0]
    mode, mode_error = select_mode(intent, text)
    if mode_error:
        return needs_input(base, ["请确认这次是只查看，还是批准执行。"], mode_error, intent)
    project_id, project_questions = resolve_project(data_root, args.project_id, intent)
    if project_questions:
        return needs_input(base, project_questions, "C12_PROJECT_CONTEXT_REQUIRED", intent)
    assert project_id is not None and mode is not None
    if intent in {"FREEZE_DISCONNECTION", "VALIDATE_HANDBACK"}:
        mode = "APPLY"
    supplied = {
        "taskId": args.task_id,
        "packageId": args.package_id,
        "validationId": args.validation_id,
        "recoveryCaseId": args.recovery_case_id,
        "windowId": args.window_id,
    }
    context, inferred = infer_context(data_root, project_id, intent, supplied)
    required = CONTEXT_REQUIREMENTS.get(intent)
    if required and context[required] is None:
        return needs_input(base, [question_for(required)], f"C12_{required.upper()}_REQUIRED", intent)
    approval_ref = require_reference(args.authorization_ref, "AUTHORIZATION_REFERENCE")
    if mode == "APPLY" and approval_ref is None:
        approval_ref = f"boss-c12-{text_digest[:16]}"
    authorization_status = "APPROVED" if mode == "APPLY" else ("PENDING" if mode == "PREPARE" else "NOT_REQUIRED")
    routing_request = {
        "requestSchemaVersion": CENTRAL_REQUEST_VERSION,
        "recordType": "C09_CENTRAL_ROUTING_REQUEST",
        "requestId": f"c12-{text_digest[:24]}",
        "submittedBy": "boss",
        "projectId": project_id,
        "intent": intent,
        "executionMode": mode,
        "bossAuthorization": {"status": authorization_status, "reference": approval_ref},
        "contextRefs": context,
    }
    try:
        routing_decision = central_route(data_root, routing_request, load_registry())
    except (CentralRoutingError, LedgerError) as error:
        return {
            **base,
            "status": "REFUSED",
            "reason": str(error),
            "intent": intent,
            "executionMode": mode,
            "projectId": project_id,
            "questions": [],
            "inferredContextRefs": inferred,
            "routingRequest": routing_request,
            "routingDecision": None,
        }
    return {
        **base,
        "status": "ROUTED_TO_SOLE_CENTRAL",
        "reason": None,
        "intent": intent,
        "executionMode": mode,
        "projectId": project_id,
        "questions": [],
        "inferredContextRefs": inferred,
        "routingRequest": routing_request,
        "routingDecision": routing_decision,
    }


def examples() -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C12_BOSS_LANGUAGE_EXAMPLES",
        "examples": [
            {"say": "查看当前总账和下一个候选任务", "effect": "只读，不施工"},
            {"say": "生成任务包，不要派发", "effect": "先准备，等待批准"},
            {"say": "批准派发这个任务包", "effect": "转 C10，仍须通过下游门禁"},
            {"say": "这个问题我不能裁定，请中央后台给意见", "effect": "建议只回 Boss"},
            {"say": "窗口失联了，先冻结现场", "effect": "转 C08，不自动重派"},
        ],
        "boundaries": {"writePerformed": False, "businessWritePerformed": False},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C12 Boss natural-language gateway")
    root = parser.add_mutually_exclusive_group(required=True)
    root.add_argument("--data-root")
    root.add_argument("--config")
    commands = parser.add_subparsers(dest="command", required=True)
    interpret_parser = commands.add_parser("interpret")
    interpret_parser.add_argument("--text", required=True)
    interpret_parser.add_argument("--project-id")
    interpret_parser.add_argument("--task-id")
    interpret_parser.add_argument("--package-id")
    interpret_parser.add_argument("--validation-id")
    interpret_parser.add_argument("--recovery-case-id")
    interpret_parser.add_argument("--window-id")
    interpret_parser.add_argument("--authorization-ref")
    commands.add_parser("examples")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data_root = load_data_root(args)
        output = examples() if args.command == "examples" else interpret(data_root, args.text, args)
        print_result(output)
        return 0 if output.get("status") != "REFUSED" else 2
    except (C02Error, GatewayError, ValueError) as error:
        print_result({
            "status": "REFUSED",
            "reason": str(error),
            "writePerformed": False,
            "businessWritePerformed": False,
            "dispatchPerformed": False,
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
