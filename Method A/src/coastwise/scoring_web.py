"""Web configuration and artifact boundary for the shared Method A scorer."""
from dataclasses import dataclass, field

from .baseline import ASSETS, BaselineConfig

SCORING_FILES = ["assessment.json", "assessments.csv", "components.csv", "evidence.csv", "citations.csv",
                 "metrics.csv", "metrics.json", "review_flags.json", "result.json", "outputs.zip"]


def default_scoring_prompt() -> str:
    return (ASSETS / "system_prompt.txt").read_text()


@dataclass
class ScoringWebConfig(BaselineConfig):
    workflow: str = "scoring"
    pages: list[int] | None = None
    system_prompt: str = field(default_factory=default_scoring_prompt)
    max_requests: int = 8
    max_runtime_seconds: int = 1200
    full_schema: bool = False

    def __post_init__(self):
        super().__post_init__()
        if self.workflow != "scoring":
            raise ValueError("Unknown scoring workflow")
        if type(self.full_schema) is not bool:
            raise ValueError("full_schema must be true or false")
        if not 1 <= len(self.system_prompt.strip()) <= 30000:
            raise ValueError("System prompt must contain 1–30000 characters")
        if not 1 <= self.max_requests <= 100 or not 30 <= self.max_runtime_seconds <= 1200:
            raise ValueError("Invalid scoring request or runtime limit")

    def baseline_config(self):
        return BaselineConfig(**{name: getattr(self, name) for name in BaselineConfig.__dataclass_fields__})
