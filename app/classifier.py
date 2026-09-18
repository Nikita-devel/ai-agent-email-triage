"""LLM classification + structured extraction.

Structured output is enforced with Anthropic tool-use (forced tool choice),
so the model can only answer with a JSON object matching the schema below.
A `mock` provider implements the same interface for offline demos and tests.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import date
from typing import Protocol

from .retry import retry

from .config import CATEGORIES, PRIORITIES, Config, config
from .email_reader import IncomingEmail

log = logging.getLogger(__name__)

TOOL_SCHEMA = {
    "name": "record_triage",
    "description": "Record the triage result for one incoming customer email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": list(CATEGORIES),
                "description": (
                    "technical_support: an existing client reports a bug, outage, or asks how to "
                    "use the product. sales_inquiry: a prospect or client asks about a new project, "
                    "quote, pricing, or capability. complaint: dissatisfaction with delivery, "
                    "quality, billing, or service already provided. administrative: invoices, "
                    "contracts, legal/tax/accounting paperwork. other: newsletters, cold outreach, "
                    "notifications, anything not actionable as client work."
                ),
            },
            "sender_name": {"type": "string", "description": "Person's name as signed in the email."},
            "summary": {
                "type": "string",
                "description": "One or two sentences, in English, stating what the sender needs.",
            },
            "priority": {
                "type": "string",
                "enum": list(PRIORITIES),
                "description": (
                    "Judge the business consequence of NOT acting today, not the sender's tone.\n"
                    "urgent: work is stopped right now (production halted, nobody can log in), "
                    "or an explicit ultimatum / deadline under 48h.\n"
                    "high: money or a relationship is at stake if it slips - an inbound lead or "
                    "quote request, a contract or renewal to sign, a statutory or tax deadline "
                    "(URSSAF, TVA, declarations), a repeat escalation, or a degraded system that "
                    "still runs.\n"
                    "medium: a real request with no date pressure - a routine question, an "
                    "invoice detail, a bug with a workaround.\n"
                    "low: informational or explicitly not blocking - the sender says it can wait, "
                    "plans a future budget, or the message needs no action at all.\n"
                    "A stated regulatory deadline is never low. A sender saying 'ce n'est pas "
                    "bloquant' is never above medium."
                ),
            },
            "extracted_deadline": {
                "type": ["string", "null"],
                "description": "ISO date YYYY-MM-DD if a deadline is stated or clearly implied, else null.",
            },
            "language": {"type": "string", "description": "ISO 639-1 code of the email body, e.g. fr, en, de."},
            "action_required": {
                "type": "boolean",
                "description": "True if a human must reply or act; false for newsletters/notifications.",
            },
            "confidence": {"type": "number", "description": "0.0-1.0 confidence in the category."},
        },
        "required": [
            "category", "sender_name", "summary", "priority",
            "extracted_deadline", "language", "action_required", "confidence",
        ],
    },
}

SYSTEM_PROMPT = """You triage inbound email for a freelance software engineer who builds \
custom business applications for small industrial manufacturers in France.

Rules:
- Classify into exactly one category using the tool definitions. When two categories fit, \
prefer the one describing what the sender WANTS, not the tone they use.
- A dissatisfied client who still asks for a technical fix is technical_support; a client \
asking for compensation, a refund, or an apology is a complaint.
- Invoices and contracts are administrative even when the sender is annoyed, unless they \
dispute the amount - a disputed invoice is a complaint.
- Automated notifications and cold sales outreach are other, with action_required=false.
- Resolve relative dates ("before Friday", "end of the month") against the email's received \
date, given below. If no deadline is stated, return null - never invent one.
- summary is always in English, regardless of the email language.

Priority is decided by consequence and time, in this order - the first rule that matches wins:
1. The sender states the problem is NOT blocking, NOT urgent, or that there is no rush -> low, \
even for a technical fault.
2. Production is stopped, people cannot work, there is an ultimatum, or a stated deadline is \
within 48 hours -> urgent.
3. A stated deadline within 14 days that requires a reply -> at least high. This includes \
legal, tax and accounting deadlines in automated messages (URSSAF, tax office, administration): \
an automated sender does not make a legal deadline low.
4. Money is on the table in a concrete way - a contract or its renewal, a written proposal \
requested, a stated budget, a multi-site or multi-year scope -> high. A first-contact enquiry \
with no budget, scope or deadline is medium.
5. A recurring or scheduled process is stuck and the sender is blocked on its output -> high.
6. A newsletter, cold outreach, or notification that asks nothing of the reader -> low.
7. Anything else -> medium.

Today's date for reference: {today}."""

FEW_SHOT = [
    {
        "email": (
            "From: Luc Bonnet <l.bonnet@atelier-bonnet.fr>\n"
            "Received: 2026-03-02\n"
            "Subject: L'application ne demarre plus\n\n"
            "Bonjour, depuis ce matin l'application affiche une page blanche sur les 6 postes "
            "de l'atelier. Nous ne pouvons plus lancer la production. Merci de regarder en urgence.\nLuc"
        ),
        "result": {
            "category": "technical_support", "sender_name": "Luc Bonnet",
            "summary": "Application shows a blank page on all 6 workshop machines since this morning; production is stopped.",
            "priority": "urgent", "extracted_deadline": None, "language": "fr",
            "action_required": True, "confidence": 0.95,
        },
    },
    {
        "email": (
            "From: Newsletter Industrie <news@industrie-mag.fr>\n"
            "Received: 2026-03-02\n"
            "Subject: Les 10 tendances de l'usine connectee\n\n"
            "Decouvrez notre dossier special. Se desabonner."
        ),
        "result": {
            "category": "other", "sender_name": "Newsletter Industrie",
            "summary": "Marketing newsletter about smart factory trends.",
            "priority": "low", "extracted_deadline": None, "language": "fr",
            "action_required": False, "confidence": 0.98,
        },
    },
    {
        "email": (
            "From: Anne Girard <a.girard@precitech.fr>\n"
            "Received: 2026-03-02\n"
            "Subject: Devis pour un module de suivi de production\n\n"
            "Bonjour, nous sommes une PME de 25 personnes et cherchons un outil de suivi "
            "sur mesure. Pouvez-vous nous envoyer une proposition avant le 20 mars ?\nAnne Girard"
        ),
        "result": {
            "category": "sales_inquiry", "sender_name": "Anne Girard",
            "summary": "25-person SME requests a quote for a custom production tracking module.",
            "priority": "high", "extracted_deadline": "2026-03-20", "language": "fr",
            "action_required": True, "confidence": 0.93,
        },
    },
]


@dataclass
class TriageResult:
    category: str
    sender_name: str
    summary: str
    priority: str
    extracted_deadline: str | None
    language: str
    action_required: bool
    confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


class ClassificationError(RuntimeError):
    pass


def _normalise(payload: dict, fallback: IncomingEmail) -> TriageResult:
    category = str(payload.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        raise ClassificationError(f"category out of enum: {category!r}")
    priority = str(payload.get("priority", "medium")).strip().lower()
    if priority not in PRIORITIES:
        priority = "medium"
    deadline = payload.get("extracted_deadline")
    if deadline and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(deadline)):
        deadline = None
    try:
        confidence = min(max(float(payload.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return TriageResult(
        category=category,
        sender_name=str(payload.get("sender_name") or fallback.sender_name)[:120],
        summary=str(payload.get("summary") or "")[:500],
        priority=priority,
        extracted_deadline=deadline,
        language=str(payload.get("language") or "")[:5],
        action_required=bool(payload.get("action_required", True)),
        confidence=confidence,
    )


def render_email(item: IncomingEmail) -> str:
    return (
        f"From: {item.sender_name} <{item.sender_email}>\n"
        f"Received: {(item.received_at or '')[:10]}\n"
        f"Subject: {item.subject}\n\n{item.body}"
    )


class Provider(Protocol):
    def classify(self, item: IncomingEmail) -> TriageResult: ...


class AnthropicProvider:
    def __init__(self, cfg: Config = config) -> None:
        import anthropic  # imported lazily so the mock path needs no SDK

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        self.model = cfg.llm_model

    def _messages(self, item: IncomingEmail) -> list[dict]:
        msgs: list[dict] = []
        for shot in FEW_SHOT:
            msgs.append({"role": "user", "content": shot["email"]})
            msgs.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use", "id": f"shot_{len(msgs)}",
                    "name": TOOL_SCHEMA["name"], "input": shot["result"],
                }],
            })
            msgs.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": f"shot_{len(msgs) - 1}", "content": "recorded"}],
            })
        msgs.append({"role": "user", "content": render_email(item)})
        return msgs

    @retry(attempts=4, base_delay=2, max_delay=30)
    def classify(self, item: IncomingEmail) -> TriageResult:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT.format(today=date.today().isoformat()),
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": TOOL_SCHEMA["name"]},
            messages=self._messages(item),
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == TOOL_SCHEMA["name"]:
                return _normalise(dict(block.input), item)
        raise ClassificationError("model returned no tool_use block")


class MockProvider:
    """Keyword heuristic with the same contract - lets the pipeline run without an API key."""

    RULES = [
        ("complaint", ("relance", "rembours", "avoir", "decevant", "jamais ete livre",
                       "non realis", "troisieme fois", "sans surcout", "refund", "unacceptable")),
        ("administrative", ("facture", "rib", "iban", "tva", "contrat", "avenant",
                            "declaration", "urssaf", "comptab", "invoice", "signature")),
        ("technical_support", ("erreur", "bug", "ne fonctionne", "inaccessible", "401",
                               "mot de passe", "page blanche", "stuck", "error", "crash",
                               "connexion refusee", "probleme")),
        ("sales_inquiry", ("devis", "tarif", "proposition", "budget", "consultation",
                           "quote", "pricing", "maintenance mensuel", "besoin")),
    ]
    NOISE = ("noreply", "no-reply", "linkedin", "newsletter", "desabonner", "outreach", "unsubscribe")
    URGENT = ("urgence", "bloqu", "a l'arret", "48h", "immediat", "urgent", "production est bloquee")

    def classify(self, item: IncomingEmail) -> TriageResult:
        blob = f"{item.subject}\n{item.body}".lower()
        sender = item.sender_email.lower()

        category = "other"
        if not any(n in sender or n in blob for n in self.NOISE):
            for name, keywords in self.RULES:
                if any(k in blob for k in keywords):
                    category = name
                    break
        elif "urssaf" in sender:
            category = "administrative"

        if category == "other":
            priority = "low"
        elif any(k in blob for k in self.URGENT):
            priority = "urgent"
        else:
            priority = "medium"

        match = re.search(r"(\d{1,2})[/\s-](\d{1,2})(?:[/\s-](\d{2,4}))?", blob)
        deadline = None
        if match and any(w in blob for w in ("avant", "delai", "limite", "before", "deadline", "echeance")):
            day, month, year = match.group(1), match.group(2), match.group(3) or str(date.today().year)
            try:
                deadline = date(int(year), int(month), int(day)).isoformat()
            except ValueError:
                deadline = None

        return TriageResult(
            category=category,
            sender_name=item.sender_name,
            summary=f"[mock] {item.subject}",
            priority=priority,
            extracted_deadline=deadline,
            language="fr" if any(c in blob for c in ("bonjour", "cordialement", "merci")) else "en",
            action_required=category != "other",
            confidence=0.5,
        )


def get_provider(cfg: Config = config) -> Provider:
    cfg.require_llm()
    if cfg.llm_provider == "anthropic":
        log.info("LLM provider: anthropic (%s)", cfg.llm_model)
        return AnthropicProvider(cfg)
    log.warning("LLM provider: MOCK - keyword heuristic, not a real classification")
    return MockProvider()


def classify(item: IncomingEmail, provider: Provider | None = None) -> TriageResult:
    return (provider or get_provider()).classify(item)


__all__ = ["TriageResult", "ClassificationError", "get_provider", "classify",
           "AnthropicProvider", "MockProvider", "TOOL_SCHEMA"]
