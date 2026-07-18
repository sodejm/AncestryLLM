from __future__ import annotations

import pytest

from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.domain.models import LivingStatus


def test_prompt_versions_are_immutable_and_rendered_safely(app_context: AppContext) -> None:
    first = app_context.prompts.save("summary", "research", "Summarize $name", ["name"])
    second = app_context.prompts.save("summary", "research", "Briefly summarize $name", ["name"])
    assert first.version == 1
    assert second.version == 2
    assert app_context.prompts.get("summary", 1).body == "Summarize $name"
    assert app_context.prompts.render("summary", {"name": "Ada"}) == "Briefly summarize Ada"


def test_prompt_rejects_undeclared_or_missing_variables(app_context: AppContext) -> None:
    with pytest.raises(AncestryError, match="do not match"):
        app_context.prompts.save("bad", "test", "Hello $name", [])
    app_context.prompts.save("good", "test", "Hello $name", ["name"])
    with pytest.raises(AncestryError, match="exactly"):
        app_context.prompts.render("good", {"name": "Ada", "extra": "x"})


def test_research_people_are_reusable_across_service_calls(app_context: AppContext) -> None:
    created = app_context.research.add_person("Ada Example", LivingStatus.DECEASED, "Fictional")
    listed = app_context.research.list_people()
    assert listed == [created]
