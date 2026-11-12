"""Use existing scenario articles as section indexes without changing their URLs."""

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files
from mkdocs.structure.nav import Navigation, Section
from mkdocs.structure.pages import Page


class ScenarioIndexPage(Page):
    @property
    def is_index(self) -> bool:
        return True


def sections(items):
    for item in items:
        if isinstance(item, Section):
            yield item
            yield from sections(item.children)


def on_nav(nav: Navigation, config: MkDocsConfig, files: Files) -> Navigation:
    replacements: dict[str, Page] = {}
    for section in sections(nav.items):
        if not section.children or not isinstance(section.children[0], Page):
            continue
        overview = section.children[0]
        source = overview.file.src_uri
        if not source.startswith("business-events/scenarios/") or overview.is_index:
            continue
        deployment = source.removesuffix(".md") + "/deployment.md"
        if not any(isinstance(child, Page) and child.file.src_uri == deployment
                   for child in section.children[1:]):
            continue
        landing = ScenarioIndexPage(None, overview.file, config)
        landing.parent = overview.parent
        landing.previous_page = overview.previous_page
        landing.next_page = overview.next_page
        section.children[0] = landing
        replacements[source] = landing

    nav.pages[:] = [replacements.get(page.file.src_uri, page) for page in nav.pages]
    for page in nav.pages:
        if page.previous_page:
            page.previous_page = replacements.get(page.previous_page.file.src_uri, page.previous_page)
        if page.next_page:
            page.next_page = replacements.get(page.next_page.file.src_uri, page.next_page)
    return nav
