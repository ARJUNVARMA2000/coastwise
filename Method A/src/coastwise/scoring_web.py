"""Web configuration and artifact boundary for the shared Method A scorer."""
from dataclasses import dataclass, field

from .baseline import ASSETS, BaselineConfig

SCORING_FILES = ["assessment.json", "assessments.csv", "components.csv", "evidence.csv", "citations.csv",
                 "metrics.csv", "metrics.json", "review_flags.json", "result.json", "outputs.zip"]


def default_scoring_prompt() -> str:
    return (ASSETS / "system_prompt.txt").read_text()


@dataclass
class ScoringWebConfig(BaselineConfig):
    model: str = "deepseek/deepseek-v4.1-flash-1017"
    chunk_chars: int = 24000
    max_output_tokens: int = 12000
    workflow: str = "scoring"
    pages: list[int] | None = None
    system_prompt: str = field(default_factory=default_scoring_prompt)
    max_requests: int = 100
    max_runtime_seconds: int = 600
    full_schema: bool = False
    extraction_mode: str = "method_a_one_shot"
    context_chars: int = 1500
    concurrency: int = 4

    def __post_init__(self):
        super().__post_init__()
        if self.workflow != "scoring":
            raise ValueError("Unknown scoring workflow")
        if type(self.full_schema) is not bool:
            raise ValueError("full_schema must be true or false")
        if self.extraction_mode not in {"method_a_one_shot", "report_v1", "defect_v1", "four_table_v1", "four_table_v2"}:
            raise ValueError("Unknown extraction mode")
        if self.extraction_mode == "method_a_one_shot":
            if self.model != "deepseek/deepseek-v4.1-flash-1017":
                raise ValueError("Method A uses deepseek/deepseek-v4.1-flash-1017")
            if self.full_schema:
                raise ValueError("Method A uses its compact defect schema")
            if not 0 <= self.context_chars <= 4000 or not 1 <= self.concurrency <= 4:
                raise ValueError("Method A context must be 0–4000 characters and concurrency 1–4")
        if self.extraction_mode == "defect_v1":
            from .defects import default_prompt
            self.full_schema = True
            if self.system_prompt == default_scoring_prompt():
                self.system_prompt = default_prompt()
        if not 1 <= len(self.system_prompt.strip()) <= 30000:
            raise ValueError("System prompt must contain 1–30000 characters")
        if not 1 <= self.max_requests <= 100 or not 30 <= self.max_runtime_seconds <= 1200:
            raise ValueError("Invalid scoring request or runtime limit")

    def baseline_config(self):
        return BaselineConfig(**{name: getattr(self, name) for name in BaselineConfig.__dataclass_fields__})
