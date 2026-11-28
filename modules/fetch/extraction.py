"""Extract article text from HTML without crossing into recommendation panels."""
from bs4 import BeautifulSoup
from urllib.parse import urlsplit

NON_ARTICLE = (
    'script,style,nav,header,footer,aside,form,iframe,noscript,'
    '[role="dialog"],[aria-modal="true"],.cookie-banner,#cookie-banner,.consent-banner,'
    '.related-articles,.recommended-articles,.read-also,[data-component="related-articles"]'
)
BODY = '[itemprop="articleBody"],.article-body,.post-content,.entry-content'


def paragraphs(scope):
    return '\n'.join(
        node.get_text(separator=' ', strip=True)
        for node in scope.find_all('p')
        if node.get_text(strip=True)
    )


def extract_article_text(markup, *, source_url=None):
    soup = BeautifulSoup(markup, 'html.parser')
    for node in soup.select(NON_ARTICLE):
        node.decompose()

    main = soup.select_one('main,[role="main"]') or soup
    host = urlsplit(source_url).hostname if source_url else None
    if host in ('futura-sciences.com', 'www.futura-sciences.com') and main is not soup:
        bodies = main.select('.single-content')
        if len(bodies) == 1 and main.find('h1') is not None:
            for aside_article in bodies[0].find_all('article'):
                aside_article.decompose()
            if text := paragraphs(bodies[0]):
                return text

    # A nested <article> in the lead article often describes another story.
    articles = [node for node in main.find_all('article') if node.find_parent('article') is None]
    if len(articles) > 1:
        with_headline = [node for node in articles if node.find('h1')]
        if len(with_headline) == 1:
            articles = with_headline
        else:
            # WordPress cards can share the article tag but have no prose.
            # Require a single explicit body owned by the only prose-bearing article.
            bodies = main.select(BODY)
            owners = [node for node in articles if node.find('p') is not None]
            if (len(bodies) != 1 or len(owners) != 1
                    or bodies[0].find_parent('article') is not owners[0]):
                return ''
            articles = owners
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
        return paragraphs(scope)

    # With no page structure, accept only a single explicit body. Multiple
    # competing bodies provide no basis for choosing one as the story.
    bodies = soup.select(BODY)
    if len(bodies) == 1:
        return bodies[0].get_text(separator='\n', strip=True)
    if bodies:
        return ''
    return paragraphs(soup)
