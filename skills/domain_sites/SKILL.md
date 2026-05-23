---
name: domain-sites
description: 'Use when the research domain is known and authoritative source domains would materially improve web_search. Provides include_domains/site patterns. Skip for quick open discovery, simple lookups, or when structured tools are better.'
---

# Authoritative Site Directory

When the domain is clear and authoritative sources are predictable, call `domain_sites(domain)` before `web_search` to get `site:` patterns or `include_domains`. This is a precision tool, not a required step for every search.

## Core Rule

**Known domain + need precision → `domain_sites(domain)` → web_search with `include_domains`.**

For multi-domain questions, call once per important domain and combine the include_domains lists. For exploratory discovery, start with a high-entropy query first and add domain locks after a source family appears.

## Three-Tier Structure

### Tier 1: Elite Multidisciplinary
`elite_multidisciplinary` — top journals and publishers used across sciences and engineering. Nature, Science, PNAS, Cell, Lancet, IEEE, ACM, Springer, etc. Include this for broad academic/scientific research when elite publishers are plausible, alongside the field-specific domain.

### Tier 2: Field-Specific
One domain per field. Call the ones relevant to your question:
- `cs_conferences` — CS conference proceedings, bibliographies
- `biology` — genes, proteins, species, pathways
- `materials` — materials science, computational materials
- `chemistry` — compounds, reactions, chemical data
- `environment` — climate, ecology, pollution data
- `mathematics` — math reviews, journals, preprints
- `physics` — APS, IOP, arXiv physics, CERN, astrophysics
- `legal_cn` / `legal_en` / `legal_historical` — laws and regulations
- `medical` — clinical, drug approvals, public health
- `academic` — general academic search (arXiv, Semantic Scholar, Google Scholar)
- `historical` — history, archives
- `scientific` — science news
- `tech_programming` — GitHub, Stack Overflow, docs
- `finance` — markets, economic data
- `government_cn` / `government_en` — official statistics
- `patents` — patent search
- `news_factcheck` — factual news, fact-checking
- `encyclopedia` — Wikipedia, Britannica
- `geography` — maps, locations
- `standards` — ISO, IEEE standards
- `film_media` — movies, TV, cast

### Tier 3: Common Knowledge
`common_knowledge` — general reference for everyday facts, how things work, trivia, life常识. Wikipedia, Britannica, HowStuffWorks, National Geographic, etc. Use when the question is broad or doesn't fit a specialized field.

## Multi-Domain Recipes

| Question type | Call these |
|---|---|
| Biology paper about a gene | `domain_sites("elite_multidisciplinary")` + `domain_sites("biology")` |
| CS conference paper | `domain_sites("cs_conferences")` (+ `domain_sites("elite_multidisciplinary")` if top-tier) |
| Material property lookup | `domain_sites("materials")` + `domain_sites("elite_multidisciplinary")` |
| Chinese law about environment | `domain_sites("legal_cn")` + `domain_sites("environment")` |
| Who invented X, year Y | `domain_sites("common_knowledge")` |
| Movie cast and release year | `domain_sites("film_media")` + `domain_sites("common_knowledge")` |

## Usage Pattern

```
# Single domain:
sites = domain_sites(domain="legal_cn")
web_search(query="...", include_domains=sites.include_domains)

# Multi-domain:
elite = domain_sites(domain="elite_multidisciplinary")
field = domain_sites(domain="biology")
all_domains = elite.include_domains + field.include_domains
web_search(query="CRISPR gene editing mechanism", include_domains=all_domains)
```
