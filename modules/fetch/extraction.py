"""Extract article text from HTML without crossing into recommendation panels."""
from bs4 import BeautifulSoup

NON_ARTICLE = (
    'script,style,nav,header,footer,aside,form,iframe,noscript,'
    '[role="dialog"],[aria-modal="true"],.cookie-banner,#cookie-banner,.consent-banner,'
    '.related-articles,.recommended-articles,.read-also,[data-component="related-articles"]'
)
BODY = '[itemprop="articleBody"],.article-body,.post-content,.entry-content'


def extract_article_text(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    for node in soup.select(NON_ARTICLE):
        node.decompose()

    main = soup.select_one('main,[role="main"]') or soup
    # A nested <article> in the lead article often describes another story.
    articles = [node for node in main.find_all('article') if node.find_parent('article') is None]
    if len(articles) > 1:
        with_headline = [node for node in articles if node.find('h1')]
        if len(with_headline) != 1:
            return ''
        articles = with_headline
    article = articles[0] if articles else None
    if article is not None:
        for nested in article.find_all('article'):
            if nested.parent is not None:
                nested.decompose()
    scope = article if article is not None else main

    for selector in ('[itemprop="articleBody"]', '.article-body', '.post-content', '.entry-content'):
        for node in scope.select(selector):
            text = node.get_text(separator='\n', strip=True)
            if any(c.isalnum() for c in text):
                return text
    if scope is not soup:
        paragraphs = scope.find_all('p')
        return '\n'.join(p.get_text(separator=' ', strip=True) for p in paragraphs if p.get_text(strip=True))

    # With no page structure, accept only a single explicit body. Multiple
    # competing bodies provide no basis for choosing one as the story.
    bodies = soup.select(BODY)
    if len(bodies) == 1:
        return bodies[0].get_text(separator='\n', strip=True)
    if bodies:
        return ''
    return '\n'.join(p.get_text(separator=' ', strip=True) for p in soup.find_all('p') if p.get_text(strip=True))
