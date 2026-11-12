"""Source layout checks for the Fender findings and corrosion table formats."""
from __future__ import annotations

import re
from collections import defaultdict

CAPTION = re.compile(r"^TABLE\s*[-–]\s*\d+[A-Z]?\b.*\bFENDER\s*#\s*\d+", re.IGNORECASE)
ZONE = re.compile(r"\b(?:mid\s*water|mudline|bottom|below\s+conc)", re.IGNORECASE)
SECTION_LOSS = re.compile(r"([<>≤≥=]*\s*\d+(?:\.\d+)?)\s*%\s*section\s+loss\b", re.IGNORECASE)
THICKNESS_ROW = re.compile(r"^(?:outer flange|web|inner flange)\s*\n\s*[\d.]+$", re.IGNORECASE)
FENDER_LABEL = re.compile(r"Fender #\d+", re.IGNORECASE)
FENDER_HEADER = re.compile(r"(?:underwater|submerged) inspection (?:element|component) "
                           r"(?:findings|observations) (?:condition|rating)")
FENDER_PILE = re.compile(r"Pile\s*#?\s*(\d+)\b", re.IGNORECASE)


def normalized(value):
    return " ".join(value.casefold().split())


def fender_groups(lookup):
    """Locate merged Fender labels using their table headers and page continuations."""
    by_page = defaultdict(list)
    for tag, block in lookup.items():
        if block["bbox"]:
            by_page[block["page"]].append((tag, block))
    groups, previous = {}, None
    for page, blocks in sorted(by_page.items()):
        header_boxes = sorted((p["bbox"][1], p["bbox"][0]) for _, p in blocks
                              if FENDER_HEADER.fullmatch(normalized(p["text"])))
        headers = [y for y, _ in header_boxes]
        labels = sorted((p["bbox"][1], tag, p) for tag, p in blocks if FENDER_LABEL.fullmatch(p["text"].strip()))
        carry, previous = previous, None
        if not headers or len(headers) != len(labels):
            continue
        ends = [*headers[1:], float("inf")]
        if not all(start < y < end and p["bbox"][2] < x
                   for (start, x), end, (y, _, p) in zip(header_boxes, ends, labels, strict=True)):
            continue
        sections = [{"v": p["text"].strip(), "src": [tag]} for _, tag, p in labels]
        # An unlabeled top block ending in a surface inspection belongs to the
        # previous group only if its final underwater rows reached the page bottom.
        lead = [(tag, p) for tag, p in blocks if p["bbox"][3] <= headers[0]]
        surface = [p for _, p in lead if normalized(p["text"]).startswith("surface inspection")]
        lead_piles = sorted((p["bbox"][1], int(m[1])) for _, p in lead if (m := FENDER_PILE.match(p["text"])))
        if (carry and carry[0] == page - 1 and len(surface) == 1 and lead_piles
                and lead_piles[0][1] == carry[2] + 1 and lead_piles[0][0] < surface[0]["bbox"][1]):
            groups.update({tag: carry[1] for tag, p in lead if p["bbox"][3] <= surface[0]["bbox"][3]})
        for start, end, section in zip(headers, ends, sections, strict=True):
            groups.update({tag: section for tag, p in blocks if start <= p["bbox"][1] and p["bbox"][3] < end})
        page_height = next((p["page_height"] for _, p in blocks if p.get("page_height")),
                           max(p["bbox"][3] for _, p in blocks))
        last = [p for _, p in blocks if headers[-1] <= p["bbox"][1]]
        piles = sorted((p["bbox"][3], int(m[1])) for p in last if (m := FENDER_PILE.match(p["text"])))
        if (piles and piles[-1][0] >= 0.85 * page_height
                and not any(normalized(p["text"]).startswith("surface inspection") for p in last)):
            previous = (page, sections[-1], piles[-1][1])
    return groups


def table_rows(lookup):
    """Index supported native table shapes by visual position, independent of PDF block order."""
    by_page = defaultdict(list)
    for tag, block in lookup.items():
        if block["bbox"] and block["text"].strip():
            by_page[block["page"]].append((tag, block))
    index = {}
    for blocks in by_page.values():
        captions = [(tag, block) for tag, block in blocks if CAPTION.match(block["text"])]
        if len(captions) != 1:
            continue
        caption_tag, caption = captions[0]
        blocks.sort(key=lambda x: (x[1]["bbox"][1], x[1]["bbox"][0]))
        if "corrosion evaluation" in normalized(caption["text"]):
            index.update(corrosion_rows(blocks, caption_tag))
        elif "inspection findings" in normalized(caption["text"]):
            index.update(findings_rows(blocks, caption_tag))
    return index


def findings_rows(blocks, caption_tag):
    header = next((tag for tag, p in blocks if normalized(p["text"]) == "item condition repair renew remarks"), "")
    if not header:
        return {}
    index, side, group, side_tag, group_tag = {}, "", "", "", ""
    for tag, p in blocks:
        if tag in (caption_tag, header):
            continue
        lines = [line.strip() for line in p["text"].splitlines() if line.strip()]
        label = lines[0]
        name = normalized(label)
        if name in ("east rubber fender", "west rubber fender"):
            side, side_tag = label, tag
        if len(lines) == 1:
            group, group_tag = label, tag
            if name != "bolts to mounting plate":
                side, side_tag = "", ""
            continue
        # The next unindented item ends the previous group. Indented table labels
        # are anchored to a group only while the group remains open.
        if p["bbox"][0] < 30:
            group, group_tag = "", ""
            if name not in ("east rubber fender", "west rubber fender"):
                side, side_tag = "", ""
        if name == "anchors to seawall":
            preceding = next(reversed(index.values()), None)
            if preceding and normalized(preceding["row_label"]) in (
                    "east fender mounting plate", "west fender mounting plate"):
                group, group_tag = preceding["row_label"], preceding["row_tag"]
        index[tag] = {"layout": "findings", "caption_tag": caption_tag, "header_tag": header,
                      "row_tag": tag, "row_label": label, "group": group, "group_tag": group_tag,
                      "side": side if normalized(group) == "bolts to mounting plate" else "",
                      "side_tag": side_tag if normalized(group) == "bolts to mounting plate" else ""}
    return index


def corrosion_rows(blocks, caption_tag):
    thickness = next(((tag, p) for tag, p in blocks if normalized(p["text"]) == "current thickness"), None)
    evaluation = next(((tag, p) for tag, p in blocks if normalized(p["text"]) == "corrosion evaluation"), None)
    if not thickness or not evaluation:
        return {}
    header_tag, header = thickness
    unit_tags = [tag for tag, p in blocks if normalized(p["text"]) == "(inch)"
                 and header["bbox"][0] <= p["bbox"][0] <= header["bbox"][2]
                 and 0 <= p["bbox"][1] - header["bbox"][3] < 12]
    if len(unit_tags) != 1:
        return {}
    rows = [(tag, p) for tag, p in blocks if p["bbox"][1] > header["bbox"][3]]
    notes = next((p["bbox"][1] for _, p in rows if normalized(p["text"]) == "notes:"), float("inf"))
    groups = [(tag, p) for tag, p in rows if p["bbox"][1] < notes and p["bbox"][0] < header["bbox"][0]
              and "\n" not in p["text"] and " - " in p["text"] and ZONE.search(p["text"])]
    index = {}
    for i, (tag, block) in enumerate(groups):
        lo = block["bbox"][1]
        hi = groups[i + 1][1]["bbox"][1] if i + 1 < len(groups) else notes
        cells = [(t, p) for t, p in rows if lo < p["bbox"][1] < hi]
        ratings = [(t, m.group(1).strip()) for t, p in cells
                   if p["bbox"][0] >= evaluation[1]["bbox"][0] - 8 and (m := SECTION_LOSS.search(p["text"]))]
        values = [t for t, p in cells if p["bbox"][0] < header["bbox"][0] and THICKNESS_ROW.fullmatch(p["text"])]
        if len(ratings) == 1 and values:
            index[tag] = {"layout": "corrosion", "caption_tag": caption_tag, "header_tag": header_tag,
                          "row_tag": tag, "row_label": block["text"], "rating_tag": ratings[0][0],
                          "rating_value": ratings[0][1],
                          "unit_tag": unit_tags[0], "value_tags": values}
    return index


def component_row(component, index):
    candidates = set(component["name"]["src"]) & index.keys()
    if len(candidates) == 1:
        return index[next(iter(candidates))]
    candidates = [index[t] for t in component["findings_text"]["src"]
                  if t in index and index[t]["layout"] == "findings"]
    if len(candidates) != 1:
        return {}
    return candidates[0]


def validate_component_row(component, context):
    """Reject row conflicts even if the individual text is present on the page."""
    name, identifier = normalized(component["name"]["v"]), normalized(component["identifier"])
    label = normalized(context["row_label"])
    if context["layout"] == "corrosion":
        return name == label
    if name == label:
        return True
    if context["group_tag"] not in component["name"]["src"]:
        return False
    if name == normalized(context["group"]) and label == identifier:
        return bool(context["side_tag"])
    if name == normalized(context["group"]) and context["side_tag"]:
        return identifier in (f'{normalized(context["side"])} {label}', f'{label} {normalized(context["side"])}')
    return False


def measurement_scope(context, measurement):
    """Validate row and unit header independently of the numerical quote match."""
    if not context:
        return "unresolved"
    source_tags = set(measurement["src"])
    unit_tags = set(measurement["unit_src"])
    if context["layout"] == "corrosion" and len(source_tags) != 1:
        return "conflict"
    if context["layout"] == "findings":
        allowed = {context["row_tag"]}
        units = allowed
    elif measurement["measurement_kind"] == "remaining_thickness":
        allowed = set(context["value_tags"])
        units = {context["unit_tag"]}
    elif measurement["measurement_kind"] == "section_loss":
        allowed = units = {context["rating_tag"]}
        if re.sub(r"[\s%]", "", measurement["value"]) != re.sub(r"\s", "", context["rating_value"]):
            return "conflict"
    else:
        return "unresolved"
    if not source_tags or not source_tags <= allowed or not unit_tags <= units:
        return "conflict"
    return "verified" if unit_tags else "unresolved"
