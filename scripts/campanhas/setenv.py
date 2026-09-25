#!/usr/bin/env python3
"""Set/replace keys in the project's .env in place. Usage: setenv.py KEY=VAL KEY2=VAL2 ..."""
import re, sys

# ⛔ GUARDA DO `v4-pro` (09/09/2026). O DeepSeek aposenta o `deepseek-v4-pro` em 14/09/2026 12:00
#    (Pequim): a partir daí o pedido NÃO falha — ele é roteado para o Flash e cobrado como Flash,
#    enquanto o nosso store continua gravando `writer=cloud:deepseek-v4-pro`. Ou seja, o modo de
#    falha é MISLABEL SILENCIOSO, não erro — a família de bug que mais custou tempo nesta campanha.
#    Decisão da usuária (09/09): "não vamos mais produzir com pro; todos os nossos testes são com
#    flash". O P1 já é 100% flash (auditado: 8 células de nuvem, zero registros em pro).
#    ⚠️ A guarda fica AQUI e não em cada script porque este é o único caminho por onde o `.env` é
#    escrito — cobre inclusive script novo que ainda não existe.
_PROIBIDOS = ("deepseek-v4-pro", "deepseek-v4_pro")
for _a in sys.argv[1:]:
    _k, _, _v = _a.partition("=")
    if _v.strip() in _PROIBIDOS:
        sys.stderr.write(
            f"⛔ RECUSADO: {_k}={_v}\n"
            "   O `deepseek-v4-pro` foi aposentado em 14/09/2026 e agora devolve Flash com o NOME\n"
            "   pro — a célula nasceria mislabelada. Use `deepseek-v4-flash`.\n"
            "   (Se for uma reprodução histórica deliberada, edite o .env à mão e documente a era.)\n")
        sys.exit(7)

env = open(".env").read()
for arg in sys.argv[1:]:
    k, _, v = arg.partition("=")
    if re.search(rf"(?m)^{re.escape(k)}=", env):
        env = re.sub(rf"(?m)^{re.escape(k)}=.*", f"{k}={v}", env)
    else:
        env = env.rstrip("\n") + f"\n{k}={v}\n"
open(".env", "w").write(env)
print(f"  set: {' '.join(a.split('=')[0] for a in sys.argv[1:])}")
