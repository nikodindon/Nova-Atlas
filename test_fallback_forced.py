#!/usr/bin/env python3
"""Test du fallback : on force OpenRouter à rate pour vérifier que Ollama génère les résumés."""
import sys, os, shutil, time, subprocess

CFG = "config/config.yaml"
CFG_BAK = "config/config.yaml.bak"

def backup():
    shutil.copy(CFG, CFG_BAK)
    print(f"✅ Config sauvegardée en {CFG_BAK}")

def restore():
    if os.path.exists(CFG_BAK):
        shutil.move(CFG_BAK, CFG)
        print("✅ Config restaurée")

def force_fast_openrouter_timeout():
    """Remplace timeout_fetch openrouter par 1s pour qu'il rate."""
    with open(CFG, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    changed = False
    out = []
    in_openrouter = False
    for line in lines:
        if line.strip().startswith('openrouter:'):
            in_openrouter = True
        elif in_openrouter and line.startswith(' '):
            if 'timeout_fetch:' in line:
                out.append('    timeout_fetch: 1\n')
                changed = True
                continue
        elif in_openrouter and not line.startswith(' '):
            in_openrouter = False
        out.append(line)
    if changed:
        with open(CFG, 'w', encoding='utf-8') as f:
            f.writelines(out)
        print("✅ OpenRouter timeout forcé à 1s")
    else:
        print("⚠️  timeout_fetch: 1 non trouvé — peut-être déjà modifié")
    return changed

def run_fetch(limit=5):
    """Lance le fetch pendant ~15s puis stoppe."""
    proc = subprocess.Popen(
        [sys.executable, 'main.py', '--fetch'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    time.sleep(15)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    # Extraits les lignes utiles des logs en mémoire
    # (pas de fichier de log, donc on ne voit rien ici ; on vérifiera les articles)
    print("⏱️  Fetch terminé (ou interrompu)")

def check_articles():
    import json
    from pathlib import Path
    today = time.strftime('%Y%m%d')
    fpath = Path('data') / f'{today}_articles.json'
    if not fpath.exists():
        print("⚠️  Fichier articles introuvable")
        return
    with open(fpath, encoding='utf-8') as fp:
        articles = json.load(fp)
    print(f"\n📄 {len(articles)} articles dans le JSON")
    empty = 0
    for a in articles[:10]:
        h = a.get('hash', '?')[:8]
        s = a.get('summary', '')
        if not s or s.startswith('['):
            empty += 1
            print(f"  ❌ {h} summary vide")
        else:
            print(f"  ✅ {h} summary={s[:60]}...")
    print(f"\nRésumés vides : {empty}/{len(articles)}")

def main():
    backup()
    try:
        force_fast_openrouter_timeout()
        run_fetch(limit=5)
        time.sleep(2)
        check_articles()
    finally:
        restore()

if __name__ == '__main__':
    main()
