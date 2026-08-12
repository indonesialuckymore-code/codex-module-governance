#!/usr/bin/env python3
"""C11 isolated external Skill registry tests."""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPTS=Path(__file__).parent; C02=SCRIPTS/"initialize_project.py"; C03=SCRIPTS/"ledger_manager.py"; C08=SCRIPTS/"disconnection_recovery_controller.py"; C11=SCRIPTS/"external_skill_adapter_controller.py"; PROJECT="c11-demo-project"

def invoke(script,args):
 r=subprocess.run([sys.executable,str(script),*args],capture_output=True,text=True); return r.returncode,json.loads(r.stdout)
def write(path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2)); return path
def setup(root):
 code,out=invoke(C02,["--data-root",str(root),"--project-id",PROJECT,"--display-name","C11 Fictional","--scope-summary","Isolated Skill registry only.","--apply"]); assert code==0,out
 code,out=invoke(C03,["--data-root",str(root),"--project-id",PROJECT,"--writer-id","codex-module-central","initialize","--apply"]); assert code==0,out
def request(skill="fictional-browser",slot="browser",available="AVAILABLE",critical="OPTIONAL",license_status="APPROVED",approved=True,replaces=None,protocol="codex-governance-protocol-v1",pin="v1.2.3",permissions=None):
 return {"requestSchemaVersion":"0.11.0","recordType":"C11_EXTERNAL_SKILL_REQUEST","requestId":f"request-{skill}","projectId":PROJECT,"slotId":slot,"requirement":{"criticality":critical,"capabilityContract":f"contract-{slot}-v1","fallback":f"disable-{slot}"},"candidate":{"skillId":skill,"source":{"type":"GITHUB","locator":f"github:example/{skill}"},"versionPin":{"type":"SEMVER","value":pin},"license":{"status":license_status,"identifier":"MIT","evidenceRef":"license-evidence-001"},"permissions":permissions or ["BROWSER"],"adapter":{"protocol":protocol,"inputContract":"central-task-input-v1","outputContract":"central-evidence-output-v1"},"replacesSkillId":replaces},"availability":{"status":available,"evidenceRef":"availability-evidence-001"},"bossAuthorization":{"status":"APPROVED" if approved else "PENDING","reference":"boss-skill-c11" if approved else None}}
def command(root,cmd,value=None,slot=None):
 args=["--data-root",str(root),"--project-id",PROJECT,"--writer-id","codex-module-central",cmd]
 if value is not None: args += ["--request",str(write(root/"c11-inputs"/f"{cmd}.json",value))]
 if slot: args += ["--slot-id",slot]
 return invoke(C11,args)

class C11Tests(unittest.TestCase):
 def test_assess_ready_never_installs(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); code,out=command(root,"assess",request()); self.assertEqual(code,0,out); self.assertEqual(out["status"],"READY_FOR_ACTIVATION"); self.assertFalse(out["installationPerformed"])
 def test_license_pending_blocks(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); _,out=command(root,"assess",request(license_status="PENDING")); self.assertEqual(out["status"],"BLOCKED_LICENSE_REVIEW")
 def test_optional_missing_disables_only_feature(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); _,out=command(root,"assess",request(available="MISSING")); self.assertEqual(out["status"],"DISABLED_OPTIONAL"); self.assertEqual(out["nextAction"],"disable-browser")
 def test_required_missing_hard_stops_dependency(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); _,out=command(root,"assess",request(available="MISSING",critical="CORE_REQUIRED")); self.assertEqual(out["status"],"BLOCKED_REQUIRED_CAPABILITY")
 def test_protocol_change_forbidden(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); code,out=command(root,"assess",request(protocol="other-protocol")); self.assertEqual(code,2); self.assertEqual(out["reason"],"C11_CENTRAL_PROTOCOL_CHANGE_FORBIDDEN")
 def test_unpinned_version_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); value=request(); value["candidate"]["versionPin"]["value"]=""; code,out=command(root,"assess",value); self.assertEqual(code,2)
 def test_unknown_permission_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); code,out=command(root,"assess",request(permissions=["ROOT_ACCESS"])); self.assertEqual(code,2); self.assertEqual(out["reason"],"C11_PERMISSION_SCOPE_INVALID")
 def test_activation_requires_boss(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); code,out=command(root,"activate",request(approved=False)); self.assertEqual(code,2); self.assertEqual(out["reason"],"C11_EXPLICIT_BOSS_ACTIVATION_APPROVAL_REQUIRED")
 def test_activation_updates_private_registry_and_c03_reference(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); code,out=command(root,"activate",request()); self.assertEqual(code,0,out); self.assertFalse(out["installationPerformed"]); ledger=json.loads((root/"module-ledgers"/PROJECT/"ledger.json").read_text()); self.assertEqual(ledger["skills"]["browser"]["skillId"],"fictional-browser")
 def test_resolve_preserves_all_central_protocols(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); command(root,"activate",request()); _,out=command(root,"resolve",slot="browser"); self.assertEqual(out["status"],"EXTERNAL_SKILL_RESOLVED"); self.assertTrue(all(v is False for v in out["boundary"].values()))
 def test_replacement_requires_explicit_relation(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); command(root,"activate",request()); code,out=command(root,"activate",request(skill="fictional-browser-v2",pin="v2.0.0")); self.assertEqual(code,2); self.assertEqual(out["reason"],"C11_REPLACEMENT_RELATION_REQUIRED")
 def test_explicit_replacement_succeeds_and_is_idempotent(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); command(root,"activate",request()); value=request(skill="fictional-browser-v2",pin="v2.0.0",replaces="fictional-browser"); code,out=command(root,"activate",value); self.assertEqual(code,0,out); self.assertEqual(out["replacedSkillId"],"fictional-browser"); code,out=command(root,"activate",value); self.assertEqual(out["status"],"IDEMPOTENT_ACTIVE_SKILL")
 def test_registry_receipt_tampering_detected(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); _,out=command(root,"activate",request()); receipt=root/"external-skill-registry"/PROJECT/"receipts"/f"{out['registryReceiptId']}.json"; data=json.loads(receipt.read_text()); data["afterRegistryDigest"]="bad"; receipt.write_text(json.dumps(data)); code,out=command(root,"verify"); self.assertEqual(code,2); self.assertEqual(out["reason"],"C11_RECEIPT_CHAIN_INVALID")
 def test_unregistered_slot_returns_safe_fallback(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/"private"; setup(root); _,out=command(root,"resolve",slot="email"); self.assertEqual(out["status"],"SKILL_SLOT_NOT_REGISTERED"); self.assertFalse(out["writePerformed"])

if __name__=="__main__": unittest.main(verbosity=2)
