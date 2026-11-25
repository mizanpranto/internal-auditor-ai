"""Reference demo: hand-written verdicts for the bundled sample documents.

Lets anyone (a recruiter, a reviewer) explore a populated workspace without
installing Ollama. These verdicts are NOT model output. Every quote is checked
against the sample documents by the test-suite, exactly like the live citation lock.
"""

from __future__ import annotations

import shutil
from datetime import datetime

from .config import ROOT
from .schemas import Assessment, Control
from .workspace import Workspace

ISP = "information_security_policy.md"
ACP = "access_control_procedure.md"
IBP = "incident_and_backup_procedure.md"

# control id -> (status, quote, document, reasoning, what is still missing)
DEMO: dict[str, tuple[str, str, str, str, str]] = {
    "5.1": ("Met", "The policy is approved by the Board of Directors, published on the company intranet and communicated to all employees and contractors on joining.", ISP,
            "The policy is board-approved, published and communicated, and the document also states an annual review.", ""),
    "5.2": ("Met", "An ISMS Steering Committee made up of the CISO, the Head of IT, the Head of HR and the Data Protection Officer meets quarterly.", ISP,
            "Security roles are named and a steering committee is set up.", ""),
    "5.4": ("Partially met", "Every department head is accountable for security within their team.", ISP,
            "Management accountability is stated, but nothing shows how management enforces it.", "Management review minutes or directives showing leaders require policy compliance."),
    "5.5": ("Partially met", "Where personal data is affected, the Data Protection Officer decides whether the regulator must be notified within 72 hours.", IBP,
            "Regulator notification is covered for personal data breaches only.", "A contact list of authorities and an escalation procedure for other incident types."),
    "5.9": ("Partially met", "IT maintains a register of laptops, servers and software licences.", ISP,
            "A hardware and licence register exists, but information assets and owners are not covered.", "An information asset inventory with a named owner for each asset."),
    "5.10": ("Met", "Company systems and information may be used only for authorised business purposes.", ISP,
             "Acceptable use is defined and handling rules are referenced.", ""),
    "5.12": ("Met", "Information is classified into four levels: Public, Internal, Confidential and Restricted.", ISP,
             "A four-level classification scheme is defined with handling rules per level.", ""),
    "5.14": ("Partially met", "Confidential and Restricted information must not be stored on personal devices or sent to personal email accounts.", ISP,
             "Restrictions on personal channels are set, but secure transfer is not described.", "Approved transfer methods and agreements for sharing information with third parties."),
    "5.15": ("Met", "Access to systems and data is granted on a need-to-know and least-privilege basis.", ACP,
             "The access principles are stated and backed by a joiner, mover and leaver process.", ""),
    "5.16": ("Met", "When an employee leaves, HR notifies IT on the last working day and all accounts are disabled within 24 hours.", ACP,
             "The identity life cycle is covered, including unique accounts and removal at exit.", ""),
    "5.17": ("Met", "Passwords must be at least 14 characters long and must not be reused across systems.", ACP,
             "Password rules, lockout and a password manager are defined.", ""),
    "5.18": ("Met", "System owners review the list of users and permissions for all business-critical systems every quarter.", ACP,
             "Access is approved before it is granted and reviewed every quarter.", ""),
    "5.19": ("Partially met", "Suppliers that process Confidential or Restricted data must complete a security questionnaire before a contract is signed.", ISP,
             "Supplier screening at onboarding exists, but nothing covers ongoing supplier risk.", "A supplier register, risk tiers and periodic supplier reviews."),
    "5.24": ("Partially met", "Reports are logged in the incident register.", IBP,
             "Reporting and logging exist, but incident management planning is thin.", "Defined incident roles, an on-call rota and response playbooks."),
    "5.25": ("Met", "The security team assesses each report within four hours and classifies it using the severity matrix: Low, Medium, High or Critical.", IBP,
             "Triage timing and a severity matrix are defined.", ""),
    "5.26": ("Met", "High and Critical incidents trigger the Incident Response Team, led by the CISO, which contains the incident, preserves logs, and coordinates communication.", IBP,
             "A response team and its duties are defined for serious incidents.", ""),
    "5.27": ("Met", "A post-incident review is held within ten working days of closing every High or Critical incident.", IBP,
             "Reviews, root cause and tracked corrective actions are required.", ""),
    "5.28": ("Partially met", "High and Critical incidents trigger the Incident Response Team, led by the CISO, which contains the incident, preserves logs, and coordinates communication.", IBP,
             "Log preservation is mentioned, but there is no evidence-handling procedure.", "A forensic evidence procedure with chain-of-custody records."),
    "5.30": ("Partially met", "A disaster recovery plan is maintained by IT.", IBP,
             "A DR plan exists but recovery objectives and testing are not described.", "Recovery time and recovery point objectives, and results of a DR test."),
    "5.31": ("Partially met", "The company complies with the GDPR and the UK Data Protection Act 2018.", ISP,
             "Key laws are named, but there is no register of obligations.", "A legal and contractual obligations register with an owner and review dates."),
    "5.34": ("Partially met", "Legal reviews data protection obligations when new services are launched.", ISP,
             "A privacy review step exists but the supporting records are not described.", "Records of processing, impact assessments and a data subject request procedure."),
    "6.3": ("Met", "Security awareness training is mandatory for every employee at onboarding and then once a year.", ISP,
            "Training is mandatory, recurring and tracked by HR.", ""),
    "6.6": ("Met", "All employees sign a confidentiality agreement as part of their employment contract.", ISP,
            "Confidentiality agreements are part of employment.", ""),
    "6.7": ("Met", "Employees working remotely must connect through the company VPN when accessing internal systems.", ISP,
            "Remote access is protected by a VPN and full-disk encryption.", ""),
    "6.8": ("Met", "Anyone who notices a suspected security event, such as a phishing email, a lost laptop or unusual system behaviour, must report it immediately to security@northwind.example or call the IT service desk.", IBP,
            "A reporting channel exists and every report is logged.", ""),
    "7.9": ("Partially met", "Company laptops use full-disk encryption.", ISP,
            "Laptop encryption protects off-site devices, but travel and loss procedures are missing.", "An off-premises asset procedure covering travel, loss and theft."),
    "8.1": ("Partially met", "Company laptops use full-disk encryption.", ISP,
            "Disk encryption is stated, but other endpoint protections are not.", "An endpoint security standard covering patching, device management and screen lock."),
    "8.2": ("Met", "Administrators use a separate named admin account that is distinct from their day-to-day account.", ACP,
            "Admin accounts are separate, limited to IT and reviewed every quarter.", ""),
    "8.3": ("Partially met", "Admin accounts are limited to the IT team and are reviewed every quarter together with the general access review.", ACP,
            "Restriction principles are set, but application-level permission rules are not shown.", "Permission matrices or configuration evidence per application."),
    "8.5": ("Met", "Multi-factor authentication is mandatory for all remote access, for email, and for every administrator account.", ACP,
            "MFA, lockout and a password manager are required.", ""),
    "8.13": ("Met", "Production databases and file servers are backed up daily with incremental backups and a full backup every week.", IBP,
             "Backups are scheduled, encrypted, retained for 30 days and restore-tested yearly.", ""),
    "8.24": ("Partially met", "Backups are encrypted and stored in a separate cloud region.", IBP,
             "Encryption of backups is stated, but no wider cryptography rules exist.", "A cryptography policy with key management rules."),
}


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def demo_quotes() -> list[tuple[str, str, str]]:
    """(control id, quote, document) for every quoted demo verdict (used by tests)."""
    return [(cid, v[1], v[2]) for cid, v in DEMO.items()]


DEMO_FRAMEWORKS = ("iso27001_2022", "hipaa")


def demo_for(framework_id: str) -> tuple[dict, str]:
    """(verdict table, sample document folder) for a framework that has a reference demo."""
    if framework_id == "hipaa":
        from .demo_hipaa import DEMO_HIPAA
        return DEMO_HIPAA, "sample_docs_hipaa"
    return DEMO, "sample_docs"


def demo_assessments(controls: list[Control], table: dict | None = None) -> list[Assessment]:
    DEMO = table if table is not None else globals()["DEMO"]  # noqa: N806
    out: list[Assessment] = []
    for c in controls:
        if c.id in DEMO:
            status, quote, doc, why, missing = DEMO[c.id]
            out.append(Assessment(
                control_id=c.id, domain=c.domain, title=c.title, status=status, quote=quote,
                source_doc=doc, citation_verified=True, reasoning=why, missing=missing,
                review_status="Approved", reviewer="Reference demo", reviewed_at=_stamp(),
            ))
        else:
            out.append(Assessment(
                control_id=c.id, domain=c.domain, title=c.title, status="No evidence",
                reasoning="No passage in the sample documents covers this control.",
                missing=c.evidence_hint, review_status="Approved", reviewer="Reference demo", reviewed_at=_stamp(),
            ))
    return out


def load_demo(ws: Workspace, controls: list[Control]) -> int:
    """Copy the fictional sample documents into the workspace and seed approved demo verdicts."""
    table, folder = demo_for(ws.framework_id)
    for f in sorted((ROOT / folder).glob("*.md")):
        shutil.copy(f, ws.evidence_dir / f.name)
    items = demo_assessments(controls, table)
    ws.put_many(items)
    ws.state["demo"] = True
    ws.log("Loaded the reference demo (fictional sample documents, hand-written verdicts)")
    return len(items)
