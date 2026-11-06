"""Configured categories plus historical categories, never silently omitted."""
from dataclasses import dataclass,asdict
from pathlib import Path
from modules.core.source_manager import SourceManager,SourceError

CATEGORY_LABELS = {'geopolitique': 'Géopolitique', 'economie': 'Économie', 'crypto': 'Crypto', 'tech': 'Tech & IA', 'france': 'France', 'monde': 'Monde', 'science': 'Science & Santé', 'environnement': 'Environnement', 'societe': 'Société & Droits', 'culture': 'Culture & Arts', 'sport': 'Sport'}
CATEGORY_ICONS = {'geopolitique': '🌍', 'economie': '📈', 'crypto': '₿', 'tech': '⚡', 'france': '🗼', 'monde': '🌐', 'science': '🔬', 'environnement': '🌿', 'societe': '✊', 'culture': '🎭', 'sport': '⚽', 'sante': '🏥', 'gaming': '🎮', 'sciences_humaines': '🧠', 'auto': '🚗', 'regions': '🗺️', 'asie': '🏯', 'monde_arabe': '🕌', 'eurasie': '🪆', 'europe': '🏰', 'afrique': '🌍', 'amerique_latine': '🌴'}

@dataclass(frozen=True)
class Category:
    id:str
    name:str
    icon:str
    color:str|None
    order:int
    active:bool


def registry(feeds=None,articles=(),*,labels=None):
    configured=[];active=set()
    if feeds is not None:
        try:
            doc=SourceManager(feeds).read();configured=doc.categories;active={s.category for s in doc.sources if s.enabled}
        except SourceError:pass  # No mutation or credential reflection on malformed source config.
    observed=list(dict.fromkeys(a.get('category') or 'monde' for a in articles if isinstance(a,dict)))
    names=list(dict.fromkeys(configured+list(CATEGORY_LABELS)+list(CATEGORY_ICONS)+observed))
    labels=labels or {}
    return [Category(cat,labels.get(cat,CATEGORY_LABELS.get(cat,cat.replace('_',' ').capitalize())),CATEGORY_ICONS.get(cat,'🌐'),None,i,cat in active or cat in observed) for i,cat in enumerate(names)]


def ordered_categories(articles,feeds=None,legacy_order=None):
    present={a.get('category') or 'monde' for a in articles}
    configured=[]
    if feeds is not None:
        try:configured=SourceManager(feeds).read().categories
        except SourceError:pass
    order=list(dict.fromkeys(configured+list(legacy_order or [])+[c.id for c in registry(None,articles)]))
    return [cat for cat in order if cat in present]
