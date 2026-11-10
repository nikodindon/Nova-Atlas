"""Opt-in editorial-v2.1 body selection. Pure HTML parsing; no network/retries."""
from bs4 import BeautifulSoup


def extract_article_text(markup):
    soup=BeautifulSoup(markup,'html.parser')
    for node in soup.select('script,style,nav,header,footer,aside,form,iframe,noscript,[role="dialog"],[aria-modal="true"],.cookie-banner,#cookie-banner,.consent-banner'):
        node.decompose()
    # A nested explicit article body takes precedence over its larger container.
    for selector in ('[itemprop="articleBody"]','.article-body','.post-content','.entry-content','article','main','[role="main"]','.content'):
        for node in soup.select(selector):
            text=node.get_text(separator='\n',strip=True)
            if any(c.isalnum() for c in text):return text
    # Short factual paragraphs are data too; the model must judge sufficiency.
    return '\n'.join(p.get_text(separator=' ',strip=True) for p in soup.find_all('p') if p.get_text(strip=True))
