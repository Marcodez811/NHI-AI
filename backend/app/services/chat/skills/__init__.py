"""Chat skills: sticky conversation modes with their own instructions, tools and workspace."""

from app.services.chat.skills.legislative_qa import SKILL_ID as LEGISLATIVE_QA_SKILL

SUPPORTED_SKILLS = (LEGISLATIVE_QA_SKILL,)

__all__ = ["LEGISLATIVE_QA_SKILL", "SUPPORTED_SKILLS"]
