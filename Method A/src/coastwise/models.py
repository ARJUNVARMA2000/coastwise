"""A small explicit shortlist, checked against OpenRouter's live catalog."""

FEATURED_MODELS = (
    ("openai/gpt-6-sol", "GPT-6 Sol", "OpenAI"),
    ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5", "Anthropic"),
    ("google/gemini-3.8-flash", "Gemini 3.8 Flash", "Google"),
    ("openai/gpt-6-luna", "GPT-6 Luna", "OpenAI"),
    ("deepseek/deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017", "DeepSeek"),
)


def model_options(catalog: list[dict], expanded: bool = False) -> list[dict]:
    eligible = {m["id"]: m for m in catalog
                if {"response_format", "structured_outputs"} & set(m.get("supported_parameters", []))
                and ":batch" not in m["id"]}
    choices = [(slug, name, vendor) for slug, name, vendor in FEATURED_MODELS if slug in eligible]
    if expanded:
        featured_ids = {m[0] for m in choices}
        choices += [(slug, m.get("name", slug), slug.split("/")[0].title())
                    for slug, m in sorted(eligible.items()) if slug not in featured_ids]
    return [{"id": slug, "name": name, "vendor": vendor,
             "featured": slug in {m[0] for m in FEATURED_MODELS},
             "context_length": eligible[slug].get("context_length"),
             "pricing": eligible[slug].get("pricing", {}),
             "supports_tools": "tools" in eligible[slug].get("supported_parameters", [])}
            for slug, name, vendor in choices]
