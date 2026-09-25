"""Learned-rules store (architecture B: rule-in-prompt + corrector).

A validated heuristic's `prompt_rule` (written by the formalizer) is persisted here and injected into
the rewrite prompt — but ONLY in APPLICATION mode (`APPLY_HEURISTICS`). In DISCOVERY mode the prompt
stays neutral (injecting a technique rule there would SEED the discovery and contaminate it). So:
  - discovery (APPLY_HEURISTICS off): neutral prompt, measures unaided capability;
  - application (APPLY_HEURISTICS on): the discovered rules guide the model; the corrector repairs the SQL.
"""
import json
import os

RULES_FILE = ".cache/learned_rules.json"   # default: the rules DISCOVERED by the system itself


def _rules_file() -> str:
    """Path of the rule set to inject. `LEARNED_RULES_FILE` enables RULE-SET ABLATION (arm #5:
    hand-crafted rules from prior work vs rules discovered and S=1-validated by the system) without
    touching the discovered-rules file — overwrite/restore would risk losing them if anything broke
    mid-cell. Read PER CALL (not at import) so it does not depend on when the module was loaded."""
    return os.getenv("LEARNED_RULES_FILE", "").strip() or RULES_FILE


def _app_mode() -> bool:
    return os.getenv("APPLY_HEURISTICS", "").strip().lower() in ("1", "true", "on", "yes")


def load_rules() -> dict:
    try:
        with open(_rules_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def add_rule(heuristic_name: str, prompt_rule: str) -> None:
    """Persist a validated heuristic's prompt rule (called when a heuristic is promoted in arch B).
    Always reads AND writes the DISCOVERED-rules file, never the ablation set: a promotion while
    `LEARNED_RULES_FILE` points elsewhere would otherwise merge foreign rules into the discovered set."""
    try:
        with open(RULES_FILE, encoding="utf-8") as f:
            rules = json.load(f)
    except Exception:
        rules = {}
    rules[heuristic_name] = (prompt_rule or "").strip()
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)


def active_rule_names() -> list[str]:
    """Names of the rules currently injected into the rewrite prompt (provenance / audit trail).
    EMPTY in discovery mode — nothing is injected there, so attribution is unambiguous."""
    if not _app_mode():
        return []
    return [k for k, v in load_rules().items() if v]


def learned_rules_text() -> str:
    """Validated prompt rules formatted for the rewrite prompt — EMPTY unless in application mode
    (so discovery stays neutral / unseeded)."""
    if not _app_mode():
        return ""
    rules = [r for r in load_rules().values() if r]
    return "\n".join(f"- {r}" for r in rules)
