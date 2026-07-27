from typing import Literal


BrainItemType = Literal[
    "idea", "fact", "preference", "person", "project", "goal", "decision",
    "task", "reference", "reflection",
]
RelationType = Literal[
    "about", "supports", "contradicts", "continues", "decides", "updates", "source_for",
    "same entity", "same domain", "related topic (inferred)",
]
ConnectionRelationType = Literal["same entity", "same domain", "related topic (inferred)"]

BRAIN_ITEM_TYPES = frozenset(BrainItemType.__args__)
RELATION_TYPES = frozenset(RelationType.__args__)
RELATION_CONFIDENCE_THRESHOLD = 0.85
