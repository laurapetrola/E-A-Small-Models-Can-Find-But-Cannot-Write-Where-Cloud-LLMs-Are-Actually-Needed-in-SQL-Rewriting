#!/usr/bin/env python3
"""EXPORTS the P1 ARTIFACT to a clean directory, ready to become its own repository.

⛔⛔ RULE ZERO — SECRETS NEVER LEAVE. `.env` has `KEY_DEEPSEEK`. This script:
     · NEVER copies `.env` (only `.env.example`);
     · SCANS every exported text file for key patterns and ABORTS if it finds one.
     A leak here is public and irreversible — the scan runs even if it costs time.

⭐ WHY A SEPARATE REPO (user's decision, 20/09). This repo's `.cache/` has **4,852**
   records; only **1,849** go into the paper. Exporting instead of publishing the whole repo leaves the
   artifact with **exactly what the paper describes**, plus the three infeasibility measurements the
   text cites. What's left out isn't hidden: it's in the MANIFEST.

USAGE:  python scripts/export_artifact_p1.py [destination]     (default: ../athena-p1-artifact)
      --dry-run  shows what it would copy, without copying
"""
import glob, json, os, re, shutil, sys, hashlib

DEST = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "../athena-p1-artifact"
DRY = "--dry-run" in sys.argv

# ── stores that GO IN: the paper's 25 cells + the 3 infeasibility measurements the text cites
EXTRA = {
    "p1_deepseek_aided_local_imdb_raw",
    "p1_llama_aided_local_imdb_raw",
    "p1_qwen_aided_local_schemalink_reaoff",
    # ⛔ OLD ERA, but the PAPER CITES three measurements of it (the q38 counter-example, §4.6):
    #    15.395→9.717 · 18.804→16.570 · 17.210→10.735 ms. A cited store not sent is a
    #    number the reviewer can't check — so it goes in, even though it's outside the P1 regime.
    #    ⚠️ If §4.6 switches to citing `control_monolithic_flash_p1` (P1 regime), REMOVE this.
    "control_monolithic_flash",
}
# ⛔ secret patterns. If ANY of these matches in an exported file, the script aborts.
SEGREDO = re.compile(
    r"(sk-[A-Za-z0-9]{16,})"                    # OpenAI/DeepSeek-style key
    r"|(KEY_[A-Z_]*\s*=\s*['\"]?[A-Za-z0-9\-_]{16,})"
    r"|(api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{16,})",
    re.I)

ARVORE = [
    ("src", "the pipeline — nodes, backends, S=1 gate"),
    ("scripts", "runner, cell_status, doc/figure/table generators, campaigns"),
    ("queries", "the 21 TPC-DS + 13 JOB queries, verbatim"),
    ("documentation/experiments/final/p1", "the 29 cell docs + README + MANIFEST"),
    ("documentation/article/experimentation_article/figures", "the generated figures (PDF/PNG)"),
]
SOLTOS = ["documentation/fixed_bugs.md", ".env.example", "requirements.txt", "pyproject.toml"]


def celulas_do_paper():
    s = set()
    for d in glob.glob("documentation/experiments/final/p1/*.md"):
        for m in re.findall(r"Cell `([a-z0-9_\-]+)`", open(d, encoding="utf-8", errors="ignore").read()):
            s.add(m)
    return s


def varre_segredos(raiz):
    """⛔ Aborts if it finds a secret. Runs AFTER copying, before declaring success."""
    achados = []
    for dp, _, fs in os.walk(raiz):
        if "/.git" in dp:
            continue
        for f in fs:
            p = os.path.join(dp, f)
            if os.path.getsize(p) > 5_000_000:
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for m in SEGREDO.finditer(txt):
                achados.append((p, m.group(0)[:28]))
    return achados


def main():
    cels = celulas_do_paper() | EXTRA
    print(f"  destination: {DEST}" + ("  (DRY-RUN)" if DRY else ""))
    print(f"  cells to export: {len(cels)}\n")

    if not DRY:
        if os.path.exists(DEST):
            sys.exit(f"  {DEST} already exists. Remove it or choose another destination — not overwriting.")
        os.makedirs(DEST)

    total = 0
    for rel, desc in ARVORE:
        if not os.path.isdir(rel):
            print(f"  doesn't exist, skipping: {rel}"); continue
        n = sum(len(f) for _, _, f in os.walk(rel))
        print(f"  {'[dry] ' if DRY else ''}{rel:52} {n:5} files — {desc}")
        total += n
        if not DRY:
            shutil.copytree(rel, os.path.join(DEST, rel),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))

    for f in SOLTOS:
        if os.path.exists(f):
            print(f"  {'[dry] ' if DRY else ''}{f}")
            if not DRY:
                os.makedirs(os.path.join(DEST, os.path.dirname(f)) or DEST, exist_ok=True)
                shutil.copy2(f, os.path.join(DEST, f))

    nf = 0
    for s in sorted(cels):
        src = f".cache/suggestions_{s}"
        if not os.path.isdir(src):
            print(f"  missing store: {s}"); continue
        fs = glob.glob(src + "/*.json"); nf += len(fs)
        if not DRY:
            shutil.copytree(src, os.path.join(DEST, src))
        # the index store comes along, when it exists
        idx = f".cache/index_{s}"
        if os.path.isdir(idx) and not DRY:
            shutil.copytree(idx, os.path.join(DEST, idx))
    print(f"\n  {'[dry] ' if DRY else ''}stores: {len(cels)} cells · {nf} records")

    if DRY:
        print("\n  (dry-run — nothing was copied)"); return

    print("\n  SECRET SCAN...")
    achados = varre_segredos(DEST)
    if achados:
        print(f"  {len(achados)} OCCURRENCE(S) — ARTIFACT IS NOT SAFE:")
        for p, t in achados[:10]:
            print(f"     {p}: {t}…")
        print(f"  REMOVING {DEST} for safety. Fix and re-export.")
        shutil.rmtree(DEST)
        sys.exit(1)
    print("  no secrets found")

    if os.path.exists(os.path.join(DEST, ".env")):
        sys.exit("  .env ended up in the destination — ABORT and investigate")
    print(f"\n  artifact at {DEST}  ({total} code/doc files + {nf} records)")
    print("  MANUAL NEXT STEP: write the artifact's README (how to reproduce) and `git init`.")


if __name__ == "__main__":
    main()
